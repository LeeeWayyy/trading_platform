"""Tests for API key manager Streamlit component."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.web_console.components import api_key_manager
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Role


class _FakeCursor:
    def __init__(self, rows: list[Any]):
        self.rows = rows

    async def fetchall(self) -> list[Any]:
        return self.rows

    async def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None


class _FakeDB:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.executed.append((query, params))
        return _FakeCursor(self.rows)


class _FakeAuditLogger:
    def __init__(self) -> None:
        self.logged: list[dict[str, Any]] = []

    async def log_action(self, **kwargs: Any) -> None:
        self.logged.append(kwargs)


class _FakeStreamlit:
    """Minimal Streamlit stub for component testing."""

    def __init__(self) -> None:
        self.session_state: dict[str, Any] = {}
        self.text_inputs: dict[str, Any] = {}
        self.text_areas: dict[str, Any] = {}
        self.checkbox_values: dict[str, bool] = {}
        self.date_input_value: Any = None
        self.form_submit_results: list[bool] = []
        self.button_results: dict[str, bool] = {}
        self.errors: list[str] = []
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self.codes: list[str] = []
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.subheaders: list[str] = []
        self.titles: list[str] = []

    # UI primitives -----------------------------------------------------
    def title(self, msg: str, **_kwargs: Any) -> None:
        self.titles.append(msg)

    def caption(self, msg: str, **_kwargs: Any) -> None:
        self.captions.append(msg)

    def subheader(self, msg: str, **_kwargs: Any) -> None:
        self.subheaders.append(msg)

    def info(self, msg: str, **_kwargs: Any) -> None:
        self.infos.append(str(msg))

    def warning(self, msg: str, **_kwargs: Any) -> None:
        self.warnings.append(str(msg))

    def error(self, msg: str, **_kwargs: Any) -> None:
        self.errors.append(str(msg))

    def success(self, msg: str, **_kwargs: Any) -> None:
        self.successes.append(str(msg))

    def markdown(self, msg: str, **_kwargs: Any) -> None:
        self.markdowns.append(str(msg))

    def code(self, msg: str, **_kwargs: Any) -> None:
        self.codes.append(str(msg))

    # Layout ------------------------------------------------------------
    def columns(self, spec) -> list[_FakeStreamlit]:
        count = len(spec) if isinstance(spec, list | tuple) else int(spec)
        return [self for _ in range(count)]

    def tabs(self, labels) -> list[_FakeStreamlit]:
        return [self for _ in labels]

    # Form handling -----------------------------------------------------
    def form(self, _name: str):
        return self

    def __enter__(self) -> _FakeStreamlit:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def text_input(self, label: str, value: Any = "", **_kwargs: Any) -> Any:
        return self.text_inputs.get(label, value)

    def text_area(self, label: str, value: str | None = None, **_kwargs: Any) -> str | None:
        return self.text_areas.get(label, value)

    def checkbox(self, label: str, value: bool | None = False, **_kwargs: Any) -> bool:
        return self.checkbox_values.get(label, value or False)

    def date_input(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.date_input_value

    def form_submit_button(self, _label: str, **_kwargs: Any) -> bool:
        if self.form_submit_results:
            return self.form_submit_results.pop(0)
        return False

    def button(self, label: str, key: str | None = None, **_kwargs: Any) -> bool:
        btn_key = key or label
        return self.button_results.get(btn_key, False)


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
    monkeypatch.setattr(api_key_manager, "st", stub)
    # Ensure CSRF helper uses the same stubbed session state
    monkeypatch.setattr(api_key_manager.generate_csrf_token.__module__ + ".st", stub)
    monkeypatch.setattr(api_key_manager.verify_csrf_token.__module__ + ".st", stub)
    return stub


def test_create_flow_happy_path(monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser):
    """API key creation shows modal and writes to DB."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "csrf"
    stub.text_inputs = {"Key Name": "Trading Bot", "csrf": "csrf"}
    stub.checkbox_values = {
        "Read positions": True,
        "Read orders": False,
        "Write orders": False,
        "Read strategies": False,
        "Set expiry date (optional)": False,
    }
    stub.form_submit_results = [True]  # submit create form

    fake_db = _FakeDB(
        rows=[
            (
                "k1",
                "Trading Bot",
                "tp_live_demo123",
                ["read_positions"],
                None,
                None,
                "2025-12-20T00:00:00Z",
                None,
            )
        ]
    )
    audit = _FakeAuditLogger()
    redis_client = AsyncMock()

    monkeypatch.setattr(
        api_key_manager, "generate_api_key", lambda: ("fullkey", "tp_live_demo123", "salt")
    )
    monkeypatch.setattr(api_key_manager, "hash_api_key", lambda key, salt: "hashed")

    api_key_manager.render_api_key_manager(admin_user, fake_db, audit, redis_client)

    assert api_key_manager._MODAL_STATE_KEY in stub.session_state
    modal = stub.session_state[api_key_manager._MODAL_STATE_KEY]
    assert modal["prefix"] == "tp_live_demo123"
    assert fake_db.executed[0][0].strip().startswith("INSERT INTO api_keys")
    assert audit.logged[0]["action"] == "api_key_created"


