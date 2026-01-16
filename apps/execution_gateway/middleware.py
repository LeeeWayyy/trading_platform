"""Authentication middleware for Execution Gateway.

This module provides HMAC-signed header validation middleware for internal
service-to-service authentication. The middleware populates request.state.user
from trusted headers sent by internal services (e.g., performance dashboard).

Design Rationale:
    - Fail-closed security: invalid/missing token → 401
    - HMAC-SHA256 signature prevents header spoofing
    - Timestamp validation prevents replay attacks
    - JSON payload prevents delimiter injection
    - Constant-time comparison prevents timing attacks

Security Model:
    - INTERNAL_TOKEN_REQUIRED=true (default): Token validation required
    - INTERNAL_TOKEN_REQUIRED=false: Headers trusted without validation (dev only)

Headers:
    - X-User-Role: User role (admin, trader, viewer)
    - X-User-Id: User identifier
    - X-User-Strategies: Comma-separated list of authorized strategies
    - X-User-Signature: HMAC-SHA256 signature (when validation enabled)
    - X-Request-Timestamp: Epoch seconds for replay protection

Usage:
    from apps.execution_gateway.middleware import populate_user_from_headers

    app.middleware("http")(populate_user_from_headers)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class _SecretValue(Protocol):
    """Protocol for types that can provide a secret value (e.g., SecretStr)."""

    def get_secret_value(self) -> str: ...


class _InternalTokenSettings(Protocol):
    """Protocol for settings used in internal token validation.

    This allows both real Settings and test mocks to satisfy the type.
    """

    internal_token_required: bool
    internal_token_secret: _SecretValue
    internal_token_timestamp_tolerance_seconds: int


def _verify_internal_token(
    token: str | None,
    timestamp_str: str | None,
    user_id: str,
    role: str,
    strategies: str,
    settings: _InternalTokenSettings,
) -> tuple[bool, str]:
    """Verify X-User-Signature using HMAC-SHA256.

    Token format: HMAC-SHA256(secret, canonical_json_payload)
    where the payload is a JSON object with sorted keys:
    {"role": ..., "strats": ..., "ts": ..., "uid": ...}
    This prevents delimiter injection attacks that could occur with simple concatenation.

    Args:
        token: Value from X-User-Signature header
        timestamp_str: Value from X-Request-Timestamp header (epoch seconds)
        user_id: Value from X-User-Id header
        role: Value from X-User-Role header
        strategies: Value from X-User-Strategies header (comma-separated)
        settings: Application settings with internal_token_* config

    Returns:
        Tuple of (is_valid, error_reason). error_reason is empty if valid.

    Security Notes:
        - Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks
        - Validates timestamp within ±tolerance_seconds to prevent replay attacks
        - Fails closed: missing token/timestamp when required returns False
        - Binds strategies to signature to prevent privilege escalation

    Example:
        >>> settings = get_settings()
        >>> is_valid, error = _verify_internal_token(
        ...     token="abc123...",
        ...     timestamp_str="1705329600",
        ...     user_id="user123",
        ...     role="trader",
        ...     strategies="alpha_baseline,momentum",
        ...     settings=settings,
        ... )
        >>> if is_valid:
        ...     # Proceed with request
        ...     pass
    """
    if not settings.internal_token_required:
        return True, ""

    secret_value = settings.internal_token_secret.get_secret_value()
    if not secret_value:
        logger.error("INTERNAL_TOKEN_REQUIRED=true but INTERNAL_TOKEN_SECRET is empty")
        return False, "token_secret_not_configured"

    if not token:
        return False, "missing_token"

    if not timestamp_str:
        return False, "missing_timestamp"

    # Parse and validate timestamp
    try:
        request_timestamp = int(timestamp_str)
    except ValueError:
        return False, "invalid_timestamp_format"

    now = int(time.time())
    skew = abs(now - request_timestamp)
    if skew > settings.internal_token_timestamp_tolerance_seconds:
        logger.warning(
            "Internal token timestamp outside tolerance",
            extra={
                "skew_seconds": skew,
                "tolerance_seconds": settings.internal_token_timestamp_tolerance_seconds,
                "user_id_prefix": user_id[:4] if user_id else "none",
            },
        )
        return False, "timestamp_expired"

    # Compute expected signature using JSON payload to prevent delimiter injection
    # Example attack without JSON: user_id="u1:admin" + role="viewer"
    # could become user_id="u1" + role="admin:viewer"
    # JSON with sorted keys provides canonical representation immune to such attacks
    # Note: Replay protection is timestamp-based only. For stronger protection,
    # consider adding nonce validation with Redis in high-security environments.
    payload_data = {
        "uid": user_id.strip(),
        "role": role.strip(),
        "strats": strategies.strip(),
        "ts": timestamp_str.strip(),
    }
    payload = json.dumps(payload_data, separators=(",", ":"), sort_keys=True)
    expected_signature = hmac.new(
        secret_value.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(expected_signature, token.lower()):
        # Log mismatch without revealing signature prefixes to avoid leaking secret-derived data
        logger.warning(
            "Internal token signature mismatch",
            extra={
                "user_id_prefix": user_id[:4] if user_id else "none",
                "token_length": len(token) if token else 0,
            },
        )
        return False, "invalid_signature"

    return True, ""


async def populate_user_from_headers(request: Request, call_next: Any) -> Any:
    """Populate request.state.user from trusted internal headers.

    The performance dashboard Streamlit client sends X-User-Role, X-User-Id, and
    X-User-Strategies headers. This middleware validates these headers using
    HMAC-signed X-User-Signature when INTERNAL_TOKEN_REQUIRED=true.

    Headers:
        X-User-Role: User role (admin, trader, viewer)
        X-User-Id: User identifier
        X-User-Strategies: Comma-separated list of authorized strategies
        X-User-Signature: HMAC-SHA256 signature (when validation enabled)
        X-Request-Timestamp: Epoch seconds for replay protection

    Backward Compatibility:
        - INTERNAL_TOKEN_REQUIRED=false (explicit): Headers trusted without validation
        - INTERNAL_TOKEN_REQUIRED=true (default): Token validation required for user context

    This middleware populates request.state.user which build_user_context()
    then uses for RBAC enforcement.

    Args:
        request: FastAPI request object
        call_next: Next middleware or endpoint handler

    Returns:
        Response from next handler, or 401 if token validation fails

    Security Notes:
        - Only validates when INTERNAL_TOKEN_REQUIRED=true
        - Missing headers → no user context (endpoints must check)
        - Invalid token → 401 response immediately
        - Valid token → populates request.state.user

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> app.middleware("http")(populate_user_from_headers)
    """
    from config.settings import get_settings

    role = request.headers.get("X-User-Role")
    user_id = request.headers.get("X-User-Id")
    strategies_header = request.headers.get("X-User-Strategies", "")

    if role and user_id:
        # Validate internal token if required
        settings = get_settings()
        if settings.internal_token_required:
            token = request.headers.get("X-User-Signature")
            timestamp = request.headers.get("X-Request-Timestamp")

            is_valid, error_reason = _verify_internal_token(
                token=token,
                timestamp_str=timestamp,
                user_id=user_id,
                role=role,
                strategies=strategies_header,
                settings=settings,  # type: ignore[arg-type]
            )

            if not is_valid:
                logger.warning(
                    "Internal token validation failed",
                    extra={
                        "error_reason": error_reason,
                        "path": request.url.path,
                        "user_id_prefix": user_id[:4] if user_id else "none",
                    },
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing internal authentication token"},
                )

        strategies = [s.strip() for s in strategies_header.split(",") if s.strip()]
        request.state.user = {
            "role": role.strip(),
            "user_id": user_id.strip(),
            "strategies": strategies,
        }

    return await call_next(request)
