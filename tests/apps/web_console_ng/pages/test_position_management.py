from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from apps.web_console_ng.auth import middleware as auth_middleware
from apps.web_console_ng.pages import position_management as position_management_module


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
        self._click_cb: Callable[..., Any] | None = None
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

    def set_text(self, text: str) -> None:
        self.text = text

    def update(self) -> None:
        self.updated = True

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._events[event] = handler
        if event == "click":
            self._click_cb = handler

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

    def textarea(self, label: str, **kwargs: Any) -> FakeElement:
        element = FakeElement("textarea", label=label, value=kwargs.get("value"))
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


def _find_element(
    elements: list[FakeElement],
    *,
    kind: str,
    label: str | None = None,
    text: str | None = None,
    text_prefix: str | None = None,
) -> FakeElement:
    for element in elements:
        if element.kind != kind:
            continue
        if label is not None and element.label != label:
            continue
        if text is not None and element.text != text:
            continue
        if text_prefix is not None and (element.text is None or not element.text.startswith(text_prefix)):
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
    monkeypatch.setattr(position_management_module, "ui", ui)
    monkeypatch.setattr(position_management_module, "app", SimpleNamespace(storage=SimpleNamespace(user={})))
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
    client.fetch_positions = AsyncMock(return_value={"positions": []})
    client.fetch_kill_switch_status = AsyncMock(return_value={"state": "DISENGAGED"})
    client.close_position = AsyncMock(return_value={"order_id": "order-1"})
    client.cancel_all_orders = AsyncMock(return_value={"cancelled_count": 0})
    client.flatten_all_positions = AsyncMock(return_value={"positions_closed": 0})
    monkeypatch.setattr(position_management_module.AsyncTradingClient, "get", lambda: client)
    return client


@pytest.fixture()
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> FakeLifecycleManager:
    manager = FakeLifecycleManager()
    monkeypatch.setattr(position_management_module.ClientLifecycleManager, "get", lambda: manager)
    return manager


@pytest.fixture()
def realtime(monkeypatch: pytest.MonkeyPatch) -> type[FakeRealtimeUpdater]:
    monkeypatch.setattr(position_management_module, "RealtimeUpdater", FakeRealtimeUpdater)
    monkeypatch.setattr(position_management_module, "kill_switch_channel", lambda: "kill-switch")
    monkeypatch.setattr(position_management_module, "position_channel", lambda user_id: f"positions:{user_id}")
    return FakeRealtimeUpdater


@pytest.fixture()
def audit_log_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _audit_log(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(position_management_module, "audit_log", _audit_log)
    return calls


@pytest.mark.asyncio()
async def test_check_kill_switch_safety_states(trading_client: MagicMock) -> None:
    trading_client.fetch_kill_switch_status = AsyncMock(return_value={"state": "ENGAGED"})
    result = await position_management_module.check_kill_switch_safety(
        trading_client, "user1", "operator"
    )
    assert result.safe_to_proceed is False
    assert result.kill_switch_engaged is True

    trading_client.fetch_kill_switch_status = AsyncMock(return_value={"state": "UNKNOWN"})
    result = await position_management_module.check_kill_switch_safety(
        trading_client, "user1", "operator"
    )
    assert result.safe_to_proceed is False
    assert result.kill_switch_engaged is True
    assert result.error_message == "Cannot verify kill switch: unknown state"

    trading_client.fetch_kill_switch_status = AsyncMock(return_value={"state": "DISENGAGED"})
    result = await position_management_module.check_kill_switch_safety(
        trading_client, "user1", "operator"
    )
    assert result.safe_to_proceed is True
    assert result.kill_switch_engaged is False

    trading_client.fetch_kill_switch_status = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
    )
    result = await position_management_module.check_kill_switch_safety(
        trading_client, "user1", "operator"
    )
    assert result.safe_to_proceed is False
    assert result.kill_switch_engaged is True
    assert result.error_message == "Cannot verify kill switch status - action blocked"


