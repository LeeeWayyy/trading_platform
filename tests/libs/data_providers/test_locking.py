"""Unit tests for OS-atomic file locking system."""

from __future__ import annotations

import datetime
import json
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from libs.data_providers.locking import (
    AtomicFileLock,
    LockAcquisitionError,
    atomic_lock,
)
from libs.data_quality.exceptions import LockNotHeldError
from libs.data_quality.types import LockToken


@pytest.fixture
def lock_dir(tmp_path: Path) -> Path:
    """Create a temporary lock directory."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    return lock_dir


class TestAtomicFileLock:
    """Tests for AtomicFileLock class."""

    def test_lock_acquisition_succeeds_when_unlocked(self, lock_dir: Path) -> None:
        """Test 1: Lock acquisition succeeds when unlocked."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire(timeout_seconds=5.0)

        assert token is not None
        assert token.pid == os.getpid()
        assert token.hostname == socket.gethostname()
        assert token.lock_path == lock_dir / "test_dataset.lock"
        assert token.lock_path.exists()

        lock.release(token)

    def test_lock_acquisition_fails_when_locked(self, lock_dir: Path) -> None:
        """Test 2: Lock acquisition fails when locked by active process."""
        lock1 = AtomicFileLock(lock_dir, "test_dataset", writer_id="writer1")
        lock2 = AtomicFileLock(lock_dir, "test_dataset", writer_id="writer2")

        token1 = lock1.acquire()

        # Second lock should fail quickly
        with pytest.raises(LockAcquisitionError):
            lock2.acquire(timeout_seconds=0.5)

        lock1.release(token1)

    def test_lock_release_deletes_lock_file(self, lock_dir: Path) -> None:
        """Test 3: Lock release deletes lock file."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        assert token.lock_path.exists()
        lock.release(token)
        assert not token.lock_path.exists()

    def test_stale_lock_detection_timeout_exceeded(self, lock_dir: Path) -> None:
        """Test 4: Stale lock detection when timeout exceeded."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Create an expired lock file
        lock_path = lock_dir / "test_dataset.lock"
        now = datetime.datetime.now(datetime.UTC)
        expired_time = now - datetime.timedelta(hours=5)

        lock_data = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "writer_id": "old_writer",
            "acquired_at": expired_time.isoformat(),
            "expires_at": expired_time.isoformat(),  # Already expired
        }
        lock_path.write_text(json.dumps(lock_data))

        # Should detect as stale and recover
        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale
        assert "expired" in reason.lower()

    def test_stale_lock_recovery_dead_pid(self, lock_dir: Path) -> None:
        """Test 5: Stale lock recovery when PID is dead."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Create lock with non-existent PID
        lock_path = lock_dir / "test_dataset.lock"
        now = datetime.datetime.now(datetime.UTC)
        future = now + datetime.timedelta(hours=2)

        lock_data = {
            "pid": 999999,  # Non-existent PID
            "hostname": socket.gethostname(),
            "writer_id": "dead_writer",
            "acquired_at": now.isoformat(),
            "expires_at": future.isoformat(),
        }
        lock_path.write_text(json.dumps(lock_data))

        # Should detect as stale (dead PID)
        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale
        assert "dead" in reason.lower() or "not" in reason.lower()

    def test_pid_reuse_handling_hostname_writer_id_check(self, lock_dir: Path) -> None:
        """Test 6: PID reuse handling with hostname + writer_id check."""
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="new_writer")

        # Create lock with same PID but different writer_id
        lock_path = lock_dir / "test_dataset.lock"
        now = datetime.datetime.now(datetime.UTC)
        expired = now - datetime.timedelta(hours=5)

        lock_data = {
            "pid": os.getpid(),  # Same PID
            "hostname": socket.gethostname(),
            "writer_id": "different_writer",  # Different writer
            "acquired_at": expired.isoformat(),
            "expires_at": expired.isoformat(),  # Expired
        }
        lock_path.write_text(json.dumps(lock_data))

        # Should be stale due to expiry despite same PID
        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale

    def test_malformed_json_treated_as_stale(self, lock_dir: Path) -> None:
        """Test 7: Malformed JSON is treated as stale for self-healing."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Create lock file with invalid JSON
        lock_path = lock_dir / "test_dataset.lock"
        lock_path.write_text("not valid json {{{")

        # Should be treated as stale, not raise exception
        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale
        assert "Malformed lock file" in reason

    def test_extra_fields_treated_as_stale(self, lock_dir: Path) -> None:
        """Test 8: Extra fields in JSON treated as stale for self-healing."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Create lock file with extra fields
        lock_path = lock_dir / "test_dataset.lock"
        now = datetime.datetime.now(datetime.UTC)
        future = now + datetime.timedelta(hours=2)

        lock_data = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "writer_id": "writer",
            "acquired_at": now.isoformat(),
            "expires_at": future.isoformat(),
            "extra_field": "should_not_exist",  # Extra field
        }
        lock_path.write_text(json.dumps(lock_data))

        # Should be treated as stale, not raise exception
        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale
        assert "Invalid lock schema" in reason

    def test_token_validation_on_release(self, lock_dir: Path) -> None:
        """Test 9: Token validation on release."""
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="writer1")
        token = lock.acquire()

        # Create a fake token with different writer_id
        fake_token = LockToken(
            pid=token.pid,
            hostname=token.hostname,
            writer_id="different_writer",  # Different
            acquired_at=token.acquired_at,
            expires_at=token.expires_at,
            lock_path=token.lock_path,
        )

        with pytest.raises(LockNotHeldError):
            lock.release(fake_token)

        # Clean up with correct token
        lock.release(token)

    def test_lock_refresh_extends_expiry(self, lock_dir: Path) -> None:
        """Test 10: Lock refresh extends expiry."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        original_expires = token.expires_at

        # Wait a tiny bit and refresh
        import time
        time.sleep(0.1)

        new_token = lock.refresh(token)

        assert new_token.expires_at > original_expires
        assert new_token.pid == token.pid
        assert new_token.writer_id == token.writer_id

        lock.release(new_token)

    def test_fsync_called_after_lock_creation(self, lock_dir: Path) -> None:
        """Test 11: fsync called after lock creation."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        with patch("os.fsync") as mock_fsync:
            token = lock.acquire()
            # fsync should have been called at least once
            assert mock_fsync.called

            lock.release(token)


class TestAtomicLockContextManager:
    """Tests for atomic_lock context manager."""

    def test_context_manager_acquires_and_releases(self, lock_dir: Path) -> None:
        """Test context manager properly acquires and releases lock."""
        with atomic_lock(lock_dir, "test_dataset") as token:
            assert token is not None
            assert token.lock_path.exists()

        # Lock should be released after context
        lock_path = lock_dir / "test_dataset.lock"
        assert not lock_path.exists()

    def test_context_manager_releases_on_exception(self, lock_dir: Path) -> None:
        """Test context manager releases lock even on exception."""
        lock_path = lock_dir / "test_dataset.lock"

        with pytest.raises(ValueError):
            with atomic_lock(lock_dir, "test_dataset") as token:
                assert token.lock_path.exists()
                raise ValueError("Test exception")

        # Lock should still be released
        assert not lock_path.exists()


class TestPIDAliveCheck:
    """Tests for PID liveness checking."""

    def test_is_pid_alive_returns_true_for_current_process(
        self, lock_dir: Path
    ) -> None:
        """Current process PID should be detected as alive."""
        lock = AtomicFileLock(lock_dir, "test")
        assert lock._is_pid_alive(os.getpid())

    def test_is_pid_alive_returns_false_for_nonexistent_pid(
        self, lock_dir: Path
    ) -> None:
        """Non-existent PID should be detected as dead."""
        lock = AtomicFileLock(lock_dir, "test")
        # Use a very high PID that's unlikely to exist
        assert not lock._is_pid_alive(999999)
