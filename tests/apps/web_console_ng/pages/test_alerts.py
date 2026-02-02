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


# ============================================================================
# Tests for _get_alert_service function (lines 58-66)
# ============================================================================


class FakeStorage:
    """Fake storage for simulating app.storage."""

    pass


class FakeApp:
    """Fake app with storage attribute."""

    def __init__(self) -> None:
        self.storage = FakeStorage()


@pytest.mark.asyncio()
async def test_get_alert_service_creates_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_alert_service creates service if not cached."""
    fake_app = FakeApp()
    monkeypatch.setattr(alerts_module, "app", fake_app)

    # Mock the db pool
    mock_pool = object()

    # Call the function
    service = alerts_module._get_alert_service(mock_pool)  # type: ignore[arg-type]

    # Verify service was created and cached
    assert service is not None
    assert hasattr(fake_app.storage, "_alert_service")


@pytest.mark.asyncio()
async def test_get_alert_service_returns_cached_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_alert_service returns cached service."""
    fake_app = FakeApp()
    cached_service = FakeAlertService()
    fake_app.storage._alert_service = cached_service  # type: ignore[attr-defined]
    monkeypatch.setattr(alerts_module, "app", fake_app)

    mock_pool = object()
    service = alerts_module._get_alert_service(mock_pool)  # type: ignore[arg-type]

    assert service is cached_service


# ============================================================================
# Tests for alerts_page function (lines 74-112)
# ============================================================================


