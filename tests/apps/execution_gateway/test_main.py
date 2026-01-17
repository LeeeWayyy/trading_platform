"""
Unit tests for Execution Gateway FastAPI application.

Tests cover:
- Root and health endpoints
- Order submission (DRY_RUN mode)
- Order retrieval by client_order_id
- Position listing with P&L
- Helper functions for price fetching and P&L calculation
"""

import importlib
import sys
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from starlette.requests import Request

# Import app at module level (will use real clients initially)
from apps.execution_gateway.main import app
from apps.execution_gateway.schemas import OrderDetail, Position


def _clear_registry() -> None:
    collectors = list(REGISTRY._collector_to_names)  # type: ignore[attr-defined]
    for collector in collectors:
        REGISTRY.unregister(collector)


@pytest.fixture()
def clean_registry():
    original_collectors = list(REGISTRY._collector_to_names)  # type: ignore[attr-defined]
    _clear_registry()
    yield
    _clear_registry()
    for collector in original_collectors:
        REGISTRY.register(collector)


def _reload_main(monkeypatch, clean_registry):  # noqa: ARG001 - fixture hook
    if "apps.execution_gateway.main" in sys.modules:
        return importlib.reload(sys.modules["apps.execution_gateway.main"])
    return importlib.import_module("apps.execution_gateway.main")


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
    from libs.trading.risk_management.position_reservation import ReleaseResult, ReservationResult

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
        mock_recovery_manager = Mock()
        mock_recovery_manager.needs_recovery.return_value = False

        with (
            patch.object(app.state.context, "db", mock_db),
            patch.object(app.state.context, "recovery_manager", mock_recovery_manager),
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
        mock_recovery_manager = Mock()
        mock_recovery_manager.needs_recovery.return_value = False

        with (
            patch.object(app.state.context, "db", mock_db),
            patch.object(app.state.context, "recovery_manager", mock_recovery_manager),
        ):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database_connected"] is False


class TestSubmitOrderEndpoint:
    """Tests for order submission endpoint."""

    def test_submit_order_dry_run_mode(
        self,
        test_client,
        mock_db,
        mock_kill_switch,
        mock_circuit_breaker,
        mock_position_reservation,
    ):
        """Test order submission in DRY_RUN mode logs order without broker submission."""
        # Mock: Order doesn't exist yet, then is retrievable after insert

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
        mock_db.insert_order.return_value = None
        mock_db.get_order_by_client_id.side_effect = [None, created_order]

        # Create mock recovery_manager with proper state object
        from apps.execution_gateway.recovery_manager import RecoveryState

        mock_state = RecoveryState()
        mock_state.kill_switch = mock_kill_switch
        mock_state.circuit_breaker = mock_circuit_breaker
        mock_state.position_reservation = mock_position_reservation
        mock_state.kill_switch_unavailable = False
        mock_state.circuit_breaker_unavailable = False
        mock_state.position_reservation_unavailable = False

        mock_recovery_manager = Mock()
        mock_recovery_manager.needs_recovery.return_value = False
        mock_recovery_manager.is_kill_switch_unavailable.return_value = False
        mock_recovery_manager.is_circuit_breaker_unavailable.return_value = False
        mock_recovery_manager.is_position_reservation_unavailable.return_value = False
        mock_recovery_manager._state = mock_state
        # Also set property accessors directly on the mock
        mock_recovery_manager.kill_switch = mock_kill_switch
        mock_recovery_manager.circuit_breaker = mock_circuit_breaker
        mock_recovery_manager.position_reservation = mock_position_reservation

        with (
            patch.object(app.state.context, "db", mock_db),
            patch.object(app.state.context, "recovery_manager", mock_recovery_manager),
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
        self,
        test_client,
        mock_db,
        mock_kill_switch,
        mock_circuit_breaker,
        mock_position_reservation,
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

        # Create mock recovery_manager with proper state object
        from apps.execution_gateway.recovery_manager import RecoveryState

        mock_state = RecoveryState()
        mock_state.kill_switch = mock_kill_switch
        mock_state.circuit_breaker = mock_circuit_breaker
        mock_state.position_reservation = mock_position_reservation
        mock_state.kill_switch_unavailable = False
        mock_state.circuit_breaker_unavailable = False
        mock_state.position_reservation_unavailable = False

        mock_recovery_manager = Mock()
        mock_recovery_manager.needs_recovery.return_value = False
        mock_recovery_manager.is_kill_switch_unavailable.return_value = False
        mock_recovery_manager.is_circuit_breaker_unavailable.return_value = False
        mock_recovery_manager.is_position_reservation_unavailable.return_value = False
        mock_recovery_manager._state = mock_state
        # Also set property accessors directly on the mock
        mock_recovery_manager.kill_switch = mock_kill_switch
        mock_recovery_manager.circuit_breaker = mock_circuit_breaker
        mock_recovery_manager.position_reservation = mock_position_reservation

        with (
            patch.object(app.state.context, "db", mock_db),
            patch.object(app.state.context, "recovery_manager", mock_recovery_manager),
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
        assert "already exists" in data["message"]

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

        with patch.object(app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/orders/test123")

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "test123"
        assert data["symbol"] == "AAPL"
        assert data["status"] == "filled"

    def test_get_order_not_found(self, test_client, mock_db):
        """Test retrieving non-existent order returns 404."""
        mock_db.get_order_by_client_id.return_value = None

        with patch.object(app.state.context, "db", mock_db):
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

        with patch.object(app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/positions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 2
        assert data["total_positions"] == 2
        assert Decimal(data["total_unrealized_pl"]) == Decimal("45.00")

    def test_get_positions_empty(self, test_client, mock_db):
        """Test getting positions when none exist returns empty list."""
        mock_db.get_all_positions.return_value = []

        with patch.object(app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/positions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 0
        assert data["total_positions"] == 0


class TestBatchFetchRealtimePrices:
    """Tests for batch real-time price fetching helper."""

    def test_batch_fetch_with_valid_data(self, mock_redis):
        """Test batch fetching prices from Redis with valid data."""
        from apps.execution_gateway.services.order_helpers import (
            batch_fetch_realtime_prices_from_redis,
        )

        # Mock Redis MGET response (strings, not bytes, due to decode_responses=True)
        mock_redis.mget.return_value = [
            '{"mid": "152.50", "timestamp": "2024-10-19T12:00:00+00:00"}',
            '{"mid": "295.00", "timestamp": "2024-10-19T12:00:00+00:00"}',
        ]

        result = batch_fetch_realtime_prices_from_redis(["AAPL", "MSFT"], mock_redis)

        assert len(result) == 2
        assert result["AAPL"][0] == Decimal("152.50")
        assert result["MSFT"][0] == Decimal("295.00")

    def test_batch_fetch_redis_unavailable(self):
        """Test batch fetch returns dict with None values when Redis is unavailable."""
        from apps.execution_gateway.services.order_helpers import (
            batch_fetch_realtime_prices_from_redis,
        )

        result = batch_fetch_realtime_prices_from_redis(["AAPL"], None)

        assert result == {"AAPL": (None, None)}

    def test_batch_fetch_empty_symbols(self, mock_redis):
        """Test batch fetch with empty symbol list returns empty dict."""
        from apps.execution_gateway.services.order_helpers import (
            batch_fetch_realtime_prices_from_redis,
        )

        result = batch_fetch_realtime_prices_from_redis([], mock_redis)

        assert result == {}


class TestCalculatePositionPnL:
    """Tests for position P&L calculation helper."""

    def test_calculate_pnl_for_long_position_profit(self):
        """Test P&L calculation for profitable long position."""
        from apps.execution_gateway.services.pnl_calculator import calculate_position_pnl

        position = Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl = calculate_position_pnl(
            position,
            Decimal("155.00"),
            "real-time",
            datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        assert pnl.unrealized_pl == Decimal("50.00")  # 10 shares * $5 profit
        assert pnl.price_source == "real-time"

    def test_calculate_pnl_for_short_position_profit(self):
        """Test P&L calculation for profitable short position."""
        from apps.execution_gateway.services.pnl_calculator import calculate_position_pnl

        position = Position(
            symbol="MSFT",
            qty=Decimal("-5"),
            avg_entry_price=Decimal("300.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl = calculate_position_pnl(position, Decimal("295.00"), "database", None)

        assert pnl.unrealized_pl == Decimal("25.00")  # 5 shares * $5 profit (short)

    def test_calculate_pnl_for_long_position_loss(self):
        """Test P&L calculation for losing long position."""
        from apps.execution_gateway.services.pnl_calculator import calculate_position_pnl

        position = Position(
            symbol="GOOGL",
            qty=Decimal("2"),
            avg_entry_price=Decimal("2800.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
        )

        pnl = calculate_position_pnl(position, Decimal("2750.00"), "fallback", None)

        assert pnl.unrealized_pl == Decimal("-100.00")  # 2 shares * -$50 loss


class TestResolveAndCalculatePnL:
    """Tests for price resolution and P&L calculation."""

    def test_uses_realtime_price_when_available(self):
        """Test uses real-time price when available in Redis."""
        from apps.execution_gateway.services.pnl_calculator import resolve_and_calculate_pnl

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

        pnl, is_realtime = resolve_and_calculate_pnl(position, realtime_price_data)

        assert pnl.price_source == "real-time"
        assert is_realtime is True

    def test_falls_back_to_database_price(self):
        """Test falls back to database price when Redis unavailable."""
        from apps.execution_gateway.services.pnl_calculator import resolve_and_calculate_pnl

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

        pnl, is_realtime = resolve_and_calculate_pnl(position, realtime_price_data)

        assert pnl.price_source == "database"
        assert is_realtime is False

    def test_falls_back_to_entry_price(self):
        """Test falls back to entry price when no current price available."""
        from apps.execution_gateway.services.pnl_calculator import resolve_and_calculate_pnl

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

        pnl, is_realtime = resolve_and_calculate_pnl(position, realtime_price_data)

        assert pnl.price_source == "fallback"
        assert is_realtime is False


def test_env_helpers_cover_invalid_branches(monkeypatch):
    from apps.execution_gateway import main

    monkeypatch.setenv("BAD_FLOAT", "not-a-float")
    monkeypatch.setenv("BAD_DEC", "not-a-decimal")
    monkeypatch.setenv("MAX_SLICE_PCT_OF_ADV", "0.01")

    assert main._get_float_env("NON_EXISTENT", 1.5) == 1.5
    assert main._get_float_env("MAX_SLICE_PCT_OF_ADV", 0.1) == main.MAX_SLICE_PCT_OF_ADV
    assert main._get_float_env("BAD_FLOAT", 2.0) == 2.0

    assert main._get_decimal_env("NON_EXISTENT_DEC", Decimal("1.2")) == Decimal("1.2")
    assert main._get_decimal_env("BAD_DEC", Decimal("3.4")) == Decimal("3.4")


def test_module_level_validations(monkeypatch, caplog, clean_registry):
    monkeypatch.setenv("MAX_SLICE_PCT_OF_ADV", "0")
    monkeypatch.setenv("FAT_FINGER_MAX_NOTIONAL", "0")
    monkeypatch.setenv("FAT_FINGER_MAX_QTY", "-1")
    monkeypatch.setenv("FAT_FINGER_MAX_ADV_PCT", "2")
    monkeypatch.setenv("FAT_FINGER_MAX_PRICE_AGE_SECONDS", "0")

    with caplog.at_level("WARNING"):
        main = _reload_main(monkeypatch, clean_registry)

    assert main.MAX_SLICE_PCT_OF_ADV == 0.01
    assert main.FAT_FINGER_MAX_NOTIONAL is None
    assert main.FAT_FINGER_MAX_QTY is None
    assert main.FAT_FINGER_MAX_ADV_PCT is None
    assert main.FAT_FINGER_MAX_PRICE_AGE_SECONDS == main.FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT
    assert "MAX_SLICE_PCT_OF_ADV must be > 0" in caplog.text


def test_module_level_invalid_int_parsing(monkeypatch, clean_registry):
    monkeypatch.setenv("FAT_FINGER_MAX_QTY", "bad")
    monkeypatch.setenv("FAT_FINGER_MAX_PRICE_AGE_SECONDS", "bad")

    main = _reload_main(monkeypatch, clean_registry)
    assert main.FAT_FINGER_MAX_QTY == main.FAT_FINGER_MAX_QTY_DEFAULT
    assert main.FAT_FINGER_MAX_PRICE_AGE_SECONDS == main.FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT


def test_build_metrics_contains_expected_keys():
    from apps.execution_gateway import main

    metrics = main._build_metrics()
    assert "orders_total" in metrics
    assert "pnl_dollars" in metrics
    assert "alpaca_api_requests_total" in metrics


@pytest.mark.asyncio()
async def test_exception_handlers():
    from apps.execution_gateway import main

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
        "app": main.app,
    }
    request = Request(scope)

    validation_resp = await main.validation_exception_handler(request, Exception("boom"))
    assert validation_resp.status_code == 422

    permission_resp = await main.permission_exception_handler(request, PermissionError("nope"))
    assert permission_resp.status_code == 403

    alpaca_validation = await main.alpaca_validation_handler(request, Exception("bad"))
    assert alpaca_validation.status_code == 400

    alpaca_rejection = await main.alpaca_rejection_handler(request, Exception("rejected"))
    assert alpaca_rejection.status_code == 422

    alpaca_connection = await main.alpaca_connection_handler(request, Exception("down"))
    assert alpaca_connection.status_code == 503


@pytest.mark.asyncio()
async def test_lifespan_sets_globals(monkeypatch):
    from apps.execution_gateway import main
    from apps.execution_gateway.lifespan import LifespanResources

    async def _fake_startup(app, settings, metrics):
        return LifespanResources(
            db_client=SimpleNamespace(name="db"),
            redis_client=None,
            alpaca_client=None,
            webhook_secret="secret",
            liquidity_service=None,
            recovery_manager=SimpleNamespace(),
            reconciliation_service=None,
            reconciliation_task=None,
        )

    async def _fake_shutdown(resources):
        return None

    monkeypatch.setattr(main, "startup_execution_gateway", _fake_startup)
    monkeypatch.setattr(main, "shutdown_execution_gateway", _fake_shutdown)

    async with main.lifespan(main.app):
        assert main.db_client.name == "db"
        assert main.WEBHOOK_SECRET == "secret"
