"""Additional coverage tests for fills.py - targeting edge cases and uncovered branches.

This module extends test_fills.py to achieve 95%+ coverage by testing:
- Pagination edge cases in backfill_alpaca_fills
- P&L recalculation failure and rollback
- Fill matching with generated fill_id (empty id)
- Activity time fallback when transaction_time is missing
- Timestamp fallbacks in backfill_fill_metadata and backfill_fill_metadata_from_order
- Order not found after lock
- Exception handling paths
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.execution_gateway.reconciliation.fills import (
    backfill_alpaca_fills,
    backfill_fill_metadata,
    backfill_fill_metadata_from_order,
)


@dataclass
class MockOrder:
    """Mock order object returned from database."""

    client_order_id: str
    symbol: str = "AAPL"
    strategy_id: str = "test_strategy"
    filled_qty: Decimal = Decimal("0")
    filled_avg_price: Decimal | None = None
    filled_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TestBackfillAlpacaFillsPaginationEdgeCases:
    """Tests for pagination edge cases in backfill_alpaca_fills."""

    def test_breaks_pagination_when_page_has_no_last_id(self) -> None:
        """Pagination stops when last item has no id field."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        # Return full page with last item having no id - should break pagination
        alpaca_client.get_account_activities.return_value = [
            {"id": "fill1", "order_id": "broker1"},
            {"id": "fill2", "order_id": "broker1"},
            {"order_id": "broker1"},  # No id field - triggers break at line 105-106
        ] * 34  # 102 items to simulate full page (100+)

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
            fills_backfill_page_size=100,
            fills_backfill_max_pages=5,
        )

        # Should only call API once due to missing last_id
        assert alpaca_client.get_account_activities.call_count == 1
        assert result["status"] == "ok"

    def test_stops_pagination_at_max_pages(self) -> None:
        """Pagination stops when max_pages limit is reached."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        # Always return full pages with valid ids
        alpaca_client.get_account_activities.return_value = [
            {"id": f"fill{i}", "order_id": "broker1"} for i in range(101)
        ]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
            fills_backfill_page_size=100,
            fills_backfill_max_pages=3,
        )

        # Loop runs while pages < max_pages with pages incrementing at end of each iteration
        # pages=0: API call #1, pages becomes 1
        # pages=1: API call #2, pages becomes 2
        # pages=2: API call #3, pages becomes 3
        # pages=3 < 3 is FALSE, loop exits
        # So with max_pages=3, we get exactly 3 API calls
        assert alpaca_client.get_account_activities.call_count == 3
        assert result["status"] == "ok"


class TestBackfillAlpacaFillsPnLRecalculation:
    """Tests for P&L recalculation paths in backfill_alpaca_fills."""

    def test_pnl_recalculation_failure_raises_runtime_error(self) -> None:
        """P&L recalculation failure raises RuntimeError and rolls back."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        mock_order = MockOrder(client_order_id="client123")
        db_client.get_orders_by_broker_ids.return_value = {"broker456": mock_order}

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.append_fill_to_order_metadata.return_value = True
        db_client.recalculate_trade_realized_pnl.side_effect = ValueError("P&L calculation error")

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {
                "id": "fill1",
                "order_id": "broker456",
                "qty": "50",
                "price": "150.00",
                "transaction_time": "2024-01-15T12:00:00Z",
            },
        ]

        with pytest.raises(RuntimeError) as exc_info:
            backfill_alpaca_fills(
                db_client,
                alpaca_client,
                fills_backfill_enabled=True,
            )

        assert "P&L recalculation failed" in str(exc_info.value)
        assert "test_strategy:AAPL" in str(exc_info.value)


