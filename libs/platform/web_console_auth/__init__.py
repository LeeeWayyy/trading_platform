"""Web Console Authentication Library.

Provides JWT token generation and validation for the Web Console authentication system.
This library implements mTLS + JWT authentication for Phase 2 (Paper Trading),
with forward compatibility for OAuth2/OIDC migration in Phase 3 (Production).

Key Components:
- JWTManager: RS256 token generation, validation, and revocation
- SessionManager: Session creation, refresh, validation, and rate limiting
- AuthConfig: Configuration with secure defaults
- OAuth2FlowHandler: OAuth2/OIDC authorization code flow with PKCE
- JWKSValidator: JWT ID token validation using Auth0 JWKS
"""

from libs.platform.web_console_auth.api_client import (
    call_api_with_auth,
    get_access_token_from_redis,
)
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.config import AuthConfig
from libs.platform.web_console_auth.exceptions import (
    AuthError,
    InvalidTokenError,
    RateLimitExceededError,
    SessionLimitExceededError,
    TokenExpiredError,
    TokenRevokedError,
)
from libs.platform.web_console_auth.idp_health import IdPHealthChecker, IdPHealthStatus
from libs.platform.web_console_auth.jwks_validator import JWKSValidator
from libs.platform.web_console_auth.jwt_manager import JWTManager
from libs.platform.web_console_auth.mfa_verification import (
    get_amr_method,
    require_2fa_for_action,
    verify_step_up_auth,
)
from libs.platform.web_console_auth.mtls_fallback import (
    CertificateInfo,
    CRLCache,
    MtlsFallbackValidator,
    get_admin_cn_allowlist,
    get_crl_url,
    is_fallback_enabled,
)
from libs.platform.web_console_auth.oauth2_flow import (
    OAuth2Config,
    OAuth2FlowHandler,
)
from libs.platform.web_console_auth.oauth2_state import OAuth2State, OAuth2StateStore
from libs.platform.web_console_auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_authorized_strategies,
    has_permission,
    require_permission,
)
from libs.platform.web_console_auth.pkce import (
    generate_nonce,
    generate_pkce_challenge,
    generate_session_id,
    generate_state,
)
from libs.platform.web_console_auth.session import SessionManager
from libs.platform.web_console_auth.session_invalidation import (
    invalidate_user_sessions,
    validate_session_version,
)
from libs.platform.web_console_auth.session_store import (
    RedisSessionStore,
    SessionData,
)
from libs.platform.web_console_auth.step_up_callback import (
    SecurityError,
    clear_step_up_state,
    handle_step_up_callback,
)

__all__ = [
    # Core auth
    "AuthConfig",
    "AuthError",
    "InvalidTokenError",
    "JWTManager",
    "RateLimitExceededError",
    "SessionLimitExceededError",
    "SessionManager",
    "TokenExpiredError",
    "TokenRevokedError",
    # Permissions
    "Permission",
    "ROLE_PERMISSIONS",
    "Role",
    "get_authorized_strategies",
    "has_permission",
    "require_permission",
    # OAuth2/OIDC
    "OAuth2Config",
    "OAuth2FlowHandler",
    "OAuth2State",
    "OAuth2StateStore",
    "JWKSValidator",
    # PKCE
    "generate_nonce",
    "generate_pkce_challenge",
    "generate_session_id",
    "generate_state",
    # Session management
    "RedisSessionStore",
    "SessionData",
    "invalidate_user_sessions",
    "validate_session_version",
    # API client
    "call_api_with_auth",
    "get_access_token_from_redis",
    # Audit
    "AuditLogger",
    # MFA
    "get_amr_method",
    "require_2fa_for_action",
    "verify_step_up_auth",
    "SecurityError",
    "clear_step_up_state",
    "handle_step_up_callback",
    # Health & fallback
    "IdPHealthChecker",
    "IdPHealthStatus",
    "CRLCache",
    "CertificateInfo",
    "MtlsFallbackValidator",
    "get_admin_cn_allowlist",
    "get_crl_url",
    "is_fallback_enabled",
]
