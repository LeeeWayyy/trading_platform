from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from apps.web_console_ng.pages import backtest as backtest_module


class DummyElement:
    def __init__(self, ui: "DummyUI", kind: str, **kwargs: Any) -> None:
        self.ui = ui
        self.kind = kind
        self.kwargs = kwargs
        self.label = kwargs.get("label")
        self.value = kwargs.get("value")
        self.text = kwargs.get("text", "")
        self.visible = True
        self.interval = kwargs.get("interval")
        self._on_click: Callable[..., Any] | None = None
        self._on_value_change: Callable[..., Any] | None = None
        self._on_event: tuple[str, Callable[..., Any]] | None = None

    def __enter__(self) -> "DummyElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *_: Any, **__: Any) -> "DummyElement":
        return self

    def props(self, *_: Any, **__: Any) -> "DummyElement":
        return self

    def on_click(self, fn: Callable[..., Any] | None) -> "DummyElement":
        self._on_click = fn
        return self

    def on_value_change(self, fn: Callable[..., Any] | None) -> "DummyElement":
        self._on_value_change = fn
        return self

    def on(self, event: str, fn: Callable[..., Any] | None) -> "DummyElement":
        if fn is not None:
            self._on_event = (event, fn)
        return self

    def set_text(self, value: str) -> None:
        self.text = value

    def refresh(self) -> None:
        self.ui.refreshes.append(self.kind)

    def cancel(self) -> None:
        self.ui.cancels.append(self.kind)


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.dates: list[DummyElement] = []
        self.notifications: list[dict[str, Any]] = []
        self.timers: list[DummyElement] = []
        self.refreshes: list[str] = []
        self.cancels: list[str] = []
        self.context = SimpleNamespace(client=SimpleNamespace(storage={}))

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(self, "label", text=text)
        self.labels.append(el)
        return el

    def button(self, label: str, on_click: Callable[..., Any] | None = None, color: str | None = None) -> DummyElement:
        el = DummyElement(self, "button", label=label, color=color)
        el.on_click(on_click)
        self.buttons.append(el)
        return el

    def input(self, label: str | None = None, placeholder: str | None = None, value: Any = None) -> DummyElement:
        el = DummyElement(self, "input", label=label, placeholder=placeholder, value=value)
        self.inputs.append(el)
        return el

    def select(self, label: str | None = None, options: list[str] | dict[str, Any] | None = None, value: Any = None, multiple: bool = False) -> DummyElement:
        el = DummyElement(self, "select", label=label, options=options, value=value, multiple=multiple)
        self.selects.append(el)
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

    def tabs(self) -> DummyElement:
        return DummyElement(self, "tabs")

    def tab(self, label: str) -> DummyElement:
        return DummyElement(self, "tab", label=label)

    def tab_panels(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement(self, "tab_panels")

    def tab_panel(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "tab_panel")

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: self.refreshes.append(fn.__name__)
        return wrapper

    def timer(self, interval: float, callback: Callable[..., Any]) -> DummyElement:
        el = DummyElement(self, "timer", interval=interval)
        el.callback = callback
        self.timers.append(el)
        return el

    def linear_progress(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "linear_progress")

    def icon(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "icon")


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(backtest_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None, *args: Any) -> None:
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb(*args)
    else:
        cb(*args)


def test_get_user_id_missing_raises() -> None:
    with pytest.raises(ValueError, match="User identification required"):
        backtest_module._get_user_id({})


def test_get_poll_interval_progressive() -> None:
    assert backtest_module._get_poll_interval(0) == 2.0
    assert backtest_module._get_poll_interval(31) == 5.0
    assert backtest_module._get_poll_interval(100) == 10.0
    assert backtest_module._get_poll_interval(400) == 30.0


def test_get_user_jobs_sync_parses_progress() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, *_: Any, **__: Any) -> None:
            self.calls += 1

        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def fetchall(self) -> list[dict[str, Any]]:
            return [
                {
                    "job_id": "j1",
                    "alpha_name": "alpha1",
                    "start_date": "2025-01-01",
                    "end_date": "2025-02-01",
                    "status": "running",
                    "created_at": None,
                    "error_message": None,
                    "mean_ic": None,
                    "icir": None,
                    "hit_rate": None,
                    "coverage": None,
                    "average_turnover": None,
                    "result_path": None,
                    "provider": "crsp",
                },
                {
                    "job_id": "j2",
                    "alpha_name": "alpha2",
                    "start_date": "2025-01-01",
                    "end_date": "2025-02-01",
                    "status": "running",
                    "created_at": None,
                    "error_message": None,
                    "mean_ic": None,
                    "icir": None,
                    "hit_rate": None,
                    "coverage": None,
                    "average_turnover": None,
                    "result_path": None,
                    "provider": None,
                },
                {
                    "job_id": "j3",
                    "alpha_name": "alpha3",
                    "start_date": "2025-01-01",
                    "end_date": "2025-02-01",
                    "status": "running",
                    "created_at": None,
                    "error_message": None,
                    "mean_ic": None,
                    "icir": None,
                    "hit_rate": None,
                    "coverage": None,
                    "average_turnover": None,
                    "result_path": None,
                    "provider": "crsp",
                },
            ]

        def fetchone(self) -> dict[str, Any] | None:
            return {"count": 3}

    class FakeConn:
        def cursor(self, *args: Any, **kwargs: Any) -> FakeCursor:
            return FakeCursor()

        def __enter__(self) -> "FakeConn":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakePool:
        def connection(self) -> FakeConn:
            return FakeConn()

    class FakeRedis:
        def mget(self, *_: Any, **__: Any) -> list[bytes | None]:
            return [
                json.dumps({"pct": 150}).encode(),
                json.dumps({"pct": -10}).encode(),
                json.dumps({"pct": "bad"}).encode(),
            ]

    jobs = backtest_module._get_user_jobs_sync(
        created_by="u1",
        status=["running"],
        db_pool=FakePool(),
        redis_client=FakeRedis(),
    )
    assert [j["progress_pct"] for j in jobs] == [100.0, 0.0, 0.0]
    assert jobs[1]["provider"] == "crsp"


@pytest.mark.asyncio()
async def test_render_new_backtest_form_invalid_symbols(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backtest_module, "_get_available_alphas", lambda: ["alpha1"])

    async def io_bound(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(backtest_module.run, "io_bound", io_bound)

    class DummyQueue:
        def enqueue(self, *_: Any, **__: Any) -> Any:
            return SimpleNamespace(id="job123")

    monkeypatch.setattr(backtest_module, "_get_job_queue", lambda: DummyQueue())

    await backtest_module._render_new_backtest_form({"user_id": "u1"})

    provider_select = next(s for s in dummy_ui.selects if s.label == "Data Source")
    universe_input = next(i for i in dummy_ui.inputs if i.label == "Yahoo Universe (comma-separated tickers)")
    submit_button = next(b for b in dummy_ui.buttons if b.label == "Run Backtest")

    provider_select.value = "Yahoo Finance (dev only)"
    universe_input.value = "AAPL, $$$"

    await _call(submit_button._on_click)
    assert any("Invalid symbols" in n["text"] for n in dummy_ui.notifications)
