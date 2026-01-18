"""Tests for order routes in apps/execution_gateway/routes/orders.py."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.fat_finger_validator import FatFingerThresholds, FatFingerValidator
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
        user=None,
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
    app.dependency_overrides[orders.order_read_auth] = _mock_auth_context
    app.dependency_overrides[orders.order_submit_rl] = lambda: 1
    app.dependency_overrides[orders.order_cancel_rl] = lambda: 1

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

        with patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id, patch(
            "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
            new_callable=AsyncMock,
        ) as resolve_context:
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

        with patch("apps.execution_gateway.routes.orders.generate_client_order_id") as gen_id, patch(
            "apps.execution_gateway.routes.orders.resolve_fat_finger_context",
            new_callable=AsyncMock,
        ) as resolve_context:
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
