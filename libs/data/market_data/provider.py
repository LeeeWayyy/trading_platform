"""Market data provider abstraction (historical data helpers)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    ALPACA_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    ALPACA_AVAILABLE = False
    StockHistoricalDataClient = None  # type: ignore[assignment,misc]
    StockBarsRequest = None  # type: ignore[assignment,misc]
    TimeFrame = None  # type: ignore[assignment,misc]

from libs.data.market_data.exceptions import MarketDataError
from libs.data.market_data.types import ADVData

logger = logging.getLogger(__name__)


class MarketDataProvider:
    """Market data provider wrapper for ADV calculations."""

    def __init__(self, api_key: str, secret_key: str, data_feed: str | None = None):
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-py package is required for MarketDataProvider")
        self._client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        self._data_feed = data_feed

    async def get_adv(self, symbol: str) -> ADVData | None:
        """Get 20-day Average Daily Volume from the data provider."""
        return await asyncio.to_thread(self._get_adv_sync, symbol)

    def _get_adv_sync(self, symbol: str) -> ADVData | None:
        symbol = symbol.upper().strip()
        if not symbol:
            return None

        try:
            request_kwargs: dict[str, Any] = {
                "symbol_or_symbols": symbol,
                "timeframe": TimeFrame.Day,
                "limit": 20,
            }
            if self._data_feed:
                request_kwargs["feed"] = self._data_feed
            request = StockBarsRequest(**request_kwargs)
            bars = self._client.get_stock_bars(request)
        except Exception as exc:  # pragma: no cover - provider errors
            logger.warning(
                "adv_provider_error",
                extra={"symbol": symbol, "error": type(exc).__name__},
            )
            raise MarketDataError(str(exc)) from exc

        bar_list: list[Any] = []
        if hasattr(bars, "data"):
            bar_list = list(getattr(bars, "data", {}).get(symbol, []))
        elif isinstance(bars, dict):
            bar_list = list(bars.get(symbol, []))

        if not bar_list:
            return None

        volumes: list[int] = []
        last_timestamp: datetime | None = None
        for bar in bar_list:
            volume = None
            if isinstance(bar, dict):
                volume = bar.get("volume") or bar.get("v")
                ts = bar.get("timestamp") or bar.get("t")
            else:
                volume = getattr(bar, "v", None) or getattr(bar, "volume", None)
                ts = getattr(bar, "t", None) or getattr(bar, "timestamp", None)

            if volume is not None:
                try:
                    volumes.append(int(volume))
                except (TypeError, ValueError):
                    continue

            if isinstance(ts, datetime):
                last_timestamp = ts

        if not volumes:
            return None

        adv_value = int(sum(volumes) / len(volumes))
        data_date = (last_timestamp or datetime.now(UTC)).date()

        return ADVData(
            symbol=symbol,
            adv=adv_value,
            data_date=data_date,
            source="alpaca",
        )
