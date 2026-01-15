"""Authentication configuration for the Web Console."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AuthConfig:
    """Authentication configuration with secure defaults.

    All settings can be overridden via environment variables using from_env().
    """

    # JWT Settings
    # NOTE: Certs moved to web_console_ng as part of web_console migration
    jwt_private_key_path: Path = Path("apps/web_console_ng/certs/jwt_private.key")
    jwt_public_key_path: Path = Path("apps/web_console_ng/certs/jwt_public.pem")
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "trading-platform-web-console"  # Issuer claim (prevents token confusion)
    jwt_audience: str = "trading-platform-api"  # Audience claim (prevents cross-service replay)

    # Token Expiration
    access_token_ttl: int = 900  # 15 minutes
    refresh_token_ttl: int = 14400  # 4 hours

    # Clock Skew Tolerance
    clock_skew_seconds: int = 30  # Accept tokens up to 30s in the future

    # Session Settings
    max_sessions_per_user: int = 3
    session_binding_strict: bool = True  # Reject IP/UA mismatch

    # Rate Limiting (Task Document Requirement)
    rate_limit_window: int = 900  # 15 minutes
    rate_limit_max_attempts: int = 5  # Max attempts per window per IP
    rate_limit_enabled: bool = True

    # Cookie Security Parameters (Codex Recommendation #3)
    cookie_secure: bool = True  # HTTPS-only
    cookie_httponly: bool = True  # No JavaScript access
    cookie_samesite: str = "Strict"  # CSRF protection
    cookie_domain: str | None = None  # Set to domain for subdomain sharing
    cookie_path: str = "/"
    cookie_max_age: int | None = None  # Defaults to refresh_token_ttl when None

    # Redis Keys
    redis_session_prefix: str = "web_console:session:"
    redis_blacklist_prefix: str = "web_console:token_blacklist:"
    redis_session_index_prefix: str = "web_console:user_sessions:"
    redis_rate_limit_prefix: str = "web_console:rate_limit:"

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Load configuration from environment variables.

        Environment variable mapping:
        - JWT_PRIVATE_KEY_PATH: Path to JWT private key
        - JWT_PUBLIC_KEY_PATH: Path to JWT public key
        - ACCESS_TOKEN_TTL: Access token expiration in seconds
        - REFRESH_TOKEN_TTL: Refresh token expiration in seconds
        - MAX_SESSIONS_PER_USER: Maximum concurrent sessions
        - SESSION_BINDING_STRICT: Enable strict IP/UA binding (true/false)
        - RATE_LIMIT_WINDOW: Rate limit window in seconds
        - RATE_LIMIT_MAX_ATTEMPTS: Max attempts per window
        - RATE_LIMIT_ENABLED: Enable rate limiting (true/false)
        - COOKIE_SECURE: Enable secure cookie flag (true/false)
        - COOKIE_DOMAIN: Cookie domain for subdomain sharing
        """
        return cls(
            jwt_private_key_path=Path(
                os.getenv("JWT_PRIVATE_KEY_PATH", "apps/web_console_ng/certs/jwt_private.key")
            ),
            jwt_public_key_path=Path(
                os.getenv("JWT_PUBLIC_KEY_PATH", "apps/web_console_ng/certs/jwt_public.pem")
            ),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "RS256"),
            jwt_issuer=os.getenv("JWT_ISSUER", "trading-platform-web-console"),
            jwt_audience=os.getenv("JWT_AUDIENCE", "trading-platform-api"),
            access_token_ttl=int(os.getenv("ACCESS_TOKEN_TTL", "900")),
            refresh_token_ttl=int(os.getenv("REFRESH_TOKEN_TTL", "14400")),
            clock_skew_seconds=int(os.getenv("CLOCK_SKEW_SECONDS", "30")),
            max_sessions_per_user=int(os.getenv("MAX_SESSIONS_PER_USER", "3")),
            session_binding_strict=os.getenv("SESSION_BINDING_STRICT", "true").lower() == "true",
            rate_limit_window=int(os.getenv("RATE_LIMIT_WINDOW", "900")),
            rate_limit_max_attempts=int(os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "5")),
            rate_limit_enabled=os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true",
            cookie_secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
            cookie_httponly=os.getenv("COOKIE_HTTPONLY", "true").lower() == "true",
            cookie_samesite=os.getenv("COOKIE_SAMESITE", "Strict"),
            cookie_domain=os.getenv("COOKIE_DOMAIN"),
            cookie_path=os.getenv("COOKIE_PATH", "/"),
            cookie_max_age=(
                int(cookie_max_age_str)
                if (cookie_max_age_str := os.getenv("COOKIE_MAX_AGE"))
                else None
            ),
        )
