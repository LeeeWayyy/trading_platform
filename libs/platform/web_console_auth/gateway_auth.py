"""Service-to-service authentication for the Execution Gateway."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis_async

from libs.platform.web_console_auth.db import acquire_connection
from libs.platform.web_console_auth.exceptions import (
    InvalidTokenError,
    MissingJtiError,
    SubjectMismatchError,
    TokenReplayedError,
    TokenRevokedError,
)
from libs.platform.web_console_auth.jwt_manager import JWTManager
from libs.platform.web_console_auth.permissions import Role

# P6T19: SessionExpiredError, validate_session_version removed (single-admin model)

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
        redis_client: redis_async.Redis,
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

        claims = await asyncio.to_thread(self._decode_and_validate, token)

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

        # P6T19: Single-admin model — always admin, all strategies
        role = Role.ADMIN
        strategies = await self.get_user_strategies()

        return AuthenticatedUser(
            user_id=x_user_id,
            role=role,
            strategies=strategies,
            session_version=x_session_version,
            request_id=x_request_id,
        )

    def _decode_and_validate(self, token: str) -> dict[str, Any]:
        """Decode JWT and map errors to domain exceptions."""
        return self.jwt_manager.validate_token(token, expected_type="service")

    async def _check_jti_one_time_use(self, jti: str, exp: int) -> None:
        """Ensure JTI is only used once by leveraging atomic Redis SET NX EX."""
        key = f"{self.JTI_SEEN_PREFIX}{jti}"
        now = int(time.time())
        ttl = max(int(exp) - now, 1)
        was_set = await self.redis.set(key, "1", nx=True, ex=ttl)
        if not was_set:
            raise TokenReplayedError(f"Token already used: {jti}")

    async def get_user_strategies(self) -> list[str]:
        """P6T19: Fetch all strategies from strategies table (no per-user filtering)."""
        query = "SELECT strategy_id FROM strategies ORDER BY strategy_id"
        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
        strategies: list[str] = []
        for row in rows or []:
            value = row["strategy_id"] if isinstance(row, dict) else row[0]
            strategies.append(str(value))
        return strategies


__all__ = ["GatewayAuthenticator", "AuthenticatedUser"]