class TestBackfillAlpacaFillsFillMatching:
    """Tests for fill matching edge cases in backfill_alpaca_fills."""

    def test_generates_fill_id_when_id_is_empty_string(self) -> None:
        """Fill ID is generated when activity id is empty string."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        mock_order = MockOrder(client_order_id="client123")
        db_client.get_orders_by_broker_ids.return_value = {"broker456": mock_order}

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.append_fill_to_order_metadata.return_value = True
        db_client.recalculate_trade_realized_pnl.return_value = {"trades_updated": 1}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {
                "id": "",  # Empty string id - triggers generate_fill_id_from_activity
                "order_id": "broker456",
                "qty": "50",
                "price": "150.00",
                "transaction_time": "2024-01-15T12:00:00Z",
            },
        ]

        with patch(
            "apps.execution_gateway.reconciliation.fills.generate_fill_id_from_activity"
        ) as mock_gen:
            mock_gen.return_value = "generated_fill_id_123"

            result = backfill_alpaca_fills(
                db_client,
                alpaca_client,
                fills_backfill_enabled=True,
            )

            mock_gen.assert_called_once()
            assert result["fills_inserted"] == 1

    def test_uses_activity_time_when_transaction_time_missing(self) -> None:
        """Uses activity_time when transaction_time is not available."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        mock_order = MockOrder(client_order_id="client123")
        db_client.get_orders_by_broker_ids.return_value = {"broker456": mock_order}

        captured_fill_data: list[dict[str, Any]] = []

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction

        def capture_fill(client_order_id: str, fill_data: dict, conn: Any) -> bool:
            captured_fill_data.append(fill_data)
            return True

        db_client.append_fill_to_order_metadata.side_effect = capture_fill
        db_client.recalculate_trade_realized_pnl.return_value = {"trades_updated": 1}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {
                "id": "fill1",
                "order_id": "broker456",
                "qty": "50",
                "price": "150.00",
                "transaction_time": None,  # No transaction_time
                "activity_time": "2024-01-15T14:30:00Z",  # Fallback to activity_time
            },
        ]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        assert result["fills_inserted"] == 1
        assert len(captured_fill_data) == 1
        assert captured_fill_data[0]["timestamp"] == "2024-01-15T14:30:00Z"

    def test_fill_not_inserted_returns_none(self) -> None:
        """Fill insertion that returns None is not counted as inserted."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        mock_order = MockOrder(client_order_id="client123")
        db_client.get_orders_by_broker_ids.return_value = {"broker456": mock_order}

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.append_fill_to_order_metadata.return_value = None  # Not inserted
        db_client.recalculate_trade_realized_pnl.return_value = {"trades_updated": 0}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {
                "id": "fill1",
                "order_id": "broker456",
                "qty": "50",
                "price": "150.00",
                "transaction_time": "2024-01-15T12:00:00Z",
            },
        ]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        assert result["fills_seen"] == 1
        assert result["fills_inserted"] == 0


class TestBackfillFillMetadataTimestampFallbacks:
    """Tests for timestamp fallback paths in backfill_fill_metadata."""

    def test_uses_created_at_when_updated_at_missing(self) -> None:
        """Uses created_at when updated_at is not available in broker_order."""
        db_client = MagicMock()

        mock_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": []},
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = mock_order

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
            "updated_at": None,  # No updated_at
            "created_at": datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),  # Fallback
        }

        with patch(
            "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
        ) as mock_calc:
            mock_calc.return_value = {
                "fill_id": "syn_123",
                "fill_qty": "100",
                "fill_price": "150.00",
                "source": "recon",
                "_missing_qty": Decimal("100"),
            }

            result = backfill_fill_metadata("client123", broker_order, db_client)

            assert result is True
            call_args = mock_calc.call_args
            assert call_args.kwargs["timestamp"] == datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

    def test_uses_datetime_now_when_no_timestamps_available(self) -> None:
        """Uses datetime.now(UTC) when no timestamps available in broker_order."""
        db_client = MagicMock()

        mock_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": []},
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = mock_order

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
            "updated_at": None,
            "created_at": None,  # No timestamps at all
        }

        mock_now = datetime(2024, 1, 15, 16, 0, 0, tzinfo=UTC)

        with (
            patch(
                "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
            ) as mock_calc,
            patch("apps.execution_gateway.reconciliation.fills.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            mock_calc.return_value = {
                "fill_id": "syn_123",
                "fill_qty": "100",
                "fill_price": "150.00",
                "source": "recon",
                "_missing_qty": Decimal("100"),
            }

            result = backfill_fill_metadata("client123", broker_order, db_client)

            assert result is True
            call_args = mock_calc.call_args
            assert call_args.kwargs["timestamp"] == mock_now


class TestBackfillFillMetadataFromOrderEdgeCases:
    """Tests for edge cases in backfill_fill_metadata_from_order."""

    def test_uses_datetime_now_when_no_timestamps_available(self) -> None:
        """Uses datetime.now(UTC) when order has no filled_at or updated_at."""
        db_client = MagicMock()

        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.00"),
            filled_at=None,
            updated_at=None,  # No timestamps
        )

        locked_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": []},
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = locked_order

        mock_now = datetime(2024, 1, 15, 18, 0, 0, tzinfo=UTC)

        with (
            patch(
                "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
            ) as mock_calc,
            patch("apps.execution_gateway.reconciliation.fills.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            mock_calc.return_value = {
                "fill_id": "syn_123",
                "fill_qty": "100",
                "fill_price": "150.00",
                "source": "recon_db",
                "_missing_qty": Decimal("100"),
            }

            result = backfill_fill_metadata_from_order(order, db_client)

            assert result is True
            call_args = mock_calc.call_args
            assert call_args.kwargs["timestamp"] == mock_now

    def test_returns_false_when_order_not_found_after_lock(self) -> None:
        """Returns False when locked order lookup returns None."""
        db_client = MagicMock()

        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.00"),
            filled_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = None  # Order not found after lock

        result = backfill_fill_metadata_from_order(order, db_client)

        assert result is False

    def test_returns_false_when_calculate_synthetic_fill_returns_none(self) -> None:
        """Returns False when no synthetic fill is needed."""
        db_client = MagicMock()

        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.00"),
            filled_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
        )

        locked_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": [{"fill_qty": "100"}]},  # Already has fills
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = locked_order

        with patch(
            "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
        ) as mock_calc:
            mock_calc.return_value = None  # No fill needed

            result = backfill_fill_metadata_from_order(order, db_client)

            assert result is False

    def test_handles_exception_gracefully(self) -> None:
        """Returns False and logs warning when exception occurs."""
        db_client = MagicMock()

        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.00"),
            filled_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.side_effect = Exception("Database connection lost")

        result = backfill_fill_metadata_from_order(order, db_client)

        assert result is False


class TestBackfillAlpacaFillsDeduplication:
    """Tests for fill deduplication during pagination."""

    def test_deduplicates_last_activity_across_pages(self) -> None:
        """Removes duplicate last activity when paginating."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        # First page: 101 items (full page triggers pagination)
        first_page = [{"id": f"fill{i}", "order_id": "broker1"} for i in range(101)]
        # Second page: includes the last item from first page (fill100) plus new items
        second_page = [
            {"id": "fill100", "order_id": "broker1"},  # Duplicate - should be filtered
            {"id": "fill101", "order_id": "broker1"},
            {"id": "fill102", "order_id": "broker1"},
        ]

        alpaca_client.get_account_activities.side_effect = [first_page, second_page]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
            fills_backfill_page_size=100,
            fills_backfill_max_pages=5,
        )

        # First page: 101 items, Second page: 2 items (after dedup)
        # Total should be 103, not 104
        assert result["fills_seen"] == 103
        assert result["unmatched"] == 103  # All unmatched since no orders


