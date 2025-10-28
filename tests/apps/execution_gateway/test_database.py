"""
Unit tests for DatabaseClient.

Tests cover:
- Initialization and validation
- Order CRUD operations (create, read, update)
- Position updates with complex P&L calculations
- Transaction handling and error cases
- Connection health checks
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from psycopg import DatabaseError, IntegrityError, OperationalError

from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import OrderDetail, OrderRequest, Position


class TestDatabaseClientInitialization:
    """Tests for DatabaseClient initialization."""

    def test_initialization_success(self):
        """Test successful initialization with valid connection string."""
        db = DatabaseClient("postgresql://localhost/trading_platform")

        assert db.db_conn_string == "postgresql://localhost/trading_platform"

    def test_initialization_with_empty_string_raises_error(self):
        """Test initialization with empty connection string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            DatabaseClient("")

    def test_initialization_logs_database_info(self):
        """Test initialization logs database connection info."""
        # Just verify it doesn't crash - logging is implementation detail
        db = DatabaseClient("postgresql://user:pass@localhost:5432/trading_platform")
        assert db.db_conn_string is not None


class TestCreateOrder:
    """Tests for create_order method."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_create_order_success(self, mock_connection):
        """Test successful order creation."""
        _, mock_conn, mock_cursor = mock_connection

        # Mock database return
        order_data = {
            "client_order_id": "abc123",
            "strategy_id": "alpha_baseline",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "pending_new",
            "broker_order_id": "broker123",
            "error_message": None,
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = order_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        result = db.create_order(
            client_order_id="abc123",
            strategy_id="alpha_baseline",
            order_request=order_request,
            status="pending_new",
            broker_order_id="broker123",
        )

        # Verify result
        assert isinstance(result, OrderDetail)
        assert result.client_order_id == "abc123"
        assert result.symbol == "AAPL"
        assert result.status == "pending_new"

        # Verify database operations
        mock_cursor.execute.assert_called_once()
        # Note: Context manager handles commit automatically - no explicit commit needed

    def test_create_order_with_dry_run_status(self, mock_connection):
        """Test order creation with dry_run status (no submitted_at)."""
        _, mock_conn, mock_cursor = mock_connection

        order_data = {
            "client_order_id": "dry123",
            "strategy_id": "alpha_baseline",
            "symbol": "MSFT",
            "side": "sell",
            "qty": 5,
            "order_type": "limit",
            "limit_price": Decimal("300.50"),
            "stop_price": None,
            "time_in_force": "day",
            "status": "dry_run",
            "broker_order_id": None,
            "error_message": None,
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": None,  # Dry run has no submission
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = order_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(
            symbol="MSFT", side="sell", qty=5, order_type="limit", limit_price=Decimal("300.50")
        )

        result = db.create_order(
            client_order_id="dry123",
            strategy_id="alpha_baseline",
            order_request=order_request,
            status="dry_run",
        )

        assert result.status == "dry_run"
        assert result.broker_order_id is None

    def test_create_order_duplicate_raises_integrity_error(self, mock_connection):
        """Test creating duplicate order raises IntegrityError."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.side_effect = IntegrityError("Duplicate key")

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10)

        with pytest.raises(IntegrityError):
            db.create_order(
                client_order_id="dup123",
                strategy_id="alpha_baseline",
                order_request=order_request,
                status="pending_new",
            )

    def test_create_order_database_error_raises(self, mock_connection):
        """Test database error during order creation raises DatabaseError."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.side_effect = DatabaseError("Connection lost")

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=10)

        with pytest.raises(DatabaseError):
            db.create_order(
                client_order_id="err123",
                strategy_id="alpha_baseline",
                order_request=order_request,
                status="pending_new",
            )


class TestGetOrderByClientId:
    """Tests for get_order_by_client_id method."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_get_order_found(self, mock_connection):
        """Test fetching existing order returns OrderDetail."""
        _, _, mock_cursor = mock_connection

        order_data = {
            "client_order_id": "found123",
            "strategy_id": "alpha_baseline",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "filled",
            "broker_order_id": "broker123",
            "error_message": None,
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("10"),
            "filled_avg_price": Decimal("150.25"),
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = order_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_order_by_client_id("found123")

        assert result is not None
        assert isinstance(result, OrderDetail)
        assert result.client_order_id == "found123"
        assert result.status == "filled"

    def test_get_order_not_found_returns_none(self, mock_connection):
        """Test fetching non-existent order returns None."""
        _, _, mock_cursor = mock_connection
        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_order_by_client_id("notfound")

        assert result is None

    def test_get_order_database_error_raises(self, mock_connection):
        """Test database error during fetch raises DatabaseError."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.side_effect = OperationalError("Connection timeout")

        db = DatabaseClient("postgresql://localhost/trading_platform")

        with pytest.raises(OperationalError):
            db.get_order_by_client_id("err123")


class TestUpdateOrderStatus:
    """Tests for update_order_status method."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_update_status_to_filled(self, mock_connection):
        """Test updating order to filled status."""
        _, mock_conn, mock_cursor = mock_connection

        updated_order = {
            "client_order_id": "upd123",
            "strategy_id": "alpha_baseline",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "filled",
            "broker_order_id": "broker123",
            "error_message": None,
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_qty": Decimal("10"),
            "filled_avg_price": Decimal("150.25"),
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = updated_order

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_order_status(
            client_order_id="upd123",
            status="filled",
            filled_qty=Decimal("10"),
            filled_avg_price=Decimal("150.25"),
        )

        assert result is not None
        assert result.status == "filled"
        assert result.filled_qty == Decimal("10")
        # Context manager handles commit automatically - no explicit commit needed

    def test_update_status_order_not_found(self, mock_connection):
        """Test updating non-existent order returns None."""
        _, _, mock_cursor = mock_connection
        mock_cursor.fetchone.return_value = None

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_order_status(client_order_id="notfound", status="filled")

        assert result is None

    def test_update_status_with_error_message(self, mock_connection):
        """Test updating order with error message."""
        _, mock_conn, mock_cursor = mock_connection

        rejected_order = {
            "client_order_id": "rej123",
            "strategy_id": "alpha_baseline",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "rejected",
            "broker_order_id": None,
            "error_message": "Insufficient buying power",
            "retry_count": 0,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = rejected_order

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_order_status(
            client_order_id="rej123", status="rejected", error_message="Insufficient buying power"
        )

        assert result.status == "rejected"
        assert result.error_message == "Insufficient buying power"


class TestUpdatePositionOnFill:
    """Tests for update_position_on_fill method with complex P&L calculations."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_open_new_long_position(self, mock_connection):
        """Test opening a new long position from flat."""
        _, mock_conn, mock_cursor = mock_connection

        # No existing position
        mock_cursor.fetchone.side_effect = [
            None,  # First fetch: no existing position
            {  # Second fetch: newly created position
                "symbol": "AAPL",
                "qty": Decimal("10"),
                "avg_entry_price": Decimal("150.00"),
                "current_price": None,
                "unrealized_pl": None,
                "realized_pl": Decimal("0.00"),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            },
        ]

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_position_on_fill(
            symbol="AAPL", qty=10, price=Decimal("150.00"), side="buy"
        )

        assert result.symbol == "AAPL"
        assert result.qty == Decimal("10")
        assert result.avg_entry_price == Decimal("150.00")
        assert result.realized_pl == Decimal("0.00")

    def test_add_to_long_position_updates_weighted_average(self, mock_connection):
        """Test adding to long position updates weighted average price."""
        _, mock_conn, mock_cursor = mock_connection

        # Existing position: 10 shares @ $150
        existing_position = {
            "symbol": "AAPL",
            "qty": Decimal("10"),
            "avg_entry_price": Decimal("150.00"),
            "realized_pl": Decimal("0.00"),
        }

        # New position: 15 shares @ $150.67 (weighted avg)
        # (10 * $150 + 5 * $152) / 15 = $150.67
        new_position = {
            "symbol": "AAPL",
            "qty": Decimal("15"),
            "avg_entry_price": Decimal("150.666666666666666666666666667"),
            "current_price": None,
            "unrealized_pl": None,
            "realized_pl": Decimal("0.00"),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.side_effect = [existing_position, new_position]

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_position_on_fill(
            symbol="AAPL", qty=5, price=Decimal("152.00"), side="buy"
        )

        assert result.qty == Decimal("15")
        # Weighted average: (150*10 + 152*5) / 15 ≈ 150.67
        assert abs(result.avg_entry_price - Decimal("150.67")) < Decimal("0.01")

    def test_close_long_position_realizes_pnl(self, mock_connection):
        """Test closing long position realizes P&L."""
        _, mock_conn, mock_cursor = mock_connection

        # Existing: 10 shares @ $150
        # Sell all 10 @ $155
        # Expected P&L: (155 - 150) * 10 = $50
        existing_position = {
            "symbol": "AAPL",
            "qty": Decimal("10"),
            "avg_entry_price": Decimal("150.00"),
            "realized_pl": Decimal("0.00"),
        }

        closed_position = {
            "symbol": "AAPL",
            "qty": Decimal("0"),
            "avg_entry_price": Decimal("155.00"),  # Last fill price
            "current_price": None,
            "unrealized_pl": None,
            "realized_pl": Decimal("50.00"),  # (155 - 150) * 10
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.side_effect = [existing_position, closed_position]

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_position_on_fill(
            symbol="AAPL", qty=10, price=Decimal("155.00"), side="sell"
        )

        assert result.qty == Decimal("0")
        assert result.realized_pl == Decimal("50.00")

    def test_reduce_long_position_realizes_partial_pnl(self, mock_connection):
        """Test reducing long position realizes partial P&L."""
        _, mock_conn, mock_cursor = mock_connection

        # Existing: 10 shares @ $150
        # Sell 5 @ $155
        # Expected partial P&L: (155 - 150) * 5 = $25
        existing_position = {
            "symbol": "AAPL",
            "qty": Decimal("10"),
            "avg_entry_price": Decimal("150.00"),
            "realized_pl": Decimal("0.00"),
        }

        reduced_position = {
            "symbol": "AAPL",
            "qty": Decimal("5"),
            "avg_entry_price": Decimal("150.00"),  # Unchanged
            "current_price": None,
            "unrealized_pl": None,
            "realized_pl": Decimal("25.00"),  # (155 - 150) * 5
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.side_effect = [existing_position, reduced_position]

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_position_on_fill(
            symbol="AAPL", qty=5, price=Decimal("155.00"), side="sell"
        )

        assert result.qty == Decimal("5")
        assert result.avg_entry_price == Decimal("150.00")
        assert result.realized_pl == Decimal("25.00")

    def test_open_short_position(self, mock_connection):
        """Test opening a short position."""
        _, mock_conn, mock_cursor = mock_connection

        # No existing position, sell to open short
        mock_cursor.fetchone.side_effect = [
            None,
            {
                "symbol": "TSLA",
                "qty": Decimal("-10"),  # Negative = short
                "avg_entry_price": Decimal("300.00"),
                "current_price": None,
                "unrealized_pl": None,
                "realized_pl": Decimal("0.00"),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            },
        ]

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_position_on_fill(
            symbol="TSLA", qty=10, price=Decimal("300.00"), side="sell"
        )

        assert result.qty == Decimal("-10")
        assert result.avg_entry_price == Decimal("300.00")

    def test_close_short_position_realizes_pnl(self, mock_connection):
        """Test closing short position realizes P&L."""
        _, mock_conn, mock_cursor = mock_connection

        # Existing: -10 shares @ $300 (short)
        # Buy 10 @ $290 to close
        # Expected P&L: (300 - 290) * 10 = $100 profit
        existing_position = {
            "symbol": "TSLA",
            "qty": Decimal("-10"),
            "avg_entry_price": Decimal("300.00"),
            "realized_pl": Decimal("0.00"),
        }

        closed_position = {
            "symbol": "TSLA",
            "qty": Decimal("0"),
            "avg_entry_price": Decimal("290.00"),
            "current_price": None,
            "unrealized_pl": None,
            "realized_pl": Decimal("100.00"),  # (300 - 290) * 10
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        }

        mock_cursor.fetchone.side_effect = [existing_position, closed_position]

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.update_position_on_fill(
            symbol="TSLA", qty=10, price=Decimal("290.00"), side="buy"
        )

        assert result.qty == Decimal("0")
        assert result.realized_pl == Decimal("100.00")


class TestGetAllPositions:
    """Tests for get_all_positions method."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_get_all_positions_returns_list(self, mock_connection):
        """Test getting all positions returns list of Position objects."""
        _, _, mock_cursor = mock_connection

        positions_data = [
            {
                "symbol": "AAPL",
                "qty": Decimal("10"),
                "avg_entry_price": Decimal("150.00"),
                "current_price": None,
                "unrealized_pl": None,
                "realized_pl": Decimal("0.00"),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            },
            {
                "symbol": "MSFT",
                "qty": Decimal("-5"),
                "avg_entry_price": Decimal("300.00"),
                "current_price": None,
                "unrealized_pl": None,
                "realized_pl": Decimal("50.00"),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "last_trade_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            },
        ]
        mock_cursor.fetchall.return_value = positions_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_all_positions()

        assert len(result) == 2
        assert all(isinstance(p, Position) for p in result)
        assert result[0].symbol == "AAPL"
        assert result[1].symbol == "MSFT"

    def test_get_all_positions_empty_returns_empty_list(self, mock_connection):
        """Test getting positions when none exist returns empty list."""
        _, _, mock_cursor = mock_connection
        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_all_positions()

        assert result == []

    def test_get_all_positions_database_error_raises(self, mock_connection):
        """Test database error during fetch raises DatabaseError."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.side_effect = OperationalError("Connection lost")

        db = DatabaseClient("postgresql://localhost/trading_platform")

        with pytest.raises(OperationalError):
            db.get_all_positions()


class TestCheckConnection:
    """Tests for check_connection method."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_check_connection_success(self, mock_connection):
        """Test successful connection check returns True."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.return_value = None  # SELECT 1 succeeds

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.check_connection()

        assert result is True

    def test_check_connection_failure_returns_false(self, mock_connection):
        """Test failed connection check returns False."""
        mock_connect, _, _ = mock_connection
        mock_connect.return_value.__enter__.side_effect = OperationalError("Connection refused")

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.check_connection()

        assert result is False


class TestCreateParentOrder:
    """Tests for create_parent_order method (Component 5 - TWAP parent orders)."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_create_parent_order_success(self, mock_connection):
        """Test successful parent order creation with parent_order_id=NULL."""
        _, mock_conn, mock_cursor = mock_connection

        # Mock database return with parent order fields
        parent_data = {
            "client_order_id": "parent123",
            "strategy_id": "twap_parent",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "pending_new",
            "broker_order_id": None,
            "error_message": None,
            "retry_count": 0,
            "parent_order_id": None,  # Parent orders have NULL parent_order_id
            "slice_num": None,  # Parent orders don't have slice_num
            "total_slices": 5,  # Indicates number of child slices planned
            "scheduled_time": None,  # Parent orders don't have scheduled_time
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": None,
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = parent_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=100, order_type="market")

        result = db.create_parent_order(
            client_order_id="parent123",
            strategy_id="twap_parent",
            order_request=order_request,
            total_slices=5,
        )

        # Verify result
        assert isinstance(result, OrderDetail)
        assert result.client_order_id == "parent123"
        assert result.symbol == "AAPL"
        assert result.parent_order_id is None  # Parent orders have NULL parent_order_id
        assert result.total_slices == 5
        assert result.slice_num is None

        # Verify database operations
        mock_cursor.execute.assert_called_once()
        # Note: commit() is automatic via psycopg context manager, not explicitly called

    def test_create_parent_order_duplicate_raises_integrity_error(self, mock_connection):
        """Test creating duplicate parent order raises IntegrityError."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.side_effect = IntegrityError("Duplicate key")

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=100)

        with pytest.raises(IntegrityError):
            db.create_parent_order(
                client_order_id="dup_parent",
                strategy_id="twap_parent",
                order_request=order_request,
                total_slices=5,
            )

    def test_create_parent_order_fields_correctly_set(self, mock_connection):
        """Test parent order fields are correctly set (NULL parent_order_id, has total_slices)."""
        _, mock_conn, mock_cursor = mock_connection

        parent_data = {
            "client_order_id": "parent456",
            "strategy_id": "twap_parent",
            "symbol": "TSLA",
            "side": "sell",
            "qty": 50,
            "order_type": "limit",
            "limit_price": Decimal("200.00"),
            "stop_price": None,
            "time_in_force": "day",
            "status": "pending_new",
            "broker_order_id": None,
            "error_message": None,
            "retry_count": 0,
            "parent_order_id": None,
            "slice_num": None,
            "total_slices": 3,
            "scheduled_time": None,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": None,
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = parent_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(
            symbol="TSLA", side="sell", qty=50, order_type="limit", limit_price=Decimal("200.00")
        )

        result = db.create_parent_order(
            client_order_id="parent456",
            strategy_id="twap_parent",
            order_request=order_request,
            total_slices=3,
            status="pending_new",
        )

        assert result.parent_order_id is None
        assert result.total_slices == 3
        assert result.slice_num is None
        assert result.scheduled_time is None
        assert result.limit_price == Decimal("200.00")


class TestCreateChildSlice:
    """Tests for create_child_slice method (Component 6 - TWAP child slices)."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_create_child_slice_success(self, mock_connection):
        """Test successful child slice creation with parent reference."""
        _, mock_conn, mock_cursor = mock_connection

        scheduled = datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC)

        # Mock database return with child slice fields
        child_data = {
            "client_order_id": "child123",
            "strategy_id": "twap_slice_parent123_0",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 20,
            "order_type": "market",
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "pending_new",
            "broker_order_id": None,
            "error_message": None,
            "retry_count": 0,
            "parent_order_id": "parent123",  # References parent order
            "slice_num": 0,  # First slice
            "total_slices": None,  # Only parent orders have total_slices
            "scheduled_time": scheduled,  # When to execute this slice
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": None,
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = child_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=20, order_type="market")

        result = db.create_child_slice(
            client_order_id="child123",
            parent_order_id="parent123",
            slice_num=0,
            strategy_id="twap_slice_parent123_0",
            order_request=order_request,
            scheduled_time=scheduled,
        )

        # Verify result
        assert isinstance(result, OrderDetail)
        assert result.client_order_id == "child123"
        assert result.parent_order_id == "parent123"
        assert result.slice_num == 0
        assert result.scheduled_time == scheduled
        assert result.total_slices is None  # Child slices don't have total_slices

        # Verify database operations
        mock_cursor.execute.assert_called_once()
        # Note: commit() is automatic via psycopg context manager, not explicitly called

    def test_create_child_slice_duplicate_id_raises_integrity_error(self, mock_connection):
        """Test creating child slice with duplicate client_order_id raises IntegrityError."""
        _, _, mock_cursor = mock_connection
        mock_cursor.execute.side_effect = IntegrityError("Duplicate key")

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=20)
        scheduled = datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC)

        with pytest.raises(IntegrityError):
            db.create_child_slice(
                client_order_id="dup_child",
                parent_order_id="parent123",
                slice_num=0,
                strategy_id="twap_slice_parent123_0",
                order_request=order_request,
                scheduled_time=scheduled,
            )

    def test_create_child_slice_duplicate_slice_num_raises_integrity_error(self, mock_connection):
        """Test creating child slice with duplicate (parent_order_id, slice_num) raises error."""
        _, _, mock_cursor = mock_connection
        # Database enforces unique constraint on (parent_order_id, slice_num)
        mock_cursor.execute.side_effect = IntegrityError("Duplicate slice_num for parent")

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(symbol="AAPL", side="buy", qty=20)
        scheduled = datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC)

        with pytest.raises(IntegrityError):
            db.create_child_slice(
                client_order_id="child_new",
                parent_order_id="parent123",
                slice_num=0,  # Duplicate slice_num for this parent
                strategy_id="twap_slice_parent123_0",
                order_request=order_request,
                scheduled_time=scheduled,
            )

    def test_create_child_slice_fields_correctly_set(self, mock_connection):
        """Test child slice fields are correctly set (has parent_order_id, slice_num, scheduled_time)."""
        _, mock_conn, mock_cursor = mock_connection

        scheduled = datetime(2024, 10, 19, 12, 2, 0, tzinfo=UTC)

        child_data = {
            "client_order_id": "child456",
            "strategy_id": "twap_slice_parent456_1",
            "symbol": "TSLA",
            "side": "sell",
            "qty": 17,
            "order_type": "limit",
            "limit_price": Decimal("200.00"),
            "stop_price": None,
            "time_in_force": "day",
            "status": "pending_new",
            "broker_order_id": None,
            "error_message": None,
            "retry_count": 0,
            "parent_order_id": "parent456",
            "slice_num": 1,
            "total_slices": None,
            "scheduled_time": scheduled,
            "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            "submitted_at": None,
            "filled_at": None,
            "filled_qty": Decimal("0"),
            "filled_avg_price": None,
            "metadata": {},
        }
        mock_cursor.fetchone.return_value = child_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        order_request = OrderRequest(
            symbol="TSLA", side="sell", qty=17, order_type="limit", limit_price=Decimal("200.00")
        )

        result = db.create_child_slice(
            client_order_id="child456",
            parent_order_id="parent456",
            slice_num=1,
            strategy_id="twap_slice_parent456_1",
            order_request=order_request,
            scheduled_time=scheduled,
        )

        assert result.parent_order_id == "parent456"
        assert result.slice_num == 1
        assert result.scheduled_time == scheduled
        assert result.total_slices is None
        assert result.limit_price == Decimal("200.00")


