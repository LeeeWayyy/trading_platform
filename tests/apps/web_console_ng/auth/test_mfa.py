"""Tests for MFA verification flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.web_console_ng.auth import mfa as mfa_module
from apps.web_console_ng.auth.session_store import SessionValidationError


class _DummyTOTP:
    def __init__(self, secret: str, *, should_verify: bool) -> None:
        self.secret = secret
        self.should_verify = should_verify

    def verify(self, _code: str) -> bool:
        return self.should_verify


@pytest.fixture()
def handler(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace]:
    session_store = SimpleNamespace(
        validate_session=AsyncMock(),
        verify_cookie=Mock(return_value="session-1"),
        rotate_session=AsyncMock(),
    )
    rate_limiter = SimpleNamespace(
        check_only=AsyncMock(return_value=(False, 0, "allowed")),
        clear_on_success=AsyncMock(),
        record_failure=AsyncMock(return_value=(True, 0, "failure_recorded")),
    )

    monkeypatch.setattr(mfa_module, "get_session_store", lambda: session_store)
    monkeypatch.setattr(mfa_module, "AuthRateLimiter", lambda: rate_limiter)

    return mfa_module.MFAHandler(), session_store, rate_limiter


@pytest.mark.asyncio()
async def test_verify_returns_service_error_on_redis_outage(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace]
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.side_effect = SessionValidationError("redis down")

    result = await mfa_handler.verify("pending", "123456")

    assert result.success is False
    assert result.error_message == "Service temporarily unavailable. Please try again."
    rate_limiter.check_only.assert_not_called()


@pytest.mark.asyncio()
async def test_verify_rejects_missing_session(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace]
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.return_value = None

    result = await mfa_handler.verify("pending", "123456")

    assert result.success is False
    assert result.error_message == "Session expired or invalid"
    session_store.verify_cookie.assert_not_called()
    rate_limiter.check_only.assert_not_called()


@pytest.mark.asyncio()
async def test_verify_rejects_invalid_cookie_signature(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace]
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.return_value = {"user": {"user_id": "mfa", "mfa_pending": True}}
    session_store.verify_cookie.return_value = None

    result = await mfa_handler.verify("pending", "123456")

    assert result.success is False
    assert result.error_message == "Session expired or invalid"
    rate_limiter.check_only.assert_not_called()


@pytest.mark.asyncio()
async def test_verify_rejects_when_mfa_not_required(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace]
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.return_value = {"user": {"user_id": "mfa", "mfa_pending": False}}
    session_store.verify_cookie.return_value = "session-1"

    result = await mfa_handler.verify("pending", "123456")

    assert result.success is False
    assert result.error_message == "MFA not required for this session"
    rate_limiter.check_only.assert_not_called()


@pytest.mark.asyncio()
async def test_verify_blocks_when_rate_limited(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace]
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.return_value = {"user": {"user_id": "mfa", "mfa_pending": True}}
    session_store.verify_cookie.return_value = "session-1"
    rate_limiter.check_only.return_value = (True, 120, "account_locked")

    result = await mfa_handler.verify("pending", "123456", client_ip="203.0.113.5")

    assert result.success is False
    assert result.locked_out is True
    assert result.lockout_remaining == 120
    assert result.error_message == "MFA temporarily locked due to failed attempts"


@pytest.mark.asyncio()
async def test_verify_rejects_missing_secret(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.return_value = {"user": {"user_id": "mfa", "mfa_pending": True}}
    session_store.verify_cookie.return_value = "session-1"
    monkeypatch.setattr(mfa_handler, "_get_totp_secret", lambda _user_id: None)

    result = await mfa_handler.verify("pending", "123456")

    assert result.success is False
    assert result.error_message == "MFA not set up for user"
    rate_limiter.record_failure.assert_not_called()


@pytest.mark.asyncio()
async def test_verify_success_rotates_session(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    user_data = {"user_id": "mfa", "mfa_pending": True}
    session_store.validate_session.return_value = {"user": user_data}
    session_store.verify_cookie.return_value = "session-1"
    session_store.rotate_session.return_value = ("cookie-new", "csrf-token")

    monkeypatch.setattr(mfa_handler, "_get_totp_secret", lambda _user_id: "SECRET")

    def _totp_factory(secret: str) -> _DummyTOTP:
        return _DummyTOTP(secret, should_verify=True)

    monkeypatch.setattr(mfa_module.pyotp, "TOTP", _totp_factory)

    result = await mfa_handler.verify("pending", "123456")

    assert result.success is True
    assert result.cookie_value == "cookie-new"
    assert result.csrf_token == "csrf-token"
    assert result.user_data == {"user_id": "mfa", "mfa_pending": False}
    rate_limiter.clear_on_success.assert_called_once_with("mfa")
    session_store.rotate_session.assert_called_once_with(
        "session-1", user_updates={"mfa_pending": False}
    )


@pytest.mark.asyncio()
async def test_verify_invalid_code_can_lock_account(
    handler: tuple[mfa_module.MFAHandler, SimpleNamespace, SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mfa_handler, session_store, rate_limiter = handler
    session_store.validate_session.return_value = {"user": {"user_id": "mfa", "mfa_pending": True}}
    session_store.verify_cookie.return_value = "session-1"

    monkeypatch.setattr(mfa_handler, "_get_totp_secret", lambda _user_id: "SECRET")

    def _totp_factory(secret: str) -> _DummyTOTP:
        return _DummyTOTP(secret, should_verify=False)

    monkeypatch.setattr(mfa_module.pyotp, "TOTP", _totp_factory)
    rate_limiter.record_failure.return_value = (False, 300, "account_locked_now")

    result = await mfa_handler.verify("pending", "000000", client_ip="203.0.113.9")

    assert result.success is False
    assert result.locked_out is True
    assert result.lockout_remaining == 300
    assert result.error_message == "MFA locked due to repeated failures"
