"""Tests for API key revoke/rotate functionality in admin.py (P6T16.3)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.web_console_ng.pages.admin as admin_module

# === DummyElement / DummyUI (matches test_admin.py pattern) ===


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

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
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
        self.badges: list[dict[str, Any]] = []
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

    def column(self) -> DummyElement:
        return DummyElement(self, "column")

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

    def badge(self, text: str, color: str | None = None) -> DummyElement:
        self.badges.append({"text": text, "color": color})
        return DummyElement(self, "badge", text=text, color=color)

    def number(self, *_: Any, **__: Any) -> DummyElement:
        return DummyElement(self, "number")


@pytest.fixture(autouse=True)
def _stub_verify_db_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub verify_db_role to return True by default (tests use object() as db_pool)."""

    async def _always_pass(*_a: Any, **_kw: Any) -> bool:
        return True

    monkeypatch.setattr(admin_module, "verify_db_role", _always_pass)


@pytest.fixture()
def dummy_ui(monkeypatch: pytest.MonkeyPatch) -> DummyUI:
    dui = DummyUI()
    monkeypatch.setattr(admin_module, "ui", dui)
    return dui


async def _call(cb: Callable[..., Any] | None, *args: Any) -> None:
    if cb is None:
        return
    result = cb(*args) if not asyncio.iscoroutinefunction(cb) else await cb(*args)
    # Handle lambdas that return coroutines (e.g., lambda _e: async_fn())
    if asyncio.iscoroutine(result):
        await result


# === Mock helpers ===


def _make_mock_pool(
    *,
    execute_return: Any = None,
    fetchone_return: Any = None,
) -> MagicMock:
    """Build a mock async db pool with configurable cursor returns."""
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=fetchone_return)

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=cursor)
    # Support conn.transaction() as async context manager
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    conn_ctx = AsyncMock()
    conn_ctx.__aenter__ = AsyncMock(return_value=conn)
    conn_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn_ctx)
    return pool


def _make_mock_redis() -> MagicMock:
    """Build a mock Redis store with get_master returning an async client."""
    redis_client = AsyncMock()
    redis_client.setex = AsyncMock()

    store = MagicMock()
    store.get_master = AsyncMock(return_value=redis_client)
    return store


# === Tests ===