def test_create_flow_validation(monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser):
    """Validation errors block creation."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "csrf"
    stub.text_inputs = {"Key Name": "ab", "csrf": "csrf"}
    stub.checkbox_values = {
        "Read positions": False,
        "Read orders": False,
        "Write orders": False,
        "Read strategies": False,
    }
    stub.form_submit_results = [True]

    fake_db = _FakeDB()

    api_key_manager.render_api_key_manager(admin_user, fake_db, _FakeAuditLogger(), AsyncMock())

    assert any("Name must be at least 3 characters." in err for err in stub.errors)
    assert api_key_manager._MODAL_STATE_KEY not in stub.session_state
    assert all("INSERT INTO api_keys" not in q for q, _ in fake_db.executed)


def test_revoke_flow(monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser):
    """Revocation path updates DB and Redis with reason check."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "csrf"
    stub.form_submit_results = [False, True]  # create form not submitted, revoke submit
    stub.button_results = {"revoke_tp_live_oldkey": True}
    stub.text_inputs = {"csrf": "csrf"}
    stub.text_areas = {"Reason for revocation (min 20 chars)": "This key was compromised!!"}

    fake_db = _FakeDB(
        rows=[
            (
                "k1",
                "Old Key",
                "tp_live_oldkey",
                ["read_positions"],
                None,
                None,
                datetime.now(UTC),
                None,
            )
        ]
    )
    redis_client = AsyncMock()
    audit = _FakeAuditLogger()

    api_key_manager.render_api_key_manager(admin_user, fake_db, audit, redis_client)

    update_queries = [q for q, _ in fake_db.executed if "UPDATE api_keys SET revoked_at" in q]
    assert update_queries, "Revocation should update DB"
    redis_client.setex.assert_awaited_once()
    assert any(entry["action"] == "api_key_revoked" for entry in audit.logged)


def test_rotation_flow(monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser):
    """Rotation creates new key and marks old one pending revocation."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "csrf"
    stub.form_submit_results = [False, True]  # create form skip, rotate form submit
    stub.button_results = {"rotate_tp_live_oldkey": True}
    stub.text_inputs = {"csrf": "csrf"}

    fake_db = _FakeDB(
        rows=[
            (
                "k1",
                "Primary",
                "tp_live_oldkey",
                ["read_positions", "write_orders"],
                None,
                None,
                datetime.now(UTC),
                None,
            )
        ]
    )
    audit = _FakeAuditLogger()

    monkeypatch.setattr(
        api_key_manager, "generate_api_key", lambda: ("newfull", "tp_live_newkey", "salt")
    )
    monkeypatch.setattr(api_key_manager, "hash_api_key", lambda key, salt: "hashed")

    api_key_manager.render_api_key_manager(admin_user, fake_db, audit, AsyncMock())

    inserts = [q for q, _ in fake_db.executed if "INSERT INTO api_keys" in q]
    assert inserts, "Rotation should insert new key"
    assert "tp_live_oldkey" in api_key_manager.st.session_state[api_key_manager._PENDING_REVOKE_KEY]
    assert api_key_manager._MODAL_STATE_KEY in stub.session_state


def test_one_time_modal_close(monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser):
    """Closing modal removes state after acknowledgement."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state[api_key_manager._MODAL_STATE_KEY] = {"full_key": "abc", "prefix": "p"}
    stub.checkbox_values = {"I have copied this key": True, "api_key_acknowledged": True}
    stub.button_results = {"Close": True}

    api_key_manager.render_api_key_manager(admin_user, _FakeDB(), _FakeAuditLogger(), AsyncMock())

    assert api_key_manager._MODAL_STATE_KEY not in stub.session_state


def test_csrf_rejected(monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser):
    """Invalid CSRF token blocks creation."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "valid"
    stub.text_inputs = {"Key Name": "Key", "csrf": "invalid"}
    stub.checkbox_values = {"Read positions": True}
    stub.form_submit_results = [True]

    fake_db = _FakeDB()

    api_key_manager.render_api_key_manager(admin_user, fake_db, _FakeAuditLogger(), AsyncMock())

    assert any("Invalid form submission" in err for err in stub.errors)
    assert all("INSERT INTO api_keys" not in q for q, _ in fake_db.executed)


def test_rbac_denied(monkeypatch: pytest.MonkeyPatch):
    """Users without MANAGE_API_KEYS cannot access component."""

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "csrf"

    viewer = AuthenticatedUser(
        user_id="viewer",
        role=Role.VIEWER,
        strategies=[],
        session_version=1,
        request_id="req",
    )

    api_key_manager.render_api_key_manager(viewer, _FakeDB(), _FakeAuditLogger(), AsyncMock())

    assert stub.errors, "Access denial should show error"
    assert stub.successes == []
    assert stub.warnings == []


def test_create_expiry_date_in_past_rejected(
    monkeypatch: pytest.MonkeyPatch, admin_user: AuthenticatedUser
):
    """Expiry dates in the past are rejected."""
    from datetime import timedelta

    stub = _install_streamlit_stub(monkeypatch)
    stub.session_state["_csrf_token"] = "csrf"
    stub.text_inputs = {"Key Name": "Past Key", "csrf": "csrf"}
    stub.checkbox_values = {
        "Read positions": True,
        "Read orders": False,
        "Write orders": False,
        "Read strategies": False,
        "Set expiry date (optional)": True,
    }
    # Set a past date
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    stub.date_input_value = yesterday
    stub.form_submit_results = [True]

    fake_db = _FakeDB()

    api_key_manager.render_api_key_manager(admin_user, fake_db, _FakeAuditLogger(), AsyncMock())

    assert any("Expiry date must be in the future" in err for err in stub.errors)
    assert api_key_manager._MODAL_STATE_KEY not in stub.session_state
    assert all("INSERT INTO api_keys" not in q for q, _ in fake_db.executed)
