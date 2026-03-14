"""Tests for the admin_users page (P6T16.2 — User Management / RBAC)."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

import apps.web_console_ng.pages.admin_users as mod

# ---------------------------------------------------------------------------
# Dummy UI stubs (matches test_admin.py pattern)
# ---------------------------------------------------------------------------


class DummyElement:
    def __init__(self, ui: DummyUI, kind: str, **kwargs: Any) -> None:
        self.ui = ui
        self.kind = kind
        self.kwargs = kwargs
        self.value = kwargs.get("value")
        self.label = kwargs.get("label")
        self.text = kwargs.get("text", "")
        self._on_click: Callable[..., Any] | None = None

    def __enter__(self) -> DummyElement:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    def classes(self, *_: Any, **__: Any) -> DummyElement:
        return self

    def props(self, *_: Any, **__: Any) -> DummyElement:
        return self

    def on_click(self, fn: Callable[..., Any] | None) -> DummyElement:
        self._on_click = fn
        return self

    def refresh(self) -> None:
        self.ui.refreshes.append(self.kind)

    def open(self) -> None:
        self.ui.opens.append(self.kind)

    def close(self) -> None:
        self.ui.opens.append(f"{self.kind}:closed")


class DummyUI:
    def __init__(self) -> None:
        self.labels: list[DummyElement] = []
        self.buttons: list[DummyElement] = []
        self.inputs: list[DummyElement] = []
        self.notifications: list[dict[str, Any]] = []
        self.refreshes: list[str] = []
        self.opens: list[str] = []

    def label(self, text: str = "") -> DummyElement:
        el = DummyElement(self, "label", text=text)
        self.labels.append(el)
        return el

    def button(
        self,
        label: str = "",
        on_click: Callable[..., Any] | None = None,
        color: str | None = None,
        icon: str | None = None,
    ) -> DummyElement:
        el = DummyElement(self, "button", label=label, color=color)
        el.on_click(on_click)
        self.buttons.append(el)
        return el

    def input(self, label: str | None = None, **kwargs: Any) -> DummyElement:
        el = DummyElement(self, "input", label=label, **kwargs)
        self.inputs.append(el)
        return el

    def row(self) -> DummyElement:
        return DummyElement(self, "row")

    def card(self) -> DummyElement:
        return DummyElement(self, "card")

    def dialog(self) -> DummyElement:
        return DummyElement(self, "dialog")

    def separator(self) -> DummyElement:
        return DummyElement(self, "separator")

    def notify(self, text: str, type: str | None = None) -> None:
        self.notifications.append({"text": text, "type": type})

    def refreshable(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.refresh = lambda: self.refreshes.append(fn.__name__)  # type: ignore[attr-defined]
        return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str = "admin1", role: str = "admin") -> dict[str, Any]:
    """Return a dict matching get_current_user() output."""
    return {"user_id": user_id, "role": role}


def _make_user_info(
    user_id: str = "u1", role: str = "viewer", strategy_count: int = 0
) -> SimpleNamespace:
    """Return a SimpleNamespace mimicking UserInfo from user_management."""
    return SimpleNamespace(
        user_id=user_id,
        role=role,
        session_version=1,
        updated_at="2026-01-01T00:00:00Z",
        updated_by=None,
        strategy_count=strategy_count,
    )


_raw_page = inspect.unwrap(mod.admin_users_page)


class FakeAuditLogger:
    """Captures audit log calls without hitting DB."""

    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    async def log_action(self, **kwargs: Any) -> None:
        self.actions.append(kwargs)

    async def log_admin_change(self, **kwargs: Any) -> None:
        self.actions.append(kwargs)


async def _call(cb: Callable[..., Any] | None) -> None:
    """Invoke a sync or async callback."""
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb()
    else:
        cb()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    ui = DummyUI()
    monkeypatch.setattr(mod, "ui", ui)
    return ui


@pytest.fixture()
def fake_audit(monkeypatch: pytest.MonkeyPatch) -> FakeAuditLogger:
    audit = FakeAuditLogger()
    monkeypatch.setattr(mod, "AuditLogger", lambda _pool: audit)
    return audit


@pytest.fixture()
def _stub_components(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out all component render functions used by the page."""
    monkeypatch.setattr(mod, "render_user_table", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "render_role_change_dialog", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "render_strategy_grants_dialog", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "render_user_activity_log", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _stub_verify_db_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub verify_db_role to return True by default (tests use object() as db_pool)."""

    async def _always_pass(*_a: Any, **_kw: Any) -> bool:
        return True

    monkeypatch.setattr(mod, "verify_db_role", _always_pass)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_permission_denied_non_admin(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-admin user sees 'Admin access required' and page returns early."""
    monkeypatch.setattr(mod, "get_current_user", lambda: _make_user("viewer1", "viewer"))
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: False)

    await _raw_page()

    label_texts = [el.text for el in dummy_ui.labels]
    assert any("Admin access required" in t for t in label_texts)
    # No further UI elements should have been created (early return)
    assert len(dummy_ui.buttons) == 0


