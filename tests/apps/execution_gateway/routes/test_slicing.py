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


class TestReconciliationAndQuarantineGates:
    """Test reconciliation and quarantine safety gates."""

    def test_quarantine_redis_unavailable_returns_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test 503 when Redis is unavailable for quarantine check."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = True

        # Redis is None - triggers quarantine unavailable
        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=None,
        )
        config = create_test_config(dry_run=False)
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
        assert "Quarantine" in str(response.json()) or "quarantine" in str(response.json()).lower()

    def test_quarantine_symbol_blocked_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test 503 when symbol is quarantined."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = True

        # Mock Redis to return quarantine flag
        mock_redis = MagicMock()
        mock_redis.mget.return_value = [b"quarantined", None]

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
        )
        config = create_test_config(dry_run=False)
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
        detail = response.json()["detail"]
        assert "quarantine" in str(detail).lower()

    def test_quarantine_redis_error_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test 503 when Redis raises error during quarantine check."""
        from redis.exceptions import RedisError

        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = True

        # Mock Redis to raise RedisError
        mock_redis = MagicMock()
        mock_redis.mget.side_effect = RedisError("Connection failed")

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
        )
        config = create_test_config(dry_run=False)
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


class TestKillSwitchUnavailable:
    """Test kill-switch unavailability handling."""

    def test_kill_switch_unavailable_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test 503 when kill-switch service is unavailable."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = True

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
        assert "Kill-switch" in str(response.json()) or "kill" in str(response.json()).lower()


class TestSliceDataCorruption:
    """Test corrupt slice data handling."""

    def test_corrupt_slice_missing_slice_num_returns_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test 500 when slice data has None slice_num (data corruption)."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        # Create slices with corrupt data (None slice_num)
        corrupt_slice = _make_order_detail(
            client_order_id="slice-0",
            strategy_id="twap_slice_parent_0",
            status="pending_new",
            qty=5,
            slice_num=None,  # Corrupt!
            scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            parent_order_id="parent-1",
        )

        parent = _make_order_detail(
            client_order_id="parent-1",
            strategy_id="twap_parent_5m_60s",
            status="pending_new",
            qty=10,
            total_slices=1,
        )

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        mock_db = MagicMock()
        mock_db.get_order_by_client_id.return_value = parent
        mock_db.get_slices_by_parent_id.return_value = [corrupt_slice]

        twap_slicer = MagicMock()
        twap_slicer.plan.return_value = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_60s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=1,
            duration_minutes=5,
            interval_seconds=60,
            slices=[],
        )

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

        assert response.status_code == 500
        assert "Corrupt" in str(response.json()) or "corrupt" in str(response.json()).lower()


class TestSchedulingErrors:
    """Test slice scheduling error handling."""

    def test_scheduling_failure_triggers_compensation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test scheduling failure cancels pending slices (compensation)."""
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
        ]
        slicing_plan = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_60s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=1,
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

        # Scheduling fails with ValueError
        recovery_manager.slice_scheduler.schedule_slices.side_effect = ValueError(
            "Invalid schedule data"
        )

        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)
        mock_db.get_order_by_client_id.return_value = None
        mock_db.cancel_pending_slices.return_value = 1
        mock_db.get_slices_by_parent_id.return_value = []

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

        # Should return 400 due to ValueError during scheduling (validation error)
        assert response.status_code == 400
        # Compensation should have been called
        mock_db.cancel_pending_slices.assert_called_once()


