"""User state persistence for NiceGUI reconnects."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, cast

import redis.asyncio as redis

from apps.web_console_ng import config

logger = logging.getLogger(__name__)


class UserStateManager:
    """Persist and restore critical UI state in Redis."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        redis_client: redis.Redis | None = None,
    ) -> None:
        self.redis = redis_client or _redis_from_url(redis_url, decode_responses=True)
        self.ttl_seconds = ttl_seconds
        self.state_prefix = "ng_ui_state:"

    async def save_state(self, user_id: str, key: str, value: Any) -> None:
        payload = json.dumps(value)
        await self.redis.setex(self._make_key(user_id, key), self.ttl_seconds, payload)

    async def load_state(self, user_id: str, key: str) -> Any | None:
        raw = await self.redis.get(self._make_key(user_id, key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("state_manager_json_decode_failed", extra={"user_id": user_id, "key": key})
            return None

    async def close(self) -> None:
        await self.redis.close()

    def _make_key(self, user_id: str, key: str) -> str:
        return f"{self.state_prefix}{user_id}:{key}"


def get_state_manager() -> UserStateManager:
    ttl_seconds = config.SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600
    return UserStateManager(redis_url=config.REDIS_URL, ttl_seconds=ttl_seconds)


def _redis_from_url(url: str, *, decode_responses: bool) -> redis.Redis:
    from_url = cast(Callable[..., redis.Redis], redis.Redis.from_url)
    return from_url(url, decode_responses=decode_responses)


__all__ = ["UserStateManager", "get_state_manager"]
