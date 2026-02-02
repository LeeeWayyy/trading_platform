"""Unit tests for MarketHours utilities."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from libs.common import market_hours as market_hours_module
from libs.common.market_hours import MarketHours, SessionState


class FakeCalendar:
    """Minimal exchange calendar stub for tests."""

    def __init__(self, session_date: date) -> None:
        self.tz = "America/New_York"
        open_dt = datetime.combine(session_date, time(9, 30), ZoneInfo(self.tz))
        close_dt = datetime.combine(session_date, time(16, 0), ZoneInfo(self.tz))
        self.schedule = pd.DataFrame(
            {"market_open": [open_dt], "market_close": [close_dt]},
            index=[pd.Timestamp(session_date)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        return ts.date() == self.schedule.index[0].date()

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([self.schedule.index[0]])


@pytest.fixture()
def fake_calendar(monkeypatch: pytest.MonkeyPatch) -> FakeCalendar:
    session_date = date(2026, 1, 16)
    cal = FakeCalendar(session_date)
    monkeypatch.setattr(
        market_hours_module.MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal)
    )
    return cal


def test_crypto_is_always_open() -> None:
    assert MarketHours.get_session_state("CRYPTO") == SessionState.OPEN
    assert MarketHours.get_next_transition("CRYPTO") is None
    assert MarketHours.is_trading_day("CRYPTO", date.today()) is True


def test_pre_market_state(fake_calendar: FakeCalendar) -> None:
    now = datetime.combine(date(2026, 1, 16), time(8, 0), ZoneInfo("America/New_York"))
    assert MarketHours.get_session_state("NYSE", now=now) == SessionState.PRE_MARKET


def test_open_state(fake_calendar: FakeCalendar) -> None:
    now = datetime.combine(date(2026, 1, 16), time(10, 0), ZoneInfo("America/New_York"))
    assert MarketHours.get_session_state("NYSE", now=now) == SessionState.OPEN


def test_post_market_state(fake_calendar: FakeCalendar) -> None:
    now = datetime.combine(date(2026, 1, 16), time(17, 0), ZoneInfo("America/New_York"))
    assert MarketHours.get_session_state("NYSE", now=now) == SessionState.POST_MARKET


def test_closed_state_before_pre_market(fake_calendar: FakeCalendar) -> None:
    now = datetime.combine(date(2026, 1, 16), time(2, 0), ZoneInfo("America/New_York"))
    assert MarketHours.get_session_state("NYSE", now=now) == SessionState.CLOSED


def test_next_transition_open(fake_calendar: FakeCalendar) -> None:
    now = datetime.combine(date(2026, 1, 16), time(8, 0), ZoneInfo("America/New_York"))
    next_transition = MarketHours.get_next_transition("NYSE", now=now)
    assert next_transition is not None
    assert next_transition.hour == 9
    assert next_transition.minute == 30


def test_time_to_next_transition(fake_calendar: FakeCalendar) -> None:
    now = datetime.combine(date(2026, 1, 16), time(8, 0), ZoneInfo("America/New_York"))
    delta = MarketHours.time_to_next_transition("NYSE", now=now)
    assert delta is not None
    assert delta == timedelta(hours=1, minutes=30)


# ============================================================================
# Additional tests for missing coverage
# ============================================================================


class FakeCalendarWithInvalidTz:
    """Calendar stub with an invalid timezone to trigger fallback."""

    def __init__(self, session_date: date) -> None:
        self.tz = "Invalid/Timezone"
        open_dt = datetime.combine(session_date, time(9, 30), ZoneInfo("America/New_York"))
        close_dt = datetime.combine(session_date, time(16, 0), ZoneInfo("America/New_York"))
        self.schedule = pd.DataFrame(
            {"market_open": [open_dt], "market_close": [close_dt]},
            index=[pd.Timestamp(session_date)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        return ts.date() == self.schedule.index[0].date()

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([self.schedule.index[0]])


class FakeCalendarNoTzAttr:
    """Calendar stub without a tz attribute to trigger AttributeError."""

    def __init__(self, session_date: date) -> None:
        open_dt = datetime.combine(session_date, time(9, 30), ZoneInfo("America/New_York"))
        close_dt = datetime.combine(session_date, time(16, 0), ZoneInfo("America/New_York"))
        self.schedule = pd.DataFrame(
            {"market_open": [open_dt], "market_close": [close_dt]},
            index=[pd.Timestamp(session_date)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        return ts.date() == self.schedule.index[0].date()

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([self.schedule.index[0]])


class FakeCalendarIsSessionRaises:
    """Calendar stub where is_session raises an exception."""

    def __init__(self, session_date: date) -> None:
        self.tz = "America/New_York"
        open_dt = datetime.combine(session_date, time(9, 30), ZoneInfo(self.tz))
        close_dt = datetime.combine(session_date, time(16, 0), ZoneInfo(self.tz))
        self.schedule = pd.DataFrame(
            {"market_open": [open_dt], "market_close": [close_dt]},
            index=[pd.Timestamp(session_date)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        raise RuntimeError("Simulated calendar error")

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([self.schedule.index[0]])


class FakeCalendarNonTradingDay:
    """Calendar stub where no day is a trading day."""

    def __init__(self, session_date: date) -> None:
        self.tz = "America/New_York"
        self.session_date = session_date
        open_dt = datetime.combine(session_date, time(9, 30), ZoneInfo(self.tz))
        close_dt = datetime.combine(session_date, time(16, 0), ZoneInfo(self.tz))
        self.schedule = pd.DataFrame(
            {"market_open": [open_dt], "market_close": [close_dt]},
            index=[pd.Timestamp(session_date)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        return False  # Always return False - no trading days

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        return pd.DatetimeIndex([])  # Empty - no trading sessions


class FakeCalendarFutureTradingDay:
    """Calendar stub where today is not a trading day but tomorrow is."""

    def __init__(self, session_date: date) -> None:
        self.tz = "America/New_York"
        self.session_date = session_date
        # Schedule for next trading day
        next_day = session_date + timedelta(days=1)
        open_dt = datetime.combine(next_day, time(9, 30), ZoneInfo(self.tz))
        close_dt = datetime.combine(next_day, time(16, 0), ZoneInfo(self.tz))
        self.schedule = pd.DataFrame(
            {"market_open": [open_dt], "market_close": [close_dt]},
            index=[pd.Timestamp(next_day)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        return ts.date() == self.schedule.index[0].date()

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        # Return next trading day
        return self.schedule.index


# Tests for _get_calendar method (lines 47-64)


def test_get_calendar_crypto_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that CRYPTO exchange returns None calendar (line 47-48)."""
    # Clear cache to ensure fresh call
    monkeypatch.setattr(MarketHours, "_calendar_cache", {})
    result = MarketHours._get_calendar("CRYPTO")
    assert result is None


