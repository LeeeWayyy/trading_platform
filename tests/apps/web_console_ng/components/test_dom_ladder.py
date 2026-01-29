from __future__ import annotations

import types
from unittest.mock import Mock

import pytest

from apps.web_console_ng.components import dom_ladder as dom_module
from apps.web_console_ng.components.depth_visualizer import DepthVisualizer


class DummyLabel:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self._classes: set[str] = set()

    def classes(self, add: str | None = None, remove: str | None = None):
        if remove:
            for cls in remove.split():
                self._classes.discard(cls)
        if add:
            for cls in add.split():
                self._classes.add(cls)
        return self


class DummyElement:
    def __init__(self) -> None:
        self._classes: list[str] = []
        self._props: list[str] = []

    def classes(self, cls: str):
        self._classes.append(cls)
        return self

    def props(self, props: str):
        self._props.append(props)
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class DummyHandle:
    def __init__(self, callback) -> None:
        self._callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self._callback()


class DummyLoop:
    def __init__(self) -> None:
        self.calls: list[tuple[float, DummyHandle]] = []

    def call_later(self, delay: float, callback):
        handle = DummyHandle(callback)
        self.calls.append((delay, handle))
        return handle


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch):
    js_calls: list[str] = []

    def run_javascript(script: str) -> None:
        js_calls.append(script)

    dummy = types.SimpleNamespace(
        label=lambda text="": DummyLabel(text),
        card=lambda: DummyElement(),
        row=lambda: DummyElement(),
        element=lambda *args, **kwargs: DummyElement(),
        run_javascript=run_javascript,
    )
    monkeypatch.setattr(dom_module, "ui", dummy)
    return js_calls


def test_depth_visualizer_orders_levels() -> None:
    visualizer = DepthVisualizer(levels=2)
    payload = visualizer.build_payload(
        {
            "S": "AAPL",
            "b": [{"p": 10.0, "s": 100}, {"p": 11.0, "s": 50}],
            "a": [{"p": 12.0, "s": 60}, {"p": 11.5, "s": 40}],
        }
    )

    assert payload is not None
    assert [level["price"] for level in payload["bids"]] == [11.0, 10.0]
    assert [level["price"] for level in payload["asks"]] == [11.5, 12.0]
    assert payload["mid"] == (11.0 + 11.5) / 2


def test_depth_visualizer_flags_large_orders() -> None:
    visualizer = DepthVisualizer(levels=1, history_size=4, large_multiplier=2.0)
    payload1 = visualizer.build_payload({"S": "AAPL", "b": [{"p": 10, "s": 10}]})
    payload2 = visualizer.build_payload({"S": "AAPL", "b": [{"p": 10, "s": 30}]})

    assert payload1 is not None
    assert payload2 is not None
    assert payload2["bids"][0]["is_large"] is True


def test_depth_visualizer_handles_empty_and_invalid() -> None:
    visualizer = DepthVisualizer(levels=2)
    assert visualizer.build_payload({"S": ""}) is None
    assert visualizer.build_payload({"S": "AAPL", "b": "bad", "a": []}) is None

    payload = visualizer.build_payload({"S": "AAPL", "b": [], "a": [], "t": 123})
    assert payload is not None
    assert payload["mid"] is None
    assert payload["bids"] == []
    assert payload["asks"] == []
    assert payload["timestamp"] == "123"


def test_dom_ladder_create_and_set_symbol(dummy_ui, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dom_module.Level2WebSocketService,
        "entitlement_status",
        staticmethod(lambda: (True, "Mock mode (synthetic data)")),
    )
    comp = dom_module.DOMLadderComponent()
    comp.create()
    assert comp._status_label is not None
    assert "warning" in " ".join(comp._status_label._classes)

    comp.set_symbol("AAPL")
    assert comp._symbol_label is not None
    assert comp._symbol_label.text == "AAPL"
    assert "MOCK DATA - AAPL" in comp._status_label.text

    comp.set_symbol(None)
    assert comp._symbol_label.text == "--"
    assert "Select a symbol" in comp._status_label.text


def test_dom_ladder_not_entitled_clears(dummy_ui, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dom_module.Level2WebSocketService,
        "entitlement_status",
        staticmethod(lambda: (False, "Level 2 data not enabled")),
    )
    comp = dom_module.DOMLadderComponent()
    comp.create()
    assert comp._status_label is not None
    assert "Level 2 data not enabled" in comp._status_label.text
    assert any("DOMLadder.clear" in call for call in dummy_ui)


def test_dom_ladder_emit_throttling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dom_module.Level2WebSocketService,
        "entitlement_status",
        staticmethod(lambda: (True, "Mock mode (synthetic data)")),
    )
    comp = dom_module.DOMLadderComponent()
    comp._emit = Mock()
    loop = DummyLoop()
    monkeypatch.setattr(dom_module.asyncio, "get_running_loop", lambda: loop)

    now = 100.0
    monkeypatch.setattr(dom_module.time, "time", lambda: now)
    comp._schedule_emit({"foo": "bar"})
    comp._emit.assert_called_once()

    comp._emit.reset_mock()
    comp._last_emit = now
    later = now + comp._min_interval / 2
    monkeypatch.setattr(dom_module.time, "time", lambda: later)
    comp._schedule_emit({"bar": "baz"})
    assert comp._pending_payload == {"bar": "baz"}
    assert loop.calls
    delay, handle = loop.calls[0]
    assert delay > 0
    handle.fire()
    comp._emit.assert_called_once_with({"bar": "baz"})


def test_dom_ladder_clear_and_emit_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dom_module.Level2WebSocketService,
        "entitlement_status",
        staticmethod(lambda: (True, "Mock mode (synthetic data)")),
    )
    comp = dom_module.DOMLadderComponent()
    comp._container_id = "abc"
    monkeypatch.setattr(
        dom_module.ui,
        "run_javascript",
        Mock(side_effect=RuntimeError("boom")),
    )
    comp.clear("hello")
    comp._emit({"x": 1})


def test_dom_ladder_dispose_cancels_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dom_module.Level2WebSocketService,
        "entitlement_status",
        staticmethod(lambda: (True, "Mock mode (synthetic data)")),
    )
    comp = dom_module.DOMLadderComponent()
    handle = DummyHandle(lambda: None)
    comp._flush_handle = handle
    comp.dispose()
    assert handle.cancelled is True
