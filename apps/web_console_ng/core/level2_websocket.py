"""Level 2 order book streaming service (Alpaca Pro)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any

from apps.web_console_ng.core.redis_ha import get_redis_store

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.data.alpaca.markets/v2/sip"


def l2_channel(user_id: str, symbol: str) -> str:
    """Build per-user L2 channel name."""
    return f"l2:{user_id}:{symbol}"


class Level2WebSocketService:
    """Manage Alpaca Level 2 WebSocket with Redis fanout.

    NOTE: In environments without Alpaca credentials, the service runs in mock
    mode and publishes synthetic orderbook snapshots.
    """

    _instance: Level2WebSocketService | None = None

    def __init__(
        self,
        *,
        max_symbols: int | None = None,
        time_fn: Callable[[], float] | None = None,
        mock_mode: bool | None = None,
    ) -> None:
        self._redis_store = get_redis_store()
        self._lock = asyncio.Lock()
        self._symbol_refcounts: dict[str, int] = {}
        self._symbol_users: dict[str, set[str]] = {}
        self._user_symbols: dict[str, set[str]] = {}
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._time_fn = time_fn or time.time

        self._max_symbols = max_symbols or int(os.getenv("ALPACA_L2_MAX_SYMBOLS", "30"))
        self._mock_mode = mock_mode if mock_mode is not None else self._should_use_mock()
        self._mock_rng = random.Random(42)
        self._mock_prices: dict[str, float] = {}

    @classmethod
    def get(cls) -> Level2WebSocketService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def entitlement_status() -> tuple[bool, str]:
        # Mock mode is always "entitled" for dev/testing
        if os.getenv("ALPACA_L2_USE_MOCK", "").lower() in {"1", "true", "yes"}:
            return True, "Mock mode enabled"
        enabled = os.getenv("ALPACA_L2_ENABLED", "false").lower() in {"1", "true", "yes"}
        if not enabled:
            return False, "Level 2 data not enabled"
        api_key = os.getenv("ALPACA_PRO_API_KEY", "").strip()
        api_secret = os.getenv("ALPACA_PRO_API_SECRET", "").strip()
        if not api_key or not api_secret:
            return False, "Alpaca Pro credentials missing"
        return True, "OK"

    def _should_use_mock(self) -> bool:
        if os.getenv("ALPACA_L2_USE_MOCK", "").lower() in {"1", "true", "yes"}:
            return True
        entitled, _ = self.entitlement_status()
        return not entitled

    async def subscribe(self, user_id: str, symbol: str) -> bool:
        symbol = symbol.upper()
        async with self._lock:
            if symbol not in self._symbol_refcounts and len(self._symbol_refcounts) >= self._max_symbols:
                logger.warning(
                    "level2_symbol_cap_reached",
                    extra={"symbol": symbol, "max": self._max_symbols},
                )
                return False

            self._symbol_refcounts[symbol] = self._symbol_refcounts.get(symbol, 0) + 1
            self._symbol_users.setdefault(symbol, set()).add(user_id)
            self._user_symbols.setdefault(user_id, set()).add(symbol)

            # Start streaming loop inside lock to prevent race conditions
            if not self._running:
                self._running = True
                self._task = asyncio.create_task(self._run())
        return True

    async def unsubscribe(self, user_id: str, symbol: str) -> None:
        symbol = symbol.upper()
        async with self._lock:
            if user_id in self._user_symbols:
                self._user_symbols[user_id].discard(symbol)
                if not self._user_symbols[user_id]:
                    del self._user_symbols[user_id]

            if symbol in self._symbol_users:
                self._symbol_users[symbol].discard(user_id)
                if not self._symbol_users[symbol]:
                    del self._symbol_users[symbol]

            if symbol in self._symbol_refcounts:
                self._symbol_refcounts[symbol] -= 1
                if self._symbol_refcounts[symbol] <= 0:
                    del self._symbol_refcounts[symbol]

        await self._stop_if_idle()

    async def publish_update(self, symbol: str, payload: dict[str, Any]) -> None:
        """Publish update to all subscribed user channels for symbol."""
        symbol = symbol.upper()
        users = list(self._symbol_users.get(symbol, set()))
        if not users:
            return

        message = json.dumps(payload)
        redis = await self._redis_store.get_master()
        for user_id in users:
            await redis.publish(l2_channel(user_id, symbol), message)

    async def _ensure_running(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def _stop_if_idle(self) -> None:
        async with self._lock:
            if self._symbol_refcounts:
                return
        if self._task and not self._task.done():
            self._running = False
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        if self._mock_mode:
            await self._mock_loop()
            return
        await self._connection_loop()

    async def _mock_loop(self) -> None:
        interval = 0.2
        while self._running:
            await asyncio.sleep(interval)
            symbols = list(self._symbol_refcounts.keys())
            if not symbols:
                continue
            now = self._time_fn()
            for symbol in symbols:
                payload = self._generate_mock_snapshot(symbol, now)
                await self.publish_update(symbol, payload)

    async def _connection_loop(self) -> None:
        """Placeholder for real Alpaca connection - falls back to mock mode.

        The real Alpaca WebSocket implementation will be added in a future PR.
        For now, log a warning and fall back to mock mode to avoid crash loops.
        """
        logger.warning(
            "level2_real_connection_not_implemented",
            extra={"action": "falling_back_to_mock_mode"},
        )
        # Fall back to mock mode instead of crash-looping
        self._mock_mode = True
        await self._mock_loop()

    def _generate_mock_snapshot(self, symbol: str, now: float) -> dict[str, Any]:
        base = self._mock_prices.get(symbol)
        if base is None:
            base = self._mock_rng.uniform(50, 300)
        base += self._mock_rng.uniform(-0.05, 0.05)
        self._mock_prices[symbol] = base

        bids = []
        asks = []
        for i in range(10):
            bid_price = round(base - 0.01 * (i + 1), 2)
            ask_price = round(base + 0.01 * (i + 1), 2)
            bid_size = int(self._mock_rng.uniform(50, 500))
            ask_size = int(self._mock_rng.uniform(50, 500))
            bids.append({"p": bid_price, "s": bid_size})
            asks.append({"p": ask_price, "s": ask_size})

        return {
            "T": "o",
            "S": symbol,
            "t": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
            "b": bids,
            "a": asks,
        }
