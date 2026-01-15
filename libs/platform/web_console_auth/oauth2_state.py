"""OAuth2 temporary state storage in Redis.

CRITICAL SECURITY: State, nonce, and PKCE verifier are stored in Redis
with 10-minute TTL and SINGLE-USE enforcement to prevent CSRF and replay attacks.

Redis Schema:
  Key: oauth_state:{state}
  Value: JSON blob with code_verifier, nonce, code_challenge, redirect_uri, created_at
  TTL: 600 seconds (10 minutes, matches authorization code expiration)

Single-Use Enforcement:
  - State retrieved once via get_and_delete_state()
  - Subsequent callback attempts with same state are rejected
  - Expired states (>10min) automatically purged by Redis
"""

import logging
from datetime import datetime

import redis.asyncio
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OAuth2State(BaseModel):
    """OAuth2 temporary state stored in Redis during authorization flow."""

    state: str
    code_verifier: str
    nonce: str
    code_challenge: str
    redirect_uri: str
    created_at: datetime


class OAuth2StateStore:
    """Manages temporary OAuth2 state in Redis with single-use enforcement."""

    def __init__(self, redis_client: redis.asyncio.Redis, ttl_seconds: int = 600):
        """Initialize OAuth2 state store.

        Args:
            redis_client: Redis async client (DB 1, same as session store)
            ttl_seconds: State TTL in seconds (default: 600 = 10 minutes)
        """
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds

    async def store_state(self, oauth_state: OAuth2State) -> None:
        """Store OAuth2 state in Redis with 10-minute TTL.

        Args:
            oauth_state: OAuth2 state to store
        """
        key = f"oauth_state:{oauth_state.state}"
        value = oauth_state.model_dump_json()

        await self.redis.setex(key, self.ttl_seconds, value)

        logger.info(
            "OAuth2 state stored",
            extra={
                "state": oauth_state.state[:8] + "...",
                "ttl_seconds": self.ttl_seconds,
            },
        )

    async def get_and_delete_state(self, state: str) -> OAuth2State | None:
        """Retrieve and DELETE OAuth2 state (single-use enforcement).

        CRITICAL: This method enforces single-use by deleting the state
        after retrieval. Replay attacks with the same state will fail.

        Args:
            state: State parameter from callback

        Returns:
            OAuth2State if found and valid, None otherwise
        """
        key = f"oauth_state:{state}"

        # Atomic get-and-delete using Redis pipeline
        async with self.redis.pipeline() as pipe:
            await pipe.get(key)
            await pipe.delete(key)
            results = await pipe.execute()

        value = results[0]

        if not value:
            logger.warning(
                "OAuth2 state not found or already used",
                extra={"state": state[:8] + "..."},
            )
            return None

        oauth_state = OAuth2State.model_validate_json(value)

        logger.info(
            "OAuth2 state retrieved and deleted (single-use)",
            extra={"state": state[:8] + "..."},
        )

        return oauth_state
