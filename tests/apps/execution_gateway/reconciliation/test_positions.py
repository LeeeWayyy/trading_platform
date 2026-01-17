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
