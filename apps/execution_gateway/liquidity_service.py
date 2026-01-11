"""
Liquidity service for fetching Average Daily Volume (ADV).

Fetches 20-day daily bars from Alpaca Market Data API and computes ADV.
Implements in-memory TTL caching to avoid repeated API calls. On failures,
can fall back to stale cached values subject to an optional max-staleness cap.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


class LiquidityService:
    """
    Fetches ADV (Average Daily Volume) using Alpaca Market Data API.

    Notes:
        - Uses 20-day lookback of daily bars
        - Caches results in-memory with TTL (default 24h)
        - On API failure, may return stale cached ADV if allowed by max_stale_seconds
        - Returns None when no usable cache is available (caller decides how to handle)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://data.alpaca.markets",
        data_feed: str | None = None,
        lookback_days: int = 20,
        ttl_seconds: int = 24 * 60 * 60,
        max_stale_seconds: int | None = None,
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._data_feed = data_feed.strip() if data_feed else None
        self._lookback_days = lookback_days
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_stale = (
            timedelta(seconds=max_stale_seconds) if max_stale_seconds is not None else None
        )
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._cache: dict[str, tuple[int, datetime]] = {}
        self._lock = threading.Lock()

    def get_adv(self, symbol: str) -> int | None:
        """
        Return 20-day ADV for the given symbol.

        Returns stale cached value if allowed; otherwise None on failure.
        """
        symbol = symbol.upper()
        now = datetime.now(UTC)

        cached_adv: int | None = None
        cached_at: datetime | None = None

        with self._lock:
            cached = self._cache.get(symbol)
            if cached:
                cached_adv, cached_at = cached
                if now - cached_at <= self._ttl:
                    return cached_adv

        def _maybe_use_stale_cache(reason: str) -> int | None:
            if cached_adv is None or cached_at is None:
                return None

            stale_age_seconds = (now - cached_at).total_seconds()
            if self._max_stale is not None and stale_age_seconds > self._max_stale.total_seconds():
                logger.warning(
                    "ADV lookup failed and cached value too stale; skipping",
                    extra={
                        "symbol": symbol,
                        "stale_age_seconds": stale_age_seconds,
                        "max_stale_seconds": self._max_stale.total_seconds(),
                        "using_stale_cache": False,
                        "reason": reason,
                    },
                )
                return None

            logger.warning(
                "ADV lookup failed; using stale cached value",
                extra={
                    "symbol": symbol,
                    "stale_age_seconds": stale_age_seconds,
                    "using_stale_cache": True,
                    "reason": reason,
                },
            )
            return cached_adv

        if not self._api_key or not self._api_secret:
            logger.warning(
                "Liquidity check enabled but Alpaca credentials missing; skipping ADV lookup",
                extra={"symbol": symbol},
            )
            return _maybe_use_stale_cache("missing_credentials")

        url = f"{self._base_url}/v2/stocks/{symbol}/bars"
        params: dict[str, str | int] = {"timeframe": "1Day", "limit": self._lookback_days}
        if self._data_feed:
            params["feed"] = self._data_feed
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
        }

        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            logger.warning(
                "ADV lookup failed due to request error",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return _maybe_use_stale_cache("request_error")

        if response.status_code != 200:
            logger.warning(
                "ADV lookup failed with non-200 status",
                extra={
                    "symbol": symbol,
                    "status_code": response.status_code,
                    "response_body": response.text[:200],
                },
            )
            return _maybe_use_stale_cache("bad_status")

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning(
                "ADV lookup failed to parse JSON response",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return _maybe_use_stale_cache("json_parse_error")

        bars = payload.get("bars") or []
        if not isinstance(bars, list) or not bars:
            logger.warning(
                "ADV lookup returned no bars",
                extra={
                    "symbol": symbol,
                    "bars_count": len(bars) if isinstance(bars, list) else 0,
                    "status_code": response.status_code,
                    "feed": self._data_feed or "default",
                    "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
                    "response_body_preview": response.text[:200],
                },
            )
            logger.warning(
                "ADV no bars details: symbol=%s feed=%s status=%s payload_keys=%s body_preview=%s",
                symbol,
                self._data_feed or "default",
                response.status_code,
                sorted(payload.keys()) if isinstance(payload, dict) else [],
                response.text[:200],
            )
            return _maybe_use_stale_cache("no_bars")

        volumes: list[int] = []
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            volume = bar.get("v", bar.get("volume"))
            try:
                if volume is not None:
                    volumes.append(int(volume))
            except (TypeError, ValueError):
                continue

        if not volumes:
            logger.warning(
                "ADV lookup returned bars without volume data",
                extra={"symbol": symbol},
            )
            return _maybe_use_stale_cache("no_volume")

        adv = int(sum(volumes) / len(volumes))

        with self._lock:
            self._cache[symbol] = (adv, now)

        return adv
