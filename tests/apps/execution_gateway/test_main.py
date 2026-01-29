"""
Unit tests for Execution Gateway FastAPI application.

Tests cover:
- Root and health endpoints
- Order submission (DRY_RUN mode)
- Order retrieval by client_order_id
- Position listing with P&L
- Helper functions for price fetching and P&L calculation
"""

import asyncio
import importlib
import json
import runpy
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from pydantic import BaseModel, ValidationError
from starlette.requests import Request

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.dependencies import get_config, get_context, get_metrics

# Import app at module level (will use real clients initially)
from apps.execution_gateway.main import app
from apps.execution_gateway.routes.orders import order_read_auth, order_submit_auth
from apps.execution_gateway.routes.positions import order_read_auth as positions_read_auth
from apps.execution_gateway.schemas import OrderDetail, Position
from libs.core.common.api_auth_dependency import AuthContext
from libs.platform.web_console_auth.gateway_auth import AuthenticatedUser
from libs.platform.web_console_auth.permissions import Role


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


def _make_request(path: str, body: bytes = b"{}") -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


@pytest.fixture()
def test_client():
    """Create FastAPI test client."""
    return TestClient(app)


@pytest.fixture()
def mock_auth_context():
    """Create a mock AuthContext for dependency injection."""
    user = AuthenticatedUser(
        user_id="test-user",
        role=Role.OPERATOR,
        strategies=["alpha_baseline"],
        session_version=1,
        request_id="test-request-id",
    )
    return AuthContext(
        user=user,
        internal_claims=None,
        auth_type="jwt",
        is_authenticated=True,
    )


@pytest.fixture()
def mock_context(
    mock_db, mock_redis, mock_kill_switch, mock_circuit_breaker, mock_position_reservation
):
    """Create a mock AppContext for dependency injection."""
    from decimal import Decimal

    from apps.execution_gateway.fat_finger_validator import FatFingerResult
    from apps.execution_gateway.schemas import FatFingerThresholds
    from libs.trading.risk_management import RiskConfig

    mock_recovery_manager = Mock()
    mock_recovery_manager.needs_recovery.return_value = False
    mock_recovery_manager.kill_switch = mock_kill_switch
    mock_recovery_manager.circuit_breaker = mock_circuit_breaker
    mock_recovery_manager.position_reservation = mock_position_reservation
    mock_recovery_manager.is_kill_switch_unavailable.return_value = False
    mock_recovery_manager.is_circuit_breaker_unavailable.return_value = False
    mock_recovery_manager.is_position_reservation_unavailable.return_value = False

    # Mock reconciliation service
    mock_reconciliation_service = Mock()
    mock_reconciliation_service.is_startup_complete.return_value = True
    mock_reconciliation_service.startup_timed_out.return_value = False
    mock_reconciliation_service.override_active.return_value = False

    # Mock fat finger validator with a passing result
    mock_fat_finger_validator = Mock()
    thresholds = FatFingerThresholds(
        max_notional=Decimal("100000"),
        max_qty=10000,
        max_adv_pct=Decimal("0.05"),
    )
    mock_fat_finger_validator.validate.return_value = FatFingerResult(
        breached=False,
        breaches=(),
        thresholds=thresholds,
        notional=Decimal("1500"),  # 10 * 150
        adv=1000000,
        adv_pct=Decimal("0.00001"),
        price=Decimal("150"),
    )
    mock_twap_slicer = Mock()

    ctx = Mock(spec=AppContext)
    ctx.db = mock_db
    ctx.redis = mock_redis
    ctx.alpaca = None  # DRY_RUN mode
    ctx.recovery_manager = mock_recovery_manager
    ctx.reconciliation_service = mock_reconciliation_service
    ctx.liquidity_service = None
    ctx.risk_config = RiskConfig()
    ctx.fat_finger_validator = mock_fat_finger_validator
    ctx.twap_slicer = mock_twap_slicer
    ctx.webhook_secret = "test-webhook-secret"
    ctx.metrics = _create_mock_metrics()
    # Add position tracking attributes needed by get_positions endpoint
    ctx.position_metrics_lock = asyncio.Lock()
    ctx.tracked_position_symbols = set()
    return ctx


