"""Sparkline data service for position P&L history."""

from __future__ import annotations

import logging
import math
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
        max_cache_entries: int = 10000,
    ) -> None:
        self._redis = redis
        self._max_points = max_points
        self._sample_interval = sample_interval_seconds
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn or time.time
        self._last_sampled: dict[tuple[str, str], int] = {}
        self._last_prune_bucket: int = 0
        # Prune every 10 sample intervals to avoid unbounded growth
        self._prune_interval_buckets = 10
        # Hard limit on cache size to prevent unbounded memory growth
        self._max_cache_entries = max_cache_entries

    def _key(self, user_id: str, symbol: str) -> str:
        return f"pnl_history:{user_id}:{symbol}"

    def _current_bucket(self) -> int:
        now = int(self._time_fn())
        return now - (now % self._sample_interval)

    def _maybe_prune_rate_limit_cache(self, current_bucket: int) -> None:
        """Prune stale entries from rate-limit cache to prevent unbounded growth."""
        # Force prune if cache exceeds hard limit
        force_prune = len(self._last_sampled) >= self._max_cache_entries

        prune_threshold = self._prune_interval_buckets * self._sample_interval
        if not force_prune and current_bucket - self._last_prune_bucket < prune_threshold:
            return

        self._last_prune_bucket = current_bucket
        # Remove entries older than TTL
        cutoff = current_bucket - self._ttl_seconds
        stale_keys = [k for k, v in self._last_sampled.items() if v < cutoff]
        for key in stale_keys:
            del self._last_sampled[key]

        # If still over limit after TTL prune, remove oldest entries
        if len(self._last_sampled) >= self._max_cache_entries:
            # Sort by bucket (oldest first) and remove excess
            sorted_entries = sorted(self._last_sampled.items(), key=lambda x: x[1])
            excess = len(self._last_sampled) - (self._max_cache_entries // 2)
            for key, _ in sorted_entries[:excess]:
                del self._last_sampled[key]

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
        # Reject NaN/inf to prevent invalid ZSET entries and SVG rendering issues
        if not math.isfinite(pnl_value):
            logger.debug(
                "sparkline_invalid_pnl_skipped",
                extra={"user_id": user_id, "symbol": symbol, "value": str(pnl_value)},
            )
            return

        bucket = self._current_bucket()
        sample_key = (user_id, symbol)
        if self._last_sampled.get(sample_key) == bucket:
            return

        # Prune BEFORE inserting to prevent brief spikes above max_cache_entries
        self._maybe_prune_rate_limit_cache(bucket)
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
        """Fetch sparkline data for multiple symbols in parallel."""
        import asyncio

        valid_symbols = [s for s in symbols if s]
        if not valid_symbols:
            return {}

        # Fetch all symbols in parallel to avoid N sequential Redis round-trips
        tasks = [self.get_sparkline_data(user_id, symbol) for symbol in valid_symbols]
        data_list = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, list[float]] = {}
        for symbol, data in zip(valid_symbols, data_list, strict=False):
            if isinstance(data, Exception):
                logger.warning(
                    "sparkline_parallel_fetch_failed",
                    extra={"user_id": user_id, "symbol": symbol, "error": type(data).__name__},
                )
                results[symbol] = []
            else:
                results[symbol] = data
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
                val = float(value_str)
                # Filter out NaN/inf that might be stored in Redis
                if math.isfinite(val):
                    data.append(val)
            except ValueError:
                continue
        return data


__all__ = ["SparklineDataService"]
