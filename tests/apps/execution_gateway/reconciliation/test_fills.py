"""Unit tests for fills.py - fill backfill logic."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from apps.execution_gateway.reconciliation.fills import (
    backfill_alpaca_fills,
    backfill_fill_metadata,
    backfill_fill_metadata_from_order,
    backfill_missing_fills_scan,
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


class TestBackfillAlpacaFills:
    """Tests for backfill_alpaca_fills function."""

    def test_returns_disabled_when_flag_off_and_no_lookback(self) -> None:
        """Backfill returns disabled status when feature flag is off."""
        db_client = MagicMock()
        alpaca_client = MagicMock()

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=False,
            lookback_hours=None,
        )

        assert result["status"] == "disabled"
        alpaca_client.get_account_activities.assert_not_called()

    def test_uses_lookback_hours_when_provided(self) -> None:
        """Backfill uses provided lookback_hours for time window."""
        db_client = MagicMock()
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = []

        with patch("apps.execution_gateway.reconciliation.fills.datetime") as mock_datetime:
            mock_now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            backfill_alpaca_fills(
                db_client,
                alpaca_client,
                lookback_hours=6,
            )

            call_args = alpaca_client.get_account_activities.call_args
            assert call_args.kwargs["after"] == mock_now - timedelta(hours=6)

    def test_uses_high_water_mark_when_available(self) -> None:
        """Backfill uses high water mark minus overlap when no lookback."""
        db_client = MagicMock()
        hwm = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        db_client.get_reconciliation_high_water_mark.return_value = hwm
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = []

        backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
            overlap_seconds=60,
        )

        call_args = alpaca_client.get_account_activities.call_args
        # Should use hwm - overlap
        expected_after = hwm - timedelta(seconds=60)
        assert call_args.kwargs["after"] == expected_after

    def test_uses_initial_lookback_when_no_hwm(self) -> None:
        """Backfill uses initial lookback when no high water mark exists."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = []

        with patch("apps.execution_gateway.reconciliation.fills.datetime") as mock_datetime:
            mock_now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            backfill_alpaca_fills(
                db_client,
                alpaca_client,
                fills_backfill_enabled=True,
                fills_backfill_initial_lookback_hours=24,
            )

            call_args = alpaca_client.get_account_activities.call_args
            assert call_args.kwargs["after"] == mock_now - timedelta(hours=24)

    def test_returns_ok_with_zero_fills(self) -> None:
        """Backfill returns ok status when no fills found."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = []

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        assert result["status"] == "ok"
        assert result["fills_seen"] == 0
        assert result["fills_inserted"] == 0
        assert result["unmatched"] == 0

    def test_updates_high_water_mark_after_run(self) -> None:
        """Backfill updates high water mark after successful run."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = []

        backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        db_client.set_reconciliation_high_water_mark.assert_called()

    def test_matches_fills_to_orders_by_broker_id(self) -> None:
        """Backfill matches fills to orders using broker order ID."""
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
        db_client.get_orders_by_broker_ids.assert_called_with(["broker456"])

    def test_counts_unmatched_fills(self) -> None:
        """Backfill correctly counts fills that couldn't be matched."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None
        db_client.get_orders_by_broker_ids.return_value = {}  # No matches

        alpaca_client = MagicMock()
        alpaca_client.get_account_activities.return_value = [
            {"id": "fill1", "order_id": "unknown_broker"},
            {"id": "fill2"},  # No order_id
        ]

        result = backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
        )

        assert result["unmatched"] == 2

    def test_paginates_through_activities(self) -> None:
        """Backfill paginates through API when more results exist."""
        db_client = MagicMock()
        db_client.get_reconciliation_high_water_mark.return_value = None
        db_client.get_orders_by_broker_ids.return_value = {}

        alpaca_client = MagicMock()
        # First page full, second page partial
        alpaca_client.get_account_activities.side_effect = [
            [{"id": f"fill{i}", "order_id": "broker1"} for i in range(100)],
            [{"id": f"fill{i}", "order_id": "broker1"} for i in range(50)],
        ]

        backfill_alpaca_fills(
            db_client,
            alpaca_client,
            fills_backfill_enabled=True,
            fills_backfill_page_size=100,
            fills_backfill_max_pages=5,
        )

        # Should have called API twice
        assert alpaca_client.get_account_activities.call_count == 2


class TestBackfillFillMetadata:
    """Tests for backfill_fill_metadata function."""

    def test_returns_false_when_no_filled_avg_price(self) -> None:
        """Backfill returns False when broker order has no filled_avg_price."""
        db_client = MagicMock()
        broker_order: dict[str, Any] = {
            "filled_qty": "100",
            "filled_avg_price": None,
        }

        result = backfill_fill_metadata("client123", broker_order, db_client)

        assert result is False

    def test_returns_false_when_order_not_found(self) -> None:
        """Backfill returns False when order not found in DB."""
        db_client = MagicMock()

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = None

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
        }

        result = backfill_fill_metadata("client123", broker_order, db_client)

        assert result is False

    def test_creates_synthetic_fill_when_missing(self) -> None:
        """Backfill creates synthetic fill when fills are missing."""
        db_client = MagicMock()

        mock_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": []},  # No existing fills
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = mock_order

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
            "updated_at": datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
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
            db_client.append_fill_to_order_metadata.assert_called_once()

    def test_returns_false_when_no_fill_needed(self) -> None:
        """Backfill returns False when synthetic fill calculation returns None."""
        db_client = MagicMock()

        mock_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": [{"fill_qty": "100"}]},  # Existing fill
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.return_value = mock_order

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
        }

        with patch(
            "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
        ) as mock_calc:
            mock_calc.return_value = None  # No fill needed

            result = backfill_fill_metadata("client123", broker_order, db_client)

            assert result is False

    def test_uses_cached_order_when_provided(self) -> None:
        """Backfill uses cached order instead of fetching from DB."""
        db_client = MagicMock()

        cached_order = MockOrder(
            client_order_id="client123",
            metadata={"fills": []},
        )

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
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

            backfill_fill_metadata("client123", broker_order, db_client, cached_order=cached_order)

            db_client.get_order_for_update.assert_not_called()

    def test_handles_exception_gracefully(self) -> None:
        """Backfill handles exceptions and returns False."""
        db_client = MagicMock()

        @contextmanager
        def mock_transaction():
            yield MagicMock()

        db_client.transaction = mock_transaction
        db_client.get_order_for_update.side_effect = Exception("DB error")

        broker_order = {
            "filled_qty": "100",
            "filled_avg_price": "150.00",
        }

        result = backfill_fill_metadata("client123", broker_order, db_client)

        assert result is False


class TestBackfillFillMetadataFromOrder:
    """Tests for backfill_fill_metadata_from_order function."""

    def test_returns_false_when_no_filled_avg_price(self) -> None:
        """Backfill returns False when order has no filled_avg_price."""
        db_client = MagicMock()
        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=None,
        )

        result = backfill_fill_metadata_from_order(order, db_client)

        assert result is False

    def test_returns_false_when_no_filled_qty(self) -> None:
        """Backfill returns False when order has no filled_qty."""
        db_client = MagicMock()
        order = MockOrder(
            client_order_id="client123",
            filled_qty=None,  # type: ignore
            filled_avg_price=Decimal("150.00"),
        )

        result = backfill_fill_metadata_from_order(order, db_client)

        assert result is False

    def test_creates_synthetic_fill_from_order_data(self) -> None:
        """Backfill creates synthetic fill using order's filled data."""
        db_client = MagicMock()

        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.00"),
            filled_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
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

        with patch(
            "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
        ) as mock_calc:
            mock_calc.return_value = {
                "fill_id": "syn_123",
                "fill_qty": "100",
                "fill_price": "150.00",
                "source": "recon_db",
                "_missing_qty": Decimal("100"),
            }

            result = backfill_fill_metadata_from_order(order, db_client)

            assert result is True
            call_kwargs = db_client.append_fill_to_order_metadata.call_args.kwargs
            assert call_kwargs["fill_data"]["source"] == "reconciliation_db_backfill"

    def test_uses_updated_at_when_no_filled_at(self) -> None:
        """Backfill uses updated_at when filled_at is not available."""
        db_client = MagicMock()

        order = MockOrder(
            client_order_id="client123",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("150.00"),
            filled_at=None,
            updated_at=datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC),
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

        with patch(
            "apps.execution_gateway.reconciliation.fills.calculate_synthetic_fill"
        ) as mock_calc:
            mock_calc.return_value = {
                "fill_id": "syn_123",
                "fill_qty": "100",
                "fill_price": "150.00",
                "source": "recon_db",
                "_missing_qty": Decimal("100"),
            }

            backfill_fill_metadata_from_order(order, db_client)

            call_args = mock_calc.call_args
            assert call_args.kwargs["timestamp"] == order.updated_at


