from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis
from cryptography.fernet import Fernet
from fakeredis.aioredis import FakeRedis

from apps.web_console_ng.auth.audit import AuthAuditLogger
from apps.web_console_ng.auth.session_store import (
    RateLimitExceeded,
    ServerSessionStore,
    SessionCreationError,
    SessionValidationError,
    _get_user_id,
    _ip_subnet,
    _normalize_fernet_key,
    extract_session_id,
    get_session_store,
)


def _make_store(redis_client: FakeRedis) -> ServerSessionStore:
    return ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=[Fernet.generate_key()],
        signing_keys={"01": b"a" * 32, "02": b"b" * 32},
        current_signing_key_id="01",
        redis_client=redis_client,
    )


@pytest.fixture()
def redis_client() -> FakeRedis:
    return FakeRedis()


@pytest.fixture()
def session_store(redis_client: FakeRedis) -> ServerSessionStore:
    return _make_store(redis_client)


@pytest.mark.asyncio()
async def test_verify_cookie_and_extract_session_id(session_store: ServerSessionStore) -> None:
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    session_id = session_store.verify_cookie(cookie_value)
    assert session_id is not None
    assert extract_session_id(cookie_value) == session_id

    tampered = cookie_value.replace("01:", "02:")
    assert session_store.verify_cookie(tampered) is None


@pytest.mark.asyncio()
async def test_validate_session_bad_signature_returns_none(
    session_store: ServerSessionStore,
) -> None:
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    tampered = cookie_value[:-1] + ("0" if cookie_value[-1] != "0" else "1")
    assert await session_store.validate_session(tampered, "10.0.0.1", "ua") is None


@pytest.mark.asyncio()
async def test_validate_session_json_decode_error_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    session_id = "session-123"
    cookie_value = session_store._build_cookie_value(session_id)

    encrypted = session_store.fernet.encrypt(b"not-json")
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, encrypted)

    assert await session_store.validate_session(cookie_value, "10.0.0.1", "ua") is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_check_rate_limit_fallback_when_eval_missing(
    session_store: ServerSessionStore,
) -> None:
    session_store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    allowed = await session_store._check_rate_limit("10.0.0.9", "validate", 1)
    blocked = await session_store._check_rate_limit("10.0.0.9", "validate", 1)

    assert allowed is True
    assert blocked is False


@pytest.mark.asyncio()
async def test_validate_session_redis_error_raises(session_store: ServerSessionStore) -> None:
    session_store.redis.get = AsyncMock(side_effect=redis.RedisError("boom"))

    cookie_value = session_store._build_cookie_value("session-xyz")
    with pytest.raises(SessionValidationError):
        await session_store.validate_session(cookie_value, "10.0.0.1", "ua")


@pytest.mark.asyncio()
async def test_rotate_session_fallback_when_eval_missing(
    session_store: ServerSessionStore,
) -> None:
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    session_store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    rotated = await session_store.rotate_session(session_id)
    assert rotated is not None
    new_cookie, new_csrf = rotated
    assert new_cookie != cookie_value
    assert new_csrf

    assert await session_store.redis.get(f"{session_store.session_prefix}{session_id}") is None


def test_extract_session_id_handles_dots() -> None:
    signed_cookie = "part1.part2.part3.01:signature"
    assert extract_session_id(signed_cookie) == "part1.part2.part3"


def test_extract_session_id_invalid_cookie_raises() -> None:
    with pytest.raises(ValueError, match="Empty cookie"):
        extract_session_id("")
    with pytest.raises(ValueError, match="missing signature"):
        extract_session_id("missing-signature")
    with pytest.raises(ValueError, match="empty session ID"):
        extract_session_id(".01:signature")


# =============================================================================
# Test rate limit exceeded during session creation
# =============================================================================


@pytest.fixture()
def store_with_audit(redis_client: FakeRedis) -> ServerSessionStore:
    """Create a session store with a mock audit logger."""
    audit_logger = MagicMock(spec=AuthAuditLogger)
    return ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=[Fernet.generate_key()],
        signing_keys={"01": b"a" * 32, "02": b"b" * 32},
        current_signing_key_id="01",
        redis_client=redis_client,
        audit_logger=audit_logger,
    )


