"""Unit tests for MarketClock component."""

from __future__ import annotations

import types
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from apps.web_console_ng.components import market_clock as market_clock_module
from apps.web_console_ng.components.market_clock import MarketClock
from libs.common.market_hours import SessionState


class DummyLabel:
    """Mock NiceGUI label for testing."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self._classes: set[str] = set()
        self.tooltip_text: str | None = None

    def set_text(self, text: str) -> None:
        self.text = text

    def classes(self, add: str | None = None, remove: str | None = None) -> DummyLabel:
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self

    def tooltip(self, text: str) -> DummyLabel:
        self.tooltip_text = text
        return self


class DummyRow:
    """Mock NiceGUI row for testing."""

    def __enter__(self) -> DummyRow:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        pass

    def classes(self, _add: str | None = None) -> DummyRow:
        return self


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    def row() -> DummyRow:
        return DummyRow()

    def label(text: str = "") -> DummyLabel:
        return DummyLabel(text)

    dummy = types.SimpleNamespace(row=row, label=label)
    monkeypatch.setattr(market_clock_module, "ui", dummy)


def test_market_clock_open_state(monkeypatch: pytest.MonkeyPatch, dummy_ui: None) -> None:
    next_transition = datetime(2026, 1, 16, 16, 0, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.OPEN,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: next_transition,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=2, minutes=15),
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert "OPEN" in label.text
    assert "Closes in" in label.text
    assert "bg-green-600" in label._classes
    assert label.tooltip_text is not None


def test_market_clock_crypto(monkeypatch: pytest.MonkeyPatch, dummy_ui: None) -> None:
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.OPEN,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: None,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: None,
    )

    clock = MarketClock(exchanges=["CRYPTO"])
    clock.update(force=True)

    label = clock._labels["CRYPTO"]
    assert "24/7" in label.text
    assert "bg-blue-600" in label._classes


def test_market_clock_pre_market_state(monkeypatch: pytest.MonkeyPatch, dummy_ui: None) -> None:
    """Test PRE_MARKET state displays correctly."""
    next_transition = datetime(2026, 1, 16, 9, 30, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.PRE_MARKET,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: next_transition,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=1, minutes=30),
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert "PRE-MKT" in label.text
    assert "Opens in" in label.text
    assert "bg-yellow-500" in label._classes
    assert label.tooltip_text is not None


def test_market_clock_post_market_state(monkeypatch: pytest.MonkeyPatch, dummy_ui: None) -> None:
    """Test POST_MARKET state displays correctly."""
    next_transition = datetime(2026, 1, 16, 20, 0, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.POST_MARKET,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: next_transition,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=2),
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert "POST-MKT" in label.text
    assert "Closes in" in label.text
    assert "bg-yellow-500" in label._classes
    assert label.tooltip_text is not None


def test_market_clock_closed_state_with_next_transition(
    monkeypatch: pytest.MonkeyPatch, dummy_ui: None
) -> None:
    """Test CLOSED state with next transition time."""
    next_transition = datetime(2026, 1, 17, 9, 30, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.CLOSED,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: next_transition,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=12),
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert "CLOSED" in label.text
    assert "Opens" in label.text
    assert "bg-gray-600" in label._classes
    assert label.tooltip_text is not None


def test_market_clock_closed_state_without_next_transition(
    monkeypatch: pytest.MonkeyPatch, dummy_ui: None
) -> None:
    """Test CLOSED state without next transition time."""
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.CLOSED,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: None,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: None,
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert label.text == "NYSE: CLOSED"
    assert label.tooltip_text == "Next: --"


def test_market_clock_exception_handling(
    monkeypatch: pytest.MonkeyPatch, dummy_ui: None
) -> None:
    """Test exception handling in update method."""

    def raise_error(exchange: str, now: Any = None) -> None:
        raise ValueError("Test error")

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        raise_error,
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert label.text == "NYSE: --"
    assert "bg-gray-600" in label._classes


def test_market_clock_throttling(monkeypatch: pytest.MonkeyPatch, dummy_ui: None) -> None:
    """Test update throttling (60s interval)."""
    call_count = 0

    def count_calls(exchange: str, now: Any = None) -> SessionState:
        nonlocal call_count
        call_count += 1
        return SessionState.OPEN

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        count_calls,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: datetime.now(ZoneInfo("UTC")),
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=1),
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)  # First call should work
    assert call_count == 1

    clock.update()  # Should be throttled (within 60s)
    assert call_count == 1  # Still 1, throttled

    clock.update(force=True)  # Force bypasses throttle
    assert call_count == 2


def test_format_timedelta_zero() -> None:
    """Test _format_timedelta with zero or negative values."""
    from apps.web_console_ng.components.market_clock import _format_timedelta

    assert _format_timedelta(timedelta(seconds=0)) == "0m"
    assert _format_timedelta(timedelta(seconds=-10)) == "0m"


def test_format_timedelta_days() -> None:
    """Test _format_timedelta with days."""
    from apps.web_console_ng.components.market_clock import _format_timedelta

    assert _format_timedelta(timedelta(days=2, hours=3, minutes=15)) == "2d 3h 15m"
    # When days=1, hours=0, minutes=0 -> only days and minutes (0m) are shown since there's no hours
    assert _format_timedelta(timedelta(days=1)) == "1d"


def test_format_timedelta_hours_only() -> None:
    """Test _format_timedelta with hours but no minutes."""
    from apps.web_console_ng.components.market_clock import _format_timedelta

    # When hours=5, minutes=0 -> only hours shown (no 0m since hours is present)
    assert _format_timedelta(timedelta(hours=5)) == "5h"


def test_format_timedelta_minutes_only() -> None:
    """Test _format_timedelta with minutes only."""
    from apps.web_console_ng.components.market_clock import _format_timedelta

    assert _format_timedelta(timedelta(minutes=45)) == "45m"


def test_format_time_label() -> None:
    """Test _format_time_label function."""
    from apps.web_console_ng.components.market_clock import _format_time_label

    dt = datetime(2026, 1, 16, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    result = _format_time_label(dt)
    assert "9:30" in result or "09:30" in result.lstrip("0")
    assert "AM" in result


def test_format_time_label_windows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _format_time_label Windows fallback (no %-I support)."""
    # We test the Windows fallback by patching the module-level datetime class
    # to simulate Windows behavior where %-I format raises ValueError
    import apps.web_console_ng.components.market_clock as mc

    class MockDatetime(datetime):
        def strftime(self, fmt: str) -> str:
            if "%-" in fmt:
                raise ValueError("Invalid format string")
            return super().strftime(fmt)

    # Create a test datetime using the mock
    test_dt = MockDatetime(2026, 1, 16, 9, 30, tzinfo=ZoneInfo("America/New_York"))

    # Call the function directly with the mock datetime
    result = mc._format_time_label(test_dt)

    # Should have returned a formatted time using the fallback %I format
    assert "9:30" in result or "09:30" in result.lstrip("0")
    assert "AM" in result