class TestLegacyTwapFallback:
    """Test legacy TWAP hash fallback logic."""

    def test_non_default_interval_skips_legacy_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test non-default interval (not 60s) skips legacy hash fallback."""
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
        ]
        slicing_plan = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_30s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=1,
            duration_minutes=5,
            interval_seconds=30,  # Non-default interval
            slices=slice_details,
        )

        twap_slicer = MagicMock()
        twap_slicer.plan.return_value = slicing_plan

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False
        recovery_manager.slice_scheduler.schedule_slices.return_value = ["job-1"]

        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)
        mock_db.get_order_by_client_id.return_value = None  # No existing order

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
                "interval_seconds": 30,  # Non-default
            },
        )

        assert response.status_code == 200


class TestQuarantineConnectionErrors:
    """Test quarantine check connection error handling."""

    def test_quarantine_connection_error_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test 503 when Redis connection error during quarantine check."""
        import redis.exceptions

        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = True

        # Mock Redis to raise ConnectionError
        mock_redis = MagicMock()
        mock_redis.mget.side_effect = redis.exceptions.ConnectionError("Connection failed")

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
        )
        config = create_test_config(dry_run=False)
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

    def test_quarantine_type_error_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test 503 when TypeError during quarantine check."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = True

        # Mock Redis to raise TypeError
        mock_redis = MagicMock()
        mock_redis.mget.side_effect = TypeError("Type error")

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
        )
        config = create_test_config(dry_run=False)
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


class TestReconciliationGates:
    """Test reconciliation and override handling."""

    def test_reconciliation_not_ready_no_override_returns_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test 503 when reconciliation not ready and no override active."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = False

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
        )
        config = create_test_config(dry_run=False)
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
        assert "Reconciliation" in str(response.json())

    def test_reconciliation_not_ready_with_override_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test order proceeds when reconciliation has override active."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", False)

        slice_details = [
            SliceDetail(
                slice_num=0,
                qty=10,
                scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                client_order_id="slice-0",
                strategy_id="twap_slice_parent_0",
                status="pending_new",
            ),
        ]
        slicing_plan = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_60s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=1,
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
        recovery_manager.slice_scheduler.schedule_slices.return_value = ["job-1"]

        recon_service = MagicMock()
        recon_service.is_startup_complete.return_value = False
        recon_service.override_active.return_value = True
        recon_service.override_context.return_value = {"reason": "manual override"}

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None, None]

        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)
        mock_db.get_order_by_client_id.return_value = None

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            reconciliation_service=recon_service,
            redis=mock_redis,
            twap_slicer=twap_slicer,
            db=mock_db,
        )
        config = create_test_config(dry_run=False)
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