@pytest.mark.asyncio()
async def test_create_session_rate_limit_exceeded(store_with_audit: ServerSessionStore) -> None:
    """Test that session creation rate limit raises RateLimitExceeded with audit log."""
    # Simulate rate limit exceeded by returning a high count
    store_with_audit.redis.eval = AsyncMock(return_value=100)

    with pytest.raises(RateLimitExceeded, match="Session creation rate limit exceeded"):
        await store_with_audit.create_session(
            {"user_id": "user-1"},
            {"user_agent": "ua"},
            "10.0.0.1",
        )

    # Verify audit logger was called
    store_with_audit.audit_logger.log_event.assert_called_once()
    call_kwargs = store_with_audit.audit_logger.log_event.call_args[1]
    assert call_kwargs["event_type"] == "rate_limit_exceeded"
    assert call_kwargs["failure_reason"] == "create_rate_limit"


@pytest.mark.asyncio()
async def test_create_session_redis_error_raises(session_store: ServerSessionStore) -> None:
    """Test that Redis error during session creation raises SessionCreationError."""
    session_store.redis.setex = AsyncMock(side_effect=redis.RedisError("connection failed"))

    with pytest.raises(SessionCreationError, match="Session creation failed"):
        await session_store.create_session(
            {"user_id": "user-1"},
            {"user_agent": "ua"},
            "10.0.0.1",
        )


# =============================================================================
# Test session validation edge cases
# =============================================================================


@pytest.mark.asyncio()
async def test_validate_session_rate_limit_exceeded(store_with_audit: ServerSessionStore) -> None:
    """Test that validation rate limit returns None with audit log."""
    # First create a session successfully
    store_with_audit.redis.eval = AsyncMock(return_value=1)  # Allow creation
    cookie_value, _ = await store_with_audit.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    # Now simulate rate limit exceeded for validation
    store_with_audit.redis.eval = AsyncMock(return_value=200)

    result = await store_with_audit.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None

    # Verify audit logger was called for rate limit
    calls = store_with_audit.audit_logger.log_event.call_args_list
    rate_limit_calls = [c for c in calls if c[1].get("event_type") == "rate_limit_exceeded"]
    assert len(rate_limit_calls) == 1
    assert rate_limit_calls[0][1]["failure_reason"] == "validate_rate_limit"


@pytest.mark.asyncio()
async def test_validate_session_malformed_cookie(store_with_audit: ServerSessionStore) -> None:
    """Test that malformed cookie returns None with audit log."""
    # Cookie without proper format (no dot separator)
    result = await store_with_audit.validate_session("malformed-cookie", "10.0.0.1", "ua")
    assert result is None

    # Verify audit log for malformed cookie
    call_kwargs = store_with_audit.audit_logger.log_event.call_args[1]
    assert call_kwargs["event_type"] == "session_validation_failure"
    assert call_kwargs["failure_reason"] == "malformed_cookie"


@pytest.mark.asyncio()
async def test_validate_session_missing_key_sig(store_with_audit: ServerSessionStore) -> None:
    """Test that cookie with dot but no colon in sig returns None."""
    # Cookie with dot but missing colon in key_sig portion
    result = await store_with_audit.validate_session("session-id.no-colon-here", "10.0.0.1", "ua")
    assert result is None

    call_kwargs = store_with_audit.audit_logger.log_event.call_args[1]
    assert call_kwargs["failure_reason"] == "malformed_cookie"


@pytest.mark.asyncio()
async def test_validate_session_invalid_signature(store_with_audit: ServerSessionStore) -> None:
    """Test that invalid signature returns None with audit log."""
    # Valid format but wrong signature
    result = await store_with_audit.validate_session(
        "session-id.01:invalid-signature", "10.0.0.1", "ua"
    )
    assert result is None

    call_kwargs = store_with_audit.audit_logger.log_event.call_args[1]
    assert call_kwargs["event_type"] == "session_validation_failure"
    assert call_kwargs["failure_reason"] == "invalid_signature"


