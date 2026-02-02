"""Unit tests for OS-atomic file locking system."""

from __future__ import annotations

import datetime
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from libs.data.data_providers.locking import (
    AtomicFileLock,
    LockAcquisitionError,
    atomic_lock,
)
from libs.data.data_quality.exceptions import LockNotHeldError
from libs.data.data_quality.types import LockToken


@pytest.fixture()
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

        def _raise_with_lock() -> None:
            with atomic_lock(lock_dir, "test_dataset") as token:
                assert token.lock_path.exists()
                raise ValueError("Test exception")

        with pytest.raises(ValueError, match="Test exception"):
            _raise_with_lock()

        # Lock should still be released
        assert not lock_path.exists()


class TestPIDAliveCheck:
    """Tests for PID liveness checking."""

    def test_is_pid_alive_returns_true_for_current_process(self, lock_dir: Path) -> None:
        """Current process PID should be detected as alive."""
        lock = AtomicFileLock(lock_dir, "test")
        assert lock._is_pid_alive(os.getpid())

    def test_is_pid_alive_returns_false_for_nonexistent_pid(self, lock_dir: Path) -> None:
        """Non-existent PID should be detected as dead."""
        lock = AtomicFileLock(lock_dir, "test")
        # Use a very high PID that's unlikely to exist
        assert not lock._is_pid_alive(999999)

    def test_is_pid_alive_permission_error_returns_true(self, lock_dir: Path) -> None:
        """Process exists but we can't signal it - should return True."""
        lock = AtomicFileLock(lock_dir, "test")
        with patch("os.kill", side_effect=PermissionError("No permission")):
            assert lock._is_pid_alive(12345)


class TestStaleLockRecovery:
    """Tests for stale lock recovery scenarios."""

    def test_stale_lock_recovery_wins_when_first(self, lock_dir: Path) -> None:
        """Test winning stale lock recovery via atomic rename."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        lock_path = lock_dir / "test_dataset.lock"

        # Create a stale lock file
        now = datetime.datetime.now(datetime.UTC)
        lock_data = {
            "pid": 999999,  # Dead PID
            "hostname": socket.gethostname(),
            "writer_id": "old_writer",
            "acquired_at": now.isoformat(),
            "expires_at": (now + datetime.timedelta(hours=2)).isoformat(),
        }
        lock_path.write_text(json.dumps(lock_data))

        # Recovery should succeed
        result = lock._recover_stale_lock(lock_path)
        assert result is True
        assert not lock_path.exists()

    def test_stale_lock_recovery_loses_when_file_gone(self, lock_dir: Path) -> None:
        """Test losing recovery when lock file already removed."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        lock_path = lock_dir / "test_dataset.lock"

        # Lock file doesn't exist - recovery should fail gracefully
        result = lock._recover_stale_lock(lock_path)
        assert result is False

    def test_stale_lock_recovery_oserror_returns_false(self, lock_dir: Path) -> None:
        """Test recovery fails gracefully on OSError."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        lock_path = lock_dir / "test_dataset.lock"

        # Create a lock file
        lock_path.write_text("{}")

        # Simulate OSError during rename
        with patch("os.rename", side_effect=OSError("Permission denied")):
            result = lock._recover_stale_lock(lock_path)
            assert result is False

    def test_acquire_with_stale_lock_triggers_recovery(self, lock_dir: Path) -> None:
        """Test acquire detects stale lock and attempts recovery."""
        lock_path = lock_dir / "test_dataset.lock"

        # Create an expired lock file
        now = datetime.datetime.now(datetime.UTC)
        expired = now - datetime.timedelta(hours=5)
        lock_data = {
            "pid": 999999,
            "hostname": socket.gethostname(),
            "writer_id": "old_writer",
            "acquired_at": expired.isoformat(),
            "expires_at": expired.isoformat(),
        }
        lock_path.write_text(json.dumps(lock_data))

        # New lock should acquire successfully after recovering stale lock
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="new_writer")
        token = lock.acquire(timeout_seconds=5.0)

        assert token is not None
        assert token.writer_id == "new_writer"
        lock.release(token)


class TestReleaseEdgeCases:
    """Tests for lock release edge cases."""

    def test_release_when_lock_file_gone(self, lock_dir: Path) -> None:
        """Test release handles missing lock file gracefully."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # Manually delete the lock file
        token.lock_path.unlink()

        # Release should not raise
        lock.release(token)

    def test_release_oserror_raises_lock_not_held(self, lock_dir: Path) -> None:
        """Test release raises LockNotHeldError on OSError during rename."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # Simulate OSError during rename
        with patch.object(Path, "rename", side_effect=OSError("Permission denied")):
            with pytest.raises(LockNotHeldError, match="Failed to release lock"):
                lock.release(token)

    def test_release_when_recovered_by_another_process(self, lock_dir: Path) -> None:
        """Test release when lock was recovered by another process."""
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="original_writer")
        token = lock.acquire()

        # Simulate another process recovering and rewriting the lock
        different_lock_data = {
            "pid": 88888,
            "hostname": "other-host",
            "writer_id": "different_writer",
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
            ).isoformat(),
        }
        token.lock_path.write_text(json.dumps(different_lock_data))

        # Release should detect mismatch and raise
        with pytest.raises(LockNotHeldError, match="recovered by another process"):
            lock.release(token)

    def test_release_with_corrupted_lock_file(self, lock_dir: Path) -> None:
        """Test release handles corrupted lock file by attempting restore."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # Corrupt the lock file
        token.lock_path.write_text("not valid json {{{")

        # Release should handle gracefully (log warning, not raise)
        lock.release(token)

    def test_release_lock_not_held_reraises(self, lock_dir: Path) -> None:
        """Test that LockNotHeldError is re-raised during release."""
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="writer1")
        token = lock.acquire()

        # Modify the lock file to have different writer_id
        modified_data = {
            "pid": token.pid,
            "hostname": token.hostname,
            "writer_id": "different_writer",
            "acquired_at": token.acquired_at.isoformat(),
            "expires_at": token.expires_at.isoformat(),
        }
        token.lock_path.write_text(json.dumps(modified_data))

        with pytest.raises(LockNotHeldError, match="recovered by another process"):
            lock.release(token)


