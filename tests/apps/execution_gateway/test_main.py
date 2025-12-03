"""
Unit tests for Execution Gateway FastAPI application.

Tests cover:
- Root and health endpoints
- Order submission (DRY_RUN mode)
- Order retrieval by client_order_id
- Position listing with P&L
- Helper functions for price fetching and P&L calculation
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

# Import app at module level (will use real clients initially)
from apps.execution_gateway.main import app
from apps.execution_gateway.schemas import OrderDetail, Position


@pytest.fixture()
def test_client():
    """Create FastAPI test client."""
    return TestClient(app)


@pytest.fixture()
def mock_db():
    """Create a mock DatabaseClient."""
    return Mock()


@pytest.fixture()
def mock_redis():
    """Create a mock RedisClient."""
    return Mock()


@pytest.fixture()
def mock_kill_switch():
    """Create a mock KillSwitch (not engaged, available)."""
    mock_ks = Mock()
    mock_ks.is_engaged.return_value = False
    return mock_ks


@pytest.fixture()
def mock_circuit_breaker():
    """Create a mock CircuitBreaker (not tripped)."""
    mock_cb = Mock()
    mock_cb.is_tripped.return_value = False
    mock_cb.get_trip_reason.return_value = None
    return mock_cb


@pytest.fixture()
def mock_position_reservation():
    """Create a mock PositionReservation (always succeeds)."""
    from libs.risk_management.position_reservation import ReleaseResult, ReservationResult

    mock_pr = Mock()
    # Return successful reservation result
    mock_pr.reserve.return_value = ReservationResult(
        success=True,
        token="mock-token-123",
        reason="",
        previous_position=0,
        new_position=10,
    )
    # confirm and release return ReleaseResult
    mock_pr.confirm.return_value = ReleaseResult(
        success=True,
        reason="",
    )
    mock_pr.release.return_value = ReleaseResult(
        success=True,
        reason="",
    )
    return mock_pr


class TestRootEndpoint:
    """Tests for root endpoint."""

    def test_root_returns_service_info(self, test_client):
        """Test root endpoint returns service information."""
        response = test_client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "execution_gateway"
        assert "version" in data
        assert "status" in data


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_check_healthy_in_dry_run(self, test_client, mock_db):
        """Test health check returns healthy when database is up (DRY_RUN mode)."""
        mock_db.check_connection.return_value = True

        with (
            patch("apps.execution_gateway.main.db_client", mock_db),
            patch("apps.execution_gateway.main._kill_switch_unavailable", False),
            patch("apps.execution_gateway.main._circuit_breaker_unavailable", False),
            patch("apps.execution_gateway.main._position_reservation_unavailable", False),
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database_connected"] is True
        assert data["dry_run"] is True

    def test_health_check_unhealthy_when_db_down(self, test_client, mock_db):
        """Test health check returns unhealthy when database is down."""
        mock_db.check_connection.return_value = False

        with (
            patch("apps.execution_gateway.main.db_client", mock_db),
            patch("apps.execution_gateway.main._kill_switch_unavailable", False),
            patch("apps.execution_gateway.main._circuit_breaker_unavailable", False),
            patch("apps.execution_gateway.main._position_reservation_unavailable", False),
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database_connected"] is False


class TestSubmitOrderEndpoint:
    """Tests for order submission endpoint."""

    def test_submit_order_dry_run_mode(
        self, test_client, mock_db, mock_kill_switch, mock_circuit_breaker, mock_position_reservation
    ):
        """Test order submission in DRY_RUN mode logs order without broker submission."""
        # Mock: Order doesn't exist yet
        mock_db.get_order_by_client_id.return_value = None

        # Mock: Order creation
        created_order = OrderDetail(
            client_order_id="test123",
            strategy_id="alpha_baseline",
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
            status="dry_run",
            broker_order_id=None,
            error_message=None,
            retry_count=0,
            created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            submitted_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            filled_at=None,
            filled_qty=Decimal("0"),
            filled_avg_price=None,
        )
        mock_db.create_order.return_value = created_order

        with (
            patch("apps.execution_gateway.main.db_client", mock_db),
            patch("apps.execution_gateway.main.kill_switch", mock_kill_switch),
            patch("apps.execution_gateway.main.circuit_breaker", mock_circuit_breaker),
            patch("apps.execution_gateway.main.position_reservation", mock_position_reservation),
            patch("apps.execution_gateway.main._kill_switch_unavailable", False),
            patch("apps.execution_gateway.main._circuit_breaker_unavailable", False),
            patch("apps.execution_gateway.main._position_reservation_unavailable", False),
        ):
            response = test_client.post(
                "/api/v1/orders",
                json={"symbol": "AAPL", "side": "buy", "qty": 10, "order_type": "market"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dry_run"
        assert data["symbol"] == "AAPL"
        assert data["side"] == "buy"
        assert data["qty"] == 10
        assert "Order logged (DRY_RUN mode)" in data["message"]

    def test_submit_order_idempotent_returns_existing(
        self, test_client, mock_db, mock_kill_switch, mock_circuit_breaker, mock_position_reservation
    ):
        """Test submitting duplicate order returns existing order (idempotent)."""
        # Mock: Order already exists
        existing_order = OrderDetail(
            client_order_id="existing123",
            strategy_id="alpha_baseline",
            symbol="MSFT",
            side="sell",
            qty=5,
            order_type="limit",
            limit_price=Decimal("300.50"),
            stop_price=None,
            time_in_force="day",
            status="pending_new",
            broker_order_id="broker123",
            error_message=None,
            retry_count=0,
            created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            submitted_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            filled_at=None,
            filled_qty=Decimal("0"),
            filled_avg_price=None,
        )
        mock_db.get_order_by_client_id.return_value = existing_order

        with (
            patch("apps.execution_gateway.main.db_client", mock_db),
            patch("apps.execution_gateway.main.kill_switch", mock_kill_switch),
            patch("apps.execution_gateway.main.circuit_breaker", mock_circuit_breaker),
            patch("apps.execution_gateway.main.position_reservation", mock_position_reservation),
            patch("apps.execution_gateway.main._kill_switch_unavailable", False),
            patch("apps.execution_gateway.main._circuit_breaker_unavailable", False),
            patch("apps.execution_gateway.main._position_reservation_unavailable", False),
        ):
            response = test_client.post(
                "/api/v1/orders",
                json={
                    "symbol": "MSFT",
                    "side": "sell",
                    "qty": 5,
                    "order_type": "limit",
                    "limit_price": 300.50,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending_new"  # Existing order's status
        assert "already submitted" in data["message"]

    def test_submit_order_validation_error(self, test_client):
        """Test order submission with invalid parameters returns 422."""
        response = test_client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "invalid_side",  # Invalid side
                "qty": 10,
                "order_type": "market",
            },
        )

        assert response.status_code == 422  # Validation error


class TestGetOrderEndpoint:
    """Tests for get order endpoint."""

    def test_get_order_found(self, test_client, mock_db):
        """Test retrieving an existing order returns order details."""
        order = OrderDetail(
            client_order_id="test123",
            strategy_id="alpha_baseline",
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
            limit_price=None,
            stop_price=None,
            time_in_force="day",
            status="filled",
            broker_order_id="broker123",
            error_message=None,
            retry_count=0,
            created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            submitted_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            filled_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            filled_qty=Decimal("10"),
            filled_avg_price=Decimal("150.25"),
        )
        mock_db.get_order_by_client_id.return_value = order

        with patch("apps.execution_gateway.main.db_client", mock_db):
            response = test_client.get("/api/v1/orders/test123")

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "test123"
        assert data["symbol"] == "AAPL"
        assert data["status"] == "filled"

    def test_get_order_not_found(self, test_client, mock_db):
        """Test retrieving non-existent order returns 404."""
        mock_db.get_order_by_client_id.return_value = None

        with patch("apps.execution_gateway.main.db_client", mock_db):
            response = test_client.get("/api/v1/orders/nonexistent123")

        assert response.status_code == 404


class TestGetPositionsEndpoint:
    """Tests for get positions endpoint."""

    def test_get_positions_returns_list(self, test_client, mock_db):
        """Test getting positions returns list of positions."""
        positions = [
            Position(
                symbol="AAPL",
                qty=Decimal("10"),
                avg_entry_price=Decimal("150.00"),
                current_price=Decimal("152.00"),
                unrealized_pl=Decimal("20.00"),
                realized_pl=Decimal("0.00"),
                updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            ),
            Position(
                symbol="MSFT",
                qty=Decimal("-5"),
                avg_entry_price=Decimal("300.00"),
                current_price=Decimal("295.00"),
                unrealized_pl=Decimal("25.00"),
                realized_pl=Decimal("0.00"),
                updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
            ),
        ]
        mock_db.get_all_positions.return_value = positions

        with patch("apps.execution_gateway.main.db_client", mock_db):
            response = test_client.get("/api/v1/positions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 2
        assert data["total_positions"] == 2
        assert Decimal(data["total_unrealized_pl"]) == Decimal("45.00")

    def test_get_positions_empty(self, test_client, mock_db):
        """Test getting positions when none exist returns empty list."""
        mock_db.get_all_positions.return_value = []

        with patch("apps.execution_gateway.main.db_client", mock_db):
            response = test_client.get("/api/v1/positions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 0
        assert data["total_positions"] == 0


class TestBatchFetchRealtimePrices:
    """Tests for batch real-time price fetching helper."""

    def test_batch_fetch_with_valid_data(self, mock_redis):
        """Test batch fetching prices from Redis with valid data."""
        from apps.execution_gateway.main import _batch_fetch_realtime_prices_from_redis

        # Mock Redis MGET response (strings, not bytes, due to decode_responses=True)
        mock_redis.mget.return_value = [
            '{"mid": "152.50", "timestamp": "2024-10-19T12:00:00+00:00"}',
            '{"mid": "295.00", "timestamp": "2024-10-19T12:00:00+00:00"}',
        ]

        result = _batch_fetch_realtime_prices_from_redis(["AAPL", "MSFT"], mock_redis)

        assert len(result) == 2
        assert result["AAPL"][0] == Decimal("152.50")
        assert result["MSFT"][0] == Decimal("295.00")

    def test_batch_fetch_redis_unavailable(self):
        """Test batch fetch returns dict with None values when Redis is unavailable."""
        from apps.execution_gateway.main import _batch_fetch_realtime_prices_from_redis

        result = _batch_fetch_realtime_prices_from_redis(["AAPL"], None)

        assert result == {"AAPL": (None, None)}

    def test_batch_fetch_empty_symbols(self, mock_redis):
        """Test batch fetch with empty symbol list returns empty dict."""
        from apps.execution_gateway.main import _batch_fetch_realtime_prices_from_redis

        result = _batch_fetch_realtime_prices_from_redis([], mock_redis)

        assert result == {}


class TestCalculatePositionPnL:
    """Tests for position P&L calculation helper."""

    def test_calculate_pnl_for_long_position_profit(self):
        """Test P&L calculation for profitable long position."""
        from apps.execution_gateway.main import _calculate_position_pnl

        position = Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl = _calculate_position_pnl(
            position,
            Decimal("155.00"),
            "real-time",
            datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        assert pnl.unrealized_pl == Decimal("50.00")  # 10 shares * $5 profit
        assert pnl.price_source == "real-time"

    def test_calculate_pnl_for_short_position_profit(self):
        """Test P&L calculation for profitable short position."""
        from apps.execution_gateway.main import _calculate_position_pnl

        position = Position(
            symbol="MSFT",
            qty=Decimal("-5"),
            avg_entry_price=Decimal("300.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl = _calculate_position_pnl(position, Decimal("295.00"), "database", None)

        assert pnl.unrealized_pl == Decimal("25.00")  # 5 shares * $5 profit (short)

    def test_calculate_pnl_for_long_position_loss(self):
        """Test P&L calculation for losing long position."""
        from apps.execution_gateway.main import _calculate_position_pnl

        position = Position(
            symbol="GOOGL",
            qty=Decimal("2"),
            avg_entry_price=Decimal("2800.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl = _calculate_position_pnl(position, Decimal("2750.00"), "fallback", None)

        assert pnl.unrealized_pl == Decimal("-100.00")  # 2 shares * -$50 loss


class TestResolveAndCalculatePnL:
    """Tests for price resolution and P&L calculation."""

    def test_uses_realtime_price_when_available(self):
        """Test uses real-time price when available in Redis."""
        from apps.execution_gateway.main import _resolve_and_calculate_pnl

        position = Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("151.00"),  # Database price
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        # Realtime price data is a tuple, not a dict
        realtime_price_data = (
            Decimal("152.50"),
            datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)

        assert pnl.price_source == "real-time"
        assert is_realtime is True

    def test_falls_back_to_database_price(self):
        """Test falls back to database price when Redis unavailable."""
        from apps.execution_gateway.main import _resolve_and_calculate_pnl

        position = Position(
            symbol="MSFT",
            qty=Decimal("5"),
            avg_entry_price=Decimal("300.00"),
            current_price=Decimal("302.00"),  # Database price
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        # No real-time data (None, None tuple)
        realtime_price_data = (None, None)

        pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)

        assert pnl.price_source == "database"
        assert is_realtime is False

    def test_falls_back_to_entry_price(self):
        """Test falls back to entry price when no current price available."""
        from apps.execution_gateway.main import _resolve_and_calculate_pnl

        position = Position(
            symbol="GOOGL",
            qty=Decimal("2"),
            avg_entry_price=Decimal("2800.00"),
            current_price=None,  # No database price
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        # No real-time data (None, None tuple)
        realtime_price_data = (None, None)

        pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)

        assert pnl.price_source == "fallback"
        assert is_realtime is False
