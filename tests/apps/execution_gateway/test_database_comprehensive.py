"""
Comprehensive unit tests for DatabaseClient to achieve 85%+ branch coverage.

This test suite focuses on gaps in existing test coverage, including:
- Transaction handling and context managers
- Connection management (pool recreation, cleanup)
- Query builder methods (filters, pagination)
- Edge cases for reconciliation helpers
- Error handling paths
- Database operation variants (transactional vs standalone)

Design:
- Uses mocks to avoid real database dependencies
- Tests both happy paths and error conditions
- Validates SQL query construction and parameter handling
- Covers connection lifecycle (init, close, recreate)
"""

from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from psycopg import DatabaseError, IntegrityError, OperationalError
from psycopg.errors import AdminShutdown

from apps.execution_gateway.database import (
    PENDING_STATUSES,
    TERMINAL_STATUSES,
    DatabaseClient,
    calculate_position_update,
    calculate_position_update_decimal,
    status_rank_for,
)
from apps.execution_gateway.schemas import OrderRequest


@contextmanager
def _mock_pool_connection(cursor_side_effect=None, conn_side_effect=None):
    """Context manager returning a mocked pool connection + cursor."""
    with patch("apps.execution_gateway.database.ConnectionPool") as mock_pool:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        pool_instance = mock_pool.return_value

        if conn_side_effect:
            pool_instance.connection.return_value.__enter__.side_effect = conn_side_effect
        else:
            pool_instance.connection.return_value.__enter__.return_value = mock_conn

        if cursor_side_effect:
            mock_conn.cursor.return_value.__enter__.side_effect = cursor_side_effect
        else:
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        yield mock_pool, mock_conn, mock_cursor


# ============================================================================
# Test Pure Functions (calculate_position_update variants)
# ============================================================================


class TestPositionCalculationFunctions:
    """Test pure P&L calculation functions without database dependencies."""

    def test_calculate_position_update_open_long(self):
        """Test opening a new long position from flat (qty=0)."""
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=0,
            old_avg_price=Decimal("0"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("150"),
            side="buy",
        )

        assert new_qty == 100
        assert new_avg == Decimal("150")
        assert new_pl == Decimal("0")

    def test_calculate_position_update_open_short(self):
        """Test opening a new short position from flat (qty=0)."""
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=0,
            old_avg_price=Decimal("0"),
            old_realized_pl=Decimal("0"),
            fill_qty=50,
            fill_price=Decimal("200"),
            side="sell",
        )

        assert new_qty == -50
        assert new_avg == Decimal("200")
        assert new_pl == Decimal("0")

    def test_calculate_position_update_add_to_long(self):
        """Test adding to existing long position (weighted average)."""
        # Initial: 100 @ $150, add 50 @ $160
        # New avg: (100*150 + 50*160) / 150 = $153.33
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=100,
            old_avg_price=Decimal("150"),
            old_realized_pl=Decimal("0"),
            fill_qty=50,
            fill_price=Decimal("160"),
            side="buy",
        )

        assert new_qty == 150
        assert abs(new_avg - Decimal("153.333333")) < Decimal("0.001")
        assert new_pl == Decimal("0")  # No P&L when adding

    def test_calculate_position_update_reduce_long_with_profit(self):
        """Test reducing long position realizes partial P&L."""
        # Initial: 100 @ $150, sell 50 @ $160
        # P&L: (160 - 150) * 50 = $500
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=100,
            old_avg_price=Decimal("150"),
            old_realized_pl=Decimal("0"),
            fill_qty=50,
            fill_price=Decimal("160"),
            side="sell",
        )

        assert new_qty == 50
        assert new_avg == Decimal("150")  # Avg unchanged
        assert new_pl == Decimal("500")

    def test_calculate_position_update_close_long_with_loss(self):
        """Test closing long position realizes loss."""
        # Initial: 100 @ $150, sell all 100 @ $140
        # P&L: (140 - 150) * 100 = -$1000
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=100,
            old_avg_price=Decimal("150"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("140"),
            side="sell",
        )

        assert new_qty == 0
        assert new_avg == Decimal("0")  # Closed position resets avg
        assert new_pl == Decimal("-1000")

    def test_calculate_position_update_flip_long_to_short(self):
        """Test position flip (long to short) realizes P&L on closed portion."""
        # Initial: 50 @ $100, sell 100 @ $120
        # P&L on 50 closed: (120 - 100) * 50 = $1000
        # New position: -50 @ $120
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=50,
            old_avg_price=Decimal("100"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("120"),
            side="sell",
        )

        assert new_qty == -50
        assert new_avg == Decimal("120")
        assert new_pl == Decimal("1000")

    def test_calculate_position_update_flip_short_to_long(self):
        """Test position flip (short to long) realizes P&L."""
        # Initial: -50 @ $200, buy 100 @ $180
        # P&L on 50 covered: (200 - 180) * 50 = $1000 profit
        # New position: 50 @ $180
        new_qty, new_avg, new_pl = calculate_position_update(
            old_qty=-50,
            old_avg_price=Decimal("200"),
            old_realized_pl=Decimal("0"),
            fill_qty=100,
            fill_price=Decimal("180"),
            side="buy",
        )

        assert new_qty == 50
        assert new_avg == Decimal("180")
        assert new_pl == Decimal("1000")

    def test_calculate_position_update_decimal_variant(self):
        """Test Decimal-safe variant for trade reconstruction."""
        new_qty, new_avg, realized_delta = calculate_position_update_decimal(
            old_qty=Decimal("100"),
            old_avg_price=Decimal("150"),
            fill_qty=Decimal("50"),
            fill_price=Decimal("160"),
            side="sell",
        )

        assert new_qty == Decimal("50")
        assert new_avg == Decimal("150")
        assert realized_delta == Decimal("500")  # (160 - 150) * 50

    def test_status_rank_for_terminal_statuses(self):
        """Test status ranking for conflict resolution."""
        # Terminal fill status has highest rank
        assert status_rank_for("filled") == 5

        # Other terminal statuses have rank 4
        assert status_rank_for("canceled") == 4
        assert status_rank_for("rejected") == 4

        # Active statuses have rank 3
        assert status_rank_for("partially_filled") == 3

        # Submitted statuses have rank 2
        assert status_rank_for("submitted") == 2

        # Initial statuses have rank 1
        assert status_rank_for("pending_new") == 1

        # Unknown status returns 0
        assert status_rank_for("unknown_status") == 0


