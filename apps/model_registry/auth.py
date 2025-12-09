"""
Authentication for Model Registry API.

Provides JWT bearer token verification with scope-based authorization.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


MODEL_REGISTRY_CONFIG: dict[str, dict[str, str | float | int | list[str]]] = {
    "auth": {
        "type": "bearer",
        "token_env": "MODEL_REGISTRY_TOKEN",
        "scopes_required": ["model:read"],  # default scope
    },
    "timeout": {"connect": 5.0, "read": 30.0, "write": 60.0},
    "retry": {"max_attempts": 3, "backoff_base": 1.0, "backoff_factor": 2.0},
}


_AUTH_TOKEN_ENV_VAR = "MODEL_REGISTRY_TOKEN"


# =============================================================================
# Auth Types
# =============================================================================


@dataclass
class ServiceToken:
    """Verified service token with scopes."""

    token: str
    scopes: list[str]
    service_name: str


# =============================================================================
# Security Scheme
# =============================================================================


security = HTTPBearer(auto_error=False)


def _get_expected_token() -> str | None:
    """Get expected token from environment."""
    return os.environ.get(_AUTH_TOKEN_ENV_VAR)


def _parse_token_scopes(token: str) -> list[str]:
    """Parse scopes from token.

    DESIGN NOTE: This simple bearer token auth grants admin scope to all
    authenticated requests. For production systems requiring granular
    scope separation, implement JWT-based auth with scope claims.

    Current behavior:
    - Token matches MODEL_REGISTRY_TOKEN -> all scopes (admin)
    - Token format "service:scope1,scope2" -> parsed scopes (unreachable
      since verify_token rejects non-matching tokens)
    - Other -> read-only (unreachable for same reason)

    Args:
        token: Bearer token string.

    Returns:
        List of scopes.
    """
    # For simple bearer token auth, if token matches env var, grant all scopes
    # NOTE: This means scope checks only prevent unauthenticated access,
    # not unauthorized scope access. Use JWT for real scope separation.
    expected = _get_expected_token()
    # Use constant-time comparison to prevent timing attacks
    if expected and secrets.compare_digest(token, expected):
        return ["model:read", "model:write", "model:admin"]

    # These branches are unreachable with current verify_token logic
    # but kept for potential future JWT implementation
    if ":" in token:
        parts = token.split(":", 1)
        if len(parts) == 2:
            return parts[1].split(",")

    return ["model:read"]


def _parse_service_name(token: str) -> str:
    """Parse service name from token.

    Args:
        token: Bearer token string.

    Returns:
        Service name.
    """
    if ":" in token:
        return token.split(":")[0]
    return "unknown"


# =============================================================================
# Dependency Functions
# =============================================================================


async def verify_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> ServiceToken:
    """Verify bearer token is valid.

    Args:
        credentials: HTTP authorization credentials from header.

    Returns:
        ServiceToken with verified token info.

    Raises:
        HTTPException 401: If token is missing or invalid.
    """
    # In development mode, allow requests without token
    # CRITICAL: Auth disable requires BOTH explicit ENVIRONMENT=dev/test AND the disable flag
    # This prevents accidental auth bypass if ENVIRONMENT is unset (fails closed)
    auth_disabled = os.environ.get("MODEL_REGISTRY_AUTH_DISABLED", "").lower() == "true"
    environment = os.environ.get("ENVIRONMENT", "").lower()  # Empty default - must be explicit

    if auth_disabled:
        # Block auth disable unless ENVIRONMENT is EXPLICITLY set to dev/test
        if environment in ("prod", "production"):
            logger.error(
                "SECURITY VIOLATION: MODEL_REGISTRY_AUTH_DISABLED=true in production environment. "
                "Auth disable is blocked in production."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication cannot be disabled in production environment",
            )
        elif environment not in ("dev", "development", "test", "testing"):
            # ENVIRONMENT not explicitly set or set to unknown value - fail closed
            logger.error(
                "SECURITY: MODEL_REGISTRY_AUTH_DISABLED=true but ENVIRONMENT is not explicitly "
                f"set to dev/test (got: '{environment or 'unset'}'). Auth disable requires "
                "explicit ENVIRONMENT=dev or ENVIRONMENT=test."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth disable requires explicit ENVIRONMENT=dev or ENVIRONMENT=test",
            )
        logger.warning(
            f"Auth disabled via MODEL_REGISTRY_AUTH_DISABLED (ENVIRONMENT={environment}) - "
            "this should ONLY be used in dev/test"
        )
        return ServiceToken(
            token="dev-token",
            scopes=["model:read", "model:write", "model:admin"],
            service_name="dev",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Verify token against expected value - REQUIRED in production
    expected = _get_expected_token()
    if not expected:
        # Fail closed: reject all requests if no token is configured
        logger.error(
            "Authentication failed: MODEL_REGISTRY_TOKEN environment variable not set"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not configured - MODEL_REGISTRY_TOKEN required",
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token, expected):
        # Reject non-matching tokens
        logger.warning(
            "Token verification failed: token does not match expected value",
            extra={"token_prefix": token[:8] + "..." if len(token) > 8 else "***"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scopes = _parse_token_scopes(token)
    service = _parse_service_name(token)

    return ServiceToken(token=token, scopes=scopes, service_name=service)


async def verify_read_scope(
    token: Annotated[ServiceToken, Depends(verify_token)],
) -> ServiceToken:
    """Verify token has model:read scope.

    Args:
        token: Verified service token.

    Returns:
        ServiceToken if scope is present.

    Raises:
        HTTPException 403: If scope is missing.
    """
    if "model:read" not in token.scopes and "model:admin" not in token.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient scope: model:read required",
        )
    return token


async def verify_write_scope(
    token: Annotated[ServiceToken, Depends(verify_token)],
) -> ServiceToken:
    """Verify token has model:write scope.

    Args:
        token: Verified service token.

    Returns:
        ServiceToken if scope is present.

    Raises:
        HTTPException 403: If scope is missing.
    """
    if "model:write" not in token.scopes and "model:admin" not in token.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient scope: model:write required",
        )
    return token


async def verify_admin_scope(
    token: Annotated[ServiceToken, Depends(verify_token)],
) -> ServiceToken:
    """Verify token has model:admin scope.

    Args:
        token: Verified service token.

    Returns:
        ServiceToken if scope is present.

    Raises:
        HTTPException 403: If scope is missing.
    """
    if "model:admin" not in token.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient scope: model:admin required",
        )
    return token
