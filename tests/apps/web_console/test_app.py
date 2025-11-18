"""
Tests for Web Console Main Application.

Tests dashboard rendering, order entry, kill switch, and API integration.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.web_console import app


class TestAPIHelpers:
    """Test API helper functions."""

    def test_fetch_api_get_success(self):
        """Test successful GET request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}

        with patch("requests.get", return_value=mock_response) as mock_get:
            result = app.fetch_api("positions", method="GET")

        assert result == {"status": "ok"}
        mock_get.assert_called_once()
        mock_response.raise_for_status.assert_called_once()

    def test_fetch_api_post_success(self):
        """Test successful POST request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"client_order_id": "abc123"}

        post_data = {"symbol": "AAPL", "side": "buy", "qty": 10}

        with patch("requests.post", return_value=mock_response) as mock_post:
            result = app.fetch_api("submit_order", method="POST", data=post_data)

        assert result == {"client_order_id": "abc123"}
        mock_post.assert_called_once_with(
            app.config.ENDPOINTS["submit_order"], json=post_data, timeout=5
        )

    def test_fetch_api_delete_success(self):
        """Test successful DELETE request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"deleted": True}

        with patch("requests.delete", return_value=mock_response) as mock_delete:
            result = app.fetch_api("kill_switch_status", method="DELETE")

        assert result == {"deleted": True}
        mock_delete.assert_called_once()

    def test_fetch_api_timeout_error(self):
        """Test API timeout error."""
        with patch("requests.get", side_effect=requests.exceptions.Timeout):
            with pytest.raises(requests.exceptions.Timeout):
                app.fetch_api("positions")

    def test_fetch_api_connection_error(self):
        """Test API connection error."""
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
            with pytest.raises(requests.exceptions.ConnectionError):
                app.fetch_api("positions")

    def test_fetch_api_http_error(self):
        """Test API HTTP error (4xx, 5xx)."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(requests.exceptions.HTTPError):
                app.fetch_api("positions")

    def test_fetch_api_unsupported_method(self):
        """Test unsupported HTTP method."""
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            app.fetch_api("positions", method="PATCH")


class TestCachedFetchers:
    """Test cached API fetch functions."""

    def test_fetch_positions_cached(self):
        """Test positions fetching with cache."""
        mock_data = {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": 10,
                    "avg_entry_price": 150.0,
                }
            ],
            "total_positions": 1,
        }

        with patch("apps.web_console.app.fetch_api", return_value=mock_data):
            result = app.fetch_positions()

        assert result == mock_data
        assert result["total_positions"] == 1

    def test_fetch_realtime_pnl_cached(self):
        """Test real-time P&L fetching with cache."""
        mock_data = {
            "positions": [],
            "total_positions": 0,
            "total_unrealized_pl": 0,
            "realtime_prices_available": 0,
        }

        with patch("apps.web_console.app.fetch_api", return_value=mock_data):
            result = app.fetch_realtime_pnl()

        assert result == mock_data

    def test_fetch_kill_switch_status_cached(self):
        """Test kill switch status fetching with cache."""
        mock_data = {"state": "ACTIVE"}

        with patch("apps.web_console.app.fetch_api", return_value=mock_data):
            result = app.fetch_kill_switch_status()

        assert result == mock_data
        assert result["state"] == "ACTIVE"


class TestAuditLog:
    """Test audit logging function."""

    def test_audit_log_manual_order(self, capsys):
        """Test audit log for manual order."""
        mock_user_info = {
            "username": "test_user",
            "session_id": "abc123",
        }

        with patch("apps.web_console.app.auth.get_current_user", return_value=mock_user_info):
            app.audit_log(
                action="manual_order",
                details={"symbol": "AAPL", "side": "buy", "qty": 10},
                reason="Test order",
            )

        captured = capsys.readouterr()
        assert "[AUDIT]" in captured.out
        assert "manual_order" in captured.out
        assert "test_user" in captured.out
        assert "Test order" in captured.out

    def test_audit_log_kill_switch(self, capsys):
        """Test audit log for kill switch action."""
        mock_user_info = {
            "username": "ops_team",
            "session_id": "xyz789",
        }

        with patch("apps.web_console.app.auth.get_current_user", return_value=mock_user_info):
            app.audit_log(
                action="kill_switch_engage",
                details={"operator": "ops_team"},
                reason="Market anomaly",
            )

        captured = capsys.readouterr()
        assert "[AUDIT]" in captured.out
        assert "kill_switch_engage" in captured.out
        assert "ops_team" in captured.out
        assert "Market anomaly" in captured.out


class TestDashboard:
    """Test dashboard rendering (basic smoke tests)."""

    def test_render_dashboard_no_positions(self):
        """Test dashboard with no positions."""
        mock_pnl_data = {
            "positions": [],
            "total_positions": 0,
            "total_unrealized_pl": 0,
            "realtime_prices_available": 0,
            "timestamp": "2024-11-17T10:00:00",
        }
        mock_kill_switch = {"state": "ACTIVE"}
        mock_config = {"dry_run": True, "environment": "dev"}

        with patch("apps.web_console.app.fetch_realtime_pnl", return_value=mock_pnl_data):
            with patch(
                "apps.web_console.app.fetch_kill_switch_status", return_value=mock_kill_switch
            ):
                with patch(
                    "apps.web_console.app.fetch_gateway_config", return_value=mock_config
                ):
                    with patch("apps.web_console.app.st"):
                        # Should not raise exception
                        app.render_dashboard()

    def test_render_dashboard_with_positions(self):
        """Test dashboard with positions."""
        mock_pnl_data = {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": 10,
                    "avg_entry_price": 150.0,
                    "current_price": 152.5,
                    "unrealized_pl": 25.0,
                    "unrealized_pl_pct": 1.67,
                    "price_source": "real-time",
                }
            ],
            "total_positions": 1,
            "total_unrealized_pl": 25.0,
            "total_unrealized_pl_pct": 1.67,
            "realtime_prices_available": 1,
            "timestamp": "2024-11-17T10:00:00",
        }
        mock_kill_switch = {"state": "ACTIVE"}
        mock_config = {"dry_run": False, "environment": "production"}

        with patch("apps.web_console.app.fetch_realtime_pnl", return_value=mock_pnl_data):
            with patch(
                "apps.web_console.app.fetch_kill_switch_status", return_value=mock_kill_switch
            ):
                with patch(
                    "apps.web_console.app.fetch_gateway_config", return_value=mock_config
                ):
                    with patch("apps.web_console.app.st"):
                        # Should not raise exception
                        app.render_dashboard()

    def test_render_dashboard_kill_switch_engaged(self):
        """Test dashboard with kill switch engaged."""
        mock_pnl_data = {
            "positions": [],
            "total_positions": 0,
            "total_unrealized_pl": 0,
            "realtime_prices_available": 0,
            "timestamp": "2024-11-17T10:00:00",
        }
        mock_kill_switch = {"state": "ENGAGED", "engaged_by": "ops_team"}
        mock_config = {"dry_run": True}

        with patch("apps.web_console.app.fetch_realtime_pnl", return_value=mock_pnl_data):
            with patch(
                "apps.web_console.app.fetch_kill_switch_status", return_value=mock_kill_switch
            ):
                with patch(
                    "apps.web_console.app.fetch_gateway_config", return_value=mock_config
                ):
                    with patch("apps.web_console.app.st") as mock_st:
                        app.render_dashboard()

                        # Should show error banner
                        mock_st.error.assert_called()


class TestManualOrderEntry:
    """Test manual order entry (basic smoke tests)."""

    def test_render_manual_order_entry_initial(self):
        """Test initial order entry form rendering."""
        # Mock session state without pending confirmation
        mock_session_state = {
            "order_confirmation_pending": False,
            "order_preview": None,
        }

        with patch("apps.web_console.app.st.session_state", mock_session_state):
            with patch("apps.web_console.app.st"):
                # Should not raise exception
                app.render_manual_order_entry()


class TestKillSwitch:
    """Test kill switch rendering (basic smoke tests)."""

    def test_render_kill_switch_active(self):
        """Test kill switch rendering when active."""
        mock_status = {"state": "ACTIVE"}

        with patch("apps.web_console.app.fetch_kill_switch_status", return_value=mock_status):
            with patch("apps.web_console.app.st"):
                with patch("apps.web_console.app.auth.get_current_user") as mock_user:
                    mock_user.return_value = {"username": "test_user"}
                    # Should not raise exception
                    app.render_kill_switch()

    def test_render_kill_switch_engaged(self):
        """Test kill switch rendering when engaged."""
        mock_status = {
            "state": "ENGAGED",
            "engaged_by": "ops_team",
            "engagement_reason": "Test",
            "engaged_at": "2024-11-17T10:00:00",
        }

        with patch("apps.web_console.app.fetch_kill_switch_status", return_value=mock_status):
            with patch("apps.web_console.app.st"):
                with patch("apps.web_console.app.auth.get_current_user") as mock_user:
                    mock_user.return_value = {"username": "test_user"}
                    # Should not raise exception
                    app.render_kill_switch()


class TestAuditLogViewer:
    """Test audit log viewer rendering (basic smoke tests)."""

    def test_render_audit_log(self):
        """Test audit log viewer rendering."""
        with patch("apps.web_console.app.st"):
            # Should not raise exception (placeholder implementation)
            app.render_audit_log()