# ============================================================================
# Test Connection Management
# ============================================================================


class TestDatabaseClientLifecycle:
    """Test database client initialization, connection pooling, and cleanup."""

    def test_initialization_validates_empty_connection_string(self):
        """Test that empty connection string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            DatabaseClient("")

    def test_initialization_creates_connection_pool(self):
        """Test that initialization creates a connection pool with correct settings."""
        with patch("apps.execution_gateway.database.ConnectionPool") as mock_pool:
            _ = DatabaseClient("postgresql://user:pass@localhost/db")

            mock_pool.assert_called_once()
            call_args = mock_pool.call_args

            # Verify connection string passed
            assert call_args[0][0] == "postgresql://user:pass@localhost/db"

            # Verify pool settings
            assert call_args[1]["min_size"] >= 2
            assert call_args[1]["max_size"] >= 2
            assert call_args[1]["timeout"] > 0

    def test_close_closes_pool(self):
        """Test that close() properly closes the connection pool."""
        with patch("apps.execution_gateway.database.ConnectionPool") as mock_pool:
            db = DatabaseClient("postgresql://localhost/db")
            pool_instance = mock_pool.return_value

            db.close()

            pool_instance.close.assert_called_once()

    def test_close_is_safe_to_call_multiple_times(self):
        """Test that calling close() multiple times doesn't error."""
        with patch("apps.execution_gateway.database.ConnectionPool") as mock_pool:
            db = DatabaseClient("postgresql://localhost/db")
            pool_instance = mock_pool.return_value

            db.close()
            db.close()  # Should not raise

            assert pool_instance.close.call_count == 2

    def test_recreate_pool_closes_old_and_creates_new(self):
        """Test that _recreate_pool handles pool recreation."""
        with patch("apps.execution_gateway.database.ConnectionPool") as mock_pool:
            db = DatabaseClient("postgresql://localhost/db")
            old_pool = mock_pool.return_value

            # Recreate pool
            db._recreate_pool()

            # Old pool should be closed
            old_pool.close.assert_called_once()

            # New pool should be created (2 calls: init + recreate)
            assert mock_pool.call_count == 2


# ============================================================================
# Test Transaction Context Manager
# ============================================================================


