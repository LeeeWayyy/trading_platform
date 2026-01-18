from __future__ import annotations

from typing import Any

import pytest

from apps.web_console_ng.pages import data_management as data_module


class DummyElement:
    def __init__(self, *, text: str | None = None, value: Any = None) -> None:
        self.text = text or ""
        self.value = value
        self.visible = True
        self.on_click_cb = None
        self.on_value_change_cb = None

    def __enter__(self) -> "DummyElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *args, **kwargs) -> "DummyElement":
        return self

    def props(self, *args, **kwargs) -> "DummyElement":
        return self

    def set_visibility(self, value: bool) -> None:
        self.visible = value

    def on_click(self, cb) -> None:
        self.on_click_cb = cb

    def on_value_change(self, cb) -> None:
        self.on_value_change_cb = cb

    def clear(self) -> None:
        return None


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.buttons: list[DummyElement] = []
        self.inputs: dict[str, DummyElement] = {}
        self.selects: dict[str, DummyElement] = {}
        self.notifications: list[tuple[str, str | None]] = []

    def tabs(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def tab(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def tab_panels(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def tab_panel(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def card(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def row(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def column(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def label(self, text: str = "", *args, **kwargs) -> DummyElement:
        self.labels.append(text)
        return DummyElement(text=text)

    def select(self, *, label: str = "", options=None, value=None, **kwargs) -> DummyElement:
        element = DummyElement(text=label, value=value)
        if label:
            self.selects[label] = element
        return element

    def input(self, *, label: str = "", value: Any = "", **kwargs) -> DummyElement:
        element = DummyElement(text=label, value=value)
        if label:
            self.inputs[label] = element
        return element

    def textarea(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def button(self, text: str = "", icon: str | None = None, on_click=None, **kwargs) -> DummyElement:
        element = DummyElement(text=text)
        if on_click is not None:
            element.on_click(on_click)
        self.buttons.append(element)
        return element

    def table(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def separator(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def notify(self, message: str, type: str | None = None) -> None:
        self.notifications.append((message, type))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(data_module, "ui", ui)
    return ui


@pytest.mark.asyncio()
async def test_data_sync_requires_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_module, "has_permission", lambda user, perm: False)

    await data_module._render_data_sync_section({"role": "viewer"}, None)

    assert "Permission denied: VIEW_DATA_SYNC required" in dummy_ui.labels


@pytest.mark.asyncio()
async def test_trigger_sync_requires_reason(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def has_permission(user, perm):
        return True

    monkeypatch.setattr(data_module, "has_permission", has_permission)

    await data_module._render_sync_status({"role": "admin"})

    trigger_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Trigger Sync")
    await trigger_btn.on_click_cb()

    assert any("Please provide a reason" in msg for msg, _ in dummy_ui.notifications)

    dummy_ui.inputs["Reason"].value = "Backfill"
    await trigger_btn.on_click_cb()

    assert any("Sync triggered" in msg for msg, _ in dummy_ui.notifications)
    assert dummy_ui.inputs["Reason"].value == ""


@pytest.mark.asyncio()
async def test_data_explorer_run_query_and_export(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def has_permission(user, perm):
        return True

    monkeypatch.setattr(data_module, "has_permission", has_permission)

    await data_module._render_data_explorer_section({"role": "admin"}, None)

    run_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Run Query")
    export_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Export Results")

    await run_btn.on_click_cb()
    await export_btn.on_click_cb()

    assert any("Query executed" in msg for msg, _ in dummy_ui.notifications)
    assert any("Export started" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_data_quality_requires_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(data_module, "has_permission", lambda user, perm: False)

    await data_module._render_data_quality_section({"role": "viewer"}, None)

    assert "Permission denied: VIEW_DATA_QUALITY required" in dummy_ui.labels
