from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from apps.web_console_ng.pages import alerts as alerts_module
from libs.platform.alerts.models import AlertEvent, AlertRule, ChannelConfig, ChannelType
from libs.platform.alerts.pii import mask_recipient
from libs.platform.web_console_auth.permissions import Permission


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


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.checkboxes: list[DummyElement] = []
        self.textareas: list[DummyElement] = []
        self.tables: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
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

    def select(
        self,
        label: str | None = None,
        options: list[str] | dict[str, Any] | None = None,
        value: Any = None,
        multiple: bool = False,
    ) -> DummyElement:
        el = DummyElement(
            self, "select", label=label, options=options, value=value, multiple=multiple
        )
        self.selects.append(el)
        return el

    def checkbox(self, label: str | None = None, value: Any = False) -> DummyElement:
        el = DummyElement(self, "checkbox", label=label, value=value)
        self.checkboxes.append(el)
        return el

    def number(
        self, label: str | None = None, value: Any = None, format: str | None = None
    ) -> DummyElement:
        el = DummyElement(self, "number", label=label, value=value, format=format)
        self.inputs.append(el)
        return el

    def textarea(self, label: str | None = None, placeholder: str | None = None) -> DummyElement:
        el = DummyElement(self, "textarea", label=label, placeholder=placeholder)
        self.textareas.append(el)
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

    def expansion(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "expansion")

    def json_editor(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "json_editor")

    def table(self, *, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> DummyElement:
        self.tables.append({"columns": columns, "rows": rows})
        return DummyElement(self, "table")

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def separator(self) -> DummyElement:
        return DummyElement(self, "separator")

    def icon(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "icon")

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: None
        return wrapper


class FakeAlertService:
    def __init__(
        self, rules: list[AlertRule] | None = None, events: list[AlertEvent] | None = None
    ) -> None:
        self.rules = rules or []
        self.events = events or []
        self.created: list[Any] = []
        self.deleted: list[str] = []
        self.acked: list[tuple[str, str]] = []

    async def get_rules(self) -> list[AlertRule]:
        return list(self.rules)

    async def create_rule(self, rule: Any, user: dict[str, Any]) -> None:
        self.created.append((rule, user))

    async def delete_rule(self, rule_id: str, user: dict[str, Any]) -> None:
        self.deleted.append(rule_id)

    async def get_alert_events(self) -> list[AlertEvent]:
        return list(self.events)

    async def acknowledge_alert(self, event_id: str, note: str, user: dict[str, Any]) -> None:
        self.acked.append((event_id, note))


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(alerts_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None, *args: Any) -> None:
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb(*args)
    else:
        cb(*args)


@pytest.mark.asyncio()
async def test_render_alert_rules_create_validations(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertService(rules=[])
    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    name_input = next(i for i in dummy_ui.inputs if i.label == "Rule Name")
    email_input = next(i for i in dummy_ui.inputs if i.label == "Email (optional)")
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Rule")

    name_input.value = "ab"
    await _call(create_button._on_click)
    assert any(
        "Rule name must be at least 3 characters" in n["text"] for n in dummy_ui.notifications
    )

    name_input.value = "Valid Rule"
    email_input.value = ""
    await _call(create_button._on_click)
    assert any("At least one notification channel" in n["text"] for n in dummy_ui.notifications)

    email_input.value = "alerts@example.com"
    await _call(create_button._on_click)
    assert service.created


@pytest.mark.asyncio()
async def test_render_alert_history_acknowledge_flow(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.ACKNOWLEDGE_ALERT

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Drawdown",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    note_input = next(t for t in dummy_ui.textareas if "Acknowledgment Note" in (t.label or ""))
    ack_button = next(b for b in dummy_ui.buttons if b.label == "Acknowledge")

    note_input.value = "too short"
    await _call(ack_button._on_click)
    assert any("Note must be at least" in n["text"] for n in dummy_ui.notifications)

    note_input.value = "Resolved by disabling strategy for the day"
    await _call(ack_button._on_click)
    assert service.acked


@pytest.mark.asyncio()
async def test_render_channels_masks_recipient(dummy_ui: DummyUI) -> None:
    rule = AlertRule(
        id=uuid4(),
        name="Rule",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[
            ChannelConfig(type=ChannelType.EMAIL, recipient="alerts@example.com", enabled=True)
        ],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_channels({"user_id": "u1"}, service)

    masked = mask_recipient("alerts@example.com", "email")
    assert any(masked in label.text for label in dummy_ui.labels)
