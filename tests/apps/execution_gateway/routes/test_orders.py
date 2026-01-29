"""Tests for order routes in apps/execution_gateway/routes/orders.py."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from psycopg.errors import LockNotAvailable

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.fat_finger_validator import (
    FatFingerBreach,
    FatFingerThresholds,
    FatFingerValidator,
)
from apps.execution_gateway.routes import orders
from apps.execution_gateway.schemas import OrderDetail
from libs.core.common.api_auth_dependency import AuthContext
from libs.trading.risk_management import RiskConfig


class _ReservationResult:
    def __init__(
        self,
        success: bool,
        token: str | None,
        reason: str = "",
        previous_position: Decimal = Decimal("0"),
        new_position: Decimal = Decimal("0"),
    ) -> None:
        self.success = success
        self.token = token
        self.reason = reason
        self.previous_position = previous_position
        self.new_position = new_position


def _mock_auth_context() -> AuthContext:
    return AuthContext(
        user={"role": "operator", "strategies": ["alpha_baseline"], "user_id": "test-user"},
        internal_claims=None,
        auth_type="test",
        is_authenticated=True,
    )


def _make_order_detail(client_order_id: str, status: str = "dry_run") -> OrderDetail:
    now = datetime.now(UTC)
    return OrderDetail(
        client_order_id=client_order_id,
        strategy_id="alpha_baseline",
        symbol="AAPL",
        side="buy",
        qty=10,
        order_type="market",
        limit_price=None,
        stop_price=None,
        time_in_force="day",
        status=status,
        broker_order_id=None,
        error_message=None,
        retry_count=0,
        created_at=now,
        updated_at=now,
        submitted_at=None,
        filled_at=None,
        filled_qty=Decimal("0"),
        filled_avg_price=None,
        metadata={},
    )


def _build_test_app(ctx: Any, config: Any) -> TestClient:
    app = FastAPI()
    app.include_router(orders.router)

    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[orders.order_submit_auth] = _mock_auth_context
    app.dependency_overrides[orders.order_cancel_auth] = _mock_auth_context
    app.dependency_overrides[orders.order_modify_auth] = _mock_auth_context
    app.dependency_overrides[orders.order_read_auth] = _mock_auth_context
    app.dependency_overrides[orders.order_preview_auth] = _mock_auth_context
    app.dependency_overrides[orders.order_submit_rl] = lambda: 1
    app.dependency_overrides[orders.order_cancel_rl] = lambda: 1
    app.dependency_overrides[orders.order_modify_rl] = lambda: 1
    app.dependency_overrides[orders.order_preview_rl] = lambda: 1

    return TestClient(app)


class TestSubmitOrder:
    def test_submit_order_dry_run_success(self) -> None:
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        order_detail = _make_order_detail("client-123")
        db.get_order_by_client_id.side_effect = [None, order_detail]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )

        config = create_test_config(dry_run=True, strategy_id="alpha_baseline")
        client = _build_test_app(ctx, config)

        order_payload = {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
        }

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post("/api/v1/orders", json=order_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "client-123"
        assert data["status"] == "dry_run"
        assert data["message"] == "Order logged (DRY_RUN mode)"

        reservation.release.assert_called_once_with("AAPL", "token-1")
        db.create_order.assert_called_once()

    def test_submit_order_rejects_twap(self) -> None:
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        db.get_order_by_client_id.return_value = None

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())
        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True, strategy_id="alpha_baseline")
        client = _build_test_app(ctx, config)

        order_payload = {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "order_type": "market",
            "time_in_force": "day",
            "execution_style": "twap",
            "twap_duration_minutes": 10,
            "twap_interval_seconds": 60,
        }

        response = client.post("/api/v1/orders", json=order_payload)
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"] == "twap_not_supported"
        reservation.reserve.assert_not_called()

    def test_submit_order_idempotent_returns_existing(self) -> None:
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-2", new_position=Decimal("10")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        order_detail = _make_order_detail("client-456", status="pending_new")
        db.get_order_by_client_id.return_value = order_detail

        fat_finger_validator = FatFingerValidator(FatFingerThresholds())

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True, strategy_id="alpha_baseline")
        client = _build_test_app(ctx, config)

        order_payload = {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day",
        }

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-456"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post("/api/v1/orders", json=order_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "client-456"
        assert data["status"] == "pending_new"
        assert data["message"] == "Order already exists (idempotent retry)"

        reservation.release.assert_called_once_with("AAPL", "token-2")
        db.create_order.assert_not_called()


class TestCancelAndGetOrder:
    def test_cancel_order_not_found(self) -> None:
        db = MagicMock()
        db.get_order_by_client_id.return_value = None

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post("/api/v1/orders/missing-order/cancel")

        assert response.status_code == 404
        assert response.json()["detail"] == "Order not found: missing-order"

    def test_get_order_success(self) -> None:
        order_detail = _make_order_detail("client-789", status="new")
        db = MagicMock()
        db.get_order_by_client_id.return_value = order_detail

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.get("/api/v1/orders/client-789")

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "client-789"
        assert data["status"] == "new"

    def test_get_order_not_found(self) -> None:
        """Test GET order returns 404 when not found."""
        db = MagicMock()
        db.get_order_by_client_id.return_value = None

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.get("/api/v1/orders/not-found-id")

        assert response.status_code == 404
        assert "Order not found" in response.json()["detail"]


class TestSafetyGates:
    """Tests for safety gate blocking."""

    def test_kill_switch_unavailable_returns_503(self) -> None:
        """Test 503 when kill-switch is unavailable."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = True
        recovery_manager.kill_switch = None

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Kill-switch unavailable" in str(response.json())

    def test_circuit_breaker_unavailable_returns_503(self) -> None:
        """Test 503 when circuit-breaker is unavailable."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_circuit_breaker_unavailable.return_value = True
        recovery_manager.circuit_breaker = None

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Circuit-breaker" in str(response.json())

    def test_position_reservation_unavailable_returns_503(self) -> None:
        """Test 503 when position-reservation is unavailable."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.is_position_reservation_unavailable.return_value = True
        recovery_manager.position_reservation = None

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Position-reservation" in str(response.json())

    def test_kill_switch_engaged_returns_503(self) -> None:
        """Test 503 when kill-switch is engaged."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = True
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Kill-switch engaged" in response.json()["detail"]

    def test_circuit_breaker_tripped_returns_503(self) -> None:
        """Test 503 when circuit-breaker is tripped."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = True
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Circuit breaker tripped" in response.json()["detail"]


