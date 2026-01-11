"""Shared FastAPI dependencies for manual control endpoints."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
import redis
import redis.asyncio as redis_async
from fastapi import Depends, HTTPException, Request, status
from psycopg_pool import AsyncConnectionPool

from apps.execution_gateway.alpaca_client import AlpacaExecutor
from apps.execution_gateway.database import DatabaseClient
from libs.common.secrets import get_required_secret
from libs.web_console_auth.audit_logger import AuditLogger
from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import (
    AuthError,
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    MissingJtiError,
    SessionExpiredError,
    SubjectMismatchError,
    TokenExpiredError,
    TokenReplayedError,
    TokenRevokedError,
)
from libs.web_console_auth.gateway_auth import AuthenticatedUser, GatewayAuthenticator
from libs.web_console_auth.jwks_validator import JWKSValidator
from libs.web_console_auth.jwt_manager import JWTManager
from libs.web_console_auth.permissions import Permission, Role, has_permission
from libs.web_console_auth.rate_limiter import RateLimiter
from libs.web_console_auth.redis_client import (
    create_async_redis,
    create_sync_redis,
    load_redis_config,
)

logger = logging.getLogger(__name__)

TwoFaResult = tuple[bool, str | None, str | None]
TwoFaValidator = Callable[[str, str], Awaitable[TwoFaResult]]

# Environment configuration (CONFIG - not secrets)
# DATABASE_URL will be loaded lazily via get_database_url()
REDIS_CONFIG = load_redis_config()

AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")

# Parse Auth0 domain using urllib.parse for robust URL handling
_raw_auth0_domain = os.getenv("AUTH0_DOMAIN", "").strip()
if _raw_auth0_domain:
    # Handle both full URLs (https://domain.auth0.com) and bare domains (domain.auth0.com)
    parsed = urlparse(
        _raw_auth0_domain if "://" in _raw_auth0_domain else f"https://{_raw_auth0_domain}"
    )
    AUTH0_DOMAIN = parsed.netloc if parsed.netloc else parsed.path.strip("/")
    AUTH0_ISSUER = f"https://{AUTH0_DOMAIN}/" if AUTH0_DOMAIN else ""
else:
    AUTH0_DOMAIN = ""
    AUTH0_ISSUER = ""
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
MFA_TOKEN_MAX_AGE_SECONDS = 60

# Singletons initialized lazily
# Note: Functions WITH Depends() parameters cannot use @lru_cache due to FastAPI's
# dependency injection mechanism - they need the global pattern for proper initialization.
_rate_limiter: RateLimiter | None = None
_audit_logger: AuditLogger | None = None
_gateway_authenticator: GatewayAuthenticator | None = None

# Map auth exceptions to HTTP responses for consistent error handling.
AUTH_EXCEPTION_MAP: dict[type[Exception], tuple[int, str, str]] = {
    InvalidSignatureError: (
        status.HTTP_401_UNAUTHORIZED,
        "invalid_signature",
        "Token signature verification failed",
    ),
    TokenExpiredError: (status.HTTP_401_UNAUTHORIZED, "token_expired", "Token has expired"),
    ImmatureSignatureError: (
        status.HTTP_401_UNAUTHORIZED,
        "token_not_valid_yet",
        "Token not yet valid",
    ),
    TokenRevokedError: (status.HTTP_401_UNAUTHORIZED, "token_revoked", "Token has been revoked"),
    TokenReplayedError: (status.HTTP_401_UNAUTHORIZED, "token_replayed", "Token already used"),
    MissingJtiError: (
        status.HTTP_401_UNAUTHORIZED,
        "invalid_token",
        "Token missing required jti claim",
    ),
    InvalidIssuerError: (
        status.HTTP_403_FORBIDDEN,
        "invalid_issuer",
        "Token issuer not trusted",
    ),
    InvalidAudienceError: (
        status.HTTP_403_FORBIDDEN,
        "invalid_audience",
        "Token not intended for this service",
    ),
    SubjectMismatchError: (
        status.HTTP_403_FORBIDDEN,
        "subject_mismatch",
        "Token subject does not match X-User-ID",
    ),
    SessionExpiredError: (
        status.HTTP_403_FORBIDDEN,
        "session_expired",
        "Session invalidated. Please log in again.",
    ),
}


def error_detail(error: str, message: str, retry_after: int | None = None) -> dict[str, Any]:
    """Build consistent error payload with timestamp."""

    detail: dict[str, Any] = {
        "error": error,
        "message": message,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if retry_after is not None:
        detail["retry_after"] = retry_after
    return detail


def _get_required_header(
    request: Request, header_name: str, log_only: bool, default_value: str = ""
) -> str:
    """Extract a required header, falling back to default in log_only mode.

    Reduces repetitive header validation logic for X-User-ID, X-Request-ID, etc.
    """
    value = request.headers.get(header_name)
    if not value:
        if log_only:
            return default_value
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("missing_header", f"{header_name} header required"),
        )
    return value


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_database_url() -> str:
    """Return database URL from secrets (lazy loading)."""
    return get_required_secret("database/url")


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def get_db_pool() -> AsyncConnectionPool:
    """Return async connection pool for auth/session validation.

    Note: open=False allows lazy initialization within async context.
    The pool will open automatically on first use.
    """

    return AsyncConnectionPool(get_database_url(), open=False)


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_async_redis() -> redis_async.Redis:
    """Return shared async Redis client (decode responses for string keys)."""

    return create_async_redis(REDIS_CONFIG, decode_responses=True)


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_sync_redis() -> redis.Redis:
    """Return sync Redis client (used by JWTManager blacklist)."""

    return create_sync_redis(REDIS_CONFIG, decode_responses=True)


def get_rate_limiter(redis_client: redis_async.Redis = Depends(get_async_redis)) -> RateLimiter:
    """Return singleton rate limiter with fail-closed fallback.

    Note: Cannot use @lru_cache because FastAPI's Depends() injection is incompatible with it.
    The global pattern ensures singleton behavior while allowing dependency injection.
    """

    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(redis_client=redis_client, fallback_mode="deny")
    return _rate_limiter


def get_audit_logger(db_pool: AsyncConnectionPool = Depends(get_db_pool)) -> AuditLogger:
    """Return audit logger backed by shared async pool.

    Note: Cannot use @lru_cache because FastAPI's Depends() injection is incompatible with it.
    The global pattern ensures singleton behavior while allowing dependency injection.
    """

    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(db_pool=db_pool)
    return _audit_logger


def build_gateway_authenticator() -> GatewayAuthenticator:
    """Build GatewayAuthenticator without FastAPI DI (for direct calls).

    This function is safe to call directly outside of FastAPI's DI context.
    Uses the cached singleton factories for db_pool and redis_client.
    """
    global _gateway_authenticator
    if _gateway_authenticator is None:
        config = AuthConfig.from_env()
        jwt_manager = JWTManager(config=config, redis_client=get_sync_redis())
        _gateway_authenticator = GatewayAuthenticator(
            jwt_manager=jwt_manager,
            db_pool=get_db_pool(),  # Call cached factory directly
            redis_client=get_async_redis(),  # Call cached factory directly
        )
    return _gateway_authenticator


def get_gateway_authenticator(
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    redis_client: redis_async.Redis = Depends(get_async_redis),
) -> GatewayAuthenticator:
    """Return GatewayAuthenticator configured with JWT manager and Redis.

    Note: Cannot use @lru_cache because FastAPI's Depends() injection is incompatible with it.
    The global pattern ensures singleton behavior while allowing dependency injection.

    For direct calls outside FastAPI DI, use build_gateway_authenticator() instead.
    """
    # Delegate to the non-DI builder which handles the global singleton
    return build_gateway_authenticator()


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_jwks_validator() -> JWKSValidator | None:
    """Return JWKS validator for 2FA tokens, or None if Auth0 is not configured."""

    # Fail-fast: require proper Auth0 configuration for MFA
    if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID:
        return None
    return JWKSValidator(auth0_domain=AUTH0_DOMAIN)


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_db_client() -> DatabaseClient:
    """Database client for manual control operations (sync).

    Callers must run synchronous DB methods in a worker thread (e.g., asyncio.to_thread)
    to avoid blocking the event loop. The manual-controls router wraps all calls via
    its _db_call helper, which is the intended usage pattern here.
    """

    return DatabaseClient(get_database_url())


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_alpaca_executor() -> AlpacaExecutor | None:
    """Create Alpaca executor if credentials are configured."""

    if DRY_RUN:
        return None

    # Use lazy secret loading
    try:
        api_key = get_required_secret("alpaca/api_key_id")
        secret_key = get_required_secret("alpaca/api_secret_key")
    except RuntimeError:
        # Secrets not configured - return None for fail-closed behavior
        logger.warning("Alpaca credentials not configured (DRY_RUN=false)")
        return None

    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    paper_flag = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    if not api_key or not secret_key:
        return None

    try:
        return AlpacaExecutor(
            api_key=api_key, secret_key=secret_key, base_url=base_url, paper=paper_flag
        )
    except (ValueError, TypeError) as exc:
        # Invalid credentials or configuration - fail closed with None
        logger.error(
            "alpaca_executor_init_failed",
            extra={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "base_url": base_url,
                "paper": paper_flag,
            },
            exc_info=True,
        )
        return None
    except (ConnectionError, TimeoutError) as exc:
        # Network error connecting to Alpaca - fail closed with None
        logger.error(
            "alpaca_executor_connection_failed",
            extra={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "base_url": base_url,
            },
            exc_info=True,
        )
        return None


async def get_authenticated_user(
    request: Request,
    authenticator: GatewayAuthenticator = Depends(get_gateway_authenticator),
) -> AuthenticatedUser:
    """Validate headers and return authenticated user context."""

    mode = os.getenv("API_AUTH_MODE", "enforce").lower().strip()
    debug_mode = os.getenv("DEBUG", "false").lower().strip() in ("true", "1", "yes")
    dry_run = os.getenv("DRY_RUN", "true").lower().strip() in ("true", "1", "yes")

    alpaca_paper = os.getenv("ALPACA_PAPER", "true").lower().strip() in (
        "true",
        "1",
        "yes",
    )
    alpaca_base_url = os.getenv("ALPACA_BASE_URL", "").lower().strip()
    paper_url = "paper" in alpaca_base_url if alpaca_base_url else False
    paper_enabled = alpaca_paper or paper_url

    # SECURITY: log_only mode only allowed in debug + non-live trading contexts.
    # This prevents accidental production deployment with auth bypass.
    # In debug mode, allow log_only when DRY_RUN=true OR when paper trading is enabled.
    if mode == "log_only":
        if not debug_mode:
            logger.error(
                "auth_mode_security_violation",
                extra={
                    "mode": mode,
                    "debug": debug_mode,
                    "dry_run": dry_run,
                    "detail": "API_AUTH_MODE=log_only requires DEBUG=true. Falling back to enforce mode.",
                },
            )
            mode = "enforce"
        elif not dry_run and not paper_enabled:
            logger.error(
                "auth_mode_security_violation",
                extra={
                    "mode": mode,
                    "debug": debug_mode,
                    "dry_run": dry_run,
                    "alpaca_paper": alpaca_paper,
                    "alpaca_base_url": alpaca_base_url,
                    "detail": "API_AUTH_MODE=log_only requires DRY_RUN=true or ALPACA_PAPER=true. Falling back to enforce mode.",
                },
            )
            mode = "enforce"
        elif paper_enabled and not dry_run:
            logger.warning(
                "auth_mode_log_only_paper_allowed",
                extra={
                    "mode": mode,
                    "debug": debug_mode,
                    "dry_run": dry_run,
                    "alpaca_paper": alpaca_paper,
                    "alpaca_base_url": alpaca_base_url,
                    "detail": "API_AUTH_MODE=log_only allowed for paper trading in debug mode.",
                },
            )

    log_only = mode == "log_only" and debug_mode and (dry_run or paper_enabled)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        if log_only:
            return _build_dev_fallback_user(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("invalid_token", "Authorization required"),
        )

    token = auth_header[7:]
    user_id = _get_required_header(request, "X-User-ID", log_only)
    request_id = _get_required_header(request, "X-Request-ID", log_only)

    # Validate request_id is valid UUID if present
    try:
        if request_id:
            uuid.UUID(request_id)
    except ValueError as exc:
        if log_only:
            request_id = ""
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_detail("invalid_header", "X-Request-ID must be valid UUID"),
            ) from exc

    session_version_header = _get_required_header(request, "X-Session-Version", log_only)
    try:
        session_version = int(session_version_header) if session_version_header else 0
    except ValueError as exc:
        if log_only:
            session_version = 0
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_detail("invalid_header", "X-Session-Version must be integer"),
            ) from exc

    try:
        return await authenticator.authenticate(
            token=token,
            x_user_id=user_id,
            x_request_id=request_id,
            x_session_version=session_version,
        )
    except AuthError as exc:
        if log_only:
            logger.warning(
                "gateway_auth_failed_log_only",
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            )
            return _build_dev_fallback_user(request)
        if type(exc) in AUTH_EXCEPTION_MAP:
            status_code, code, message = AUTH_EXCEPTION_MAP[type(exc)]
            raise HTTPException(
                status_code=status_code,
                detail=error_detail(code, message),
            ) from exc
        logger.warning(
            "unmapped_auth_error",
            extra={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("invalid_token", "An unexpected authentication error occurred."),
        ) from exc


def _build_dev_fallback_user(request: Request) -> AuthenticatedUser:
    """Return a dev user when API_AUTH_MODE=log_only AND DEBUG=true.

    This is intended for local dev dashboards when auth headers are missing.
    SECURITY: Defaults to OPERATOR role (not ADMIN) as defense-in-depth.
    """

    user_id: str = request.headers.get("X-User-ID") or os.getenv("WEB_CONSOLE_DEV_USER_ID", "dev") or "dev"
    role_value = os.getenv("WEB_CONSOLE_DEV_ROLE", "operator")  # Default to operator, not admin
    try:
        role = Role(role_value)
    except ValueError:
        role = Role.OPERATOR  # Fallback to operator, not admin

    strategies_raw = os.getenv("WEB_CONSOLE_DEV_STRATEGIES", "").strip()
    if strategies_raw:
        strategies = [s.strip() for s in strategies_raw.split(",") if s.strip()]
    else:
        default_strategy = os.getenv("STRATEGY_ID", "").strip()
        strategies = [default_strategy] if default_strategy else []

    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    session_version = int(os.getenv("WEB_CONSOLE_DEV_SESSION_VERSION", "1"))

    return AuthenticatedUser(
        user_id=user_id,
        role=role,
        strategies=strategies,
        session_version=session_version,
        request_id=request_id,
    )


async def check_rate_limit_with_fallback(
    rate_limiter: RateLimiter,
    user_id: str,
    action: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int, bool]:
    """Check rate limit with fail-closed fallback (429).

    Returns:
        (allowed, remaining, is_fallback) - is_fallback=True when denied due to backend error.
    """

    try:
        allowed, remaining = await rate_limiter.check_rate_limit(
            user_id, action, max_requests, window_seconds
        )
        return allowed, remaining, False
    except redis.exceptions.ConnectionError as exc:
        # Redis connection error - fail closed per task doc
        logger.error(
            "rate_limit_redis_connection_error",
            extra={
                "action": action,
                "user_id": user_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
            exc_info=True,
        )
        return False, 0, True
    except redis.exceptions.RedisError as exc:
        # Redis operation error (timeout, pipeline error, etc.) - fail closed
        logger.error(
            "rate_limit_redis_error",
            extra={
                "action": action,
                "user_id": user_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
            exc_info=True,
        )
        return False, 0, True
    except (ValueError, KeyError) as exc:
        # Invalid data structure or missing key - fail closed
        logger.error(
            "rate_limit_data_error",
            extra={
                "action": action,
                "user_id": user_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
            exc_info=True,
        )
        return False, 0, True
    except Exception as exc:
        # Catch-all for unexpected errors - fail closed for safety
        logger.error(
            "rate_limit_unexpected_error",
            extra={
                "action": action,
                "user_id": user_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
            exc_info=True,
        )
        return False, 0, True


async def verify_2fa_token(
    id_token: str,
    requesting_user_id: str,
    jwks_validator: JWKSValidator | None = None,
) -> tuple[bool, str | None, str | None]:
    """Validate 2FA ID token with JWKS and claim checks."""

    if jwks_validator is None:
        jwks_validator = get_jwks_validator()

    # Fail-fast: Auth0 configuration is required for MFA validation
    if jwks_validator is None:
        return False, "mfa_misconfigured", None

    try:
        claims = await jwks_validator.validate_id_token(
            id_token=id_token,
            expected_nonce=None,
            expected_audience=AUTH0_CLIENT_ID,
            expected_issuer=AUTH0_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        return False, "token_expired", None
    except jwt.InvalidIssuerError:
        return False, "invalid_issuer", None
    except jwt.InvalidAudienceError:
        return False, "invalid_audience", None
    except jwt.ImmatureSignatureError:
        return False, "token_not_yet_valid", None
    except httpx.RequestError as exc:
        # Network error fetching JWKS - return specific error for observability
        logger.error(
            "mfa_jwks_fetch_failed",
            extra={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "requesting_user_id": requesting_user_id,
            },
            exc_info=True,
        )
        return False, "mfa_unavailable", None
    except (jwt.InvalidTokenError, jwt.DecodeError) as exc:
        # JWT decode/validation error (malformed token, invalid signature, etc.)
        logger.error(
            "mfa_token_validation_failed",
            extra={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "requesting_user_id": requesting_user_id,
            },
            exc_info=True,
        )
        return False, "invalid_jwt", None
    except (ValueError, KeyError) as exc:
        # Invalid data structure or missing claims
        logger.error(
            "mfa_token_data_error",
            extra={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "requesting_user_id": requesting_user_id,
            },
            exc_info=True,
        )
        return False, "invalid_jwt", None

    if claims.get("sub") != requesting_user_id:
        return False, "token_mismatch", None

    amr = claims.get("amr", [])
    mfa_methods = {"mfa", "otp", "sms", "push", "webauthn", "hwk"}
    if not any(method in amr for method in mfa_methods):
        return False, "mfa_required", None

    auth_time = claims.get("auth_time")
    if not auth_time:
        return False, "mfa_required", None

    auth_age = (datetime.now(UTC) - datetime.fromtimestamp(int(auth_time), tz=UTC)).total_seconds()
    if auth_age < 0:
        return False, "token_not_yet_valid", None
    if auth_age > MFA_TOKEN_MAX_AGE_SECONDS:
        return False, "mfa_expired", None

    amr_method = next(
        (m for m in ["webauthn", "hwk", "otp", "sms", "push", "mfa"] if m in amr), None
    )
    return True, None, amr_method


def get_2fa_validator() -> TwoFaValidator:
    """Return the 2FA verification callable (overridable in tests)."""

    return verify_2fa_token


def ensure_permission(user: AuthenticatedUser, permission: Permission) -> None:
    """Fail-fast permission check with HTTPException."""

    if not has_permission(user, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail(
                "permission_denied",
                f"Permission {permission.name} required",
            ),
        )


__all__ = [
    "get_authenticated_user",
    "get_rate_limiter",
    "get_audit_logger",
    "get_gateway_authenticator",
    "get_db_pool",
    "get_db_client",
    "get_alpaca_executor",
    "error_detail",
    "check_rate_limit_with_fallback",
    "verify_2fa_token",
    "get_2fa_validator",
    "ensure_permission",
]
