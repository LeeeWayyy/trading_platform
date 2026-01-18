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