class TestQuarantineCheck:
    """Tests for quarantine check functionality."""

    def test_quarantine_redis_unavailable_returns_503(self) -> None:
        """Test 503 when Redis is unavailable for quarantine check."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()

        # No redis
        ctx = create_mock_context(recovery_manager=recovery_manager, redis=None)
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Quarantine check unavailable" in str(response.json())

    def test_quarantine_symbol_blocked_returns_503(self) -> None:
        """Test 503 when symbol is quarantined."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [b"quarantined", None]

        ctx = create_mock_context(recovery_manager=recovery_manager, redis=mock_redis)
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "quarantined" in str(response.json()).lower()

    def test_quarantine_redis_error_returns_503(self) -> None:
        """Test 503 when Redis raises error during quarantine check."""
        from redis.exceptions import RedisError

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()

        mock_redis = MagicMock()
        mock_redis.mget.side_effect = RedisError("Connection failed")

        ctx = create_mock_context(recovery_manager=recovery_manager, redis=mock_redis)
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503

    def test_quarantine_connection_error_returns_503(self) -> None:
        """Test 503 when Redis connection error during quarantine check."""
        import redis.exceptions

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()

        mock_redis = MagicMock()
        mock_redis.mget.side_effect = redis.exceptions.ConnectionError("Connection failed")

        ctx = create_mock_context(recovery_manager=recovery_manager, redis=mock_redis)
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503

    def test_quarantine_type_error_returns_503(self) -> None:
        """Test 503 when TypeError during quarantine check."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()

        mock_redis = MagicMock()
        mock_redis.mget.side_effect = TypeError("Type error")

        ctx = create_mock_context(recovery_manager=recovery_manager, redis=mock_redis)
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503


class TestPositionReservation:
    """Tests for position reservation failures."""

    def test_position_reservation_failure_returns_422(self) -> None:
        """Test 422 when position limit exceeded."""
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=False,
            token=None,
            reason="Position limit exceeded",
            previous_position=Decimal("100"),
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("100")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 422
        assert "Position limit exceeded" in str(response.json())

    def test_db_position_lookup_failure_returns_503(self) -> None:
        """Test 503 when DB position lookup fails."""
        reservation = MagicMock()

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.side_effect = Exception("Database error")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Position lookup unavailable" in response.json()["detail"]

    def test_reservation_token_missing_returns_500(self) -> None:
        """Test 500 when reservation returns success but no token."""
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True,
            token=None,  # Missing token!
            new_position=Decimal("10"),
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 500
        assert "token" in str(response.json()).lower()


class TestFatFingerValidation:
    """Tests for fat-finger validation failures."""

    def test_fat_finger_breach_returns_400(self) -> None:
        """Test 400 when fat-finger validation fails."""
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("1000000")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        db.get_order_by_client_id.return_value = None

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000"),  # Very low limit
                max_qty=10,  # Very low limit
                max_adv_pct=Decimal("0.001"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-fat"
            resolve_context.return_value = (Decimal("500"), 10000)  # High price, low ADV

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 1000000,  # Very high qty
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 400
        assert "fat-finger" in response.json()["detail"].lower()


class TestCancelOrder:
    """Tests for order cancellation."""

    def test_cancel_order_terminal_state_returns_success(self) -> None:
        """Test canceling an order already in terminal state returns success."""
        order_detail = _make_order_detail("client-terminal", status="filled")
        db = MagicMock()
        db.get_order_by_client_id.return_value = order_detail

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post("/api/v1/orders/client-terminal/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "filled"
        assert data["message"] == "Order already in terminal state"

    def test_cancel_order_dry_run_success(self) -> None:
        """Test canceling order in dry-run mode."""
        order_detail = _make_order_detail("client-cancel", status="pending_new")
        order_detail.broker_order_id = "broker-123"
        db = MagicMock()
        db.get_order_by_client_id.return_value = order_detail
        updated_order = _make_order_detail("client-cancel", status="canceled")
        db.update_order_status_cas.return_value = updated_order

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post("/api/v1/orders/client-cancel/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Order canceled"

    def test_cancel_order_live_mode_success(self) -> None:
        """Test canceling order in live mode calls Alpaca."""
        order_detail = _make_order_detail("client-live-cancel", status="pending_new")
        order_detail.broker_order_id = "broker-456"
        db = MagicMock()
        db.get_order_by_client_id.return_value = order_detail
        updated_order = _make_order_detail("client-live-cancel", status="canceled")
        db.update_order_status_cas.return_value = updated_order

        alpaca = MagicMock()
        alpaca.cancel_order.return_value = None

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            alpaca=alpaca,
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post("/api/v1/orders/client-live-cancel/cancel")

        assert response.status_code == 200
        alpaca.cancel_order.assert_called_once_with("broker-456")

    def test_cancel_order_alpaca_unavailable_returns_503(self) -> None:
        """Test 503 when Alpaca is unavailable for cancel."""
        order_detail = _make_order_detail("client-no-alpaca", status="pending_new")
        order_detail.broker_order_id = "broker-789"
        db = MagicMock()
        db.get_order_by_client_id.return_value = order_detail

        recovery_manager = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.position_reservation = MagicMock()

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            alpaca=None,
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post("/api/v1/orders/client-no-alpaca/cancel")

        assert response.status_code == 503
        assert "Alpaca client not initialized" in response.json()["detail"]


class TestReconciliationGating:
    """Tests for reconciliation gating during startup."""

    def _create_recovery_manager(self):
        """Create a mock recovery manager with all gates passing."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()
        recovery_manager.position_reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )
        return recovery_manager

    def test_reconciliation_not_ready_alpaca_unavailable_returns_503(self) -> None:
        """Test 503 when reconciliation not ready and Alpaca unavailable."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=None,  # No Alpaca
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Broker unavailable" in str(response.json())

    def test_reconciliation_reduce_only_order_allowed(self) -> None:
        """Test reduce-only order allowed during reconciliation gating."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        alpaca = MagicMock()
        # We have a long position of 100 shares
        alpaca.get_open_position.return_value = {"qty": 100}
        alpaca.get_orders.return_value = []

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("100")
        db.get_order_by_client_id.side_effect = [None, _make_order_detail("client-reduce")]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-reduce"
            resolve_context.return_value = (Decimal("100"), 1000000)

            # Sell order to reduce long position (reduce-only)
            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "sell",
                    "qty": 50,  # Less than position, reduce-only
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200

    def test_reconciliation_position_increasing_blocked(self) -> None:
        """Test position-increasing order blocked during reconciliation gating."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        alpaca = MagicMock()
        # No position
        alpaca.get_open_position.return_value = None
        alpaca.get_orders.return_value = []

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=alpaca,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Reconciliation in progress" in str(response.json())

    def test_reconciliation_startup_timeout_reduce_only_with_pending_orders(self) -> None:
        """Startup timeout logs but still allows reduce-only orders with pending info."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = True
        recon_service.startup_elapsed_seconds.return_value = 3600

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        alpaca = MagicMock()
        alpaca.get_open_position.return_value = {"qty": 100}
        alpaca.get_orders.return_value = [
            {"side": "sell", "qty": 30, "filled_qty": 0},
            {"side": "buy", "qty": 10, "filled_qty": 0},
        ]
        alpaca.submit_order.return_value = {
            "id": "broker-1",
            "status": "accepted",
            "client_order_id": "client-reduce",
        }

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("100")
        db.get_order_by_client_id.side_effect = [None, _make_order_detail("client-reduce")]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-reduce"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "sell",
                    "qty": 50,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200

    def test_reconciliation_open_orders_fetch_error_allows_reduce_only(self) -> None:
        """Open orders fetch error should still allow reduce-only orders."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        alpaca = MagicMock()
        alpaca.get_open_position.return_value = {"qty": 100}
        alpaca.get_orders.side_effect = RuntimeError("orders fetch failed")
        alpaca.submit_order.return_value = {
            "id": "broker-2",
            "status": "accepted",
            "client_order_id": "client-reduce-2",
        }

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("100")
        db.get_order_by_client_id.side_effect = [None, _make_order_detail("client-reduce-2")]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-reduce-2"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "sell",
                    "qty": 50,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200

    def test_reconciliation_override_active_allows_order(self) -> None:
        """Test order allowed when reconciliation override is active."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = True
        recon_service.override_context.return_value = {"reason": "manual"}

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        db.get_order_by_client_id.side_effect = [None, _make_order_detail("client-override")]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-override"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200


