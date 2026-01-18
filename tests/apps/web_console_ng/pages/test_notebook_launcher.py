from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console_ng.pages import notebook_launcher as notebook_module
from libs.web_console_services.notebook_launcher_service import (
    NotebookParameter,
    NotebookTemplate,
    SessionStatus,
)


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
        self.links: list[tuple[str, str]] = []
        self.buttons: list[DummyElement] = []
        self.inputs: dict[str, DummyElement] = {}
        self.selects: dict[str, DummyElement] = {}
        self.notifications: list[tuple[str, str | None]] = []
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

    def number(self, *, label: str = "", value: Any = 0, **kwargs) -> DummyElement:
        element = DummyElement(text=label, value=value)
        if label:
            self.inputs[label] = element
        return element

    def button(self, text: str = "", icon: str | None = None, on_click=None, **kwargs) -> DummyElement:
        element = DummyElement(text=text)
        if on_click is not None:
            element.on_click(on_click)
        self.buttons.append(element)
        return element

    def link(self, target: str, text: str, new_tab: bool = False) -> DummyElement:
        self.links.append((target, text))
        return DummyElement(text=text)

    def notify(self, message: str, type: str | None = None) -> None:
        self.notifications.append((message, type))

    def separator(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def icon(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def markdown(self, *args, **kwargs) -> DummyElement:
        return DummyElement()

    def table(self, *args, **kwargs) -> DummyElement:
        return DummyElement()


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(notebook_module, "ui", ui)
    return ui


@pytest.mark.asyncio()
async def test_get_redis_session_store_handles_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyRedis:
        def get(self, key: str):
            return b"not-json"

    monkeypatch.setattr(notebook_module, "get_sync_redis_client", lambda: DummyRedis())

    result = notebook_module._get_redis_session_store("user-1")
    assert result == {}


def test_save_redis_session_store_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = MagicMock()
    monkeypatch.setattr(notebook_module, "get_sync_redis_client", lambda: redis_client)

    notebook_module._save_redis_session_store("user-1", {"s": 1})

    redis_client.setex.assert_called_once()
    args, _ = redis_client.setex.call_args
    assert args[0] == "notebook_session:user-1"
    assert args[1] == notebook_module._NOTEBOOK_SESSION_TTL
    assert json.loads(args[2])["s"] == 1


@pytest.mark.asyncio()
async def test_render_notebook_launcher_launches_session(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = [
        NotebookTemplate(
            template_id="tmpl-1",
            name="Alpha Research",
            description="Demo",
            parameters=(
                NotebookParameter(
                    key="strategy_id",
                    label="Strategy ID",
                    kind="text",
                    default="momentum_v1",
                ),
            ),
        )
    ]

    session = SimpleNamespace(
        status=SessionStatus.RUNNING,
        error_message=None,
        access_url="http://example.local/nb",
    )

    service = SimpleNamespace(create_notebook=MagicMock(return_value=session))

    async def io_bound(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(notebook_module.run, "io_bound", io_bound)
    monkeypatch.setattr(notebook_module, "_save_redis_session_store", MagicMock())
    monkeypatch.setattr(notebook_module, "_render_active_sessions", AsyncMock())

    await notebook_module._render_notebook_launcher(
        service, templates, "user-1", session_store={}
    )

    assert "Strategy ID" in dummy_ui.inputs
    dummy_ui.inputs["Strategy ID"].value = "alpha_v2"

    launch_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Launch Notebook")
    assert launch_btn.on_click_cb is not None
    await launch_btn.on_click_cb()

    service.create_notebook.assert_called_once_with("tmpl-1", {"strategy_id": "alpha_v2"})
    assert any("Notebook session started successfully" in label for label in dummy_ui.labels)
    assert ("http://example.local/nb", "http://example.local/nb") in dummy_ui.links


@pytest.mark.asyncio()
async def test_render_notebook_launcher_requires_template_selection(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = [
        NotebookTemplate(
            template_id="tmpl-1",
            name="Alpha Research",
            description="Demo",
            parameters=(),
        )
    ]

    service = SimpleNamespace(create_notebook=MagicMock())

    async def io_bound(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(notebook_module.run, "io_bound", io_bound)
    monkeypatch.setattr(notebook_module, "_render_active_sessions", AsyncMock())

    await notebook_module._render_notebook_launcher(
        service, templates, "user-1", session_store={}
    )

    dummy_ui.selects["Notebook Template"].value = None

    launch_btn = next(btn for btn in dummy_ui.buttons if btn.text == "Launch Notebook")
    await launch_btn.on_click_cb()

    assert any("Please select a template" in msg for msg, _ in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_active_sessions_terminate_calls_service(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    sessions = [
        SimpleNamespace(
            session_id="sess-123456",
            template_id="tmpl-1",
            status=SessionStatus.RUNNING,
            created_at=now,
            access_url="http://example",
        )
    ]

    service = SimpleNamespace(
        list_sessions=MagicMock(return_value=sessions),
        terminate_session=MagicMock(),
    )

    async def io_bound(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(notebook_module.run, "io_bound", io_bound)
    monkeypatch.setattr(notebook_module, "_save_redis_session_store", MagicMock())

    await notebook_module._render_active_sessions(service, "user-1", session_store={})

    terminate_btn = next(btn for btn in dummy_ui.buttons if btn.text.startswith("Terminate"))
    await terminate_btn.on_click_cb()

    service.terminate_session.assert_called_once_with("sess-123456")
    assert any("terminated" in msg for msg, _ in dummy_ui.notifications)