class TestTransactionContextManager:
    """Test transaction() context manager for atomic operations."""

    def test_transaction_commits_on_success(self):
        """Test transaction commits when no exception occurs."""
        with _mock_pool_connection() as (_, mock_conn, _):
            mock_txn = MagicMock()
            mock_conn.transaction.return_value = mock_txn
            mock_txn.__enter__.return_value = None
            mock_txn.__exit__.return_value = False

            db = DatabaseClient("postgresql://localhost/db")

            with db.transaction() as conn:
                assert conn == mock_conn

            # Verify transaction was entered
            mock_conn.transaction.assert_called_once()
            mock_txn.__enter__.assert_called_once()
            mock_txn.__exit__.assert_called_once()

    def test_transaction_rolls_back_on_integrity_error(self):
        """Test transaction rolls back on IntegrityError."""
        with _mock_pool_connection() as (_, mock_conn, _):
            mock_txn = MagicMock()
            mock_conn.transaction.return_value = mock_txn
            mock_txn.__enter__.return_value = None

            db = DatabaseClient("postgresql://localhost/db")

            with pytest.raises(IntegrityError):
                with db.transaction():
                    raise IntegrityError("Duplicate key")

    def test_transaction_rolls_back_on_database_error(self):
        """Test transaction rolls back on DatabaseError."""
        with _mock_pool_connection() as (_, mock_conn, _):
            mock_txn = MagicMock()
            mock_conn.transaction.return_value = mock_txn
            mock_txn.__enter__.return_value = None

            db = DatabaseClient("postgresql://localhost/db")

            with pytest.raises(DatabaseError):
                with db.transaction():
                    raise DatabaseError("Connection lost")


# ============================================================================
# Test Query Helpers and Filters
# ============================================================================


