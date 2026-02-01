"""
Unit tests for libs.platform.web_console_auth.audit_logger.

Tests cover:
- AuditLogger initialization and Prometheus metrics exports
- Audit event writing with broader exception handling
- Transaction context manager
- Public logging methods (access, action, auth, admin, export)
- Old event cleanup
- Fail-open behavior

Target: 85%+ branch coverage (baseline from 0%)
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.platform.web_console_auth.audit_logger import (
    AuditLogger,
    _maybe_transaction,
    admin_action_total,
    audit_cleanup_duration_seconds,
    audit_events_total,
    audit_write_failures_total,
    audit_write_latency_seconds,
)


class TestPrometheusMetricsExports:
    """Tests for exported Prometheus metrics."""

    def test_audit_events_total_exported(self):
        """Test audit_events_total Counter is exported."""
        assert audit_events_total is not None
        assert hasattr(audit_events_total, "labels")

    def test_audit_write_failures_total_exported(self):
        """Test audit_write_failures_total Counter is exported."""
        assert audit_write_failures_total is not None
        assert hasattr(audit_write_failures_total, "labels")

    def test_admin_action_total_exported(self):
        """Test admin_action_total Counter is exported."""
        assert admin_action_total is not None
        assert hasattr(admin_action_total, "labels")

    def test_audit_write_latency_seconds_exported(self):
        """Test audit_write_latency_seconds Histogram is exported."""
        assert audit_write_latency_seconds is not None
        assert hasattr(audit_write_latency_seconds, "observe")

    def test_audit_cleanup_duration_seconds_exported(self):
        """Test audit_cleanup_duration_seconds Histogram is exported."""
        assert audit_cleanup_duration_seconds is not None
        assert hasattr(audit_cleanup_duration_seconds, "observe")


class TestAuditLoggerInitialization:
    """Tests for AuditLogger initialization."""

    def test_audit_logger_init_with_defaults(self):
        """Test AuditLogger initializes with default retention days."""
        mock_db_pool = Mock()

        logger = AuditLogger(db_pool=mock_db_pool)

        assert logger.db_pool is mock_db_pool
        assert logger.retention_days >= 90  # Default from env

    def test_audit_logger_init_with_custom_retention(self):
        """Test AuditLogger initializes with custom retention days."""
        mock_db_pool = Mock()

        logger = AuditLogger(db_pool=mock_db_pool, retention_days=30)

        assert logger.retention_days == 30

    def test_audit_logger_init_without_db_pool(self):
        """Test AuditLogger initializes without db_pool (fallback mode)."""
        logger = AuditLogger(db_pool=None)

        assert logger.db_pool is None


class TestMaybeTransaction:
    """Tests for _maybe_transaction context manager."""

    @pytest.mark.asyncio()
    async def test_maybe_transaction_with_transaction_method(self):
        """Test _maybe_transaction uses conn.transaction() when available."""
        mock_conn = Mock()
        mock_txn = AsyncMock()
        mock_conn.transaction = Mock(return_value=mock_txn)
        mock_txn.__aenter__ = AsyncMock()
        mock_txn.__aexit__ = AsyncMock()

        async with _maybe_transaction(mock_conn):
            pass

        mock_conn.transaction.assert_called_once()
        mock_txn.__aenter__.assert_called_once()
        mock_txn.__aexit__.assert_called_once()

    @pytest.mark.asyncio()
    async def test_maybe_transaction_without_transaction_method(self):
        """Test _maybe_transaction no-ops when transaction not available."""
        mock_conn = Mock(spec=[])

        async with _maybe_transaction(mock_conn):
            pass

        # Should complete without exception


class TestAuditLoggerWrite:
    """Tests for AuditLogger._write() core write function."""

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_write_success_with_all_fields(self, mock_acquire):
        """Test _write() successfully writes audit event."""
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

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO audit_log" in call_args[0][0]

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.admin_action_total")
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
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

        mock_metric.labels.assert_called_once_with(action="disable_user")
        mock_metric.labels.return_value.inc.assert_called_once()

    @pytest.mark.asyncio()
    async def test_write_without_db_pool_logs_fallback(self, caplog):
        """Test _write() logs fallback when db_pool is None."""
        import logging

        caplog.set_level(logging.INFO)
        logger = AuditLogger(db_pool=None)

        await logger._write(
            user_id="user123",
            action="test_action",
            event_type="action",
            outcome="success",
        )

        assert "audit_log_fallback" in caplog.text

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.audit_write_failures_total")
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_write_handles_exception(self, mock_acquire, mock_failure_metric, caplog):
        """Test _write() handles broad Exception gracefully (fail-open)."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB error"))
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

        mock_failure_metric.labels.assert_called_once_with(reason="RuntimeError")
        mock_failure_metric.labels.return_value.inc.assert_called_once()
        assert "audit_log_write_failed" in caplog.text

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.audit_write_latency_seconds")
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_write_records_latency_metric(self, mock_acquire, mock_latency):
        """Test _write() records latency metric on success."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger._write(
            user_id="user123",
            action="test",
            event_type="action",
            outcome="success",
        )

        # Verify latency was observed
        mock_latency.observe.assert_called_once()
        observed_duration = mock_latency.observe.call_args[0][0]
        assert isinstance(observed_duration, float)
        assert observed_duration >= 0

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.audit_write_latency_seconds")
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_write_records_latency_on_exception(self, mock_acquire, mock_latency):
        """Test _write() records latency metric even on exception (finally block)."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=ValueError("Error"))
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger._write(
            user_id="user123",
            action="test",
            event_type="action",
            outcome="success",
        )

        # Verify latency was observed despite exception
        mock_latency.observe.assert_called_once()


