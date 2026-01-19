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

    def classes(
        self, value: str | None = None, *, add: str | None = None, remove: str | None = None
    ) -> FakeElement:
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

    def button(
        self, text: str, on_click: Callable[..., Any] | None = None, **_kwargs: Any
    ) -> FakeElement:
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


def _find_element(
    elements: list[FakeElement], *, kind: str, label: str | None = None, text: str | None = None
) -> FakeElement:
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
    monkeypatch.setattr(
        manual_order_module, "app", SimpleNamespace(storage=SimpleNamespace(user={}))
    )
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

    limit_price_containers = [
        e for e in fake_ui.elements if e.kind == "column" and e.visibility_set is not None
    ]
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


# ============================================================================
# Kill Switch Tests (Lines 140-178)
# ============================================================================


@pytest.mark.asyncio()
async def test_kill_switch_cached_engaged_blocks_preview(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that cached kill switch ENGAGED state blocks preview."""
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    # Simulate real-time kill switch update to ENGAGED
    realtime_instance = None
    for sub in lifecycle.cleanup_calls or []:
        if hasattr(sub[1], "__self__") and isinstance(sub[1].__self__, FakeRealtimeUpdater):
            realtime_instance = sub[1].__self__
            break

    # Find the kill switch handler from subscriptions
    for channel, handler in (realtime_instance.subscriptions if realtime_instance else []):
        if channel == "kill-switch":
            await handler({"state": "ENGAGED"})
            break

    # Fill in valid form data
    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert any(
        "Cannot submit: Kill Switch is ENGAGED" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_kill_switch_api_engaged_blocks_confirm(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that kill switch API check returns ENGAGED at confirmation time."""
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    # Initial kill switch is disengaged
    trading_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    # Now change kill switch to ENGAGED for confirmation
    trading_client.fetch_kill_switch_status.return_value = {"state": "ENGAGED"}

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    # Order should NOT have been submitted
    assert trading_client.submit_manual_order.await_count == 0
    assert any(
        "Cannot submit: Kill Switch is ENGAGED" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_kill_switch_unknown_state_blocks(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that unknown kill switch state blocks order at confirmation."""
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    # Initial check returns DISENGAGED so page loads normally
    call_count = 0

    async def mock_fetch_kill_switch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"state": "DISENGAGED"}
        # Return unknown state at confirmation time
        return {"state": "UNKNOWN"}

    trading_client.fetch_kill_switch_status = mock_fetch_kill_switch

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert any(
        "Cannot verify kill switch: unknown state" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_kill_switch_http_error_blocks(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that HTTP error during kill switch check blocks order."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    # First call succeeds (for initial check), subsequent calls fail
    call_count = 0

    async def mock_fetch_kill_switch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"state": "DISENGAGED"}
        response = httpx.Response(500, request=httpx.Request("GET", "http://test"))
        raise httpx.HTTPStatusError("Server error", request=response.request, response=response)

    trading_client.fetch_kill_switch_status = mock_fetch_kill_switch

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert any(
        "Cannot verify kill switch: HTTP 500" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_kill_switch_network_error_blocks(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that network error during kill switch check blocks order."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    call_count = 0

    async def mock_fetch_kill_switch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"state": "DISENGAGED"}
        raise httpx.ConnectError("Connection failed")

    trading_client.fetch_kill_switch_status = mock_fetch_kill_switch

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert any(
        "Cannot verify kill switch: network error" in msg for msg, _ in fake_ui.notifications
    )


# ============================================================================
# Form Validation Tests (Lines 180-215)
# ============================================================================


@pytest.mark.asyncio()
async def test_validation_empty_symbol(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that empty symbol is rejected."""
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
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = ""
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert ("Symbol is required", "warning") in fake_ui.notifications


@pytest.mark.asyncio()
async def test_validation_invalid_quantity(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that quantity less than 1 is rejected."""
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
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 0
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert ("Quantity must be at least 1", "warning") in fake_ui.notifications


@pytest.mark.asyncio()
async def test_validation_fractional_quantity(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that fractional quantity is rejected."""
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
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10.5
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert ("Quantity must be a whole number", "warning") in fake_ui.notifications


@pytest.mark.asyncio()
async def test_validation_short_reason(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that reason shorter than 10 chars is rejected."""
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
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Short"  # Less than 10 chars

    await submit_btn._click_cb()

    assert ("Reason must be at least 10 characters", "warning") in fake_ui.notifications


@pytest.mark.asyncio()
async def test_validation_limit_order_missing_price(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that limit order without price is rejected."""
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
    limit_price_input = _find_element(fake_ui.elements, kind="number", label="Limit Price")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"
    order_type_select.value = "limit"
    limit_price_input.value = None

    await submit_btn._click_cb()

    assert ("Limit price is required for limit orders", "warning") in fake_ui.notifications


@pytest.mark.asyncio()
async def test_validation_limit_order_zero_price(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that limit order with zero price is rejected."""
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
    limit_price_input = _find_element(fake_ui.elements, kind="number", label="Limit Price")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"
    order_type_select.value = "limit"
    limit_price_input.value = 0

    await submit_btn._click_cb()

    assert ("Limit price is required for limit orders", "warning") in fake_ui.notifications


# ============================================================================
# Read-Only Mode Tests (Lines 222-224, 275-280)
# ============================================================================


@pytest.mark.asyncio()
async def test_read_only_mode_blocks_preview(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that read-only mode blocks preview."""
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    monkeypatch.setattr(
        manual_order_module, "app", SimpleNamespace(storage=SimpleNamespace(user={"read_only": True}))
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert ("Read-only mode: connection lost", "warning") in fake_ui.notifications


@pytest.mark.asyncio()
async def test_read_only_mode_blocks_confirm(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that read-only mode blocks confirmation."""
    app_storage = SimpleNamespace(user={})
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    monkeypatch.setattr(
        manual_order_module, "app", SimpleNamespace(storage=app_storage)
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    # Now switch to read-only mode before confirmation
    app_storage.user["read_only"] = True

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert trading_client.submit_manual_order.await_count == 0
    assert ("Read-only mode: connection lost", "warning") in fake_ui.notifications


# ============================================================================
# Order Submission Error Tests (Lines 328-386)
# ============================================================================


@pytest.mark.asyncio()
async def test_order_submit_http_error_with_detail(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test HTTP error during order submission with error detail."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    # Mock response with detail
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"detail": {"message": "Insufficient funds"}}

    trading_client.submit_manual_order = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Bad request",
            request=httpx.Request("POST", "http://test"),
            response=mock_response,
        )
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert any(
        "Order failed: Insufficient funds (HTTP 400)" in msg for msg, _ in fake_ui.notifications
    )
    assert any(log["action"] == "manual_order_failed" for log in audit_log_calls)


@pytest.mark.asyncio()
async def test_order_submit_http_error_with_error_key(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test HTTP error with 'error' key in detail."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.json.return_value = {"detail": {"error": "Permission denied"}}

    trading_client.submit_manual_order = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Forbidden",
            request=httpx.Request("POST", "http://test"),
            response=mock_response,
        )
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert any(
        "Order failed: Permission denied (HTTP 403)" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_order_submit_http_error_without_detail(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test HTTP error without error detail."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {}  # No detail

    trading_client.submit_manual_order = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Server error",
            request=httpx.Request("POST", "http://test"),
            response=mock_response,
        )
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    # Without error detail, should show generic message
    assert any(
        msg == "Order failed: HTTP 500"
        for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_order_submit_http_error_json_parse_failure(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test HTTP error when JSON parsing fails."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    mock_response = MagicMock()
    mock_response.status_code = 502
    mock_response.json.side_effect = ValueError("Invalid JSON")

    trading_client.submit_manual_order = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Bad gateway",
            request=httpx.Request("POST", "http://test"),
            response=mock_response,
        )
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert any("Order failed: HTTP 502" in msg for msg, _ in fake_ui.notifications)


@pytest.mark.asyncio()
async def test_order_submit_network_error(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test network error during order submission."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    trading_client.submit_manual_order = AsyncMock(
        side_effect=httpx.ConnectError("Connection failed")
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert ("Order failed: network error", "negative") in fake_ui.notifications
    assert any(log["action"] == "manual_order_failed" for log in audit_log_calls)


# ============================================================================
# Real-time Kill Switch Update Tests (Lines 400-421)
# ============================================================================


@pytest.mark.asyncio()
async def test_realtime_kill_switch_update_engaged(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test real-time kill switch update to ENGAGED state."""
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    # Find the realtime instance through cleanup callbacks
    realtime_instance = None
    for _, callback in lifecycle.cleanup_calls or []:
        if hasattr(callback, "__self__") and isinstance(callback.__self__, FakeRealtimeUpdater):
            realtime_instance = callback.__self__
            break

    assert realtime_instance is not None
    assert len(realtime_instance.subscriptions) == 1
    channel, handler = realtime_instance.subscriptions[0]
    assert channel == "kill-switch"

    # Simulate update to ENGAGED
    await handler({"state": "ENGAGED"})

    # Now try to submit - should be blocked by cached state
    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert any(
        "Cannot submit: Kill Switch is ENGAGED" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_realtime_kill_switch_update_disengaged(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test real-time kill switch update to DISENGAGED state."""
    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    # Start with ENGAGED state
    trading_client.fetch_kill_switch_status.return_value = {"state": "ENGAGED"}

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    # Find the realtime instance
    realtime_instance = None
    for _, callback in lifecycle.cleanup_calls or []:
        if hasattr(callback, "__self__") and isinstance(callback.__self__, FakeRealtimeUpdater):
            realtime_instance = callback.__self__
            break

    assert realtime_instance is not None
    _, handler = realtime_instance.subscriptions[0]

    # Simulate update to DISENGAGED
    await handler({"state": "DISENGAGED"})

    # Reset kill switch mock for confirmation
    trading_client.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

    # Now submit should work
    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    # Should show preview dialog (no kill switch error)
    assert not any(
        "Kill Switch" in msg for msg, _ in fake_ui.notifications
    )


@pytest.mark.asyncio()
async def test_initial_kill_switch_check_failure(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that initial kill switch check failure sets engaged state."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    # Initial check fails
    trading_client.fetch_kill_switch_status = AsyncMock(
        side_effect=httpx.ConnectError("Connection failed")
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    # Try to submit - should be blocked due to fail-closed behavior
    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    assert any(
        "Cannot submit: Kill Switch is ENGAGED" in msg for msg, _ in fake_ui.notifications
    )


# ============================================================================
# Limit Order Success Test
# ============================================================================


@pytest.mark.asyncio()
async def test_limit_order_success(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test successful limit order submission."""
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
    limit_price_input = _find_element(fake_ui.elements, kind="number", label="Limit Price")
    side_select = _find_element(fake_ui.elements, kind="select", label="Side")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"
    order_type_select.value = "limit"
    limit_price_input.value = 150.50
    side_select.value = "sell"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    assert trading_client.submit_manual_order.await_count == 1
    payload = trading_client.submit_manual_order.call_args.args[0]
    assert payload["symbol"] == "AAPL"
    assert payload["order_type"] == "limit"
    assert payload["limit_price"] == 150.50
    assert payload["side"] == "sell"


# ============================================================================
# Dialog Close Handler Test
# ============================================================================


@pytest.mark.asyncio()
async def test_dialog_close_reenables_button(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that closing dialog re-enables submit button."""
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
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    # Find and click cancel button
    _ = _find_element(fake_ui.elements, kind="button", text="Cancel")
    dialog = _find_element(fake_ui.elements, kind="dialog")

    # Simulate dialog close
    dialog.close()

    # Submit button should be re-enabled
    assert submit_btn.enabled is True


# ============================================================================
# HTTP Error with Non-dict Payload Test
# ============================================================================


@pytest.mark.asyncio()
async def test_order_submit_http_error_non_dict_payload(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test HTTP error with non-dict payload."""
    import httpx

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = "Simple error string"

    trading_client.submit_manual_order = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Bad request",
            request=httpx.Request("POST", "http://test"),
            response=mock_response,
        )
    )

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")
    await confirm_btn._click_cb()

    # With non-dict payload, should fall back to generic error
    assert any("Order failed: HTTP 400" in msg for msg, _ in fake_ui.notifications)


# ============================================================================
# Double-Click Prevention Tests (Lines 219-220, 267-268)
# ============================================================================


@pytest.mark.asyncio()
async def test_double_click_prevention_show_preview(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that double-clicking preview is prevented when submitting is True."""
    import asyncio

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    submit_call_count = 0

    async def slow_submit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal submit_call_count
        submit_call_count += 1
        await asyncio.sleep(0.1)
        return {"client_order_id": "abc123"}

    trading_client.submit_manual_order = slow_submit

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    # First click shows preview
    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")

    # Start confirm (will set submitting=True)
    confirm_task = asyncio.create_task(confirm_btn._click_cb())

    # Give some time for submitting to be set to True
    await asyncio.sleep(0.01)

    # Try to click preview again while confirming - should do nothing due to submitting flag
    # This tests line 219-220 in the source
    await submit_btn._click_cb()

    # Wait for confirm to complete
    await confirm_task

    # Only one order should have been submitted (the double-click on preview was blocked)
    assert submit_call_count == 1


@pytest.mark.asyncio()
async def test_double_click_prevention_confirm(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that double-clicking confirm is prevented."""
    import asyncio

    monkeypatch.setattr(
        manual_order_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    submit_call_count = 0

    async def slow_submit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal submit_call_count
        submit_call_count += 1
        await asyncio.sleep(0.1)
        return {"client_order_id": "abc123"}

    trading_client.submit_manual_order = slow_submit

    await _unwrap_page(manual_order_module.manual_order_page)(client)

    symbol_input = _find_element(fake_ui.elements, kind="input", label="Symbol")
    qty_input = _find_element(fake_ui.elements, kind="number", label="Quantity")
    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    submit_btn = _find_element(fake_ui.elements, kind="button", text="Preview Order")

    symbol_input.value = "AAPL"
    qty_input.value = 10
    reason_input.value = "Valid reason for trade"

    await submit_btn._click_cb()

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Confirm")

    # Start first confirm
    confirm_task1 = asyncio.create_task(confirm_btn._click_cb())

    # Try to click confirm again while first is processing
    # This should be blocked by the submitting flag
    await confirm_btn._click_cb()

    # Wait for first confirm to complete
    await confirm_task1

    # Only one order should have been submitted
    assert submit_call_count == 1