class TestRefreshEdgeCases:
    """Tests for lock refresh edge cases."""

    def test_refresh_invalid_token_raises(self, lock_dir: Path) -> None:
        """Test refresh with invalid token raises LockNotHeldError."""
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="writer1")
        token = lock.acquire()

        # Create fake token with wrong writer_id
        fake_token = LockToken(
            pid=token.pid,
            hostname=token.hostname,
            writer_id="wrong_writer",
            acquired_at=token.acquired_at,
            expires_at=token.expires_at,
            lock_path=token.lock_path,
        )

        with pytest.raises(LockNotHeldError, match="Cannot refresh - lock not held"):
            lock.refresh(fake_token)

        lock.release(token)

    def test_refresh_when_recovered_by_another_process(self, lock_dir: Path) -> None:
        """Test refresh when lock was recovered by another process."""
        lock = AtomicFileLock(lock_dir, "test_dataset", writer_id="original_writer")
        token = lock.acquire()

        # Simulate another process recovering the lock
        different_data = {
            "pid": 88888,
            "hostname": "other-host",
            "writer_id": "different_writer",
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
            ).isoformat(),
        }
        token.lock_path.write_text(json.dumps(different_data))

        with pytest.raises(LockNotHeldError, match="recovered by another process"):
            lock.refresh(token)

    def test_refresh_when_lock_file_missing(self, lock_dir: Path) -> None:
        """Test refresh when lock file was deleted."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # Delete the lock file
        token.lock_path.unlink()

        with pytest.raises(LockNotHeldError, match="lock file missing"):
            lock.refresh(token)

    def test_refresh_when_lock_file_unreadable(self, lock_dir: Path) -> None:
        """Test refresh when lock file has corrupted JSON."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # Corrupt the lock file
        token.lock_path.write_text("invalid json {{{")

        with pytest.raises(LockNotHeldError, match="lock file unreadable"):
            lock.refresh(token)


