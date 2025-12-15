"""Tests for CSRF failure audit logging."""

import asyncio
from unittest.mock import AsyncMock, patch


def _run_and_close(coro):
    """Execute a coroutine to completion to avoid unawaited warnings."""

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


class TestCSRFFailureAudit:
    """Tests for CSRF failure audit logging."""

    def test_role_editor_csrf_failure_logged(self):
        """Test CSRF failure in role editor is logged to audit."""

        from apps.web_console.components.user_role_editor import _log_csrf_failure_sync

        mock_audit = AsyncMock()

        with patch("asyncio.run", side_effect=_run_and_close) as mock_run:
            _log_csrf_failure_sync(mock_audit, "admin1", "role_change", "user1")
            mock_run.assert_called_once()

    def test_strategy_assignment_csrf_failure_logged(self):
        """Test CSRF failure in strategy assignment is logged to audit."""

        from apps.web_console.components.strategy_assignment import _log_csrf_failure_sync

        mock_audit = AsyncMock()

        with patch("asyncio.run", side_effect=_run_and_close) as mock_run:
            _log_csrf_failure_sync(mock_audit, "admin1", "strategy_assignment", "user1")
            mock_run.assert_called_once()

    def test_bulk_operations_csrf_failure_logged(self):
        """Test CSRF failure in bulk operations is logged to audit."""

        from apps.web_console.components.bulk_operations import _log_csrf_failure_sync

        mock_audit = AsyncMock()

        with patch("asyncio.run", side_effect=_run_and_close) as mock_run:
            _log_csrf_failure_sync(mock_audit, "admin1", "bulk_role_change")
            mock_run.assert_called_once()
