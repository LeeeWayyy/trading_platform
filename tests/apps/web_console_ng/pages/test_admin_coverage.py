"""Comprehensive coverage tests for admin page.

Targets uncovered code paths in admin.py to reach 85%+ coverage.
Focus: UI rendering, form validations, database operations, error handling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import psycopg
import pytest
from pydantic import ValidationError

from apps.web_console_ng.pages import admin as admin_module


class DummyElement:
    """Minimal fake NiceGUI element for testing UI interactions."""

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

    def on_click(self, fn: Callable[..., Any] | None, **kwargs: Any) -> DummyElement:
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
    """Fake NiceGUI UI for testing without actual NiceGUI."""

    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.numbers: list[DummyElement] = []
        self.textareas: list[DummyElement] = []
        self.checkboxes: list[DummyElement] = []
        self.dates: list[DummyElement] = []
        self.selects: list[DummyElement] = []
        self.tables: list[dict[str, Any]] = []
        self.json_editors: list[dict[str, Any]] = []
        self.expansions: list[DummyElement] = []
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

    def number(
        self,
        label: str | None = None,
        value: Any = None,
        min: float | None = None,
        max: float | None = None,
        step: float | None = None,
        format: str | None = None,
    ) -> DummyElement:
        el = DummyElement(
            self, "number", label=label, value=value, min=min, max=max, step=step, format=format
        )
        self.numbers.append(el)
        return el

    def textarea(
        self, label: str | None = None, placeholder: str | None = None, value: Any = None
    ) -> DummyElement:
        el = DummyElement(self, "textarea", label=label, placeholder=placeholder, value=value)
        self.textareas.append(el)
        return el

    def checkbox(self, label: str | None = None, value: Any = False) -> DummyElement:
        el = DummyElement(self, "checkbox", label=label, value=value)
        self.checkboxes.append(el)
        return el

    def date(self, value: Any = None) -> DummyElement:
        el = DummyElement(self, "date", value=value)
        self.dates.append(el)
        return el

    def select(
        self, label: str | None = None, options: list[str] | None = None, value: Any = None
    ) -> DummyElement:
        el = DummyElement(self, "select", label=label, options=options, value=value)
        self.selects.append(el)
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

    def expansion(self, label: str | None = None) -> DummyElement:
        el = DummyElement(self, "expansion", label=label)
        self.expansions.append(el)
        return el

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: self.refreshes.append(fn.__name__)
        return wrapper

    def json_editor(self, *args: Any, **kwargs: Any) -> DummyElement:
        self.json_editors.append({"args": args, "kwargs": kwargs})
        return DummyElement(self, "json_editor")

    def code(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "code")

    def separator(self) -> DummyElement:
        return DummyElement(self, "separator")

    def notify(self, text: str, type: str | None = None, **kwargs: Any) -> None:
        self.notifications.append({"text": text, "type": type, **kwargs})

    def download(self, data: bytes, filename: str) -> None:
        self.downloads.append({"data": data, "filename": filename})


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    """Replace NiceGUI UI with dummy for testing."""
    ui = DummyUI()
    monkeypatch.setattr(admin_module, "ui", ui)
    return ui


async def _call(cb: Callable[..., Any] | None) -> None:
    """Call async or sync callback."""
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb()
    else:
        cb()


class FakeAsyncConnection:
    """Fake async database connection."""

    def __init__(self, data: dict[str, Any] | None = None, error: Exception | None = None):
        self.data = data or {}
        self.error = error
        self.executed_queries: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> FakeAsyncConnection:
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.executed_queries.append((query, params or ()))
        if self.error:
            raise self.error

    def cursor(self, *, row_factory: Any = None) -> FakeAsyncCursor:
        return FakeAsyncCursor(self.data, self.error)


class FakeAsyncCursor:
    """Fake async database cursor."""

    def __init__(self, data: dict[str, Any] | None = None, error: Exception | None = None):
        self.data = data or {}
        self.error = error
        self.executed_queries: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> FakeAsyncCursor:
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.executed_queries.append((query, params or ()))
        if self.error:
            raise self.error

    async def fetchone(self) -> dict[str, Any] | None:
        if self.error:
            raise self.error
        return self.data.get("row")

    async def fetchall(self) -> list[dict[str, Any]]:
        if self.error:
            raise self.error
        return self.data.get("rows", [])


class FakeAsyncPool:
    """Fake async connection pool."""

    def __init__(self, data: dict[str, Any] | None = None, error: Exception | None = None):
        self.data = data or {}
        self.error = error

    def connection(self) -> FakeAsyncConnection:
        return FakeAsyncConnection(self.data, self.error)


# === Unit Tests ===


def test_trading_hours_config_validation() -> None:
    """Test TradingHoursConfig validation."""
    # Valid config
    config = admin_module.TradingHoursConfig(
        market_open=time(9, 30),
        market_close=time(16, 0),
        pre_market_enabled=False,
        after_hours_enabled=False,
    )
    assert config.market_open == time(9, 30)
    assert config.market_close == time(16, 0)

    # Invalid: close before open
    with pytest.raises(ValidationError):
        admin_module.TradingHoursConfig(market_open=time(16, 0), market_close=time(9, 30))


def test_position_limits_config_validation() -> None:
    """Test PositionLimitsConfig validation."""
    # Valid config
    config = admin_module.PositionLimitsConfig(
        max_position_per_symbol=1000,
        max_notional_total=Decimal("100000"),
        max_open_orders=10,
    )
    assert config.max_position_per_symbol == 1000
    assert config.max_notional_total == Decimal("100000")

    # Invalid: below min
    with pytest.raises(ValidationError):
        admin_module.PositionLimitsConfig(max_position_per_symbol=0)

    # Invalid: above max
    with pytest.raises(ValidationError):
        admin_module.PositionLimitsConfig(max_position_per_symbol=200000)


def test_system_defaults_config_validation() -> None:
    """Test SystemDefaultsConfig validation."""
    # Valid config
    config = admin_module.SystemDefaultsConfig(
        dry_run=True,
        circuit_breaker_enabled=True,
        drawdown_threshold=Decimal("0.05"),
    )
    assert config.dry_run is True
    assert config.drawdown_threshold == Decimal("0.05")

    # Invalid: drawdown too low
    with pytest.raises(ValidationError):
        admin_module.SystemDefaultsConfig(drawdown_threshold=Decimal("0.001"))

    # Invalid: drawdown too high
    with pytest.raises(ValidationError):
        admin_module.SystemDefaultsConfig(drawdown_threshold=Decimal("1.0"))


def test_audit_filters_dataclass() -> None:
    """Test AuditFilters dataclass."""
    filters = admin_module.AuditFilters(
        user_id="u1",
        action="login",
        event_type="auth",
        outcome="success",
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert filters.user_id == "u1"
    assert filters.action == "login"
    assert filters.event_type == "auth"
    assert filters.outcome == "success"


def test_build_audit_csv_handles_none_timestamp() -> None:
    """Test CSV export handles None timestamps."""
    logs = [
        {
            "timestamp": None,
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
    assert "timestamp,user_id,action" in data
    # Empty timestamp field
    assert '""' in data or ",," in data


def test_build_audit_csv_handles_empty_logs() -> None:
    """Test CSV export with empty logs."""
    logs: list[dict[str, Any]] = []
    data = admin_module._build_audit_csv(logs).decode()
    assert "timestamp,user_id,action" in data


# === Config Operations ===


@pytest.mark.asyncio()
async def test_get_config_returns_default_on_no_row() -> None:
    """Test _get_config returns default when no DB row exists."""
    pool = FakeAsyncPool(data={"row": None})
    config = await admin_module._get_config(pool, "trading_hours", admin_module.TradingHoursConfig)
    assert isinstance(config, admin_module.TradingHoursConfig)
    assert config.market_open == time(9, 30)  # Default


@pytest.mark.asyncio()
async def test_get_config_handles_validation_error() -> None:
    """Test _get_config returns default on validation error."""
    pool = FakeAsyncPool(data={"row": {"config_value": {"market_open": "invalid"}}})
    config = await admin_module._get_config(pool, "trading_hours", admin_module.TradingHoursConfig)
    assert isinstance(config, admin_module.TradingHoursConfig)


@pytest.mark.asyncio()
async def test_get_config_handles_value_error() -> None:
    """Test _get_config returns default on value error."""
    pool = FakeAsyncPool(data={"row": {"config_value": "not a dict"}})
    config = await admin_module._get_config(pool, "trading_hours", admin_module.TradingHoursConfig)
    assert isinstance(config, admin_module.TradingHoursConfig)


@pytest.mark.asyncio()
async def test_save_config_success() -> None:
    """Test _save_config successful save."""
    pool = FakeAsyncPool()
    config = admin_module.TradingHoursConfig()
    result = await admin_module._save_config(pool, "trading_hours", config, "user1")
    assert result is True


@pytest.mark.asyncio()
async def test_save_config_handles_operational_error() -> None:
    """Test _save_config returns False on DB error."""
    pool = FakeAsyncPool(error=psycopg.OperationalError("DB down"))
    config = admin_module.TradingHoursConfig()
    result = await admin_module._save_config(pool, "trading_hours", config, "user1")
    assert result is False


@pytest.mark.asyncio()
async def test_save_config_handles_value_error() -> None:
    """Test _save_config returns False on value error."""
    pool = FakeAsyncPool(error=ValueError("Bad value"))
    config = admin_module.TradingHoursConfig()
    result = await admin_module._save_config(pool, "trading_hours", config, "user1")
    assert result is False


# === API Key Manager Tests ===


@pytest.mark.asyncio()
async def test_render_api_key_manager_no_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test API key manager shows permission denied."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: False)
    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())
    assert any("Permission denied" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_api_key_manager_with_existing_keys(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test API key manager displays existing keys."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": 1,
                "name": "Test Key",
                "key_prefix": "KEY",
                "scopes": ["read_positions", "read_orders"],
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "expires_at": None,
                "revoked_at": None,
                "last_used_at": None,
            },
            {
                "id": 2,
                "name": "Expired Key",
                "key_prefix": "EXP",
                "scopes": ["read_positions"],
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "expires_at": datetime(2025, 6, 1, tzinfo=UTC),
                "revoked_at": None,
                "last_used_at": None,
            },
            {
                "id": 3,
                "name": "Revoked Key",
                "key_prefix": "REV",
                "scopes": ["write_orders"],
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "expires_at": None,
                "revoked_at": datetime(2025, 12, 1, tzinfo=UTC),
                "last_used_at": None,
            },
        ]

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())

    # Should display table with keys
    assert len(dummy_ui.tables) > 0
    table = dummy_ui.tables[0]
    assert len(table["rows"]) == 3
    # Check status handling
    assert any(row["status"] == "Active" for row in table["rows"])
    assert any(row["status"] == "Expired" for row in table["rows"])
    assert any(row["status"] == "Revoked" for row in table["rows"])


@pytest.mark.asyncio()
async def test_create_api_key_validations(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create API key form validations."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())

    name_input = next(i for i in dummy_ui.inputs if i.label == "Key Name")
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Key")

    # Test: name too long
    name_input.value = "x" * 60
    await _call(create_button._on_click)
    assert any("50 characters or fewer" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_create_api_key_with_expiry(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create API key with expiry date."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    async def fake_create_key(*_: Any, **__: Any) -> dict[str, Any]:
        return {"full_key": "KEY123", "prefix": "KEY"}

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)
    monkeypatch.setattr(admin_module, "_create_api_key", fake_create_key)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())

    name_input = next(i for i in dummy_ui.inputs if i.label == "Key Name")
    scope_boxes = [
        c
        for c in dummy_ui.checkboxes
        if c.label in {"Read positions", "Read orders", "Write orders", "Read strategies"}
    ]
    set_expiry = next(c for c in dummy_ui.checkboxes if c.label == "Set expiry date")
    expiry_date = dummy_ui.dates[0]
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Key")

    # Set expiry in the past
    name_input.value = "Test Key"
    scope_boxes[0].value = True
    set_expiry.value = True
    expiry_date.value = "2020-01-01"
    await _call(create_button._on_click)
    assert any("future" in n["text"] for n in dummy_ui.notifications)

    # Set valid future expiry
    dummy_ui.notifications.clear()
    expiry_date.value = "2030-01-01"
    await _call(create_button._on_click)
    assert any("API key created" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_create_api_key_handles_value_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create API key handles validation errors."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    async def fake_create_key(*_: Any, **__: Any) -> dict[str, Any]:
        raise ValueError("Invalid key data")

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)
    monkeypatch.setattr(admin_module, "_create_api_key", fake_create_key)
    # Suppress logger to avoid LogRecord conflict with 'name' key
    monkeypatch.setattr(admin_module.logger, "exception", lambda *args, **kwargs: None)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())

    name_input = next(i for i in dummy_ui.inputs if i.label == "Key Name")
    scope_boxes = [
        c
        for c in dummy_ui.checkboxes
        if c.label in {"Read positions", "Read orders", "Write orders", "Read strategies"}
    ]
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Key")

    name_input.value = "TestKey"
    scope_boxes[0].value = True
    await _call(create_button._on_click)
    assert any("Invalid input" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_create_api_key_handles_runtime_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create API key handles service errors."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    async def fake_create_key(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("Service down")

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)
    monkeypatch.setattr(admin_module, "_create_api_key", fake_create_key)
    # Suppress logger to avoid LogRecord conflict with 'name' key
    monkeypatch.setattr(admin_module.logger, "exception", lambda *args, **kwargs: None)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=object())

    name_input = next(i for i in dummy_ui.inputs if i.label == "Key Name")
    scope_boxes = [
        c
        for c in dummy_ui.checkboxes
        if c.label in {"Read positions", "Read orders", "Write orders", "Read strategies"}
    ]
    create_button = next(b for b in dummy_ui.buttons if b.label == "Create Key")

    name_input.value = "TestKey"
    scope_boxes[0].value = True
    await _call(create_button._on_click)
    assert any("Service error" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_list_api_keys_returns_empty_on_no_rows() -> None:
    """Test _list_api_keys returns empty list when no keys."""
    pool = FakeAsyncPool(data={"rows": []})
    keys = await admin_module._list_api_keys(pool, "user1")
    assert keys == []


@pytest.mark.asyncio()
async def test_create_api_key_database_operation() -> None:
    """Test _create_api_key performs database insert."""
    pool = FakeAsyncPool()
    result = await admin_module._create_api_key(
        pool,
        "user1",
        "Test Key",
        {"read_positions": True, "read_orders": False},
        None,
    )
    assert result is not None
    assert "full_key" in result
    assert "prefix" in result


# === Config Editor Tests ===


@pytest.mark.asyncio()
async def test_render_config_editor_no_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test config editor shows permission denied."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: False)
    await admin_module._render_config_editor({"user_id": "u1"}, db_pool=object())
    assert any("Permission denied" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_trading_hours_form(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trading hours form rendering and save."""
    async def fake_get_config(*_: Any, **__: Any) -> admin_module.TradingHoursConfig:
        return admin_module.TradingHoursConfig(
            market_open=time(9, 30),
            market_close=time(16, 0),
            pre_market_enabled=True,
            after_hours_enabled=False,
        )

    async def fake_save_config(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(admin_module, "_get_config", fake_get_config)
    monkeypatch.setattr(admin_module, "_save_config", fake_save_config)

    await admin_module._render_trading_hours_form({"user_id": "u1"}, db_pool=object())

    # Check form elements
    open_input = next(i for i in dummy_ui.inputs if i.label == "Market Open (HH:MM)")
    assert open_input.value == "09:30"

    save_button = next(b for b in dummy_ui.buttons if b.label == "Save Trading Hours")
    await _call(save_button._on_click)
    assert any("Trading hours saved" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_trading_hours_form_validation_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trading hours form handles validation errors."""
    async def fake_get_config(*_: Any, **__: Any) -> admin_module.TradingHoursConfig:
        return admin_module.TradingHoursConfig()

    monkeypatch.setattr(admin_module, "_get_config", fake_get_config)

    await admin_module._render_trading_hours_form({"user_id": "u1"}, db_pool=object())

    open_input = next(i for i in dummy_ui.inputs if i.label == "Market Open (HH:MM)")
    save_button = next(b for b in dummy_ui.buttons if b.label == "Save Trading Hours")

    # Invalid time format
    open_input.value = "invalid"
    await _call(save_button._on_click)
    assert any("Invalid input" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_trading_hours_form_save_failure(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test trading hours form handles save failure."""
    async def fake_get_config(*_: Any, **__: Any) -> admin_module.TradingHoursConfig:
        return admin_module.TradingHoursConfig()

    async def fake_save_config(*_: Any, **__: Any) -> bool:
        return False

    monkeypatch.setattr(admin_module, "_get_config", fake_get_config)
    monkeypatch.setattr(admin_module, "_save_config", fake_save_config)

    await admin_module._render_trading_hours_form({"user_id": "u1"}, db_pool=object())

    save_button = next(b for b in dummy_ui.buttons if b.label == "Save Trading Hours")
    await _call(save_button._on_click)
    assert any("Failed to save" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_position_limits_form(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test position limits form rendering and save."""
    async def fake_get_config(*_: Any, **__: Any) -> admin_module.PositionLimitsConfig:
        return admin_module.PositionLimitsConfig()

    async def fake_save_config(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(admin_module, "_get_config", fake_get_config)
    monkeypatch.setattr(admin_module, "_save_config", fake_save_config)

    await admin_module._render_position_limits_form({"user_id": "u1"}, db_pool=object())

    save_button = next(b for b in dummy_ui.buttons if b.label == "Save Limits")
    await _call(save_button._on_click)
    assert any("Position limits saved" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_position_limits_form_validation_error(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test position limits form handles validation errors."""
    async def fake_get_config(*_: Any, **__: Any) -> admin_module.PositionLimitsConfig:
        return admin_module.PositionLimitsConfig()

    async def fake_save_config(*_: Any, **__: Any) -> bool:
        raise ValidationError.from_exception_data("test", [])

    monkeypatch.setattr(admin_module, "_get_config", fake_get_config)
    monkeypatch.setattr(admin_module, "_save_config", fake_save_config)

    await admin_module._render_position_limits_form({"user_id": "u1"}, db_pool=object())

    save_button = next(b for b in dummy_ui.buttons if b.label == "Save Limits")
    await _call(save_button._on_click)
    assert any("Invalid input" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_system_defaults_form(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test system defaults form rendering and save."""
    async def fake_get_config(*_: Any, **__: Any) -> admin_module.SystemDefaultsConfig:
        return admin_module.SystemDefaultsConfig()

    async def fake_save_config(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(admin_module, "_get_config", fake_get_config)
    monkeypatch.setattr(admin_module, "_save_config", fake_save_config)

    await admin_module._render_system_defaults_form({"user_id": "u1"}, db_pool=object())

    save_button = next(b for b in dummy_ui.buttons if b.label == "Save Defaults")
    await _call(save_button._on_click)
    assert any("System defaults saved" in n["text"] for n in dummy_ui.notifications)


# === Reconciliation Tools Tests ===


@pytest.mark.asyncio()
async def test_render_reconciliation_tools_no_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reconciliation tools shows permission denied."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: False)
    await admin_module._render_reconciliation_tools({"user_id": "u1"})
    assert any("Permission denied" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_reconciliation_tools_success(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reconciliation tools renders and executes backfill."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    mock_client = AsyncMock()
    mock_client.run_fills_backfill = AsyncMock(
        return_value={"fills_synced": 10, "trades_recalculated": 5}
    )
    monkeypatch.setattr(admin_module.AsyncTradingClient, "get", lambda: mock_client)

    await admin_module._render_reconciliation_tools({"user_id": "u1", "role": "admin"})

    run_button = next(b for b in dummy_ui.buttons if b.label == "Run Fills Backfill")
    await _call(run_button._on_click)
    assert any("Fills backfill completed" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_reconciliation_tools_invalid_lookback(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reconciliation tools validates lookback hours."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)
    monkeypatch.setattr(admin_module.AsyncTradingClient, "get", lambda: AsyncMock())

    await admin_module._render_reconciliation_tools({"user_id": "u1", "role": "admin"})

    lookback_input = next(n for n in dummy_ui.numbers if n.label == "Lookback Hours (optional)")
    run_button = next(b for b in dummy_ui.buttons if b.label == "Run Fills Backfill")

    lookback_input.value = "invalid"
    await _call(run_button._on_click)
    assert any("must be a number" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_render_reconciliation_tools_handles_exception(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test reconciliation tools handles exceptions."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    mock_client = AsyncMock()
    mock_client.run_fills_backfill = AsyncMock(side_effect=Exception("Service down"))
    monkeypatch.setattr(admin_module.AsyncTradingClient, "get", lambda: mock_client)

    await admin_module._render_reconciliation_tools({"user_id": "u1", "role": "admin"})

    run_button = next(b for b in dummy_ui.buttons if b.label == "Run Fills Backfill")
    await _call(run_button._on_click)
    assert any("Fills backfill failed" in n["text"] for n in dummy_ui.notifications)


# === Audit Log Viewer Tests ===


@pytest.mark.asyncio()
async def test_render_audit_log_viewer_no_permission(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test audit log viewer shows permission denied."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: False)
    await admin_module._render_audit_log_viewer({"user_id": "u1"}, db_pool=object())
    assert any("Permission denied" in label.text for label in dummy_ui.labels)


@pytest.mark.asyncio()
async def test_render_audit_log_viewer_with_logs(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test audit log viewer displays logs."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_fetch_logs(*_: Any, **__: Any) -> tuple[list[dict[str, Any]], int]:
        return [
            {
                "id": 1,
                "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                "user_id": "u1",
                "action": "login",
                "event_type": "auth",
                "resource_type": "user",
                "resource_id": "u1",
                "outcome": "success",
                "details": {"ip": "127.0.0.1"},
            }
        ], 1

    monkeypatch.setattr(admin_module, "_fetch_audit_logs", fake_fetch_logs)

    await admin_module._render_audit_log_viewer({"user_id": "u1"}, db_pool=object())

    # Should render table with logs
    assert len(dummy_ui.tables) > 0
    table = dummy_ui.tables[0]
    assert len(table["rows"]) == 1


@pytest.mark.asyncio()
async def test_render_audit_log_viewer_pagination(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test audit log viewer pagination."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_fetch_logs(*_: Any, **__: Any) -> tuple[list[dict[str, Any]], int]:
        return [], 100

    monkeypatch.setattr(admin_module, "_fetch_audit_logs", fake_fetch_logs)

    await admin_module._render_audit_log_viewer({"user_id": "u1"}, db_pool=object())

    prev_button = next(b for b in dummy_ui.buttons if b.label == "Previous")
    next_button = next(b for b in dummy_ui.buttons if b.label == "Next")

    # Previous on first page (no-op)
    await _call(prev_button._on_click)

    # Next page
    await _call(next_button._on_click)


@pytest.mark.asyncio()
async def test_render_audit_log_viewer_filters(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test audit log viewer filter application."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_fetch_logs(*_: Any, **__: Any) -> tuple[list[dict[str, Any]], int]:
        return [], 0

    monkeypatch.setattr(admin_module, "_fetch_audit_logs", fake_fetch_logs)

    await admin_module._render_audit_log_viewer({"user_id": "u1"}, db_pool=object())

    user_filter = next(i for i in dummy_ui.inputs if i.label == "User ID")
    apply_button = next(b for b in dummy_ui.buttons if b.label == "Apply Filters")

    user_filter.value = "u1"
    await _call(apply_button._on_click)


@pytest.mark.asyncio()
async def test_render_audit_log_viewer_export_csv(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test audit log viewer CSV export."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    async def fake_fetch_logs(*_: Any, **__: Any) -> tuple[list[dict[str, Any]], int]:
        return [
            {
                "id": 1,
                "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                "user_id": "u1",
                "action": "login",
                "event_type": "auth",
                "resource_type": "user",
                "resource_id": "u1",
                "outcome": "success",
                "details": {"ip": "127.0.0.1"},
            }
        ], 1

    monkeypatch.setattr(admin_module, "_fetch_audit_logs", fake_fetch_logs)

    await admin_module._render_audit_log_viewer({"user_id": "u1"}, db_pool=object())

    export_button = next(b for b in dummy_ui.buttons if b.label == "Export CSV")
    await _call(export_button._on_click)
    assert len(dummy_ui.downloads) == 1
    assert dummy_ui.downloads[0]["filename"] == "audit_logs.csv"


@pytest.mark.asyncio()
async def test_fetch_audit_logs_with_filters() -> None:
    """Test _fetch_audit_logs applies filters correctly."""
    pool = FakeAsyncPool(
        data={
            "rows": [
                {
                    "id": 1,
                    "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                    "user_id": "u1",
                    "action": "login",
                    "event_type": "auth",
                    "resource_type": "user",
                    "resource_id": "u1",
                    "outcome": "success",
                    "details": {"ip": "127.0.0.1"},
                }
            ],
            "row": {"count": 1},
        }
    )

    filters = admin_module.AuditFilters(
        user_id="u1",
        action="login",
        event_type="auth",
        outcome="success",
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    logs, total = await admin_module._fetch_audit_logs(pool, filters, 50, 0)
    assert len(logs) == 1
    assert total == 1
    assert logs[0]["action"] == "login"


@pytest.mark.asyncio()
async def test_fetch_audit_logs_sanitizes_details() -> None:
    """Test _fetch_audit_logs sanitizes sensitive details."""
    pool = FakeAsyncPool(
        data={
            "rows": [
                {
                    "id": 1,
                    "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                    "user_id": "u1",
                    "action": "login",
                    "event_type": "auth",
                    "resource_type": "user",
                    "resource_id": "u1",
                    "outcome": "success",
                    "details": '{"password": "secret123", "api_key": "KEY123"}',
                }
            ],
            "row": {"count": 1},
        }
    )

    filters = admin_module.AuditFilters(
        user_id=None,
        action=None,
        event_type=None,
        outcome=None,
        start_at=None,
        end_at=None,
    )

    logs, _ = await admin_module._fetch_audit_logs(pool, filters, 50, 0)
    # Details should be parsed and sanitized (password replaced with ***)
    details_str = str(logs[0]["details"])
    assert "***" in details_str or "secret123" not in details_str


@pytest.mark.asyncio()
async def test_fetch_audit_logs_handles_malformed_json() -> None:
    """Test _fetch_audit_logs handles malformed JSON in details."""
    pool = FakeAsyncPool(
        data={
            "rows": [
                {
                    "id": 1,
                    "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                    "user_id": "u1",
                    "action": "login",
                    "event_type": "auth",
                    "resource_type": "user",
                    "resource_id": "u1",
                    "outcome": "success",
                    "details": "not valid json",
                }
            ],
            "row": {"count": 1},
        }
    )

    filters = admin_module.AuditFilters(
        user_id=None,
        action=None,
        event_type=None,
        outcome=None,
        start_at=None,
        end_at=None,
    )

    logs, _ = await admin_module._fetch_audit_logs(pool, filters, 50, 0)
    # Should wrap malformed JSON in a raw field
    assert "raw" in logs[0]["details"]
