"""Tests for reconciliation routes in apps/execution_gateway/routes/reconciliation.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.routes import reconciliation
from libs.trading.risk_management import RiskConfig


def _mock_user_context(_request: Request) -> dict[str, Any]:
    return {
        "role": "admin",
        "user_id": "user-1",
        "user": {"role": "admin", "user_id": "user-1"},
    }


async def _bypass_rate_limit() -> int:
    return 1


def _build_test_client(ctx: Any, config: Any) -> TestClient:
    app = FastAPI()
    app.include_router(reconciliation.router)

    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[reconciliation.build_user_context] = _mock_user_context
    app.dependency_overrides[reconciliation.fills_backfill_rate_limiter] = _bypass_rate_limit

    return TestClient(app)


class TestReconciliationStatus:
    def test_status_dry_run_returns_disabled_message(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=MagicMock(),
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)

        client = _build_test_client(ctx, config)
        response = client.get("/api/v1/reconciliation/status")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["startup_complete"] is True
        assert data["dry_run"] is True
        assert data["message"] == "DRY_RUN mode - reconciliation gating disabled"

    def test_status_without_service_returns_not_initialized(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=None,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.get("/api/v1/reconciliation/status")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["startup_complete"] is False
        assert data["dry_run"] is False
        assert data["message"] == "Reconciliation service not initialized"

    def test_status_with_service_returns_state(self) -> None:
        service = MagicMock()
        service.is_startup_complete.return_value = True
        service.startup_elapsed_seconds.return_value = 12.5
        service.startup_timed_out.return_value = False
        service.override_active.return_value = True
        service.override_context.return_value = {"reason": "manual"}

        ctx = create_mock_context(
            reconciliation_service=service,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.get("/api/v1/reconciliation/status")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["startup_complete"] is True
        assert data["dry_run"] is False
        assert data["startup_elapsed_seconds"] == 12.5
        assert data["startup_timed_out"] is False
        assert data["override_active"] is True
        assert data["override_context"] == {"reason": "manual"}


class TestRunReconciliation:
    def test_run_reconciliation_dry_run_skips(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=MagicMock(),
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)

        client = _build_test_client(ctx, config)
        response = client.post("/api/v1/reconciliation/run")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "skipped",
            "message": "DRY_RUN mode - reconciliation disabled",
        }

    def test_run_reconciliation_missing_service_returns_503(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=None,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post("/api/v1/reconciliation/run")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "Reconciliation service not initialized"

    def test_run_reconciliation_invokes_service(self) -> None:
        service = MagicMock()
        service.run_reconciliation_once = AsyncMock()

        ctx = create_mock_context(
            reconciliation_service=service,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post("/api/v1/reconciliation/run")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "ok",
            "message": "Reconciliation run complete",
        }
        service.run_reconciliation_once.assert_awaited_once_with("manual")


class TestFillsBackfill:
    def test_fills_backfill_dry_run_skips(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=MagicMock(),
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)

        client = _build_test_client(ctx, config)
        response = client.post("/api/v1/reconciliation/fills-backfill")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "skipped",
            "message": "DRY_RUN mode - reconciliation disabled",
        }

    def test_fills_backfill_missing_service_returns_503(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=None,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post("/api/v1/reconciliation/fills-backfill")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "Reconciliation service not initialized"

    def test_fills_backfill_defaults(self) -> None:
        service = MagicMock()
        service.run_fills_backfill_once = AsyncMock(return_value={"fills": 3})

        ctx = create_mock_context(
            reconciliation_service=service,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post("/api/v1/reconciliation/fills-backfill")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "ok",
            "message": "Fills backfill complete",
            "result": {"fills": 3},
        }
        service.run_fills_backfill_once.assert_awaited_once_with(
            lookback_hours=None,
            recalc_all_trades=False,
        )

    def test_fills_backfill_payload_overrides(self) -> None:
        service = MagicMock()
        service.run_fills_backfill_once = AsyncMock(return_value={"fills": 5})

        ctx = create_mock_context(
            reconciliation_service=service,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post(
            "/api/v1/reconciliation/fills-backfill",
            json={"lookback_hours": 48, "recalc_all_trades": True},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "ok"
        service.run_fills_backfill_once.assert_awaited_once_with(
            lookback_hours=48,
            recalc_all_trades=True,
        )


class TestForceComplete:
    def test_force_complete_dry_run_skips(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=MagicMock(),
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)

        client = _build_test_client(ctx, config)
        response = client.post(
            "/api/v1/reconciliation/force-complete",
            json={"reason": "testing"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "skipped",
            "message": "DRY_RUN mode - reconciliation disabled",
        }

    def test_force_complete_missing_service_returns_503(self) -> None:
        ctx = create_mock_context(
            reconciliation_service=None,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post(
            "/api/v1/reconciliation/force-complete",
            json={"reason": "testing"},
        )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "Reconciliation service not initialized"

    def test_force_complete_marks_override(self) -> None:
        service = MagicMock()

        ctx = create_mock_context(
            reconciliation_service=service,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=False)

        client = _build_test_client(ctx, config)
        response = client.post(
            "/api/v1/reconciliation/force-complete",
            json={"reason": "operator override"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "override_enabled"
        assert data["user_id"] == "user-1"
        assert data["reason"] == "operator override"
        service.mark_startup_complete.assert_called_once_with(
            forced=True,
            user_id="user-1",
            reason="operator override",
        )