@pytest.mark.asyncio()
async def test_revoke_key_permission_check(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_render_api_key_manager shows permission error when user lacks MANAGE_API_KEYS."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: False)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=MagicMock())

    # Should show permission denied label, no buttons rendered
    denied_labels = [lbl for lbl in dummy_ui.labels if "Permission denied" in lbl.text]
    assert len(denied_labels) == 1
    assert "MANAGE_API_KEYS" in denied_labels[0].text
    # No revoke/rotate buttons should exist
    revoke_buttons = [b for b in dummy_ui.buttons if b.label == "Revoke"]
    assert len(revoke_buttons) == 0


@pytest.mark.asyncio()
async def test_revoke_key_updates_db_and_caches(
    dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_revoke_key updates DB, caches revocation in Redis, and logs audit."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda: {"user_id": "u1", "role": "admin"})

    # DB pool: fetchone returns the key_prefix after UPDATE
    pool = _make_mock_pool(fetchone_return=("tp_live_ABC",))

    # Redis store mock
    redis_store = _make_mock_redis()
    monkeypatch.setattr(admin_module, "get_redis_store", lambda: redis_store)

    # Audit logger mock
    audit_mock = AsyncMock()
    audit_mock.log_admin_change = AsyncMock()
    monkeypatch.setattr(admin_module, "AuditLogger", lambda pool: audit_mock)

    # List keys returns one active key so the manager renders fully
    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "key-1",
                "name": "My Key",
                "key_prefix": "tp_live_ABC",
                "scopes": ["read_positions"],
                "expires_at": None,
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            }
        ]

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=pool)

    # Find the Revoke button and click it to open the dialog
    revoke_buttons = [b for b in dummy_ui.buttons if b.label == "Revoke"]
    assert len(revoke_buttons) >= 1

    # The first Revoke button is from the keys_list (flat dense style)
    # Its on_click is a lambda _e, ... so pass a dummy event
    await _call(revoke_buttons[0]._on_click, None)

    # Now a confirmation dialog was opened. Find the confirm input and set it.
    confirm_input = next((i for i in dummy_ui.inputs if i.label == "Type REVOKE"), None)
    assert confirm_input is not None
    confirm_input.value = "REVOKE"

    # Find the inner Revoke button (from the dialog, color="red")
    inner_revoke_buttons = [
        b for b in dummy_ui.buttons if b.label == "Revoke" and b.kwargs.get("color") == "red"
    ]
    assert len(inner_revoke_buttons) >= 1

    await _call(inner_revoke_buttons[-1]._on_click)

    # Verify DB was called (UPDATE with revoked_at)
    conn_ctx = pool.connection()
    conn = await conn_ctx.__aenter__()
    conn.execute.assert_called()

    # Verify Redis cache was set
    redis_client = await redis_store.get_master()
    redis_client.setex.assert_called_once_with("api_key_revoked:tp_live_ABC", 300, "1")

    # Verify audit log
    audit_mock.log_admin_change.assert_called_once()
    call_kwargs = audit_mock.log_admin_change.call_args.kwargs
    assert call_kwargs["action"] == "api_key_revoked"
    assert call_kwargs["details"]["key_prefix"] == "tp_live_ABC"

    # Verify success notification
    assert any("API key revoked" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_rotate_key_creates_new(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """_rotate_key revokes old key and creates new one in a transaction."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda: {"user_id": "u1", "role": "admin"})

    # DB pool: fetchone returns old key metadata (name, scopes, expires_at, key_prefix)
    pool = _make_mock_pool(
        fetchone_return=("My Key", ["read_positions"], None, "tp_live_OLD"),
    )

    # Redis store
    redis_store = _make_mock_redis()
    monkeypatch.setattr(admin_module, "get_redis_store", lambda: redis_store)

    # Audit logger
    audit_mock = AsyncMock()
    audit_mock.log_admin_change = AsyncMock()
    monkeypatch.setattr(admin_module, "AuditLogger", lambda pool: audit_mock)

    # Mock generate_api_key / hash_api_key for deterministic output
    monkeypatch.setattr(
        "libs.platform.admin.api_keys.generate_api_key",
        lambda: ("new_full_key_abc", "tp_live_NEW", "salt123"),
    )
    monkeypatch.setattr(
        "libs.platform.admin.api_keys.hash_api_key",
        lambda key, salt: "hashed_value",
    )

    # List keys returns one active key
    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "key-1",
                "name": "My Key",
                "key_prefix": "tp_live_OLD",
                "scopes": ["read_positions"],
                "expires_at": None,
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            }
        ]

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)

    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=pool)

    # Click the Rotate button from the key row (lambda _e, ... needs dummy event)
    rotate_buttons = [b for b in dummy_ui.buttons if b.label == "Rotate"]
    assert len(rotate_buttons) >= 1
    await _call(rotate_buttons[0]._on_click, None)

    # Set the type-to-confirm input value
    rotate_confirm = next((i for i in dummy_ui.inputs if i.label == "Type ROTATE"), None)
    assert rotate_confirm is not None
    rotate_confirm.value = "ROTATE"

    # Find the inner Rotate confirm button (color="orange")
    inner_rotate_buttons = [
        b for b in dummy_ui.buttons if b.label == "Rotate" and b.kwargs.get("color") == "orange"
    ]
    assert len(inner_rotate_buttons) >= 1

    await _call(inner_rotate_buttons[-1]._on_click)

    # Verify DB executed (the UPDATE + INSERT inside transaction)
    conn_ctx = pool.connection()
    conn = await conn_ctx.__aenter__()
    # Should have at least 2 execute calls: UPDATE old + INSERT new
    assert conn.execute.call_count >= 2

    # Verify Redis cached old key revocation
    redis_client = await redis_store.get_master()
    redis_client.setex.assert_called_once_with("api_key_revoked:tp_live_OLD", 300, "1")

    # Verify audit log records rotation with old and new prefixes
    audit_mock.log_admin_change.assert_called_once()
    call_kwargs = audit_mock.log_admin_change.call_args.kwargs
    assert call_kwargs["action"] == "api_key_rotated"
    assert call_kwargs["details"]["old_key_prefix"] == "tp_live_OLD"
    assert call_kwargs["details"]["new_key_prefix"] == "tp_live_NEW"

    # Verify success notification
    assert any("API key rotated" in n["text"] for n in dummy_ui.notifications)


@pytest.mark.asyncio()
async def test_api_key_status_badges(dummy_ui: DummyUI, monkeypatch: pytest.MonkeyPatch) -> None:
    """keys_list renders correct status badges for active, revoked, and expired keys."""
    monkeypatch.setattr(admin_module, "has_permission", lambda *_: True)

    now = datetime.now(UTC)
    keys = [
        {
            "id": "key-active",
            "name": "Active Key",
            "key_prefix": "tp_live_ACT",
            "scopes": ["read_positions"],
            "expires_at": now + timedelta(days=30),
            "last_used_at": None,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "revoked_at": None,
        },
        {
            "id": "key-revoked",
            "name": "Revoked Key",
            "key_prefix": "tp_live_REV",
            "scopes": ["read_orders"],
            "expires_at": None,
            "last_used_at": None,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "revoked_at": datetime(2026, 2, 15, 10, 30, tzinfo=UTC),
        },
        {
            "id": "key-expired",
            "name": "Expired Key",
            "key_prefix": "tp_live_EXP",
            "scopes": ["write_orders"],
            "expires_at": now - timedelta(days=1),
            "last_used_at": None,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "revoked_at": None,
        },
    ]

    async def fake_list_keys(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return keys

    monkeypatch.setattr(admin_module, "_list_api_keys", fake_list_keys)

    pool = _make_mock_pool()
    await admin_module._render_api_key_manager({"user_id": "u1"}, db_pool=pool)

    # Check badges
    badge_texts = [b["text"] for b in dummy_ui.badges]

    # Active key -> green "Active" badge
    assert any("Active" in t for t in badge_texts)
    active_badge = next(b for b in dummy_ui.badges if "Active" in b["text"])
    assert active_badge["color"] == "green"

    # Revoked key -> red "Revoked" badge with timestamp
    revoked_badges = [b for b in dummy_ui.badges if "Revoked" in b["text"]]
    assert len(revoked_badges) == 1
    assert revoked_badges[0]["color"] == "red"
    assert "2026-02-15" in revoked_badges[0]["text"]

    # Expired key -> yellow "Expired" badge
    expired_badges = [b for b in dummy_ui.badges if b["text"] == "Expired"]
    assert len(expired_badges) == 1
    assert expired_badges[0]["color"] == "yellow"

    # Only active keys get Revoke/Rotate buttons
    revoke_buttons = [b for b in dummy_ui.buttons if b.label == "Revoke"]
    rotate_buttons = [b for b in dummy_ui.buttons if b.label == "Rotate"]
    # 1 active key -> 1 Revoke + 1 Rotate
    assert len(revoke_buttons) == 1
    assert len(rotate_buttons) == 1
