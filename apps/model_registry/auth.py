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
        "token_env": "MODEL_REGISTRY_READ_TOKEN",
        "scopes_required": ["model:read"],  # default scope
    },
    "timeout": {"connect": 5.0, "read": 30.0, "write": 60.0},
    "retry": {"max_attempts": 3, "backoff_base": 1.0, "backoff_factor": 2.0},
}

_AUTH_TOKEN_ENV_VAR = "MODEL_REGISTRY_TOKEN"  # Legacy shared token (read-only fallback)
_READ_TOKEN_ENV_VAR = "MODEL_REGISTRY_READ_TOKEN"
_ADMIN_TOKEN_ENV_VAR = "MODEL_REGISTRY_ADMIN_TOKEN"


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


def _get_expected_tokens() -> dict[str, str]:
    """Get configured tokens keyed by access level."""

    tokens: dict[str, str] = {}

    admin_token = os.environ.get(_ADMIN_TOKEN_ENV_VAR)
    read_token = os.environ.get(_READ_TOKEN_ENV_VAR)
    legacy_token = os.environ.get(_AUTH_TOKEN_ENV_VAR)

    if admin_token:
        tokens["admin"] = admin_token
    if read_token:
        tokens["read"] = read_token
    if legacy_token:
        # Legacy shared token is treated as read-only for safety
        tokens["legacy_read"] = legacy_token

    return tokens


def _parse_token_scopes(token: str) -> list[str]:
    """Parse scopes from token.

    DESIGN NOTE: For production systems requiring granular scope separation,
    implement JWT-based auth with scope claims. This helper only supports
    shared bearer tokens with two tiers of access.

    Current behavior:
    - MODEL_REGISTRY_ADMIN_TOKEN -> admin scopes
    - MODEL_REGISTRY_READ_TOKEN or legacy MODEL_REGISTRY_TOKEN -> read-only
    - Unknown/unsigned tokens -> no scopes (fail closed)

    Args:
        token: Bearer token string.

    Returns:
        List of scopes.
    """
    expected_tokens = _get_expected_tokens()
    admin_token = expected_tokens.get("admin")
    read_tokens = [
        expected_tokens.get("read"),
        expected_tokens.get("legacy_read"),
    ]

    # Admin token is explicitly configured and grants full scopes
    if admin_token and secrets.compare_digest(token, admin_token):
        return ["model:read", "model:write", "model:admin"]

    # Shared/legacy tokens are read-only for safety
    for candidate in read_tokens:
        if candidate and secrets.compare_digest(token, candidate):
            return ["model:read"]

    # Unknown token -> no scopes.  IMPORTANT: Do **not** accept arbitrary
    # "service:scope" bearer tokens here because the token value has not been
    # authenticated. Allowing free-form scopes would let any caller mint an
    # admin token by sending `foo:model:admin`. Until we introduce signed JWTs
    # or HMAC tokens, only configured secrets are trusted.
    return []


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
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    configured_tokens = _get_expected_tokens()
    if not configured_tokens:
        # Fail closed: reject all requests if no token is configured
        logger.error(
            "Authentication failed: no MODEL_REGISTRY_* tokens configured"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Authentication not configured - set MODEL_REGISTRY_READ_TOKEN and/or "
                "MODEL_REGISTRY_ADMIN_TOKEN"
            ),
        )

    scopes = _parse_token_scopes(token)
    if not scopes:
        logger.warning(
            "Token verification failed: token not recognized for configured scopes",
            extra={"token_prefix": token[:8] + "..." if len(token) > 8 else "***"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