class TestBrokerSubmission:
    """Tests for live broker submission paths."""

    def _create_base_mocks(self):
        """Create base mocks for broker submission tests."""
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = True

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        db.get_order_by_client_id.side_effect = [None, _make_order_detail("client-123")]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        return {
            "recovery_manager": recovery_manager,
            "reservation": reservation,
            "recon_service": recon_service,
            "mock_redis": mock_redis,
            "db": db,
            "fat_finger_validator": fat_finger_validator,
        }

    def test_alpaca_unavailable_returns_503(self) -> None:
        """Test 503 when Alpaca client is not initialized."""
        mocks = self._create_base_mocks()

        ctx = create_mock_context(
            db=mocks["db"],
            recovery_manager=mocks["recovery_manager"],
            reconciliation_service=mocks["recon_service"],
            redis=mocks["mock_redis"],
            alpaca=None,
            risk_config=RiskConfig(),
            fat_finger_validator=mocks["fat_finger_validator"],
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 503
        assert "Alpaca client not initialized" in response.json()["detail"]

    def test_alpaca_validation_error_returns_400(self) -> None:
        """Test 400 when Alpaca returns validation error."""
        from apps.execution_gateway.alpaca_client import AlpacaValidationError

        mocks = self._create_base_mocks()

        alpaca = MagicMock()
        alpaca.submit_order.side_effect = AlpacaValidationError("Invalid symbol")

        ctx = create_mock_context(
            db=mocks["db"],
            recovery_manager=mocks["recovery_manager"],
            reconciliation_service=mocks["recon_service"],
            redis=mocks["mock_redis"],
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=mocks["fat_finger_validator"],
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 400
        assert "validation failed" in response.json()["detail"].lower()

    def test_alpaca_value_error_returns_422(self) -> None:
        """Test 422 when Alpaca client raises ValueError."""
        mocks = self._create_base_mocks()

        alpaca = MagicMock()
        alpaca.submit_order.side_effect = ValueError("missing stop_price")

        ctx = create_mock_context(
            db=mocks["db"],
            recovery_manager=mocks["recovery_manager"],
            reconciliation_service=mocks["recon_service"],
            redis=mocks["mock_redis"],
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=mocks["fat_finger_validator"],
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 422
        assert "validation failed" in response.json()["detail"].lower()
        mocks["reservation"].release.assert_called_once()

    def test_alpaca_rejection_error_returns_422(self) -> None:
        """Test 422 when broker rejects order."""
        from apps.execution_gateway.alpaca_client import AlpacaRejectionError

        mocks = self._create_base_mocks()

        alpaca = MagicMock()
        alpaca.submit_order.side_effect = AlpacaRejectionError("Insufficient buying power")

        ctx = create_mock_context(
            db=mocks["db"],
            recovery_manager=mocks["recovery_manager"],
            reconciliation_service=mocks["recon_service"],
            redis=mocks["mock_redis"],
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=mocks["fat_finger_validator"],
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 422
        assert "rejected by broker" in response.json()["detail"].lower()

    def test_alpaca_connection_error_returns_503(self) -> None:
        """Test 503 when broker connection fails."""
        from apps.execution_gateway.alpaca_client import AlpacaConnectionError

        mocks = self._create_base_mocks()

        alpaca = MagicMock()
        alpaca.submit_order.side_effect = AlpacaConnectionError("Connection timeout")

        ctx = create_mock_context(
            db=mocks["db"],
            recovery_manager=mocks["recovery_manager"],
            reconciliation_service=mocks["recon_service"],
            redis=mocks["mock_redis"],
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=mocks["fat_finger_validator"],
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 503
        assert "connection error" in response.json()["detail"].lower()

    def test_live_submission_success(self) -> None:
        """Test successful live order submission."""
        mocks = self._create_base_mocks()

        alpaca = MagicMock()
        alpaca.submit_order.return_value = {"id": "broker-order-id"}

        order_detail = _make_order_detail("client-123", status="pending_new")
        order_detail.broker_order_id = "broker-order-id"
        mocks["db"].get_order_by_client_id.side_effect = [None, order_detail]

        ctx = create_mock_context(
            db=mocks["db"],
            recovery_manager=mocks["recovery_manager"],
            reconciliation_service=mocks["recon_service"],
            redis=mocks["mock_redis"],
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=mocks["fat_finger_validator"],
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-123"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["broker_order_id"] == "broker-order-id"
        mocks["reservation"].confirm.assert_called_once()


class TestIdempotencyCheckErrors:
    """Tests for idempotency check error paths."""

    def test_db_error_during_idempotency_check_returns_503(self) -> None:
        """Test 503 when DB fails during idempotency check."""
        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        db.get_order_by_client_id.side_effect = Exception("Database unavailable")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "idempotency" in response.json()["detail"].lower()
        reservation.release.assert_called()


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_reduce_only_no_position(self) -> None:
        """Test _is_reduce_only_order returns False when no position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, None)
        assert result is False

    def test_is_reduce_only_flat_position(self) -> None:
        """Test _is_reduce_only_order returns False when flat position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": 0})
        assert result is False

    def test_is_reduce_only_long_position_sell(self) -> None:
        """Test _is_reduce_only_order returns True for sell on long position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=50,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": 100})
        assert result is True

    def test_is_reduce_only_long_position_buy(self) -> None:
        """Test _is_reduce_only_order returns False for buy on long position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": 100})
        assert result is False

    def test_is_reduce_only_long_position_oversell(self) -> None:
        """Test _is_reduce_only_order returns False for overselling long position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=150,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": 100})
        assert result is False

    def test_is_reduce_only_short_position_buy(self) -> None:
        """Test _is_reduce_only_order returns True for buy on short position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=50,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": -100})
        assert result is True

    def test_is_reduce_only_short_position_sell(self) -> None:
        """Test _is_reduce_only_order returns False for sell on short position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=10,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": -100})
        assert result is False

    def test_is_reduce_only_short_position_overbuy(self) -> None:
        """Test _is_reduce_only_order returns False for overbuying short position."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=150,
            order_type="market",
            time_in_force="day",
        )
        result = _is_reduce_only_order(order, {"qty": -100})
        assert result is False

    def test_calculate_pending_order_qty(self) -> None:
        """Test _calculate_pending_order_qty calculates correctly."""
        from apps.execution_gateway.routes.orders import _calculate_pending_order_qty

        open_orders = [
            {"side": "buy", "qty": 100, "filled_qty": 20},
            {"side": "buy", "qty": 50, "filled_qty": 0},
            {"side": "sell", "qty": 30, "filled_qty": 10},
        ]

        buy_qty = _calculate_pending_order_qty(open_orders, "buy")
        assert buy_qty == Decimal("130")  # (100-20) + (50-0)

        sell_qty = _calculate_pending_order_qty(open_orders, "sell")
        assert sell_qty == Decimal("20")  # (30-10)

    def test_is_reconciliation_ready_dry_run(self) -> None:
        """Test _is_reconciliation_ready returns True in dry_run mode."""
        from apps.execution_gateway.routes.orders import _is_reconciliation_ready

        ctx = MagicMock()
        ctx.reconciliation_service = None

        config = create_test_config(dry_run=True)

        result = _is_reconciliation_ready(ctx, config)
        assert result is True

    def test_is_reconciliation_ready_no_service(self) -> None:
        """Test _is_reconciliation_ready returns False when no service."""
        from apps.execution_gateway.routes.orders import _is_reconciliation_ready

        ctx = MagicMock()
        ctx.reconciliation_service = None

        config = create_test_config(dry_run=False)

        result = _is_reconciliation_ready(ctx, config)
        assert result is False


class TestDatabaseInsertErrors:
    """Tests for database insert error paths."""

    def test_unique_violation_returns_existing_order(self) -> None:
        """Test UniqueViolation during insert returns existing order."""
        from psycopg.errors import UniqueViolation

        reservation = MagicMock()
        reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )

        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = reservation

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")
        # First get returns None (no existing), then create fails with UniqueViolation
        # Then second get returns the existing order
        db.get_order_by_client_id.side_effect = [
            None,
            _make_order_detail("client-race", status="pending_new"),
        ]
        db.create_order.side_effect = UniqueViolation()

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-race"
            resolve_context.return_value = (Decimal("100"), 1000000)

            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 10,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200
        assert "concurrent retry" in response.json()["message"]


class TestReconciliationBrokerErrors:
    """Tests for broker errors during reconciliation gate."""

    def _create_recovery_manager(self):
        """Create a mock recovery manager with all gates passing."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()
        recovery_manager.position_reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )
        return recovery_manager

    def test_broker_position_fetch_error_returns_503(self) -> None:
        """Test 503 when broker position fetch fails during reconciliation."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        alpaca = MagicMock()
        alpaca.get_open_position.side_effect = Exception("Connection timeout")

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("0")

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=alpaca,
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)
        client = _build_test_app(ctx, config)

        response = client.post(
            "/api/v1/orders",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "time_in_force": "day",
            },
        )

        assert response.status_code == 503
        assert "Broker unavailable" in str(response.json())

    def test_broker_orders_fetch_error_continues(self) -> None:
        """Test order fetch error during reconciliation doesn't block (fail-open for open orders)."""
        recovery_manager = self._create_recovery_manager()

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False
        recon_service.startup_timed_out.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        alpaca = MagicMock()
        alpaca.get_open_position.return_value = {"qty": 100}  # Long position
        alpaca.get_orders.side_effect = Exception("Order fetch failed")

        db = MagicMock()
        db.get_position_by_symbol.return_value = Decimal("100")
        db.get_order_by_client_id.side_effect = [None, _make_order_detail("client-orders-fail")]

        fat_finger_validator = FatFingerValidator(
            FatFingerThresholds(
                max_notional=Decimal("1000000"),
                max_qty=100000,
                max_adv_pct=Decimal("1"),
            )
        )

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            alpaca=alpaca,
            risk_config=RiskConfig(),
            fat_finger_validator=fat_finger_validator,
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        with (
            patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id,
            patch(
                "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
                new_callable=AsyncMock,
            ) as resolve_context,
        ):
            gen_id.return_value = "client-orders-fail"
            resolve_context.return_value = (Decimal("100"), 1000000)

            # Reduce-only order should still work
            response = client.post(
                "/api/v1/orders",
                json={
                    "symbol": "AAPL",
                    "side": "sell",
                    "qty": 50,
                    "order_type": "market",
                    "time_in_force": "day",
                },
            )

        assert response.status_code == 200


class TestPendingOrdersReduceOnly:
    """Tests for pending orders affecting reduce-only calculations."""

    def _create_recovery_manager(self):
        """Create a mock recovery manager with all gates passing."""
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()
        recovery_manager.position_reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )
        return recovery_manager

    def test_pending_sells_reduce_available_position(self) -> None:
        """Test pending sells reduce available position for more sells."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="sell",
            qty=60,  # Want to sell 60
            order_type="market",
            time_in_force="day",
        )
        # Have 100 long, but 50 pending sells
        # Available = 100 - 50 = 50
        # 60 > 50, so not reduce-only
        open_orders = [{"side": "sell", "qty": 50, "filled_qty": 0}]
        result = _is_reduce_only_order(order, {"qty": 100}, open_orders)
        assert result is False


class TestTWAPPreview:
    def _payload(self, **overrides: Any) -> dict[str, Any]:
        payload = {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 21,
            "order_type": "market",
            "duration_minutes": 10,
            "interval_seconds": 60,
            "strategy_id": "alpha_baseline",
            "timezone": "UTC",
        }
        payload.update(overrides)
        return payload

    def test_twap_preview_unauthorized_returns_403(self) -> None:
        ctx = create_mock_context()
        client = _build_test_app(ctx, create_test_config())

        response = client.post(
            "/api/v1/orders/twap-preview",
            json=self._payload(strategy_id="not-allowed"),
        )

        assert response.status_code == 403

    def test_twap_preview_plan_error_returns_422(self) -> None:
        ctx = create_mock_context()
        ctx.twap_slicer.plan.side_effect = ValueError("bad plan")
        client = _build_test_app(ctx, create_test_config())

        response = client.post("/api/v1/orders/twap-preview", json=self._payload())

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "validation_error"
        assert "bad plan" in detail["errors"][0]

    def test_twap_preview_min_slices_rejected(self) -> None:
        ctx = create_mock_context()
        ctx.twap_slicer.plan.return_value = SimpleNamespace(total_slices=1, slices=[])
        client = _build_test_app(ctx, create_test_config())

        response = client.post("/api/v1/orders/twap-preview", json=self._payload(qty=10))

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "TWAP requires at least" in detail["errors"][0]

    def test_twap_preview_market_order_warns_on_missing_quote(self) -> None:
        ctx = create_mock_context()
        plan = SimpleNamespace(
            total_slices=2,
            slices=[
                SimpleNamespace(slice_num=0, qty=11, scheduled_time=datetime.now(UTC)),
                SimpleNamespace(slice_num=1, qty=10, scheduled_time=datetime.now(UTC)),
            ],
        )
        ctx.twap_slicer.plan.return_value = plan
        client = _build_test_app(ctx, create_test_config())

        with patch(
            "apps.execution_gateway.routes.orders._get_side_aware_quote",
            new=AsyncMock(return_value=None),
        ):
            response = client.post(
                "/api/v1/orders/twap-preview",
                json=self._payload(timezone="Invalid/Zone"),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["slice_count"] == 2
        assert data["remainder_distribution"] == [0]
        assert data["slice_notional"] is None
        assert data["notional_warning"]
        assert data["display_times"][0].endswith("+00:00")

    def test_twap_preview_limit_order_notional_too_small(self) -> None:
        ctx = create_mock_context()
        plan = SimpleNamespace(
            total_slices=2,
            slices=[
                SimpleNamespace(slice_num=0, qty=10, scheduled_time=datetime.now(UTC)),
                SimpleNamespace(slice_num=1, qty=10, scheduled_time=datetime.now(UTC)),
            ],
        )
        ctx.twap_slicer.plan.return_value = plan
        client = _build_test_app(ctx, create_test_config())

        response = client.post(
            "/api/v1/orders/twap-preview",
            json=self._payload(order_type="limit", limit_price=10.0),
        )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Slice notional" in detail["errors"][0]

    def test_twap_preview_base_slice_qty_too_small(self) -> None:
        ctx = create_mock_context()
        plan = SimpleNamespace(
            total_slices=2,
            slices=[
                SimpleNamespace(slice_num=0, qty=5, scheduled_time=datetime.now(UTC)),
                SimpleNamespace(slice_num=1, qty=5, scheduled_time=datetime.now(UTC)),
            ],
        )
        ctx.twap_slicer.plan.return_value = plan
        client = _build_test_app(ctx, create_test_config())

        response = client.post(
            "/api/v1/orders/twap-preview",
            json=self._payload(qty=15),
        )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "TWAP minimum slice size" in detail["errors"][0]

    def test_twap_preview_market_order_low_quote_rejected(self) -> None:
        ctx = create_mock_context()
        plan = SimpleNamespace(
            total_slices=2,
            slices=[
                SimpleNamespace(slice_num=0, qty=10, scheduled_time=datetime.now(UTC)),
                SimpleNamespace(slice_num=1, qty=10, scheduled_time=datetime.now(UTC)),
            ],
        )
        ctx.twap_slicer.plan.return_value = plan
        client = _build_test_app(ctx, create_test_config())

        with patch(
            "apps.execution_gateway.routes.orders._get_side_aware_quote",
            new=AsyncMock(return_value=Decimal("10")),
        ):
            response = client.post(
                "/api/v1/orders/twap-preview",
                json=self._payload(qty=20),
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Slice notional" in detail["errors"][0]

    def test_twap_preview_limit_order_success(self) -> None:
        ctx = create_mock_context()
        plan = SimpleNamespace(
            total_slices=2,
            slices=[
                SimpleNamespace(slice_num=0, qty=10, scheduled_time=datetime.now(UTC)),
                SimpleNamespace(slice_num=1, qty=10, scheduled_time=datetime.now(UTC)),
            ],
        )
        ctx.twap_slicer.plan.return_value = plan
        client = _build_test_app(ctx, create_test_config())

        response = client.post(
            "/api/v1/orders/twap-preview",
            json=self._payload(order_type="limit", limit_price=60.0, qty=20),
        )

        assert response.status_code == 200
        data = response.json()
        assert Decimal(str(data["slice_notional"])) == Decimal("600")


class TestModifyOrder:
    def _build_context(self, order: OrderDetail) -> tuple[Any, Any]:
        recovery_manager = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.is_circuit_breaker_unavailable.return_value = False
        recovery_manager.is_position_reservation_unavailable.return_value = False
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.circuit_breaker = MagicMock()
        recovery_manager.circuit_breaker.is_tripped.return_value = False
        recovery_manager.position_reservation = MagicMock()
        recovery_manager.position_reservation.reserve.return_value = _ReservationResult(
            success=True, token="token-1", new_position=Decimal("10")
        )

        db = MagicMock()
        db.get_order_by_client_id.return_value = order
        db.get_modification_by_idempotency_key.return_value = None
        db.get_position_by_symbol.return_value = 0
        db.get_next_modification_seq.return_value = 1
        db.insert_pending_modification.return_value = "mod-1"
        db.finalize_modification.return_value = None
        db.update_order_status_simple_with_conn.return_value = order
        db.insert_replacement_order.return_value = order

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        db.transaction = _tx

        risk_config = MagicMock()
        risk_config.position_limits.max_position_size = 1000

        fat_result = MagicMock()
        fat_result.breached = False
        fat_result.breaches = []

        ctx = create_mock_context(
            db=db,
            recovery_manager=recovery_manager,
            risk_config=risk_config,
            fat_finger_validator=MagicMock(),
        )
        ctx.fat_finger_validator.get_effective_thresholds.return_value = FatFingerThresholds(
            max_notional=Decimal("1000000"),
            max_qty=100000,
            max_adv_pct=Decimal("1"),
        )
        ctx.fat_finger_validator.validate.return_value = fat_result
        ctx.alpaca.replace_order.return_value = {
            "id": "broker-new",
            "client_order_id": "replace-1",
            "status": "accepted",
        }
        return ctx, db

    def test_modify_order_updates_qty(self) -> None:
        order = OrderDetail(
            client_order_id="client-1",
            strategy_id="alpha_baseline",
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("100"),
            stop_price=None,
            time_in_force="day",
            status="new",
            broker_order_id="broker-1",
            error_message=None,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            submitted_at=None,
            filled_at=None,
            filled_qty=Decimal("0"),
            filled_avg_price=None,
            metadata={},
        )
        ctx, _ = self._build_context(order)
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "qty": 12}
        with patch(
            "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
            new=AsyncMock(return_value=(Decimal("100"), 100000)),
        ), patch(
            "apps.execution_gateway.routes.orders._generate_replacement_order_id",
            return_value="replace-1",
        ):
            response = client.patch("/api/v1/orders/client-1", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["new_client_order_id"] == "replace-1"
        assert data["status"] == "completed"
        ctx.recovery_manager.position_reservation.reserve.assert_called_once_with(
            symbol="AAPL",
            side="buy",
            qty=2,
            max_limit=1000,
            current_position=0,
        )
        ctx.recovery_manager.position_reservation.confirm.assert_called_once_with(
            "AAPL",
            "token-1",
        )

    def test_modify_order_buy_stop_lower_skips_fat_finger(self) -> None:
        order = OrderDetail(
            client_order_id="client-stop-buy",
            strategy_id="alpha_baseline",
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="stop",
            limit_price=None,
            stop_price=Decimal("100"),
            time_in_force="day",
            status="new",
            broker_order_id="broker-stop-buy",
            error_message=None,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            submitted_at=None,
            filled_at=None,
            filled_qty=Decimal("0"),
            filled_avg_price=None,
            metadata={},
        )
        ctx, _ = self._build_context(order)
        ctx.fat_finger_validator.validate = MagicMock()
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "stop_price": 95.0}
        with patch(
            "apps.execution_gateway.routes.orders._generate_replacement_order_id",
            return_value="replace-1",
        ):
            response = client.patch("/api/v1/orders/client-stop-buy", json=payload)

        assert response.status_code == 200
        ctx.fat_finger_validator.validate.assert_not_called()

    def test_modify_order_sell_stop_lower_triggers_fat_finger(self) -> None:
        order = OrderDetail(
            client_order_id="client-stop-sell",
            strategy_id="alpha_baseline",
            symbol="AAPL",
            side="sell",
            qty=10,
            order_type="stop",
            limit_price=None,
            stop_price=Decimal("100"),
            time_in_force="day",
            status="new",
            broker_order_id="broker-stop-sell",
            error_message=None,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            submitted_at=None,
            filled_at=None,
            filled_qty=Decimal("0"),
            filled_avg_price=None,
            metadata={},
        )
        ctx, _ = self._build_context(order)
        fat_result = MagicMock()
        fat_result.breached = True
        fat_result.breaches = (
            FatFingerBreach(
                threshold_type="notional",
                limit=Decimal("1000"),
                actual=Decimal("2000"),
                metadata={"reason": "test"},
            ),
        )
        ctx.fat_finger_validator.validate.return_value = fat_result
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "stop_price": 90.0}
        with patch(
            "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
            new=AsyncMock(return_value=(Decimal("100"), 100000)),
        ):
            response = client.patch("/api/v1/orders/client-stop-sell", json=payload)

        assert response.status_code == 422

    def test_modify_order_finalization_failure_marks_submitted_unconfirmed(self) -> None:
        order = _make_order_detail("client-10", status="new")
        order.broker_order_id = "broker-10"
        ctx, db = self._build_context(order)
        db.finalize_modification.side_effect = RuntimeError("db down")
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "qty": 12}
        with patch(
            "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
            new=AsyncMock(return_value=(Decimal("100"), 100000)),
        ), patch(
            "apps.execution_gateway.routes.orders._generate_replacement_order_id",
            return_value="replace-1",
        ):
            response = client.patch("/api/v1/orders/client-10", json=payload)

        assert response.status_code == 500
        db.update_modification_status.assert_called_once()
        args, kwargs = db.update_modification_status.call_args
        assert args[0] == "mod-1"
        assert kwargs["status"] == "submitted_unconfirmed"

    def test_modify_order_blocked_when_kill_switch_engaged(self) -> None:
        order = _make_order_detail("client-2", status="new")
        order.broker_order_id = "broker-2"
        ctx, _ = self._build_context(order)
        ctx.recovery_manager.kill_switch.is_engaged.return_value = True
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "limit_price": 101.0}
        response = client.patch("/api/v1/orders/client-2", json=payload)
        assert response.status_code == 503

    def test_modify_order_allows_risk_reducing_during_kill_switch(self) -> None:
        order = OrderDetail(
            client_order_id="client-3",
            strategy_id="alpha_baseline",
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("100"),
            stop_price=None,
            time_in_force="day",
            status="new",
            broker_order_id="broker-3",
            error_message=None,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            submitted_at=None,
            filled_at=None,
            filled_qty=Decimal("0"),
            filled_avg_price=None,
            metadata={},
        )
        ctx, _ = self._build_context(order)
        ctx.recovery_manager.kill_switch.is_engaged.return_value = True
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "qty": 5}
        with patch(
            "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
            new=AsyncMock(return_value=(Decimal("100"), 100000)),
        ), patch(
            "apps.execution_gateway.routes.orders._generate_replacement_order_id",
            return_value="replace-1",
        ):
            response = client.patch("/api/v1/orders/client-3", json=payload)

        assert response.status_code == 200
        ctx.recovery_manager.position_reservation.reserve.assert_not_called()
        ctx.recovery_manager.position_reservation.confirm.assert_not_called()

    def test_modify_order_blocks_qty_increase_when_reservation_unavailable(self) -> None:
        order = _make_order_detail("client-4", status="new")
        order.broker_order_id = "broker-4"
        ctx, _ = self._build_context(order)
        ctx.recovery_manager.is_position_reservation_unavailable.return_value = True
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "qty": 12}
        response = client.patch("/api/v1/orders/client-4", json=payload)
        assert response.status_code == 503

    def test_modify_order_idempotent_pending_returns_202(self) -> None:
        order = _make_order_detail("client-5", status="new")
        order.broker_order_id = "broker-5"
        ctx, _ = self._build_context(order)
        ctx.db.get_modification_by_idempotency_key.return_value = {
            "original_client_order_id": "client-5",
            "new_client_order_id": "replace-5",
            "modification_id": "mod-5",
            "modified_at": datetime.now(UTC),
            "changes": {"qty": [10, 12]},
            "status": "pending",
        }
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "qty": 12}
        response = client.patch("/api/v1/orders/client-5", json=payload)
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"

    def test_modify_order_idempotent_failed_returns_409(self) -> None:
        order = _make_order_detail("client-6", status="new")
        order.broker_order_id = "broker-6"
        ctx, _ = self._build_context(order)
        ctx.db.get_modification_by_idempotency_key.return_value = {
            "original_client_order_id": "client-6",
            "new_client_order_id": "replace-6",
            "modification_id": "mod-6",
            "modified_at": datetime.now(UTC),
            "changes": {"qty": [10, 12]},
            "status": "failed",
            "error_message": "broker unavailable",
        }
        client = _build_test_app(ctx, create_test_config())

        payload = {"idempotency_key": str(uuid.uuid4()), "qty": 12}
        response = client.patch("/api/v1/orders/client-6", json=payload)
        assert response.status_code == 409

    def test_modify_order_not_found_returns_404(self) -> None:
        ctx = create_mock_context()
        ctx.db.get_order_by_client_id.return_value = None
        client = _build_test_app(ctx, create_test_config())

        response = client.patch(
            "/api/v1/orders/missing",
            json={"idempotency_key": str(uuid.uuid4()), "qty": 5},
        )

        assert response.status_code == 404

    def test_modify_order_unauthorized_returns_403(self) -> None:
        order = _make_order_detail("client-unauth", status="new")
        order.broker_order_id = "broker-unauth"
        order.strategy_id = "other_strategy"
        ctx, _ = self._build_context(order)
        client = _build_test_app(ctx, create_test_config())

        response = client.patch(
            "/api/v1/orders/client-unauth",
            json={"idempotency_key": str(uuid.uuid4()), "qty": 5},
        )

        assert response.status_code == 403

    def test_modify_order_rejects_qty_below_filled(self) -> None:
        order = _make_order_detail("client-filled", status="new")
        order.broker_order_id = "broker-filled"
        order.filled_qty = Decimal("5")
        ctx, _ = self._build_context(order)
        client = _build_test_app(ctx, create_test_config())

        response = client.patch(
            "/api/v1/orders/client-filled",
            json={"idempotency_key": str(uuid.uuid4()), "qty": 4},
        )

        assert response.status_code == 422

    def test_modify_order_no_changes_returns_400(self) -> None:
        order = _make_order_detail("client-nochg", status="new")
        order.broker_order_id = "broker-nochg"
        ctx, _ = self._build_context(order)
        client = _build_test_app(ctx, create_test_config())

        response = client.patch(
            "/api/v1/orders/client-nochg",
            json={"idempotency_key": str(uuid.uuid4()), "qty": 10},
        )

        assert response.status_code == 400

    def test_pending_buys_reduce_available_short_cover(self) -> None:
        """Test pending buys reduce available cover for more buys on short."""
        from apps.execution_gateway.routes.orders import _is_reduce_only_order
        from apps.execution_gateway.schemas import OrderRequest

        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=60,  # Want to buy 60 to cover
            order_type="market",
            time_in_force="day",
        )
        # Have -100 short, but 50 pending buys
        # Available to cover = 100 - 50 = 50
        # 60 > 50, so not reduce-only
        open_orders = [{"side": "buy", "qty": 50, "filled_qty": 0}]
        result = _is_reduce_only_order(order, {"qty": -100}, open_orders)
        assert result is False


class TestModificationHelpers:
    def _order(self, status: str = "new") -> OrderDetail:
        order = _make_order_detail("client-helper", status=status)
        order.broker_order_id = "broker-helper"
        order.execution_style = "instant"
        return order

    def test_extract_user_id_from_auth(self) -> None:
        @dataclass
        class DummyUser:
            user_id: str

        assert orders._extract_user_id_from_auth(None) == "unknown"
        assert orders._extract_user_id_from_auth(DummyUser("user-1")) == "user-1"
        assert orders._extract_user_id_from_auth({"user_id": "user-2"}) == "user-2"
        assert orders._extract_user_id_from_auth({"user_id": None}) == "unknown"
        assert orders._extract_user_id_from_auth(object()) == "unknown"

    def test_check_order_modification_eligibility_rejects_invalid_status(self) -> None:
        order = self._order(status="filled")
        with pytest.raises(HTTPException) as excinfo:
            orders._check_order_modification_eligibility(order)
        assert excinfo.value.status_code == 400

    def test_check_order_modification_eligibility_requires_broker_id(self) -> None:
        order = self._order(status="new")
        order.broker_order_id = None
        with pytest.raises(HTTPException) as excinfo:
            orders._check_order_modification_eligibility(order)
        assert excinfo.value.status_code == 400

    def test_check_order_modification_eligibility_rejects_twap(self) -> None:
        order = self._order(status="new")
        order.execution_style = "twap"
        with pytest.raises(HTTPException) as excinfo:
            orders._check_order_modification_eligibility(order)
        assert excinfo.value.status_code == 400

    def test_check_order_modification_eligibility_allows_valid(self) -> None:
        order = self._order(status="new")
        orders._check_order_modification_eligibility(order)

    def test_acquire_modification_lock_returns_ids(self) -> None:
        ctx = create_mock_context()
        db = ctx.db
        db.get_modification_by_idempotency_key.return_value = None
        db.get_next_modification_seq.return_value = 2
        db.insert_pending_modification.return_value = "mod-123"

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        db.transaction = _tx
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=12)

        with patch(
            "apps.execution_gateway.routes.orders._generate_replacement_order_id",
            return_value="replace-123",
        ):
            modification_id, new_client_order_id = orders._acquire_modification_lock(
                ctx=ctx,
                client_order_id="client-helper",
                order=self._order(),
                payload=payload,
                changes={"qty": (10, 12)},
                user_id="user-1",
                response=None,
            )

        assert modification_id == "mod-123"
        assert new_client_order_id == "replace-123"

    def test_acquire_modification_lock_idempotent_returns_response(self) -> None:
        ctx = create_mock_context()
        db = ctx.db
        db.get_modification_by_idempotency_key.return_value = {
            "original_client_order_id": "client-helper",
            "new_client_order_id": "replace-123",
            "modification_id": "mod-123",
            "modified_at": datetime.now(UTC),
            "changes": {"qty": [10, 12]},
            "status": "pending",
        }

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        db.transaction = _tx
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=12)
        response = Response()

        with pytest.raises(orders._IdempotentModificationException) as excinfo:
            orders._acquire_modification_lock(
                ctx=ctx,
                client_order_id="client-helper",
                order=self._order(),
                payload=payload,
                changes={"qty": (10, 12)},
                user_id="user-1",
                response=response,
            )

        assert excinfo.value.response.status == "pending"
        assert response.status_code == 202

    def test_acquire_modification_lock_lock_contention_raises_409(self) -> None:
        ctx = create_mock_context()
        db = ctx.db
        db.get_modification_by_idempotency_key.side_effect = [None, None]
        db.get_next_modification_seq.side_effect = LockNotAvailable()

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        db.transaction = _tx
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=12)

        with pytest.raises(HTTPException) as excinfo:
            orders._acquire_modification_lock(
                ctx=ctx,
                client_order_id="client-helper",
                order=self._order(),
                payload=payload,
                changes={"qty": (10, 12)},
                user_id="user-1",
                response=None,
            )

        assert excinfo.value.status_code == 409

    def test_call_broker_replace_success_confirms_reservation(self) -> None:
        ctx = create_mock_context()
        ctx.alpaca.replace_order.return_value = {
            "id": "broker-new",
            "client_order_id": "broker-client",
            "status": "accepted",
        }
        ctx.recovery_manager.position_reservation = MagicMock()
        order = self._order()
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=12)

        broker_order_id, broker_client_order_id, _ = orders._call_broker_replace(
            ctx=ctx,
            order=order,
            payload=payload,
            new_client_order_id="replace-123",
            modification_id="mod-123",
            reservation_token="token-1",
        )

        assert broker_order_id == "broker-new"
        assert broker_client_order_id == "broker-client"
        ctx.recovery_manager.position_reservation.confirm.assert_called_once_with("AAPL", "token-1")

    def test_call_broker_replace_validation_error_releases_reservation(self) -> None:
        ctx = create_mock_context()
        ctx.alpaca.replace_order.side_effect = orders.AlpacaValidationError("bad request")
        ctx.recovery_manager.position_reservation = MagicMock()
        order = self._order()
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=12)

        with pytest.raises(HTTPException) as excinfo:
            orders._call_broker_replace(
                ctx=ctx,
                order=order,
                payload=payload,
                new_client_order_id="replace-123",
                modification_id="mod-123",
                reservation_token="token-1",
            )

        assert excinfo.value.status_code == 400
        ctx.recovery_manager.position_reservation.release.assert_called_once_with("AAPL", "token-1")
        ctx.db.update_modification_status.assert_called_once_with(
            "mod-123",
            status="failed",
            error_message="bad request",
        )

    def test_finalize_modification_in_db_success(self) -> None:
        ctx = create_mock_context()
        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        ctx.db.transaction = _tx

        orders._finalize_modification_in_db(
            ctx=ctx,
            client_order_id="client-1",
            order=self._order(),
            modification_id="mod-1",
            broker_order_id="broker-1",
            broker_client_order_id="replace-1",
            broker_response={"status": "accepted"},
            replacement_request=orders.OrderRequest(
                symbol="AAPL",
                side="buy",
                qty=10,
                order_type="market",
                time_in_force="day",
            ),
        )

        ctx.db.finalize_modification.assert_called_once()
        ctx.db.update_order_status_simple_with_conn.assert_called_once_with(
            "client-1", "replaced", conn=conn
        )
        ctx.db.insert_replacement_order.assert_called_once()

    def test_finalize_modification_in_db_failure_marks_unconfirmed(self) -> None:
        ctx = create_mock_context()
        ctx.db.finalize_modification.side_effect = RuntimeError("db down")

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        ctx.db.transaction = _tx

        with pytest.raises(HTTPException) as excinfo:
            orders._finalize_modification_in_db(
                ctx=ctx,
                client_order_id="client-1",
                order=self._order(),
                modification_id="mod-1",
                broker_order_id="broker-1",
                broker_client_order_id="replace-1",
                broker_response={"status": "accepted"},
                replacement_request=orders.OrderRequest(
                    symbol="AAPL",
                    side="buy",
                    qty=10,
                    order_type="market",
                    time_in_force="day",
                ),
            )

        assert excinfo.value.status_code == 500
        ctx.db.update_modification_status.assert_called_once()

    def test_build_replacement_request_uses_payload_overrides(self) -> None:
        order = self._order()
        order.qty = 10
        order.limit_price = Decimal("100")
        order.stop_price = Decimal("95")
        order.time_in_force = "day"
        order.execution_style = None
        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            qty=12,
            limit_price=Decimal("101"),
            time_in_force="gtc",
        )

        replacement = orders._build_replacement_request(order, payload)

        assert replacement.qty == 12
        assert replacement.limit_price == Decimal("101")
        assert replacement.stop_price == Decimal("95")
        assert replacement.time_in_force == "gtc"
        assert replacement.execution_style == "instant"

    def test_serialize_change_value_handles_decimal_and_datetime(self) -> None:
        now = datetime.now(UTC)
        assert orders._serialize_change_value(Decimal("1.23")) == "1.23"
        assert orders._serialize_change_value(now) == now.isoformat()

    def test_serialize_change_value_passthrough(self) -> None:
        assert orders._serialize_change_value("plain") == "plain"

    def test_compute_modification_changes_includes_limit_stop_tif(self) -> None:
        order = self._order()
        order.limit_price = Decimal("100")
        order.stop_price = Decimal("90")
        order.time_in_force = "day"
        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            limit_price=Decimal("101"),
            stop_price=Decimal("95"),
            time_in_force="gtc",
        )

        changes = orders._compute_modification_changes(order, payload)

        assert changes["limit_price"] == ("100", "101")
        assert changes["stop_price"] == ("90", "95")
        assert changes["time_in_force"] == ("day", "gtc")

    def test_validate_modify_fields_rejects_invalid_prices(self) -> None:
        order = self._order()
        order.order_type = "market"
        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            limit_price=Decimal("101"),
        )
        with pytest.raises(HTTPException) as excinfo:
            orders._validate_modify_fields(order, payload)
        assert excinfo.value.status_code == 422

        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            stop_price=Decimal("99"),
        )
        with pytest.raises(HTTPException) as excinfo:
            orders._validate_modify_fields(order, payload)
        assert excinfo.value.status_code == 422

    def test_validate_modify_fields_stop_limit_constraints(self) -> None:
        order = self._order()
        order.order_type = "stop_limit"
        order.side = "buy"
        order.stop_price = Decimal("100")
        order.limit_price = Decimal("105")
        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            limit_price=Decimal("95"),
        )
        with pytest.raises(HTTPException) as excinfo:
            orders._validate_modify_fields(order, payload)
        assert excinfo.value.status_code == 422

    def test_validate_modify_fields_stop_limit_sell_constraint(self) -> None:
        order = self._order()
        order.order_type = "stop_limit"
        order.side = "sell"
        order.stop_price = Decimal("100")
        order.limit_price = Decimal("95")
        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            limit_price=Decimal("110"),
        )
        with pytest.raises(HTTPException) as excinfo:
            orders._validate_modify_fields(order, payload)
        assert excinfo.value.status_code == 422

    def test_handle_idempotent_modification_completed_and_failed(self) -> None:
        completed = {
            "original_client_order_id": "client-1",
            "new_client_order_id": "replace-1",
            "modification_id": "mod-1",
            "modified_at": datetime.now(UTC),
            "changes": {},
            "status": "completed",
        }
        response = orders._handle_idempotent_modification_response(completed, None)
        assert response is not None
        assert response.status == "completed"

        failed = {
            "original_client_order_id": "client-1",
            "new_client_order_id": "replace-1",
            "modification_id": "mod-1",
            "modified_at": datetime.now(UTC),
            "changes": {},
            "status": "failed",
            "error_message": "broker down",
        }
        with pytest.raises(HTTPException) as excinfo:
            orders._handle_idempotent_modification_response(failed, None)
        assert excinfo.value.status_code == 409

    @pytest.mark.asyncio()
    async def test_is_strictly_risk_reducing_paths(self) -> None:
        ctx = create_mock_context()
        ctx.db.get_position_by_symbol.return_value = 10
        order = self._order()
        order.qty = 10
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=5)

        assert await orders._is_strictly_risk_reducing(order, payload, ctx) is True

    @pytest.mark.asyncio()
    async def test_is_strictly_risk_reducing_rejects_stop_price_or_tif(self) -> None:
        ctx = create_mock_context()
        ctx.db.get_position_by_symbol.return_value = 10
        order = self._order()
        order.qty = 10
        order.stop_price = Decimal("100")

        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            qty=5,
            stop_price=Decimal("110"),
        )
        assert await orders._is_strictly_risk_reducing(order, payload, ctx) is False

        payload = orders.OrderModifyRequest(
            idempotency_key=str(uuid.uuid4()),
            qty=5,
            time_in_force="gtc",
        )
        assert await orders._is_strictly_risk_reducing(order, payload, ctx) is False

    @pytest.mark.asyncio()
    async def test_check_modify_safety_gates_circuit_breaker_unavailable(self) -> None:
        ctx = create_mock_context()
        ctx.recovery_manager.is_kill_switch_unavailable.return_value = False
        ctx.recovery_manager.is_circuit_breaker_unavailable.return_value = True
        ctx.recovery_manager.kill_switch = MagicMock()
        ctx.recovery_manager.kill_switch.is_engaged.return_value = False
        ctx.recovery_manager.circuit_breaker = None
        order = self._order()
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=5)

        with pytest.raises(HTTPException) as excinfo:
            await orders._check_modify_safety_gates(ctx, order, payload)
        assert excinfo.value.status_code == 503

    def test_validate_modify_position_limits_exceeds(self) -> None:
        ctx = create_mock_context()
        ctx.risk_config.position_limits.max_position_size = 15
        ctx.db.get_position_by_symbol.return_value = 0
        order = self._order()
        order.qty = 10
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=20)

        with pytest.raises(HTTPException) as excinfo:
            orders._validate_modify_position_limits(order, payload, ctx)
        assert excinfo.value.status_code == 422

    def test_reserve_modify_delta_position_lookup_failure(self) -> None:
        ctx = create_mock_context()
        ctx.recovery_manager.is_position_reservation_unavailable.return_value = False
        ctx.recovery_manager.position_reservation = MagicMock()
        ctx.db.get_position_by_symbol.side_effect = RuntimeError("db down")
        order = self._order()
        order.qty = 10
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=20)

        with pytest.raises(HTTPException) as excinfo:
            orders._reserve_modify_delta(order, payload, ctx)
        assert excinfo.value.status_code == 503

    def test_reserve_modify_delta_failure(self) -> None:
        ctx = create_mock_context()
        ctx.recovery_manager.is_position_reservation_unavailable.return_value = False
        ctx.recovery_manager.position_reservation = MagicMock()
        ctx.recovery_manager.position_reservation.reserve.return_value = SimpleNamespace(
            success=False, token=None, reason="limit"
        )
        ctx.db.get_position_by_symbol.return_value = 0
        order = self._order()
        order.qty = 10
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=20)

        with pytest.raises(HTTPException) as excinfo:
            orders._reserve_modify_delta(order, payload, ctx)
        assert excinfo.value.status_code == 422

    def test_acquire_modification_lock_lock_contention_idempotent(self) -> None:
        ctx = create_mock_context()
        db = ctx.db
        db.get_modification_by_idempotency_key.side_effect = [
            None,
            {
                "original_client_order_id": "client-helper",
                "new_client_order_id": "replace-123",
                "modification_id": "mod-123",
                "modified_at": datetime.now(UTC),
                "changes": {"qty": [10, 12]},
                "status": "pending",
            },
        ]
        db.get_next_modification_seq.side_effect = LockNotAvailable()

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        db.transaction = _tx
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=12)
        response = Response()

        with pytest.raises(orders._IdempotentModificationException) as excinfo:
            orders._acquire_modification_lock(
                ctx=ctx,
                client_order_id="client-helper",
                order=self._order(),
                payload=payload,
                changes={"qty": (10, 12)},
                user_id="user-1",
                response=response,
            )

        assert excinfo.value.response.status == "pending"
        assert response.status_code == 202

    def test_finalize_modification_in_db_update_status_failure(self) -> None:
        ctx = create_mock_context()
        ctx.db.finalize_modification.side_effect = RuntimeError("db down")
        ctx.db.update_modification_status.side_effect = RuntimeError("status update failed")

        conn = MagicMock()

        @contextmanager
        def _tx():
            yield conn

        ctx.db.transaction = _tx

        with pytest.raises(HTTPException) as excinfo:
            orders._finalize_modification_in_db(
                ctx=ctx,
                client_order_id="client-1",
                order=self._order(),
                modification_id="mod-1",
                broker_order_id="broker-1",
                broker_client_order_id="replace-1",
                broker_response={"status": "accepted"},
                replacement_request=orders.OrderRequest(
                    symbol="AAPL",
                    side="buy",
                    qty=10,
                    order_type="market",
                    time_in_force="day",
                ),
            )

        assert excinfo.value.status_code == 500

    @pytest.mark.asyncio()
    async def test_check_modify_safety_gates_unavailable(self) -> None:
        ctx = create_mock_context()
        ctx.recovery_manager.is_kill_switch_unavailable.return_value = True
        ctx.recovery_manager.kill_switch = None
        ctx.recovery_manager.is_circuit_breaker_unavailable.return_value = False
        ctx.recovery_manager.circuit_breaker = MagicMock()
        order = self._order()
        payload = orders.OrderModifyRequest(idempotency_key=str(uuid.uuid4()), qty=5)

        with pytest.raises(HTTPException) as excinfo:
            await orders._check_modify_safety_gates(ctx, order, payload)
        assert excinfo.value.status_code == 503


class TestModificationHistory:
    def test_modification_history_not_found_returns_404(self) -> None:
        ctx = create_mock_context()
        ctx.db.get_order_by_client_id.return_value = None
        client = _build_test_app(ctx, create_test_config())

        response = client.get("/api/v1/orders/missing/modifications")
        assert response.status_code == 404

    def test_modification_history_unauthorized_returns_403(self) -> None:
        order = _make_order_detail("client-history", status="new")
        order.strategy_id = "other_strategy"
        ctx = create_mock_context()
        ctx.db.get_order_by_client_id.return_value = order
        client = _build_test_app(ctx, create_test_config())

        response = client.get("/api/v1/orders/client-history/modifications")
        assert response.status_code == 403


class TestOrderUtilityHelpers:
    def test_normalize_utc_handles_naive_and_aware(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0, 0)
        aware = datetime(2026, 1, 1, 7, 0, 0, tzinfo=ZoneInfo("America/New_York"))

        normalized_naive = orders._normalize_utc(naive)
        normalized_aware = orders._normalize_utc(aware)

        assert normalized_naive.tzinfo == UTC
        assert normalized_aware.tzinfo == UTC

    def test_market_hours_warning_outside_and_inside(self) -> None:
        market_open = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
        market_close = datetime(2026, 1, 2, 21, 0, tzinfo=UTC)
        warning = orders._get_market_hours_warning(
            market_open - orders.timedelta(minutes=30),
            market_close + orders.timedelta(minutes=30),
        )
        ok = orders._get_market_hours_warning(
            market_open + orders.timedelta(minutes=5),
            market_close - orders.timedelta(minutes=5),
        )

        assert warning is not None
        assert ok is None

    @pytest.mark.asyncio()
    async def test_get_side_aware_quote_paths(self) -> None:
        ctx = create_mock_context()
        ctx.alpaca = None
        assert await orders._get_side_aware_quote("AAPL", "buy", ctx) is None

        alpaca = MagicMock()
        alpaca.get_latest_quotes.side_effect = orders.AlpacaConnectionError("down")
        ctx.alpaca = alpaca
        assert await orders._get_side_aware_quote("AAPL", "buy", ctx) is None

        alpaca.get_latest_quotes.side_effect = None
        alpaca.get_latest_quotes.return_value = {
            "AAPL": {"ask_price": Decimal("101"), "bid_price": Decimal("99")}
        }
        assert await orders._get_side_aware_quote("AAPL", "buy", ctx) == Decimal("101")
        assert await orders._get_side_aware_quote("AAPL", "sell", ctx) == Decimal("99")