def _create_mock_metrics() -> dict[str, Any]:
    """Create mock Prometheus metrics."""
    mock_counter = Mock()
    mock_counter.labels.return_value = mock_counter
    mock_gauge = Mock()
    return {
        "orders_total": mock_counter,
        "orders_submitted_total": mock_counter,
        "order_errors_total": mock_counter,
        "order_latency_seconds": mock_counter,
        "database_connection_status": mock_gauge,
        "redis_connection_status": mock_gauge,
        "alpaca_connection_status": mock_gauge,
        "alpaca_api_requests_total": mock_counter,
    }


@pytest.fixture()
def mock_config():
    """Create a mock ExecutionGatewayConfig."""
    from decimal import Decimal

    config = Mock(spec=ExecutionGatewayConfig)
    config.dry_run = True
    config.environment = "test"
    config.strategy_id = "alpha_baseline"
    config.circuit_breaker_enabled = True
    config.liquidity_check_enabled = True
    config.alpaca_base_url = "https://paper-api.alpaca.markets"
    config.alpaca_paper = True
    config.alpaca_data_feed = None
    # Fat finger validation settings
    config.fat_finger_max_notional = Decimal("100000")
    config.fat_finger_max_qty = 10000
    config.fat_finger_max_adv_pct = Decimal("0.05")
    config.fat_finger_max_price_age_seconds = 30
    config.max_slice_pct_of_adv = 0.01
    # Redis settings
    config.redis_host = "localhost"
    config.redis_port = 6379
    config.redis_db = 0
    # Performance settings
    config.performance_cache_ttl = 300
    config.max_performance_days = 90
    config.feature_performance_dashboard = False
    return config


@pytest.fixture()
def test_client_with_context(mock_context, mock_config) -> Generator[TestClient, None, None]:
    """Create test client with mocked dependencies."""

    def override_context():
        return mock_context

    def override_config():
        return mock_config

    def override_metrics():
        return mock_context.metrics

    app.dependency_overrides[get_context] = override_context
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_metrics] = override_metrics

    yield TestClient(app)

    # Cleanup
    app.dependency_overrides.clear()


@pytest.fixture()
def test_client_with_auth(
    mock_context, mock_config, mock_auth_context
) -> Generator[TestClient, None, None]:
    """Create test client with mocked dependencies and auth bypass."""

    def override_context():
        return mock_context

    def override_config():
        return mock_config

    def override_metrics():
        return mock_context.metrics

    def override_auth():
        return mock_auth_context

    app.dependency_overrides[get_context] = override_context
    app.dependency_overrides[get_config] = override_config
    app.dependency_overrides[get_metrics] = override_metrics
    # Override auth for orders and positions
    app.dependency_overrides[order_submit_auth] = override_auth
    app.dependency_overrides[order_read_auth] = override_auth
    app.dependency_overrides[positions_read_auth] = override_auth

    yield TestClient(app)

    # Cleanup
    app.dependency_overrides.clear()


@pytest.fixture()
def mock_db():
    """Create a mock DatabaseClient."""
    return Mock()


@pytest.fixture()
def mock_redis():
    """Create a mock RedisClient."""
    mock = Mock()
    # mget should return a list (even if empty)
    mock.mget.return_value = []
    # get should return None by default
    mock.get.return_value = None
    mock.health_check.return_value = True
    return mock


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

    def test_health_check_healthy_in_dry_run(self, test_client_with_context, mock_context):
        """Test health check returns healthy when database is up (DRY_RUN mode)."""
        mock_context.db.check_connection.return_value = True

        response = test_client_with_context.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database_connected"] is True
        assert data["dry_run"] is True

    def test_health_check_unhealthy_when_db_down(self, test_client_with_context, mock_context):
        """Test health check returns unhealthy when database is down."""
        mock_context.db.check_connection.return_value = False

        response = test_client_with_context.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database_connected"] is False