@pytest.mark.asyncio()
async def test_validate_session_decrypt_error_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that decryption TypeError/AttributeError invalidates session."""
    session_id = "session-decrypt-error"
    cookie_value = session_store._build_cookie_value(session_id)

    # Store data that will cause decryption to fail with TypeError
    # Use bytes that look like encrypted data but will fail
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, b"invalid-data")

    # Mock fernet.decrypt to raise TypeError
    original_decrypt = session_store.fernet.decrypt
    session_store.fernet.decrypt = MagicMock(side_effect=TypeError("mock decrypt error"))

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None

    session_store.fernet.decrypt = original_decrypt


@pytest.mark.asyncio()
async def test_validate_session_decrypt_attribute_error(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that AttributeError during decryption invalidates session."""
    session_id = "session-attr-error"
    cookie_value = session_store._build_cookie_value(session_id)

    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, b"invalid-data")

    session_store.fernet.decrypt = MagicMock(side_effect=AttributeError("mock error"))

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_validate_session_non_dict_payload_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that non-dict session payload invalidates session."""
    session_id = "session-non-dict"
    cookie_value = session_store._build_cookie_value(session_id)

    # Encrypt a valid JSON but not a dict (e.g., a list)
    encrypted = session_store.fernet.encrypt(json.dumps([1, 2, 3]).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, encrypted)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_validate_session_corrupt_timestamps_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that corrupt timestamp fields invalidate session."""
    session_id = "session-corrupt-ts"
    cookie_value = session_store._build_cookie_value(session_id)

    # Create session with invalid timestamp format
    session_data = {
        "session_id": session_id,
        "user": {"user_id": "user-1"},
        "csrf_token": "token",
        "created_at": "not-a-valid-timestamp",
        "last_activity": datetime.now(UTC).isoformat(),
        "device": {},
    }
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, encrypted)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_validate_session_missing_timestamps_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that missing timestamp fields invalidate session."""
    session_id = "session-missing-ts"
    cookie_value = session_store._build_cookie_value(session_id)

    # Create session without required fields
    session_data = {
        "session_id": session_id,
        "user": {"user_id": "user-1"},
        "csrf_token": "token",
        # Missing created_at and last_activity
        "device": {},
    }
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 60, encrypted)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None


@pytest.mark.asyncio()
async def test_validate_session_absolute_timeout_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that session exceeding absolute timeout is invalidated."""
    session_id = "session-abs-timeout"
    cookie_value = session_store._build_cookie_value(session_id)

    # Create session that was created long ago (exceeds absolute timeout)
    old_time = datetime.now(UTC) - timedelta(hours=100)
    session_data = {
        "session_id": session_id,
        "user": {"user_id": "user-1"},
        "csrf_token": "token",
        "created_at": old_time.isoformat(),
        "last_activity": datetime.now(UTC).isoformat(),
        "device": {},
    }
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 3600, encrypted)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_validate_session_idle_timeout_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that session exceeding idle timeout is invalidated."""
    session_id = "session-idle-timeout"
    cookie_value = session_store._build_cookie_value(session_id)

    now = datetime.now(UTC)
    # Last activity was long ago (exceeds idle timeout)
    old_activity = now - timedelta(hours=2)
    session_data = {
        "session_id": session_id,
        "user": {"user_id": "user-1"},
        "csrf_token": "token",
        "created_at": now.isoformat(),
        "last_activity": old_activity.isoformat(),
        "device": {},
    }
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 3600, encrypted)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_validate_session_device_binding_mismatch(redis_client: FakeRedis) -> None:
    """Test that device binding mismatch invalidates session."""
    store = ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=[Fernet.generate_key()],
        signing_keys={"01": b"a" * 32},
        current_signing_key_id="01",
        redis_client=redis_client,
    )

    # Create session with specific device info
    with patch("apps.web_console_ng.auth.session_store.config") as mock_config:
        mock_config.SESSION_IDLE_TIMEOUT_MINUTES = 15
        mock_config.SESSION_ABSOLUTE_TIMEOUT_HOURS = 4
        mock_config.DEVICE_BINDING_ENABLED = True
        mock_config.DEVICE_BINDING_SUBNET_MASK = 24
        mock_config.AUTH_TYPE = "basic"

        cookie_value, _ = await store.create_session(
            {"user_id": "user-1"},
            {"user_agent": "Mozilla/5.0"},
            "192.168.1.100",
        )

        # Try to validate from different device (different IP subnet and user agent)
        result = await store.validate_session(cookie_value, "10.0.0.50", "Different-Agent")
        assert result is None


@pytest.mark.asyncio()
async def test_validate_session_remaining_ttl_zero_invalidates(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that session with zero remaining TTL is invalidated."""
    session_id = "session-ttl-zero"
    cookie_value = session_store._build_cookie_value(session_id)

    # Create session where age exactly equals absolute timeout
    now = datetime.now(UTC)
    created_at = now - timedelta(seconds=session_store.absolute_timeout)
    session_data = {
        "session_id": session_id,
        "user": {"user_id": "user-1"},
        "csrf_token": "token",
        "created_at": created_at.isoformat(),
        "last_activity": now.isoformat(),
        "device": {},
    }
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 3600, encrypted)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None


