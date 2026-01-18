"""Unit tests for positions.py - position reconciliation logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from apps.execution_gateway.reconciliation.positions import reconcile_positions


@dataclass
class MockPosition:
    """Mock position object returned from database."""

    symbol: str
    qty: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")


class TestReconcilePositions:
    """Tests for reconcile_positions function."""

    def test_updates_positions_from_broker(self) -> None:
        """Reconcile updates DB positions with broker data."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.00",
                "current_price": "155.00",
            },
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        assert result["flattened"] == 0
        db_client.upsert_position_snapshot.assert_called_once()
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["qty"] == Decimal("100")
        assert call_kwargs["avg_entry_price"] == Decimal("150.00")
        assert call_kwargs["current_price"] == "155.00"

    def test_flattens_positions_not_at_broker(self) -> None:
        """Reconcile sets positions to flat if not found at broker."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
            MockPosition(symbol="TSLA"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "50", "avg_entry_price": "100.00"},
        ]  # TSLA not at broker

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1  # AAPL updated
        assert result["flattened"] == 1  # TSLA flattened
        # Check TSLA was flattened
        calls = db_client.upsert_position_snapshot.call_args_list
        tsla_call = [c for c in calls if c.kwargs["symbol"] == "TSLA"]
        assert len(tsla_call) == 1
        assert tsla_call[0].kwargs["qty"] == Decimal("0")
        assert tsla_call[0].kwargs["avg_entry_price"] == Decimal("0")

    def test_handles_empty_broker_positions(self) -> None:
        """Reconcile handles empty broker positions list."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 0
        assert result["flattened"] == 1

    def test_handles_empty_db_positions(self) -> None:
        """Reconcile handles empty DB positions list."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        assert result["flattened"] == 0

    def test_handles_both_empty(self) -> None:
        """Reconcile handles both empty broker and DB positions."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 0
        assert result["flattened"] == 0
        db_client.upsert_position_snapshot.assert_not_called()

    def test_handles_multiple_broker_positions(self) -> None:
        """Reconcile handles multiple broker positions."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "TSLA", "qty": "50", "avg_entry_price": "200.00"},
            {"symbol": "GOOG", "qty": "25", "avg_entry_price": "2500.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 3
        assert result["flattened"] == 0
        assert db_client.upsert_position_snapshot.call_count == 3

    def test_handles_missing_qty_as_zero(self) -> None:
        """Reconcile treats missing qty as zero."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "avg_entry_price": "150.00"},  # No qty
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("0")

    def test_handles_missing_avg_entry_price_as_zero(self) -> None:
        """Reconcile treats missing avg_entry_price as zero."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100"},  # No avg_entry_price
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["avg_entry_price"] == Decimal("0")

    def test_handles_none_current_price(self) -> None:
        """Reconcile handles None current_price."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.00",
                "current_price": None,
            },
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["current_price"] is None

    def test_updated_at_timestamp_is_set(self) -> None:
        """Reconcile sets updated_at timestamp for positions."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        with patch(
            "apps.execution_gateway.reconciliation.positions.datetime"
        ) as mock_datetime:
            mock_now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            reconcile_positions(db_client, alpaca_client)

            call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
            assert call_kwargs["updated_at"] == mock_now

    def test_overlapping_symbols_updated_not_flattened(self) -> None:
        """Reconcile updates overlapping symbols, doesn't flatten them."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
            MockPosition(symbol="TSLA"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "TSLA", "qty": "50", "avg_entry_price": "200.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 2
        assert result["flattened"] == 0

    def test_decimal_precision_preserved(self) -> None:
        """Reconcile preserves decimal precision in quantities and prices."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100.5", "avg_entry_price": "150.123456"},
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("100.5")
        assert call_kwargs["avg_entry_price"] == Decimal("150.123456")

    def test_new_symbol_at_broker_added(self) -> None:
        """Reconcile adds new symbols from broker that weren't in DB."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "TSLA", "qty": "50", "avg_entry_price": "200.00"},  # New
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 2  # Both AAPL and TSLA
        assert result["flattened"] == 0


class TestReconcilePositionsEdgeCases:
    """Edge case tests for position reconciliation."""

    def test_handles_negative_quantity(self) -> None:
        """Reconcile handles negative quantities (short positions)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "-100", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("-100")

    def test_handles_fractional_shares(self) -> None:
        """Reconcile handles fractional share quantities."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "10.123456789", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("10.123456789")

    def test_handles_very_large_quantities(self) -> None:
        """Reconcile handles very large position quantities."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "999999999.99", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("999999999.99")

    def test_handles_very_small_prices(self) -> None:
        """Reconcile handles very small prices (penny stocks, crypto)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "PENNY", "qty": "1000000", "avg_entry_price": "0.0001"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["avg_entry_price"] == Decimal("0.0001")

    def test_handles_zero_qty_from_broker(self) -> None:
        """Reconcile handles zero quantity positions from broker."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "0", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("0")

    def test_handles_zero_price(self) -> None:
        """Reconcile handles zero avg_entry_price."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "0"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["avg_entry_price"] == Decimal("0")

    def test_handles_missing_current_price_field(self) -> None:
        """Reconcile handles missing current_price field entirely."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["current_price"] is None

    def test_handles_multiple_db_positions_all_flattened(self) -> None:
        """Reconcile flattens all DB positions when broker has none."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
            MockPosition(symbol="TSLA"),
            MockPosition(symbol="GOOG"),
            MockPosition(symbol="MSFT"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 0
        assert result["flattened"] == 4
        assert db_client.upsert_position_snapshot.call_count == 4

    def test_handles_mixed_scenario(self) -> None:
        """Reconcile handles mixed scenario: updates, flattens, and new."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),  # Will be updated
            MockPosition(symbol="TSLA"),  # Will be flattened
            MockPosition(symbol="GOOG"),  # Will be updated
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "GOOG", "qty": "25", "avg_entry_price": "2500.00"},
            {"symbol": "MSFT", "qty": "50", "avg_entry_price": "300.00"},  # New
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 3  # AAPL, GOOG, MSFT
        assert result["flattened"] == 1  # TSLA
        assert db_client.upsert_position_snapshot.call_count == 4

    def test_preserves_string_current_price(self) -> None:
        """Reconcile preserves current_price as string (not converted to Decimal)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.00",
                "current_price": "155.50",
            },
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        # current_price is passed as-is (string)
        assert call_kwargs["current_price"] == "155.50"

    def test_handles_numeric_current_price(self) -> None:
        """Reconcile handles numeric current_price (int or float)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.00",
                "current_price": 155.50,  # Float
            },
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["current_price"] == 155.50

    def test_flattened_positions_have_none_current_price(self) -> None:
        """Reconcile sets current_price to None for flattened positions."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["qty"] == Decimal("0")
        assert call_kwargs["avg_entry_price"] == Decimal("0")
        assert call_kwargs["current_price"] is None


class TestReconcilePositionsLogging:
    """Tests for logging behavior in position reconciliation."""

    def test_logs_flattened_positions(self) -> None:
        """Reconcile logs info when positions are flattened."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        with patch(
            "apps.execution_gateway.reconciliation.positions.logger"
        ) as mock_logger:
            reconcile_positions(db_client, alpaca_client)

            mock_logger.info.assert_called_once_with(
                "Position flattened - not found at broker",
                extra={"symbol": "AAPL"},
            )

    def test_logs_multiple_flattened_positions(self) -> None:
        """Reconcile logs each flattened position."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
            MockPosition(symbol="TSLA"),
            MockPosition(symbol="GOOG"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        with patch(
            "apps.execution_gateway.reconciliation.positions.logger"
        ) as mock_logger:
            reconcile_positions(db_client, alpaca_client)

            assert mock_logger.info.call_count == 3
            logged_symbols = [
                call_args.kwargs["extra"]["symbol"]
                for call_args in mock_logger.info.call_args_list
            ]
            assert set(logged_symbols) == {"AAPL", "TSLA", "GOOG"}

    def test_no_logging_for_updated_positions(self) -> None:
        """Reconcile doesn't log for updated positions (normal operation)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        with patch(
            "apps.execution_gateway.reconciliation.positions.logger"
        ) as mock_logger:
            reconcile_positions(db_client, alpaca_client)

            mock_logger.info.assert_not_called()


class TestReconcilePositionsTimestamps:
    """Tests for timestamp handling in position reconciliation."""

    def test_uses_utc_timezone_for_timestamps(self) -> None:
        """Reconcile uses UTC timezone for all timestamps."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        with patch(
            "apps.execution_gateway.reconciliation.positions.datetime"
        ) as mock_datetime:
            mock_now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            reconcile_positions(db_client, alpaca_client)

            # Verify datetime.now called with UTC
            mock_datetime.now.assert_called_with(UTC)

    def test_different_timestamps_for_multiple_positions(self) -> None:
        """Reconcile may use different timestamps for positions (timing variation)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "TSLA", "qty": "50", "avg_entry_price": "200.00"},
        ]

        reconcile_positions(db_client, alpaca_client)

        # Both should have updated_at set (don't verify exact timestamp)
        calls = db_client.upsert_position_snapshot.call_args_list
        assert all("updated_at" in c.kwargs for c in calls)
        assert all(c.kwargs["updated_at"].tzinfo == UTC for c in calls)

    def test_flattened_positions_have_timestamps(self) -> None:
        """Reconcile sets timestamps for flattened positions."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        with patch(
            "apps.execution_gateway.reconciliation.positions.datetime"
        ) as mock_datetime:
            mock_now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            reconcile_positions(db_client, alpaca_client)

            call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
            assert call_kwargs["updated_at"] == mock_now


