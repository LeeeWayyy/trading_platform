"""
Authentication for Model Registry API.

Provides bearer token verification with scope-based authorization.
"""

from __future__ import annotations

import functools
import logging
import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


class _AuthConfig(BaseModel, frozen=True):
    """Auth section of model registry configuration."""

    type: str = "bearer"
    token_env: str = "MODEL_REGISTRY_READ_TOKEN"


class _TimeoutConfig(BaseModel, frozen=True):
    """Timeout section of model registry configuration."""

    connect: float = 5.0
    read: float = 30.0
    write: float = 60.0


class _RetryConfig(BaseModel, frozen=True):
    """Retry section of model registry configuration."""

    max_attempts: int = 3
    backoff_base: float = 1.0
    backoff_factor: float = 2.0


class ModelRegistryConfig(BaseModel, frozen=True):
    """Model registry configuration."""

    auth: _AuthConfig = _AuthConfig()
    timeout: _TimeoutConfig = _TimeoutConfig()
    retry: _RetryConfig = _RetryConfig()


MODEL_REGISTRY_CONFIG: ModelRegistryConfig = ModelRegistryConfig()

_AUTH_TOKEN_ENV_VAR = "MODEL_REGISTRY_TOKEN"  # Legacy shared token (read-only fallback)
_READ_TOKEN_ENV_VAR = "MODEL_REGISTRY_READ_TOKEN"
_ADMIN_TOKEN_ENV_VAR = "MODEL_REGISTRY_ADMIN_TOKEN"

_ADMIN_SCOPES: tuple[str, ...] = ("model:read", "model:write", "model:admin")
_READ_SCOPES: tuple[str, ...] = ("model:read",)


# =============================================================================
# Auth Types
# =============================================================================


class ServiceToken(BaseModel, frozen=True):
    """Verified service token with scopes."""

    scopes: list[str]
    auth_role: str


# =============================================================================
# Security Scheme
# =============================================================================


security = HTTPBearer(auto_error=False)


@functools.lru_cache(maxsize=1)
def _get_expected_tokens() -> dict[str, str]:
    """Get configured tokens keyed by access level.

    Results are cached for the process lifetime.  Call
    ``_get_expected_tokens.cache_clear()`` when environment variables
    change (e.g. in tests).
    """

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


def _authenticate_token(
    token: str,
    expected_tokens: dict[str, str] | None = None,
) -> tuple[list[str], str] | None:
    """Authenticate a bearer token and return its scopes and role label.

    Performs a single pass over configured tokens using constant-time
    comparison.  Returns both the granted scopes and a safe, non-secret
    role label (e.g. "admin", "read") so callers never need to iterate
    the token list twice.

    DESIGN NOTE: For production systems requiring granular scope separation,
    implement JWT-based auth with scope claims. This helper only supports
    shared bearer tokens with two tiers of access.

    Current behavior:
    - MODEL_REGISTRY_ADMIN_TOKEN -> admin scopes, role "admin"
    - MODEL_REGISTRY_READ_TOKEN -> read-only scopes, role "read"
    - Legacy MODEL_REGISTRY_TOKEN -> read-only scopes, role "legacy_read"
    - Unknown/unsigned tokens -> ``None`` (fail closed)

    The role label is never derived from token content to avoid leaking
    credential material into logs (fixes #174).

    Args:
        token: Bearer token string.
        expected_tokens: Pre-fetched token map.  When ``None`` the tokens
            are loaded from environment variables via ``_get_expected_tokens``.

    Returns:
        ``(scopes, role)`` tuple when the token matches a configured
        secret, or ``None`` for unrecognised tokens.
    """
    if expected_tokens is None:
        expected_tokens = _get_expected_tokens()

    # Admin token is explicitly configured and grants full scopes
    admin_token = expected_tokens.get("admin")
    if admin_token and secrets.compare_digest(token, admin_token):
        return list(_ADMIN_SCOPES), "admin"

    # Shared/legacy tokens are read-only for safety
    for role in ("read", "legacy_read"):
        candidate = expected_tokens.get(role)
        if candidate and secrets.compare_digest(token, candidate):
            return list(_READ_SCOPES), role

    # Unknown token -> no match.  IMPORTANT: Do **not** accept arbitrary
    # "service:scope" bearer tokens here because the token value has not been
    # authenticated. Allowing free-form scopes would let any caller mint an
    # admin token by sending `foo:model:admin`. Until we introduce signed JWTs
    # or HMAC tokens, only configured secrets are trusted.
    return None


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
        logger.error("Authentication failed: no MODEL_REGISTRY_* tokens configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Authentication not configured - set MODEL_REGISTRY_READ_TOKEN and/or "
                "MODEL_REGISTRY_ADMIN_TOKEN"
            ),
        )

    result = _authenticate_token(token, expected_tokens=configured_tokens)
    if result is None:
        logger.warning(
            "Token verification failed: token not recognized for configured scopes",
            extra={"token_length": len(token)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scopes, role = result

    return ServiceToken(scopes=scopes, auth_role=role)


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
    # Defense-in-depth: admin tokens already include model:read, but we check
    # model:admin explicitly so scope gates remain correct if tier assignments change.
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
    # Defense-in-depth: admin tokens already include model:write.
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
