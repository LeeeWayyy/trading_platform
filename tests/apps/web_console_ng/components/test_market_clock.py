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