def test_get_calendar_returns_cached_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that cached calendar is returned (lines 50-52)."""
    fake_cached = object()  # Some fake cached calendar
    monkeypatch.setattr(MarketHours, "_calendar_cache", {"XNYS": fake_cached})
    result = MarketHours._get_calendar("NYSE")
    assert result is fake_cached


# Tests for _get_exchange_tz method (lines 69, 72-81)


def test_get_exchange_tz_none_calendar_returns_utc() -> None:
    """Test that None calendar returns UTC timezone (line 69)."""
    result = MarketHours._get_exchange_tz(None)
    assert result == ZoneInfo("UTC")


def test_get_exchange_tz_invalid_timezone_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """Test fallback to America/New_York on ZoneInfoNotFoundError (lines 72-81)."""
    cal = FakeCalendarWithInvalidTz(date(2026, 1, 16))
    result = MarketHours._get_exchange_tz(cal)
    assert result == ZoneInfo("America/New_York")
    assert "market_calendar_timezone_fallback" in caplog.text


def test_get_exchange_tz_no_tz_attribute_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """Test fallback when calendar has no tz attribute (lines 72-81)."""
    cal = FakeCalendarNoTzAttr(date(2026, 1, 16))
    result = MarketHours._get_exchange_tz(cal)
    assert result == ZoneInfo("America/New_York")
    assert "market_calendar_timezone_fallback" in caplog.text


# Tests for _is_trading_day exception handling (lines 87-96)


def test_is_trading_day_exception_returns_false(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _is_trading_day returns False on exception (lines 87-96)."""
    cal = FakeCalendarIsSessionRaises(date(2026, 1, 16))
    result = MarketHours._is_trading_day(cal, date(2026, 1, 16))
    assert result is False
    assert "market_calendar_session_check_failed" in caplog.text