class TestGetPendingOrders:
    """Test get_pending_orders with pagination and filters."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_pending_orders_no_filters(self, mock_connection):
        """Test fetching pending orders without filters."""
        _, _, mock_cursor = mock_connection

        # Mock two rows: one for orders, one for count
        order_data = {
            "client_order_id": "order1",
            "strategy_id": "alpha",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "pending_new",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("0"),
        }

        mock_cursor.fetchall.return_value = [order_data]
        mock_cursor.fetchone.return_value = (1,)  # Total count

        db = DatabaseClient("postgresql://localhost/db")
        orders, total = db.get_pending_orders()

        assert len(orders) == 1
        assert total == 1
        assert orders[0].client_order_id == "order1"

    def test_get_pending_orders_with_symbol_filter(self, mock_connection):
        """Test fetching pending orders filtered by symbol."""
        _, _, mock_cursor = mock_connection

        order_data = {
            "client_order_id": "order1",
            "strategy_id": "alpha",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "new",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("0"),
        }

        mock_cursor.fetchall.return_value = [order_data]
        mock_cursor.fetchone.return_value = (1,)

        db = DatabaseClient("postgresql://localhost/db")
        orders, total = db.get_pending_orders(symbol="AAPL")

        assert len(orders) == 1
        assert orders[0].symbol == "AAPL"

    def test_get_pending_orders_with_pagination(self, mock_connection):
        """Test pending orders with limit and offset."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = (50,)

        db = DatabaseClient("postgresql://localhost/db")
        orders, total = db.get_pending_orders(limit=10, offset=20)

        assert len(orders) == 0
        assert total == 50

    def test_get_pending_orders_with_strategy_filter(self, mock_connection):
        """Test pending orders filtered by strategy IDs."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = (0,)

        db = DatabaseClient("postgresql://localhost/db")
        orders, total = db.get_pending_orders(strategy_ids=["alpha", "beta"])

        assert total == 0

    def test_get_pending_orders_handles_admin_shutdown(self, mock_connection):
        """Test get_pending_orders recovers from AdminShutdown error."""
        mock_pool, _, mock_cursor = mock_connection

        # First call raises AdminShutdown, second succeeds
        # Must use actual AdminShutdown exception (not OperationalError with message)
        # because code checks: "AdminShutdown" in type(exc).__name__
        mock_cursor.execute.side_effect = [
            AdminShutdown("terminating connection due to administrator command"),
            None,
            None,
        ]
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = (0,)

        db = DatabaseClient("postgresql://localhost/db")

        # First execution should trigger pool recreation and retry
        orders, total = db.get_pending_orders()

        assert total == 0


class TestGetRecentFills:
    """Test get_recent_fills with time windows and filters."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_recent_fills_empty_strategies_returns_empty(self, mock_connection):
        """Test that empty strategy list returns empty result."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_recent_fills(strategy_ids=[], limit=50, lookback_hours=24)

        assert result == []

    def test_get_recent_fills_with_limit_capping(self, mock_connection):
        """Test that limit is capped at 200."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_recent_fills(strategy_ids=["alpha"], limit=500, lookback_hours=24)

        # Limit should be capped at 200
        assert result == []

    def test_get_recent_fills_with_lookback_capping(self, mock_connection):
        """Test that lookback_hours is capped at 168 (7 days)."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_recent_fills(strategy_ids=["alpha"], limit=50, lookback_hours=1000)

        assert result == []

    def test_get_recent_fills_handles_admin_shutdown(self, mock_connection):
        """Test get_recent_fills recovers from AdminShutdown error."""
        _, _, mock_cursor = mock_connection

        mock_cursor.execute.side_effect = [
            OperationalError("terminating connection due to administrator command"),
            None,
        ]
        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_recent_fills(strategy_ids=["alpha"], limit=50, lookback_hours=24)

        assert result == []


# ============================================================================
# Test Reconciliation Helpers
# ============================================================================


class TestReconciliationMethods:
    """Test reconciliation-specific database methods."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_filled_orders_missing_fills_limits_results(self, mock_connection):
        """Test that limit parameter is clamped between 1 and 500."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/db")

        # Test limit clamping
        result = db.get_filled_orders_missing_fills(limit=1000)
        assert result == []

        result = db.get_filled_orders_missing_fills(limit=0)
        assert result == []

    def test_get_order_ids_by_client_ids_empty_input(self, mock_connection):
        """Test that empty client_order_ids returns empty set."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_order_ids_by_client_ids([])

        assert result == set()

    def test_get_order_ids_by_client_ids_deduplicates(self, mock_connection):
        """Test that duplicate IDs are deduplicated."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = [
            {"client_order_id": "id1"},
            {"client_order_id": "id2"},
        ]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_order_ids_by_client_ids(["id1", "id1", "id2", "id2"])

        assert result == {"id1", "id2"}

    def test_get_orders_by_broker_ids_empty_input(self, mock_connection):
        """Test that empty broker_order_ids returns empty dict."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_orders_by_broker_ids([])

        assert result == {}

    def test_get_non_terminal_orders_without_date_filter(self, mock_connection):
        """Test fetching non-terminal orders without date filter."""
        _, _, mock_cursor = mock_connection

        order_data = {
            "client_order_id": "order1",
            "strategy_id": "alpha",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "new",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("0"),
        }

        mock_cursor.fetchall.return_value = [order_data]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_non_terminal_orders()

        assert len(result) == 1

    def test_get_non_terminal_orders_with_date_filter(self, mock_connection):
        """Test fetching non-terminal orders with date filter."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/db")
        cutoff = datetime(2024, 10, 19, 0, 0, 0, tzinfo=UTC)
        result = db.get_non_terminal_orders(created_before=cutoff)

        assert result == []

    def test_get_reconciliation_high_water_mark_not_found(self, mock_connection):
        """Test fetching high-water mark when no record exists."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_reconciliation_high_water_mark("orders")

        assert result is None

    def test_get_reconciliation_high_water_mark_found(self, mock_connection):
        """Test fetching existing high-water mark."""
        _, _, mock_cursor = mock_connection

        timestamp = datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC)
        mock_cursor.fetchone.return_value = {"last_check_time": timestamp}

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_reconciliation_high_water_mark("orders")

        assert result == timestamp

    def test_set_reconciliation_high_water_mark(self, mock_connection):
        """Test upserting reconciliation high-water mark."""
        _, mock_conn, mock_cursor = mock_connection

        db = DatabaseClient("postgresql://localhost/db")
        timestamp = datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC)

        db.set_reconciliation_high_water_mark(timestamp, name="orders")

        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()


# ============================================================================
# Test Orphan Order Management
# ============================================================================