class TestSubmitOrderEndpoint:
    """Tests for order submission endpoint."""

    def test_submit_order_dry_run_mode(
        self,
        test_client_with_auth,
        mock_context,
        mock_kill_switch,
        mock_circuit_breaker,
        mock_position_reservation,
    ):
        """Test order submission in DRY_RUN mode logs order without broker submission."""
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

        # Mock: Order doesn't exist yet, then is retrievable after insert
        mock_context.db.insert_order.return_value = None
        mock_context.db.get_order_by_client_id.side_effect = [None, created_order]

        # Configure recovery manager for the test
        mock_context.recovery_manager.is_kill_switch_unavailable.return_value = False
        mock_context.recovery_manager.is_circuit_breaker_unavailable.return_value = False
        mock_context.recovery_manager.is_position_reservation_unavailable.return_value = False

        response = test_client_with_auth.post(
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
        test_client_with_auth,
        mock_context,
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
        mock_context.db.get_order_by_client_id.return_value = existing_order

        # Configure recovery manager for the test
        mock_context.recovery_manager.is_kill_switch_unavailable.return_value = False
        mock_context.recovery_manager.is_circuit_breaker_unavailable.return_value = False
        mock_context.recovery_manager.is_position_reservation_unavailable.return_value = False

        response = test_client_with_auth.post(
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

    def test_get_order_found(self, test_client_with_auth, mock_context):
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
        mock_context.db.get_order_by_client_id.return_value = order

        response = test_client_with_auth.get("/api/v1/orders/test123")

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "test123"
        assert data["symbol"] == "AAPL"
        assert data["status"] == "filled"

    def test_get_order_not_found(self, test_client_with_auth, mock_context):
        """Test retrieving non-existent order returns 404."""
        mock_context.db.get_order_by_client_id.return_value = None

        response = test_client_with_auth.get("/api/v1/orders/nonexistent123")

        assert response.status_code == 404


class TestGetPositionsEndpoint:
    """Tests for get positions endpoint."""

    def test_get_positions_returns_list(self, test_client_with_auth, mock_context):
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
        mock_context.db.get_all_positions.return_value = positions

        response = test_client_with_auth.get("/api/v1/positions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["positions"]) == 2
        assert data["total_positions"] == 2
        assert Decimal(data["total_unrealized_pl"]) == Decimal("45.00")

    def test_get_positions_empty(self, test_client_with_auth, mock_context):
        """Test getting positions when none exist returns empty list."""
        mock_context.db.get_all_positions.return_value = []

        response = test_client_with_auth.get("/api/v1/positions")

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


@pytest.mark.asyncio()
async def test_request_validation_exception_handler_twap_preview():
    from apps.execution_gateway import main

    exc = RequestValidationError([{"loc": ("body", "qty"), "msg": "field required"}])
    request = _make_request("/api/v1/orders/twap-preview")

    response = await main.request_validation_exception_handler_twap(request, exc)
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["error"] == "validation_error"
    assert "qty" in payload["errors"][0]


@pytest.mark.asyncio()
async def test_request_validation_exception_handler_twap_submit():
    from apps.execution_gateway import main

    exc = RequestValidationError([{"loc": ("body", "qty"), "msg": "field required"}])
    request = _make_request("/api/v1/orders", body=b'{"execution_style": "twap"}')

    response = await main.request_validation_exception_handler_twap(request, exc)
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["error"] == "validation_error"
    assert "qty" in payload["errors"][0]


@pytest.mark.asyncio()
async def test_request_validation_exception_handler_non_twap_falls_back():
    from apps.execution_gateway import main

    exc = RequestValidationError([{"loc": ("body", "qty"), "msg": "field required"}])
    request = _make_request("/api/v1/orders", body=b'{"execution_style": "instant"}')

    response = await main.request_validation_exception_handler_twap(request, exc)
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert "detail" in payload


@pytest.mark.asyncio()
async def test_request_validation_exception_handler_invalid_json_falls_back():
    from apps.execution_gateway import main

    exc = RequestValidationError([{"loc": ("body", "qty"), "msg": "field required"}])
    request = _make_request("/api/v1/orders", body=b"{")

    response = await main.request_validation_exception_handler_twap(request, exc)
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert "detail" in payload


@pytest.mark.asyncio()
async def test_twap_validation_exception_handler():
    from apps.execution_gateway import main

    request = _make_request("/api/v1/orders/twap-preview")
    response = await main.twap_validation_exception_handler(
        request, main.TWAPValidationException(["bad"])
    )
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["error"] == "validation_error"


@pytest.mark.asyncio()
async def test_validation_exception_handler():
    from apps.execution_gateway import main

    class DummyModel(BaseModel):
        qty: int

    try:
        DummyModel(qty="bad")
    except ValidationError as exc:
        request = _make_request("/api/v1/orders")
        response = await main.validation_exception_handler(request, exc)
        payload = json.loads(response.body)
        assert response.status_code == 422
        assert payload["error"] == "Validation error"


def test_main_entrypoint_invokes_uvicorn(monkeypatch):
    mock_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=mock_run))

    runpy.run_module("apps.execution_gateway.main", run_name="__main__")

    assert mock_run.called
