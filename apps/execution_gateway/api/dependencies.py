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

import jwt
import redis
import redis.asyncio as redis_async
from fastapi import Depends, HTTPException, Request, status
from psycopg_pool import AsyncConnectionPool

from apps.execution_gateway.alpaca_client import AlpacaExecutor
from apps.execution_gateway.database import DatabaseClient
from libs.web_console_auth.audit_logger import AuditLogger
from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import (
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
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
from libs.web_console_auth.permissions import Permission, has_permission
from libs.web_console_auth.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

TwoFaResult = tuple[bool, str | None, str | None]
TwoFaValidator = Callable[[str, str], Awaitable[TwoFaResult]]

# Environment configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trader:trader@localhost:5433/trader")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

# Parse REDIS_PORT with safe fallback
try:
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
except ValueError:
    logger.warning("Invalid REDIS_PORT, using default 6379")
    REDIS_PORT = 6379

# Parse REDIS_DB with safe fallback
try:
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))
except ValueError:
    logger.warning("Invalid REDIS_DB, using default 0")
    REDIS_DB = 0

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
_rate_limiter: RateLimiter | None = None
_audit_logger: AuditLogger | None = None
_gateway_authenticator: GatewayAuthenticator | None = None
_jwks_validator: JWKSValidator | None = None
_db_client: DatabaseClient | None = None
_alpaca_executor: AlpacaExecutor | None = None


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


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def get_db_pool() -> AsyncConnectionPool:
    """Return async connection pool for auth/session validation."""

    return AsyncConnectionPool(DATABASE_URL, open=True)


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_async_redis() -> redis_async.Redis:
    """Return shared async Redis client (decode responses for string keys)."""

    return redis_async.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)


@lru_cache(maxsize=None)  # noqa: UP033 - thread-safe singleton with lru_cache
def get_sync_redis() -> redis.Redis:
    """Return sync Redis client (used by JWTManager blacklist)."""

    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)


def get_rate_limiter(redis_client: redis_async.Redis = Depends(get_async_redis)) -> RateLimiter:
    """Return singleton rate limiter with fail-closed fallback."""

    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(redis_client=redis_client, fallback_mode="deny")
    return _rate_limiter


def get_audit_logger(db_pool: AsyncConnectionPool = Depends(get_db_pool)) -> AuditLogger:
    """Return audit logger backed by shared async pool."""

    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(db_pool=db_pool)
    return _audit_logger


def get_gateway_authenticator(
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    redis_client: redis_async.Redis = Depends(get_async_redis),
) -> GatewayAuthenticator:
    """Return GatewayAuthenticator configured with JWT manager and Redis."""

    global _gateway_authenticator
    if _gateway_authenticator is None:
        config = AuthConfig.from_env()
        jwt_manager = JWTManager(config=config, redis_client=get_sync_redis())
        _gateway_authenticator = GatewayAuthenticator(
            jwt_manager=jwt_manager,
            db_pool=db_pool,
            redis_client=redis_client,
        )
    return _gateway_authenticator


def get_jwks_validator() -> JWKSValidator | None:
    """Return JWKS validator for 2FA tokens, or None if Auth0 is not configured."""

    global _jwks_validator
    if _jwks_validator is None:
        # Fail-fast: require proper Auth0 configuration for MFA
        if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID:
            return None
        _jwks_validator = JWKSValidator(auth0_domain=AUTH0_DOMAIN)
    return _jwks_validator


def get_db_client() -> DatabaseClient:
    """Database client for manual control operations (sync)."""

    global _db_client
    if _db_client is None:
        _db_client = DatabaseClient(DATABASE_URL)
    return _db_client


def get_alpaca_executor() -> AlpacaExecutor | None:
    """Create Alpaca executor if credentials are configured."""

    if DRY_RUN:
        return None

    global _alpaca_executor
    if _alpaca_executor is not None:
        return _alpaca_executor

    api_key = os.getenv("ALPACA_API_KEY_ID", "")
    secret_key = os.getenv("ALPACA_API_SECRET_KEY", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    paper_flag = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    if not api_key or not secret_key:
        return None

    try:
        _alpaca_executor = AlpacaExecutor(
            api_key=api_key, secret_key=secret_key, base_url=base_url, paper=paper_flag
        )
    except Exception:
        # Keep None to allow fail-closed responses; actual errors logged by caller
        logger.exception("alpaca_executor_init_failed")
        _alpaca_executor = None
    return _alpaca_executor


async def get_authenticated_user(
    request: Request,
    authenticator: GatewayAuthenticator = Depends(get_gateway_authenticator),
) -> AuthenticatedUser:
    """Validate headers and return authenticated user context."""

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("invalid_token", "Authorization required"),
        )

    token = auth_header[7:]
    user_id = request.headers.get("X-User-ID")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("missing_header", "X-User-ID header required"),
        )

    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("missing_header", "X-Request-ID header required"),
        )
    try:
        uuid.UUID(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("invalid_header", "X-Request-ID must be valid UUID"),
        ) from exc

    session_version_header = request.headers.get("X-Session-Version")
    if not session_version_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("missing_header", "X-Session-Version header required"),
        )
    try:
        session_version = int(session_version_header)
    except ValueError as exc:
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
    except InvalidSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("invalid_signature", "Token signature verification failed"),
        ) from exc
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("token_expired", "Token has expired"),
        ) from exc
    except ImmatureSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("token_not_valid_yet", "Token not yet valid"),
        ) from exc
    except TokenRevokedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("token_revoked", "Token has been revoked"),
        ) from exc
    except TokenReplayedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("token_replayed", "Token already used"),
        ) from exc
    except MissingJtiError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("invalid_token", "Token missing required jti claim"),
        ) from exc
    except InvalidIssuerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("invalid_issuer", "Token issuer not trusted"),
        ) from exc
    except InvalidAudienceError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("invalid_audience", "Token not intended for this service"),
        ) from exc
    except SubjectMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("subject_mismatch", "Token subject does not match X-User-ID"),
        ) from exc
    except SessionExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("session_expired", "Session invalidated. Please log in again."),
        ) from exc
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_detail("invalid_token", str(exc)),
        ) from exc


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
    except Exception as exc:
        # Fail closed per task doc - log error for debugging
        logger.warning(
            "rate_limit_fallback",
            extra={"action": action, "user_id": user_id, "error": str(exc)},
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
            expected_issuer=AUTH0_ISSUER or f"https://{AUTH0_DOMAIN}/",
        )
    except jwt.ExpiredSignatureError:
        return False, "token_expired", None
    except jwt.InvalidIssuerError:
        return False, "invalid_issuer", None
    except jwt.InvalidAudienceError:
        return False, "invalid_audience", None
    except jwt.ImmatureSignatureError:
        return False, "token_not_yet_valid", None
    except Exception:
        logger.exception("mfa_token_validation_failed")
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
