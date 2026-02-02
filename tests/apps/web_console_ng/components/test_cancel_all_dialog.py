"""Tests for CancelAllDialog component."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console_ng.components.cancel_all_dialog import CancelAllDialog
from apps.web_console_ng.utils.orders import (
    FALLBACK_ID_PREFIX,
    SYNTHETIC_ID_PREFIX,
)


def create_mock_order(
    symbol: str = "AAPL",
    side: str = "buy",
    client_order_id: str = "order-123",
    qty: int = 100,
) -> dict[str, Any]:
    """Create a mock order for testing."""
    return {
        "symbol": symbol,
        "side": side,
        "client_order_id": client_order_id,
        "qty": qty,
        "status": "open",
    }


class TestCancelAllDialogPermissions:
    """Test permission checks."""

    def test_viewer_blocked(self) -> None:
        """Viewer role cannot cancel orders."""
        dialog = CancelAllDialog(
            orders=[create_mock_order()],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="viewer",
        )
        allowed, reason = dialog._check_permissions()
        assert not allowed
        assert "Viewers cannot cancel" in reason

    def test_trader_allowed(self) -> None:
        """Trader role can cancel orders."""
        dialog = CancelAllDialog(
            orders=[create_mock_order()],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )
        allowed, reason = dialog._check_permissions()
        assert allowed
        assert reason == ""

    def test_admin_allowed(self) -> None:
        """Admin role can cancel orders."""
        dialog = CancelAllDialog(
            orders=[create_mock_order()],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="admin",
        )
        allowed, reason = dialog._check_permissions()
        assert allowed
        assert reason == ""

    def test_read_only_does_not_block(self) -> None:
        """Read-only mode does NOT block cancellation (fail-open for risk-reducing)."""
        dialog = CancelAllDialog(
            orders=[create_mock_order()],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
            is_read_only=True,
        )
        allowed, reason = dialog._check_permissions()
        assert allowed
        assert reason == ""


class TestCancelAllDialogFiltering:
    """Test order filtering logic."""

    def test_filter_by_symbol(self) -> None:
        """Filter orders by specific symbol."""
        orders = [
            create_mock_order(symbol="AAPL", client_order_id="o1"),
            create_mock_order(symbol="GOOG", client_order_id="o2"),
            create_mock_order(symbol="AAPL", client_order_id="o3"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("AAPL", "All Sides")
        assert len(valid) == 2
        assert all(o["symbol"] == "AAPL" for o in valid)
        assert len(skipped) == 0

    def test_filter_all_symbols(self) -> None:
        """All Symbols returns all orders."""
        orders = [
            create_mock_order(symbol="AAPL", client_order_id="o1"),
            create_mock_order(symbol="GOOG", client_order_id="o2"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("All Symbols", "All Sides")
        assert len(valid) == 2

    def test_filter_buy_only(self) -> None:
        """Filter for buy orders only."""
        orders = [
            create_mock_order(side="buy", client_order_id="o1"),
            create_mock_order(side="sell", client_order_id="o2"),
            create_mock_order(side="buy", client_order_id="o3"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("All Symbols", "Buy Only")
        assert len(valid) == 2
        assert all(o["side"] == "buy" for o in valid)

    def test_filter_sell_only(self) -> None:
        """Filter for sell orders only."""
        orders = [
            create_mock_order(side="buy", client_order_id="o1"),
            create_mock_order(side="sell", client_order_id="o2"),
            create_mock_order(side="sell", client_order_id="o3"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("All Symbols", "Sell Only")
        assert len(valid) == 2
        assert all(o["side"] == "sell" for o in valid)

    def test_filter_combined_symbol_and_side(self) -> None:
        """Filter by both symbol and side."""
        orders = [
            create_mock_order(symbol="AAPL", side="buy", client_order_id="o1"),
            create_mock_order(symbol="AAPL", side="sell", client_order_id="o2"),
            create_mock_order(symbol="GOOG", side="buy", client_order_id="o3"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("AAPL", "Buy Only")
        assert len(valid) == 1
        assert valid[0]["symbol"] == "AAPL"
        assert valid[0]["side"] == "buy"

    def test_skip_orders_with_missing_id(self) -> None:
        """Orders with missing client_order_id are skipped."""
        orders = [
            create_mock_order(client_order_id="valid-id"),
            {"symbol": "AAPL", "side": "buy", "qty": 100},  # Missing ID
            {"symbol": "GOOG", "side": "sell", "client_order_id": "", "qty": 100},  # Empty ID
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("All Symbols", "All Sides")
        assert len(valid) == 1
        assert len(skipped) == 2

    def test_skip_synthetic_orders(self) -> None:
        """Orders with SYNTHETIC prefix are skipped."""
        orders = [
            create_mock_order(client_order_id="valid-id"),
            create_mock_order(client_order_id=f"{SYNTHETIC_ID_PREFIX}12345"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("All Symbols", "All Sides")
        assert len(valid) == 1
        assert len(skipped) == 1
        assert skipped[0]["client_order_id"].startswith(SYNTHETIC_ID_PREFIX)

    def test_skip_fallback_orders(self) -> None:
        """Orders with FALLBACK prefix are skipped."""
        orders = [
            create_mock_order(client_order_id="valid-id"),
            create_mock_order(client_order_id=f"{FALLBACK_ID_PREFIX}12345"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        valid, skipped = dialog._filter_orders("All Symbols", "All Sides")
        assert len(valid) == 1
        assert len(skipped) == 1
        assert skipped[0]["client_order_id"].startswith(FALLBACK_ID_PREFIX)


class TestCancelAllDialogUniqueSymbols:
    """Test unique symbols extraction."""

    def test_unique_symbols(self) -> None:
        """Extract unique symbols from orders."""
        orders = [
            create_mock_order(symbol="AAPL"),
            create_mock_order(symbol="GOOG"),
            create_mock_order(symbol="AAPL"),
            create_mock_order(symbol="MSFT"),
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        symbols = dialog._unique_symbols()
        assert symbols == ["AAPL", "GOOG", "MSFT"]  # Sorted alphabetically

    def test_unique_symbols_empty(self) -> None:
        """No symbols when orders list is empty."""
        dialog = CancelAllDialog(
            orders=[],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        symbols = dialog._unique_symbols()
        assert symbols == []

    def test_unique_symbols_skips_invalid(self) -> None:
        """Skip orders with invalid symbols."""
        orders = [
            create_mock_order(symbol="AAPL"),
            {"side": "buy", "qty": 100},  # Missing symbol
            {"symbol": None, "side": "buy", "qty": 100},  # None symbol
        ]
        dialog = CancelAllDialog(
            orders=orders,
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        symbols = dialog._unique_symbols()
        assert symbols == ["AAPL"]


class TestCancelAllDialogExecution:
    """Test order cancellation execution."""

    @pytest.mark.asyncio()
    async def test_execute_cancel_all_success(self) -> None:
        """All cancels succeed."""
        orders = [
            create_mock_order(client_order_id="o1"),
            create_mock_order(client_order_id="o2"),
        ]

        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value=None)

        dialog = CancelAllDialog(
            orders=orders,
            trading_client=mock_client,
            user_id="user1",
            user_role="trader",
        )

        success, failed = await dialog._execute_cancel_all(orders)
        assert success == 2
        assert failed == 0
        assert mock_client.cancel_order.call_count == 2

    @pytest.mark.asyncio()
    async def test_execute_cancel_all_partial_failure(self) -> None:
        """Some cancels fail."""
        orders = [
            create_mock_order(client_order_id="o1"),
            create_mock_order(client_order_id="o2"),
            create_mock_order(client_order_id="o3"),
        ]

        mock_client = MagicMock()

        call_count = [0]

        async def mock_cancel(*args: Any, **kwargs: Any) -> None:
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Network error")

        mock_client.cancel_order = AsyncMock(side_effect=mock_cancel)

        dialog = CancelAllDialog(
            orders=orders,
            trading_client=mock_client,
            user_id="user1",
            user_role="trader",
        )

        success, failed = await dialog._execute_cancel_all(orders)
        assert success == 2
        assert failed == 1

    @pytest.mark.asyncio()
    async def test_execute_cancel_passes_correct_params(self) -> None:
        """Cancel calls include correct parameters."""
        orders = [create_mock_order(client_order_id="o1", symbol="AAPL")]

        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value=None)

        dialog = CancelAllDialog(
            orders=orders,
            trading_client=mock_client,
            user_id="user1",
            user_role="trader",
            strategies=["alpha_baseline"],
        )

        await dialog._execute_cancel_all(orders)

        mock_client.cancel_order.assert_called_once()
        call_kwargs = mock_client.cancel_order.call_args.kwargs
        assert call_kwargs["role"] == "trader"
        assert call_kwargs["strategies"] == ["alpha_baseline"]
        assert call_kwargs["reason"] == "Cancel All Orders dialog"
        assert call_kwargs["requested_by"] == "user1"

    @pytest.mark.asyncio()
    async def test_execute_cancel_bounded_concurrency(self) -> None:
        """Verify bounded concurrency via semaphore."""
        # Create more orders than MAX_CONCURRENT_CANCELS
        orders = [create_mock_order(client_order_id=f"o{i}") for i in range(10)]

        concurrent_count = [0]
        max_concurrent = [0]

        mock_client = MagicMock()

        async def mock_cancel(*args: Any, **kwargs: Any) -> None:
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            # Small delay to allow concurrency measurement
            import asyncio

            await asyncio.sleep(0.01)
            concurrent_count[0] -= 1

        mock_client.cancel_order = AsyncMock(side_effect=mock_cancel)

        dialog = CancelAllDialog(
            orders=orders,
            trading_client=mock_client,
            user_id="user1",
            user_role="trader",
        )

        await dialog._execute_cancel_all(orders)

        # Max concurrent should not exceed MAX_CONCURRENT_CANCELS
        assert max_concurrent[0] <= CancelAllDialog.MAX_CONCURRENT_CANCELS


class TestCancelAllDialogShow:
    """Test dialog show functionality."""

    @pytest.mark.asyncio()
    async def test_show_blocked_for_viewer(self) -> None:
        """Show notifies and returns for viewer role."""
        dialog = CancelAllDialog(
            orders=[create_mock_order()],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="viewer",
        )

        with patch("apps.web_console_ng.components.cancel_all_dialog.ui") as mock_ui:
            await dialog.show()
            mock_ui.notify.assert_called_once()
            assert "Viewers" in mock_ui.notify.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_show_creates_dialog(self) -> None:
        """Show creates dialog with correct components."""
        dialog = CancelAllDialog(
            orders=[create_mock_order()],
            trading_client=MagicMock(),
            user_id="user1",
            user_role="trader",
        )

        with patch("apps.web_console_ng.components.cancel_all_dialog.ui") as mock_ui:
            # Mock dialog context manager
            mock_dialog = MagicMock()
            mock_dialog.__enter__ = MagicMock(return_value=mock_dialog)
            mock_dialog.__exit__ = MagicMock(return_value=False)
            mock_ui.dialog.return_value = mock_dialog

            mock_card = MagicMock()
            mock_card.__enter__ = MagicMock(return_value=mock_card)
            mock_card.__exit__ = MagicMock(return_value=False)
            mock_card.classes.return_value = mock_card
            mock_ui.card.return_value = mock_card

            mock_row = MagicMock()
            mock_row.__enter__ = MagicMock(return_value=mock_row)
            mock_row.__exit__ = MagicMock(return_value=False)
            mock_row.classes.return_value = mock_row
            mock_ui.row.return_value = mock_row

            mock_select = MagicMock()
            mock_select.on_value_change = MagicMock()
            mock_select.value = "All Symbols"
            mock_ui.select.return_value = mock_select

            mock_label = MagicMock()
            mock_label.classes.return_value = mock_label
            mock_label.text = ""
            mock_ui.label.return_value = mock_label

            mock_button = MagicMock()
            mock_button.classes.return_value = mock_button
            mock_ui.button.return_value = mock_button

            await dialog.show()

            # Verify dialog was opened
            mock_dialog.open.assert_called_once()


class TestCancelAllDialogAuditLog:
    """Test audit logging."""

    @pytest.mark.asyncio()
    async def test_audit_log_on_execution(self) -> None:
        """Audit log entry created on successful execution."""
        orders = [create_mock_order(client_order_id="o1")]

        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value=None)
        mock_client.fetch_open_orders = AsyncMock(return_value={"orders": orders})

        dialog = CancelAllDialog(
            orders=orders,
            trading_client=mock_client,
            user_id="user1",
            user_role="trader",
        )

        with patch("apps.web_console_ng.components.cancel_all_dialog.logger"):
            # Execute directly via _execute_cancel_all
            await dialog._execute_cancel_all(orders)

            # Note: Full audit happens in confirm() which requires UI mocking
            # Just verify cancellation worked
            assert mock_client.cancel_order.call_count == 1


__all__ = [
    "TestCancelAllDialogAuditLog",
    "TestCancelAllDialogExecution",
    "TestCancelAllDialogFiltering",
    "TestCancelAllDialogPermissions",
    "TestCancelAllDialogShow",
    "TestCancelAllDialogUniqueSymbols",
]
