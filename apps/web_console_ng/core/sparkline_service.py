"""Sparkline data service for position P&L history."""

from __future__ import annotations

import logging
import time
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class SparklineDataService:
    """Maintain per-position P&L history for inline sparklines."""

    def __init__(
        self,
        redis: Redis,
        *,
        max_points: int = 60,
        sample_interval_seconds: int = 60,
        ttl_seconds: int = 7200,
        time_fn: Any = None,
    ) -> None:
        self._redis = redis
        self._max_points = max_points
        self._sample_interval = sample_interval_seconds
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn or time.time
        self._last_sampled: dict[tuple[str, str], int] = {}

    def _key(self, user_id: str, symbol: str) -> str:
        return f"pnl_history:{user_id}:{symbol}"

    def _current_bucket(self) -> int:
        now = int(self._time_fn())
        return now - (now % self._sample_interval)

    async def record_positions(self, user_id: str, positions: list[dict[str, Any]]) -> None:
        """Record P&L snapshots for all positions (rate-limited per symbol)."""
        if not user_id:
            return
        for position in positions:
            symbol = str(position.get("symbol", "")).strip()
            if not symbol:
                continue
            pnl_raw = position.get("unrealized_pl")
            try:
                pnl_value = float(pnl_raw)
            except (TypeError, ValueError):
                continue
            await self.record_point(user_id, symbol, pnl_value)

    async def record_point(self, user_id: str, symbol: str, pnl_value: float) -> None:
        """Record a single P&L data point if rate limit allows."""
        bucket = self._current_bucket()
        sample_key = (user_id, symbol)
        if self._last_sampled.get(sample_key) == bucket:
            return
        self._last_sampled[sample_key] = bucket

        key = self._key(user_id, symbol)
        member = f"{bucket}:{pnl_value}"
        try:
            await self._redis.zadd(key, {member: bucket})
            size = await self._redis.zcard(key)
            if size > self._max_points:
                await self._redis.zremrangebyrank(key, 0, -(self._max_points + 1))
            await self._redis.expire(key, self._ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sparkline_record_failed",
                extra={"user_id": user_id, "symbol": symbol, "error": type(exc).__name__},
            )

    async def get_sparkline_data(self, user_id: str, symbol: str) -> list[float]:
        """Fetch sparkline data for a symbol (chronological)."""
        key = self._key(user_id, symbol)
        try:
            members = await self._redis.zrange(key, 0, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sparkline_fetch_failed",
                extra={"user_id": user_id, "symbol": symbol, "error": type(exc).__name__},
            )
            return []
        return self._parse_members(members)

    async def get_sparkline_map(
        self, user_id: str, symbols: list[str]
    ) -> dict[str, list[float]]:
        """Fetch sparkline data for multiple symbols."""
        results: dict[str, list[float]] = {}
        for symbol in symbols:
            if not symbol:
                continue
            results[symbol] = await self.get_sparkline_data(user_id, symbol)
        return results

    @staticmethod
    def _parse_members(members: list[Any]) -> list[float]:
        data: list[float] = []
        for member in members:
            if isinstance(member, bytes):
                member_str = member.decode("utf-8", errors="ignore")
            else:
                member_str = str(member)
            if ":" in member_str:
                _, value_str = member_str.split(":", 1)
            else:
                value_str = member_str
            try:
                data.append(float(value_str))
            except ValueError:
                continue
        return data


__all__ = ["SparklineDataService"]