@pytest.mark.asyncio()
async def test_validate_session_success_updates_last_activity(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that successful validation updates last_activity."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )

    # Validate and check activity is updated
    with patch("apps.web_console_ng.auth.session_store.config") as mock_config:
        mock_config.SESSION_IDLE_TIMEOUT_MINUTES = 15
        mock_config.SESSION_ABSOLUTE_TIMEOUT_HOURS = 4
        mock_config.DEVICE_BINDING_ENABLED = False
        mock_config.AUTH_TYPE = "basic"

        result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
        assert result is not None
        assert "last_activity" in result


# =============================================================================
# Test session rotation edge cases
# =============================================================================


@pytest.mark.asyncio()
async def test_rotate_session_not_found(session_store: ServerSessionStore) -> None:
    """Test that rotating non-existent session returns None."""
    result = await session_store.rotate_session("non-existent-session")
    assert result is None


@pytest.mark.asyncio()
async def test_rotate_session_expired_ttl(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that rotating session with expired TTL returns None."""
    session_id = "session-expired"

    # Create session that's already expired (created long ago)
    old_time = datetime.now(UTC) - timedelta(hours=100)
    session_data = {
        "session_id": session_id,
        "user": {"user_id": "user-1"},
        "csrf_token": "token",
        "created_at": old_time.isoformat(),
        "issued_at": old_time.isoformat(),
        "last_activity": old_time.isoformat(),
        "device": {},
    }
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 3600, encrypted)

    result = await session_store.rotate_session(session_id)
    assert result is None
    # Old session should be deleted
    assert await redis_client.get(f"{session_store.session_prefix}{session_id}") is None


@pytest.mark.asyncio()
async def test_rotate_session_with_user_updates(session_store: ServerSessionStore) -> None:
    """Test that rotation applies user updates correctly."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1", "mfa_pending": True},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Rotate with user updates to clear mfa_pending
    result = await session_store.rotate_session(session_id, user_updates={"mfa_pending": False})
    assert result is not None
    new_cookie, new_csrf = result

    # Validate new session and check user updates were applied
    with patch("apps.web_console_ng.auth.session_store.config") as mock_config:
        mock_config.SESSION_IDLE_TIMEOUT_MINUTES = 15
        mock_config.SESSION_ABSOLUTE_TIMEOUT_HOURS = 4
        mock_config.DEVICE_BINDING_ENABLED = False
        mock_config.AUTH_TYPE = "basic"

        session = await session_store.validate_session(new_cookie, "10.0.0.1", "ua")
        assert session is not None
        assert session["user"]["mfa_pending"] is False


@pytest.mark.asyncio()
async def test_rotate_session_lua_returns_none(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that Lua script returning None aborts rotation."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Mock eval to return None (simulating race condition where old session deleted)
    session_store.redis.eval = AsyncMock(return_value=None)

    result = await session_store.rotate_session(session_id)
    assert result is None


@pytest.mark.asyncio()
async def test_rotate_session_redis_error(session_store: ServerSessionStore) -> None:
    """Test that Redis error during rotation returns None."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Mock redis.get to raise error during rotation
    session_store.redis.get = AsyncMock(side_effect=redis.RedisError("connection lost"))

    result = await session_store.rotate_session(session_id)
    assert result is None


@pytest.mark.asyncio()
async def test_rotate_session_data_corruption(
    session_store: ServerSessionStore,
    redis_client: FakeRedis,
) -> None:
    """Test that data corruption during rotation returns None."""
    session_id = "session-corrupt"

    # Store corrupted session data (missing required fields)
    session_data = {"invalid": "data"}
    encrypted = session_store.fernet.encrypt(json.dumps(session_data).encode())
    await redis_client.setex(f"{session_store.session_prefix}{session_id}", 3600, encrypted)

    result = await session_store.rotate_session(session_id)
    assert result is None


@pytest.mark.asyncio()
async def test_rotate_session_with_audit_logging(redis_client: FakeRedis) -> None:
    """Test that successful rotation logs audit event."""
    audit_logger = MagicMock(spec=AuthAuditLogger)
    store = ServerSessionStore(
        redis_url="redis://localhost:6379/1",
        encryption_keys=[Fernet.generate_key()],
        signing_keys={"01": b"a" * 32},
        current_signing_key_id="01",
        redis_client=redis_client,
        audit_logger=audit_logger,
    )

    cookie_value, _ = await store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Clear any previous calls from create_session
    audit_logger.reset_mock()

    # Use fallback path (no Lua) to ensure rotation succeeds
    store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    result = await store.rotate_session(session_id)
    assert result is not None

    # Verify audit log for rotation
    audit_logger.log_event.assert_called_once()
    call_kwargs = audit_logger.log_event.call_args[1]
    assert call_kwargs["event_type"] == "session_rotation"
    assert call_kwargs["outcome"] == "success"


# =============================================================================
# Test rate limit fallback edge cases
# =============================================================================


@pytest.mark.asyncio()
async def test_rate_limit_fallback_non_eval_error_raises(
    session_store: ServerSessionStore,
) -> None:
    """Test that non-EVAL Redis errors during rate limit check are raised."""
    session_store.redis.eval = AsyncMock(side_effect=redis.RedisError("connection refused"))

    with pytest.raises(redis.RedisError, match="connection refused"):
        await session_store._check_rate_limit("10.0.0.1", "validate", 100)


@pytest.mark.asyncio()
async def test_rate_limit_fallback_expire_call(redis_client: FakeRedis) -> None:
    """Test that fallback path sets expire correctly for first request."""
    store = _make_store(redis_client)

    # Simulate EVAL not supported
    store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    # First call should increment and set expire
    result = await store._check_rate_limit("10.0.0.5", "test-action", 10)
    assert result is True

    # Verify key was set with TTL
    key = f"{store.rate_limit_prefix}test-action:10.0.0.5"
    ttl = await redis_client.ttl(key)
    assert ttl > 0


# =============================================================================
# Test signature verification edge cases
# =============================================================================


def test_verify_signature_missing_colon(session_store: ServerSessionStore) -> None:
    """Test that signature without colon fails verification."""
    result = session_store._verify_signature("session-id", "no-colon-here")
    assert result is False


def test_verify_signature_unknown_key_id(session_store: ServerSessionStore) -> None:
    """Test that unknown key_id fails verification."""
    result = session_store._verify_signature("session-id", "99:somesignature")
    assert result is False


def test_verify_signature_mismatch(session_store: ServerSessionStore) -> None:
    """Test that signature mismatch fails verification."""
    result = session_store._verify_signature("session-id", "01:wrongsignature")
    assert result is False


def test_parse_cookie_no_dot() -> None:
    """Test that cookie without dot returns None."""
    store = _make_store(FakeRedis())
    session_id, key_sig = store._parse_cookie("no-dot-here")
    assert session_id is None
    assert key_sig is None


def test_parse_cookie_no_colon_in_sig() -> None:
    """Test that cookie with dot but no colon in sig returns None."""
    store = _make_store(FakeRedis())
    session_id, key_sig = store._parse_cookie("session.nocolon")
    assert session_id is None
    assert key_sig is None


def test_verify_cookie_invalid(session_store: ServerSessionStore) -> None:
    """Test verify_cookie with invalid cookie returns None."""
    assert session_store.verify_cookie("invalid-cookie") is None
    assert session_store.verify_cookie("session.nocolon") is None
    assert session_store.verify_cookie("session.01:wrongsig") is None


# =============================================================================
# Test helper functions
# =============================================================================


def test_normalize_fernet_key_44_bytes() -> None:
    """Test that 44-byte key is returned as-is."""
    key = Fernet.generate_key()  # 44 bytes
    assert len(key) == 44
    result = _normalize_fernet_key(key)
    assert result == key


def test_normalize_fernet_key_32_bytes() -> None:
    """Test that 32-byte key is base64 encoded."""
    key = b"a" * 32
    result = _normalize_fernet_key(key)
    assert len(result) == 44  # Base64 encoded


def test_normalize_fernet_key_other_length() -> None:
    """Test that other length keys are base64 encoded."""
    key = b"short"
    result = _normalize_fernet_key(key)
    # Should be base64 encoded
    assert result != key


def test_ip_subnet_valid_ipv4() -> None:
    """Test IP subnet calculation for valid IPv4."""
    result = _ip_subnet("192.168.1.100", 24)
    assert result == "192.168.1.0/24"


def test_ip_subnet_valid_ipv6() -> None:
    """Test IP subnet calculation for valid IPv6."""
    result = _ip_subnet("2001:db8::1", 64)
    assert "2001:db8::" in result


def test_ip_subnet_invalid_ip() -> None:
    """Test that invalid IP returns the original string."""
    result = _ip_subnet("not-an-ip", 24)
    assert result == "not-an-ip"


def test_ip_subnet_empty_ip() -> None:
    """Test that empty IP returns empty string."""
    result = _ip_subnet("", 24)
    assert result == ""


def test_get_user_id_valid_session() -> None:
    """Test _get_user_id with valid session."""
    session = {"user": {"user_id": "test-user-123"}}
    assert _get_user_id(session) == "test-user-123"


def test_get_user_id_no_user() -> None:
    """Test _get_user_id with no user field."""
    session: dict = {}
    assert _get_user_id(session) is None


def test_get_user_id_user_not_dict() -> None:
    """Test _get_user_id when user is not a dict."""
    session = {"user": "not-a-dict"}
    assert _get_user_id(session) is None


def test_get_user_id_no_user_id() -> None:
    """Test _get_user_id when user dict has no user_id."""
    session = {"user": {"name": "test"}}
    assert _get_user_id(session) is None


# =============================================================================
# Test get_session_store factory
# =============================================================================


def test_get_session_store_creates_singleton() -> None:
    """Test that get_session_store creates and reuses singleton."""
    import apps.web_console_ng.auth.session_store as store_module

    # Reset singleton
    original_store = store_module._store
    store_module._store = None

    try:
        with patch.object(store_module, "get_redis_store") as mock_redis_store, patch.object(
            store_module, "config"
        ) as mock_config:
            mock_redis_client = MagicMock()
            mock_redis_store.return_value.get_master_client.return_value = mock_redis_client
            mock_config.REDIS_URL = "redis://localhost:6379/1"
            mock_config.get_encryption_keys.return_value = [Fernet.generate_key()]
            mock_config.get_signing_keys.return_value = {"01": b"a" * 32}
            mock_config.HMAC_CURRENT_KEY_ID = "01"
            mock_config.SESSION_IDLE_TIMEOUT_MINUTES = 15
            mock_config.SESSION_ABSOLUTE_TIMEOUT_HOURS = 4
            mock_config.AUDIT_LOG_DB_ENABLED = False

            store1 = get_session_store()
            store2 = get_session_store()

            # Should be same instance
            assert store1 is store2
    finally:
        store_module._store = original_store


def test_get_session_store_with_audit_logger() -> None:
    """Test that get_session_store accepts and uses audit logger."""
    import apps.web_console_ng.auth.session_store as store_module

    original_store = store_module._store
    store_module._store = None

    try:
        with patch.object(store_module, "get_redis_store") as mock_redis_store, patch.object(
            store_module, "config"
        ) as mock_config:
            mock_redis_client = MagicMock()
            mock_redis_store.return_value.get_master_client.return_value = mock_redis_client
            mock_config.REDIS_URL = "redis://localhost:6379/1"
            mock_config.get_encryption_keys.return_value = [Fernet.generate_key()]
            mock_config.get_signing_keys.return_value = {"01": b"a" * 32}
            mock_config.HMAC_CURRENT_KEY_ID = None  # Test fallback to first key
            mock_config.SESSION_IDLE_TIMEOUT_MINUTES = 15
            mock_config.SESSION_ABSOLUTE_TIMEOUT_HOURS = 4
            mock_config.AUDIT_LOG_DB_ENABLED = False

            audit_logger = MagicMock(spec=AuthAuditLogger)
            store = get_session_store(audit_logger)

            assert store.audit_logger is audit_logger
    finally:
        store_module._store = original_store


def test_get_session_store_update_audit_logger() -> None:
    """Test that passing audit_logger updates existing store if it has none."""
    import apps.web_console_ng.auth.session_store as store_module

    original_store = store_module._store
    store_module._store = None

    try:
        with patch.object(store_module, "get_redis_store") as mock_redis_store, patch.object(
            store_module, "config"
        ) as mock_config:
            mock_redis_client = MagicMock()
            mock_redis_store.return_value.get_master_client.return_value = mock_redis_client
            mock_config.REDIS_URL = "redis://localhost:6379/1"
            mock_config.get_encryption_keys.return_value = [Fernet.generate_key()]
            mock_config.get_signing_keys.return_value = {"01": b"a" * 32}
            mock_config.HMAC_CURRENT_KEY_ID = "01"
            mock_config.SESSION_IDLE_TIMEOUT_MINUTES = 15
            mock_config.SESSION_ABSOLUTE_TIMEOUT_HOURS = 4
            mock_config.AUDIT_LOG_DB_ENABLED = False

            # First call without audit logger
            store1 = get_session_store()
            assert store1.audit_logger is not None  # Default logger

            # Store already exists, set audit_logger to None to test update path
            store1.audit_logger = None

            # Second call with audit logger should update
            audit_logger = MagicMock(spec=AuthAuditLogger)
            store2 = get_session_store(audit_logger)

            assert store1 is store2
            assert store2.audit_logger is audit_logger
    finally:
        store_module._store = original_store


# =============================================================================
# Test audit failure helper
# =============================================================================


def test_audit_failure_without_logger(session_store: ServerSessionStore) -> None:
    """Test that _audit_failure does nothing without logger."""
    # session_store has no audit_logger
    assert session_store.audit_logger is None
    # Should not raise
    session_store._audit_failure(
        "test_event", "user-1", "session-1", "10.0.0.1", "ua", "test_reason"
    )


def test_audit_failure_with_logger(store_with_audit: ServerSessionStore) -> None:
    """Test that _audit_failure logs with logger."""
    store_with_audit._audit_failure(
        "test_event", "user-1", "session-1", "10.0.0.1", "ua", "test_reason"
    )

    store_with_audit.audit_logger.log_event.assert_called_once()
    call_kwargs = store_with_audit.audit_logger.log_event.call_args[1]
    assert call_kwargs["event_type"] == "test_event"
    assert call_kwargs["user_id"] == "user-1"
    assert call_kwargs["session_id"] == "session-1"
    assert call_kwargs["failure_reason"] == "test_reason"


# =============================================================================
# Additional edge cases for complete coverage
# =============================================================================


@pytest.mark.asyncio()
async def test_validate_session_not_found_in_redis(session_store: ServerSessionStore) -> None:
    """Test that session not found in Redis returns None."""
    # Build a valid cookie but don't create the session
    session_id = "session-not-exists"
    cookie_value = session_store._build_cookie_value(session_id)

    result = await session_store.validate_session(cookie_value, "10.0.0.1", "ua")
    assert result is None


@pytest.mark.asyncio()
async def test_rotate_session_redis_error_during_eval(
    session_store: ServerSessionStore,
) -> None:
    """Test that non-EVAL Redis error during rotation re-raises."""
    cookie_value, _ = await session_store.create_session(
        {"user_id": "user-1"},
        {"user_agent": "ua"},
        "10.0.0.1",
    )
    session_id = extract_session_id(cookie_value)

    # Mock eval to raise a non-"unknown command" error
    session_store.redis.eval = AsyncMock(side_effect=redis.RedisError("connection timeout"))

    # Should return None (Redis errors during rotation are caught)
    result = await session_store.rotate_session(session_id)
    assert result is None


@pytest.mark.asyncio()
async def test_rate_limit_production_warning(redis_client: FakeRedis) -> None:
    """Test that production mode logs warning when EVAL fallback is used."""
    store = _make_store(redis_client)
    store.redis.eval = AsyncMock(side_effect=redis.RedisError("unknown command"))

    with patch("apps.web_console_ng.auth.session_store.config") as mock_config, patch(
        "apps.web_console_ng.auth.session_store.logger"
    ) as mock_logger:
        mock_config.DEBUG = False

        await store._check_rate_limit("10.0.0.1", "test", 100)

        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        assert "Rate limit fallback" in mock_logger.warning.call_args[0][0]