@pytest.mark.asyncio()
async def test_position_management_redirects_on_missing_user_id(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(position_management_module, "get_current_user", lambda: {})
    client = SimpleNamespace(storage={})

    await _unwrap_page(position_management_module.position_management_page)(client)

    assert fake_ui.notifications == [("Session expired - please log in again", "negative")]
    assert fake_ui.navigations == ["/login"]
    trading_client.fetch_positions.assert_not_called()


@pytest.mark.asyncio()
async def test_position_management_blocks_viewer(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        position_management_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "viewer"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(position_management_module.position_management_page)(client)

    assert fake_ui.notifications == [("Viewers cannot manage positions", "negative")]
    assert fake_ui.navigations == ["/"]
    trading_client.fetch_positions.assert_not_called()


@pytest.mark.asyncio()
async def test_position_management_loads_positions_and_summary(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions = [
        {"symbol": "AAPL", "qty": 10, "market_value": 1000.0, "unrealized_pl": 50.0},
        {"symbol": "MSFT", "qty": 5, "market_value": 500.0, "unrealized_pl": -25.0},
    ]
    trading_client.fetch_positions = AsyncMock(return_value={"positions": positions})
    monkeypatch.setattr(
        position_management_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(position_management_module.position_management_page)(client)

    grid = _find_element(fake_ui.elements, kind="aggrid")
    assert grid.options["rowData"] == positions

    position_label = _find_element(fake_ui.elements, kind="label", text_prefix="Positions:")
    total_label = _find_element(fake_ui.elements, kind="label", text_prefix="Total Value:")
    pnl_label = _find_element(fake_ui.elements, kind="label", text_prefix="Unrealized P&L:")

    assert position_label.text == "Positions: 2"
    assert total_label.text == "Total Value: $1,500.00"
    assert pnl_label.text == "Unrealized P&L: $25.00"

    assert trading_client.fetch_kill_switch_status.await_count == 1


@pytest.mark.asyncio()
async def test_position_management_close_position_success(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    audit_log_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions = [{"symbol": "AAPL", "qty": 10, "market_value": 1000.0, "unrealized_pl": 50.0}]
    trading_client.fetch_positions = AsyncMock(return_value={"positions": positions})
    monkeypatch.setattr(
        position_management_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    monkeypatch.setattr(
        position_management_module,
        "check_kill_switch_safety",
        AsyncMock(
            return_value=position_management_module.KillSwitchCheckResult(
                safe_to_proceed=True,
                kill_switch_engaged=False,
            )
        ),
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(position_management_module.position_management_page)(client)

    grid = _find_element(fake_ui.elements, kind="aggrid")
    grid.selected_rows = [positions[0]]

    close_btn = _find_element(fake_ui.elements, kind="button", text="Close Selected Position")
    await close_btn._click_cb()

    reason_input = _find_element(fake_ui.elements, kind="textarea", label="Reason (required)")
    reason_input.value = "Closing for unit test"

    confirm_btn = _find_element(fake_ui.elements, kind="button", text="Close Position")
    await confirm_btn._click_cb()

    assert trading_client.close_position.await_count == 1
    assert audit_log_calls
    assert audit_log_calls[-1]["action"] == "position_closed"
    assert any("Closing AAPL" in message for message, _ in fake_ui.notifications)
    assert trading_client.fetch_positions.await_count >= 2


@pytest.mark.asyncio()
async def test_position_management_flatten_requires_admin(
    fake_ui: FakeUI,
    trading_client: MagicMock,
    lifecycle: FakeLifecycleManager,
    realtime: type[FakeRealtimeUpdater],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trading_client.fetch_positions = AsyncMock(return_value={"positions": []})
    trading_client.fetch_kill_switch_status = AsyncMock(return_value={"state": "DISENGAGED"})
    monkeypatch.setattr(
        position_management_module,
        "get_current_user",
        lambda: {"user_id": "u1", "role": "operator"},
    )
    client = SimpleNamespace(storage={})

    await _unwrap_page(position_management_module.position_management_page)(client)

    flatten_btn = _find_element(fake_ui.elements, kind="button", text="FLATTEN ALL POSITIONS")
    await flatten_btn._click_cb()

    assert ("Admin permission required to flatten all positions", "negative") in fake_ui.notifications
