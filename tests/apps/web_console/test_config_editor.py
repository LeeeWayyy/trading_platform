"""Tests for config editor component."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from apps.web_console.components import config_editor
from apps.web_console.components.config_editor import (
    PositionLimitsConfig,
    SystemDefaultsConfig,
    TradingHoursConfig,
)
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Role


class _FakeCursor:
    def __init__(self, row: Any):
        self.row = row

    async def fetchone(self) -> Any:
        return self.row


class _FakeConn:
    def __init__(self, row: Any = None) -> None:
        self.row = row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.executed.append((query, params))
        return _FakeCursor(self.row)

    async def fetchone(self, query: str, params: tuple[Any, ...]) -> Any:
        self.executed.append((query, params))
        return self.row


class _FakeAuditLogger:
    def __init__(self) -> None:
        self.logged: list[dict[str, Any]] = []

    async def log_action(self, **kwargs: Any) -> None:
        self.logged.append(kwargs)


class _FakeAsyncCM:
    """Async context manager wrapper for fake connections."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def __aenter__(self) -> Any:
        return self.conn

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeStreamlit:
    """Minimal Streamlit stub for config editor testing."""

    def __init__(self) -> None:
        self.session_state: dict[str, Any] = {}
        self.time_inputs: dict[str, Any] = {}
        self.checkbox_values: dict[str, bool] = {}
        self.number_inputs: dict[str, float | int] = {}
        self.text_inputs: dict[str, str] = {}
        self.form_submit_results: list[bool] = []
        self.tabs_labels: list[str] = []
        self.errors: list[str] = []
        self.successes: list[str] = []
        self.toasts: list[str] = []
        self.titles: list[str] = []

    # UI primitives
    def title(self, msg: str, **_kwargs: Any) -> None:
        self.titles.append(msg)

    def caption(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def tabs(self, labels) -> list[_FakeStreamlit]:
        self.tabs_labels = list(labels)
        return [self for _ in labels]

    def form(self, _name: str):
        return self

    def __enter__(self) -> _FakeStreamlit:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def time_input(self, label: str, value: Any = None, **_kwargs: Any) -> Any:
        return self.time_inputs.get(label, value)

    def checkbox(self, label: str, value: bool = False, **_kwargs: Any) -> bool:
        return self.checkbox_values.get(label, value)

    def number_input(self, label: str, value: float | int, **_kwargs: Any) -> float | int:
        return self.number_inputs.get(label, value)

    def text_input(self, label: str, value: str = "", **_kwargs: Any) -> str:
        return self.text_inputs.get(label, value)

    def form_submit_button(self, _label: str, **_kwargs: Any) -> bool:
        if self.form_submit_results:
            return self.form_submit_results.pop(0)
        return False

    def success(self, msg: str, **_kwargs: Any) -> None:
        self.successes.append(msg)

    def error(self, msg: str, **_kwargs: Any) -> None:
        self.errors.append(msg)

    def toast(self, msg: str, **_kwargs: Any) -> None:
        self.toasts.append(msg)


@pytest.fixture()
def admin_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="admin",
        role=Role.ADMIN,
        strategies=[],
        session_version=1,
        request_id="req-1",
    )


def _install_streamlit_stub(monkeypatch: pytest.MonkeyPatch) -> _FakeStreamlit:
    stub = _FakeStreamlit()
    monkeypatch.setattr(config_editor, "st", stub)
    monkeypatch.setattr(config_editor.generate_csrf_token.__module__ + ".st", stub)
    monkeypatch.setattr(config_editor.verify_csrf_token.__module__ + ".st", stub)
    return stub


def test_trading_hours_validation_success() -> None:
    config = TradingHoursConfig(market_open=time(9, 30), market_close=time(16, 0))
    assert config.market_close > config.market_open


def test_trading_hours_validation_failure() -> None:
    with pytest.raises(ValidationError):
        TradingHoursConfig(market_open=time(10, 0), market_close=time(9, 59))


def test_position_limits_range_validation() -> None:
    cfg = PositionLimitsConfig()
    assert cfg.max_position_per_symbol == 1000
    with pytest.raises(ValidationError):
        PositionLimitsConfig(max_position_per_symbol=0)


def test_system_defaults_validation() -> None:
    cfg = SystemDefaultsConfig(drawdown_threshold=Decimal("0.10"))
    assert cfg.drawdown_threshold == Decimal("0.10")
    with pytest.raises(ValidationError):
        SystemDefaultsConfig(drawdown_threshold=Decimal("0.90"))


@pytest.mark.asyncio()
async def test_get_config_defaults_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_conn = _FakeConn(row=None)
    fake_pool = MagicMock(connection=None)
    monkeypatch.setattr(config_editor, "acquire_connection", lambda _: _FakeAsyncCM(fake_conn))

    result = await config_editor.get_config(
        config_editor.CONFIG_KEY_TRADING_HOURS, fake_pool, None, TradingHoursConfig
    )
    assert isinstance(result, TradingHoursConfig)


@pytest.mark.asyncio()
async def test_save_config_persists_and_audits(
    monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser
) -> None:
    fake_conn = _FakeConn()
    fake_pool = MagicMock(connection=None)
    fake_audit = _FakeAuditLogger()
    monkeypatch.setattr(config_editor, "acquire_connection", lambda _: _FakeAsyncCM(fake_conn))

    saved = await config_editor.save_config(
        config_key=config_editor.CONFIG_KEY_SYSTEM_DEFAULTS,
        config_value=SystemDefaultsConfig(),
        config_type="system_defaults",
        user=admin_user,
        db_pool=fake_pool,
        audit_logger=fake_audit,
        redis_client=None,
    )

    assert saved is True
    assert fake_conn.executed
    assert fake_audit.logged[0]["resource_id"] == config_editor.CONFIG_KEY_SYSTEM_DEFAULTS


@pytest.mark.asyncio()
async def test_cache_invalidation_on_save(
    monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser
) -> None:
    fake_conn = _FakeConn()
    fake_pool = MagicMock(connection=None)
    fake_audit = _FakeAuditLogger()
    redis_client = AsyncMock()
    monkeypatch.setattr(config_editor, "acquire_connection", lambda _: _FakeAsyncCM(fake_conn))

    await config_editor.save_config(
        config_key=config_editor.CONFIG_KEY_POSITION_LIMITS,
        config_value=PositionLimitsConfig(),
        config_type="position_limits",
        user=admin_user,
        db_pool=fake_pool,
        audit_logger=fake_audit,
        redis_client=redis_client,
    )

    redis_client.delete.assert_awaited_with("system_config:position_limits")


def test_rbac_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _install_streamlit_stub(monkeypatch)
    non_admin = AuthenticatedUser(
        user_id="viewer", role=Role.VIEWER, strategies=[], session_version=1, request_id="req-2"
    )
    config_editor.render_config_editor(non_admin, None, _FakeAuditLogger(), None)
    assert "Permission denied" in stub.errors[0]
