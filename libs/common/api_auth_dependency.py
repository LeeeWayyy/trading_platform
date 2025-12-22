"""FastAPI API authentication dependency for trading endpoints.

Provides JWT and S2S (internal service token) authentication with:
- Fail-closed default (API_AUTH_MODE=enforce)
- Mandatory replay protection for S2S tokens via Redis nonce store
- Integration with C5 rate limiting via request.state propagation
- Role/permission enforcement per endpoint

CRITICAL: API_AUTH_MODE defaults to enforce. Only set to log_only during staged rollout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from fastapi import Header, HTTPException, Request, status
from prometheus_client import Counter

from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Permission, Role, has_permission

logger = logging.getLogger(__name__)

# Metrics
api_auth_checks_total = Counter(
    "api_auth_checks_total",
    "API authentication checks",
    ["action", "result", "auth_type", "mode"],
)

s2s_auth_checks_total = Counter(
    "s2s_auth_checks_total",
    "Service-to-service authentication checks",
    ["service_id", "result"],
)

s2s_replay_detected_total = Counter(
    "s2s_replay_detected_total",
    "S2S token replay attempts detected",
    ["service_id"],
)

# Configuration - read at call time to support secret rotation without restart
# Note: INTERNAL_TOKEN_SECRET is fetched via _get_internal_token_secret() per-request
INTERNAL_TOKEN_TIMESTAMP_TOLERANCE = int(
    os.getenv("INTERNAL_TOKEN_TIMESTAMP_TOLERANCE_SECONDS", "300")
)


def _get_internal_token_secret() -> str:
    """Get the internal token secret, read at call time for secret rotation support.

    SECURITY: Reading at call time allows secret rotation without service restart.
    """
    return os.getenv("INTERNAL_TOKEN_SECRET", "")

# SECURITY: Service ID whitelist - only allow known internal services
# Prevents rogue services from authenticating with stolen secrets
# Note: Normalize by stripping whitespace and lowercasing for robustness
_raw_service_ids = os.getenv("ALLOWED_SERVICE_IDS", "orchestrator,signal_service,execution_gateway")
ALLOWED_SERVICE_IDS = frozenset(
    s.strip().lower() for s in _raw_service_ids.split(",") if s.strip()
)


def _get_auth_mode() -> str:
    """Get current auth mode. Read per-request for hot-switch support.

    SECURITY: Defaults to enforce (fail-closed).
    Normalizes to lowercase and treats unknown values as enforce for fail-closed behavior.
    """
    mode = os.getenv("API_AUTH_MODE", "enforce").lower().strip()
    # SECURITY: Fail-closed - unknown values treated as enforce
    if mode not in ("enforce", "log_only"):
        logger.warning(
            "api_auth_mode_invalid",
            extra={"configured_mode": mode, "effective_mode": "enforce"},
        )
        return "enforce"
    return mode


def _get_service_secret(service_id: str) -> str:
    """Get the secret for a specific service.

    SECURITY: Supports per-service secrets for defense in depth.
    If INTERNAL_TOKEN_SECRET_{SERVICE_ID} is set, use it.
    Otherwise, fall back to global INTERNAL_TOKEN_SECRET.

    Per-service secrets limit blast radius if one service is compromised:
    - Attacker can only impersonate the compromised service
    - Cannot forge tokens for other services

    Example:
        INTERNAL_TOKEN_SECRET_ORCHESTRATOR=abc123...
        INTERNAL_TOKEN_SECRET_SIGNAL_SERVICE=def456...
    """
    # Try per-service secret first (service_id uppercase with non-alphanumeric chars replaced)
    # Use regex for robustness - handles dots, hyphens, and any other special characters
    service_key = re.sub(r"[^A-Z0-9_]", "_", service_id.upper())
    per_service_secret = os.getenv(f"INTERNAL_TOKEN_SECRET_{service_key}", "")
    if per_service_secret:
        return per_service_secret

    # Fall back to global secret (read at call time for secret rotation support)
    return _get_internal_token_secret()


def _is_service_id_allowed(service_id: str) -> bool:
    """Check if service_id is in the allowed whitelist.

    SECURITY: Prevents unknown or rogue services from authenticating.
    Even with a valid secret, unauthorized service_ids are rejected.
    """
    # Compare lowercase to match normalized whitelist
    return service_id.lower() in ALLOWED_SERVICE_IDS


def _is_internal_token_required() -> bool:
    """Check if internal token is required.

    SECURITY: Defaults to true (fail-closed).
    """
    return os.getenv("INTERNAL_TOKEN_REQUIRED", "true").lower() == "true"


def validate_auth_config() -> None:
    """Validate auth configuration at startup.

    SECURITY: Fail-closed on invalid configuration.
    Call this in app startup (lifespan or on_event).
    """
    mode = os.getenv("API_AUTH_MODE", "enforce")
    if mode not in ("enforce", "log_only"):
        raise RuntimeError(f"Invalid API_AUTH_MODE: {mode}. Must be 'enforce' or 'log_only'")

    env = os.getenv("ENVIRONMENT", "production")
    if mode == "log_only" and env == "production":
        logger.warning(
            "api_auth_log_only_in_prod: API_AUTH_MODE=log_only in production - ensure staged rollout",
        )


def validate_internal_token_config() -> None:
    """Validate internal token configuration at startup.

    SECURITY: INTERNAL_TOKEN_REQUIRED defaults to true (fail-closed).
    Also validates per-service secrets if configured.
    """
    secret = os.getenv("INTERNAL_TOKEN_SECRET", "")
    env = os.getenv("ENVIRONMENT", "production")
    token_required = _is_internal_token_required()

    if token_required:
        if not secret:
            raise RuntimeError(
                "INTERNAL_TOKEN_SECRET is required when INTERNAL_TOKEN_REQUIRED=true. "
                'Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )
        if len(secret) < 32:
            raise RuntimeError(
                f"INTERNAL_TOKEN_SECRET must be at least 32 bytes (got {len(secret)}). "
                'Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )

        # SECURITY: Detect service ID collisions after normalization
        # e.g., "my-service" and "my_service" both normalize to "MY_SERVICE"
        # This could cause services to unintentionally share secrets
        normalized_keys: dict[str, str] = {}
        for service_id in ALLOWED_SERVICE_IDS:
            # Use same sanitization as _get_service_secret for consistency
            service_key = re.sub(r"[^A-Z0-9_]", "_", service_id.upper())
            if service_key in normalized_keys:
                original_sid = normalized_keys[service_key]
                raise RuntimeError(
                    f"Service ID collision detected: '{original_sid}' and '{service_id}' "
                    f"both normalize to '{service_key}'. This would cause them to share secrets. "
                    "Please ensure service IDs are unique after normalization."
                )
            normalized_keys[service_key] = service_id

            # SECURITY: Validate per-service secrets meet minimum length
            per_service_secret = os.getenv(f"INTERNAL_TOKEN_SECRET_{service_key}", "")
            if per_service_secret and len(per_service_secret) < 32:
                raise RuntimeError(
                    f"INTERNAL_TOKEN_SECRET_{service_key} must be at least 32 bytes "
                    f"(got {len(per_service_secret)}). "
                    'Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"'
                )

    if not token_required and env == "production":
        logger.warning(
            "internal_token_disabled_in_prod: INTERNAL_TOKEN_REQUIRED=false in production - S2S auth disabled!",
        )


@dataclass
class InternalTokenClaims:
    """Verified internal token claims for S2S calls."""

    service_id: str
    user_id: str | None
    strategy_id: str | None
    nonce: str
    timestamp: int


@dataclass
class AuthContext:
    """Authentication context returned by api_auth dependency."""

    user: AuthenticatedUser | None  # JWT-authenticated user
    internal_claims: InternalTokenClaims | None  # S2S internal token claims
    auth_type: str  # "jwt", "internal_token", "none"
    is_authenticated: bool


@dataclass
class APIAuthConfig:
    """Configuration for API authentication."""

    action: str  # For metrics labeling (e.g., "order_submit", "signal_generate")
    require_role: Role | None = None  # Role requirement (e.g., Role.OPERATOR for orders)
    require_permission: Permission | None = None  # Permission check


# Maximum nonce length to prevent Redis key bloat
MAX_NONCE_LENGTH = 128


async def _check_nonce_unique(
    redis_client: redis.Redis,
    nonce: str,
    service_id: str,
    tolerance_seconds: int,
    mode: str,
) -> str:
    """Ensure nonce is used only once within tolerance window.

    Redis error handling:
    - enforce mode: Fail-closed (raise 503)
    - log_only mode: Log error, allow request (soft-fail)

    SECURITY: Nonce keys are scoped by service_id to prevent cross-service
    collisions and DoS attacks where one service reuses another's nonces.

    Returns:
        "ok" - Nonce is unique and valid
        "nonce_too_long" - Nonce exceeds max length
        "replay_detected" - Nonce was already used
    """
    # SECURITY: Prevent Redis key bloat from excessively long nonces
    if len(nonce) > MAX_NONCE_LENGTH:
        logger.warning(
            "s2s_nonce_too_long",
            extra={"service_id": service_id, "nonce_length": len(nonce)},
        )
        return "nonce_too_long"

    # Include service_id in key to prevent cross-service collisions
    key = f"internal_nonce:{service_id}:{nonce}"
    try:
        was_set = await redis_client.set(key, "1", nx=True, ex=tolerance_seconds * 2)
        if not was_set:
            s2s_replay_detected_total.labels(service_id=service_id).inc()
            logger.warning("s2s_replay_detected", extra={"nonce": nonce, "service_id": service_id})
            return "replay_detected"
        return "ok"
    except Exception as exc:
        logger.error(
            "s2s_nonce_redis_error",
            extra={"nonce": nonce, "service_id": service_id, "error": str(exc)},
        )
        if mode == "enforce":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "service_unavailable",
                    "message": "Auth service temporarily unavailable",
                },
            ) from exc
        # log_only: Soft-fail, allow request but log
        return "ok"


def _verify_hmac_signature(
    token: str,
    service_id: str,
    method: str,
    path: str,
    query: str,
    timestamp: str,
    nonce: str,
    user_id: str | None,
    strategy_id: str | None,
    body_hash: str,
) -> bool:
    """Verify HMAC-SHA256 signature of internal token with body and query integrity.

    SECURITY:
    - Uses per-service secrets when available (limits blast radius)
    - Falls back to global secret for backward compatibility
    - Query string in signature prevents parameter tampering
    - Body hash in signature prevents payload tampering
    """
    # Use per-service secret if available, otherwise global secret
    secret = _get_service_secret(service_id).encode()
    if not secret:
        return False

    # Build payload as JSON for unambiguous serialization
    # SECURITY: JSON prevents delimiter collision attacks where attacker-controlled values
    # containing delimiters could forge signatures (e.g., query="a|b" colliding with user_id="b")
    payload_dict = {
        "service_id": service_id,
        "method": method,
        "path": path,
        "query": query,
        "timestamp": timestamp,
        "nonce": nonce,
        "user_id": user_id or "",
        "strategy_id": strategy_id or "",
        "body_hash": body_hash,
    }
    # Use sorted keys and compact separators for deterministic serialization
    payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
    expected_sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(token, expected_sig)


async def verify_internal_token(
    request: Request,
    redis_client: redis.Redis,
    token: str | None,
    timestamp: str | None,
    nonce: str | None,
    service_id: str | None,
    user_id: str | None,
    strategy_id: str | None,
    body_hash: str | None,
) -> InternalTokenClaims | None:
    """Verify HMAC-signed internal token for S2S calls with replay protection and body integrity.

    SECURITY:
    - Query string from request.url.query included in signature to prevent parameter tampering
    - Body hash verification prevents payload tampering
    - HMAC verified BEFORE body hashing to prevent DoS attacks

    Note: Query string is taken from request.url.query, NOT from a header, to prevent tampering.

    Returns InternalTokenClaims if valid, None otherwise.
    Sets request.state for C5 rate limiting integration.
    """
    mode = _get_auth_mode()

    # All headers required for S2S auth
    if not all([token, timestamp, nonce, service_id]):
        return None

    # Type narrowing: after the check above, these are guaranteed to be str
    assert token is not None
    assert timestamp is not None
    assert nonce is not None
    assert service_id is not None

    # SECURITY: Validate service_id against whitelist
    # Prevents rogue or unknown services from authenticating
    if not _is_service_id_allowed(service_id):
        logger.warning(
            "s2s_service_id_not_allowed",
            extra={"service_id": service_id, "allowed": list(ALLOWED_SERVICE_IDS)},
        )
        s2s_auth_checks_total.labels(service_id=service_id, result="service_not_allowed").inc()
        return None

    # Verify timestamp within tolerance
    try:
        ts = int(timestamp)
        now = int(time.time())
        if abs(now - ts) > INTERNAL_TOKEN_TIMESTAMP_TOLERANCE:
            logger.warning(
                "s2s_timestamp_out_of_range",
                extra={"timestamp": ts, "now": now, "service_id": service_id},
            )
            s2s_auth_checks_total.labels(service_id=service_id, result="timestamp_invalid").inc()
            return None
    except ValueError:
        logger.warning("s2s_timestamp_invalid", extra={"timestamp": timestamp})
        return None

    # SECURITY: Verify HMAC signature FIRST (before body hashing)
    # This prevents DoS attacks where attacker sends large bodies to waste CPU
    method = request.method
    path = request.url.path

    # SECURITY: Use actual query string from request, NOT the X-Query header
    # This prevents query tampering where attacker changes URL params but leaves
    # the X-Query header unchanged. The signature will fail because we verify
    # against what's actually in the request URL.
    actual_query = request.url.query or ""

    if not _verify_hmac_signature(
        token, service_id, method, path, actual_query, timestamp, nonce, user_id, strategy_id, body_hash or ""
    ):
        logger.warning(
            "s2s_signature_invalid",
            extra={"service_id": service_id, "path": path},
        )
        s2s_auth_checks_total.labels(service_id=service_id, result="signature_invalid").inc()
        return None

    # SECURITY: Verify body integrity AFTER signature check (DoS prevention)
    # Only hash body after we know the request has valid HMAC

    # SECURITY: Require body hash for state-changing methods (POST, PUT, PATCH, DELETE)
    # This prevents attackers from bypassing body integrity by omitting the hash
    is_state_changing = method.upper() in ("POST", "PUT", "PATCH", "DELETE")
    if is_state_changing and not body_hash:
        logger.warning(
            "s2s_body_hash_required",
            extra={"service_id": service_id, "method": method, "path": path},
        )
        s2s_auth_checks_total.labels(service_id=service_id, result="body_hash_missing").inc()
        return None

    if body_hash:
        try:
            body_bytes = await request.body()
            # Note: Always compute hash, even for empty body (b"" is valid and has a hash)
            actual_body_hash = hashlib.sha256(body_bytes).hexdigest()
            if actual_body_hash != body_hash:
                logger.warning(
                    "s2s_body_hash_mismatch",
                    extra={
                        "service_id": service_id,
                        "expected_hash": body_hash[:16] + "...",
                        "actual_hash": actual_body_hash[:16] + "...",
                    },
                )
                s2s_auth_checks_total.labels(service_id=service_id, result="body_tampered").inc()
                return None
        except Exception as exc:
            logger.error("s2s_body_read_error", extra={"error": str(exc)})
            if mode == "enforce":
                return None
            # log_only: Continue without body verification

    # Check nonce uniqueness (replay protection)
    nonce_result = await _check_nonce_unique(
        redis_client, nonce, service_id, INTERNAL_TOKEN_TIMESTAMP_TOLERANCE, mode
    )
    if nonce_result != "ok":
        # Use the specific result (nonce_too_long or replay_detected) for proper metrics
        s2s_auth_checks_total.labels(service_id=service_id, result=nonce_result).inc()
        return None

    # Build claims
    claims = InternalTokenClaims(
        service_id=service_id,
        user_id=user_id,
        strategy_id=strategy_id,
        nonce=nonce,
        timestamp=ts,
    )

    # CRITICAL: Set request.state for C5 rate limiting integration
    request.state.internal_service_verified = True
    request.state.service_id = service_id

    # Propagate user context if present (for audit trail and rate limiting)
    if user_id:
        request.state.user = {"user_id": user_id, "aud": "internal-service"}
    if strategy_id:
        request.state.strategy_id = strategy_id

    s2s_auth_checks_total.labels(service_id=service_id, result="authenticated").inc()
    logger.info(
        "s2s_auth_success",
        extra={"service_id": service_id, "user_id": user_id, "strategy_id": strategy_id},
    )

    return claims


def _get_redis_client() -> redis.Redis:
    """Get Redis client for nonce storage.

    Uses DB 2 (same as rate limiter) with decode_responses=True.
    """
    from libs.web_console_auth.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    return limiter.redis


def api_auth(
    config: APIAuthConfig,
    authenticator_getter: Any = None,  # For testing injection
) -> Any:
    """FastAPI dependency for API authentication with dual-mode support.

    Returns a dependency function that:
    1. Checks for S2S internal token first
    2. Falls back to JWT authentication
    3. Enforces role/permission requirements
    4. Sets request.state for C5 integration
    """
    from apps.execution_gateway.api.dependencies import get_gateway_authenticator

    async def dependency(
        request: Request,
        authorization: str | None = Header(None, alias="Authorization"),
        x_user_id: str | None = Header(None, alias="X-User-ID"),
        x_request_id: str | None = Header(None, alias="X-Request-ID"),
        x_session_version: str | None = Header(None, alias="X-Session-Version"),
        # S2S headers
        x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
        x_internal_timestamp: str | None = Header(None, alias="X-Internal-Timestamp"),
        x_internal_nonce: str | None = Header(None, alias="X-Internal-Nonce"),
        x_service_id: str | None = Header(None, alias="X-Service-ID"),
        x_strategy_id: str | None = Header(None, alias="X-Strategy-ID"),
        x_body_hash: str | None = Header(None, alias="X-Body-Hash"),
        # Note: X-Query header removed - query string is now taken from request.url.query
        # to prevent tampering attacks where attacker could modify URL params but leave header unchanged
        # NOTE: authenticator is now resolved lazily inside the JWT branch (P1 fix)
        # This prevents S2S calls from requiring JWT infrastructure (Postgres, Redis for sessions)
    ) -> AuthContext:
        mode = _get_auth_mode()

        # 1. Try S2S authentication first (internal services)
        # IMPORTANT: S2S auth is checked BEFORE JWT to avoid requiring JWT infrastructure
        if x_internal_token and _is_internal_token_required():
            redis_client = _get_redis_client()
            internal_claims = await verify_internal_token(
                request=request,
                redis_client=redis_client,
                token=x_internal_token,
                timestamp=x_internal_timestamp,
                nonce=x_internal_nonce,
                service_id=x_service_id,
                user_id=x_user_id,
                strategy_id=x_strategy_id,
                body_hash=x_body_hash,
            )
            if internal_claims:
                api_auth_checks_total.labels(
                    action=config.action, result="internal_bypass", auth_type="internal_token", mode=mode
                ).inc()
                return AuthContext(
                    user=None,
                    internal_claims=internal_claims,
                    auth_type="internal_token",
                    is_authenticated=True,
                )

        # 2. Try JWT authentication (external clients)
        # IMPORTANT: Authenticator is resolved lazily here to avoid requiring JWT infrastructure
        # (Postgres, Redis for sessions) when the request is an S2S call with internal token
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            if x_user_id and x_request_id and x_session_version:
                try:
                    # Lazy resolution of authenticator - only when JWT auth is actually needed
                    authenticator = (authenticator_getter or get_gateway_authenticator)()
                    session_version = int(x_session_version)
                    user = await authenticator.authenticate(
                        token=token,
                        x_user_id=x_user_id,
                        x_request_id=x_request_id,
                        x_session_version=session_version,
                    )

                    # Set request.state for C5 rate limiting
                    request.state.user = {
                        "user_id": user.user_id,
                        "role": user.role.value if user.role else None,
                        "strategies": user.strategies,
                    }

                    # Check role requirement
                    if config.require_role and user.role:
                        # Define role levels for clear hierarchy comparison
                        # Higher level = more permissions (ADMIN > OPERATOR > VIEWER)
                        ROLE_LEVELS = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
                        user_level = ROLE_LEVELS.get(user.role, -1)
                        required_level = ROLE_LEVELS.get(config.require_role, -1)
                        if user_level < required_level:
                            api_auth_checks_total.labels(
                                action=config.action,
                                result="insufficient_role",
                                auth_type="jwt",
                                mode=mode,
                            ).inc()
                            if mode == "enforce":
                                raise HTTPException(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    detail={
                                        "error": "insufficient_role",
                                        "message": f"Role {config.require_role.value} or higher required",
                                    },
                                )

                    # Check permission requirement
                    if config.require_permission:
                        if not has_permission(user, config.require_permission):
                            api_auth_checks_total.labels(
                                action=config.action,
                                result="permission_denied",
                                auth_type="jwt",
                                mode=mode,
                            ).inc()
                            if mode == "enforce":
                                raise HTTPException(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    detail={
                                        "error": "permission_denied",
                                        "message": f"Permission {config.require_permission.value} required",
                                    },
                                )

                    api_auth_checks_total.labels(
                        action=config.action, result="authenticated", auth_type="jwt", mode=mode
                    ).inc()
                    return AuthContext(
                        user=user,
                        internal_claims=None,
                        auth_type="jwt",
                        is_authenticated=True,
                    )
                except HTTPException:
                    raise
                except Exception as exc:
                    logger.warning(
                        "jwt_auth_failed",
                        extra={"action": config.action, "error": str(exc)},
                    )
                    api_auth_checks_total.labels(
                        action=config.action, result="jwt_invalid", auth_type="jwt", mode=mode
                    ).inc()
                    if mode == "enforce":
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"error": "invalid_token", "message": str(exc)},
                        ) from exc

        # 3. No valid authentication found
        api_auth_checks_total.labels(
            action=config.action, result="unauthenticated", auth_type="none", mode=mode
        ).inc()

        if mode == "enforce":
            logger.warning(
                "api_auth_rejected",
                extra={
                    "action": config.action,
                    "path": request.url.path,
                    "has_auth_header": bool(authorization),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "authentication_required", "message": "Authentication required"},
            )

        # log_only mode: Log and allow
        logger.warning(
            "api_auth_unauthenticated_allowed",
            extra={
                "action": config.action,
                "path": request.url.path,
                "mode": mode,
            },
        )
        return AuthContext(
            user=None,
            internal_claims=None,
            auth_type="none",
            is_authenticated=False,
        )

    return dependency


__all__ = [
    "APIAuthConfig",
    "AuthContext",
    "InternalTokenClaims",
    "api_auth",
    "api_auth_checks_total",
    "s2s_auth_checks_total",
    "s2s_replay_detected_total",
    "validate_auth_config",
    "validate_internal_token_config",
    "verify_internal_token",
]