class TestReconcilePositionsDatabaseInteraction:
    """Tests for database interaction patterns."""

    def test_calls_get_all_positions_once(self) -> None:
        """Reconcile calls get_all_positions exactly once."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        reconcile_positions(db_client, alpaca_client)

        db_client.get_all_positions.assert_called_once()

    def test_calls_alpaca_get_all_positions_once(self) -> None:
        """Reconcile calls alpaca get_all_positions exactly once."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        reconcile_positions(db_client, alpaca_client)

        alpaca_client.get_all_positions.assert_called_once()

    def test_upsert_order_matches_broker_order(self) -> None:
        """Reconcile upserts positions in same order as broker returns them."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "TSLA", "qty": "50", "avg_entry_price": "200.00"},
            {"symbol": "GOOG", "qty": "25", "avg_entry_price": "2500.00"},
        ]

        reconcile_positions(db_client, alpaca_client)

        calls = db_client.upsert_position_snapshot.call_args_list
        symbols = [c.kwargs["symbol"] for c in calls]
        # Order should be preserved (same as broker response)
        assert symbols == ["AAPL", "TSLA", "GOOG"]

    def test_flattening_happens_after_updates(self) -> None:
        """Reconcile flattens DB positions after updating broker positions."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="OLD"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        reconcile_positions(db_client, alpaca_client)

        calls = db_client.upsert_position_snapshot.call_args_list
        # First call should be AAPL (broker), second call should be OLD (flattened)
        assert calls[0].kwargs["symbol"] == "AAPL"
        assert calls[1].kwargs["symbol"] == "OLD"
        assert calls[1].kwargs["qty"] == Decimal("0")


