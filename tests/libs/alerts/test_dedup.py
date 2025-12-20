"""Tests for alert deduplication utilities."""

from datetime import UTC, datetime

from libs.alerts.dedup import (
    compute_dedup_key,
    compute_recipient_hash,
)


class TestComputeDedupKey:
    """Test dedup key computation."""

    def test_consistent_key(self):
        """Test same inputs produce same key."""
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        secret = "test-secret-key-12345678"

        key1 = compute_dedup_key("alert-1", "email", "user@example.com", ts, secret)
        key2 = compute_dedup_key("alert-1", "email", "user@example.com", ts, secret)

        assert key1 == key2

    def test_different_alert_id(self):
        """Test different alert IDs produce different keys."""
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        secret = "test-secret-key-12345678"

        key1 = compute_dedup_key("alert-1", "email", "user@example.com", ts, secret)
        key2 = compute_dedup_key("alert-2", "email", "user@example.com", ts, secret)

        assert key1 != key2

    def test_different_channel(self):
        """Test different channels produce different keys."""
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        secret = "test-secret-key-12345678"

        key1 = compute_dedup_key("alert-1", "email", "user@example.com", ts, secret)
        key2 = compute_dedup_key("alert-1", "slack", "user@example.com", ts, secret)

        assert key1 != key2

    def test_different_recipient(self):
        """Test different recipients produce different keys."""
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        secret = "test-secret-key-12345678"

        key1 = compute_dedup_key("alert-1", "email", "user1@example.com", ts, secret)
        key2 = compute_dedup_key("alert-1", "email", "user2@example.com", ts, secret)

        assert key1 != key2

    def test_different_timestamp(self):
        """Test different timestamps produce different keys."""
        ts1 = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2024, 1, 15, 13, 0, 0, tzinfo=UTC)
        secret = "test-secret-key-12345678"

        key1 = compute_dedup_key("alert-1", "email", "user@example.com", ts1, secret)
        key2 = compute_dedup_key("alert-1", "email", "user@example.com", ts2, secret)

        assert key1 != key2

    def test_key_format(self):
        """Test key has expected format with colon separators."""
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        secret = "test-secret-key-12345678"

        key = compute_dedup_key("alert-1", "email", "user@example.com", ts, secret)

        # Format: {alert_id}:{channel}:{recipient_hash}:{hour_bucket}
        # The hour_bucket is an ISO format datetime
        parts = key.split(":")
        assert len(parts) >= 4  # May have more due to ISO format colons
        assert parts[0] == "alert-1"
        assert parts[1] == "email"


class TestComputeRecipientHash:
    """Test recipient hash computation."""

    def test_consistent_hash(self):
        """Test same inputs produce same hash."""
        secret = "test-secret-key-12345678"

        hash1 = compute_recipient_hash("user@example.com", "email", secret)
        hash2 = compute_recipient_hash("user@example.com", "email", secret)

        assert hash1 == hash2

    def test_different_recipient(self):
        """Test different recipients produce different hashes."""
        secret = "test-secret-key-12345678"

        hash1 = compute_recipient_hash("user1@example.com", "email", secret)
        hash2 = compute_recipient_hash("user2@example.com", "email", secret)

        assert hash1 != hash2

    def test_different_channel(self):
        """Test different channels produce different hashes."""
        secret = "test-secret-key-12345678"

        hash1 = compute_recipient_hash("user@example.com", "email", secret)
        hash2 = compute_recipient_hash("user@example.com", "sms", secret)

        assert hash1 != hash2

    def test_hash_length(self):
        """Test hash has reasonable length for storage."""
        secret = "test-secret-key-12345678"
        hash_value = compute_recipient_hash("user@example.com", "email", secret)

        # SHA256 hex digest truncated or full
        assert len(hash_value) >= 16
        assert len(hash_value) <= 64

