"""Web Console Authentication Library.

Provides JWT token generation and validation for the Web Console authentication system.
This library implements mTLS + JWT authentication for Phase 2 (Paper Trading),
with forward compatibility for OAuth2/OIDC migration in Phase 3 (Production).

Key Components:
- JWTManager: RS256 token generation, validation, and revocation
- SessionManager: Session creation, refresh, validation, and rate limiting
- AuthConfig: Configuration with secure defaults
"""

from libs.platform.web_console_auth.config import AuthConfig
from libs.platform.web_console_auth.exceptions import (
    AuthError,
    InvalidTokenError,
    RateLimitExceededError,
    SessionLimitExceededError,
    TokenExpiredError,
    TokenRevokedError,
)
from libs.platform.web_console_auth.jwt_manager import JWTManager
from libs.platform.web_console_auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_authorized_strategies,
    has_permission,
    require_permission,
)
from libs.platform.web_console_auth.session import SessionManager

__all__ = [
    "AuthConfig",
    "AuthError",
    "InvalidTokenError",
    "JWTManager",
    "RateLimitExceededError",
    "SessionLimitExceededError",
    "SessionManager",
    "Permission",
    "ROLE_PERMISSIONS",
    "Role",
    "get_authorized_strategies",
    "has_permission",
    "require_permission",
    "TokenExpiredError",
    "TokenRevokedError",
]