class TestGetSlicesByParentId:
    """Tests for get_slices_by_parent_id method (Component 6 - query child slices)."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_get_slices_by_parent_id_empty(self, mock_connection):
        """Test querying slices for parent with no slices returns empty list."""
        _, _, mock_cursor = mock_connection
        mock_cursor.fetchall.return_value = []

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_slices_by_parent_id("parent_no_slices")

        assert result == []

    def test_get_slices_by_parent_id_multiple(self, mock_connection):
        """Test querying slices returns all child slices for parent."""
        _, _, mock_cursor = mock_connection

        slices_data = [
            {
                "client_order_id": "child0",
                "parent_order_id": "parent123",
                "slice_num": 0,
                "strategy_id": "twap_slice_parent123_0",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 20,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "day",
                "status": "pending_new",
                "broker_order_id": None,
                "error_message": None,
                "retry_count": 0,
                "total_slices": None,
                "scheduled_time": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "submitted_at": None,
                "filled_at": None,
                "filled_qty": Decimal("0"),
                "filled_avg_price": None,
                "metadata": {},
            },
            {
                "client_order_id": "child1",
                "parent_order_id": "parent123",
                "slice_num": 1,
                "strategy_id": "twap_slice_parent123_1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 20,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "day",
                "status": "filled",
                "broker_order_id": "broker001",
                "error_message": None,
                "retry_count": 0,
                "total_slices": None,
                "scheduled_time": datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC),
                "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC),
                "submitted_at": datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC),
                "filled_at": datetime(2024, 10, 19, 12, 1, 10, tzinfo=UTC),
                "filled_qty": Decimal("20"),
                "filled_avg_price": Decimal("150.50"),
                "metadata": {},
            },
        ]
        mock_cursor.fetchall.return_value = slices_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_slices_by_parent_id("parent123")

        assert len(result) == 2
        assert all(isinstance(s, OrderDetail) for s in result)
        assert result[0].parent_order_id == "parent123"
        assert result[1].parent_order_id == "parent123"
        assert result[0].slice_num == 0
        assert result[1].slice_num == 1

    def test_get_slices_by_parent_id_ordering(self, mock_connection):
        """Test slices are returned in slice_num order (0, 1, 2, ...)."""
        _, _, mock_cursor = mock_connection

        # Return slices in correct order (0, 1, 2)
        slices_data = [
            {
                "client_order_id": "child0",
                "parent_order_id": "parent789",
                "slice_num": 0,
                "strategy_id": "twap_slice_parent789_0",
                "symbol": "GOOG",
                "side": "buy",
                "qty": 1,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "day",
                "status": "pending_new",
                "broker_order_id": None,
                "error_message": None,
                "retry_count": 0,
                "total_slices": None,
                "scheduled_time": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "submitted_at": None,
                "filled_at": None,
                "filled_qty": Decimal("0"),
                "filled_avg_price": None,
                "metadata": {},
            },
            {
                "client_order_id": "child1",
                "parent_order_id": "parent789",
                "slice_num": 1,
                "strategy_id": "twap_slice_parent789_1",
                "symbol": "GOOG",
                "side": "buy",
                "qty": 1,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "day",
                "status": "pending_new",
                "broker_order_id": None,
                "error_message": None,
                "retry_count": 0,
                "total_slices": None,
                "scheduled_time": datetime(2024, 10, 19, 12, 1, 0, tzinfo=UTC),
                "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "submitted_at": None,
                "filled_at": None,
                "filled_qty": Decimal("0"),
                "filled_avg_price": None,
                "metadata": {},
            },
            {
                "client_order_id": "child2",
                "parent_order_id": "parent789",
                "slice_num": 2,
                "strategy_id": "twap_slice_parent789_2",
                "symbol": "GOOG",
                "side": "buy",
                "qty": 1,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "day",
                "status": "pending_new",
                "broker_order_id": None,
                "error_message": None,
                "retry_count": 0,
                "total_slices": None,
                "scheduled_time": datetime(2024, 10, 19, 12, 2, 0, tzinfo=UTC),
                "created_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                "submitted_at": None,
                "filled_at": None,
                "filled_qty": Decimal("0"),
                "filled_avg_price": None,
                "metadata": {},
            },
        ]
        mock_cursor.fetchall.return_value = slices_data

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.get_slices_by_parent_id("parent789")

        # Verify ordering: slice_num should be 0, 1, 2
        assert len(result) == 3
        assert result[0].slice_num == 0
        assert result[1].slice_num == 1
        assert result[2].slice_num == 2


class TestCancelPendingSlices:
    """Tests for cancel_pending_slices method (Component 6 - cancel child slices)."""

    @pytest.fixture()
    def mock_connection(self):
        """Create mock database connection."""
        with patch("apps.execution_gateway.database.psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            yield mock_connect, mock_conn, mock_cursor

    def test_cancel_pending_slices_zero(self, mock_connection):
        """Test canceling slices when none are pending returns 0."""
        _, mock_conn, mock_cursor = mock_connection
        mock_cursor.rowcount = 0  # No rows updated

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.cancel_pending_slices("parent_no_pending")

        assert result == 0
        # Context manager handles commit automatically - no explicit commit needed

    def test_cancel_pending_slices_some(self, mock_connection):
        """Test canceling some pending slices returns correct count."""
        _, mock_conn, mock_cursor = mock_connection
        mock_cursor.rowcount = 3  # 3 rows updated

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.cancel_pending_slices("parent123")

        assert result == 3
        # Context manager handles commit automatically - no explicit commit needed

    def test_cancel_pending_slices_all(self, mock_connection):
        """Test canceling all pending slices for a parent."""
        _, mock_conn, mock_cursor = mock_connection
        mock_cursor.rowcount = 5  # All 5 slices canceled

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.cancel_pending_slices("parent_all_pending")

        assert result == 5
        # Context manager handles commit automatically - no explicit commit needed

    def test_cancel_pending_slices_ignores_non_pending(self, mock_connection):
        """Test cancel only affects pending_new slices, not filled/canceled/etc."""
        _, mock_conn, mock_cursor = mock_connection
        # Out of 5 total slices: 2 pending_new, 2 filled, 1 canceled
        # Only the 2 pending_new should be canceled
        mock_cursor.rowcount = 2

        db = DatabaseClient("postgresql://localhost/trading_platform")
        result = db.cancel_pending_slices("parent_mixed_status")

        assert result == 2
        # Context manager handles commit automatically - no explicit commit needed