@pytest.mark.asyncio()
async def test_db_unavailable(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """When get_db_pool returns None, page shows 'Database unavailable'."""
    monkeypatch.setattr(mod, "get_current_user", lambda: _make_user())
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: None)

    await _raw_page()

    label_texts = [el.text for el in dummy_ui.labels]
    assert any("Database unavailable" in t for t in label_texts)
    assert len(dummy_ui.buttons) == 0


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_page_renders_with_users(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Happy-path: page renders title, provision input, and user table."""
    admin = _make_user("admin1", "admin")
    users = [
        _make_user_info("admin1", "admin"),
        _make_user_info("viewer1", "viewer"),
    ]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    rendered_table_calls: list[dict[str, Any]] = []

    def capture_render(*args: Any, **kwargs: Any) -> None:
        rendered_table_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(mod, "render_user_table", capture_render)

    await _raw_page()

    # Title rendered
    label_texts = [el.text for el in dummy_ui.labels]
    assert "User Management" in label_texts

    # Provision input + button exist
    assert len(dummy_ui.inputs) >= 1
    assert any(b.label == "Provision" for b in dummy_ui.buttons)

    # User table was rendered
    assert len(rendered_table_calls) == 1
    assert rendered_table_calls[0]["args"][0] is users


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_provision_user(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Provision button calls ensure_user_provisioned and notifies on success."""
    admin = _make_user("admin1", "admin")
    users = [_make_user_info("admin1", "admin")]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    provision_calls: list[tuple[Any, ...]] = []

    async def fake_provision(
        pool: Any, uid: str, role: str, admin_id: str, audit: Any
    ) -> tuple[bool, str]:
        provision_calls.append((uid, role, admin_id))
        return True, "User created"

    monkeypatch.setattr(mod, "ensure_user_provisioned", fake_provision)

    await _raw_page()

    # Find provision button and input
    provision_btn = next(b for b in dummy_ui.buttons if b.label == "Provision")
    provision_input = dummy_ui.inputs[0]
    provision_input.value = "  new_user  "

    await _call(provision_btn._on_click)

    assert len(provision_calls) == 1
    assert provision_calls[0][0] == "new_user"  # stripped
    assert any("User created" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_provision_user_empty_id(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Provision with empty input shows warning and does not call service."""
    admin = _make_user("admin1", "admin")
    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return [_make_user_info("admin1", "admin")]

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    provision_calls: list[str] = []

    async def fake_provision(*args: Any, **kwargs: Any) -> tuple[bool, str]:
        provision_calls.append("called")
        return True, ""

    monkeypatch.setattr(mod, "ensure_user_provisioned", fake_provision)

    await _raw_page()

    provision_btn = next(b for b in dummy_ui.buttons if b.label == "Provision")
    provision_input = dummy_ui.inputs[0]
    provision_input.value = "   "

    await _call(provision_btn._on_click)

    assert len(provision_calls) == 0
    assert any("Enter a user ID" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_role_change_self_edit_blocked(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Cannot change your own role — self-edit guard fires."""
    admin = _make_user("admin1", "admin")
    users = [_make_user_info("admin1", "admin")]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    # Capture callback kwargs from render_user_table
    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_role_change = captured_callbacks["on_role_change"]
    await on_role_change("admin1")  # same as current user

    # Should notify about self-edit
    assert any("Cannot change your own role" in n["text"] for n in dummy_ui.notifications)
    # Should log the denial
    assert any(a.get("action") == "role_change_denied" for a in fake_audit.actions)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_force_logout_self_blocked(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Cannot force-logout yourself."""
    admin = _make_user("admin1", "admin")
    users = [_make_user_info("admin1", "admin")]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_force_logout = captured_callbacks["on_force_logout"]
    await on_force_logout("admin1")

    assert any("Cannot force-logout yourself" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_force_logout_other_user(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Force-logout of another user opens a confirmation dialog."""
    admin = _make_user("admin1", "admin")
    users = [
        _make_user_info("admin1", "admin"),
        _make_user_info("viewer1", "viewer"),
    ]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_force_logout = captured_callbacks["on_force_logout"]
    await on_force_logout("viewer1")

    # Should NOT show self-logout warning
    assert not any("Cannot force-logout yourself" in n["text"] for n in dummy_ui.notifications)
    # Dialog was opened (label for confirmation created)
    label_texts = [el.text for el in dummy_ui.labels]
    assert any("Force logout viewer1" in t for t in label_texts)
    # Force Logout button was created in dialog
    assert any(b.label == "Force Logout" for b in dummy_ui.buttons)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_force_logout_confirm_executes(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """Clicking 'Force Logout' confirm calls invalidate and logs audit event."""
    admin = _make_user("admin1", "admin")
    users = [
        _make_user_info("admin1", "admin"),
        _make_user_info("viewer1", "viewer"),
    ]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    invalidate_calls: list[str] = []

    async def fake_invalidate(uid: str) -> int:
        invalidate_calls.append(uid)
        return 2

    monkeypatch.setattr(mod, "invalidate_redis_sessions_for_user", fake_invalidate)

    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_force_logout = captured_callbacks["on_force_logout"]
    await on_force_logout("viewer1")

    # Find the "Force Logout" confirm button and click it
    confirm_btn = next(b for b in dummy_ui.buttons if b.label == "Force Logout")
    await _call(confirm_btn._on_click)

    # invalidate was called with the target user
    assert invalidate_calls == ["viewer1"]
    # Audit log recorded the action
    assert any(a.get("action") == "force_logout" for a in fake_audit.actions)
    # Success notification shown
    assert any("Logged out viewer1" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_force_logout_confirm_permission_denied(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """If permission is revoked between opening dialog and confirming, deny."""
    admin = _make_user("admin1", "admin")
    users = [
        _make_user_info("admin1", "admin"),
        _make_user_info("viewer1", "viewer"),
    ]

    # Mutable flag: allow initially, deny after dialog opens
    allow = [True]

    def dynamic_permission(user: Any, perm: Any) -> bool:
        return allow[0]

    async def dynamic_verify(*_a: Any, **_kw: Any) -> bool:
        return allow[0]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", dynamic_permission)
    monkeypatch.setattr(mod, "verify_db_role", dynamic_verify)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    invalidate_calls: list[str] = []

    async def fake_invalidate(uid: str) -> int:
        invalidate_calls.append(uid)
        return 0

    monkeypatch.setattr(mod, "invalidate_redis_sessions_for_user", fake_invalidate)

    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_force_logout = captured_callbacks["on_force_logout"]
    await on_force_logout("viewer1")

    # Revoke permission after dialog is open
    allow[0] = False

    # Find confirm button and click — permission should now be denied
    confirm_btn = next(b for b in dummy_ui.buttons if b.label == "Force Logout")
    await _call(confirm_btn._on_click)

    # invalidate should NOT have been called
    assert invalidate_calls == []
    assert any("Permission denied" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_strategy_grants_opens_dialog(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """View strategies callback fetches data and calls render_strategy_grants_dialog."""
    admin = _make_user("admin1", "admin")
    users = [
        _make_user_info("admin1", "admin"),
        _make_user_info("viewer1", "viewer"),
    ]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    strat_a = SimpleNamespace(strategy_id="strat_a", name="Strat A", description=None)
    strat_b = SimpleNamespace(strategy_id="strat_b", name="Strat B", description=None)

    async def fake_get_strategies(_pool: Any, uid: str) -> list[str]:
        return ["strat_a"]

    async def fake_list_strategies(_pool: Any) -> list[SimpleNamespace]:
        return [strat_a, strat_b]

    monkeypatch.setattr(mod, "get_user_strategies", fake_get_strategies)
    monkeypatch.setattr(mod, "list_strategies", fake_list_strategies)

    dialog_calls: list[dict[str, Any]] = []

    def capture_dialog(*args: Any, **kwargs: Any) -> None:
        dialog_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(mod, "render_strategy_grants_dialog", capture_dialog)

    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_view_strategies = captured_callbacks["on_view_strategies"]
    await on_view_strategies("viewer1")

    assert len(dialog_calls) == 1
    assert dialog_calls[0]["args"][0] == "viewer1"
    assert dialog_calls[0]["args"][1] == ["strat_a"]
    assert dialog_calls[0]["args"][2] == [strat_a, strat_b]


@pytest.mark.asyncio()
@pytest.mark.usefixtures("_stub_components")
async def test_activity_log_fetches_events(
    dummy_ui: DummyUI,
    monkeypatch: pytest.MonkeyPatch,
    fake_audit: FakeAuditLogger,
) -> None:
    """View activity callback calls _fetch_user_activity and renders log."""
    admin = _make_user("admin1", "admin")
    users = [_make_user_info("admin1", "admin"), _make_user_info("viewer1", "viewer")]

    monkeypatch.setattr(mod, "get_current_user", lambda: admin)
    monkeypatch.setattr(mod, "has_permission", lambda user, perm: True)
    monkeypatch.setattr(mod, "get_db_pool", lambda: object())

    async def fake_list_users(_pool: Any) -> list[SimpleNamespace]:
        return users

    monkeypatch.setattr(mod, "list_users", fake_list_users)

    events_data = [{"action": "login", "user_id": "viewer1"}]

    async def fake_fetch(_pool: Any, uid: str, limit: int = 100) -> list[dict[str, Any]]:
        return events_data

    monkeypatch.setattr(mod, "_fetch_user_activity", fake_fetch)

    activity_log_calls: list[tuple[Any, ...]] = []

    def capture_activity(*args: Any, **kwargs: Any) -> None:
        activity_log_calls.append(args)

    monkeypatch.setattr(mod, "render_user_activity_log", capture_activity)

    captured_callbacks: dict[str, Any] = {}

    def capture_table(*args: Any, **kwargs: Any) -> None:
        captured_callbacks.update(kwargs)

    monkeypatch.setattr(mod, "render_user_table", capture_table)

    await _raw_page()

    on_view_activity = captured_callbacks["on_view_activity"]
    await on_view_activity("viewer1")

    assert len(activity_log_calls) == 1
    assert activity_log_calls[0][0] == "viewer1"
    assert activity_log_calls[0][1] is events_data


@pytest.mark.asyncio()
async def test_fetch_user_activity_returns_none_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fetch_user_activity returns None when DB query fails."""

    class BrokenPool:
        def connection(self) -> Any:
            class Conn:
                async def __aenter__(self) -> Any:
                    raise psycopg.Error("db down")

                async def __aexit__(self, *a: Any) -> bool:
                    return False

            return Conn()

    result = await mod._fetch_user_activity(BrokenPool(), "user1")
    assert result is None
