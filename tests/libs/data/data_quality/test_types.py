"""Tests for libs.data_quality.types module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from libs.data.data_quality.types import DiskSpaceStatus, LockToken


class TestLockToken:
    """Tests for LockToken dataclass."""

    def test_to_dict_serialization(self) -> None:
        """Test LockToken serializes to dict correctly."""
        now = datetime.now(UTC)
        expires = now + timedelta(hours=4)
        lock_path = Path("/tmp/test.lock")

        token = LockToken(
            pid=12345,
            hostname="test-host",
            writer_id="writer-123",
            acquired_at=now,
            expires_at=expires,
            lock_path=lock_path,
        )

        result = token.to_dict()

        assert result["pid"] == 12345
        assert result["hostname"] == "test-host"
        assert result["writer_id"] == "writer-123"
        assert result["acquired_at"] == now.isoformat()
        assert result["expires_at"] == expires.isoformat()

    def test_from_dict_deserialization(self) -> None:
        """Test LockToken deserializes from dict correctly."""
        now = datetime.now(UTC)
        expires = now + timedelta(hours=4)
        lock_path = Path("/tmp/test.lock")

        data = {
            "pid": 12345,
            "hostname": "test-host",
            "writer_id": "writer-123",
            "acquired_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }

        token = LockToken.from_dict(data, lock_path)

        assert token.pid == 12345
        assert token.hostname == "test-host"
        assert token.writer_id == "writer-123"
        assert token.acquired_at == now
        assert token.expires_at == expires
        assert token.lock_path == lock_path

    def test_is_expired_returns_true_when_past_expires_at(self) -> None:
        """Test is_expired returns True when current time is past expires_at."""
        now = datetime.now(UTC)
        expired_at = now - timedelta(hours=1)  # Expired 1 hour ago
        lock_path = Path("/tmp/test.lock")

        token = LockToken(
            pid=12345,
            hostname="test-host",
            writer_id="writer-123",
            acquired_at=now - timedelta(hours=5),
            expires_at=expired_at,
            lock_path=lock_path,
        )

        assert token.is_expired() is True

    def test_is_expired_returns_false_when_before_expires_at(self) -> None:
        """Test is_expired returns False when current time is before expires_at."""
        now = datetime.now(UTC)
        expires = now + timedelta(hours=4)
        lock_path = Path("/tmp/test.lock")

        token = LockToken(
            pid=12345,
            hostname="test-host",
            writer_id="writer-123",
            acquired_at=now,
            expires_at=expires,
            lock_path=lock_path,
        )

        assert token.is_expired() is False

    def test_round_trip_through_json(self) -> None:
        """Test LockToken survives JSON round-trip."""
        now = datetime.now(UTC)
        expires = now + timedelta(hours=4)
        lock_path = Path("/tmp/test.lock")

        original = LockToken(
            pid=12345,
            hostname="test-host",
            writer_id="writer-123",
            acquired_at=now,
            expires_at=expires,
            lock_path=lock_path,
        )

        # Serialize to JSON
        json_str = original.to_json()

        # Deserialize from JSON
        restored = LockToken.from_json(json_str, lock_path)

        assert restored.pid == original.pid
        assert restored.hostname == original.hostname
        assert restored.writer_id == original.writer_id
        assert restored.acquired_at == original.acquired_at
        assert restored.expires_at == original.expires_at
        assert restored.lock_path == original.lock_path


class TestDiskSpaceStatus:
    """Tests for DiskSpaceStatus dataclass."""

    def test_ok_status(self) -> None:
        """Test DiskSpaceStatus with OK level."""
        status = DiskSpaceStatus(
            level="ok",
            free_bytes=100_000_000_000,
            total_bytes=500_000_000_000,
            used_pct=0.80,
            message="OK: 80% disk usage",
        )

        assert status.level == "ok"
        assert status.free_bytes == 100_000_000_000
        assert status.total_bytes == 500_000_000_000
        assert status.used_pct == 0.80
        assert "OK" in status.message

    def test_warning_status(self) -> None:
        """Test DiskSpaceStatus with warning level."""
        status = DiskSpaceStatus(
            level="warning",
            free_bytes=50_000_000_000,
            total_bytes=500_000_000_000,
            used_pct=0.90,
            message="Warning: 90% disk usage",
        )

        assert status.level == "warning"
        assert "Warning" in status.message

    def test_critical_status(self) -> None:
        """Test DiskSpaceStatus with critical level."""
        status = DiskSpaceStatus(
            level="critical",
            free_bytes=25_000_000_000,
            total_bytes=500_000_000_000,
            used_pct=0.95,
            message="Critical: 95% disk usage",
        )

        assert status.level == "critical"
        assert "Critical" in status.message