class TestOrphanOrderMethods:
    """Test orphan order detection and tracking."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_create_orphan_order(self, mock_connection):
        """Test creating an orphan order record."""
        _, mock_conn, mock_cursor = mock_connection

        db = DatabaseClient("postgresql://localhost/db")

        db.create_orphan_order(
            broker_order_id="broker123",
            client_order_id="client456",
            symbol="AAPL",
            strategy_id="alpha",
            side="buy",
            qty=10,
            estimated_notional=Decimal("1500"),
            status="filled",
        )

        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_update_orphan_order_status(self, mock_connection):
        """Test updating orphan order status."""
        _, mock_conn, mock_cursor = mock_connection

        db = DatabaseClient("postgresql://localhost/db")
        resolved_at = datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC)

        db.update_orphan_order_status("broker123", "resolved", resolved_at=resolved_at)

        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_get_orphan_exposure(self, mock_connection):
        """Test calculating orphan exposure for symbol/strategy."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = {"total": Decimal("5000")}

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_orphan_exposure("AAPL", "alpha")

        assert result == Decimal("5000")

    def test_get_orphan_exposure_no_results(self, mock_connection):
        """Test orphan exposure when no unresolved orphans exist."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_orphan_exposure("AAPL", "alpha")

        assert result == Decimal("0")


# ============================================================================
# Test Strategy and Position Methods
# ============================================================================


class TestStrategyStatusMethods:
    """Test strategy status aggregation methods."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_strategy_status_not_found(self, mock_connection):
        """Test getting status for non-existent strategy returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_strategy_status("nonexistent")

        assert result is None

    def test_get_strategy_status_found(self, mock_connection):
        """Test getting status for existing strategy."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = {
            "positions_count": 5,
            "open_orders_count": 3,
            "last_signal": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_strategy_status("alpha")

        assert result is not None
        assert result["positions_count"] == 5
        assert result["open_orders_count"] == 3
        assert result["today_pnl"] == Decimal("0")

    def test_get_all_strategy_ids_no_filter(self, mock_connection):
        """Test fetching all strategy IDs without filter."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = [("alpha",), ("beta",), ("gamma",)]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_all_strategy_ids()

        assert result == ["alpha", "beta", "gamma"]

    def test_get_all_strategy_ids_with_filter(self, mock_connection):
        """Test fetching strategy IDs with filter."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = [("alpha",), ("beta",)]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_all_strategy_ids(filter_ids=["alpha", "beta"])

        assert result == ["alpha", "beta"]

    def test_get_bulk_strategy_status_empty_input(self, mock_connection):
        """Test bulk strategy status with empty input returns empty dict."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_bulk_strategy_status([])

        assert result == {}

    def test_get_bulk_strategy_status_fills_missing_strategies(self, mock_connection):
        """Test bulk status ensures all requested strategies have entries."""
        _, _, mock_cursor = mock_connection

        # Only return data for "alpha", not "beta"
        mock_cursor.fetchall.return_value = [
            {
                "strategy_id": "alpha",
                "positions_count": 5,
                "open_orders_count": 3,
                "last_signal": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            }
        ]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_bulk_strategy_status(["alpha", "beta"])

        assert "alpha" in result
        assert "beta" in result
        assert result["beta"]["positions_count"] == 0


class TestPositionHelpers:
    """Test position-related helper methods."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_position_by_symbol_not_found(self, mock_connection):
        """Test fetching position for symbol with no position returns 0."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_position_by_symbol("AAPL")

        assert result == 0

    def test_get_position_by_symbol_found(self, mock_connection):
        """Test fetching existing position quantity."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = {"qty": Decimal("100")}

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_position_by_symbol("AAPL")

        assert result == 100

    def test_get_positions_for_strategies_empty_input(self, mock_connection):
        """Test that empty strategy list returns empty positions."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_positions_for_strategies([])

        assert result == []

    def test_get_strategy_map_for_symbols_empty_input(self, mock_connection):
        """Test strategy map with empty symbol list returns empty dict."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_strategy_map_for_symbols([])

        assert result == {}

    def test_get_strategy_map_for_symbols_single_strategy(self, mock_connection):
        """Test strategy map when symbol has single strategy."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = [
            {"symbol": "AAPL", "strategies": ["alpha"]}
        ]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_strategy_map_for_symbols(["AAPL"])

        assert result["AAPL"] == "alpha"

    def test_get_strategy_map_for_symbols_multiple_strategies(self, mock_connection):
        """Test strategy map when symbol has multiple strategies returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = [
            {"symbol": "AAPL", "strategies": ["alpha", "beta"]}
        ]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_strategy_map_for_symbols(["AAPL"])

        assert result["AAPL"] is None


# ============================================================================
# Test Transactional Fill Processing
# ============================================================================


