"""Tests for Strategy Status API endpoints.

Tests the /api/v1/strategies and /api/v1/strategies/{strategy_id} endpoints
for monitoring strategy state.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

# Stub redis + jwt before importing main to prevent cryptography/PyO3 issues in test env
redis_stub = ModuleType("redis")
redis_stub.exceptions = ModuleType("redis.exceptions")
redis_stub.connection = ModuleType("redis.connection")
redis_stub.asyncio = ModuleType("redis.asyncio")
redis_stub.lock = ModuleType("redis.lock")


class _RedisError(Exception):
    pass


class _ConnectionPool:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def disconnect(self) -> None:
        pass


redis_stub.exceptions.RedisError = _RedisError
redis_stub.exceptions.ConnectionError = _RedisError  # type: ignore[attr-defined]
redis_stub.exceptions.TimeoutError = _RedisError  # type: ignore[attr-defined]
redis_stub.connection.ConnectionPool = _ConnectionPool  # type: ignore[attr-defined]


class _RedisClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def ping(self) -> bool:
        return True


class _AsyncRedis:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @classmethod
    def from_url(cls, *args: Any, **kwargs: Any) -> _AsyncRedis:
        return cls()


class _Lock:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class _StubAlgorithm:
    pass


redis_stub.Redis = _RedisClient  # type: ignore[attr-defined]
redis_stub.asyncio.Redis = _AsyncRedis  # type: ignore[attr-defined]
redis_stub.lock.Lock = _Lock  # type: ignore[attr-defined]
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
sys.modules.setdefault("redis.connection", redis_stub.connection)
sys.modules.setdefault("redis.asyncio", redis_stub.asyncio)
sys.modules.setdefault("redis.lock", redis_stub.lock)

jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = SimpleNamespace(PyJWK=None, PyJWKSet=None)  # type: ignore[attr-defined]
jwt_stub.algorithms = SimpleNamespace(  # type: ignore[attr-defined]
    get_default_algorithms=lambda: {},
    has_crypto=lambda: False,
    requires_cryptography=False,
    ECAlgorithm=_StubAlgorithm,
    RSAAlgorithm=_StubAlgorithm,
)
jwt_stub.utils = SimpleNamespace()  # type: ignore[attr-defined]
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)  # type: ignore[arg-type]
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)  # type: ignore[arg-type]
sys.modules.setdefault("jwt.utils", jwt_stub.utils)  # type: ignore[arg-type]

from apps.execution_gateway import main
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.services.auth_helpers import build_user_context

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client() -> TestClient:
    """FastAPI test client bound to execution gateway app."""
    return TestClient(main.app)


@pytest.fixture()
def mock_db() -> MagicMock:
    """MagicMock for DatabaseClient with common defaults."""
    db = MagicMock(spec=DatabaseClient)
    db.get_all_strategy_ids.return_value = []
    db.get_strategy_status.return_value = None
    db.get_bulk_strategy_status.return_value = {}
    return db


@pytest.fixture(autouse=True)
def override_user_context() -> Any:
    """Default user context providing viewer access to test strategies."""
    # Include all strategy names used in tests
    all_test_strategies = [
        "alpha_baseline",
        "momentum_v2",
        "recent_strategy",
        "old_strategy",
        "good_strategy",
        "bad_strategy",
        "dormant_strategy",
        "active_orders",
        "momentum_reversion_v2",
        "nonexistent",
    ]

    def override_ctx(
        request: Request,
        role: str | None = None,
        strategies: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "role": "viewer",
            "strategies": all_test_strategies,
            "requested_strategies": all_test_strategies,
            "user_id": "u1",
            "user": {"role": "viewer", "strategies": all_test_strategies, "user_id": "u1"},
        }

    main.app.dependency_overrides[build_user_context] = override_ctx
    yield
    main.app.dependency_overrides.pop(build_user_context, None)


# ---------------------------------------------------------------------------
# GET /api/v1/strategies tests
# ---------------------------------------------------------------------------


class TestListStrategies:
    """Tests for the list strategies endpoint."""

    def test_list_strategies_empty(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns empty list when no strategies exist."""
        mock_db.get_all_strategy_ids.return_value = []

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert data["strategies"] == []
        assert data["total_count"] == 0
        assert "timestamp" in data

    def test_list_strategies_single(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns single strategy with correct status fields."""
        now = datetime.now(UTC)
        mock_db.get_all_strategy_ids.return_value = ["alpha_baseline"]
        mock_db.get_bulk_strategy_status.return_value = {
            "alpha_baseline": {
                "positions_count": 3,
                "open_orders_count": 1,
                "today_pnl": Decimal("150.50"),
                "last_signal_at": now - timedelta(hours=2),
            }
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert len(data["strategies"]) == 1

        strategy = data["strategies"][0]
        assert strategy["strategy_id"] == "alpha_baseline"
        assert strategy["name"] == "Alpha Baseline"
        assert strategy["status"] == "active"
        assert strategy["positions_count"] == 3
        assert strategy["open_orders_count"] == 1
        assert strategy["today_pnl"] == "150.50"

    def test_list_strategies_multiple(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns multiple strategies correctly."""
        now = datetime.now(UTC)
        mock_db.get_all_strategy_ids.return_value = ["alpha_baseline", "momentum_v2"]
        mock_db.get_bulk_strategy_status.return_value = {
            "alpha_baseline": {
                "positions_count": 2,
                "open_orders_count": 0,
                "today_pnl": Decimal("100.00"),
                "last_signal_at": now - timedelta(hours=1),
            },
            "momentum_v2": {
                "positions_count": 0,
                "open_orders_count": 0,
                "today_pnl": None,
                "last_signal_at": now - timedelta(days=2),
            },
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 2
        assert len(data["strategies"]) == 2

        # First strategy should be active (has positions)
        alpha = next(s for s in data["strategies"] if s["strategy_id"] == "alpha_baseline")
        assert alpha["status"] == "active"

        # Second strategy should be inactive (no positions, old signal)
        momentum = next(s for s in data["strategies"] if s["strategy_id"] == "momentum_v2")
        assert momentum["status"] == "inactive"

    def test_list_strategies_active_by_recent_signal(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Strategy is active if it has a recent signal (within 24h)."""
        now = datetime.now(UTC)
        mock_db.get_all_strategy_ids.return_value = ["recent_strategy"]
        mock_db.get_bulk_strategy_status.return_value = {
            "recent_strategy": {
                "positions_count": 0,
                "open_orders_count": 0,
                "today_pnl": None,
                "last_signal_at": now - timedelta(hours=12),  # Within 24h
            }
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert data["strategies"][0]["status"] == "active"

    def test_list_strategies_inactive_by_old_signal(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Strategy is inactive if signal is older than 24h."""
        now = datetime.now(UTC)
        mock_db.get_all_strategy_ids.return_value = ["old_strategy"]
        mock_db.get_bulk_strategy_status.return_value = {
            "old_strategy": {
                "positions_count": 0,
                "open_orders_count": 0,
                "today_pnl": None,
                "last_signal_at": now - timedelta(hours=25),  # Beyond 24h
            }
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies")

        assert response.status_code == 200
        data = response.json()
        assert data["strategies"][0]["status"] == "inactive"

    def test_list_strategies_handles_missing_strategy(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Handles case where bulk status returns None for a strategy."""
        mock_db.get_all_strategy_ids.return_value = ["good_strategy", "bad_strategy"]
        # Bulk query returns data for good_strategy but None for bad_strategy
        mock_db.get_bulk_strategy_status.return_value = {
            "good_strategy": {
                "positions_count": 1,
                "open_orders_count": 0,
                "today_pnl": None,
                "last_signal_at": datetime.now(UTC),
            },
            "bad_strategy": None,  # Simulates missing/invalid data
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies")

        # Should return 200 with only the good strategy
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["strategies"][0]["strategy_id"] == "good_strategy"


# ---------------------------------------------------------------------------
# GET /api/v1/strategies/{strategy_id} tests
# ---------------------------------------------------------------------------


class TestGetStrategyStatus:
    """Tests for the get strategy status endpoint."""

    def test_get_strategy_status_success(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns strategy status when found."""
        now = datetime.now(UTC)
        mock_db.get_strategy_status.return_value = {
            "positions_count": 5,
            "open_orders_count": 2,
            "today_pnl": Decimal("250.75"),
            "last_signal_at": now - timedelta(minutes=30),
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies/alpha_baseline")

        assert response.status_code == 200
        data = response.json()
        assert data["strategy_id"] == "alpha_baseline"
        assert data["name"] == "Alpha Baseline"
        assert data["status"] == "active"
        assert data["positions_count"] == 5
        assert data["open_orders_count"] == 2
        assert data["today_pnl"] == "250.75"
        assert "timestamp" in data

    def test_get_strategy_status_not_found(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns 404 when strategy not found."""
        mock_db.get_strategy_status.return_value = None

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies/nonexistent")

        assert response.status_code == 404
        data = response.json()
        assert "nonexistent" in data["detail"]

    def test_get_strategy_status_inactive(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns inactive status for strategy with no recent activity."""
        mock_db.get_strategy_status.return_value = {
            "positions_count": 0,
            "open_orders_count": 0,
            "today_pnl": None,
            "last_signal_at": None,
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies/dormant_strategy")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "inactive"

    def test_get_strategy_status_active_by_open_orders(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Strategy is active if it has open orders."""
        mock_db.get_strategy_status.return_value = {
            "positions_count": 0,
            "open_orders_count": 3,
            "today_pnl": None,
            "last_signal_at": None,
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies/active_orders")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"

    def test_get_strategy_name_formatting(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Strategy name is formatted from ID correctly."""
        mock_db.get_strategy_status.return_value = {
            "positions_count": 1,
            "open_orders_count": 0,
            "today_pnl": None,
            "last_signal_at": datetime.now(UTC),
        }

        with patch.object(main.app.state.context, "db", mock_db):
            response = test_client.get("/api/v1/strategies/momentum_reversion_v2")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Momentum Reversion V2"


# ---------------------------------------------------------------------------
# Authorization / 403 tests
# ---------------------------------------------------------------------------


class TestStrategyAuthorizationErrors:
    """Tests for authorization errors on strategy endpoints."""

    def test_list_strategies_403_when_no_strategies_authorized(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns 403 when user has no authorized strategies."""

        def override_ctx_no_strategies(
            request: Request,
            role: str | None = None,
            strategies: list[str] | None = None,
            user_id: str | None = None,
        ) -> dict[str, Any]:
            return {
                "role": "viewer",
                "strategies": [],
                "requested_strategies": [],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": [], "user_id": "u1"},
            }

        main.app.dependency_overrides[build_user_context] = override_ctx_no_strategies

        try:
            with patch.object(main.app.state.context, "db", mock_db):
                response = test_client.get("/api/v1/strategies")

            assert response.status_code == 403
            data = response.json()
            assert "access" in data["detail"].lower()
        finally:
            # Restore default override from autouse fixture
            pass

    def test_get_strategy_403_when_strategy_not_authorized(
        self,
        test_client: TestClient,
        mock_db: MagicMock,
    ) -> None:
        """Returns 403 when requesting a strategy user is not authorized to access."""

        def override_ctx_limited(
            request: Request,
            role: str | None = None,
            strategies: list[str] | None = None,
            user_id: str | None = None,
        ) -> dict[str, Any]:
            # User only has access to 'alpha_baseline', not 'momentum_v2'
            return {
                "role": "viewer",
                "strategies": ["alpha_baseline"],
                "requested_strategies": ["alpha_baseline"],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": ["alpha_baseline"], "user_id": "u1"},
            }

        main.app.dependency_overrides[build_user_context] = override_ctx_limited

        try:
            with patch.object(main.app.state.context, "db", mock_db):
                response = test_client.get("/api/v1/strategies/momentum_v2")

            assert response.status_code == 403
            data = response.json()
            assert "not authorized" in data["detail"].lower()
            assert "momentum_v2" in data["detail"]
        finally:
            # Restore default override from autouse fixture
            pass