class TestDatabaseErrors:
    """Test database error handling paths."""

    def test_get_slices_db_operational_error_returns_500(self) -> None:
        """Test 500 when database operational error during get_slices."""
        import psycopg

        mock_db = MagicMock()
        mock_db.get_slices_by_parent_id.side_effect = psycopg.OperationalError("Connection lost")

        ctx = create_mock_context(db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.get("/api/v1/orders/parent-1/slices")

        assert response.status_code == 500
        assert "database error" in response.json()["detail"]

    def test_get_slices_attribute_error_returns_500(self) -> None:
        """Test 500 when AttributeError during get_slices."""
        mock_db = MagicMock()
        mock_db.get_slices_by_parent_id.side_effect = AttributeError("Missing attribute")

        ctx = create_mock_context(db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.get("/api/v1/orders/parent-1/slices")

        assert response.status_code == 500

    def test_cancel_slices_db_operational_error_returns_500(self) -> None:
        """Test 500 when database operational error during cancel_slices."""
        import psycopg

        parent = _make_order_detail(
            client_order_id="parent-1",
            strategy_id="twap_parent_5m_60s",
            status="pending_new",
            qty=10,
            total_slices=2,
        )

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.slice_scheduler.cancel_remaining_slices.side_effect = (
            psycopg.OperationalError("Connection lost")
        )

        mock_db = MagicMock()
        mock_db.get_order_by_client_id.return_value = parent

        ctx = create_mock_context(recovery_manager=recovery_manager, db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.delete("/api/v1/orders/parent-1/slices")

        assert response.status_code == 500
        assert "database error" in response.json()["detail"]

    def test_cancel_slices_runtime_error_returns_500(self) -> None:
        """Test 500 when scheduler RuntimeError during cancel_slices."""
        parent = _make_order_detail(
            client_order_id="parent-1",
            strategy_id="twap_parent_5m_60s",
            status="pending_new",
            qty=10,
            total_slices=2,
        )

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.slice_scheduler.cancel_remaining_slices.side_effect = RuntimeError(
            "Scheduler error"
        )

        mock_db = MagicMock()
        mock_db.get_order_by_client_id.return_value = parent

        ctx = create_mock_context(recovery_manager=recovery_manager, db=mock_db)
        config = create_test_config()
        app = _build_app(ctx, config)

        client = TestClient(app)
        response = client.delete("/api/v1/orders/parent-1/slices")

        assert response.status_code == 500


class TestLiquidityChecks:
    """Test liquidity service error paths."""

    def test_adv_lookup_returns_none_returns_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test 503 when ADV lookup returns None."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", True)

        recovery_manager = MagicMock()
        recovery_manager.slice_scheduler = MagicMock()
        recovery_manager.kill_switch = MagicMock()
        recovery_manager.is_kill_switch_unavailable.return_value = False
        recovery_manager.kill_switch.is_engaged.return_value = False

        liquidity_service = MagicMock()
        liquidity_service.get_adv.return_value = None  # ADV unavailable

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            liquidity_service=liquidity_service,
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

        assert response.status_code == 503
        assert "ADV lookup failed" in response.json()["detail"]

    def test_adv_computed_less_than_one_clamps_to_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that very low ADV clamps max_slice_qty to 1."""
        monkeypatch.setattr(slicing, "LIQUIDITY_CHECK_ENABLED", True)

        slice_details = [
            SliceDetail(
                slice_num=0,
                qty=10,
                scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                client_order_id="slice-0",
                strategy_id="twap_slice_parent_0",
                status="pending_new",
            ),
        ]
        slicing_plan = SlicingPlan(
            parent_order_id="parent-1",
            parent_strategy_id="twap_parent_5m_60s",
            symbol="AAPL",
            side="buy",
            total_qty=10,
            total_slices=1,
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
        recovery_manager.slice_scheduler.schedule_slices.return_value = ["job-1"]

        liquidity_service = MagicMock()
        liquidity_service.get_adv.return_value = 0.001  # Very low ADV

        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)
        mock_db.get_order_by_client_id.return_value = None

        ctx = create_mock_context(
            recovery_manager=recovery_manager,
            liquidity_service=liquidity_service,
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


class TestSchedulingPartialFailure:
    """Test partial scheduling failure with progressed slices."""

    def test_scheduling_failure_with_progressed_slices_leaves_parent_active(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test scheduling failure when some slices already progressed."""
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

        # Scheduling fails with AttributeError
        recovery_manager.slice_scheduler.schedule_slices.side_effect = AttributeError(
            "Invalid schedule data"
        )

        # Slices that have already progressed (one filled, one pending)
        progressed_slice = _make_order_detail(
            client_order_id="slice-0",
            strategy_id="twap_slice_parent_0",
            status="filled",  # Already progressed
            qty=5,
            slice_num=0,
            scheduled_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            parent_order_id="parent-1",
        )
        pending_slice = _make_order_detail(
            client_order_id="slice-1",
            strategy_id="twap_slice_parent_1",
            status="pending_new",
            qty=5,
            slice_num=1,
            scheduled_time=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
            parent_order_id="parent-1",
        )

        mock_db = MagicMock()
        conn = MagicMock()
        mock_db.transaction.return_value = _transaction_context(conn)
        mock_db.get_order_by_client_id.return_value = None
        mock_db.cancel_pending_slices.return_value = 1
        mock_db.get_slices_by_parent_id.return_value = [progressed_slice, pending_slice]

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

        # Should return 500 due to AttributeError (data error, not validation error)
        assert response.status_code == 500
        # Should NOT cancel parent since slices already progressed
        mock_db.update_order_status.assert_not_called()