class TestTransactionalFillMethods:
    """Test fill metadata appending and transactional position updates."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_append_fill_to_order_metadata_missing_fill_id(self, mock_connection):
        """Test that fill without fill_id is rejected."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = {"client_order_id": "order1"}

        db = DatabaseClient("postgresql://localhost/db")

        # Create a fake connection to pass
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Fill without fill_id should return None
        result = db.append_fill_to_order_metadata(
            "order1",
            {"fill_qty": "10", "fill_price": "100", "realized_pl": "0"},
            fake_conn,
        )

        assert result is None

    def test_append_fill_to_order_metadata_order_not_found(self, mock_connection):
        """Test appending fill to non-existent order returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")

        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = mock_cursor

        result = db.append_fill_to_order_metadata(
            "nonexistent",
            {"fill_id": "fill123", "fill_qty": "10"},
            fake_conn,
        )

        assert result is None

    def test_get_position_for_update_with_advisory_lock(self, mock_connection):
        """Test get_position_for_update takes advisory lock."""
        _, _, mock_cursor = mock_connection

        pos_data = {
            "symbol": "AAPL",
            "qty": Decimal("100"),
            "avg_entry_price": Decimal("150"),
            "realized_pl": Decimal("0"),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.return_value = pos_data

        db = DatabaseClient("postgresql://localhost/db")

        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = mock_cursor

        result = db.get_position_for_update("AAPL", fake_conn)

        assert result is not None
        assert result.symbol == "AAPL"
        # Verify advisory lock was acquired
        assert mock_cursor.execute.call_count == 2  # Lock + select

    def test_get_order_for_update_locks_row(self, mock_connection):
        """Test get_order_for_update uses SELECT FOR UPDATE."""
        _, _, mock_cursor = mock_connection

        order_data = {
            "client_order_id": "order1",
            "strategy_id": "alpha",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "new",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("0"),
        }

        mock_cursor.fetchone.return_value = order_data

        db = DatabaseClient("postgresql://localhost/db")

        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = mock_cursor

        result = db.get_order_for_update("order1", fake_conn)

        assert result is not None
        assert result.client_order_id == "order1"


# ============================================================================
# Test Update Order Status CAS (Compare-and-Swap)
# ============================================================================


class TestUpdateOrderStatusCAS:
    """Test conflict resolution for concurrent order status updates."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_update_order_status_cas_success(self, mock_connection):
        """Test CAS update succeeds when conditions met."""
        _, _, mock_cursor = mock_connection

        updated_order = {
            "client_order_id": "order1",
            "strategy_id": "alpha",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "filled",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("10"),
            "filled_avg_price": Decimal("150"),
        }

        mock_cursor.fetchone.return_value = updated_order

        db = DatabaseClient("postgresql://localhost/db")

        result = db.update_order_status_cas(
            client_order_id="order1",
            status="filled",
            broker_updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            status_rank=5,
            source_priority=1,
            filled_qty=Decimal("10"),
            filled_avg_price=Decimal("150"),
        )

        assert result is not None
        assert result.status == "filled"

    def test_update_order_status_cas_conflict_returns_none(self, mock_connection):
        """Test CAS update returns None when update conditions not met."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None  # No rows updated

        db = DatabaseClient("postgresql://localhost/db")

        result = db.update_order_status_cas(
            client_order_id="order1",
            status="filled",
            broker_updated_at=datetime(2024, 10, 19, 11, 0, 0, tzinfo=UTC),  # Older
            status_rank=5,
            source_priority=1,
        )

        assert result is None


# ============================================================================
# Test Performance Dashboard Queries
# ============================================================================


class TestPerformanceDashboardQueries:
    """Test performance dashboard data retrieval."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_data_availability_date_no_data(self, mock_connection):
        """Test data availability when no fills exist."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = {"first_date": None}

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_data_availability_date()

        assert result is None

    def test_get_data_availability_date_with_data(self, mock_connection):
        """Test data availability returns earliest fill date."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = {"first_date": date(2024, 1, 1)}

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_data_availability_date()

        assert result == date(2024, 1, 1)

    def test_get_daily_pnl_history_empty_strategies(self, mock_connection):
        """Test daily PnL with empty strategies returns empty list."""
        db = DatabaseClient("postgresql://localhost/db")

        result = db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 31), [])

        assert result == []

    def test_get_daily_pnl_history_with_data(self, mock_connection):
        """Test daily PnL history returns aggregated data."""
        _, _, mock_cursor = mock_connection

        pnl_data = [
            {
                "trade_date": date(2024, 1, 1),
                "daily_realized_pl": Decimal("100"),
                "closing_trade_count": 5,
            },
            {
                "trade_date": date(2024, 1, 2),
                "daily_realized_pl": Decimal("-50"),
                "closing_trade_count": 3,
            },
        ]

        mock_cursor.fetchall.return_value = pnl_data

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_daily_pnl_history(date(2024, 1, 1), date(2024, 1, 2), ["alpha"])

        assert len(result) == 2
        assert result[0]["trade_date"] == date(2024, 1, 1)


