from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng.auth import middleware as auth_middleware
from apps.web_console_ng.pages import manual_order as manual_order_module


class FakeEvent:
    def __init__(self, value: Any) -> None:
        self.value = value


class FakeElement:
    def __init__(
        self,
        kind: str,
        *,
        text: str | None = None,
        label: str | None = None,
        value: Any = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.label = label
        self.value = value
        self.options = options or {}
        self.visible = True
        self.enabled = True
        self._events: dict[str, Callable[..., Any]] = {}
        self._value_change_cb: Callable[[Any], Any] | None = None
        self._click_cb: Callable[..., Any] | None = None
        self.visibility_set: bool | None = None
        self.updated = False

    def __enter__(self) -> FakeElement:  # pragma: no cover - trivial
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - trivial
        return None

    def classes(self, value: str | None = None, *, add: str | None = None, remove: str | None = None) -> FakeElement:
        return self

    def props(self, _value: str) -> FakeElement:
        return self

    def set_visibility(self, visible: bool) -> None:
        self.visible = visible
        self.visibility_set = visible

    def set_text(self, text: str) -> None:
        self.text = text

    def update(self) -> None:
        self.updated = True

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._events[event] = handler
        if event == "click":
            self._click_cb = handler

    def on_value_change(self, handler: Callable[[Any], Any]) -> None:
        self._value_change_cb = handler

    def on_click(self, handler: Callable[..., Any] | None = None, **_kwargs: Any) -> FakeElement:
        self._click_cb = handler
        return self

    def disable(self) -> None:
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True

    def open(self) -> None:
        return None

    def close(self) -> None:
        if "close" in self._events:
            self._events["close"]()


class FakeAgGrid(FakeElement):
    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__("aggrid", options=options)
        self.selected_rows: list[dict[str, Any]] = []

    async def get_selected_rows(self) -> list[dict[str, Any]]:
        return list(self.selected_rows)


class FakeTimer:
    def __init__(self, interval: float, callback: Callable[..., Any]) -> None:
        self.interval = interval
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeUI:
    def __init__(self) -> None:
        self.elements: list[FakeElement] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.navigations: list[str] = []
        self.timers: list[FakeTimer] = []
        self.navigate = SimpleNamespace(to=self._navigate_to)

    def _navigate_to(self, path: str) -> None:
        self.navigations.append(path)

    def notify(self, message: str, *, type: str | None = None) -> None:
        self.notifications.append((message, type))

    def label(self, text: str) -> FakeElement:
        element = FakeElement("label", text=text)
        self.elements.append(element)
        return element

    def input(self, label: str, **kwargs: Any) -> FakeElement:
        element = FakeElement("input", label=label, value=kwargs.get("value"))
        self.elements.append(element)
        return element

    def number(self, label: str, **kwargs: Any) -> FakeElement:
        element = FakeElement("number", label=label, value=kwargs.get("value"))
        self.elements.append(element)
        return element

    def textarea(self, label: str, **kwargs: Any) -> FakeElement:
        element = FakeElement("textarea", label=label, value=kwargs.get("value"))
        self.elements.append(element)
        return element

    def select(self, *, options: list[str], label: str, value: Any = None) -> FakeElement:
        element = FakeElement("select", label=label, value=value)
        element.options["choices"] = options
        self.elements.append(element)
        return element

    def button(self, text: str, on_click: Callable[..., Any] | None = None, **_kwargs: Any) -> FakeElement:
        element = FakeElement("button", text=text)
        if on_click is not None:
            element.on_click(on_click)
        self.elements.append(element)
        return element

    def card(self) -> FakeElement:
        element = FakeElement("card")
        self.elements.append(element)
        return element

    def row(self) -> FakeElement:
        element = FakeElement("row")
        self.elements.append(element)
        return element

    def column(self) -> FakeElement:
        element = FakeElement("column")
        self.elements.append(element)
        return element

    def dialog(self) -> FakeElement:
        element = FakeElement("dialog")
        self.elements.append(element)
        return element

    def icon(self, _name: str, **_kwargs: Any) -> FakeElement:
        element = FakeElement("icon")
        self.elements.append(element)
        return element

    def aggrid(self, options: dict[str, Any]) -> FakeAgGrid:
        element = FakeAgGrid(options)
        self.elements.append(element)
        return element

    def timer(self, interval: float, callback: Callable[..., Any]) -> FakeTimer:
        timer = FakeTimer(interval, callback)
        self.timers.append(timer)
        return timer


class FakeRealtimeUpdater:
    def __init__(self, client_id: str, client: Any) -> None:
        self.client_id = client_id
        self.client = client
        self.subscriptions: list[tuple[str, Callable[..., Any]]] = []
        self.cleaned_up = False

    async def subscribe(self, channel: str, handler: Callable[..., Any]) -> None:
        self.subscriptions.append((channel, handler))

    async def cleanup(self) -> None:
        self.cleaned_up = True


@dataclass
class FakeLifecycleManager:
    client_id: str = "client-123"
    cleanup_calls: list[tuple[str, Callable[..., Any]]] | None = None

    def generate_client_id(self) -> str:
        return self.client_id

    async def register_cleanup_callback(self, client_id: str, callback: Callable[..., Any]) -> None:
        if self.cleanup_calls is None:
            self.cleanup_calls = []
        self.cleanup_calls.append((client_id, callback))


def _find_element(elements: list[FakeElement], *, kind: str, label: str | None = None, text: str | None = None) -> FakeElement:
    for element in elements:
        if element.kind != kind:
            continue
        if label is not None and element.label != label:
            continue
        if text is not None and element.text != text:
            continue
        return element
    raise AssertionError(f"Element not found: kind={kind} label={label} text={text}")


def _unwrap_page(func: Callable[..., Any]) -> Callable[..., Any]:
    unwrapped = func
    while hasattr(unwrapped, "__wrapped__"):
        unwrapped = unwrapped.__wrapped__  # type: ignore[attr-defined]
    return unwrapped


@pytest.fixture()
def fake_ui(monkeypatch: pytest.MonkeyPatch) -> FakeUI:
    ui = FakeUI()
    monkeypatch.setattr(manual_order_module, "ui", ui)
    monkeypatch.setattr(manual_order_module, "app", SimpleNamespace(storage=SimpleNamespace(user={})))
    return ui


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = SimpleNamespace(user={})
    monkeypatch.setattr(auth_middleware, "app", SimpleNamespace(storage=storage))
    monkeypatch.setattr(
        auth_middleware,
        "_validate_and_get_user_for_decorator",
        AsyncMock(return_value=({}, None, False)),
    )


@pytest.fixture()
def trading_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.fetch_kill_switch_status = AsyncMock(return_value={"state": "DISENGAGED"})
    client.submit_manual_order = AsyncMock(return_value={"client_order_id": "abc1234567890"})
    monkeypatch.setattr(manual_order_module.AsyncTradingClient, "get", lambda: client)
    return client


@pytest.fixture()
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> FakeLifecycleManager:
    manager = FakeLifecycleManager()
    monkeypatch.setattr(manual_order_module.ClientLifecycleManager, "get", lambda: manager)
    return manager


@pytest.fixture()
def realtime(monkeypatch: pytest.MonkeyPatch) -> type[FakeRealtimeUpdater]:
    monkeypatch.setattr(manual_order_module, "RealtimeUpdater", FakeRealtimeUpdater)
    monkeypatch.setattr(manual_order_module, "kill_switch_channel", lambda: "kill-switch")
    return FakeRealtimeUpdater


@pytest.fixture()
def audit_log_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _audit_log(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(manual_order_module, "audit_log", _audit_log)
    return calls


@pytest.mark.asyncio()
async def test_manual_order_redirects_on_missing_user_id(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manual_order_module, "get_current_user", lambda: {})
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    assert fake_ui.notifications == [("Session expired - please log in again", "negative")]
    assert fake_ui.navigations == ["/login"]
    trading_client.fetch_kill_switch_status.assert_not_called()
    assert lifecycle.cleanup_calls is None


@pytest.mark.asyncio()
async def test_manual_order_blocks_viewer(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "viewer"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    assert fake_ui.notifications == [("Viewers cannot submit orders", "negative")]
    assert fake_ui.navigations == ["/"]
    trading_client.fetch_kill_switch_status.assert_not_called()


@pytest.mark.asyncio()
async def test_manual_order_order_type_toggle(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    order_type_select = _find_element(fake_ui.elements, kind="select", label="Order Type")
    order_type_select._value_change_cb(FakeEvent("limit"))

    limit_price_containers = [e for e in fake_ui.elements if e.kind == "column" and e.visibility_set is not None]
    assert limit_price_containers
    assert limit_price_containers[0].visible is True

    order_type_select._value_change_cb(FakeEvent("market"))
    assert limit_price_containers[0].visible is False


@pytest.mark.asyncio()
async def test_manual_order_submit_flow_success(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    order_type_select = _find_element(fake_ui.elements, kind="select", label="Order Type")
    side_select = _find_element(fake_ui.elements, kind="select", label="Side")
    tif_select = _find_element(fake_ui.elements, kind="select", label="Time in Force")
    limit_price_input = _find_element(fake_ui.elements, kind="number", label="Limit Price")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"
    order_type_select.value = "market"
    side_select.value = "buy"
    tif_select.value = "day"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert trading_client.submit_manual_order.await_count == 1
    payload = trading_client.submit_manual_order.call_args.args[0]
    assert payload["symbol"] == "AAPL"
    assert payload["qty"] == 10
    assert payload["order_type"] == "market"
    assert payload["reason"] == "Valid reason for trade"

    assert audit_log_calls
    assert audit_log_calls[-1]["action"] == "manual_order_submitted"

    assert any("Order submitted:" in message for message, _ in fake_ui.notifications)
    assert symbol_input.value == ""
    assert qty_input.value == 0
    assert reason_input.value == ""
    assert order_type_select.value == "market"
    assert side_select.value == "buy"
    assert tif_select.value == "day"
    assert limit_price_input.value is None
