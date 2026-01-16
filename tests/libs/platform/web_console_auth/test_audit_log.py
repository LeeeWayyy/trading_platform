"""
Unit tests for libs.platform.web_console_auth.audit_log.

Tests cover:
- AuditLogger initialization and configuration
- Audit event writing (access, action, auth, admin, export)
- Transaction context manager (_maybe_transaction)
- Fail-open behavior (graceful degradation without db_pool)
- Old event cleanup with retention window
- Error handling (OSError, TimeoutError)

Target: 85%+ branch coverage (baseline from 0%)
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.platform.web_console_auth.audit_log import AuditLogger, _maybe_transaction


class TestAuditLoggerInitialization:
    """Tests for AuditLogger initialization and configuration."""

    def test_audit_logger_init_with_defaults(self):
        """Test AuditLogger initializes with default retention days."""
        mock_db_pool = Mock()

        logger = AuditLogger(db_pool=mock_db_pool)

        assert logger.db_pool is mock_db_pool
        assert logger.retention_days == int(os.getenv("AUDIT_RETENTION_DAYS", "90"))

    def test_audit_logger_init_with_custom_retention(self):
        """Test AuditLogger initializes with custom retention days."""
        mock_db_pool = Mock()

        logger = AuditLogger(db_pool=mock_db_pool, retention_days=30)

        assert logger.retention_days == 30

    def test_audit_logger_init_without_db_pool(self):
        """Test AuditLogger initializes without db_pool (fallback mode)."""
        logger = AuditLogger(db_pool=None)

        assert logger.db_pool is None
        assert logger.retention_days > 0


class TestMaybeTransaction:
    """Tests for _maybe_transaction context manager."""

    @pytest.mark.asyncio
    async def test_maybe_transaction_with_transaction_method(self):
        """Test _maybe_transaction uses conn.transaction() when available."""
        mock_conn = Mock()
        mock_txn = AsyncMock()
        mock_conn.transaction = Mock(return_value=mock_txn)
        mock_txn.__aenter__ = AsyncMock()
        mock_txn.__aexit__ = AsyncMock()

        async with _maybe_transaction(mock_conn):
            pass

        # Verify transaction was entered
        mock_conn.transaction.assert_called_once()
        mock_txn.__aenter__.assert_called_once()
        mock_txn.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_maybe_transaction_without_transaction_method(self):
        """Test _maybe_transaction no-ops when conn.transaction not available."""
        mock_conn = Mock(spec=[])  # No transaction method

        async with _maybe_transaction(mock_conn):
            pass

        # No exception should be raised, just a no-op

    @pytest.mark.asyncio
    async def test_maybe_transaction_with_non_callable_transaction(self):
        """Test _maybe_transaction no-ops when transaction is not callable."""
        mock_conn = Mock()
        mock_conn.transaction = "not_callable"  # Not a method

        async with _maybe_transaction(mock_conn):
            pass

        # Should no-op gracefully


class TestAuditLoggerWrite:
    """Tests for AuditLogger._write() core write function."""

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_write_success_with_all_fields(self, mock_acquire):
        """Test _write() successfully writes audit event with all fields."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger._write(
            user_id="user123",
            action="delete_strategy",
            event_type="action",
            resource_type="strategy",
            resource_id="strat456",
            outcome="success",
            details={"reason": "test"},
            amr_method="mfa",
        )

        # Verify execute called with correct SQL
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO audit_log" in call_args[0][0]
        assert call_args[0][1] == (
            "user123",
            "delete_strategy",
            '{"reason": "test"}',
            "action",
            "strategy",
            "strat456",
            "success",
            "mfa",
        )

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.admin_action_total")
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_write_admin_event_increments_metric(self, mock_acquire, mock_metric):
        """Test _write() increments admin_action_total for admin events."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger._write(
            user_id="admin789",
            action="disable_user",
            event_type="admin",
            outcome="success",
        )

        # Verify admin metric incremented
        mock_metric.labels.assert_called_once_with(action="disable_user")
        mock_metric.labels.return_value.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_without_db_pool_logs_fallback(self, caplog):
        """Test _write() logs fallback when db_pool is None."""
        logger = AuditLogger(db_pool=None)

        await logger._write(
            user_id="user123",
            action="test_action",
            event_type="action",
            outcome="success",
        )

        # Verify fallback logging occurred
        assert "audit_log_fallback" in caplog.text

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.audit_write_failures_total")
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_write_handles_oserror(self, mock_acquire, mock_failure_metric, caplog):
        """Test _write() handles OSError gracefully (fail-open)."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=OSError("Connection lost"))
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        # Should not raise exception (fail-open)
        await logger._write(
            user_id="user123",
            action="test_action",
            event_type="action",
            outcome="success",
        )

        # Verify failure metric incremented
        mock_failure_metric.labels.assert_called_once_with(reason="OSError")
        mock_failure_metric.labels.return_value.inc.assert_called_once()

        # Verify exception logged
        assert "audit_log_write_failed" in caplog.text

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.audit_write_failures_total")
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_write_handles_timeout_error(self, mock_acquire, mock_failure_metric, caplog):
        """Test _write() handles TimeoutError gracefully (fail-open)."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError("INSERT timeout"))
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        # Should not raise exception (fail-open)
        await logger._write(
            user_id="user123",
            action="test_action",
            event_type="action",
            outcome="success",
        )

        # Verify failure metric incremented
        mock_failure_metric.labels.assert_called_once_with(reason="TimeoutError")
        mock_failure_metric.labels.return_value.inc.assert_called_once()


class TestAuditLoggerPublicMethods:
    """Tests for AuditLogger public logging methods."""

    @pytest.mark.asyncio
    @patch.object(AuditLogger, "_write")
    async def test_log_access(self, mock_write):
        """Test log_access() delegates to _write() with correct parameters."""
        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.log_access(
            user_id="user123",
            resource_type="strategy",
            resource_id="strat456",
            outcome="success",
            details={"ip": "192.168.1.1"},
        )

        mock_write.assert_called_once_with(
            user_id="user123",
            action="access",
            event_type="access",
            resource_type="strategy",
            resource_id="strat456",
            outcome="success",
            details={"ip": "192.168.1.1"},
        )

    @pytest.mark.asyncio
    @patch.object(AuditLogger, "_write")
    async def test_log_action(self, mock_write):
        """Test log_action() delegates to _write() with correct parameters."""
        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.log_action(
            user_id="user123",
            action="delete_strategy",
            resource_type="strategy",
            resource_id="strat456",
            outcome="success",
            details={"reason": "user requested"},
            amr_method="mfa",
        )

        mock_write.assert_called_once_with(
            user_id="user123",
            action="delete_strategy",
            event_type="action",
            resource_type="strategy",
            resource_id="strat456",
            outcome="success",
            details={"reason": "user requested"},
            amr_method="mfa",
        )

    @pytest.mark.asyncio
    @patch.object(AuditLogger, "_write")
    async def test_log_auth_event(self, mock_write):
        """Test log_auth_event() delegates to _write() with auth event type."""
        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.log_auth_event(
            user_id="user123",
            action="login",
            outcome="success",
            details={"ip": "192.168.1.1"},
            amr_method="otp",
        )

        mock_write.assert_called_once_with(
            user_id="user123",
            action="login",
            event_type="auth",
            resource_type="user",
            resource_id="user123",
            outcome="success",
            details={"ip": "192.168.1.1"},
            amr_method="otp",
        )

    @pytest.mark.asyncio
    @patch.object(AuditLogger, "_write")
    async def test_log_admin_change(self, mock_write):
        """Test log_admin_change() delegates to _write() for admin actions."""
        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.log_admin_change(
            admin_user_id="admin789",
            action="disable_user",
            target_user_id="user123",
            details={"reason": "violation"},
        )

        mock_write.assert_called_once_with(
            user_id="admin789",
            action="disable_user",
            event_type="admin",
            resource_type="user",
            resource_id="user123",
            outcome="success",
            details={"reason": "violation"},
        )

    @pytest.mark.asyncio
    @patch.object(AuditLogger, "_write")
    async def test_log_export(self, mock_write):
        """Test log_export() delegates to _write() with export details."""
        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.log_export(
            user_id="user123",
            export_type="csv",
            resource_type="trades",
            row_count=1000,
            metadata={"date_range": "2025-01"},
        )

        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args[1]
        assert call_kwargs["action"] == "export_csv"
        assert call_kwargs["event_type"] == "export"
        assert call_kwargs["details"]["row_count"] == 1000
        assert call_kwargs["details"]["metadata"]["date_range"] == "2025-01"

    @pytest.mark.asyncio
    @patch.object(AuditLogger, "_write")
    async def test_log_export_without_metadata(self, mock_write):
        """Test log_export() works without optional metadata."""
        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.log_export(
            user_id="user123",
            export_type="json",
            resource_type="positions",
            row_count=50,
        )

        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args[1]
        assert call_kwargs["details"]["row_count"] == 50
        assert "metadata" not in call_kwargs["details"]


class TestAuditLoggerCleanup:
    """Tests for cleanup_old_events() retention management."""

    @pytest.mark.asyncio
    async def test_cleanup_old_events_without_db_pool(self):
        """Test cleanup_old_events() returns 0 when db_pool is None."""
        logger = AuditLogger(db_pool=None)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_cleanup_old_events_success(self, mock_acquire):
        """Test cleanup_old_events() deletes old events and returns rowcount."""
        mock_conn = AsyncMock()
        mock_result = Mock()
        mock_result.rowcount = 500  # 500 rows deleted
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool, retention_days=90)

        deleted = await logger.cleanup_old_events()

        assert deleted == 500
        # Verify DELETE query executed
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "DELETE FROM audit_log" in call_args[0][0]
        assert "WHERE timestamp <" in call_args[0][0]

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_cleanup_old_events_missing_rowcount(self, mock_acquire, caplog):
        """Test cleanup_old_events() handles missing rowcount gracefully."""
        mock_conn = AsyncMock()
        mock_result = Mock(spec=[])  # No rowcount attribute
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0
        # Verify warning logged
        assert "audit_log_cleanup_missing_rowcount" in caplog.text

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_cleanup_old_events_handles_oserror(self, mock_acquire, caplog):
        """Test cleanup_old_events() handles OSError gracefully."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=OSError("Connection lost"))
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0
        # Verify exception logged
        assert "audit_log_cleanup_failed" in caplog.text

    @pytest.mark.asyncio
    @patch("libs.platform.web_console_auth.audit_log.acquire_connection")
    async def test_cleanup_old_events_handles_timeout(self, mock_acquire, caplog):
        """Test cleanup_old_events() handles TimeoutError gracefully."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError("DELETE timeout"))
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0
        # Verify exception logged
        assert "audit_log_cleanup_failed" in caplog.text
