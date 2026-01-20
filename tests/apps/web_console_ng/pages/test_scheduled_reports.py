from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng.pages import scheduled_reports as reports_module


class DummyElement:
    def __init__(self, *, text: str | None = None, value: Any = None) -> None:
        self.text = text or ""
        self.value = value
        self.visible = True
        self.on_click_cb = None
        self.on_value_change_cb = None

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def classes(self, *args, **kwargs) -> DummyElement:
        return self

    def props(self, *args, **kwargs) -> DummyElement:
        return self

    def set_visibility(self, value: bool) -> None:
        self.visible = value

    def set_text(self, value: str) -> None:
        self.text = value

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
        self.textareas: dict[str, DummyElement] = {}
        self.selects: dict[str, DummyElement] = {}
        self.notifications: list[tuple[str, str | None]] = []
        self.downloads: list[tuple[bytes, str]] = []
        self.navigate = SimpleNamespace(to=MagicMock())

    def refreshable(self, func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        def refresh(*args, **kwargs):
            return None

        wrapper.refresh = refresh
        return wrapper

    def card(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def row(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def column(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def grid(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def expansion(self, *args, **kwargs) -> DummyElement:
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

    def textarea(self, *, label: str = "", value: Any = "", **kwargs) -> DummyElement:
        element = DummyElement(text=label, value=value)
        if label:
            self.textareas[label] = element
        return element

    def switch(self, *args, **kwargs) -> DummyElement:
        return DummyElement(value=kwargs.get("value", True))

    def button(
        self, text: str = "", icon: str | None = None, on_click=None, **kwargs
    ) -> DummyElement:
        element = DummyElement(text=text)
        if on_click is not None:
            element.on_click(on_click)
        self.buttons.append(element)
        return element

    def separator(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def download(self, content: bytes, filename: str) -> None:
        self.downloads.append((content, filename))

    def notify(self, message: str, type: str | None = None) -> None:
        self.notifications.append((message, type))

    def table(self, *args, **kwargs) -> DummyElement:
        return DummyElement()


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(reports_module, "ui", ui)
    return ui


@pytest.mark.asyncio()
async def test_schedule_form_rejects_invalid_json(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={}, schedule=None)

    dummy_ui.inputs["Schedule Name"].value = "Daily Report"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{bad-json"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    assert any("Invalid JSON" in msg for msg, _ in dummy_ui.notifications)
    service.create_schedule.assert_not_called()


@pytest.mark.asyncio()
async def test_schedule_form_requires_name(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={}, schedule=None)

    dummy_ui.inputs["Schedule Name"].value = ""
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    assert any("Schedule name is required" in msg for msg, _ in dummy_ui.notifications)
    service.create_schedule.assert_not_called()


@pytest.mark.asyncio()
async def test_schedule_form_creates_schedule(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SimpleNamespace(create_schedule=AsyncMock())

    await reports_module._render_schedule_form(service, user={"user_id": "u1"}, schedule=None)

    dummy_ui.inputs["Schedule Name"].value = "Daily Alpha"
    dummy_ui.textareas["Report Parameters (JSON)"].value = "{}"

    submit_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Create Schedule")
    await submit_btn.on_click_cb()

    service.create_schedule.assert_awaited_once()
    dummy_ui.navigate.to.assert_called_once_with("/reports")


@pytest.mark.asyncio()
async def test_run_history_download_triggers_download(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = SimpleNamespace(
        id="run-1",
        run_key="20260103-060000",
        status="completed",
        started_at=datetime(2026, 1, 3, 6, 0, 0),
        completed_at=datetime(2026, 1, 3, 6, 0, 45),
        error_message=None,
        format="pdf",
    )

    service = SimpleNamespace(
        get_run_history=AsyncMock(return_value=[run]),
        download_archive=AsyncMock(return_value="/tmp/report.pdf"),
    )

    async def io_bound(func, *args, **kwargs):
        return b"pdf-bytes"

    monkeypatch.setattr(reports_module.run, "io_bound", io_bound)

    await reports_module._render_run_history(service, schedule_id="sched-1")

    download_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Download"))
    await download_btn.on_click_cb()

    assert dummy_ui.downloads
    content, filename = dummy_ui.downloads[0]
    assert content == b"pdf-bytes"
    assert filename.endswith(".pdf")
    assert "20260103-060000" in filename