@pytest.mark.asyncio()
async def test_alerts_page_feature_disabled(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test alerts page when feature is disabled."""
    monkeypatch.setattr(alerts_module.config, "FEATURE_ALERTS", False)
    monkeypatch.setattr(alerts_module, "get_current_user", lambda: {"user_id": "u1"})

    # Get the underlying function without decorators
    # The alerts_page is wrapped by decorators, so we need to access the actual function
    page_fn = alerts_module.alerts_page
    # Unwrap decorators to get the actual coroutine function
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    assert any("Alert Configuration feature is disabled" in lbl.text for lbl in dummy_ui.labels)
    assert any("FEATURE_ALERTS=true" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_alerts_page_permission_denied(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test alerts page when user lacks VIEW_ALERTS permission."""
    monkeypatch.setattr(alerts_module.config, "FEATURE_ALERTS", True)
    monkeypatch.setattr(alerts_module, "get_current_user", lambda: {"user_id": "u1"})
    monkeypatch.setattr(alerts_module, "has_permission", lambda user, perm: False)

    page_fn = alerts_module.alerts_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    assert any("Permission denied: VIEW_ALERTS required" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_alerts_page_database_not_configured(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test alerts page when database pool is None."""
    monkeypatch.setattr(alerts_module.config, "FEATURE_ALERTS", True)
    monkeypatch.setattr(alerts_module, "get_current_user", lambda: {"user_id": "u1"})
    monkeypatch.setattr(alerts_module, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(alerts_module, "get_db_pool", lambda: None)

    page_fn = alerts_module.alerts_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    assert any("Database not configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_alerts_page_full_flow(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test alerts page full rendering with tabs."""
    monkeypatch.setattr(alerts_module.config, "FEATURE_ALERTS", True)
    monkeypatch.setattr(alerts_module, "get_current_user", lambda: {"user_id": "u1"})
    monkeypatch.setattr(alerts_module, "has_permission", lambda user, perm: True)

    # Mock database pool
    mock_pool = object()
    monkeypatch.setattr(alerts_module, "get_db_pool", lambda: mock_pool)

    # Mock the alert service
    service = FakeAlertService(rules=[], events=[])
    monkeypatch.setattr(alerts_module, "_get_alert_service", lambda pool: service)

    page_fn = alerts_module.alerts_page
    while hasattr(page_fn, "__wrapped__"):
        page_fn = page_fn.__wrapped__

    await page_fn()

    # Verify page title is displayed
    assert any("Alert Configuration" in lbl.text for lbl in dummy_ui.labels)


# ============================================================================
# Tests for _render_alert_rules error handling (lines 123-134, 150-217)
# ============================================================================


class FakeAlertServiceWithErrors(FakeAlertService):
    """Alert service that raises errors."""

    def __init__(
        self,
        rules: list[AlertRule] | None = None,
        events: list[AlertEvent] | None = None,
        rules_error: Exception | None = None,
        delete_error: Exception | None = None,
        create_error: Exception | None = None,
        events_error: Exception | None = None,
        ack_error: Exception | None = None,
    ) -> None:
        super().__init__(rules=rules, events=events)
        self.rules_error = rules_error
        self.delete_error = delete_error
        self.create_error = create_error
        self.events_error = events_error
        self.ack_error = ack_error

    async def get_rules(self) -> list[AlertRule]:
        if self.rules_error:
            raise self.rules_error
        return list(self.rules)

    async def delete_rule(self, rule_id: str, user: dict[str, Any]) -> None:
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(rule_id)

    async def create_rule(self, rule: Any, user: dict[str, Any]) -> None:
        if self.create_error:
            raise self.create_error
        self.created.append((rule, user))

    async def get_alert_events(self) -> list[AlertEvent]:
        if self.events_error:
            raise self.events_error
        return list(self.events)

    async def acknowledge_alert(self, event_id: str, note: str, user: dict[str, Any]) -> None:
        if self.ack_error:
            raise self.ack_error
        self.acked.append((event_id, note))


@pytest.mark.asyncio()
async def test_render_alert_rules_db_error_on_fetch(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_rules handles psycopg.OperationalError on fetch."""
    import psycopg

    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(rules_error=psycopg.OperationalError("DB connection lost"))

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    # Should still render, just with empty rules
    assert any("No alert rules configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_rules_validation_error_on_fetch(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_rules handles ValueError on fetch."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(rules_error=ValueError("Invalid rule data"))

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    # Should still render, just with empty rules
    assert any("No alert rules configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_rules_shows_existing_rules(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_rules displays existing rules with channels."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    rule = AlertRule(
        id=uuid4(),
        name="High Drawdown Alert",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[
            ChannelConfig(type=ChannelType.EMAIL, recipient="alerts@example.com", enabled=True),
            ChannelConfig(
                type=ChannelType.SLACK, recipient="https://hooks.slack.com/xxx", enabled=False
            ),
        ],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    # Verify rules are displayed (should see rule name in expansion)
    assert any("Existing Rules" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_rules_delete_permission_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test delete rule handles PermissionError."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.DELETE_ALERT_RULE

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    rule = AlertRule(
        id=uuid4(),
        name="Test Rule",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="test@test.com", enabled=True)],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertServiceWithErrors(
        rules=[rule], delete_error=PermissionError("Not authorized")
    )

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    # Find and click delete button
    delete_button = next((b for b in dummy_ui.buttons if b.label == "Delete"), None)
    assert delete_button is not None

    await _call(delete_button._on_click)
    assert any("Permission denied" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_rules_delete_db_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test delete rule handles psycopg.OperationalError."""
    import psycopg

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.DELETE_ALERT_RULE

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    rule = AlertRule(
        id=uuid4(),
        name="Test Rule",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="test@test.com", enabled=True)],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertServiceWithErrors(
        rules=[rule], delete_error=psycopg.OperationalError("Connection lost")
    )

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    delete_button = next((b for b in dummy_ui.buttons if b.label == "Delete"), None)
    assert delete_button is not None

    await _call(delete_button._on_click)
    assert any("Database error" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_rules_delete_validation_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test delete rule handles ValueError."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.DELETE_ALERT_RULE

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    rule = AlertRule(
        id=uuid4(),
        name="Test Rule",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="test@test.com", enabled=True)],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertServiceWithErrors(rules=[rule], delete_error=ValueError("Invalid rule ID"))

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    delete_button = next((b for b in dummy_ui.buttons if b.label == "Delete"), None)
    assert delete_button is not None

    await _call(delete_button._on_click)
    assert any("Invalid rule ID" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_rules_delete_success(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test delete rule success flow."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.DELETE_ALERT_RULE

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    rule = AlertRule(
        id=uuid4(),
        name="Test Rule",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="test@test.com", enabled=True)],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    delete_button = next((b for b in dummy_ui.buttons if b.label == "Delete"), None)
    assert delete_button is not None

    await _call(delete_button._on_click)
    assert any("Rule deleted" in n["text"] for n in dummy_ui.notifications)
    assert len(service.deleted) == 1


@pytest.mark.asyncio()
async def test_render_alert_rules_update_permission_shows_message(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that update permission shows edit message."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.UPDATE_ALERT_RULE

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    rule = AlertRule(
        id=uuid4(),
        name="Test Rule",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="test@test.com", enabled=True)],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    # Verify edit message is shown
    assert any("Edit functionality available" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_rules_no_create_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that without CREATE_ALERT_RULE permission, message is shown."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    service = FakeAlertService(rules=[])

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    assert any("do not have permission to create rules" in lbl.text for lbl in dummy_ui.labels)


# ============================================================================
# Tests for rule creation error handling (lines 314-327)
# ============================================================================


@pytest.mark.asyncio()
async def test_render_alert_rules_create_permission_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create rule handles PermissionError."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(
        rules=[], create_error=PermissionError("Not authorized to create")
    )

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    name_input = next(i for i in dummy_ui.inputs if i.label == "Rule Name")
    email_input = next(i for i in dummy_ui.inputs if i.label == "Email (optional)")
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Rule")

    name_input.value = "Valid Rule Name"
    email_input.value = "test@example.com"

    await _call(create_button._on_click)
    assert any("Permission denied" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_rules_create_db_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create rule handles psycopg.OperationalError."""
    import psycopg

    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(
        rules=[], create_error=psycopg.OperationalError("Connection lost")
    )

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    name_input = next(i for i in dummy_ui.inputs if i.label == "Rule Name")
    email_input = next(i for i in dummy_ui.inputs if i.label == "Email (optional)")
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Rule")

    name_input.value = "Valid Rule Name"
    email_input.value = "test@example.com"

    await _call(create_button._on_click)
    assert any("Database error" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_rules_create_validation_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create rule handles ValueError."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(rules=[], create_error=ValueError("Invalid threshold"))

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    name_input = next(i for i in dummy_ui.inputs if i.label == "Rule Name")
    email_input = next(i for i in dummy_ui.inputs if i.label == "Email (optional)")
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Rule")

    name_input.value = "Valid Rule Name"
    email_input.value = "test@example.com"

    await _call(create_button._on_click)
    assert any("Invalid input" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_rules_create_with_slack_channel(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create rule with slack channel."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertService(rules=[])

    await alerts_module._render_alert_rules({"user_id": "u1"}, service)

    name_input = next(i for i in dummy_ui.inputs if i.label == "Rule Name")
    slack_input = next(i for i in dummy_ui.inputs if i.label == "Slack Webhook (optional)")
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Rule")

    name_input.value = "Slack Alert Rule"
    slack_input.value = "https://hooks.slack.com/services/T00/B00/XXX"

    await _call(create_button._on_click)
    assert len(service.created) == 1


# ============================================================================
# Tests for _render_alert_history (lines 342-353, 364-365, 395->exit, 401-402, 424-469)
# ============================================================================


@pytest.mark.asyncio()
async def test_render_alert_history_db_error_on_fetch(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history handles psycopg.OperationalError on fetch."""
    import psycopg

    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(
        events_error=psycopg.OperationalError("DB connection lost")
    )

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    # Should still render, just with no events
    assert any("No alert events recorded" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_history_validation_error_on_fetch(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history handles ValueError on fetch."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: True)

    service = FakeAlertServiceWithErrors(events_error=ValueError("Invalid event data"))

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    # Should still render, just with no events
    assert any("No alert events recorded" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_history_displays_acknowledged_event(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history displays acknowledged events correctly."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    # Event that has been acknowledged
    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Position Limit",
        triggered_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        acknowledged_at=datetime(2026, 1, 1, 12, 30, 0, tzinfo=UTC),
        acknowledged_by="admin",
        acknowledgment_note="Resolved",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    # Check that table was created with correct data
    assert len(dummy_ui.tables) == 1
    table_data = dummy_ui.tables[0]
    assert len(table_data["rows"]) == 1
    assert table_data["rows"][0]["status"] == "Acknowledged"


class AlertEventWithExtras(AlertEvent):
    """AlertEvent with optional severity and message fields for testing."""

    severity: str | None = None
    message: str | None = None


@pytest.mark.asyncio()
async def test_render_alert_history_displays_event_with_severity_and_message(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history displays event severity and message."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    event = AlertEventWithExtras(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="High Latency",
        triggered_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        severity="high",
        message="Latency exceeded 500ms threshold for API endpoint",
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    assert len(dummy_ui.tables) == 1
    table_data = dummy_ui.tables[0]
    assert table_data["rows"][0]["severity"] == "high"
    assert "Latency exceeded" in table_data["rows"][0]["message"]


@pytest.mark.asyncio()
async def test_render_alert_history_truncates_long_message(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history truncates messages longer than 50 chars."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    long_message = "A" * 60  # 60 character message
    event = AlertEventWithExtras(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test Alert",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        message=long_message,
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    table_data = dummy_ui.tables[0]
    assert table_data["rows"][0]["message"].endswith("...")
    assert len(table_data["rows"][0]["message"]) == 53  # 50 chars + "..."


@pytest.mark.asyncio()
async def test_render_alert_history_no_pending_alerts(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history shows message when no pending alerts."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.ACKNOWLEDGE_ALERT

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    # All events acknowledged
    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        acknowledged_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    assert any("No pending alerts to acknowledge" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_alert_history_acknowledge_no_selection(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test acknowledge flow when no event is selected."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.ACKNOWLEDGE_ALERT

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    # Clear the select value
    event_select = next(s for s in dummy_ui.selects if s.label == "Select Alert")
    event_select.value = None

    ack_button = next(b for b in dummy_ui.buttons if b.label == "Acknowledge")
    await _call(ack_button._on_click)

    assert any("Select an alert to acknowledge" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_history_acknowledge_permission_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test acknowledge handles PermissionError."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.ACKNOWLEDGE_ALERT

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertServiceWithErrors(
        events=[event], ack_error=PermissionError("Not authorized")
    )

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    note_input = next(t for t in dummy_ui.textareas if "Acknowledgment Note" in (t.label or ""))
    note_input.value = "This is a sufficiently long acknowledgment note"

    ack_button = next(b for b in dummy_ui.buttons if b.label == "Acknowledge")
    await _call(ack_button._on_click)

    assert any("Permission denied" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_history_acknowledge_db_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test acknowledge handles psycopg.OperationalError."""
    import psycopg

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.ACKNOWLEDGE_ALERT

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertServiceWithErrors(
        events=[event], ack_error=psycopg.OperationalError("Connection lost")
    )

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    note_input = next(t for t in dummy_ui.textareas if "Acknowledgment Note" in (t.label or ""))
    note_input.value = "This is a sufficiently long acknowledgment note"

    ack_button = next(b for b in dummy_ui.buttons if b.label == "Acknowledge")
    await _call(ack_button._on_click)

    assert any("Database error" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_alert_history_acknowledge_validation_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test acknowledge handles ValueError."""

    def permission_check(_user: dict[str, Any], perm: Permission) -> bool:
        return perm == Permission.ACKNOWLEDGE_ALERT

    monkeypatch.setattr(alerts_module, "has_permission", permission_check)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test",
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertServiceWithErrors(events=[event], ack_error=ValueError("Invalid event ID"))

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    note_input = next(t for t in dummy_ui.textareas if "Acknowledgment Note" in (t.label or ""))
    note_input.value = "This is a sufficiently long acknowledgment note"

    ack_button = next(b for b in dummy_ui.buttons if b.label == "Acknowledge")
    await _call(ack_button._on_click)

    assert any("Invalid input" in n["text"] for n in dummy_ui.notifications)


# ============================================================================
# Tests for _render_channels (lines 482-488, 497-498, 505-506)
# ============================================================================


@pytest.mark.asyncio()
async def test_render_channels_db_error(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _render_channels handles psycopg.OperationalError."""
    import psycopg

    service = FakeAlertServiceWithErrors(rules_error=psycopg.OperationalError("DB error"))

    await alerts_module._render_channels({"user_id": "u1"}, service)

    # Should show "No rules configured" since rules_data is empty
    assert any("No rules configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_channels_validation_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_channels handles ValueError."""
    service = FakeAlertServiceWithErrors(rules_error=ValueError("Invalid data"))

    await alerts_module._render_channels({"user_id": "u1"}, service)

    # Should show "No rules configured" since rules_data is empty
    assert any("No rules configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_channels_no_rules(dummy_ui: DummyUI) -> None:
    """Test _render_channels with empty rules."""
    service = FakeAlertService(rules=[])

    await alerts_module._render_channels({"user_id": "u1"}, service)

    assert any("No rules configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_channels_rule_with_no_channels(dummy_ui: DummyUI) -> None:
    """Test _render_channels with rule that has no channels."""
    rule = AlertRule(
        id=uuid4(),
        name="Rule Without Channels",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[],  # No channels
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_channels({"user_id": "u1"}, service)

    assert any("No channels configured" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_channels_disabled_channel(dummy_ui: DummyUI) -> None:
    """Test _render_channels displays disabled channel correctly."""
    rule = AlertRule(
        id=uuid4(),
        name="Rule With Disabled Channel",
        condition_type="drawdown",
        threshold_value=0.05,
        comparison=">=",
        channels=[ChannelConfig(type=ChannelType.EMAIL, recipient="test@test.com", enabled=False)],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_channels({"user_id": "u1"}, service)

    # Should show "disabled" label
    assert any("disabled" in lbl.text for lbl in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_channels_multiple_channels(dummy_ui: DummyUI) -> None:
    """Test _render_channels with multiple channels on a rule."""
    rule = AlertRule(
        id=uuid4(),
        name="Multi-Channel Rule",
        condition_type="latency",
        threshold_value=0.5,
        comparison=">=",
        channels=[
            ChannelConfig(type=ChannelType.EMAIL, recipient="alert@test.com", enabled=True),
            ChannelConfig(
                type=ChannelType.SLACK, recipient="https://hooks.slack.com/xxx", enabled=True
            ),
        ],
        enabled=True,
        created_by="u1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(rules=[rule])

    await alerts_module._render_channels({"user_id": "u1"}, service)

    # Should show both EMAIL: and SLACK: labels
    email_labels = [lbl for lbl in dummy_ui.labels if "EMAIL:" in lbl.text]
    slack_labels = [lbl for lbl in dummy_ui.labels if "SLACK:" in lbl.text]
    assert len(email_labels) == 1
    assert len(slack_labels) == 1


# ============================================================================
# Tests for event with missing/None fields (edge cases)
# ============================================================================


@pytest.mark.asyncio()
async def test_render_alert_history_event_with_none_rule_name(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history handles event with None rule_name."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name=None,  # None rule_name (allowed in model)
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    table_data = dummy_ui.tables[0]
    assert table_data["rows"][0]["rule_name"] == "-"


@pytest.mark.asyncio()
async def test_render_alert_history_event_timestamp_displayed(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _render_alert_history displays event timestamp correctly."""
    monkeypatch.setattr(alerts_module, "has_permission", lambda *_: False)

    event = AlertEvent(
        id=uuid4(),
        rule_id=uuid4(),
        rule_name="Test",
        triggered_at=datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC),
        created_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
    service = FakeAlertService(events=[event])

    await alerts_module._render_alert_history({"user_id": "u1"}, service)

    table_data = dummy_ui.tables[0]
    # Verify timestamp is an ISO format string
    assert "2026-01-15" in table_data["rows"][0]["timestamp"]