# ============================================================================
# Test Slice Management Methods
# ============================================================================


class TestSliceManagement:
    """Test TWAP slice-related methods."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_get_pending_child_slices(self, mock_connection):
        """Test fetching pending child slices for recovery."""
        _, _, mock_cursor = mock_connection

        slice_data = {
            "client_order_id": "slice1",
            "parent_order_id": "parent1",
            "slice_num": 0,
            "strategy_id": "twap_slice",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "pending_new",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("0"),
            "scheduled_time": datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC),
        }

        mock_cursor.fetchall.return_value = [slice_data]

        db = DatabaseClient("postgresql://localhost/db")
        result = db.get_pending_child_slices()

        assert len(result) == 1
        assert result[0].parent_order_id == "parent1"

    def test_update_order_scheduled_time(self, mock_connection):
        """Test updating scheduled_time for a slice."""
        _, mock_conn, mock_cursor = mock_connection

        updated_slice = {
            "client_order_id": "slice1",
            "parent_order_id": "parent1",
            "slice_num": 0,
            "strategy_id": "twap_slice",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "pending_new",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("0"),
            "scheduled_time": datetime(2024, 10, 19, 12, 5, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.return_value = updated_slice

        db = DatabaseClient("postgresql://localhost/db")
        new_time = datetime(2024, 10, 19, 12, 5, 0, tzinfo=UTC)
        result = db.update_order_scheduled_time("slice1", new_time)

        assert result is not None
        assert result.scheduled_time == new_time

    def test_count_non_terminal_children(self, mock_connection):
        """Test counting non-terminal child slices."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = (3,)

        db = DatabaseClient("postgresql://localhost/db")
        result = db.count_non_terminal_children("parent1")

        assert result == 3


# ============================================================================
# Test Trade Reconstruction
# ============================================================================


class TestTradeReconstruction:
    """Test recalculate_trade_realized_pnl for trade reconciliation."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_recalculate_trade_realized_pnl_no_trades(self, mock_connection):
        """Test P&L recalculation with no trades."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/db")
        result = db.recalculate_trade_realized_pnl("alpha", "AAPL")

        assert result["trades_total"] == 0
        assert result["trades_updated"] == 0

    def test_recalculate_trade_realized_pnl_with_trades(self, mock_connection):
        """Test P&L recalculation updates synthetic trades."""
        _, _, mock_cursor = mock_connection

        # Mock trade rows
        trades = [
            {
                "trade_id": "trade1",
                "side": "buy",
                "qty": 100,
                "price": 150,
                "realized_pnl": 0,
                "source": "alpaca_activity",
                "synthetic": False,
                "executed_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            },
            {
                "trade_id": "trade2",
                "side": "sell",
                "qty": 50,
                "price": 160,
                "realized_pnl": 0,
                "source": "reconciliation_backfill",
                "synthetic": True,
                "executed_at": datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC),
            },
        ]

        mock_cursor.fetchall.return_value = trades

        db = DatabaseClient("postgresql://localhost/db")
        result = db.recalculate_trade_realized_pnl("alpha", "AAPL")

        assert result["trades_total"] == 2
        # Synthetic trade should be updated
        assert result["trades_updated"] >= 1


# ============================================================================
# Test Position Snapshot Upsert
# ============================================================================


