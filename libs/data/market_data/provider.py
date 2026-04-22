"""Market data provider abstraction (historical data helpers)."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    ALPACA_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    ALPACA_AVAILABLE = False
    StockHistoricalDataClient = None  # type: ignore[assignment,misc]
    StockBarsRequest = None  # type: ignore[assignment,misc]
    TimeFrame = None  # type: ignore[assignment,misc]
    TimeFrameUnit = None  # type: ignore[assignment,misc]

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

    async def get_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "5Min",
        limit: int = 240,
    ) -> list[dict[str, Any]]:
        """Get historical OHLCV bars for a symbol."""
        return await asyncio.to_thread(
            self._get_bars_sync,
            symbol,
            timeframe,
            limit,
        )

    def _resolve_timeframe(self, timeframe: str) -> Any:
        """Resolve supported timeframe strings to Alpaca TimeFrame."""
        normalized = timeframe.strip().lower()
        mapping: dict[str, Any] = {
            "1min": TimeFrame(1, TimeFrameUnit.Minute),
            "5min": TimeFrame(5, TimeFrameUnit.Minute),
            "15min": TimeFrame(15, TimeFrameUnit.Minute),
            "1hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1day": TimeFrame.Day,
        }
        resolved = mapping.get(normalized)
        if resolved is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        return resolved

    @staticmethod
    def _compute_bars_window(timeframe: str, limit: int) -> tuple[datetime, datetime]:
        """Compute a conservative lookback window for intraday/daily bar requests.

        Alpaca can return empty payloads for recent intraday requests without an explicit
        start/end window. We over-fetch a calendar window and rely on `limit` to trim.
        """
        normalized = timeframe.strip().lower()
        # Approximate bars per trading day by timeframe.
        bars_per_trading_day: dict[str, int] = {
            "1min": 390,
            "5min": 78,
            "15min": 26,
            "1hour": 7,
            "1day": 1,
        }
        per_day = bars_per_trading_day.get(normalized, 78)
        required_trading_days = max(2, math.ceil(limit / per_day))
        # Buffer for weekends/holidays, early closes, and feed-specific gaps.
        buffer_multiplier: dict[str, int] = {
            "1min": 4,
            "5min": 4,
            "15min": 4,
            "1hour": 5,
            "1day": 8,
        }
        calendar_days = max(5, required_trading_days * buffer_multiplier.get(normalized, 4))
        if normalized != "1day":
            calendar_days = max(calendar_days, 14)

        end = datetime.now(UTC)
        start = end - timedelta(days=calendar_days)
        return start, end

    def _normalize_bar_timestamp(self, raw_ts: Any) -> datetime | None:
        """Normalize provider bar timestamp values to UTC-aware datetimes."""
        if isinstance(raw_ts, datetime):
            if raw_ts.tzinfo is None:
                return raw_ts.replace(tzinfo=UTC)
            return raw_ts.astimezone(UTC)
        if isinstance(raw_ts, str):
            try:
                parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        return None

    @staticmethod
    def _extract_bar_list(bars: Any, symbol: str) -> list[Any]:
        """Extract symbol-scoped bars from Alpaca response payload variants."""
        if hasattr(bars, "data") and isinstance(bars.data, dict):
            return list(bars.data.get(symbol, []))
        if isinstance(bars, dict):
            return list(bars.get(symbol, []))
        return []

    def _normalize_bar(self, bar: Any) -> dict[str, Any] | None:
        """Normalize one provider bar object into the API response shape."""
        if isinstance(bar, dict):
            ts_raw = bar.get("timestamp") or bar.get("t")
            open_raw = bar.get("open") or bar.get("o")
            high_raw = bar.get("high") or bar.get("h")
            low_raw = bar.get("low") or bar.get("l")
            close_raw = bar.get("close") or bar.get("c")
            volume_raw = bar.get("volume") or bar.get("v")
        else:
            ts_raw = getattr(bar, "timestamp", None) or getattr(bar, "t", None)
            open_raw = getattr(bar, "open", None) or getattr(bar, "o", None)
            high_raw = getattr(bar, "high", None) or getattr(bar, "h", None)
            low_raw = getattr(bar, "low", None) or getattr(bar, "l", None)
            close_raw = getattr(bar, "close", None) or getattr(bar, "c", None)
            volume_raw = getattr(bar, "volume", None) or getattr(bar, "v", None)

        ts = self._normalize_bar_timestamp(ts_raw)
        if ts is None:
            return None
        if open_raw is None or high_raw is None or low_raw is None or close_raw is None:
            return None

        try:
            open_px = float(open_raw)
            high_px = float(high_raw)
            low_px = float(low_raw)
            close_px = float(close_raw)
            if (
                not math.isfinite(open_px)
                or not math.isfinite(high_px)
                or not math.isfinite(low_px)
                or not math.isfinite(close_px)
                or min(open_px, high_px, low_px, close_px) <= 0
            ):
                return None
            return {
                "timestamp": ts.isoformat(),
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": int(volume_raw) if volume_raw is not None else 0,
            }
        except (TypeError, ValueError):
            return None

    def _get_bars_sync(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        symbol = symbol.upper().strip()
        if not symbol:
            return []
        clamped_limit = max(1, min(limit, 500))
        start, end = self._compute_bars_window(timeframe, clamped_limit)
        resolved_timeframe = self._resolve_timeframe(timeframe)

        try:
            request_kwargs: dict[str, Any] = {
                "symbol_or_symbols": symbol,
                "timeframe": resolved_timeframe,
                "limit": clamped_limit,
                "start": start,
                "end": end,
                # Request newest bars first so `limit` is applied to the most recent window.
                "sort": "desc",
            }
            if self._data_feed:
                request_kwargs["feed"] = self._data_feed
            request = StockBarsRequest(**request_kwargs)
            bars = self._client.get_stock_bars(request)
        except Exception as exc:  # pragma: no cover - provider errors
            logger.warning(
                "bars_provider_error",
                extra={
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "limit": clamped_limit,
                    "error": type(exc).__name__,
                },
            )
            raise MarketDataError(str(exc)) from exc

        bar_list = self._extract_bar_list(bars, symbol)

        normalized: list[dict[str, Any]] = []
        for bar in bar_list:
            normalized_bar = self._normalize_bar(bar)
            if normalized_bar is not None:
                normalized.append(normalized_bar)

        normalized.sort(key=lambda bar: str(bar["timestamp"]))
        return normalized

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

        bar_list = self._extract_bar_list(bars, symbol)

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