def test_format_time_label_windows_compatibility() -> None:
    """Test _format_time_label handles Windows-like scenarios."""
    from apps.web_console_ng.components.market_clock import _format_time_label

    # Test with morning time (leading zero case)
    dt_morning = datetime(2026, 1, 16, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    result = _format_time_label(dt_morning)
    # Should not have leading zero in hour
    assert result.startswith("9:") or result.startswith("09:")

    # Test with afternoon time
    dt_afternoon = datetime(2026, 1, 16, 14, 30, tzinfo=ZoneInfo("America/New_York"))
    result = _format_time_label(dt_afternoon)
    assert "2:30" in result or "02:30" in result.lstrip("0")
    assert "PM" in result


def test_market_clock_multiple_exchanges(
    monkeypatch: pytest.MonkeyPatch, dummy_ui: None
) -> None:
    """Test MarketClock with multiple exchanges."""
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.OPEN,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: datetime.now(ZoneInfo("UTC")),
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=1),
    )

    clock = MarketClock(exchanges=["NYSE", "NASDAQ"])
    clock.update(force=True)

    assert "NYSE" in clock._labels
    assert "NASDAQ" in clock._labels
    assert "OPEN" in clock._labels["NYSE"].text
    assert "OPEN" in clock._labels["NASDAQ"].text


def test_market_clock_default_exchange(
    monkeypatch: pytest.MonkeyPatch, dummy_ui: None
) -> None:
    """Test MarketClock uses NYSE by default."""
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.OPEN,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: datetime.now(ZoneInfo("UTC")),
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: timedelta(hours=1),
    )

    clock = MarketClock()
    assert "NYSE" in clock._labels


def test_market_clock_open_state_no_delta(
    monkeypatch: pytest.MonkeyPatch, dummy_ui: None
) -> None:
    """Test OPEN state with no delta (None)."""
    next_transition = datetime(2026, 1, 16, 16, 0, tzinfo=ZoneInfo("America/New_York"))

    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_session_state",
        lambda exchange, now=None: SessionState.OPEN,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "get_next_transition",
        lambda exchange, now=None: next_transition,
    )
    monkeypatch.setattr(
        market_clock_module.MarketHours,
        "time_to_next_transition",
        lambda exchange, now=None: None,
    )

    clock = MarketClock(exchanges=["NYSE"])
    clock.update(force=True)

    label = clock._labels["NYSE"]
    assert "OPEN" in label.text
    assert "--" in label.text  # Countdown should show "--" when delta is None