# Tests for _next_trading_day (lines 107-111)


def test_next_trading_day_empty_sessions_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _next_trading_day returns None when no sessions found (lines 109-110)."""
    cal = FakeCalendarNonTradingDay(date(2026, 1, 16))
    result = MarketHours._next_trading_day(cal, date(2026, 1, 16))
    assert result is None


def test_next_trading_day_returns_first_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _next_trading_day returns the first available session (line 111)."""
    session_date = date(2026, 1, 16)
    cal = FakeCalendar(session_date)
    result = MarketHours._next_trading_day(cal, session_date)
    assert result == session_date


# Tests for get_session_state (lines 121, 128, 142)


def test_get_session_state_none_calendar_returns_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_session_state returns CLOSED when calendar is None (line 121)."""
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: None))
    result = MarketHours.get_session_state("NYSE")
    assert result == SessionState.CLOSED


def test_get_session_state_non_trading_day_returns_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_session_state returns CLOSED on non-trading day (line 128)."""
    cal = FakeCalendarNonTradingDay(date(2026, 1, 17))  # Saturday
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal))
    now = datetime.combine(date(2026, 1, 17), time(10, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_session_state("NYSE", now=now)
    assert result == SessionState.CLOSED


def test_get_session_state_after_post_market_returns_closed(
    fake_calendar: FakeCalendar,
) -> None:
    """Test get_session_state returns CLOSED after post-market close (line 142)."""
    now = datetime.combine(date(2026, 1, 16), time(21, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_session_state("NYSE", now=now)
    assert result == SessionState.CLOSED


# Tests for get_next_transition (lines 152, 164, 167-175)


def test_get_next_transition_none_calendar_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_next_transition returns None when calendar is None (line 152)."""
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: None))
    result = MarketHours.get_next_transition("NYSE")
    assert result is None


def test_get_next_transition_before_pre_market_returns_pre_open(
    fake_calendar: FakeCalendar,
) -> None:
    """Test get_next_transition returns pre-market open time (line 164)."""
    now = datetime.combine(date(2026, 1, 16), time(3, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_next_transition("NYSE", now=now)
    assert result is not None
    assert result.hour == 4
    assert result.minute == 0


def test_get_next_transition_during_market_returns_close(
    fake_calendar: FakeCalendar,
) -> None:
    """Test get_next_transition returns market close during market hours (lines 167-168)."""
    now = datetime.combine(date(2026, 1, 16), time(10, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_next_transition("NYSE", now=now)
    assert result is not None
    assert result.hour == 16
    assert result.minute == 0


def test_get_next_transition_during_post_market_returns_post_close(
    fake_calendar: FakeCalendar,
) -> None:
    """Test get_next_transition returns post-market close (lines 169-170)."""
    now = datetime.combine(date(2026, 1, 16), time(17, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_next_transition("NYSE", now=now)
    assert result is not None
    assert result.hour == 20
    assert result.minute == 0


class FakeCalendarTwoTradingDays:
    """Calendar stub where both today and tomorrow are trading days."""

    def __init__(self, session_date: date) -> None:
        self.tz = "America/New_York"
        self.session_date = session_date
        next_day = session_date + timedelta(days=1)
        # Schedule includes both today and tomorrow
        open_dt_today = datetime.combine(session_date, time(9, 30), ZoneInfo(self.tz))
        close_dt_today = datetime.combine(session_date, time(16, 0), ZoneInfo(self.tz))
        open_dt_tomorrow = datetime.combine(next_day, time(9, 30), ZoneInfo(self.tz))
        close_dt_tomorrow = datetime.combine(next_day, time(16, 0), ZoneInfo(self.tz))
        self.schedule = pd.DataFrame(
            {
                "market_open": [open_dt_today, open_dt_tomorrow],
                "market_close": [close_dt_today, close_dt_tomorrow],
            },
            index=[pd.Timestamp(session_date), pd.Timestamp(next_day)],
        )

    def is_session(self, ts: pd.Timestamp) -> bool:
        return ts.date() in [idx.date() for idx in self.schedule.index]

    def sessions_in_range(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        # Filter to sessions within range
        mask = (self.schedule.index >= start) & (self.schedule.index <= end)
        return self.schedule.index[mask]


def test_get_next_transition_trading_day_after_post_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_next_transition on trading day after post-market returns next day (line 169->172)."""
    session_date = date(2026, 1, 16)
    cal = FakeCalendarTwoTradingDays(session_date)
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal))
    # Time is 21:00 - after post-market close (20:00) on a trading day
    now = datetime.combine(session_date, time(21, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_next_transition("NYSE", now=now)
    assert result is not None
    # Should return pre-market open of the next trading day
    assert result.date() == date(2026, 1, 17)
    assert result.hour == 4
    assert result.minute == 0


def test_get_next_transition_after_post_market_returns_next_day_pre_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_next_transition returns next trading day pre-market (lines 172-175)."""
    session_date = date(2026, 1, 16)
    cal = FakeCalendarFutureTradingDay(session_date)
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal))
    now = datetime.combine(session_date, time(21, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_next_transition("NYSE", now=now)
    assert result is not None
    assert result.date() == date(2026, 1, 17)
    assert result.hour == 4
    assert result.minute == 0


def test_get_next_transition_no_future_sessions_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_next_transition returns None when no future sessions (lines 173-174)."""
    cal = FakeCalendarNonTradingDay(date(2026, 1, 16))
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal))
    now = datetime.combine(date(2026, 1, 16), time(21, 0), ZoneInfo("America/New_York"))
    result = MarketHours.get_next_transition("NYSE", now=now)
    assert result is None


# Tests for time_to_next_transition (line 184)


def test_time_to_next_transition_none_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test time_to_next_transition returns None when no next transition (line 184)."""
    monkeypatch.setattr(
        MarketHours, "get_next_transition", classmethod(lambda cls, exchange, now: None)
    )
    result = MarketHours.time_to_next_transition("NYSE")
    assert result is None


# Tests for is_trading_day (lines 193-196)


def test_is_trading_day_none_calendar_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test is_trading_day returns False when calendar is None (lines 194-195)."""
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: None))
    result = MarketHours.is_trading_day("NYSE", date(2026, 1, 16))
    assert result is False


def test_is_trading_day_with_valid_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test is_trading_day returns True for valid trading day (line 196)."""
    session_date = date(2026, 1, 16)
    cal = FakeCalendar(session_date)
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal))
    result = MarketHours.is_trading_day("NYSE", session_date)
    assert result is True


def test_is_trading_day_with_non_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test is_trading_day returns False for non-trading day (line 196)."""
    cal = FakeCalendarNonTradingDay(date(2026, 1, 17))
    monkeypatch.setattr(MarketHours, "_get_calendar", classmethod(lambda cls, exchange: cal))
    result = MarketHours.is_trading_day("NYSE", date(2026, 1, 17))
    assert result is False
