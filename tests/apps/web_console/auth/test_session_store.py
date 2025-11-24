"""Tests for Redis session store (Component 1, P2T3 Phase 3).

Tests the Redis-backed OAuth2 session storage with AES-256-GCM encryption including:
- Session creation and retrieval
- Encryption/decryption with primary and secondary keys
- Idle timeout enforcement
- Session deletion
- Bulk session cleanup

References:
- apps/web_console/auth/session_store.py
- docs/TASKS/P2T3-Phase3_Component1_Plan.md (Task 5)
- docs/ARCHITECTURE/redis-session-schema.md
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from apps.web_console.auth.session_store import (
    RedisSessionStore,
    SessionData,
)


@pytest.fixture()
def encryption_key():
    """Generate 32-byte encryption key."""
    return os.urandom(32)


@pytest.fixture()
def secondary_key():
    """Generate secondary 32-byte encryption key for rotation."""
    return os.urandom(32)


@pytest.fixture()
def redis_mock():
    """Create mock Redis client."""
    mock = AsyncMock()
    mock.setex = AsyncMock()
    mock.get = AsyncMock()
    mock.delete = AsyncMock(return_value=1)
    mock.scan = AsyncMock()
    return mock


@pytest.fixture()
def session_store(redis_mock, encryption_key):
    """Create Redis session store instance."""
    return RedisSessionStore(
        redis_client=redis_mock,
        encryption_key=encryption_key,
        absolute_timeout_hours=4,
        idle_timeout_minutes=15,
    )


@pytest.fixture()
def sample_session_data():
    """Create sample session data."""
    now = datetime.now(UTC)
    return SessionData(
        access_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.test_access",
        refresh_token="v1.MR.test_refresh",
        id_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.test_id",
        user_id="auth0|12345",
        email="trader@example.com",
        created_at=now,
        last_activity=now,
        ip_address="192.168.1.100",
        user_agent="Mozilla/5.0 (Test)",
    )


def test_session_store_init_valid_key(redis_mock, encryption_key):
    """Test session store initialization with valid 32-byte key."""
    store = RedisSessionStore(redis_mock, encryption_key)
    assert store.redis == redis_mock
    assert store.absolute_timeout == timedelta(hours=4)
    assert store.idle_timeout == timedelta(minutes=15)


def test_session_store_init_invalid_key_length(redis_mock):
    """Test session store initialization fails with invalid key length."""
    invalid_key = os.urandom(16)  # Only 16 bytes instead of 32
    with pytest.raises(ValueError, match="Encryption key must be exactly 32 bytes"):
        RedisSessionStore(redis_mock, invalid_key)


def test_session_store_init_invalid_secondary_key(redis_mock, encryption_key):
    """Test session store initialization fails with invalid secondary key length."""
    invalid_secondary = os.urandom(24)  # Only 24 bytes instead of 32
    with pytest.raises(ValueError, match="Secondary key must be exactly 32 bytes"):
        RedisSessionStore(redis_mock, encryption_key, secondary_key=invalid_secondary)


def test_encrypt_decrypt_round_trip(session_store):
    """Test encryption and decryption round-trip."""
    plaintext = "Hello, OAuth2!"
    encrypted = session_store._encrypt(plaintext)
    decrypted = session_store._decrypt(encrypted)
    assert decrypted == plaintext


def test_encrypt_produces_different_ciphertexts(session_store):
    """Test encryption produces different ciphertexts for same plaintext (nonce randomization)."""
    plaintext = "Same plaintext"
    encrypted1 = session_store._encrypt(plaintext)
    encrypted2 = session_store._encrypt(plaintext)

    # Different ciphertexts due to random nonce
    assert encrypted1 != encrypted2

    # But both decrypt to same plaintext
    assert session_store._decrypt(encrypted1) == plaintext
    assert session_store._decrypt(encrypted2) == plaintext


def test_decrypt_with_secondary_key_fallback(redis_mock, encryption_key, secondary_key):
    """Test decryption falls back to secondary key during rotation."""
    store = RedisSessionStore(redis_mock, encryption_key, secondary_key=secondary_key)

    # Encrypt with primary key
    plaintext = "Test data"
    encrypted = store._encrypt(plaintext)

    # Create new store with swapped keys (simulates rotation)
    rotated_store = RedisSessionStore(redis_mock, secondary_key, secondary_key=encryption_key)

    # Should decrypt using secondary key fallback
    decrypted = rotated_store._decrypt(encrypted)
    assert decrypted == plaintext


@pytest.mark.asyncio()
async def test_create_session(session_store, redis_mock, sample_session_data):
    """Test session creation stores encrypted data in Redis."""
    session_id = "abc123def456"

    await session_store.create_session(session_id, sample_session_data)

    # Verify Redis setex was called with correct key and TTL
    redis_mock.setex.assert_called_once()
    call_args = redis_mock.setex.call_args
    assert call_args[0][0] == "session:abc123def456"
    assert call_args[0][1] == 14400  # 4 hours in seconds

    # Verify encrypted data can be decrypted
    encrypted_data = call_args[0][2]
    decrypted_json = session_store._decrypt(encrypted_data)
    decrypted_session = SessionData.model_validate_json(decrypted_json)
    assert decrypted_session.user_id == sample_session_data.user_id
    assert decrypted_session.email == sample_session_data.email


@pytest.mark.asyncio()
async def test_get_session_success(session_store, redis_mock, sample_session_data):
    """Test successful session retrieval."""
    session_id = "abc123def456"

    # Simulate session stored in Redis
    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    retrieved_session = await session_store.get_session(
        session_id,
        current_ip=sample_session_data.ip_address,
        current_user_agent=sample_session_data.user_agent,
        update_activity=False,
    )

    assert retrieved_session is not None
    assert retrieved_session.user_id == sample_session_data.user_id
    assert retrieved_session.email == sample_session_data.email
    redis_mock.get.assert_called_once_with("session:abc123def456")


@pytest.mark.asyncio()
async def test_get_session_not_found(session_store, redis_mock):
    """Test session retrieval returns None when session not found."""
    redis_mock.get.return_value = None

    retrieved_session = await session_store.get_session("nonexistent")

    assert retrieved_session is None


@pytest.mark.asyncio()
async def test_get_session_idle_timeout(session_store, redis_mock, sample_session_data):
    """Test session is deleted when idle timeout exceeded."""
    # Set last_activity to 20 minutes ago (idle timeout is 15 min)
    sample_session_data.last_activity = datetime.now(UTC) - timedelta(minutes=20)

    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    retrieved_session = await session_store.get_session("abc123")

    assert retrieved_session is None
    # Session should be deleted
    redis_mock.delete.assert_called_once_with("session:abc123")


@pytest.mark.asyncio()
async def test_get_session_updates_activity(session_store, redis_mock, sample_session_data):
    """Test session retrieval updates last_activity timestamp."""
    old_activity = datetime.now(UTC) - timedelta(minutes=5)
    sample_session_data.last_activity = old_activity

    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    retrieved_session = await session_store.get_session(
        "abc123",
        current_ip=sample_session_data.ip_address,
        current_user_agent=sample_session_data.user_agent,
        update_activity=True,
    )

    assert retrieved_session is not None
    assert retrieved_session.last_activity > old_activity
    # Should have called setex to update session with remaining TTL
    redis_mock.setex.assert_called_once()


@pytest.mark.asyncio()
async def test_delete_session(session_store, redis_mock):
    """Test session deletion."""
    session_id = "abc123def456"

    await session_store.delete_session(session_id)

    redis_mock.delete.assert_called_once_with("session:abc123def456")


@pytest.mark.asyncio()
async def test_cleanup_all_sessions(session_store, redis_mock):
    """Test bulk session cleanup with SCAN + DEL."""
    # Simulate SCAN returning sessions in 2 batches
    redis_mock.scan.side_effect = [
        (1, [b"session:abc", b"session:def", b"session:ghi"]),  # First batch
        (0, [b"session:jkl", b"session:mno"]),  # Final batch (cursor=0)
    ]
    redis_mock.delete.side_effect = [3, 2]  # Deleted counts per batch

    deleted_count = await session_store.cleanup_all_sessions(prefix="session:")

    assert deleted_count == 5
    # Verify SCAN was called with correct parameters
    assert redis_mock.scan.call_count == 2
    # Verify DELETE was called for each batch
    assert redis_mock.delete.call_count == 2


@pytest.mark.asyncio()
async def test_cleanup_all_sessions_empty(session_store, redis_mock):
    """Test bulk session cleanup with no sessions."""
    redis_mock.scan.return_value = (0, [])  # No sessions found

    deleted_count = await session_store.cleanup_all_sessions(prefix="session:")

    assert deleted_count == 0
    redis_mock.scan.assert_called_once()
    redis_mock.delete.assert_not_called()


@pytest.mark.asyncio()
async def test_get_session_decryption_failure(session_store, redis_mock):
    """Test session retrieval returns None on decryption failure."""
    # Return invalid encrypted data
    redis_mock.get.return_value = b"invalid_encrypted_data"

    retrieved_session = await session_store.get_session("abc123")

    assert retrieved_session is None


@pytest.mark.asyncio()
async def test_get_session_ip_mismatch(session_store, redis_mock, sample_session_data):
    """Test session is deleted when IP address doesn't match (session hijacking prevention)."""
    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    # Request from different IP
    retrieved_session = await session_store.get_session(
        "abc123",
        current_ip="10.0.0.99",  # Different from sample_session_data.ip_address
        current_user_agent=sample_session_data.user_agent,
    )

    assert retrieved_session is None
    # Session should be deleted due to IP mismatch
    redis_mock.delete.assert_called_once_with("session:abc123")


