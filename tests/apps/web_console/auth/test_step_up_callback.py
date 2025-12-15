import time
from datetime import UTC, datetime, timedelta

import pytest

from apps.web_console.auth.step_up_callback import handle_step_up_callback


class FakeSessionData:
    def __init__(
        self,
        user_id="user-1",
        session_version=1,
        pending_action="/dashboard",
        step_up_requested_at: datetime | None = None,
    ):
        self.user_id = user_id
        self.session_version = session_version
        self.pending_action = pending_action
        self.step_up_requested_at = step_up_requested_at or datetime.now(UTC)


class FakeSessionStore:
    def __init__(self, session_data: FakeSessionData):
        self.session_data = session_data
        self.deleted = False
        self.cleared = False
        self.updated_claims = None
        self.cleared_request = False

    async def get_session(self, session_id, update_activity=True):
        return self.session_data

    async def delete_session(self, session_id):
        self.deleted = True
        return True

    async def clear_step_up_state(self, session_id):
        self.cleared = True
        return True

    async def clear_step_up_request_timestamp(self, session_id):
        self.cleared_request = True
        return True

    async def update_step_up_claims(self, session_id, claims):
        self.updated_claims = claims
        return True


class FakeAuditLogger:
    def __init__(self):
        self.events = []

    async def log_auth_event(self, **kwargs):
        self.events.append(kwargs)


class FakeJWKSValidator:
    def __init__(self, claims=None):
        self.auth0_domain = "example.auth0.com"
        self.calls = []
        self.claims = claims or {"sub": "user-1", "auth_time": int(time.time()), "amr": ["otp"]}

    async def validate_id_token(self, **kwargs):
        self.calls.append(kwargs)
        return self.claims


@pytest.mark.asyncio()
async def test_step_up_invalid_session_version_monkeypatch(monkeypatch):
    async def _invalidate(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _invalidate
    )

    session_data = FakeSessionData(session_version=2)
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    async def _exchange(_code):
        return {"id_token": "token"}

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=lambda provided, expected: True,
        exchange_code=_exchange,
    )

    assert result["error"] == "session_invalidated"
    assert store.deleted is True
    assert store.cleared is True
    assert audit.events[-1]["action"] == "step_up_session_invalidated"


@pytest.mark.asyncio()
async def test_step_up_success_path(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    session_data = FakeSessionData(pending_action="/orders")
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()
    validator = FakeJWKSValidator()

    async def _exchange(_code):
        return {"id_token": "token"}

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=validator,
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),  # Must provide db_pool for fail-closed validation
        validate_state=lambda provided, expected: True,
        exchange_code=_exchange,
    )

    assert result["redirect_to"] == "/orders"
    assert store.updated_claims is not None
    assert audit.events
    assert audit.events[-1]["action"] == "step_up_success"


@pytest.mark.asyncio()
async def test_step_up_state_validation_failure(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    session_data = FakeSessionData()
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    result = await handle_step_up_callback(
        code="code",
        state="bad",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=lambda provided, expected: False,
        exchange_code=lambda code: {"id_token": "token"},
    )

    assert result["error"] == "invalid_state"
    assert store.cleared is True
    assert audit.events[-1]["action"] == "step_up_state_validation"
    assert audit.events[-1]["details"]["reason"] == "state_mismatch"


@pytest.mark.asyncio()
async def test_step_up_missing_validator_is_logged_and_reported(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    session_data = FakeSessionData()
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=None,
        exchange_code=lambda code: {"id_token": "token"},
    )

    assert result["error"] == "state_validation_required"
    assert store.cleared is True
    assert audit.events[-1]["action"] == "step_up_state_validation"
    assert audit.events[-1]["details"]["reason"] == "missing_validator"


@pytest.mark.asyncio()
async def test_step_up_timeout_returns_error_and_clears_state(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    expired = datetime.now(UTC) - timedelta(seconds=400)
    session_data = FakeSessionData(step_up_requested_at=expired)
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=lambda provided, expected: True,
        exchange_code=lambda code: {"id_token": "token"},
    )

    assert result["error"] == "step_up_timeout"
    assert store.cleared is True
    assert audit.events[-1]["action"] == "step_up_timeout"


@pytest.mark.asyncio()
async def test_step_up_subject_mismatch_invalidates_session(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    session_data = FakeSessionData()
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    async def _exchange(_code):
        return {"id_token": "token"}

    validator = FakeJWKSValidator(
        claims={"sub": "different", "auth_time": int(time.time()), "amr": ["otp"]}
    )

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=validator,
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=lambda provided, expected: True,
        exchange_code=_exchange,
    )

    assert result["error"] == "subject_mismatch"
    assert store.deleted is True
    assert store.cleared is True
    assert audit.events[-1]["action"] == "step_up_callback_failed"


@pytest.mark.asyncio()
async def test_step_up_missing_session_returns_safe_redirect():
    class MissingSessionStore:
        def __init__(self):
            self.cleared = False

        async def get_session(self, *_args, **_kwargs):
            return None

        async def clear_step_up_state(self, _session_id):
            self.cleared = True
            return True

    store = MissingSessionStore()
    audit = FakeAuditLogger()

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=None,
        validate_state=lambda provided, expected: True,
        exchange_code=lambda _code: {"id_token": "token"},
    )

    assert result["error"] == "session_not_found"
    assert result["redirect_to"] == "/login"
    assert store.cleared is True
    assert audit.events[-1]["details"]["reason"] == "session_not_found"


@pytest.mark.asyncio()
async def test_step_up_missing_exchange_code_uses_structured_error(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    session_data = FakeSessionData()
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=lambda provided, expected: True,
        exchange_code=None,
    )

    assert result["error"] == "step_up_configuration_error"
    assert store.cleared is True
    assert audit.events[-1]["details"]["reason"] == "exchange_code_missing"


@pytest.mark.asyncio()
async def test_step_up_missing_id_token_is_normalized(monkeypatch):
    async def _validate_version(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "apps.web_console.auth.step_up_callback.validate_session_version", _validate_version
    )

    session_data = FakeSessionData()
    store = FakeSessionStore(session_data)
    audit = FakeAuditLogger()

    async def _exchange(_code):
        return {}

    result = await handle_step_up_callback(
        code="code",
        state="state",
        session_store=store,
        session_id="sid",
        audit_logger=audit,
        jwks_validator=FakeJWKSValidator(),
        expected_audience="aud",
        expected_issuer="https://issuer/",
        db_pool=object(),
        validate_state=lambda provided, expected: True,
        exchange_code=_exchange,
    )

    assert result["error"] == "id_token_missing"
    assert result["redirect_to"] == "/dashboard"
    assert store.cleared is True
    assert audit.events[-1]["details"]["reason"] == "id_token_missing"