class TestBackfillMissingFillsScan:
    """Tests for backfill_missing_fills_scan function."""

    def test_returns_zero_when_no_missing_fills(self) -> None:
        """Scan returns 0 when no orders missing fills."""
        db_client = MagicMock()
        db_client.get_filled_orders_missing_fills.return_value = []

        result = backfill_missing_fills_scan(db_client)

        assert result == 0

    def test_backfills_orders_missing_fills(self) -> None:
        """Scan backfills each order missing fills."""
        db_client = MagicMock()

        orders_missing = [
            MockOrder(
                client_order_id="order1",
                filled_qty=Decimal("100"),
                filled_avg_price=Decimal("150.00"),
            ),
            MockOrder(
                client_order_id="order2",
                filled_qty=Decimal("50"),
                filled_avg_price=Decimal("200.00"),
            ),
        ]
        db_client.get_filled_orders_missing_fills.return_value = orders_missing

        with patch(
            "apps.execution_gateway.reconciliation.fills.backfill_fill_metadata_from_order"
        ) as mock_backfill:
            mock_backfill.return_value = True

            result = backfill_missing_fills_scan(db_client)

            assert result == 2
            assert mock_backfill.call_count == 2

    def test_respects_limit_parameter(self) -> None:
        """Scan respects the limit parameter."""
        db_client = MagicMock()
        db_client.get_filled_orders_missing_fills.return_value = []

        backfill_missing_fills_scan(db_client, limit=50)

        db_client.get_filled_orders_missing_fills.assert_called_with(limit=50)

    def test_handles_exception_gracefully(self) -> None:
        """Scan handles exceptions and returns 0."""
        db_client = MagicMock()
        db_client.get_filled_orders_missing_fills.side_effect = Exception("DB error")

        result = backfill_missing_fills_scan(db_client)

        assert result == 0

    def test_counts_only_successful_backfills(self) -> None:
        """Scan counts only orders that were successfully backfilled."""
        db_client = MagicMock()

        orders_missing = [
            MockOrder(
                client_order_id="order1",
                filled_qty=Decimal("100"),
                filled_avg_price=Decimal("150.00"),
            ),
            MockOrder(
                client_order_id="order2",
                filled_qty=Decimal("50"),
                filled_avg_price=Decimal("200.00"),
            ),
        ]
        db_client.get_filled_orders_missing_fills.return_value = orders_missing

        with patch(
            "apps.execution_gateway.reconciliation.fills.backfill_fill_metadata_from_order"
        ) as mock_backfill:
            # First succeeds, second fails
            mock_backfill.side_effect = [True, False]

            result = backfill_missing_fills_scan(db_client)

            assert result == 1
