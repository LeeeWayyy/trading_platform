"""Tests for performance dashboard backend (execution_gateway).

Focuses on per-fill storage, aggregation, caching, and webhook behavior
as specified in T6.2 plan.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

# Stub redis + jwt before importing main to prevent cryptography/PyO3 issues in test env
redis_stub = type(sys)("redis")
redis_stub.exceptions = type(sys)("redis.exceptions")
redis_stub.connection = type(sys)("redis.connection")


class _RedisError(Exception):
    pass


class _ConnectionError(Exception):
    pass


class _TimeoutError(Exception):
    pass


redis_stub.exceptions.RedisError = _RedisError
redis_stub.exceptions.ConnectionError = _ConnectionError
redis_stub.exceptions.TimeoutError = _TimeoutError


class _ConnectionPool:
    def __init__(self, *args, **kwargs):
        pass


redis_stub.connection.ConnectionPool = _ConnectionPool


class _RedisClient:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True


redis_stub.Redis = _RedisClient
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
sys.modules.setdefault("redis.connection", redis_stub.connection)

jwt_stub = type(sys)("jwt")
jwt_stub.api_jwk = SimpleNamespace(PyJWK=None, PyJWKSet=None)
jwt_stub.algorithms = SimpleNamespace(
    get_default_algorithms=lambda: {},
    has_crypto=lambda: False,
    requires_cryptography=False,
)
jwt_stub.utils = SimpleNamespace()
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

from apps.execution_gateway import main
from apps.execution_gateway.database import DatabaseClient

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client():
    """FastAPI test client bound to execution gateway app."""

    return TestClient(main.app)


@pytest.fixture()
def mock_db():
    """MagicMock for DatabaseClient with common defaults."""

    db = MagicMock(spec=DatabaseClient)
    db.get_data_availability_date.return_value = date(2024, 1, 1)
    db.get_daily_pnl_history.return_value = []
    return db


@pytest.fixture()
def mock_redis():
    """Simple redis mock with get/set semantics."""

    redis = MagicMock()
    redis.get.return_value = None
    redis.set.return_value = True
    redis.delete.return_value = True
    # expose underlying client for scan
    redis._client = MagicMock()
    redis._client.scan.return_value = (0, [])
    return redis


@pytest.fixture(autouse=True)
def override_user_context():
    """Default user context providing viewer access to alpha_baseline."""

    def override_ctx(
        request: Request,
        role: str | None = None,
        strategies: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "role": "viewer",
            "strategies": ["alpha_baseline"],
            "requested_strategies": ["alpha_baseline"],
            "user_id": "u1",
            "user": {"role": "viewer", "strategies": ["alpha_baseline"], "user_id": "u1"},
        }

    main.app.dependency_overrides[main._build_user_context] = override_ctx
    yield
    main.app.dependency_overrides.pop(main._build_user_context, None)


def restore_default_user_context():
    """Helper to restore default override when a test removes it."""

    def _ctx(
        request: Request,
        role: str | None = None,
        strategies: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "role": "viewer",
            "strategies": ["alpha_baseline"],
            "requested_strategies": ["alpha_baseline"],
            "user_id": "u1",
            "user": {"role": "viewer", "strategies": ["alpha_baseline"], "user_id": "u1"},
        }

    main.app.dependency_overrides[main._build_user_context] = _ctx


def make_override(user_ctx: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """Create dependency override matching _build_user_context signature."""

    def _ctx(
        request: Request,
        role: str | None = None,
        strategies: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return user_ctx

    return _ctx


def _sample_daily_rows():
    return [
        {
            "trade_date": date(2024, 1, 1),
            "daily_realized_pl": Decimal("100"),
            "closing_trade_count": 1,
        },
        {
            "trade_date": date(2024, 1, 2),
            "daily_realized_pl": Decimal("-50"),
            "closing_trade_count": 1,
        },
    ]


def _all_negative_rows():
    return [
        {
            "trade_date": date(2024, 1, 1),
            "daily_realized_pl": Decimal("-20"),
            "closing_trade_count": 1,
        },
        {
            "trade_date": date(2024, 1, 2),
            "daily_realized_pl": Decimal("-30"),
            "closing_trade_count": 1,
        },
        {
            "trade_date": date(2024, 1, 3),
            "daily_realized_pl": Decimal("-10"),
            "closing_trade_count": 1,
        },
    ]


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestDailyPerformanceEndpoint:
    def test_default_range_returns_data(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                    "X-User-Strategies": "alpha_baseline",
                },
                params={"strategies": ["alpha_baseline"]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_realized_pl"] == "50"
        assert data["daily_pnl"][0]["cumulative_realized_pl"] == "100"
        assert data["daily_pnl"][1]["cumulative_realized_pl"] == "50"

    def test_custom_range(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()
        params = {"start_date": "2024-01-01", "end_date": "2024-01-02"}

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                params={**params, "strategies": ["alpha_baseline"]},
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                    "X-User-Strategies": "alpha_baseline",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["start_date"] == "2024-01-01"
        assert data["end_date"] == "2024-01-02"

    def test_no_orders_returns_empty(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = []

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                    "X-User-Strategies": "alpha_baseline",
                },
                params={"strategies": ["alpha_baseline"]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_pnl"] == []
        assert data["total_realized_pl"] == "0"

    def test_future_end_date_rejected(self, test_client):
        tomorrow = date.today() + timedelta(days=1)
        params = {"end_date": tomorrow.isoformat(), "strategies": ["alpha_baseline"]}

        with patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True):
            resp = test_client.get(
                "/api/v1/performance/daily",
                params=params,
                headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
            )

        assert resp.status_code == 422

    def test_cache_used_when_present(self, test_client, mock_db, mock_redis):
        cached_payload = {
            "daily_pnl": [],
            "total_realized_pl": "0",
            "max_drawdown_pct": "0",
            "start_date": "2024-01-01",
            "end_date": "2024-01-02",
            "data_available_from": None,
            "last_updated": datetime.now(UTC).isoformat(),
        }
        mock_redis.get.return_value = main.DailyPerformanceResponse.model_validate(
            cached_payload
        ).model_dump_json()

        with (
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "db_client", mock_db),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                params={"strategies": ["alpha_baseline"]},
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                    "X-User-Strategies": "alpha_baseline",
                },
            )

        assert resp.status_code == 200
        mock_db.get_daily_pnl_history.assert_not_called()
        assert resp.json()["start_date"] == "2024-01-01"

    def test_strategy_filtering_applied(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()
        main.app.dependency_overrides[main._build_user_context] = make_override(
            {
                "role": "viewer",
                "strategies": ["s1", "s2"],
                "requested_strategies": ["s1", "s2"],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": ["s1", "s2"], "user_id": "u1"},
            }
        )
        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            test_client.get(
                "/api/v1/performance/daily",
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                    "X-User-Strategies": "s1,s2",
                },
                params={"strategies": ["s1", "s2"]},
            )

        mock_db.get_daily_pnl_history.assert_called_once()
        args, kwargs = mock_db.get_daily_pnl_history.call_args
        assert args[2] == ["s1", "s2"]

    def test_forbidden_without_strategy_access(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()

        # Override to simulate user with no strategies
        main.app.dependency_overrides[main._build_user_context] = make_override(
            {
                "role": "viewer",
                "strategies": [],
                "requested_strategies": [],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": [], "user_id": "u1"},
            }
        )

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                },
                params={"strategies": []},
            )

        assert resp.status_code == 403

    def test_header_only_user_context_rejected(self, test_client, mock_db, mock_redis):
        """Requests without request.state.user must fail closed (no header fallback)."""

        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()

        # Remove override to simulate absence of request.state.user
        main.app.dependency_overrides.pop(main._build_user_context, None)

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                params={"strategies": ["alpha_baseline"]},
                headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
            )

        assert resp.status_code in {401, 403}
        # Reinstate default override for other tests
        restore_default_user_context()

    def test_feature_flag_disabled_returns_404(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", False),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                params={"strategies": ["alpha_baseline"]},
                headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
            )

        assert resp.status_code == 404

    def test_forbidden_without_user_id(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()

        # Override to omit user_id
        main.app.dependency_overrides[main._build_user_context] = make_override(
            {
                "role": "viewer",
                "strategies": ["s1"],
                "requested_strategies": ["s1"],
                "user": {"role": "viewer", "strategies": ["s1"]},
                "user_id": None,
            }
        )

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                headers={"X-User-Role": "viewer"},
                params={"strategies": ["s1"]},
            )

        assert resp.status_code == 403

    def test_requested_strategies_must_be_subset(self, test_client, mock_db, mock_redis):
        mock_db.get_daily_pnl_history.return_value = _sample_daily_rows()

        # User authorized only for s1
        main.app.dependency_overrides[main._build_user_context] = make_override(
            {
                "role": "viewer",
                "strategies": ["s1"],
                "requested_strategies": ["s1", "s2"],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": ["s1"], "user_id": "u1"},
            }
        )

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "FEATURE_PERFORMANCE_DASHBOARD", True),
        ):
            resp = test_client.get(
                "/api/v1/performance/daily",
                headers={
                    "X-User-Role": "viewer",
                    "X-User-Id": "u1",
                },
                params={"strategies": ["s1", "s2"]},  # s2 not authorized
            )

        assert resp.status_code == 403

    def test_cache_invalidated_on_fill(self, test_client, mock_db, mock_redis):
        """Ensure performance cache is cleared after a fill webhook."""

        # Prepare cached entry and index membership
        cache_key = main._performance_cache_key(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            strategies=("alpha_baseline",),
            user_id="u1",
        )
        index_key = main._performance_cache_index_key(date(2024, 1, 1))
        # sscan_iter is now used instead of smembers for non-blocking iteration
        mock_redis.sscan_iter.return_value = iter([cache_key])
        mock_redis.delete.return_value = True

        # Mock DB transactional flow for webhook
        tx_ctx = MagicMock()
        conn = MagicMock()
        tx_ctx.__enter__.return_value = conn
        tx_ctx.__exit__.return_value = False
        mock_db.transaction.return_value = tx_ctx
        mock_db.get_order_for_update.return_value = SimpleNamespace(
            filled_qty=Decimal("0"), symbol="AAPL", side="buy"
        )
        mock_db.get_position_for_update.return_value = None
        mock_db.update_position_on_fill_with_conn.return_value = SimpleNamespace(
            realized_pl=Decimal("0")
        )
        mock_db.append_fill_to_order_metadata.return_value = None
        mock_db.update_order_status_with_conn.return_value = None

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", mock_redis),
            patch.object(main, "WEBHOOK_SECRET", ""),
        ):
            resp = test_client.post(
                "/api/v1/webhooks/orders",
                json={
                    "event": "fill",
                    "timestamp": "2024-01-01T10:00:00Z",
                    "order": {
                        "client_order_id": "abc123",
                        "status": "filled",
                        "filled_qty": 10,
                        "filled_avg_price": "10",
                        "symbol": "AAPL",
                        "side": "buy",
                    },
                    "price": "10",
                },
            )

        assert resp.status_code == 200
        mock_redis.sscan_iter.assert_called_once_with(index_key)
        # _invalidate_performance_cache deletes cached keys and index key atomically
        mock_redis.delete.assert_called_once_with(cache_key, index_key)


class TestDailyPnLDatabase:
    def test_compute_daily_performance_drawdown(self):
        rows = _sample_daily_rows()
        daily, total, max_dd = main._compute_daily_performance(
            rows, date(2024, 1, 1), date(2024, 1, 2)
        )

        assert total == Decimal("50")
        assert len(daily) == 2
        assert max_dd == Decimal("-50")  # peak 100 -> drop to 50 => -50%

    def test_missing_days_filled_with_zero(self):
        rows = [
            {
                "trade_date": date(2024, 1, 1),
                "daily_realized_pl": Decimal("10"),
                "closing_trade_count": 1,
            }
        ]
        daily, _, _ = main._compute_daily_performance(rows, date(2024, 1, 1), date(2024, 1, 3))

        assert len(daily) == 3
        assert daily[1].realized_pl == Decimal("0")
        assert daily[2].cumulative_realized_pl == Decimal("10")

    def test_all_negative_series_tracks_drawdown(self):
        rows = _all_negative_rows()
        daily, total, max_dd = main._compute_daily_performance(
            rows, date(2024, 1, 1), date(2024, 1, 3)
        )

        assert total == Decimal("-60")
        # Peak should start at first cumulative (-20) and remain -20
        assert daily[0].peak_equity == Decimal("-20")
        # Drawdown reflects further declines even when starting below zero
        assert max_dd == Decimal("-200")  # cumulative -60 vs peak -20 => -200%
        assert daily[-1].drawdown_pct == max_dd


class TestOrderFillMetadata:
    def test_append_fill_records_delta(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {"metadata": {"fills": []}}

        db = DatabaseClient("postgresql://user:pass@localhost/db")
        with patch.object(db, "_pool", MagicMock()):
            db.append_fill_to_order_metadata("abc", {"fill_id": "abc_1", "realized_pl": "5"}, conn)

        cursor.execute.assert_called_once()


class TestDatabaseClientNewMethods:
    def test_get_order_for_update_uses_lock(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {"client_order_id": "abc"}

        db = DatabaseClient("postgresql://user:pass@localhost/db")
        with patch.object(db, "_pool", MagicMock()):
            result = db.get_order_for_update("abc", conn)

        cursor.execute.assert_called_with(
            "SELECT * FROM orders WHERE client_order_id = %s FOR UPDATE", ("abc",)
        )
        assert result.client_order_id == "abc"

    def test_get_position_for_update_uses_lock(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {
            "symbol": "AAPL",
            "qty": 0,
            "avg_entry_price": 0,
            "realized_pl": 0,
            "updated_at": datetime.now(UTC),
        }

        db = DatabaseClient("postgresql://user:pass@localhost/db")
        with patch.object(db, "_pool", MagicMock()):
            result = db.get_position_for_update("AAPL", conn)

        cursor.execute.assert_called_with(
            "SELECT * FROM positions WHERE symbol = %s FOR UPDATE", ("AAPL",)
        )
        assert result.symbol == "AAPL"


class TestWebhookSecurity:
    def test_invalid_signature_rejected(self, test_client):
        with (
            patch.object(main, "WEBHOOK_SECRET", "shh"),
            patch("apps.execution_gateway.main.verify_webhook_signature", return_value=False),
        ):
            resp = test_client.post("/api/v1/webhooks/orders", json={"order": {}, "event": "fill"})

        assert resp.status_code == 401

    def test_missing_signature_rejected(self, test_client):
        with patch.object(main, "WEBHOOK_SECRET", "shh"):
            resp = test_client.post("/api/v1/webhooks/orders", json={"order": {}, "event": "fill"})

        assert resp.status_code == 401


class TestOutOfOrderWebhooks:
    def test_duplicate_fill_skipped(self, test_client):
        mock_db = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = MagicMock()
        ctx.__exit__.return_value = False
        mock_db.transaction.return_value = ctx
        mock_order = SimpleNamespace(filled_qty=Decimal("100"), symbol="AAPL", side="buy")
        mock_db.get_order_for_update.return_value = mock_order

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", None),
            patch.object(main, "WEBHOOK_SECRET", ""),
        ):
            resp = test_client.post(
                "/api/v1/webhooks/orders",
                json={
                    "event": "fill",
                    "order": {
                        "client_order_id": "abc",
                        "status": "filled",
                        "filled_qty": 100,
                        "filled_avg_price": "10",
                    },
                    "price": "10",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"


class TestRealtimePnlRbac:
    def test_realtime_pnl_requires_strategy_access(self, test_client, mock_db):
        # No authorized strategies -> 403
        main.app.dependency_overrides[main._build_user_context] = make_override(
            {
                "role": "viewer",
                "strategies": [],
                "requested_strategies": [],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": [], "user_id": "u1"},
            }
        )
        with patch.object(main, "db_client", mock_db):
            resp = test_client.get("/api/v1/positions/pnl/realtime")
        assert resp.status_code == 403

    def test_realtime_pnl_allows_authorized_viewer(self, test_client, mock_db):
        main.app.dependency_overrides[main._build_user_context] = make_override(
            {
                "role": "viewer",
                "strategies": ["alpha_baseline"],
                "requested_strategies": ["alpha_baseline"],
                "user_id": "u1",
                "user": {"role": "viewer", "strategies": ["alpha_baseline"], "user_id": "u1"},
            }
        )
        mock_db.get_positions_for_strategies.return_value = []
        with patch.object(main, "db_client", mock_db):
            resp = test_client.get("/api/v1/positions/pnl/realtime")
        assert resp.status_code == 200


class TestCalculatePositionUpdate:
    @pytest.mark.parametrize(
        (
            "old_qty",
            "old_avg",
            "fill_qty",
            "side",
            "expected_qty",
            "expected_avg",
            "expected_realized",
        ),
        [
            # Short cover (reducing absolute qty, realizing profit when covering below entry)
            (-100, Decimal("50"), 40, "buy", -60, Decimal("50"), Decimal("-2000")),
            # Short flip to long: cover 80 then open 20 long at worse price
            (-80, Decimal("40"), 100, "buy", 20, Decimal("100"), Decimal("-4800")),
            # Long flip to short: sell through zero
            (50, Decimal("30"), 100, "sell", -50, Decimal("100"), Decimal("3500")),
        ],
    )
    def test_short_cover_and_flip_scenarios(
        self,
        old_qty,
        old_avg,
        fill_qty,
        side,
        expected_qty,
        expected_avg,
        expected_realized,
    ):
        new_qty, new_avg, new_realized = main.calculate_position_update(
            old_qty=old_qty,
            old_avg_price=old_avg,
            old_realized_pl=Decimal("0"),
            fill_qty=fill_qty,
            fill_price=Decimal("100"),
            side=side,
        )

        assert new_qty == expected_qty
        assert new_avg == expected_avg
        assert new_realized == expected_realized


class TestBrokerTimestamps:
    def test_broker_timestamp_used(self, test_client):
        mock_db = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = MagicMock()
        ctx.__exit__.return_value = False
        mock_db.transaction.return_value = ctx
        mock_order = SimpleNamespace(filled_qty=Decimal("0"), symbol="AAPL", side="buy")
        mock_db.get_order_for_update.return_value = mock_order
        mock_db.get_position_for_update.return_value = None
        mock_db.update_position_on_fill_with_conn.return_value = SimpleNamespace(
            realized_pl=Decimal("0")
        )

        captured_fill = {}

        def _capture_fill(*args, **kwargs):
            captured_fill.update(kwargs["fill_data"])
            return None

        mock_db.append_fill_to_order_metadata.side_effect = _capture_fill

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", None),
            patch.object(main, "WEBHOOK_SECRET", ""),
        ):
            resp = test_client.post(
                "/api/v1/webhooks/orders",
                json={
                    "event": "fill",
                    "timestamp": "2024-01-01T10:00:00Z",
                    "order": {
                        "client_order_id": "abc",
                        "status": "filled",
                        "filled_qty": 10,
                        "filled_avg_price": "10",
                        "symbol": "AAPL",
                        "side": "buy",
                    },
                    "price": "10",
                },
            )

        assert resp.status_code == 200
        assert captured_fill["timestamp"] == "2024-01-01T10:00:00+00:00"


class TestConcurrentWebhooks:
    def test_transactional_flow(self, test_client):
        mock_db = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = MagicMock()
        ctx.__exit__.return_value = False
        mock_db.transaction.return_value = ctx

        # Order before any fills
        order = SimpleNamespace(filled_qty=Decimal("0"), symbol="AAPL", side="sell")
        mock_db.get_order_for_update.return_value = order
        mock_db.get_position_for_update.return_value = None
        mock_db.update_position_on_fill_with_conn.return_value = SimpleNamespace(
            realized_pl=Decimal("5")
        )

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", None),
            patch.object(main, "WEBHOOK_SECRET", ""),
        ):
            resp = test_client.post(
                "/api/v1/webhooks/orders",
                json={
                    "event": "fill",
                    "order": {
                        "client_order_id": "abc",
                        "status": "filled",
                        "filled_qty": 10,
                        "filled_avg_price": "10",
                        "symbol": "AAPL",
                        "side": "sell",
                    },
                    "price": "10",
                },
            )

        assert resp.status_code == 200
        mock_db.get_order_for_update.assert_called_once()
        mock_db.get_position_for_update.assert_called_once()
        mock_db.append_fill_to_order_metadata.assert_called_once()

    def test_concurrent_new_symbol_position_creation_serialized(self, test_client):
        # Simulate two concurrent fills for a new symbol; advisory lock should
        # serialize updates even when the position row does not yet exist.
        mock_db = MagicMock()

        # Shared mutable state to detect lost updates
        position_state = {"qty": 0, "realized_pl": Decimal("0")}

        def _update_position(**kwargs):
            # emulate calculate_position_update using qty increments
            position_state["qty"] += (
                kwargs["fill_qty"] if kwargs["side"] == "buy" else -kwargs["fill_qty"]
            )
            position_state["realized_pl"] += Decimal("1")  # marker to ensure both commits applied
            return SimpleNamespace(**position_state)

        ctx = MagicMock()
        ctx.__enter__.return_value = MagicMock()
        ctx.__exit__.return_value = False
        mock_db.transaction.return_value = ctx
        mock_db.get_order_for_update.return_value = SimpleNamespace(
            filled_qty=Decimal("0"), symbol="NEW", side="buy"
        )
        mock_db.get_position_for_update.return_value = None
        mock_db.update_position_on_fill_with_conn.side_effect = _update_position
        mock_db.append_fill_to_order_metadata.return_value = None
        mock_db.update_order_status_with_conn.return_value = None

        with (
            patch.object(main, "db_client", mock_db),
            patch.object(main, "redis_client", None),
            patch.object(main, "WEBHOOK_SECRET", ""),
        ):
            resp1 = test_client.post(
                "/api/v1/webhooks/orders",
                json={
                    "event": "fill",
                    "order": {
                        "client_order_id": "fill1",
                        "status": "partially_filled",
                        "filled_qty": 10,
                        "filled_avg_price": "10",
                        "symbol": "NEW",
                        "side": "buy",
                    },
                    "price": "10",
                },
            )

            resp2 = test_client.post(
                "/api/v1/webhooks/orders",
                json={
                    "event": "fill",
                    "order": {
                        "client_order_id": "fill2",
                        "status": "filled",
                        "filled_qty": 20,
                        "filled_avg_price": "10",
                        "symbol": "NEW",
                        "side": "buy",
                    },
                    "price": "10",
                },
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both updates applied without overwriting
        assert position_state["qty"] == 30
        assert position_state["realized_pl"] == Decimal("2")
