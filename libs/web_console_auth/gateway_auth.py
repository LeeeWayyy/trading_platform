"""Service-to-service authentication for the Execution Gateway."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, cast

import jwt
import redis.asyncio as redis

from libs.web_console_auth.db import acquire_connection
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
from libs.web_console_auth.jwt_manager import JWTManager
from libs.web_console_auth.permissions import Role
from libs.web_console_auth.session_validation import validate_session_version

logger = logging.getLogger(__name__)


@dataclass
class AuthenticatedUser:
    """Authenticated user context returned after successful validation."""

    user_id: str
    role: Role | None
    strategies: list[str]
    session_version: int
    request_id: str


class GatewayAuthenticator:
    """Validates internal Web Console → Execution Gateway tokens."""

    JTI_SEEN_PREFIX = "jti_seen:"

    def __init__(
        self,
        jwt_manager: JWTManager,
        db_pool: Any,
        redis_client: redis.Redis,
    ) -> None:
        self.jwt_manager = jwt_manager
        self.db_pool = db_pool
        self.redis = redis_client

    async def authenticate(
        self,
        token: str,
        x_user_id: str,
        x_request_id: str,
        x_session_version: int,
    ) -> AuthenticatedUser:
        """Validate service token and return authenticated user context."""

        claims = self._decode_and_validate(token)

        # Enforce required claims
        jti = claims.get("jti")
        if not jti:
            raise MissingJtiError("Token missing jti claim")

        exp = claims.get("exp")
        if exp is None:
            raise InvalidTokenError("Token missing exp claim")

        # One-time-use JTI enforcement (replay protection)
        await self._check_jti_one_time_use(str(jti), int(exp))

        # Revocation check (independent from one-time-use)
        if self.jwt_manager.is_token_revoked(str(jti)):
            raise TokenRevokedError(f"Token has been revoked: {jti}")

        # Bind sub to header user
        if claims.get("sub") != x_user_id:
            raise SubjectMismatchError("Token subject does not match X-User-ID")

        role = await self.get_user_role(x_user_id)
        strategies = await self.get_user_strategies(x_user_id)

        is_valid_session = await validate_session_version(x_user_id, x_session_version, self.db_pool)
        if not is_valid_session:
            raise SessionExpiredError("Session invalidated")

        return AuthenticatedUser(
            user_id=x_user_id,
            role=role,
            strategies=strategies,
            session_version=x_session_version,
            request_id=x_request_id,
        )

    def _decode_and_validate(self, token: str) -> dict[str, Any]:
        """Decode JWT and map errors to domain exceptions."""
        config = self.jwt_manager.config
        try:
            claims = cast(
                dict[str, Any],
                jwt.decode(
                    token,
                    self.jwt_manager.public_key,
                    algorithms=[config.jwt_algorithm],
                    issuer=config.jwt_issuer,
                    audience=config.jwt_audience,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
                leeway=config.clock_skew_seconds,
                ),
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("Token has expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise ImmatureSignatureError("Token not yet valid") from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidIssuerError("Token issuer not trusted") from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidAudienceError("Token not intended for this service") from exc
        except jwt.InvalidSignatureError as exc:
            raise InvalidSignatureError("Token signature verification failed") from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(str(exc)) from exc

        if claims.get("type") != "service":
            raise InvalidTokenError(f"Expected service token, got {claims.get('type')}")

        return claims

    async def _check_jti_one_time_use(self, jti: str, exp: int) -> None:
        """Ensure JTI is only used once by leveraging atomic Redis SET NX EX."""
        key = f"{self.JTI_SEEN_PREFIX}{jti}"
        now = int(time.time())
        ttl = max(int(exp) - now, 1)
        was_set = await self.redis.set(key, "1", nx=True, ex=ttl)
        if not was_set:
            raise TokenReplayedError(f"Token already used: {jti}")

    async def get_user_role(self, user_id: str) -> Role | None:
        """Fetch user role from user_roles table."""
        query = "SELECT role FROM user_roles WHERE user_id = %s"
        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(query, (user_id,))
            row = await cursor.fetchone()
        if not row:
            return None
        role_value = row["role"] if isinstance(row, dict) else row[0]
        try:
            return Role(role_value)
        except ValueError:
            logger.warning("unknown_role_value", extra={"role": role_value})
            return None

    async def get_user_strategies(self, user_id: str) -> list[str]:
        """Fetch authorized strategies from user_strategy_access table."""
        query = "SELECT strategy_id FROM user_strategy_access WHERE user_id = %s ORDER BY strategy_id"
        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(query, (user_id,))
            rows = await cursor.fetchall()
        strategies: list[str] = []
        for row in rows or []:
            value = row["strategy_id"] if isinstance(row, dict) else row[0]
            strategies.append(str(value))
        return strategies


__all__ = ["GatewayAuthenticator", "AuthenticatedUser"]