class TestTokenValidation:
    """Tests for token validation."""

    def test_validate_token_no_current_token(self, lock_dir: Path) -> None:
        """Test validation fails when no current token."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Create a token without acquiring
        fake_token = LockToken(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            writer_id="fake",
            acquired_at=datetime.datetime.now(datetime.UTC),
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2),
            lock_path=lock_dir / "test_dataset.lock",
        )

        assert not lock._validate_token(fake_token)


class TestWriteLockFile:
    """Tests for atomic lock file writing."""

    def test_write_lock_file_oserror_cleanup(self, lock_dir: Path) -> None:
        """Test temp file cleanup on write failure."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token_data = LockToken(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            writer_id="test",
            acquired_at=datetime.datetime.now(datetime.UTC),
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2),
            lock_path=lock_dir / "test_dataset.lock",
        )

        # Make temp file creation succeed but rename fail
        def fail_replace(self: Path, target: Path) -> Path:
            raise OSError("Permission denied")

        with patch.object(Path, "replace", fail_replace):
            with pytest.raises(OSError, match="Permission denied"):
                lock._write_lock_file(token_data)

        # Temp file should be cleaned up
        temp_files = list(lock_dir.glob("*.tmp.*"))
        assert len(temp_files) == 0


class TestIsLockStale:
    """Tests for stale lock detection edge cases."""

    def test_invalid_token_data_treated_as_stale(self, lock_dir: Path) -> None:
        """Test invalid token data (e.g., bad datetime) treated as stale."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        lock_path = lock_dir / "test_dataset.lock"

        # Create lock with invalid datetime format
        lock_data = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "writer_id": "writer",
            "acquired_at": "not-a-valid-datetime",
            "expires_at": "also-not-valid",
        }
        lock_path.write_text(json.dumps(lock_data))

        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale
        assert "Invalid lock data" in reason

    def test_missing_fields_treated_as_stale(self, lock_dir: Path) -> None:
        """Test missing required fields treated as stale."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        lock_path = lock_dir / "test_dataset.lock"

        # Create lock with missing fields
        lock_data = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            # Missing writer_id, acquired_at, expires_at
        }
        lock_path.write_text(json.dumps(lock_data))

        is_stale, reason = lock._is_lock_stale(lock_path)
        assert is_stale
        assert "Invalid lock schema" in reason

    def test_lock_not_stale_different_hostname(self, lock_dir: Path) -> None:
        """Test lock from different hostname not checked for PID liveness."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        lock_path = lock_dir / "test_dataset.lock"

        now = datetime.datetime.now(datetime.UTC)
        future = now + datetime.timedelta(hours=2)

        lock_data = {
            "pid": 999999,  # Would be dead on this host
            "hostname": "other-hostname",  # Different host
            "writer_id": "writer",
            "acquired_at": now.isoformat(),
            "expires_at": future.isoformat(),
        }
        lock_path.write_text(json.dumps(lock_data))

        # Should not be stale (can't check PID on different host)
        is_stale, reason = lock._is_lock_stale(lock_path)
        assert not is_stale


class TestCleanupStaleReleasingFile:
    """Tests for stale .releasing file cleanup."""

    def test_cleanup_releasing_file_dead_pid(self, lock_dir: Path) -> None:
        """Test cleanup of .releasing file when releasing process is dead."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        release_path = lock_dir / "test_dataset.releasing"

        # Create a .releasing file from dead process
        releasing_data = {
            "pid": 999999,  # Dead PID
            "hostname": socket.gethostname(),
            "writer_id": "crashed_writer",
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
            ).isoformat(),
        }
        release_path.write_text(json.dumps(releasing_data))

        # Cleanup should remove the file
        lock._cleanup_stale_releasing_file()
        assert not release_path.exists()

    def test_cleanup_releasing_file_cross_host_old(self, lock_dir: Path) -> None:
        """Test cleanup of old .releasing file from different host."""
        import time

        lock = AtomicFileLock(lock_dir, "test_dataset")
        release_path = lock_dir / "test_dataset.releasing"

        # Create a .releasing file from different host
        releasing_data = {
            "pid": 12345,
            "hostname": "other-host",  # Different host
            "writer_id": "remote_writer",
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
            ).isoformat(),
        }
        release_path.write_text(json.dumps(releasing_data))

        # Set mtime to be old (>60 seconds)
        old_time = time.time() - 120  # 2 minutes ago
        os.utime(release_path, (old_time, old_time))

        # Cleanup should remove the old file
        lock._cleanup_stale_releasing_file()
        assert not release_path.exists()

    def test_cleanup_releasing_file_cross_host_recent(self, lock_dir: Path) -> None:
        """Test .releasing file from different host not cleaned if recent."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        release_path = lock_dir / "test_dataset.releasing"

        # Create a recent .releasing file from different host
        releasing_data = {
            "pid": 12345,
            "hostname": "other-host",
            "writer_id": "remote_writer",
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
            ).isoformat(),
        }
        release_path.write_text(json.dumps(releasing_data))

        # File is recent, should NOT be cleaned
        lock._cleanup_stale_releasing_file()
        assert release_path.exists()

        # Cleanup
        release_path.unlink()

    def test_cleanup_releasing_file_corrupted(self, lock_dir: Path) -> None:
        """Test cleanup of corrupted .releasing file."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        release_path = lock_dir / "test_dataset.releasing"

        # Create a corrupted .releasing file
        release_path.write_text("not valid json {{{")

        # Cleanup should remove the corrupted file
        lock._cleanup_stale_releasing_file()
        assert not release_path.exists()

    def test_cleanup_releasing_file_no_file(self, lock_dir: Path) -> None:
        """Test cleanup when no .releasing file exists."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Should not raise when file doesn't exist
        lock._cleanup_stale_releasing_file()

    def test_cleanup_releasing_file_unlink_oserror(self, lock_dir: Path) -> None:
        """Test cleanup handles unlink OSError gracefully."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        release_path = lock_dir / "test_dataset.releasing"

        # Create a corrupted .releasing file
        release_path.write_text("not valid json")

        # Simulate unlink failure
        with patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
            # Should not raise
            lock._cleanup_stale_releasing_file()

    def test_cleanup_releasing_file_live_pid_same_host(self, lock_dir: Path) -> None:
        """Test .releasing file not cleaned if releasing process is alive."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        release_path = lock_dir / "test_dataset.releasing"

        # Create a .releasing file from current process (still alive)
        releasing_data = {
            "pid": os.getpid(),  # Current process - alive
            "hostname": socket.gethostname(),
            "writer_id": "active_writer",
            "acquired_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2)
            ).isoformat(),
        }
        release_path.write_text(json.dumps(releasing_data))

        # Should NOT clean up since process is alive
        lock._cleanup_stale_releasing_file()
        assert release_path.exists()

        # Cleanup
        release_path.unlink()


class TestFsyncDirectory:
    """Tests for directory fsync."""

    def test_fsync_directory_oserror_logged(self, lock_dir: Path) -> None:
        """Test fsync directory failure is logged but doesn't raise."""
        lock = AtomicFileLock(lock_dir, "test_dataset")

        # Simulate fsync failure
        with patch("os.open", side_effect=OSError("Cannot open directory")):
            # Should not raise, just log warning
            lock._fsync_directory(lock_dir)


