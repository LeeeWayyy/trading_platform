"""Tests for position routes in apps/execution_gateway/routes/positions.py."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.execution_gateway.app_factory import create_mock_context, create_test_config
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.routes import positions
from apps.execution_gateway.schemas import Position
from libs.core.common.api_auth_dependency import AuthContext
from libs.trading.risk_management import RiskConfig


def _mock_auth_context() -> AuthContext:
    return AuthContext(
        user=None,
        internal_claims=None,
        auth_type="test",
        is_authenticated=True,
    )


def _mock_user_context(_request: Request) -> dict[str, Any]:
    return {
        "role": "viewer",
        "strategies": ["alpha_baseline"],
        "requested_strategies": ["alpha_baseline"],
        "user_id": "user-1",
        "user": {
            "role": "viewer",
            "strategies": ["alpha_baseline"],
            "user_id": "user-1",
        },
    }


def _build_test_app(ctx: Any, config: Any) -> TestClient:
    app = FastAPI()
    app.include_router(positions.router)

    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[positions.order_read_auth] = _mock_auth_context
    app.dependency_overrides[positions.build_user_context] = _mock_user_context

    return TestClient(app)


class TestGetPositions:
    def test_get_positions_calculates_totals(self) -> None:
        pos_a = Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("155"),
            unrealized_pl=None,
            realized_pl=Decimal("0"),
            updated_at=datetime.now(UTC),
        )
        pos_b = Position(
            symbol="MSFT",
            qty=Decimal("5"),
            avg_entry_price=Decimal("300"),
            current_price=Decimal("290"),
            unrealized_pl=Decimal("-50"),
            realized_pl=Decimal("10"),
            updated_at=datetime.now(UTC),
        )

        db = MagicMock()
        db.get_all_positions.return_value = [pos_a, pos_b]

        ctx = create_mock_context(
            db=db,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.get("/api/v1/positions")

        assert response.status_code == 200
        data = response.json()
        assert data["total_positions"] == 2
        assert Decimal(data["total_realized_pl"]) == Decimal("10")
        assert Decimal(data["total_unrealized_pl"]) == Decimal("0")

        aapl = next(p for p in data["positions"] if p["symbol"] == "AAPL")
        assert Decimal(aapl["unrealized_pl"]) == Decimal("50")


class TestPerformanceAndAccount:
    def test_daily_performance_disabled_returns_404(self) -> None:
        db = MagicMock()
        ctx = create_mock_context(
            db=db,
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.get("/api/v1/performance/daily")

        assert response.status_code == 404
        assert response.json()["detail"] == "Performance dashboard disabled"

    def test_account_info_dry_run_empty(self) -> None:
        ctx = create_mock_context(
            db=MagicMock(),
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        response = client.get("/api/v1/account")

        assert response.status_code == 200
        data = response.json()
        assert data["account_number"] is None
        assert data["buying_power"] is None
        assert data["cash"] is None


class TestMarketPrices:
    def test_market_prices_returns_points(self) -> None:
        pos_a = Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150"),
            current_price=Decimal("155"),
            unrealized_pl=Decimal("50"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(UTC),
        )
        pos_b = Position(
            symbol="MSFT",
            qty=Decimal("5"),
            avg_entry_price=Decimal("300"),
            current_price=Decimal("290"),
            unrealized_pl=Decimal("-50"),
            realized_pl=Decimal("10"),
            updated_at=datetime.now(UTC),
        )

        db = MagicMock()
        db.get_positions_for_strategies.return_value = [pos_a, pos_b]

        ctx = create_mock_context(
            db=db,
            redis=MagicMock(),
            recovery_manager=MagicMock(),
            risk_config=RiskConfig(),
        )
        config = create_test_config(dry_run=True)
        client = _build_test_app(ctx, config)

        with patch(
            "apps.execution_gateway.routes.positions.batch_fetch_realtime_prices_from_redis"
        ) as batch_fetch:
            batch_fetch.return_value = {
                "AAPL": (Decimal("155"), datetime.now(UTC)),
                "MSFT": (Decimal("290"), datetime.now(UTC)),
            }

            response = client.get("/api/v1/market_prices")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        symbols = {point["symbol"] for point in data}
        assert symbols == {"AAPL", "MSFT"}
