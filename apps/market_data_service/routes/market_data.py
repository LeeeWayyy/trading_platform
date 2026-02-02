"""Market data endpoints (ADV, quotes, etc.)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from apps.market_data_service.api.dependencies import build_market_data_authenticator
from apps.market_data_service.config import settings
from apps.market_data_service.schemas import ADVResponse
from libs.core.common.api_auth_dependency import APIAuthConfig, AuthContext, api_auth
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.redis_client import RedisClient
from libs.data.data_quality.types import ExchangeCalendarAdapter
from libs.data.market_data.exceptions import MarketDataError
from libs.data.market_data.provider import MarketDataProvider
from libs.platform.web_console_auth.permissions import Permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MarketData"])

# =============================================================================
# Auth + Rate Limiting
# =============================================================================

market_data_auth = api_auth(
    APIAuthConfig(
        action="market_data_adv",
        require_role=None,
        require_permission=Permission.VIEW_MARKET_DATA,
    ),
    authenticator_getter=build_market_data_authenticator,
)

market_data_rl = rate_limit(
    RateLimitConfig(
        action="market_data_adv",
        max_requests=1000,
        window_seconds=60,
        burst_buffer=0,
        fallback_mode="allow",
        global_limit=None,
    )
)

# =============================================================================
# Provider + Cache Helpers
# =============================================================================

_provider: MarketDataProvider | None = None
_cache: RedisClient | None = None

ADV_CACHE_TTL_SECONDS = 24 * 60 * 60
ADV_FRESHNESS_SECONDS = 60 * 60


def _get_provider() -> MarketDataProvider:
    global _provider
    if _provider is None:
        _provider = MarketDataProvider(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )
    return _provider


def _get_cache() -> RedisClient | None:
    global _cache
    if _cache is None:
        try:
            _cache = RedisClient(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password,
            )
        except Exception as exc:
            logger.warning("adv_cache_unavailable", extra={"error": type(exc).__name__})
            _cache = None
    return _cache


def _is_otc_symbol(symbol: str) -> bool:
    """Best-effort OTC detection for unsupported symbols."""
    normalized = symbol.upper()
    if "." in normalized:
        suffix = normalized.split(".")[-1]
        if suffix in {"F", "PK", "OB", "QX", "QB", "Q"}:
            return True
    if len(normalized) == 5 and normalized.endswith("D"):
        return True
    return False


def _trading_days_since(data_date: date, today: date) -> int:
    """Return trading days elapsed since data_date (inclusive -> exclusive)."""
    try:
        calendar = ExchangeCalendarAdapter("XNYS")
        trading_days = calendar.trading_days_between(data_date, today)
        return max(0, len(trading_days) - 1)
    except Exception as exc:
        logger.warning(
            "adv_stale_calendar_fallback",
            extra={"error": type(exc).__name__, "data_date": str(data_date)},
        )
        return max(0, (today - data_date).days)


def _build_response(
    *,
    symbol: str,
    adv: int,
    data_date: date,
    source: str,
    cached: bool,
    cached_at: datetime | None,
) -> ADVResponse:
    today = datetime.now(UTC).date()
    stale = _trading_days_since(data_date, today) > 5
    return ADVResponse(
        symbol=symbol,
        adv=adv,
        data_date=data_date,
        source=source,
        cached=cached,
        cached_at=cached_at,
        stale=stale,
    )


# =============================================================================
# ADV Endpoint
# =============================================================================


@router.get("/api/v1/market-data/{symbol}/adv", response_model=ADVResponse)
async def get_adv(
    symbol: str,
    _auth: AuthContext = Depends(market_data_auth),
    _rl: int = Depends(market_data_rl),
) -> ADVResponse:
    """Get 20-day Average Daily Volume (ADV) for a symbol."""
    symbol = symbol.upper()

    if _is_otc_symbol(symbol):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "symbol_not_supported", "detail": f"ADV not available for {symbol}"},
        )

    cache = _get_cache()
    cache_key = f"adv:{symbol}"
    cached_payload: dict[str, str] | None = None
    cached_at_dt: datetime | None = None

    if cache:
        cached_raw = await asyncio.to_thread(cache.get, cache_key)
        if cached_raw:
            try:
                cached_payload = json.loads(cached_raw)
                cached_at_raw = cached_payload.get("cached_at")
                if cached_at_raw:
                    cached_at_dt = datetime.fromisoformat(cached_at_raw)
            except Exception as exc:
                logger.warning(
                    "adv_cache_parse_failed",
                    extra={"symbol": symbol, "error": type(exc).__name__},
                )
                cached_payload = None
                cached_at_dt = None

    now = datetime.now(UTC)
    cache_is_fresh = cached_at_dt is not None and (now - cached_at_dt) <= timedelta(
        seconds=ADV_FRESHNESS_SECONDS
    )

    if cached_payload and cache_is_fresh:
        return _build_response(
            symbol=symbol,
            adv=int(cached_payload["adv"]),
            data_date=date.fromisoformat(cached_payload["data_date"]),
            source=str(cached_payload["source"]),
            cached=True,
            cached_at=cached_at_dt,
        )

    provider = _get_provider()
    try:
        adv_data = await provider.get_adv(symbol)
    except MarketDataError as exc:
        if cached_payload:
            return _build_response(
                symbol=symbol,
                adv=int(cached_payload["adv"]),
                data_date=date.fromisoformat(cached_payload["data_date"]),
                source=str(cached_payload["source"]),
                cached=True,
                cached_at=cached_at_dt,
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADV provider unavailable and no cached data",
        ) from exc

    if adv_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "symbol_not_found", "detail": f"ADV data not available for {symbol}"},
        )

    cached_at = datetime.now(UTC)
    if cache:
        cache_value = json.dumps(
            {
                "adv": adv_data.adv,
                "data_date": adv_data.data_date.isoformat(),
                "source": adv_data.source,
                "cached_at": cached_at.isoformat(),
            }
        )
        await asyncio.to_thread(cache.set, cache_key, cache_value, ex=ADV_CACHE_TTL_SECONDS)

    return _build_response(
        symbol=symbol,
        adv=adv_data.adv,
        data_date=adv_data.data_date,
        source=adv_data.source,
        cached=False,
        cached_at=cached_at,
    )