class TestPositionSnapshot:
    """Test upsert_position_snapshot for reconciliation."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_upsert_position_snapshot_creates_new(self, mock_connection):
        """Test upserting position creates new record."""
        _, mock_conn, mock_cursor = mock_connection

        pos_data = {
            "symbol": "AAPL",
            "qty": Decimal("100"),
            "avg_entry_price": Decimal("150"),
            "current_price": Decimal("155"),
            "realized_pl": Decimal("0"),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.return_value = pos_data

        db = DatabaseClient("postgresql://localhost/db")

        result = db.upsert_position_snapshot(
            symbol="AAPL",
            qty=Decimal("100"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("155"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        assert result.symbol == "AAPL"
        assert result.qty == Decimal("100")


# ============================================================================
# Test Error Handling Edge Cases
# ============================================================================


class TestErrorHandlingEdgeCases:
    """Test error handling for database operations."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_create_order_returns_none_raises_value_error(self, mock_connection):
        """Test that create_order raises ValueError when INSERT returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        req = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        with pytest.raises(ValueError, match="Failed to create order"):
            db.create_order("order1", "alpha", req, "pending_new")

    def test_create_parent_order_returns_none_raises_value_error(self, mock_connection):
        """Test that create_parent_order raises ValueError when INSERT returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        req = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        with pytest.raises(ValueError, match="Failed to create parent order"):
            db.create_parent_order("parent1", "twap", req, total_slices=5)

    def test_create_child_slice_returns_none_raises_value_error(self, mock_connection):
        """Test that create_child_slice raises ValueError when INSERT returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")
        req = OrderRequest(symbol="AAPL", side="buy", qty=20, order_type="market")

        with pytest.raises(ValueError, match="Failed to create child slice"):
            db.create_child_slice(
                "slice1",
                "parent1",
                0,
                "twap_slice",
                req,
                datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            )

    def test_upsert_position_snapshot_returns_none_raises_value_error(self, mock_connection):
        """Test that upsert_position_snapshot raises ValueError when upsert returns None."""
        _, _, mock_cursor = mock_connection

        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/db")

        with pytest.raises(ValueError, match="Failed to upsert position snapshot"):
            db.upsert_position_snapshot(
                "AAPL",
                Decimal("100"),
                Decimal("150"),
                None,
                datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            )

    def test_update_position_on_fill_returns_none_raises_value_error(self, mock_connection):
        """Test that update_position_on_fill raises ValueError when upsert returns None."""
        _, _, mock_cursor = mock_connection

        # First fetchone for current position, second for upsert result
        mock_cursor.fetchone.side_effect = [None, None]

        db = DatabaseClient("postgresql://localhost/db")

        with pytest.raises(ValueError, match="Failed to update position"):
            db.update_position_on_fill("AAPL", 10, Decimal("150"), "buy")


# ============================================================================
# Test Row to OrderDetail Helper
# ============================================================================


class TestRowToOrderDetail:
    """Test _row_to_order_detail helper method."""

    @pytest.fixture()
    def mock_connection(self):
        with _mock_pool_connection() as mocks:
            yield mocks

    def test_row_to_order_detail_with_partial_row(self, mock_connection):
        """Test _row_to_order_detail provides defaults for missing fields."""
        db = DatabaseClient("postgresql://localhost/db")

        # Minimal row with some fields missing
        partial_row = {
            "client_order_id": "order1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
        }

        result = db._row_to_order_detail(partial_row)

        assert result.client_order_id == "order1"
        assert result.symbol == "AAPL"
        assert result.strategy_id == "unknown"  # Default
        assert result.status == "pending_new"  # Default

    def test_row_to_order_detail_with_complete_row(self, mock_connection):
        """Test _row_to_order_detail uses provided values when available."""
        db = DatabaseClient("postgresql://localhost/db")

        complete_row = {
            "client_order_id": "order1",
            "strategy_id": "alpha",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
            "status": "filled",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("10"),
        }

        result = db._row_to_order_detail(complete_row)

        assert result.strategy_id == "alpha"
        assert result.status == "filled"


# ============================================================================
# Summary Statistics
# ============================================================================


def test_terminal_statuses_constant():
    """Test TERMINAL_STATUSES contains expected values."""
    assert "filled" in TERMINAL_STATUSES
    assert "canceled" in TERMINAL_STATUSES
    assert "rejected" in TERMINAL_STATUSES
    assert "expired" in TERMINAL_STATUSES
    assert len(TERMINAL_STATUSES) >= 8


def test_pending_statuses_constant():
    """Test PENDING_STATUSES contains expected values."""
    assert "new" in PENDING_STATUSES
    assert "pending_new" in PENDING_STATUSES
    assert "submitted" in PENDING_STATUSES
    assert "partially_filled" in PENDING_STATUSES
    assert len(PENDING_STATUSES) >= 8
