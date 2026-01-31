"""Tests for order audit panel in apps/web_console_ng/components/order_audit_panel.py.

Tests the OrderAuditPanel component including data fetching, formatting,
and UI rendering.
"""

from __future__ import annotations

import os

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.web_console_ng.components.order_audit_panel import (
    ACTION_LABELS,
    OUTCOME_COLORS,
    OrderAuditPanel,
    fetch_audit_trail,
    format_action,
    format_timestamp,
    get_outcome_class,
    show_order_audit_dialog,
)


class TestFormatTimestamp:
    """Tests for format_timestamp helper function."""

    def test_format_datetime_object(self) -> None:
        """Format datetime object."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        result = format_timestamp(dt)
        assert result == "2024-01-15 10:30:45"

    def test_format_iso_string(self) -> None:
        """Format ISO string timestamp."""
        result = format_timestamp("2024-01-15T10:30:45+00:00")
        assert result == "2024-01-15 10:30:45"

    def test_format_iso_string_with_z(self) -> None:
        """Format ISO string with Z suffix."""
        result = format_timestamp("2024-01-15T10:30:45Z")
        assert result == "2024-01-15 10:30:45"

    def test_format_invalid_string(self) -> None:
        """Invalid string returns original."""
        result = format_timestamp("not-a-date")
        assert result == "not-a-date"

    def test_format_empty_string(self) -> None:
        """Empty string returns empty."""
        result = format_timestamp("")
        assert result == ""


class TestFormatAction:
    """Tests for format_action helper function."""

    def test_format_known_actions(self) -> None:
        """Known action types are formatted correctly."""
        assert format_action("submit") == "Order Submitted"
        assert format_action("cancel") == "Cancellation Requested"
        assert format_action("fill") == "Fill Received"
        assert format_action("reject") == "Order Rejected"

    def test_format_case_insensitive(self) -> None:
        """Action formatting is case insensitive."""
        assert format_action("SUBMIT") == "Order Submitted"
        assert format_action("Submit") == "Order Submitted"
        assert format_action("CANCEL") == "Cancellation Requested"

    def test_format_unknown_action(self) -> None:
        """Unknown actions are title-cased with underscores replaced."""
        assert format_action("custom_action") == "Custom Action"
        assert format_action("unknown") == "Unknown"

    def test_format_all_known_labels(self) -> None:
        """All known actions have labels."""
        for action in ACTION_LABELS:
            result = format_action(action)
            assert result == ACTION_LABELS[action]


class TestGetOutcomeClass:
    """Tests for get_outcome_class helper function."""

    def test_success_outcome(self) -> None:
        """Success outcome returns green class."""
        result = get_outcome_class("success")
        assert "green" in result

    def test_failure_outcome(self) -> None:
        """Failure outcome returns red class."""
        result = get_outcome_class("failure")
        assert "red" in result

    def test_pending_outcome(self) -> None:
        """Pending outcome returns yellow class."""
        result = get_outcome_class("pending")
        assert "yellow" in result

    def test_error_outcome(self) -> None:
        """Error outcome returns red class."""
        result = get_outcome_class("error")
        assert "red" in result

    def test_case_insensitive(self) -> None:
        """Outcome class lookup is case insensitive."""
        assert get_outcome_class("SUCCESS") == get_outcome_class("success")
        assert get_outcome_class("Failure") == get_outcome_class("failure")

    def test_unknown_outcome(self) -> None:
        """Unknown outcome returns gray class."""
        result = get_outcome_class("unknown")
        assert "gray" in result

    def test_all_known_outcomes(self) -> None:
        """All known outcomes have colors."""
        for outcome in OUTCOME_COLORS:
            result = get_outcome_class(outcome)
            assert result == OUTCOME_COLORS[outcome]


class TestFetchAuditTrail:
    """Tests for fetch_audit_trail function."""

    @pytest.mark.asyncio
    async def test_fetch_success(self) -> None:
        """Successful fetch returns audit data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-123",
            "entries": [
                {
                    "id": 1,
                    "timestamp": "2024-01-15T10:30:00Z",
                    "action": "submit",
                    "outcome": "success",
                }
            ],
            "total_count": 1,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await fetch_audit_trail(
                client_order_id="order-123",
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

        assert result is not None
        assert result["client_order_id"] == "order-123"
        assert len(result["entries"]) == 1

    @pytest.mark.asyncio
    async def test_fetch_not_found(self) -> None:
        """404 response returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await fetch_audit_trail(
                client_order_id="nonexistent",
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_connection_error(self) -> None:
        """Connection error returns None."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.RequestError("Connection refused")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await fetch_audit_trail(
                client_order_id="order-123",
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_sends_correct_headers(self) -> None:
        """Fetch sends correct authentication headers."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"entries": [], "total_count": 0}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            await fetch_audit_trail(
                client_order_id="order-123",
                user_id="test_user",
                role="trader",
                strategies=["alpha", "beta"],
            )

            call_args = mock_instance.get.call_args
            headers = call_args.kwargs.get("headers", {})
            assert headers["X-User-ID"] == "test_user"
            assert headers["X-User-Role"] == "trader"
            assert headers["X-User-Strategies"] == "alpha,beta"


class TestOrderAuditPanel:
    """Tests for OrderAuditPanel class."""

    def test_init(self) -> None:
        """Basic initialization."""
        panel = OrderAuditPanel(
            user_id="test_user",
            role="trader",
            strategies=["alpha_baseline"],
        )
        assert panel.user_id == "test_user"
        assert panel.role == "trader"
        assert panel.strategies == ["alpha_baseline"]
        assert panel._dialog is None
        assert panel._current_order_id is None

    def test_init_empty_strategies(self) -> None:
        """Initialization with empty strategies."""
        panel = OrderAuditPanel(
            user_id="test_user",
            role="viewer",
            strategies=[],
        )
        assert panel.strategies == []

    @pytest.mark.asyncio
    async def test_show_creates_dialog(self) -> None:
        """Show method creates dialog if not exists."""
        panel = OrderAuditPanel(
            user_id="test_user",
            role="trader",
            strategies=[],
        )

        # Start with no dialog
        assert panel._dialog is None

        def mock_create_dialog() -> None:
            # Simulate what _create_dialog does
            panel._dialog = MagicMock()
            panel._content_container = MagicMock()

        with patch.object(panel, "_create_dialog", side_effect=mock_create_dialog) as mock_create:
            with patch.object(panel, "_load_content", new_callable=AsyncMock):
                await panel.show("order-123")

                mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_show_sets_order_id(self) -> None:
        """Show method sets current order ID."""
        panel = OrderAuditPanel(
            user_id="test_user",
            role="trader",
            strategies=[],
        )

        with patch.object(panel, "_create_dialog"):
            with patch.object(panel, "_load_content", new_callable=AsyncMock):
                panel._dialog = MagicMock()
                panel._content_container = MagicMock()

                await panel.show("order-456")

                assert panel._current_order_id == "order-456"


class TestShowOrderAuditDialog:
    """Tests for show_order_audit_dialog convenience function."""

    @pytest.mark.asyncio
    async def test_creates_panel_and_shows(self) -> None:
        """Convenience function creates panel and calls show."""
        with patch(
            "apps.web_console_ng.components.order_audit_panel.OrderAuditPanel"
        ) as mock_panel_class:
            mock_panel = MagicMock()
            mock_panel.show = AsyncMock()
            mock_panel_class.return_value = mock_panel

            await show_order_audit_dialog(
                client_order_id="order-789",
                user_id="test_user",
                role="trader",
                strategies=["alpha"],
            )

            mock_panel_class.assert_called_once_with("test_user", "trader", ["alpha"])
            mock_panel.show.assert_called_once_with("order-789")


class TestRenderAuditTable:
    """Tests for _render_audit_table function."""

    def test_render_empty_entries(self) -> None:
        """Rendering empty entries list."""
        from apps.web_console_ng.components.order_audit_panel import _render_audit_table

        with patch("apps.web_console_ng.components.order_audit_panel.ui") as mock_ui:
            mock_ui.table.return_value = MagicMock()

            _render_audit_table([])

            mock_ui.table.assert_called_once()
            call_args = mock_ui.table.call_args
            assert call_args.kwargs["rows"] == []

    def test_render_formats_entries(self) -> None:
        """Rendering formats entry data correctly."""
        from apps.web_console_ng.components.order_audit_panel import _render_audit_table

        entries = [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "action": "submit",
                "outcome": "success",
                "user_id": "test_user",
                "ip_address": "192.168.1.1",
                "details": {"symbol": "AAPL", "side": "buy", "qty": 100},
            }
        ]

        with patch("apps.web_console_ng.components.order_audit_panel.ui") as mock_ui:
            mock_ui.table.return_value = MagicMock()

            _render_audit_table(entries)

            call_args = mock_ui.table.call_args
            rows = call_args.kwargs["rows"]
            assert len(rows) == 1
            assert rows[0]["action"] == "Order Submitted"
            assert rows[0]["user_id"] == "test_user"
            assert rows[0]["ip_address"] == "192.168.1.1"
            assert "symbol=AAPL" in rows[0]["details_str"]

    def test_render_handles_missing_fields(self) -> None:
        """Rendering handles missing optional fields."""
        from apps.web_console_ng.components.order_audit_panel import _render_audit_table

        entries = [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "action": "fill",
                "outcome": "success",
                # Missing: user_id, ip_address, details
            }
        ]

        with patch("apps.web_console_ng.components.order_audit_panel.ui") as mock_ui:
            mock_ui.table.return_value = MagicMock()

            _render_audit_table(entries)

            call_args = mock_ui.table.call_args
            rows = call_args.kwargs["rows"]
            assert rows[0]["user_id"] == "-"
            assert rows[0]["ip_address"] == "-"
            assert rows[0]["details_str"] == "-"

    def test_render_truncates_long_details(self) -> None:
        """Rendering truncates details with many fields."""
        from apps.web_console_ng.components.order_audit_panel import _render_audit_table

        entries = [
            {
                "timestamp": "2024-01-15T10:30:00Z",
                "action": "submit",
                "outcome": "success",
                "details": {
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 100,
                    "price": 150.0,
                    "reason": "manual",
                    "status": "new",
                },
            }
        ]

        with patch("apps.web_console_ng.components.order_audit_panel.ui") as mock_ui:
            mock_ui.table.return_value = MagicMock()

            _render_audit_table(entries)

            call_args = mock_ui.table.call_args
            rows = call_args.kwargs["rows"]
            # Should only show first 3 key fields and add "..."
            assert "..." in rows[0]["details_str"]
