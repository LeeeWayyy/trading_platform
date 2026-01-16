"""Market hours and session state utilities."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

# Exchange mappings (friendly name -> exchange_calendars code)
EXCHANGE_CALENDAR_MAP: dict[str, str] = {
    "NYSE": "XNYS",
    "XNYS": "XNYS",
    "NASDAQ": "XNAS",
    "XNAS": "XNAS",
    "CME": "CMES",  # CME Globex (if available)
    "CMES": "CMES",
}

PRE_MARKET_OPEN = time(4, 0)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
POST_MARKET_CLOSE = time(20, 0)


class SessionState(Enum):
    """Trading session state."""

    OPEN = "OPEN"
    PRE_MARKET = "PRE_MARKET"
    POST_MARKET = "POST_MARKET"
    CLOSED = "CLOSED"


class MarketHours:
    """Market hours helper for session state and transitions."""

    _calendar_cache: dict[str, Any] = {}

    @classmethod
    def _get_calendar(cls, exchange: str) -> Any | None:
        if exchange.upper() == "CRYPTO":
            return None

        code = EXCHANGE_CALENDAR_MAP.get(exchange.upper(), exchange.upper())
        if code in cls._calendar_cache:
            return cls._calendar_cache[code]

        try:
            from exchange_calendars import get_calendar  # type: ignore[import-not-found]

            cls._calendar_cache[code] = get_calendar(code)
        except Exception as exc:  # pragma: no cover - depends on external lib
            logger.warning(
                "market_calendar_unavailable",
                extra={"exchange": exchange, "error": type(exc).__name__},
            )
            cls._calendar_cache[code] = None
        return cls._calendar_cache[code]

    @classmethod
    def _get_exchange_tz(cls, calendar: Any | None) -> ZoneInfo:
        if calendar is None:
            return ZoneInfo("UTC")
        try:
            return ZoneInfo(str(calendar.tz))
        except Exception:
            return ZoneInfo("America/New_York")

    @classmethod
    def _is_trading_day(cls, calendar: Any, session_date: date) -> bool:
        try:
            return bool(calendar.is_session(pd.Timestamp(session_date)))
        except Exception:
            return False

    @classmethod
    def _get_session_schedule(cls, calendar: Any, session_date: date) -> tuple[datetime, datetime]:
        schedule = calendar.schedule.loc[pd.Timestamp(session_date)]
        market_open = schedule["market_open"].to_pydatetime()
        market_close = schedule["market_close"].to_pydatetime()
        return market_open, market_close

    @classmethod
    def _next_trading_day(cls, calendar: Any, start_date: date) -> date | None:
        end_date = start_date + timedelta(days=14)
        sessions = calendar.sessions_in_range(pd.Timestamp(start_date), pd.Timestamp(end_date))
        if sessions.empty:
            return None
        return cast(date, sessions[0].date())

    @classmethod
    def get_session_state(cls, exchange: str, now: datetime | None = None) -> SessionState:
        """Return session state for exchange (OPEN/PRE/POST/CLOSED)."""
        if exchange.upper() == "CRYPTO":
            return SessionState.OPEN

        calendar = cls._get_calendar(exchange)
        if calendar is None:
            return SessionState.CLOSED

        tz = cls._get_exchange_tz(calendar)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        session_date = current.date()

        if not cls._is_trading_day(calendar, session_date):
            return SessionState.CLOSED

        market_open, market_close = cls._get_session_schedule(calendar, session_date)
        pre_open = datetime.combine(session_date, PRE_MARKET_OPEN, tz)
        post_close = datetime.combine(session_date, POST_MARKET_CLOSE, tz)

        if current < pre_open:
            return SessionState.CLOSED
        if pre_open <= current < market_open:
            return SessionState.PRE_MARKET
        if market_open <= current < market_close:
            return SessionState.OPEN
        if market_close <= current < post_close:
            return SessionState.POST_MARKET
        return SessionState.CLOSED

    @classmethod
    def get_next_transition(cls, exchange: str, now: datetime | None = None) -> datetime | None:
        """Return next session transition datetime for exchange."""
        if exchange.upper() == "CRYPTO":
            return None

        calendar = cls._get_calendar(exchange)
        if calendar is None:
            return None

        tz = cls._get_exchange_tz(calendar)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        session_date = current.date()

        if cls._is_trading_day(calendar, session_date):
            market_open, market_close = cls._get_session_schedule(calendar, session_date)
            pre_open = datetime.combine(session_date, PRE_MARKET_OPEN, tz)
            post_close = datetime.combine(session_date, POST_MARKET_CLOSE, tz)

            if current < pre_open:
                return pre_open
            if pre_open <= current < market_open:
                return market_open
            if market_open <= current < market_close:
                return market_close
            if market_close <= current < post_close:
                return post_close

        next_day = cls._next_trading_day(calendar, session_date + timedelta(days=1))
        if next_day is None:
            return None
        return datetime.combine(next_day, PRE_MARKET_OPEN, tz)

    @classmethod
    def time_to_next_transition(
        cls, exchange: str, now: datetime | None = None
    ) -> timedelta | None:
        """Return time delta until next session transition."""
        next_transition = cls.get_next_transition(exchange, now=now)
        if next_transition is None:
            return None
        current = now if now is not None else datetime.now(next_transition.tzinfo)
        return max(timedelta(0), next_transition - current)

    @classmethod
    def is_trading_day(cls, exchange: str, day: date) -> bool:
        """Return True if given date is trading day for exchange."""
        if exchange.upper() == "CRYPTO":
            return True
        calendar = cls._get_calendar(exchange)
        if calendar is None:
            return False
        return cls._is_trading_day(calendar, day)


__all__ = [
    "MarketHours",
    "SessionState",
]
