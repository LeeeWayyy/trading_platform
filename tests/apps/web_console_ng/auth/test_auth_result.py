from __future__ import annotations

from apps.web_console_ng.auth.auth_result import AuthResult


def test_auth_result_defaults() -> None:
    result = AuthResult(success=False)

    assert result.success is False
    assert result.cookie_value is None
    assert result.csrf_token is None
    assert result.user_data is None
    assert result.error_message is None
    assert result.warning_message is None
    assert result.requires_mfa is False
    assert result.rate_limited is False
    assert result.retry_after == 0
    assert result.locked_out is False
    assert result.lockout_remaining == 0


def test_auth_result_populated_fields() -> None:
    result = AuthResult(
        success=True,
        cookie_value="session.cookie",
        csrf_token="csrf-token",
        user_data={"user_id": "u-1"},
        warning_message="password expiring",
        requires_mfa=True,
    )

    assert result.success is True
    assert result.cookie_value == "session.cookie"
    assert result.csrf_token == "csrf-token"
    assert result.user_data == {"user_id": "u-1"}
    assert result.warning_message == "password expiring"
    assert result.requires_mfa is True
    assert result.error_message is None
    assert result.rate_limited is False
    assert result.retry_after == 0
    assert result.locked_out is False
    assert result.lockout_remaining == 0
