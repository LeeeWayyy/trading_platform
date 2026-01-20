"""Tests for authentication middleware.

This test suite validates HMAC-signed header validation middleware extracted from main.py,
ensuring correct:
- Token verification with HMAC-SHA256
- Timestamp validation for replay protection
- Fail-closed security (missing/invalid → 401)
- Constant-time comparison
- Middleware request.state population
- Backward compatibility modes

Target: 90%+ coverage per Phase 1 requirements.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from apps.execution_gateway.middleware import (
    _verify_internal_token,
    populate_user_from_headers,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def mock_settings_no_validation():
    """Settings with validation disabled."""
    settings = MagicMock()
    settings.internal_token_required = False
    return settings


@pytest.fixture()
def mock_settings_with_validation():
    """Settings with validation enabled."""
    settings = MagicMock()
    settings.internal_token_required = True
    settings.internal_token_timestamp_tolerance_seconds = 300

    secret_mock = MagicMock()
    secret_mock.get_secret_value.return_value = "test_secret_key"
    settings.internal_token_secret = secret_mock

    return settings


def create_valid_token(
    user_id: str, role: str, strategies: str, timestamp_str: str, secret: str
) -> str:
    """Helper to create valid HMAC token."""
    payload_data = {
        "uid": user_id.strip(),
        "role": role.strip(),
        "strats": strategies.strip(),
        "ts": timestamp_str.strip(),
    }
    payload = json.dumps(payload_data, separators=(",", ":"), sort_keys=True)
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ============================================================================
# Test _verify_internal_token
# ============================================================================


def test_verify_token_validation_disabled(mock_settings_no_validation):
    """Test that validation is skipped when disabled."""
    is_valid, error = _verify_internal_token(
        token=None,
        timestamp_str=None,
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_no_validation,
    )

    assert is_valid is True
    assert error == ""


def test_verify_token_missing_secret(mock_settings_with_validation):
    """Test that missing secret returns error."""
    mock_settings_with_validation.internal_token_secret.get_secret_value.return_value = ""

    is_valid, error = _verify_internal_token(
        token="abc123",
        timestamp_str=str(int(time.time())),
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "token_secret_not_configured"


def test_verify_token_missing_token(mock_settings_with_validation):
    """Test that missing token returns error."""
    is_valid, error = _verify_internal_token(
        token=None,
        timestamp_str=str(int(time.time())),
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "missing_token"


def test_verify_token_missing_timestamp(mock_settings_with_validation):
    """Test that missing timestamp returns error."""
    is_valid, error = _verify_internal_token(
        token="abc123",
        timestamp_str=None,
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "missing_timestamp"


def test_verify_token_invalid_timestamp_format(mock_settings_with_validation):
    """Test that invalid timestamp format returns error."""
    is_valid, error = _verify_internal_token(
        token="abc123",
        timestamp_str="not_a_number",
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "invalid_timestamp_format"


def test_verify_token_expired_timestamp(mock_settings_with_validation):
    """Test that expired timestamp returns error."""
    old_timestamp = str(int(time.time()) - 400)  # 400 seconds ago (tolerance: 300)

    is_valid, error = _verify_internal_token(
        token="abc123",
        timestamp_str=old_timestamp,
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "timestamp_expired"


def test_verify_token_invalid_signature(mock_settings_with_validation):
    """Test that invalid signature returns error."""
    timestamp_str = str(int(time.time()))

    is_valid, error = _verify_internal_token(
        token="invalid_signature_abc123",
        timestamp_str=timestamp_str,
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "invalid_signature"


def test_verify_token_valid_signature(mock_settings_with_validation):
    """Test that valid signature succeeds."""
    timestamp_str = str(int(time.time()))
    user_id = "user123"
    role = "trader"
    strategies = "alpha_baseline,momentum"

    valid_token = create_valid_token(user_id, role, strategies, timestamp_str, "test_secret_key")

    is_valid, error = _verify_internal_token(
        token=valid_token,
        timestamp_str=timestamp_str,
        user_id=user_id,
        role=role,
        strategies=strategies,
        settings=mock_settings_with_validation,
    )

    assert is_valid is True
    assert error == ""


def test_verify_token_signature_case_insensitive(mock_settings_with_validation):
    """Test that signature comparison is case-insensitive."""
    timestamp_str = str(int(time.time()))
    user_id = "user123"
    role = "trader"
    strategies = "alpha_baseline"

    valid_token = create_valid_token(user_id, role, strategies, timestamp_str, "test_secret_key")

    # Submit uppercase version
    is_valid, error = _verify_internal_token(
        token=valid_token.upper(),
        timestamp_str=timestamp_str,
        user_id=user_id,
        role=role,
        strategies=strategies,
        settings=mock_settings_with_validation,
    )

    assert is_valid is True
    assert error == ""


def test_verify_token_strips_whitespace(mock_settings_with_validation):
    """Test that whitespace in inputs is stripped."""
    timestamp_str = str(int(time.time()))
    user_id = "user123"
    role = "trader"
    strategies = "alpha_baseline"

    # Create token with exact values
    valid_token = create_valid_token(user_id, role, strategies, timestamp_str, "test_secret_key")

    # Submit with whitespace
    is_valid, error = _verify_internal_token(
        token=valid_token,
        timestamp_str=f"  {timestamp_str}  ",
        user_id=f"  {user_id}  ",
        role=f"  {role}  ",
        strategies=f"  {strategies}  ",
        settings=mock_settings_with_validation,
    )

    assert is_valid is True
    assert error == ""


# ============================================================================
# Test populate_user_from_headers middleware
# ============================================================================


@pytest.mark.asyncio()
async def test_middleware_no_headers():
    """Test middleware with no user headers."""
    from types import SimpleNamespace

    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.return_value = None
    request_mock.state = SimpleNamespace()  # Use real object instead of MagicMock

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = await populate_user_from_headers(request_mock, call_next_mock)

    assert response == "response"
    call_next_mock.assert_called_once_with(request_mock)
    # request.state.user should not be set
    assert not hasattr(request_mock.state, "user")


@pytest.mark.asyncio()
async def test_middleware_validation_disabled():
    """Test middleware with validation disabled."""
    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "trader",
        "X-User-Id": "user123",
        "X-User-Strategies": "alpha_baseline,momentum",
    }.get(key, default)
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = await populate_user_from_headers(request_mock, call_next_mock)

    assert response == "response"
    assert request_mock.state.user == {
        "role": "trader",
        "user_id": "user123",
        "strategies": ["alpha_baseline", "momentum"],
    }


@pytest.mark.asyncio()
async def test_middleware_validation_enabled_valid_token(mock_settings_with_validation):
    """Test middleware with validation enabled and valid token."""
    timestamp_str = str(int(time.time()))
    user_id = "user123"
    role = "admin"
    strategies = "alpha_baseline"

    valid_token = create_valid_token(user_id, role, strategies, timestamp_str, "test_secret_key")

    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": role,
        "X-User-Id": user_id,
        "X-User-Strategies": strategies,
        "X-User-Signature": valid_token,
        "X-Request-Timestamp": timestamp_str,
    }.get(key, default)
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = mock_settings_with_validation

        response = await populate_user_from_headers(request_mock, call_next_mock)

    assert response == "response"
    assert request_mock.state.user == {
        "role": "admin",
        "user_id": "user123",
        "strategies": ["alpha_baseline"],
    }


@pytest.mark.asyncio()
async def test_middleware_validation_enabled_invalid_token(mock_settings_with_validation):
    """Test middleware with validation enabled and invalid token."""
    timestamp_str = str(int(time.time()))

    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "trader",
        "X-User-Id": "user123",
        "X-User-Strategies": "alpha_baseline",
        "X-User-Signature": "invalid_token",
        "X-Request-Timestamp": timestamp_str,
    }.get(key, default)
    request_mock.url.path = "/api/v1/test"
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = mock_settings_with_validation

        response = await populate_user_from_headers(request_mock, call_next_mock)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    call_next_mock.assert_not_called()


@pytest.mark.asyncio()
async def test_middleware_empty_strategies():
    """Test middleware with empty strategies header."""
    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "viewer",
        "X-User-Id": "user456",
        "X-User-Strategies": "",
    }.get(key, default)
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        await populate_user_from_headers(request_mock, call_next_mock)

    assert request_mock.state.user == {
        "role": "viewer",
        "user_id": "user456",
        "strategies": [],  # Empty list
    }


@pytest.mark.asyncio()
async def test_middleware_strategies_with_whitespace():
    """Test middleware strips whitespace from strategies."""
    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "trader",
        "X-User-Id": "user123",
        "X-User-Strategies": "  alpha_baseline  ,  momentum  ,  ",
    }.get(key, default)
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        await populate_user_from_headers(request_mock, call_next_mock)

    assert request_mock.state.user["strategies"] == ["alpha_baseline", "momentum"]


def test_verify_token_future_timestamp(mock_settings_with_validation):
    """Test that future timestamp outside tolerance returns error."""
    # 400 seconds in the future (tolerance: 300)
    future_timestamp = str(int(time.time()) + 400)

    is_valid, error = _verify_internal_token(
        token="abc123",
        timestamp_str=future_timestamp,
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "timestamp_expired"


def test_verify_token_within_tolerance_boundary(mock_settings_with_validation):
    """Test that timestamp at exactly tolerance boundary is valid."""
    # Exactly at 300 second boundary (should be valid)
    boundary_timestamp = str(int(time.time()) - 300)
    user_id = "user123"
    role = "trader"
    strategies = "alpha_baseline"

    valid_token = create_valid_token(
        user_id, role, strategies, boundary_timestamp, "test_secret_key"
    )

    is_valid, error = _verify_internal_token(
        token=valid_token,
        timestamp_str=boundary_timestamp,
        user_id=user_id,
        role=role,
        strategies=strategies,
        settings=mock_settings_with_validation,
    )

    assert is_valid is True
    assert error == ""


@pytest.mark.asyncio()
async def test_middleware_missing_user_id_with_role():
    """Test middleware when role is present but user_id is missing."""
    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "trader",
        "X-User-Id": None,  # Missing user_id
        "X-User-Strategies": "alpha_baseline",
    }.get(key, default)
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = await populate_user_from_headers(request_mock, call_next_mock)

    # Should pass through without setting user (role AND user_id required)
    assert response == "response"
    call_next_mock.assert_called_once()


@pytest.mark.asyncio()
async def test_middleware_missing_role_with_user_id():
    """Test middleware when user_id is present but role is missing."""
    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": None,  # Missing role
        "X-User-Id": "user123",
        "X-User-Strategies": "alpha_baseline",
    }.get(key, default)
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = await populate_user_from_headers(request_mock, call_next_mock)

    # Should pass through without setting user (role AND user_id required)
    assert response == "response"
    call_next_mock.assert_called_once()


@pytest.mark.asyncio()
async def test_middleware_missing_timestamp_with_validation(mock_settings_with_validation):
    """Test middleware returns 401 when timestamp is missing but validation enabled."""
    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "trader",
        "X-User-Id": "user123",
        "X-User-Strategies": "alpha_baseline",
        "X-User-Signature": "some_signature",
        "X-Request-Timestamp": None,  # Missing timestamp
    }.get(key, default)
    request_mock.url.path = "/api/v1/test"
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = mock_settings_with_validation

        response = await populate_user_from_headers(request_mock, call_next_mock)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    call_next_mock.assert_not_called()


@pytest.mark.asyncio()
async def test_middleware_missing_signature_with_validation(mock_settings_with_validation):
    """Test middleware returns 401 when signature is missing but validation enabled."""
    timestamp_str = str(int(time.time()))

    request_mock = MagicMock(spec=Request)
    request_mock.headers.get.side_effect = lambda key, default=None: {
        "X-User-Role": "trader",
        "X-User-Id": "user123",
        "X-User-Strategies": "alpha_baseline",
        "X-User-Signature": None,  # Missing signature
        "X-Request-Timestamp": timestamp_str,
    }.get(key, default)
    request_mock.url.path = "/api/v1/test"
    request_mock.state = MagicMock()

    call_next_mock = AsyncMock(return_value="response")

    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = mock_settings_with_validation

        response = await populate_user_from_headers(request_mock, call_next_mock)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    call_next_mock.assert_not_called()


def test_verify_token_logs_warning_on_mismatch(mock_settings_with_validation, caplog):
    """Test that signature mismatch is logged with appropriate details."""
    import logging

    timestamp_str = str(int(time.time()))

    with caplog.at_level(logging.WARNING):
        is_valid, error = _verify_internal_token(
            token="invalid_signature_12345",
            timestamp_str=timestamp_str,
            user_id="user123",
            role="trader",
            strategies="alpha_baseline",
            settings=mock_settings_with_validation,
        )

    assert is_valid is False
    assert error == "invalid_signature"
    assert "signature mismatch" in caplog.text


def test_verify_token_logs_warning_on_timestamp_expired(mock_settings_with_validation, caplog):
    """Test that expired timestamp is logged with skew details."""
    import logging

    old_timestamp = str(int(time.time()) - 400)

    with caplog.at_level(logging.WARNING):
        is_valid, error = _verify_internal_token(
            token="some_token",
            timestamp_str=old_timestamp,
            user_id="user123",
            role="trader",
            strategies="alpha_baseline",
            settings=mock_settings_with_validation,
        )

    assert is_valid is False
    assert error == "timestamp_expired"
    assert "outside tolerance" in caplog.text


def test_verify_token_empty_token_string(mock_settings_with_validation):
    """Test that empty string token returns missing_token error."""
    timestamp_str = str(int(time.time()))

    is_valid, error = _verify_internal_token(
        token="",  # Empty string
        timestamp_str=timestamp_str,
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "missing_token"


def test_verify_token_empty_timestamp_string(mock_settings_with_validation):
    """Test that empty string timestamp returns missing_timestamp error."""
    is_valid, error = _verify_internal_token(
        token="some_token",
        timestamp_str="",  # Empty string
        user_id="user123",
        role="trader",
        strategies="alpha_baseline",
        settings=mock_settings_with_validation,
    )

    assert is_valid is False
    assert error == "missing_timestamp"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
