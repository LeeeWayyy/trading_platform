from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from apps.web_console_ng.pages import performance as perf_module


class DummyElement:
    def __init__(self, ui: DummyUI, kind: str, **kwargs: Any) -> None:
        self.ui = ui
        self.kind = kind
        self.kwargs = kwargs
        self.label = kwargs.get("label")
        self.value = kwargs.get("value")
        self.text = kwargs.get("text", "")
        self.visible = True
        self._on_click: Callable[..., Any] | None = None
        self._on_value_change: Callable[..., Any] | None = None

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *_: Any, **__: Any) -> DummyElement:
        return self

    def props(self, *_: Any, **__: Any) -> DummyElement:
        return self

    def on_click(self, fn: Callable[..., Any] | None) -> DummyElement:
        self._on_click = fn
        return self

    def on_value_change(self, fn: Callable[..., Any] | None) -> DummyElement:
        self._on_value_change = fn
        return self

    def clear(self) -> None:
        self.ui.clears.append(self.kind)


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.dates: list[DummyElement] = []
        self.tables: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.plotlies: list[Any] = []
        self.timers: list[DummyElement] = []
        self.clears: list[str] = []
        self.context = SimpleNamespace(client=SimpleNamespace(storage={}))

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(self, "label", text=text)
        self.labels.append(el)
        return el

    def button(self, label: str, on_click: Callable[..., Any] | None = None) -> DummyElement:
        el = DummyElement(self, "button", label=label)
        el.on_click(on_click)
        self.buttons.append(el)
        return el

    def date(self, value: Any = None) -> DummyElement:
        el = DummyElement(self, "date", value=value)
        self.dates.append(el)
        return el

    def card(self) -> DummyElement:
        return DummyElement(self, "card")

    def row(self) -> DummyElement:
        return DummyElement(self, "row")

    def column(self) -> DummyElement:
        return DummyElement(self, "column")

    def table(self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement(self, "table")

    def plotly(self, fig: Any) -> DummyElement:
        self.plotlies.append(fig)
        return DummyElement(self, "plotly")

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def separator(self) -> DummyElement:
        return DummyElement(self, "separator")

    def timer(self, interval: float, callback: Callable[..., Any]) -> DummyElement:
        el = DummyElement(self, "timer")
        el.interval = interval
        el.callback = callback
        self.timers.append(el)
        return el


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(perf_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None, *args: Any) -> None:
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb(*args)
    else:
        cb(*args)


@pytest.mark.asyncio()
async def test_render_performance_dashboard_invalid_date_notify(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: dict[str, Any] = {}

    def fake_realtime(_: Any) -> None:
        called["realtime"] = True

    def fake_positions() -> None:
        called["positions"] = True

    def fake_historical(start: date, end: date, strategies: list[str]) -> None:
        called["historical"] = (start, end, strategies)

    monkeypatch.setattr(perf_module, "_render_realtime_pnl", fake_realtime)
    monkeypatch.setattr(perf_module, "_render_position_summary", fake_positions)
    monkeypatch.setattr(perf_module, "_render_historical_performance", fake_historical)

    await perf_module._render_performance_dashboard({"user_id": "u1"}, ["alpha1"])

    from_input = dummy_ui.dates[0]
    from_input.value = "bad-date"
    await _call(from_input._on_value_change, None)

    assert any("Invalid date format" in n["text"] for n in dummy_ui.notifications)
    assert called.get("realtime") is True


@pytest.mark.asyncio()
async def test_render_performance_dashboard_preset_buttons(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_realtime(_: Any) -> None:
        return None

    def fake_positions() -> None:
        return None

    def fake_historical(start: date, end: date, strategies: list[str]) -> None:
        captured["start"] = start
        captured["end"] = end
        captured["strategies"] = strategies

    monkeypatch.setattr(perf_module, "_render_realtime_pnl", fake_realtime)
    monkeypatch.setattr(perf_module, "_render_position_summary", fake_positions)
    monkeypatch.setattr(perf_module, "_render_historical_performance", fake_historical)

    await perf_module._render_performance_dashboard({"user_id": "u1"}, ["alpha1"])

    seven_button = next(b for b in dummy_ui.buttons if b.label == "7 Days")
    await _call(seven_button._on_click)

    assert captured["strategies"] == ["alpha1"]
    assert captured["end"] >= captured["start"]


def test_render_historical_performance_invalid_range(dummy_ui: DummyUI) -> None:
    start = date(2026, 1, 10)
    end = date(2026, 1, 1)
    perf_module._render_historical_performance(start, end, ["alpha1"])
    assert any("End date must be after start date" in label.text for label in dummy_ui.labels)