@pytest.mark.asyncio()
async def test_get_session_user_agent_mismatch(session_store, redis_mock, sample_session_data):
    """Test session is deleted when User-Agent doesn't match (session hijacking prevention)."""
    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    # Request from different User-Agent
    retrieved_session = await session_store.get_session(
        "abc123",
        current_ip=sample_session_data.ip_address,
        current_user_agent="Chrome/100.0 (Attacker)",  # Different from sample UA
    )

    assert retrieved_session is None
    # Session should be deleted due to User-Agent mismatch
    redis_mock.delete.assert_called_once_with("session:abc123")


@pytest.mark.asyncio()
async def test_get_session_binding_validation_optional(
    session_store, redis_mock, sample_session_data
):
    """Test session binding validation is optional (backward compatibility)."""
    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    # No current_ip or current_user_agent provided - should still work
    retrieved_session = await session_store.get_session("abc123", update_activity=False)

    assert retrieved_session is not None
    assert retrieved_session.user_id == sample_session_data.user_id


@pytest.mark.asyncio()
async def test_get_session_absolute_timeout(session_store, redis_mock, sample_session_data):
    """Test session is deleted when absolute timeout (4 hours) is exceeded."""
    # Set created_at to 5 hours ago (absolute timeout is 4 hours)
    sample_session_data.created_at = datetime.now(UTC) - timedelta(hours=5)
    # Set last_activity to 1 minute ago (idle timeout not exceeded)
    sample_session_data.last_activity = datetime.now(UTC) - timedelta(minutes=1)

    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    retrieved_session = await session_store.get_session("abc123")

    assert retrieved_session is None
    # Session should be deleted due to absolute timeout
    redis_mock.delete.assert_called_once_with("session:abc123")


@pytest.mark.asyncio()
async def test_get_session_remaining_ttl_calculation(
    session_store, redis_mock, sample_session_data
):
    """Test session update uses remaining TTL, not full 4 hours."""
    # Session created 2 hours ago
    created_at = datetime.now(UTC) - timedelta(hours=2)
    sample_session_data.created_at = created_at
    sample_session_data.last_activity = datetime.now(UTC) - timedelta(minutes=5)

    encrypted_json = session_store._encrypt(sample_session_data.model_dump_json())
    redis_mock.get.return_value = encrypted_json.encode()

    retrieved_session = await session_store.get_session(
        "abc123",
        current_ip=sample_session_data.ip_address,
        current_user_agent=sample_session_data.user_agent,
        update_activity=True,
    )

    assert retrieved_session is not None

    # Verify setex was called with REMAINING TTL (~2 hours), not full 4 hours
    redis_mock.setex.assert_called_once()
    call_args = redis_mock.setex.call_args
    ttl_seconds = call_args[0][1]

    # Should be approximately 2 hours (7200 seconds) ± 5 seconds for test execution time
    assert 7195 <= ttl_seconds <= 7205, f"Expected ~7200 seconds, got {ttl_seconds}"
