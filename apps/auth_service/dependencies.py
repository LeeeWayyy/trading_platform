"""Shared dependencies for FastAPI auth service.

Uses functools.lru_cache for singleton pattern (similar to @st.cache_resource).
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from typing import TYPE_CHECKING

import redis.asyncio

from apps.web_console.auth.jwks_validator import JWKSValidator
from apps.web_console.auth.oauth2_flow import OAuth2Config, OAuth2FlowHandler
from apps.web_console.auth.oauth2_state import OAuth2StateStore
from apps.web_console.auth.rate_limiter import RedisRateLimiter
from apps.web_console.auth.session_store import RedisSessionStore

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


# UNIFIED CONFIG: Combines Auth0 params + cookie domain
class AuthServiceConfig:
    """Auth service configuration (Auth0 + cookie domain)."""

    def __init__(self) -> None:
        self.auth0_domain: str = os.getenv("AUTH0_DOMAIN") or ""
        self.client_id: str = os.getenv("AUTH0_CLIENT_ID") or ""
        self.client_secret: str = os.getenv("AUTH0_CLIENT_SECRET") or ""
        self.audience: str = os.getenv("AUTH0_AUDIENCE") or ""
        self.redirect_uri: str = os.getenv("OAUTH2_REDIRECT_URI") or ""
        self.logout_redirect_uri: str = os.getenv("OAUTH2_LOGOUT_REDIRECT_URI") or ""
        self.cookie_domain: str | None = os.getenv("COOKIE_DOMAIN")  # For HttpOnly cookies

    def to_oauth2_config(self) -> OAuth2Config:
        """Convert to OAuth2Config for OAuth2FlowHandler."""
        return OAuth2Config(
            auth0_domain=self.auth0_domain,
            client_id=self.client_id,
            client_secret=self.client_secret,
            audience=self.audience,
            redirect_uri=self.redirect_uri,
            logout_redirect_uri=self.logout_redirect_uri,
        )


@lru_cache
def get_redis_client() -> redis.asyncio.Redis:
    """Get Redis client singleton (DB 1 for sessions)."""
    return redis.asyncio.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=1,  # Sessions + OAuth2 state
        decode_responses=False,
    )


@lru_cache
def get_db_pool() -> AsyncConnectionPool | None:
    """Get async database connection pool for session invalidation checks."""

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None

    try:
        import psycopg_pool

        min_size = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
        max_size = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
        return psycopg_pool.AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
        )
    except Exception:
        # Fail-open to preserve existing behaviour if psycopg_pool unavailable
        return None


@lru_cache
def get_config() -> AuthServiceConfig:
    """Get auth service config singleton."""
    return AuthServiceConfig()


@lru_cache
def get_oauth2_handler() -> OAuth2FlowHandler:
    """Get OAuth2 flow handler singleton."""
    redis_client = get_redis_client()
    config = get_config()

    # Initialize components
    session_store = RedisSessionStore(
        redis_client=redis_client,
        encryption_key=get_encryption_key(),
    )

    state_store = OAuth2StateStore(redis_client=redis_client)

    jwks_validator = JWKSValidator(auth0_domain=config.auth0_domain)

    return OAuth2FlowHandler(
        config=config.to_oauth2_config(),  # Convert to OAuth2Config
        session_store=session_store,
        state_store=state_store,
        jwks_validator=jwks_validator,
        db_pool=get_db_pool(),
    )


@lru_cache
def get_rate_limiters() -> dict[str, RedisRateLimiter]:
    """Get rate limiters singleton."""
    redis_client = get_redis_client()

    return {
        "callback": RedisRateLimiter(
            redis_client=redis_client,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        ),
        "refresh": RedisRateLimiter(
            redis_client=redis_client,
            max_requests=5,
            window_seconds=60,
            key_prefix="rate_limit:refresh:",
        ),
    }


def get_encryption_key() -> bytes:
    """Get session encryption key from environment.

    Expected format: Base64-encoded 32-byte key
    Example: SESSION_ENCRYPTION_KEY=$(python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())")
    """
    key_b64 = os.getenv("SESSION_ENCRYPTION_KEY")
    if not key_b64:
        raise ValueError("SESSION_ENCRYPTION_KEY environment variable not set")

    try:
        key_bytes = base64.b64decode(key_b64)
    except (ValueError, TypeError) as e:
        raise ValueError(f"SESSION_ENCRYPTION_KEY must be base64-encoded: {e}") from e

    if len(key_bytes) != 32:
        raise ValueError(f"SESSION_ENCRYPTION_KEY must decode to 32 bytes (got {len(key_bytes)})")

    return key_bytes
