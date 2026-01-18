from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from apps.web_console_ng.pages import admin as admin_module


class DummyElement:
    def __init__(self, ui: DummyUI, kind: str, **kwargs: Any) -> None:
        self.ui = ui
        self.kind = kind
        self.kwargs = kwargs
        self.value = kwargs.get("value")
        self.label = kwargs.get("label")
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

    def set_text(self, value: str) -> None:
        self.text = value

    def refresh(self) -> None:
        self.ui.refreshes.append(self.kind)

    def clear(self) -> None:
        self.ui.clears.append(self.kind)

    def open(self) -> None:
        self.ui.opens.append(self.kind)

    def close(self) -> None:
        self.ui.opens.append(f"{self.kind}:closed")


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.checkboxes: list[DummyElement] = []
        self.dates: list[DummyElement] = []
        self.tables: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.refreshes: list[str] = []
        self.clears: list[str] = []
        self.opens: list[str] = []
        self.downloads: list[dict[str, Any]] = []
        self.context = SimpleNamespace(client=SimpleNamespace(storage={}))

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(self, "label", text=text)
        self.labels.append(el)
        return el

    def button(
        self, label: str, on_click: Callable[..., Any] | None = None, color: str | None = None
    ) -> DummyElement:
        el = DummyElement(self, "button", label=label, color=color)
        el.on_click(on_click)
        self.buttons.append(el)
        return el

    def input(
        self, label: str | None = None, placeholder: str | None = None, value: Any = None
    ) -> DummyElement:
        el = DummyElement(self, "input", label=label, placeholder=placeholder, value=value)
        self.inputs.append(el)
        return el

    def checkbox(self, label: str | None = None, value: Any = False) -> DummyElement:
        el = DummyElement(self, "checkbox", label=label, value=value)
        self.checkboxes.append(el)
        return el

    def date(self, value: Any = None) -> DummyElement:
        el = DummyElement(self, "date", value=value)
        self.dates.append(el)
        return el

    def table(self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement(self, "table")

    def dialog(self) -> DummyElement:
        return DummyElement(self, "dialog")

    def card(self) -> DummyElement:
        return DummyElement(self, "card")

    def row(self) -> DummyElement:
        return DummyElement(self, "row")

    def tabs(self) -> DummyElement:
        return DummyElement(self, "tabs")

    def tab(self, label: str) -> DummyElement:
        return DummyElement(self, "tab", label=label)

    def tab_panels(self, *args: Any, **kwargs: Any) -> DummyElement:
        return DummyElement(self, "tab_panels")

    def tab_panel(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "tab_panel")

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: self.refreshes.append(fn.__name__)
        return wrapper

    def json_editor(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "json_editor")

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def download(self, data: bytes, filename: str) -> None:
        self.downloads.append({"data": data, "filename": filename})

    def separator(self) -> DummyElement:
        return DummyElement(self, "separator")

    def code(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "code")


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(admin_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None) -> None:
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb()
    else:
        cb()


def test_get_user_identifier_fallback() -> None:
    assert admin_module._get_user_identifier({"user_id": "123"}) == "123"
    assert admin_module._get_user_identifier({"username": "alice"}) == "alice"
    assert admin_module._get_user_identifier({}) == "unknown"


def test_build_audit_csv_encodes_details() -> None:
    logs = [
        {
            "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
            "user_id": "u1",
            "action": "login",
            "event_type": "auth",
            "resource_type": "user",
            "resource_id": "u1",
            "outcome": "success",
            "details": {"ip": "127.0.0.1"},
        }
    ]
    data = admin_module._build_audit_csv(logs).decode()
    assert "timestamp,user_id,action,event_type,resource_type,resource_id,outcome,details" in data
    assert "127.0.0.1" in data


@pytest.mark.asyncio()
async def test_get_config_returns_default_on_db_error() -> None:
    class BrokenPool:
        def connection(self) -> Any:
            class Conn:
                async def __aenter__(self) -> Any:
                    raise psycopg.OperationalError("boom")

                async def __aexit__(self, exc_type, exc, tb) -> bool:
                    return False

            return Conn()

    config = await admin_module._get_config(
        BrokenPool(), "trading_hours", admin_module.TradingHoursConfig
    )
    assert isinstance(config, admin_module.TradingHoursConfig)


@pytest.mark.asyncio()
async def test_render_api_key_manager_validations(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)

    async def fake_create_key(*_: Any, **__: Any) -> dict[str, Any]:
        return {"full_key": "KEY123", "prefix": "KEY"}

    monkeypatch.setattr(admin_module, "_create_api_key", fake_create_key)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())

    name_input = next(i for i in dummy_ui.inputs if i.label == "Key Name")
    scope_boxes = [
        c
        for c in dummy_ui.checkboxes
        if c.label in {"Read positions", "Read orders", "Write orders", "Read strategies"}
    ]
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Key")

    name_input.value = "ab"
    await _call(create_button._on_click)
    assert any("Name must be at least 3 characters" in n["text"] for n in dummy_ui.notifications)

    name_input.value = "Valid Key"
    for box in scope_boxes:
        box.value = False
    await _call(create_button._on_click)
    assert any("Select at least one scope" in n["text"] for n in dummy_ui.notifications)

    scope_boxes[0].value = True
    await _call(create_button._on_click)
    assert any("API key created" in n["text"] for n in dummy_ui.notifications)
    assert "keys_list" in dummy_ui.refreshes