class TestBackfillAlpacaFillsMultipleOrdersAndStrategies:
    """Tests for handling multiple orders and affected strategy/symbol pairs."""

    def test_multiple_fills_for_same_order_aggregated(self) -> None:
        """Multiple fills for the same order are properly aggregated."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        mock_order = MockOrder(client_order_id="client123")
        db_client.get_orders_by_broker_ids.return_value = {"broker456": mock_order}

        insert_count = [0]

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction

        def track_insert(client_order_id: str, fill_data: dict, conn: Any) -> bool:
            insert_count[0] += 1
            return True

        db_client.append_fill_to_order_metadata.side_effect = track_insert
        db_client.recalculate_trade_realized_pnl.return_value = {"trades_updated": 1}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {
                "id": "fill1",
                "order_id": "broker456",
                "qty": "25",
                "price": "150.00",
                "transaction_time": "2024-01-15T12:00:00Z",
            },
            {
                "id": "fill2",
                "order_id": "broker456",
                "qty": "25",
                "price": "150.50",
                "transaction_time": "2024-01-15T12:01:00Z",
            },
            {
                "id": "fill3",
                "order_id": "broker456",
                "qty": "50",
                "price": "151.00",
                "transaction_time": "2024-01-15T12:02:00Z",
            },
        ]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        assert result["fills_seen"] == 3
        assert result["fills_inserted"] == 3
        # P&L recalculation should be called once per strategy/symbol pair
        db_client.recalculate_trade_realized_pnl.assert_called_once()

    def test_multiple_strategy_symbol_pairs_trigger_multiple_pnl_recalcs(self) -> None:
        """Different strategy/symbol pairs trigger separate P&L recalculations."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        # Two orders with different strategy/symbol pairs
        mock_order1 = MockOrder(
            client_order_id="client123", symbol="AAPL", strategy_id="strategy_a"
        )
        mock_order2 = MockOrder(
            client_order_id="client456", symbol="GOOGL", strategy_id="strategy_b"
        )
        db_client.get_orders_by_broker_ids.return_value = {
            "broker123": mock_order1,
            "broker456": mock_order2,
        }

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.append_fill_to_order_metadata.return_value = True
        db_client.recalculate_trade_realized_pnl.return_value = {"trades_updated": 1}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {
                "id": "fill1",
                "order_id": "broker123",
                "qty": "50",
                "price": "150.00",
                "transaction_time": "2024-01-15T12:00:00Z",
            },
            {
                "id": "fill2",
                "order_id": "broker456",
                "qty": "25",
                "price": "2800.00",
                "transaction_time": "2024-01-15T12:01:00Z",
            },
        ]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        assert result["fills_seen"] == 2
        assert result["fills_inserted"] == 2
        assert result["pnl_updates"] == 2
        assert db_client.recalculate_trade_realized_pnl.call_count == 2
