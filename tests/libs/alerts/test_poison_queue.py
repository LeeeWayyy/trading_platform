"""Tests for poison queue."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.alerts.poison_queue import PoisonQueue, _sanitize_error_for_log


class TestSanitizeErrorForLog:
    """Test PII sanitization in error messages."""

    def test_sanitize_email(self):
        """Test email addresses are sanitized."""
        error = "Failed to deliver to user@example.com"
        result = _sanitize_error_for_log(error)
        assert "user@example.com" not in result
        assert "[EMAIL]" in result

    def test_sanitize_multiple_emails(self):
        """Test multiple emails are sanitized."""
        error = "Failed: a@x.com and b@y.com"
        result = _sanitize_error_for_log(error)
        assert "a@x.com" not in result
        assert "b@y.com" not in result
        assert result.count("[EMAIL]") == 2

    def test_sanitize_phone_us(self):
        """Test US phone numbers are sanitized."""
        error = "SMS failed for +15551234567"
        result = _sanitize_error_for_log(error)
        assert "+15551234567" not in result
        assert "[PHONE]" in result

    def test_sanitize_phone_international(self):
        """Test international phone numbers are sanitized."""
        error = "SMS failed for +442071234567"
        result = _sanitize_error_for_log(error)
        assert "+442071234567" not in result
        assert "[PHONE]" in result

    def test_sanitize_both_email_and_phone(self):
        """Test both email and phone are sanitized."""
        error = "Contact user@test.com or +15551234567"
        result = _sanitize_error_for_log(error)
        assert "user@test.com" not in result
        assert "+15551234567" not in result
        assert "[EMAIL]" in result
        assert "[PHONE]" in result

    def test_preserves_non_pii(self):
        """Test non-PII content is preserved."""
        error = "Connection timeout after 30 seconds"
        result = _sanitize_error_for_log(error)
        assert result == error


class TestPoisonQueue:
    """Test PoisonQueue class."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

        # Setup context managers
        pool.connection = MagicMock(return_value=conn)
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor)
        conn.commit = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        cursor.fetchall = AsyncMock(return_value=[])

        return pool

    @pytest.fixture()
    def poison_queue(self, mock_db_pool):
        """Create PoisonQueue with mock database."""
        return PoisonQueue(mock_db_pool)

    @pytest.mark.asyncio()
    async def test_add_updates_status(self, poison_queue, mock_db_pool):
        """Test add() updates delivery status to POISON."""
        await poison_queue.add("delivery-123", "Test error")

        # Verify execute was called with correct status
        cursor = mock_db_pool.connection().cursor()
        cursor.execute.assert_called()
        call_args = cursor.execute.call_args
        assert "poison" in str(call_args).lower()

    @pytest.mark.asyncio()
    async def test_add_commits_transaction(self, poison_queue, mock_db_pool):
        """Test add() commits the transaction."""
        await poison_queue.add("delivery-123", "Test error")

        conn = mock_db_pool.connection()
        conn.commit.assert_called_once()

    @pytest.mark.asyncio()
    async def test_get_pending_returns_empty_list(self, poison_queue, mock_db_pool):
        """Test get_pending returns empty list when no items."""
        cursor = mock_db_pool.connection().cursor()
        cursor.fetchall = AsyncMock(return_value=[])

        result = await poison_queue.get_pending()

        assert result == []

    @pytest.mark.asyncio()
    async def test_get_pending_respects_limit(self, poison_queue, mock_db_pool):
        """Test get_pending respects limit parameter."""
        await poison_queue.get_pending(limit=50)

        cursor = mock_db_pool.connection().cursor()
        cursor.execute.assert_called()
        call_args = cursor.execute.call_args
        # Check that limit is in the query parameters
        assert 50 in call_args[0][1]

    @pytest.mark.asyncio()
    async def test_resolve_updates_status(self, poison_queue, mock_db_pool):
        """Test resolve() updates status to FAILED."""
        await poison_queue.resolve("delivery-123", "Manual resolution")

        cursor = mock_db_pool.connection().cursor()
        assert cursor.execute.call_count >= 1
        # First execute call should be the status update to FAILED
        first_call = cursor.execute.call_args_list[0]
        assert "failed" in str(first_call).lower()

    @pytest.mark.asyncio()
    async def test_resolve_commits_transaction(self, poison_queue, mock_db_pool):
        """Test resolve() commits the transaction."""
        await poison_queue.resolve("delivery-123", "Manual resolution")

        conn = mock_db_pool.connection()
        conn.commit.assert_called_once()

    @pytest.mark.asyncio()
    async def test_sync_gauge_from_db(self, poison_queue, mock_db_pool):
        """Test sync_gauge_from_db queries and returns count."""
        cursor = mock_db_pool.connection().cursor()
        cursor.fetchone = AsyncMock(return_value=(5,))

        result = await poison_queue.sync_gauge_from_db()

        assert result == 5

    @pytest.mark.asyncio()
    async def test_sync_gauge_from_db_empty(self, poison_queue, mock_db_pool):
        """Test sync_gauge_from_db returns 0 when empty."""
        cursor = mock_db_pool.connection().cursor()
        cursor.fetchone = AsyncMock(return_value=(0,))

        result = await poison_queue.sync_gauge_from_db()

        assert result == 0