class TestReconcilePositionsReturnValue:
    """Tests for return value structure and counts."""

    def test_return_value_has_required_keys(self) -> None:
        """Reconcile returns dict with 'updated' and 'flattened' keys."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = []

        result = reconcile_positions(db_client, alpaca_client)

        assert "updated" in result
        assert "flattened" in result
        assert len(result) == 2  # Only these two keys

    def test_counts_are_integers(self) -> None:
        """Reconcile returns integer counts."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert isinstance(result["updated"], int)
        assert isinstance(result["flattened"], int)

    def test_counts_match_actual_operations(self) -> None:
        """Reconcile counts match actual upsert operations."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="OLD1"),
            MockPosition(symbol="OLD2"),
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "TSLA", "qty": "50", "avg_entry_price": "200.00"},
            {"symbol": "GOOG", "qty": "25", "avg_entry_price": "2500.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        # 3 broker positions updated, 2 DB positions flattened
        assert result["updated"] == 3
        assert result["flattened"] == 2
        # Total calls = 3 + 2 = 5
        assert db_client.upsert_position_snapshot.call_count == 5


class TestReconcilePositionsSymbolHandling:
    """Tests for symbol handling edge cases."""

    def test_handles_case_sensitive_symbols(self) -> None:
        """Reconcile treats symbols as case-sensitive."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = [
            MockPosition(symbol="AAPL"),  # Uppercase
        ]

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "aapl", "qty": "100", "avg_entry_price": "150.00"},  # Lowercase
        ]

        result = reconcile_positions(db_client, alpaca_client)

        # Different case = different symbols
        assert result["updated"] == 1  # aapl updated
        assert result["flattened"] == 1  # AAPL flattened

    def test_handles_symbols_with_special_characters(self) -> None:
        """Reconcile handles symbols with special characters."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "BRK.A", "qty": "10", "avg_entry_price": "500000.00"},
            {"symbol": "BRK/B", "qty": "100", "avg_entry_price": "300.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 2
        calls = db_client.upsert_position_snapshot.call_args_list
        symbols = [c.kwargs["symbol"] for c in calls]
        assert "BRK.A" in symbols
        assert "BRK/B" in symbols

    def test_handles_duplicate_symbols_in_broker_response(self) -> None:
        """Reconcile handles duplicate symbols (uses last occurrence)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.00"},
            {"symbol": "AAPL", "qty": "200", "avg_entry_price": "155.00"},  # Duplicate
        ]

        result = reconcile_positions(db_client, alpaca_client)

        # Only one update (last one wins due to dict)
        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        # Should use last occurrence
        assert call_kwargs["qty"] == Decimal("200")
        assert call_kwargs["avg_entry_price"] == Decimal("155.00")

    def test_handles_empty_string_symbol(self) -> None:
        """Reconcile handles empty string symbol (invalid but test robustness)."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "", "qty": "100", "avg_entry_price": "150.00"},
        ]

        result = reconcile_positions(db_client, alpaca_client)

        assert result["updated"] == 1
        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["symbol"] == ""


class TestReconcilePositionsDataConversion:
    """Tests for data type conversion and handling."""

    def test_converts_string_qty_to_decimal(self) -> None:
        """Reconcile converts string quantities to Decimal."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100.5", "avg_entry_price": "150.00"},
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert isinstance(call_kwargs["qty"], Decimal)
        assert call_kwargs["qty"] == Decimal("100.5")

    def test_converts_string_price_to_decimal(self) -> None:
        """Reconcile converts string prices to Decimal."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": "150.123"},
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert isinstance(call_kwargs["avg_entry_price"], Decimal)
        assert call_kwargs["avg_entry_price"] == Decimal("150.123")

    def test_handles_integer_qty(self) -> None:
        """Reconcile handles integer quantities."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00"},
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("100")

    def test_handles_float_price(self) -> None:
        """Reconcile handles float prices."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "100", "avg_entry_price": 150.5},
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["avg_entry_price"] == Decimal("150.5")

    def test_handles_scientific_notation(self) -> None:
        """Reconcile handles scientific notation in strings."""
        db_client = MagicMock()
        db_client.get_all_positions.return_value = []

        alpaca_client = MagicMock()
        alpaca_client.get_all_positions.return_value = [
            {"symbol": "AAPL", "qty": "1e5", "avg_entry_price": "1.5e2"},
        ]

        reconcile_positions(db_client, alpaca_client)

        call_kwargs = db_client.upsert_position_snapshot.call_args.kwargs
        assert call_kwargs["qty"] == Decimal("100000")
        assert call_kwargs["avg_entry_price"] == Decimal("150")