class TestReleaseRestoreOnError:
    """Tests for release attempting to restore lock file on error."""

    def test_release_restore_on_json_decode_error(self, lock_dir: Path) -> None:
        """Test release attempts to restore lock file on JSON decode error."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # After rename to .releasing, make the file corrupted
        # We need to test the path where after rename, reading fails
        original_rename = Path.rename

        def rename_and_corrupt(self: Path, target: Path) -> Path:
            result = original_rename(self, target)
            # Corrupt the file after rename
            target.write_text("corrupted json {{{")
            return result

        with patch.object(Path, "rename", rename_and_corrupt):
            # Should handle gracefully and try to restore
            lock.release(token)

    def test_release_restore_oserror_suppressed(self, lock_dir: Path) -> None:
        """Test release restore failure is suppressed."""
        lock = AtomicFileLock(lock_dir, "test_dataset")
        token = lock.acquire()

        # Corrupt the lock file
        token.lock_path.write_text("not valid json")

        # Simulate restore failure too
        original_rename = Path.rename
        call_count = [0]

        def fail_second_rename(self: Path, target: Path) -> Path:
            call_count[0] += 1
            if call_count[0] == 1:
                # First rename succeeds (lock -> releasing)
                return original_rename(self, target)
            else:
                # Second rename fails (restore attempt)
                raise OSError("Cannot restore")

        with patch.object(Path, "rename", fail_second_rename):
            # Should handle gracefully
            lock.release(token)
