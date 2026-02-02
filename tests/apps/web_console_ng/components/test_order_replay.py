"""Tests for OrderReplayHandler component."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.web_console_ng.components.order_replay import (
    OrderReplayHandler,
    ReplayableOrder,
)


def create_mock_order(
    symbol: str = "AAPL",
    side: str = "buy",
    qty: int | str | Decimal = 100,
    status: str = "filled",
    order_type: str = "limit",
    client_order_id: str = "order-123",
    limit_price: str | None = "150.00",
    stop_price: str | None = None,
    time_in_force: str = "day",
) -> dict[str, Any]:
    """Create a mock order for testing."""
    order: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "status": status,
        "type": order_type,
        "client_order_id": client_order_id,
        "time_in_force": time_in_force,
    }
    if limit_price is not None:
        order["limit_price"] = limit_price
    if stop_price is not None:
        order["stop_price"] = stop_price
    return order


class TestReplayableOrder:
    """Test ReplayableOrder dataclass."""

    def test_create_replayable_order(self) -> None:
        """Create a ReplayableOrder with all fields."""
        order = ReplayableOrder(
            symbol="AAPL",
            side="buy",
            qty=Decimal("100"),
            order_type="limit",
            limit_price=Decimal("150.00"),
            stop_price=None,
            time_in_force="day",
            original_order_id="order-123",
        )
        assert order.symbol == "AAPL"
        assert order.side == "buy"
        assert order.qty == Decimal("100")
        assert order.order_type == "limit"
        assert order.limit_price == Decimal("150.00")
        assert order.stop_price is None
        assert order.time_in_force == "day"
        assert order.original_order_id == "order-123"

    def test_replayable_order_is_frozen(self) -> None:
        """ReplayableOrder is immutable."""
        order = ReplayableOrder(
            symbol="AAPL",
            side="buy",
            qty=Decimal("100"),
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
            original_order_id="order-123",
        )
        with pytest.raises(AttributeError):
            order.symbol = "GOOG"  # type: ignore[misc]


class TestCanReplay:
    """Test can_replay method."""

    def test_can_replay_filled_order(self) -> None:
        """Filled orders can be replayed."""
        handler = OrderReplayHandler()
        assert handler.can_replay(create_mock_order(status="filled"))

    def test_can_replay_canceled_order(self) -> None:
        """Canceled orders can be replayed."""
        handler = OrderReplayHandler()
        assert handler.can_replay(create_mock_order(status="canceled"))
        assert handler.can_replay(create_mock_order(status="cancelled"))

    def test_can_replay_expired_order(self) -> None:
        """Expired orders can be replayed."""
        handler = OrderReplayHandler()
        assert handler.can_replay(create_mock_order(status="expired"))

    def test_can_replay_rejected_order(self) -> None:
        """Rejected orders can be replayed."""
        handler = OrderReplayHandler()
        assert handler.can_replay(create_mock_order(status="rejected"))

    def test_cannot_replay_open_order(self) -> None:
        """Open orders cannot be replayed."""
        handler = OrderReplayHandler()
        assert not handler.can_replay(create_mock_order(status="open"))

    def test_cannot_replay_pending_order(self) -> None:
        """Pending orders cannot be replayed."""
        handler = OrderReplayHandler()
        assert not handler.can_replay(create_mock_order(status="pending"))

    def test_cannot_replay_partially_filled_order(self) -> None:
        """Partially filled orders cannot be replayed."""
        handler = OrderReplayHandler()
        assert not handler.can_replay(create_mock_order(status="partially_filled"))

    def test_status_case_insensitive(self) -> None:
        """Status check is case-insensitive."""
        handler = OrderReplayHandler()
        assert handler.can_replay(create_mock_order(status="FILLED"))
        assert handler.can_replay(create_mock_order(status="Filled"))


class TestExtractReplayData:
    """Test extract_replay_data method."""

    def test_extract_complete_order(self) -> None:
        """Extract all fields from complete order."""
        handler = OrderReplayHandler()
        order = create_mock_order(
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="limit",
            limit_price="150.00",
            time_in_force="gtc",
            client_order_id="order-xyz",
        )
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.symbol == "AAPL"
        assert result.side == "buy"
        assert result.qty == Decimal("100")
        assert result.order_type == "limit"
        assert result.limit_price == Decimal("150.00")
        assert result.stop_price is None
        assert result.time_in_force == "gtc"
        assert result.original_order_id == "order-xyz"

    def test_extract_market_order(self) -> None:
        """Extract market order without prices."""
        handler = OrderReplayHandler()
        order = create_mock_order(
            order_type="market",
            limit_price=None,
        )
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.order_type == "market"
        assert result.limit_price is None
        assert result.stop_price is None

    def test_extract_stop_limit_order(self) -> None:
        """Extract stop_limit order with both prices."""
        handler = OrderReplayHandler()
        order = create_mock_order(
            order_type="stop_limit",
            limit_price="150.00",
            stop_price="148.00",
        )
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.order_type == "stop_limit"
        assert result.limit_price == Decimal("150.00")
        assert result.stop_price == Decimal("148.00")

    def test_extract_missing_symbol_returns_none(self) -> None:
        """Missing symbol returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["symbol"]
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_invalid_symbol_returns_none(self) -> None:
        """Non-string symbol returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        order["symbol"] = 123  # Invalid type
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_missing_side_returns_none(self) -> None:
        """Missing side returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["side"]
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_invalid_side_returns_none(self) -> None:
        """Invalid side value returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order(side="invalid")
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_missing_client_order_id_returns_none(self) -> None:
        """Missing client_order_id returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["client_order_id"]
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_invalid_qty_returns_none(self) -> None:
        """Invalid quantity returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order(qty="invalid")
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_zero_qty_returns_none(self) -> None:
        """Zero quantity returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order(qty=0)
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_negative_qty_returns_none(self) -> None:
        """Negative quantity returns None."""
        handler = OrderReplayHandler()
        order = create_mock_order(qty=-100)
        result = handler.extract_replay_data(order)
        assert result is None

    def test_extract_uses_filled_qty_as_fallback(self) -> None:
        """Use filled_qty when qty is missing."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["qty"]
        order["filled_qty"] = 50
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.qty == Decimal("50")

    def test_extract_decimal_qty(self) -> None:
        """Handle decimal quantity."""
        handler = OrderReplayHandler()
        order = create_mock_order(qty="100.5")
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.qty == Decimal("100.5")

    def test_extract_defaults_order_type_to_market(self) -> None:
        """Default order type to market when missing."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["type"]
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.order_type == "market"

    def test_extract_uses_order_type_field(self) -> None:
        """Use order_type field if type is missing."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["type"]
        order["order_type"] = "stop"
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.order_type == "stop"

    def test_extract_defaults_invalid_order_type_to_market(self) -> None:
        """Default invalid order type to market."""
        handler = OrderReplayHandler()
        order = create_mock_order(order_type="invalid")
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.order_type == "market"

    def test_extract_defaults_time_in_force_to_day(self) -> None:
        """Default time in force to day when missing."""
        handler = OrderReplayHandler()
        order = create_mock_order()
        del order["time_in_force"]
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.time_in_force == "day"

    def test_extract_defaults_invalid_tif_to_day(self) -> None:
        """Default invalid time in force to day."""
        handler = OrderReplayHandler()
        order = create_mock_order(time_in_force="invalid")
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.time_in_force == "day"

    def test_extract_ignores_invalid_limit_price(self) -> None:
        """Invalid limit price is ignored (set to None)."""
        handler = OrderReplayHandler()
        order = create_mock_order(limit_price="invalid")
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.limit_price is None

    def test_extract_ignores_invalid_stop_price(self) -> None:
        """Invalid stop price is ignored (set to None)."""
        handler = OrderReplayHandler()
        order = create_mock_order(stop_price="invalid")
        result = handler.extract_replay_data(order)

        assert result is not None
        assert result.stop_price is None


class TestOnReplay:
    """Test on_replay method."""

    @pytest.mark.asyncio()
    async def test_on_replay_success(self) -> None:
        """Successful replay calls prefill callback."""
        handler = OrderReplayHandler()
        order = create_mock_order(status="filled", qty=100)
        prefill_mock = MagicMock()

        with patch("apps.web_console_ng.components.order_replay.ui"):
            await handler.on_replay(
                order=order,
                user_id="user1",
                user_role="trader",
                on_prefill_order_ticket=prefill_mock,
            )

            prefill_mock.assert_called_once()
            replay_data = prefill_mock.call_args[0][0]
            assert isinstance(replay_data, ReplayableOrder)
            assert replay_data.qty == Decimal("100")

    @pytest.mark.asyncio()
    async def test_on_replay_blocked_for_active_order(self) -> None:
        """Replay blocked for active orders."""
        handler = OrderReplayHandler()
        order = create_mock_order(status="open")
        prefill_mock = MagicMock()

        with patch("apps.web_console_ng.components.order_replay.ui") as mock_ui:
            await handler.on_replay(
                order=order,
                user_id="user1",
                user_role="trader",
                on_prefill_order_ticket=prefill_mock,
            )

            prefill_mock.assert_not_called()
            mock_ui.notify.assert_called_once()
            assert "active orders" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_on_replay_missing_data(self) -> None:
        """Replay blocked when data extraction fails."""
        handler = OrderReplayHandler()
        order = create_mock_order(status="filled")
        del order["symbol"]  # Make extraction fail
        prefill_mock = MagicMock()

        with patch("apps.web_console_ng.components.order_replay.ui") as mock_ui:
            await handler.on_replay(
                order=order,
                user_id="user1",
                user_role="trader",
                on_prefill_order_ticket=prefill_mock,
            )

            prefill_mock.assert_not_called()
            mock_ui.notify.assert_called_once()
            assert "missing order data" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_on_replay_truncates_fractional_qty(self) -> None:
        """Fractional quantity is truncated to integer."""
        handler = OrderReplayHandler()
        order = create_mock_order(status="filled", qty="100.75")
        prefill_mock = MagicMock()

        with patch("apps.web_console_ng.components.order_replay.ui") as mock_ui:
            await handler.on_replay(
                order=order,
                user_id="user1",
                user_role="trader",
                on_prefill_order_ticket=prefill_mock,
            )

            prefill_mock.assert_called_once()
            replay_data = prefill_mock.call_args[0][0]
            # Should be truncated to 100, not rounded up to 101
            assert replay_data.qty == Decimal("100")

            # Check notification about truncation
            calls = mock_ui.notify.call_args_list
            truncation_notification = any("adjusted" in str(call).lower() for call in calls)
            assert truncation_notification

    @pytest.mark.asyncio()
    async def test_on_replay_qty_rounds_to_zero_blocked(self) -> None:
        """Replay blocked when quantity rounds to zero."""
        handler = OrderReplayHandler()
        order = create_mock_order(status="filled", qty="0.5")
        prefill_mock = MagicMock()

        with patch("apps.web_console_ng.components.order_replay.ui") as mock_ui:
            await handler.on_replay(
                order=order,
                user_id="user1",
                user_role="trader",
                on_prefill_order_ticket=prefill_mock,
            )

            prefill_mock.assert_not_called()
            mock_ui.notify.assert_called()
            assert "rounds to zero" in mock_ui.notify.call_args[0][0].lower()

    @pytest.mark.asyncio()
    async def test_on_replay_audit_log(self) -> None:
        """Audit log entry created on successful replay."""
        handler = OrderReplayHandler()
        order = create_mock_order(
            status="filled",
            symbol="AAPL",
            client_order_id="original-123",
        )
        prefill_mock = MagicMock()

        with (
            patch("apps.web_console_ng.components.order_replay.ui"),
            patch("apps.web_console_ng.components.order_replay.logger") as mock_logger,
        ):
            await handler.on_replay(
                order=order,
                user_id="user1",
                user_role="trader",
                on_prefill_order_ticket=prefill_mock,
            )

            # Verify audit log was called with correct fields
            mock_logger.info.assert_called()
            log_call = mock_logger.info.call_args
            assert log_call[0][0] == "order_replay_prefilled"
            extra = log_call[1]["extra"]
            assert extra["user_id"] == "user1"
            assert extra["symbol"] == "AAPL"
            assert extra["replayed_from"] == "original-123"


class TestReplayableStatuses:
    """Test REPLAYABLE_STATUSES constant."""

    def test_replayable_statuses_contains_expected_values(self) -> None:
        """REPLAYABLE_STATUSES contains all expected terminal statuses."""
        expected = {"filled", "canceled", "cancelled", "expired", "rejected"}
        assert OrderReplayHandler.REPLAYABLE_STATUSES == expected


__all__ = [
    "TestCanReplay",
    "TestExtractReplayData",
    "TestOnReplay",
    "TestReplayableOrder",
    "TestReplayableStatuses",
]
