"""Tests for slicing routes in apps/execution_gateway/routes/slicing.py."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.routes import slicing
from apps.execution_gateway.schemas import OrderDetail, SliceDetail, SlicingPlan
from libs.core.common.api_auth_dependency import AuthContext


def _build_app(ctx: Any, config: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(slicing.router)
    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[slicing.order_slice_auth] = _mock_auth_context
    app.dependency_overrides[slicing.order_slice_rl] = lambda: 10
    app.dependency_overrides[slicing.order_read_auth] = _mock_auth_context
    app.dependency_overrides[slicing.order_cancel_auth] = _mock_auth_context
    return app


def _mock_auth_context() -> AuthContext:
    return AuthContext(
        user=None,
        internal_claims=None,
        auth_type="test",
        is_authenticated=True,
    )


def _make_order_detail(
    *,
    client_order_id: str,
    strategy_id: str,
    status: str,
    qty: int,
    slice_num: int | None = None,
    scheduled_time: datetime | None = None,
    parent_order_id: str | None = None,
    total_slices: int | None = None,
) -> OrderDetail:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return OrderDetail(
        client_order_id=client_order_id,
        strategy_id=strategy_id,
        symbol="AAPL",
        side="buy",
        qty=qty,
        order_type="market",
        time_in_force="day",
        status=status,
        retry_count=0,
        created_at=now,
        updated_at=now,
        filled_qty=Decimal("0"),
        parent_order_id=parent_order_id,
        slice_num=slice_num,
        total_slices=total_slices,
        scheduled_time=scheduled_time,
    )


@contextmanager
def _transaction_context(conn: MagicMock):
    yield conn


class TestSubmitSlicedOrder:
    def test_scheduler_missing_returns_503(self) -> None:
        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = None
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/orders/slice",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "duration_minutes": 5,
                "interval_seconds": 60,
            },
        )

        assert response.status_code == 503
        assert "scheduler" in response.json()["detail"]

    def test_kill_switch_engaged_returns_503(self) -> None:
        kill_switch = MagicMock()
        kill_switch.is_engaged.return_value = True
        kill_switch.get_status.return_value = {"engaged_by": "ops", "engagement_reason": "test"}

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = kill_switch
        recovery_manager.is_kill_switch_unavailable.return_value = False

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config(dry_run=True)
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/orders/slice",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "duration_minutes": 5,
                "interval_seconds": 60,
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "Kill-switch engaged"

    def test_liquidity_service_unavailable_returns_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", True)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        ctx = create_mock_context(recovery_manager=recovery_manager, liquidity_service=None)
        config = create_test_config(dry_run=True)
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/orders/slice",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "duration_minutes": 5,
                "interval_seconds": 60,
            },
        )

        assert response.status_code == 503
        assert "Liquidity service unavailable" in response.json()["detail"]

    def test_submit_order_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        slice_details = [
            SliceDetail(
                slice_num=0,
                qty=5,
                scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                client_order_id="slice-0",
                strategy_id="twap_slice_parent_0",
                status="pending_new",
            ),
            SliceDetail(
                slice_num=1,
                qty=5,
                scheduled_time=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
                client_order_id="slice-1",
                strategy_id="twap_slice_parent_1",
                status="pending_new",
            ),
        ]
        slicing_plan = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_60s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=2,
            duration_minutes=5,
            interval_seconds=60,
            slices=slice_details,
        )

        twap_slicer = MagicMock()
        twap_slicer.plan.return_value = slicing_plan

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)
        mock_db.get_order_by_client_id.return_value = None

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            twap_slicer=twap_slicer,
            db=mock_db,
        )
        config = create_test_config(dry_run=True)
        app = _build_app(ctx, config)

        recovery_manager.slice_scheduler.schedule_slices.return_value = ["job-1", "job-2"]

        client = TestClient(app)
        response = client.post(
            "/api/v1/orders/slice",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "duration_minutes": 5,
                "interval_seconds": 60,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["parent_order_id"] == "parent-1"
        assert data["total_slices"] == 2
        assert len(data["slices"]) == 2
        recovery_manager.slice_scheduler.schedule_slices.assert_called_once()

    def test_existing_twap_plan_returns_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        parent = _make_order_detail(
            client_order_id="parent-1",
            strategy_id="twap_parent_5m_60s",
            status="pending_new",
            qty=10,
            total_slices=2,
        )
        slices = [
            _make_order_detail(
                client_order_id="slice-0",
                strategy_id="twap_slice_parent_0",
                status="pending_new",
                qty=5,
                slice_num=0,
                scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                parent_order_id="parent-1",
            ),
            _make_order_detail(
                client_order_id="slice-1",
                strategy_id="twap_slice_parent_1",
                status="pending_new",
                qty=5,
                slice_num=1,
                scheduled_time=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
                parent_order_id="parent-1",
            ),
        ]

        twap_slicer = MagicMock()
        twap_slicer.plan.return_value = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_60s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=2,
            duration_minutes=5,
            interval_seconds=60,
            slices=[],
        )

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        mock_db = MagicMock()
        mock_db.get_order_by_client_id.return_value = parent
        mock_db.get_slices_by_parent_id.return_value = slices

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            twap_slicer=twap_slicer,
            db=mock_db,
        )
        config = create_test_config(dry_run=True)
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.post(
            "/api/v1/orders/slice",
            json={
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "duration_minutes": 5,
                "interval_seconds": 60,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["parent_order_id"] == "parent-1"
        assert len(data["slices"]) == 2
        mock_db.create_parent_order.assert_not_called()


class TestGetSlicesByParent:
    def test_parent_not_found_returns_404(self) -> None:
        mock_db = MagicMock()
        mock_db.get_slices_by_parent_id.return_value = []
        mock_db.get_order_by_client_id.return_value = None

        ctx = create_mock_context(db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.get("/api/v1/orders/parent-1/slices")

        assert response.status_code == 404
        assert "Parent order not found" in response.json()["detail"]

    def test_parent_found_no_slices_returns_empty_list(self) -> None:
        parent = _make_order_detail(
            client_order_id="parent-1",
            strategy_id="twap_parent_5m_60s",
            status="pending_new",
            qty=10,
            total_slices=2,
        )

        mock_db = MagicMock()
        mock_db.get_slices_by_parent_id.return_value = []
        mock_db.get_order_by_client_id.return_value = parent

        ctx = create_mock_context(db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.get("/api/v1/orders/parent-1/slices")

        assert response.status_code == 200
        assert response.json() == []

    def test_slices_returned(self) -> None:
        slices = [
            _make_order_detail(
                client_order_id="slice-0",
                strategy_id="twap_slice_parent_0",
                status="pending_new",
                qty=5,
                slice_num=0,
                scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                parent_order_id="parent-1",
            ),
            _make_order_detail(
                client_order_id="slice-1",
                strategy_id="twap_slice_parent_1",
                status="pending_new",
                qty=5,
                slice_num=1,
                scheduled_time=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
                parent_order_id="parent-1",
            ),
        ]

        mock_db = MagicMock()
        mock_db.get_slices_by_parent_id.return_value = slices

        ctx = create_mock_context(db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.get("/api/v1/orders/parent-1/slices")

        assert response.status_code == 200
        assert len(response.json()) == 2


class TestCancelSlices:
    def test_scheduler_unavailable_returns_503(self) -> None:
        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = None

        ctx = create_mock_context(recovery_manager=recovery_manager)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.delete("/api/v1/orders/parent-1/slices")

        assert response.status_code == 503
        assert "Slice scheduler unavailable" in response.json()["detail"]

    def test_parent_missing_returns_404(self) -> None:
        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()

        mock_db = MagicMock()
        mock_db.get_order_by_client_id.return_value = None

        ctx = create_mock_context(recovery_manager=recovery_manager, db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.delete("/api/v1/orders/parent-1/slices")

        assert response.status_code == 404
        assert "Parent order not found" in response.json()["detail"]

    def test_cancel_slices_success(self) -> None:
        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.slice_scheduler.cancel_remaining_slices.return_value = (2, 3)

        mock_db = MagicMock()
        mock_db.get_order_by_client_id.return_value = _make_order_detail(
            client_order_id="parent-1",
            strategy_id="twap_parent_5m_60s",
            status="pending_new",
            qty=10,
            total_slices=2,
        )

        ctx = create_mock_context(recovery_manager=recovery_manager, db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.delete("/api/v1/orders/parent-1/slices")

        assert response.status_code == 200
        data = response.json()
        assert data["parent_order_id"] == "parent-1"
        assert data["scheduler_canceled"] == 2
        assert data["db_canceled"] == 3