class TestAuditLoggerPublicMethods:
    """Tests for AuditLogger public logging methods."""

    @pytest.mark.asyncio()
    @patch.object(AuditLogger, "_write")
    async def test_log_access(self, mock_write):
        """Test log_access() delegates to _write()."""
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
            ip_address=None,
            session_id=None,
        )

    @pytest.mark.asyncio()
    @patch.object(AuditLogger, "_write")
    async def test_log_action(self, mock_write):
        """Test log_action() delegates to _write()."""
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
            ip_address=None,
            session_id=None,
        )

    @pytest.mark.asyncio()
    @patch.object(AuditLogger, "_write")
    async def test_log_auth_event(self, mock_write):
        """Test log_auth_event() delegates to _write()."""
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

    @pytest.mark.asyncio()
    @patch.object(AuditLogger, "_write")
    async def test_log_admin_change(self, mock_write):
        """Test log_admin_change() delegates to _write()."""
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

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
    async def test_cleanup_old_events_without_db_pool(self):
        """Test cleanup_old_events() returns 0 when db_pool is None."""
        logger = AuditLogger(db_pool=None)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_cleanup_old_events_success(self, mock_acquire):
        """Test cleanup_old_events() deletes old events and returns rowcount."""
        mock_conn = AsyncMock()
        mock_result = Mock()
        mock_result.rowcount = 500
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool, retention_days=90)

        deleted = await logger.cleanup_old_events()

        assert deleted == 500
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "DELETE FROM audit_log" in call_args[0][0]

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_cleanup_old_events_missing_rowcount(self, mock_acquire, caplog):
        """Test cleanup_old_events() handles missing rowcount gracefully."""
        mock_conn = AsyncMock()
        mock_result = Mock(spec=[])
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0
        assert "audit_log_cleanup_missing_rowcount" in caplog.text

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_cleanup_old_events_handles_exception(self, mock_acquire, caplog):
        """Test cleanup_old_events() handles Exception gracefully."""
        mock_acquire.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        deleted = await logger.cleanup_old_events()

        assert deleted == 0
        assert "audit_log_cleanup_failed" in caplog.text

    @pytest.mark.asyncio()
    @patch("libs.platform.web_console_auth.audit_logger.audit_cleanup_duration_seconds")
    @patch("libs.platform.web_console_auth.audit_logger.acquire_connection")
    async def test_cleanup_old_events_records_duration(self, mock_acquire, mock_duration):
        """Test cleanup_old_events() records duration metric."""
        mock_conn = AsyncMock()
        mock_result = Mock()
        mock_result.rowcount = 100
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.transaction = Mock(return_value=AsyncMock())
        mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.return_value.__aexit__ = AsyncMock()

        mock_db_pool = Mock()
        logger = AuditLogger(db_pool=mock_db_pool)

        await logger.cleanup_old_events()

        # Verify duration was observed
        mock_duration.observe.assert_called_once()
        observed_duration = mock_duration.observe.call_args[0][0]
        assert isinstance(observed_duration, float)
        assert observed_duration >= 0
