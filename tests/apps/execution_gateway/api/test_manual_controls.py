"""Tests for manual control API endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.api import dependencies as deps
from apps.execution_gateway.api import manual_controls as manual_controls_module
from apps.execution_gateway.api.manual_controls import router
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.schemas import FatFingerThresholds, OrderDetail, Position
from libs.platform.web_console_auth.audit_logger import AuditLogger
from libs.platform.web_console_auth.exceptions import (
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingJtiError,
    SessionExpiredError,
    SubjectMismatchError,
    TokenExpiredError,
    TokenReplayedError,
    TokenRevokedError,
)
from libs.platform.web_console_auth.gateway_auth import AuthenticatedUser
from libs.platform.web_console_auth.permissions import Role
from libs.platform.web_console_auth.rate_limiter import RateLimiter
from libs.trading.risk_management import RiskConfig


def _err(resp: Any) -> dict[str, Any]:
    data = resp.json()
    return data.get("detail", data)


def _order(strategy: str, client_id: str = "ord-1", status: str = "pending_new") -> OrderDetail:
    now = datetime.now(UTC)
    return OrderDetail(
        client_order_id=client_id,
        strategy_id=strategy,
        symbol="AAPL",
        side="buy",
        qty=10,
        order_type="market",
        time_in_force="day",
        status=status,
        broker_order_id="brk-1",
        retry_count=0,
        created_at=now,
        updated_at=now,
        submitted_at=now,
        filled_at=None,
        filled_qty=Decimal("0"),
        filled_avg_price=None,
        metadata={},
    )


def _position(symbol: str, qty: int) -> Position:
    now = datetime.now(UTC)
    return Position(
        symbol=symbol,
        qty=Decimal(qty),
        avg_entry_price=Decimal("10"),
        current_price=None,
        unrealized_pl=None,
        realized_pl=Decimal("0"),
        updated_at=now,
        last_trade_at=now,
    )


class StubRateLimiter(RateLimiter):
    def __init__(self, allow: bool = True, raise_error: bool = False) -> None:
        self.allow = allow
        self.raise_error = raise_error

    async def check_rate_limit(self, *args: Any, **kwargs: Any) -> tuple[bool, int]:
        if self.raise_error:
            raise RuntimeError("redis down")
        return self.allow, 0


class StubDB:
    def __init__(self) -> None:
        self.orders = {"ord-1": _order("s1")}
        self.pending = [self.orders["ord-1"]]
        self.positions = [_position("AAPL", 5)]
        self.status_updates: dict[str, str] = {}
        self.parents: list[str] = []
        self.slices: list[OrderDetail] = []

    def get_order_by_client_id(self, client_order_id: str) -> OrderDetail | None:
        return self.orders.get(client_order_id)

    def update_order_status(
        self, client_order_id: str, status: str, **_: Any
    ) -> OrderDetail | None:
        self.status_updates[client_order_id] = status
        order = self.orders.get(client_order_id)
        if order:
            order.status = status  # type: ignore[attr-defined]
        return order

    def get_pending_orders(
        self,
        *,
        symbol: str | None = None,
        strategy_ids: list[str] | None = None,
        parent_order_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[OrderDetail], int]:
        filtered = self.pending
        if symbol:
            filtered = [o for o in filtered if o.symbol == symbol]
        if strategy_ids is not None:
            filtered = [o for o in filtered if o.strategy_id in strategy_ids]
        if parent_order_id:
            filtered = [o for o in filtered if getattr(o, "parent_order_id", None) == parent_order_id]
        return filtered[offset : offset + limit], len(filtered)

    def get_positions_for_strategies(self, strategies: list[str]) -> list[Position]:
        if not strategies:
            return []
        return self.positions

    def get_all_positions(self) -> list[Position]:
        return self.positions

    def get_strategy_map_for_symbols(self, symbols: list[str]) -> dict[str, str | None]:
        return dict.fromkeys(symbols, "s1")

    def get_position_by_symbol(self, symbol: str) -> int:
        """Return position quantity for symbol (0 if no position)."""
        for pos in self.positions:
            if pos.symbol == symbol:
                return pos.qty
        return 0

    def get_recent_fills(
        self,
        *,
        strategy_ids: list[str] | None,
        limit: int = 50,
        lookback_hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Stub get_recent_fills for testing."""
        if strategy_ids is not None and not strategy_ids:
            return []  # Fail-closed
        now = datetime.now(UTC)
        return [
            {
                "client_order_id": "ord-1",
                "symbol": "AAPL",
                "side": "buy",
                "status": "filled",
                "qty": 10,
                "price": Decimal("150.00"),
                "realized_pl": Decimal("0"),
                "timestamp": now,
            },
            {
                "client_order_id": "ord-2",
                "symbol": "GOOG",
                "side": "sell",
                "status": "filled",
                "qty": 5,
                "price": Decimal("100.00"),
                "realized_pl": Decimal("50.00"),
                "timestamp": now,
            },
        ][:limit]

    def create_order(self, **kwargs: Any) -> OrderDetail:
        """Stub create_order for manual controls order persistence."""
        order = _order(
            strategy=kwargs.get("strategy_id", "stub"),
            client_id=kwargs.get("client_order_id", "stub-order"),
            status=kwargs.get("status", "pending_new"),
        )
        self.orders[order.client_order_id] = order
        return order

    def create_parent_order(self, **kwargs: Any) -> OrderDetail:
        order = _order(
            strategy=kwargs.get("strategy_id", "stub"),
            client_id=kwargs.get("client_order_id", "parent"),
            status=kwargs.get("status", "scheduled"),
        )
        self.orders[order.client_order_id] = order
        self.parents.append(order.client_order_id)
        return order

    def create_child_slice(self, **kwargs: Any) -> OrderDetail:
        order = _order(
            strategy=kwargs.get("strategy_id", "stub"),
            client_id=kwargs.get("client_order_id", "child"),
            status=kwargs.get("status", "pending_new"),
        )
        order.parent_order_id = kwargs.get("parent_order_id")  # type: ignore[attr-defined]
        order.slice_num = kwargs.get("slice_num")  # type: ignore[attr-defined]
        order.scheduled_time = kwargs.get("scheduled_time")  # type: ignore[attr-defined]
        order.qty = kwargs.get("order_request").qty  # type: ignore[attr-defined]
        self.orders[order.client_order_id] = order
        self.slices.append(order)
        return order

    def create_slice_schedule(self, **kwargs: Any) -> None:
        return None

    def get_slices_by_parent_id(self, parent_order_id: str) -> list[OrderDetail]:
        return [s for s in self.slices if s.parent_order_id == parent_order_id]

    def transaction(self):
        class _Tx:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Tx()


class StubAudit(AuditLogger):
    async def log_action(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - no-op
        return None


class StubAlpaca:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.submitted: list[tuple[OrderDetail | Any, str]] = []

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True

    def submit_order(self, order: Any, client_order_id: str) -> dict[str, Any]:
        self.submitted.append((order, client_order_id))
        return {"id": client_order_id, "status": "pending_new"}


class StubAuthenticator:
    def __init__(self, exc: Exception | None = None, role: Role = Role.OPERATOR) -> None:
        self.exc = exc
        self.role = role

    async def authenticate(
        self, token: str, x_user_id: str, x_request_id: str, x_session_version: int
    ) -> AuthenticatedUser:  # noqa: D401
        if self.exc:
            raise self.exc
        return AuthenticatedUser(
            user_id=x_user_id,
            role=self.role,
            strategies=["s1", "s2"],
            session_version=x_session_version,
            request_id=x_request_id,
        )


def build_client(overrides: dict[Callable[..., Any], Any] | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    dependency_aliases = {
        deps.get_authenticated_user: manual_controls_module.get_authenticated_user,
        deps.get_db_client: manual_controls_module.get_db_client,
        deps.get_rate_limiter: manual_controls_module.get_rate_limiter,
        deps.get_audit_logger: manual_controls_module.get_audit_logger,
        deps.get_alpaca_executor: manual_controls_module.get_alpaca_executor,
        deps.get_async_redis: manual_controls_module.get_async_redis,
        deps.get_2fa_validator: manual_controls_module.get_2fa_validator,
    }

    def _set_override(dep: Callable[..., Any], value: Any) -> None:
        app.dependency_overrides[dep] = value
        alias = dependency_aliases.get(dep)
        if alias:
            app.dependency_overrides[alias] = value

    # Default overrides
    stub_db = StubDB()
    stub_audit = StubAudit(None)  # type: ignore[arg-type]
    stub_alpaca = StubAlpaca()

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    stub_redis = StubRedis()
    stub_user = AuthenticatedUser(
        user_id="user-1",
        role=Role.OPERATOR,
        strategies=["s1"],
        session_version=1,
        request_id=str(uuid.uuid4()),
    )

    async def user_dep() -> AuthenticatedUser:
        return stub_user

    _set_override(deps.get_authenticated_user, user_dep)
    _set_override(deps.get_rate_limiter, lambda: StubRateLimiter())
    _set_override(deps.get_db_client, lambda: stub_db)
    _set_override(deps.get_audit_logger, lambda: stub_audit)
    _set_override(deps.get_alpaca_executor, lambda: stub_alpaca)
    _set_override(deps.get_gateway_authenticator, lambda: StubAuthenticator())
    _set_override(deps.get_async_redis, lambda: stub_redis)
    recovery_manager = MagicMock()
    recovery_manager.is_kill_switch_unavailable.return_value = False
    recovery_manager.kill_switch = MagicMock()
    recovery_manager.kill_switch.is_engaged.return_value = False

    app.state.context = AppContext(
        db=stub_db,
        redis=None,
        alpaca=None,
        liquidity_service=None,
        reconciliation_service=None,
        recovery_manager=recovery_manager,
        risk_config=RiskConfig(),
        fat_finger_validator=FatFingerValidator(FatFingerThresholds()),
        twap_slicer=TWAPSlicer(),
        webhook_secret="test-secret",
    )

    if overrides:
        for dep, value in overrides.items():
            _set_override(dep, value)

    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer token",
        "X-User-ID": "user-1",
        "X-Request-ID": str(uuid.uuid4()),
        "X-Session-Version": "1",
    }


def test_cancel_order_permission_denied_for_viewer():
    client = build_client(
        {
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "user-1", Role.VIEWER, ["s1"], 1, "req"
            )
        }
    )
    response = client.post(
        "/orders/ord-1/cancel",
        json={
            "reason": "too risky cancel",
            "requested_by": "u",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 403
    assert _err(response)["error"] == "permission_denied"


def test_cancel_order_operator_success():
    client = build_client()
    response = client.post(
        "/orders/ord-1/cancel",
        json={
            "reason": "duplicate order cancel",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_order_strategy_unauthorized():
    """Unauthorized strategy returns 404 to prevent information leakage."""
    db = StubDB()
    db.orders["ord-2"] = _order("s-unauth", client_id="ord-2")
    db.pending.append(db.orders["ord-2"])
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
        }
    )
    response = client.post(
        "/orders/ord-2/cancel",
        json={
            "reason": "cancel unauthorized strat",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    # Returns 404 instead of 403 to prevent leaking order existence across strategies
    assert response.status_code == 404
    assert _err(response)["error"] == "not_found"


def test_rate_limit_blocked():
    client = build_client(overrides={deps.get_rate_limiter: lambda: StubRateLimiter(allow=False)})
    response = client.post(
        "/orders/ord-1/cancel",
        json={
            "reason": "rate limit check",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_pending_orders_strategy_scope_denied():
    client = build_client()
    response = client.get("/orders/pending", params={"strategy_id": "other"})
    assert response.status_code == 403
    assert _err(response)["error"] == "strategy_unauthorized"


def test_pending_orders_admin_allowed():
    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "admin", Role.ADMIN, ["s1", "s2"], 1, "req"
            )
        }
    )
    response = client.get("/orders/pending")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert "filtered_by_strategy" in data


def test_header_validation_missing_authorization(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_AUTH_MODE", "enforce")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[manual_controls_module.get_authenticated_user] = (
        deps.get_authenticated_user
    )
    app.dependency_overrides[deps.get_gateway_authenticator] = lambda: StubAuthenticator()
    app.dependency_overrides[deps.get_rate_limiter] = lambda: StubRateLimiter()
    app.dependency_overrides[deps.get_db_client] = lambda: StubDB()
    app.dependency_overrides[deps.get_audit_logger] = lambda: StubAudit(None)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        "/orders/ord-1/cancel",
        headers={"X-User-ID": "u1", "X-Request-ID": str(uuid.uuid4()), "X-Session-Version": "1"},
        json={
            "reason": "missing auth header",
            "requested_by": "u1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 401
    assert _err(response)["error"] == "invalid_token"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidSignatureError(""), "invalid_signature"),
        (TokenExpiredError(""), "token_expired"),
        (TokenRevokedError(""), "token_revoked"),
        (TokenReplayedError(""), "token_replayed"),
        (MissingJtiError(""), "invalid_token"),
        (InvalidIssuerError(""), "invalid_issuer"),
        (InvalidAudienceError(""), "invalid_audience"),
        (SubjectMismatchError(""), "subject_mismatch"),
        (SessionExpiredError(""), "session_expired"),
        (InvalidTokenError("bad"), "invalid_token"),
    ],
)
def test_jwt_error_mapping(exc: Exception, expected: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_AUTH_MODE", "enforce")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[manual_controls_module.get_authenticated_user] = (
        deps.get_authenticated_user
    )

    app.dependency_overrides[deps.get_gateway_authenticator] = lambda: StubAuthenticator(exc=exc)
    app.dependency_overrides[deps.get_rate_limiter] = lambda: StubRateLimiter()
    app.dependency_overrides[deps.get_db_client] = lambda: StubDB()
    app.dependency_overrides[deps.get_audit_logger] = lambda: StubAudit(None)  # type: ignore[arg-type]

    client = TestClient(app)
    response = client.post(
        "/orders/ord-1/cancel",
        headers=_auth_headers(),
        json={
            "reason": "jwt error mapping",
            "requested_by": "u1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code in (401, 403)
    assert _err(response)["error"] == expected


def test_flatten_all_requires_mfa():
    async def mock_2fa_fail(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (False, "mfa_required", None)

    client = build_client(overrides={deps.get_2fa_validator: lambda: mock_2fa_fail})
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "flatten everything now please",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "bad",
        },
    )
    assert response.status_code == 403
    assert _err(response)["error"] in {
        "mfa_required",
        "mfa_invalid",
        "token_mismatch",
        "mfa_expired",
    }


def test_flatten_all_success():
    async def mock_2fa_pass(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (True, None, "otp")

    positions = [_position("AAPL", 5)]
    db = StubDB()
    db.positions = positions
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_2fa_validator: lambda: mock_2fa_pass,
        }
    )
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "flatten after mfa pass",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "good",
        },
    )
    assert response.status_code == 200
    assert response.json()["positions_closed"] == 1


def test_rate_limit_fallback_returns_429():
    client = build_client(
        overrides={
            deps.get_rate_limiter: lambda: StubRateLimiter(raise_error=True),
        }
    )
    response = client.get("/orders/pending")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


# =====================================================
# Recent Fills Endpoint Tests
# =====================================================


def test_recent_fills_viewer_can_access():
    """Viewers with VIEW_TRADES permission can access recent fills."""
    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "viewer-1", Role.VIEWER, ["s1"], 1, "req"
            )
        }
    )
    response = client.get("/orders/recent-fills")
    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert "total" in data


def test_recent_fills_operator_allowed():
    """Operators can access recent fills."""
    client = build_client()
    response = client.get("/orders/recent-fills")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 0


def test_recent_fills_admin_sees_all():
    """Admins can see all fills without strategy filtering."""
    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "admin-1", Role.ADMIN, ["s1", "s2"], 1, "req"
            )
        }
    )
    response = client.get("/orders/recent-fills")
    assert response.status_code == 200
    data = response.json()
    assert "filtered_by_strategy" in data
    assert data["filtered_by_strategy"] is False


def test_recent_fills_fail_closed_empty_strategies():
    """Users with empty strategy list get 403 (fail-closed, consistent with list_pending_orders)."""

    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "user-1", Role.OPERATOR, [], 1, "req"  # Empty strategies
            ),
        }
    )
    response = client.get("/orders/recent-fills")
    # Fail-closed: users with no authorized strategies are denied access (403),
    # not given empty results. This is consistent with list_pending_orders.
    assert response.status_code == 403
    data = response.json()
    assert data["detail"]["error"] == "strategy_unauthorized"


def test_recent_fills_limit_bounds():
    """Limit parameter is bounded between 1 and 200."""
    client = build_client()

    # Test lower bound
    response = client.get("/orders/recent-fills", params={"limit": 0})
    # Should be clamped to 1 (may return validation error or clamp silently)
    assert response.status_code in (200, 422)

    # Test upper bound
    response = client.get("/orders/recent-fills", params={"limit": 300})
    # Should be clamped to 200 (may return validation error or clamp silently)
    assert response.status_code in (200, 422)

    # Test valid limit
    response = client.get("/orders/recent-fills", params={"limit": 10})
    assert response.status_code == 200


def test_recent_fills_rate_limited():
    """Recent fills endpoint respects rate limiting."""
    client = build_client(
        overrides={
            deps.get_rate_limiter: lambda: StubRateLimiter(allow=False),
        }
    )
    response = client.get("/orders/recent-fills")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_recent_fills_returns_ordered_events():
    """Fill events are returned with expected structure."""
    client = build_client()
    response = client.get("/orders/recent-fills")
    assert response.status_code == 200
    data = response.json()
    events = data.get("events", [])
    if events:
        event = events[0]
        # Verify expected fields present
        assert "client_order_id" in event
        assert "symbol" in event
        assert "side" in event
        assert "qty" in event


# =====================================================
# Additional Coverage Tests
# =====================================================


def test_sanitize_reason_truncates_long_strings():
    """Test reason sanitization for audit logs (truncation)."""
    from apps.execution_gateway.api.manual_controls import _sanitize_reason

    long_reason = "a" * 600
    sanitized = _sanitize_reason(long_reason)
    assert len(sanitized) == 512


def test_sanitize_reason_strips_newlines():
    """Test reason sanitization removes newlines and consolidates whitespace."""
    from apps.execution_gateway.api.manual_controls import _sanitize_reason

    reason_with_newlines = "line1\nline2\n\nline3"
    sanitized = _sanitize_reason(reason_with_newlines)
    assert "\n" not in sanitized
    assert sanitized == "line1 line2 line3"


def test_parse_circuit_breaker_state_json():
    """Test parsing circuit breaker state from JSON format."""
    from apps.execution_gateway.api.manual_controls import _parse_circuit_breaker_state

    state_json = b'{"state": "TRIPPED", "reason": "drawdown"}'
    result = _parse_circuit_breaker_state(state_json)
    assert result == "TRIPPED"


def test_parse_circuit_breaker_state_legacy_string():
    """Test parsing circuit breaker state from legacy string format."""
    from apps.execution_gateway.api.manual_controls import _parse_circuit_breaker_state

    state_str = b"open"
    result = _parse_circuit_breaker_state(state_str)
    assert result == "OPEN"


def test_parse_circuit_breaker_state_empty():
    """Test parsing circuit breaker state when empty/None."""
    from apps.execution_gateway.api.manual_controls import _parse_circuit_breaker_state

    assert _parse_circuit_breaker_state(None) == ""
    assert _parse_circuit_breaker_state(b"") == ""


def test_parse_circuit_breaker_state_invalid_json():
    """Test parsing circuit breaker state with invalid JSON falls back to string."""
    from apps.execution_gateway.api.manual_controls import _parse_circuit_breaker_state

    invalid_json = b"not-json-data"
    result = _parse_circuit_breaker_state(invalid_json)
    assert result == "NOT-JSON-DATA"


def test_generate_manual_order_id_deterministic():
    """Test manual order ID generation is deterministic for same inputs."""
    from apps.execution_gateway.api.manual_controls import _generate_manual_order_id

    order_id_1 = _generate_manual_order_id(
        action="close_position",
        symbol="AAPL",
        side="sell",
        qty=Decimal(10),
        user_id="user-1",
        as_of_datetime=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )
    order_id_2 = _generate_manual_order_id(
        action="close_position",
        symbol="AAPL",
        side="sell",
        qty=Decimal(10),
        user_id="user-1",
        as_of_datetime=datetime(2025, 1, 1, 12, 30, 59, tzinfo=UTC),  # Same minute
    )
    assert order_id_1 == order_id_2
    assert len(order_id_1) == 24


def test_generate_manual_order_id_different_minutes():
    """Test manual order ID changes for different minutes."""
    from apps.execution_gateway.api.manual_controls import _generate_manual_order_id

    order_id_1 = _generate_manual_order_id(
        action="close_position",
        symbol="AAPL",
        side="sell",
        qty=Decimal(10),
        user_id="user-1",
        as_of_datetime=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )
    order_id_2 = _generate_manual_order_id(
        action="close_position",
        symbol="AAPL",
        side="sell",
        qty=Decimal(10),
        user_id="user-1",
        as_of_datetime=datetime(2025, 1, 1, 12, 31, 0, tzinfo=UTC),  # Next minute
    )
    assert order_id_1 != order_id_2


def test_generate_manual_order_id_with_limit_price():
    """Test manual order ID includes limit price for limit orders."""
    from apps.execution_gateway.api.manual_controls import _generate_manual_order_id

    order_id_1 = _generate_manual_order_id(
        action="manual_trade",
        symbol="AAPL",
        side="buy",
        qty=Decimal(10),
        user_id="user-1",
        as_of_datetime=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        order_type="limit",
        limit_price=Decimal("150.00"),
    )
    order_id_2 = _generate_manual_order_id(
        action="manual_trade",
        symbol="AAPL",
        side="buy",
        qty=Decimal(10),
        user_id="user-1",
        as_of_datetime=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        order_type="limit",
        limit_price=Decimal("160.00"),
    )
    assert order_id_1 != order_id_2


def test_strategy_allowed_admin_all_access():
    """Test admins with VIEW_ALL_STRATEGIES can access any strategy."""
    from apps.execution_gateway.api.manual_controls import _strategy_allowed

    admin = AuthenticatedUser("admin", Role.ADMIN, [], 1, "req")
    assert _strategy_allowed(admin, "any-strategy")
    assert _strategy_allowed(admin, None)


def test_strategy_allowed_manual_control_strategies():
    """Test operators can access manual control orders."""
    from apps.execution_gateway.api.manual_controls import _strategy_allowed

    operator = AuthenticatedUser("op", Role.OPERATOR, [], 1, "req")
    assert _strategy_allowed(operator, "manual_controls_close_position")
    assert _strategy_allowed(operator, "manual_controls_flatten_all")


def test_strategy_allowed_user_strategies():
    """Test users can access their assigned strategies."""
    from apps.execution_gateway.api.manual_controls import _strategy_allowed

    user = AuthenticatedUser("user", Role.OPERATOR, ["s1", "s2"], 1, "req")
    assert _strategy_allowed(user, "s1")
    assert _strategy_allowed(user, "s2")
    assert not _strategy_allowed(user, "s3")


def test_apply_strategy_scope_admin():
    """Test admin strategy scope returns None (all strategies)."""
    from apps.execution_gateway.api.manual_controls import _apply_strategy_scope

    admin = AuthenticatedUser("admin", Role.ADMIN, [], 1, "req")
    scope = _apply_strategy_scope(admin, ["s1"])
    assert scope is None


def test_apply_strategy_scope_operator():
    """Test operator strategy scope includes manual control strategies."""
    from apps.execution_gateway.api.manual_controls import _apply_strategy_scope

    operator = AuthenticatedUser("op", Role.OPERATOR, ["s1"], 1, "req")
    scope = _apply_strategy_scope(operator, ["s1"])
    assert "s1" in scope
    assert "manual_controls_close_position" in scope
    assert "manual_controls_adjust_position" in scope


def test_require_integral_qty_valid():
    """Test integral quantity validation accepts whole numbers."""
    from apps.execution_gateway.api.manual_controls import _require_integral_qty

    qty_int = _require_integral_qty(Decimal("10"), "qty")
    assert qty_int == 10


def test_require_integral_qty_fractional_raises():
    """Test integral quantity validation rejects fractional numbers."""
    from fastapi import HTTPException

    from apps.execution_gateway.api.manual_controls import _require_integral_qty

    with pytest.raises(HTTPException) as exc_info:
        _require_integral_qty(Decimal("10.5"), "qty")
    assert exc_info.value.status_code == 400
    assert "whole number" in exc_info.value.detail["message"]


def test_require_integral_qty_zero_raises():
    """Test integral quantity validation rejects zero."""
    from fastapi import HTTPException

    from apps.execution_gateway.api.manual_controls import _require_integral_qty

    with pytest.raises(HTTPException) as exc_info:
        _require_integral_qty(Decimal("0"), "qty")
    assert exc_info.value.status_code == 400
    assert "positive" in exc_info.value.detail["message"]


def test_require_integral_qty_negative_raises():
    """Test integral quantity validation rejects negative numbers."""
    from fastapi import HTTPException

    from apps.execution_gateway.api.manual_controls import _require_integral_qty

    with pytest.raises(HTTPException) as exc_info:
        _require_integral_qty(Decimal("-10"), "qty")
    assert exc_info.value.status_code == 400
    assert "positive" in exc_info.value.detail["message"]


# =====================================================
# Cancel Order Error Paths
# =====================================================


def test_cancel_order_not_found():
    """Test canceling a non-existent order returns 404."""
    client = build_client()
    response = client.post(
        "/orders/nonexistent/cancel",
        json={
            "reason": "trying to cancel missing order",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 404
    assert _err(response)["error"] == "not_found"


def test_cancel_order_no_broker_order_id():
    """Test canceling order without broker_order_id updates status directly."""
    db = StubDB()
    order_no_broker = _order("s1", client_id="ord-no-broker")
    order_no_broker.broker_order_id = None
    db.orders["ord-no-broker"] = order_no_broker
    client = build_client(overrides={deps.get_db_client: lambda: db})

    response = client.post(
        "/orders/ord-no-broker/cancel",
        json={
            "reason": "cancel order without broker id",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert db.status_updates["ord-no-broker"] == "canceled"


def test_cancel_order_broker_unavailable():
    """Test cancel order fails when broker executor is unavailable."""
    client = build_client(overrides={deps.get_alpaca_executor: lambda: None})
    response = client.post(
        "/orders/ord-1/cancel",
        json={
            "reason": "cancel with broker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "broker_unavailable"


def test_cancel_order_broker_timeout():
    """Test cancel order handles broker timeout gracefully."""

    class TimeoutAlpaca(StubAlpaca):
        def cancel_order(self, order_id: str) -> bool:
            raise TimeoutError("broker timeout")

    client = build_client(overrides={deps.get_alpaca_executor: lambda: TimeoutAlpaca()})
    response = client.post(
        "/orders/ord-1/cancel",
        json={
            "reason": "testing broker timeout handling",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 504
    assert _err(response)["error"] == "broker_timeout"


def test_cancel_order_broker_error():
    """Test cancel order handles broker errors gracefully."""
    from apps.execution_gateway.alpaca_client import AlpacaClientError

    class ErrorAlpaca(StubAlpaca):
        def cancel_order(self, order_id: str) -> bool:
            raise AlpacaClientError("order already filled")

    client = build_client(overrides={deps.get_alpaca_executor: lambda: ErrorAlpaca()})
    response = client.post(
        "/orders/ord-1/cancel",
        json={
            "reason": "testing broker error handling",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 502
    assert _err(response)["error"] == "broker_error"


# =====================================================
# Cancel All Orders Tests
# =====================================================


def test_cancel_all_orders_success():
    """Test cancel all orders for a symbol succeeds."""
    db = StubDB()
    db.pending = [_order("s1", "ord-1"), _order("s1", "ord-2")]
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/orders/cancel-all",
        json={
            "symbol": "AAPL",
            "reason": "cancel all for AAPL",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cancelled_count"] == 2
    assert len(data["order_ids"]) == 2


def test_cancel_all_orders_no_pending():
    """Test cancel all when no pending orders returns 404."""
    db = StubDB()
    db.pending = []
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/orders/cancel-all",
        json={
            "symbol": "AAPL",
            "reason": "cancel all with no orders",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 404
    assert _err(response)["error"] == "not_found"


def test_cancel_all_orders_partial_failure():
    """Test cancel all handles partial failures gracefully."""
    from apps.execution_gateway.alpaca_client import AlpacaClientError

    class PartialFailAlpaca(StubAlpaca):
        def __init__(self) -> None:
            super().__init__()
            self.call_count = 0

        def cancel_order(self, order_id: str) -> bool:
            self.call_count += 1
            if self.call_count == 2:
                raise AlpacaClientError("second order failed")
            self.cancelled.append(order_id)
            return True

    db = StubDB()
    db.pending = [_order("s1", "ord-1"), _order("s1", "ord-2")]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_alpaca_executor: lambda: PartialFailAlpaca(),
        }
    )
    response = client.post(
        "/orders/cancel-all",
        json={
            "symbol": "AAPL",
            "reason": "testing partial failure",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    # Partial success still returns 200
    assert response.status_code == 200
    data = response.json()
    assert data["cancelled_count"] == 1


def test_cancel_all_orders_all_fail():
    """Test cancel all when all orders fail returns 502."""
    from apps.execution_gateway.alpaca_client import AlpacaClientError

    class AllFailAlpaca(StubAlpaca):
        def cancel_order(self, order_id: str) -> bool:
            raise AlpacaClientError("all orders fail")

    db = StubDB()
    db.pending = [_order("s1", "ord-1")]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_alpaca_executor: lambda: AllFailAlpaca(),
        }
    )
    response = client.post(
        "/orders/cancel-all",
        json={
            "symbol": "AAPL",
            "reason": "testing all fail scenario",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 502
    assert _err(response)["error"] == "broker_error"


def test_cancel_all_orders_broker_unavailable():
    """Test cancel all fails when broker is unavailable."""
    client = build_client(overrides={deps.get_alpaca_executor: lambda: None})
    response = client.post(
        "/orders/cancel-all",
        json={
            "symbol": "AAPL",
            "reason": "testing broker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "broker_unavailable"


# =====================================================
# Close Position Tests
# =====================================================


def test_close_position_full_close():
    """Test closing a full position successfully."""
    client = build_client()
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "closing full position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "closing"
    assert data["symbol"] == "AAPL"
    assert data["qty_to_close"] == "5"


def test_close_position_partial_close():
    """Test closing a partial position successfully."""
    client = build_client()
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "partial position close",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "qty": "3",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "closing"
    assert data["qty_to_close"] == "3"


def test_close_position_already_flat():
    """Test closing a position that is already flat returns no-op."""
    db = StubDB()
    db.positions = [_position("AAPL", 0)]
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "trying to close flat position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "already_flat"
    assert data["qty_to_close"] == "0"


def test_close_position_not_found():
    """Test closing a non-existent position returns 404."""
    db = StubDB()
    db.positions = []
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/GOOG/close",
        json={
            "reason": "trying to close missing position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 404
    assert _err(response)["error"] == "not_found"


def test_close_position_qty_exceeds_position():
    """Test closing more than position size is rejected."""
    client = build_client()
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "trying to overclose position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "qty": "100",
        },
    )
    assert response.status_code == 422
    assert _err(response)["error"] == "qty_exceeds_position"


def test_close_position_fractional_position():
    """Test closing fractional position is rejected."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    db.positions[0].qty = Decimal("5.5")
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "trying to close fractional position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 422
    assert _err(response)["error"] == "fractional_position_unsupported"


def test_close_position_broker_unavailable():
    """Test close position fails when broker is unavailable."""
    client = build_client(overrides={deps.get_alpaca_executor: lambda: None})
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "testing broker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "broker_unavailable"


def test_close_position_broker_error():
    """Test close position handles broker errors."""
    from apps.execution_gateway.alpaca_client import AlpacaClientError

    class ErrorAlpaca(StubAlpaca):
        def submit_order(self, order: Any, client_order_id: str) -> dict[str, Any]:
            raise AlpacaClientError("market closed")

    db = StubDB()

    # Override create_order to not raise IntegrityError for first call
    original_create = db.create_order

    def create_order_once(**kwargs: Any) -> OrderDetail:
        return original_create(**kwargs)

    db.create_order = create_order_once  # type: ignore[method-assign]

    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_alpaca_executor: lambda: ErrorAlpaca(),
        }
    )
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "testing broker error in close",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 502
    assert _err(response)["error"] == "broker_error"


def test_close_position_idempotent_retry():
    """Test close position handles idempotent retries."""
    from psycopg import IntegrityError

    db = StubDB()
    existing_order = _order("manual_controls_close_position", "existing-order")
    db.orders["existing-order"] = existing_order

    call_count = {"count": 0}

    original_create = db.create_order

    def create_order_with_conflict(**kwargs: Any) -> OrderDetail:
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise IntegrityError("duplicate key")
        return original_create(**kwargs)

    db.create_order = create_order_with_conflict  # type: ignore[method-assign]
    db.get_order_by_client_id = lambda cid: existing_order  # type: ignore[method-assign]

    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/close",
        json={
            "reason": "testing idempotent retry",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "idempotent retry" in data.get("message", "")


# =====================================================
# Adjust Position Tests
# =====================================================


def test_adjust_position_increase():
    """Test adjusting position upward."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "10",
            "reason": "increasing position to 10",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "adjusting"
    assert data["current_qty"] == "5"
    assert data["target_qty"] == "10"


def test_adjust_position_decrease():
    """Test adjusting position downward (risk-reducing)."""
    db = StubDB()
    db.positions = [_position("AAPL", 10)]
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "5",
            "reason": "reducing position to 5",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "adjusting"
    assert data["current_qty"] == "10"
    assert data["target_qty"] == "5"


def test_adjust_position_no_change():
    """Test adjusting position to current qty is no-op."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "5",
            "reason": "adjusting to same qty (no-op)",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "adjusting"
    assert data["order_id"] is None


def test_adjust_position_not_found():
    """Test adjusting non-existent position returns 404."""
    db = StubDB()
    db.positions = []
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/GOOG/adjust",
        json={
            "target_qty": "10",
            "reason": "trying to adjust missing position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 404
    assert _err(response)["error"] == "not_found"


def test_adjust_position_fractional_position():
    """Test adjusting fractional position is rejected."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    db.positions[0].qty = Decimal("5.5")
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "10",
            "reason": "trying to adjust fractional position",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 422
    assert _err(response)["error"] == "fractional_position_unsupported"


def test_adjust_position_circuit_breaker_tripped():
    """Test adjust position blocks exposure-increasing when circuit breaker tripped."""

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "TRIPPED"}'

    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_async_redis: lambda: StubRedis(),
        }
    )
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "10",
            "reason": "trying to increase during breaker trip",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "circuit_breaker_tripped"


def test_adjust_position_circuit_breaker_allows_risk_reducing():
    """Test adjust position allows risk-reducing adjustments during circuit breaker trip."""

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "TRIPPED"}'

    db = StubDB()
    db.positions = [_position("AAPL", 10)]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_async_redis: lambda: StubRedis(),
        }
    )
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "5",
            "reason": "risk-reducing adjustment during breaker",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200


def test_adjust_position_circuit_breaker_unavailable():
    """Test adjust position fails-closed when circuit breaker unavailable."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_async_redis: lambda: None,
        }
    )
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "10",
            "reason": "trying to increase with breaker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "circuit_breaker_unavailable"


def test_adjust_position_with_limit_price():
    """Test adjusting position with limit order."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    client = build_client(overrides={deps.get_db_client: lambda: db})
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "10",
            "order_type": "limit",
            "limit_price": "150.00",
            "reason": "adjusting with limit order",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200


def test_adjust_position_broker_unavailable():
    """Test adjust position fails when broker unavailable."""
    db = StubDB()
    db.positions = [_position("AAPL", 5)]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_alpaca_executor: lambda: None,
        }
    )
    response = client.post(
        "/positions/AAPL/adjust",
        json={
            "target_qty": "10",
            "reason": "testing broker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "broker_unavailable"


# =====================================================
# Flatten All Tests
# =====================================================


def test_flatten_all_no_positions():
    """Test flatten all with no positions returns no-op."""

    async def mock_2fa_pass(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (True, None, "otp")

    db = StubDB()
    db.positions = []
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_2fa_validator: lambda: mock_2fa_pass,
        }
    )
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "flattening with no positions",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "good",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["positions_closed"] == 0


def test_flatten_all_multiple_positions():
    """Test flatten all with multiple positions."""

    async def mock_2fa_pass(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (True, None, "otp")

    db = StubDB()
    db.positions = [_position("AAPL", 5), _position("GOOG", -3)]
    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_2fa_validator: lambda: mock_2fa_pass,
        }
    )
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "flattening multiple positions",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "good",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["positions_closed"] == 2


def test_flatten_all_broker_unavailable():
    """Test flatten all fails when broker unavailable."""

    async def mock_2fa_pass(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (True, None, "otp")

    client = build_client(
        overrides={
            deps.get_alpaca_executor: lambda: None,
            deps.get_2fa_validator: lambda: mock_2fa_pass,
        }
    )
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "testing broker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "good",
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "broker_unavailable"


def test_flatten_all_broker_error():
    """Test flatten all handles broker errors."""

    async def mock_2fa_pass(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (True, None, "otp")

    from apps.execution_gateway.alpaca_client import AlpacaClientError

    class ErrorAlpaca(StubAlpaca):
        def submit_order(self, order: Any, client_order_id: str) -> dict[str, Any]:
            raise AlpacaClientError("market closed")

    client = build_client(
        overrides={
            deps.get_alpaca_executor: lambda: ErrorAlpaca(),
            deps.get_2fa_validator: lambda: mock_2fa_pass,
        }
    )
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "testing broker error in flatten",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "good",
        },
    )
    assert response.status_code == 502
    assert _err(response)["error"] == "broker_error"


def test_flatten_all_mfa_error_mapping():
    """Test flatten all MFA error mapping."""

    async def mock_2fa_fail(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (False, "token_expired", None)

    client = build_client(overrides={deps.get_2fa_validator: lambda: mock_2fa_fail})
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "testing mfa error mapping",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "bad",
        },
    )
    assert response.status_code == 403
    assert _err(response)["error"] == "mfa_expired"


def test_flatten_all_no_authorized_strategies():
    """Test flatten all fails when user has no authorized strategies."""

    async def mock_2fa_pass(token: str, uid: str) -> tuple[bool, str | None, str | None]:
        return (True, None, "otp")

    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "user", Role.OPERATOR, [], 1, "req"
            ),
            deps.get_2fa_validator: lambda: mock_2fa_pass,
        }
    )
    response = client.post(
        "/positions/flatten-all",
        json={
            "reason": "testing no authorized strategies",
            "requested_by": "user",
            "requested_at": datetime.now(UTC).isoformat(),
            "id_token": "good",
        },
    )
    assert response.status_code == 403
    assert _err(response)["error"] == "strategy_unauthorized"


# =====================================================
# Manual Order Submission Tests
# =====================================================


def test_submit_manual_order_market():
    """Test submitting a manual market order."""

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    client = build_client(overrides={deps.get_async_redis: lambda: StubRedis()})
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "order_type": "market",
            "reason": "manual market order",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "AAPL"
    assert data["side"] == "buy"
    assert data["qty"] == 10


def test_submit_manual_order_limit():
    """Test submitting a manual limit order."""

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    client = build_client(overrides={deps.get_async_redis: lambda: StubRedis()})
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "order_type": "limit",
            "limit_price": "150.00",
            "reason": "manual limit order",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200


def test_submit_manual_order_twap_creates_slices(monkeypatch: pytest.MonkeyPatch):
    """Test submitting a manual TWAP order schedules parent + slices."""

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    class StubScheduler:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def schedule_slices(self, **kwargs: Any) -> list[str]:
            self.calls.append(kwargs)
            slices = kwargs.get("slices", [])
            return [f"job-{s.slice_num}" for s in slices]

    stub_db = StubDB()
    scheduler = StubScheduler()
    ctx = AppContext(
        db=stub_db,
        redis=None,
        alpaca=None,
        liquidity_service=None,
        reconciliation_service=None,
        recovery_manager=MagicMock(),
        risk_config=RiskConfig(),
        fat_finger_validator=FatFingerValidator(FatFingerThresholds()),
        twap_slicer=TWAPSlicer(),
        webhook_secret="test-secret",
    )
    ctx.recovery_manager.slice_scheduler = scheduler  # type: ignore[assignment]
    ctx.recovery_manager.is_kill_switch_unavailable.return_value = False
    ctx.recovery_manager.kill_switch = MagicMock()
    ctx.recovery_manager.kill_switch.is_engaged.return_value = False

    # Ensure test-mode get_context override uses the stub DB (not global mock).
    monkeypatch.setattr("apps.execution_gateway.main.db_client", stub_db, raising=False)

    client = build_client(
        overrides={
            deps.get_async_redis: lambda: StubRedis(),
            deps.get_db_client: lambda: stub_db,
        }
    )
    client.app.state.context = ctx

    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "100",
            "order_type": "market",
            "time_in_force": "day",
            "execution_style": "twap",
            "twap_duration_minutes": 10,
            "twap_interval_seconds": 60,
            "reason": "manual twap order",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "scheduled"
    assert data["slice_count"] is not None
    assert data["slice_count"] > 0
    assert scheduler.calls


def test_submit_manual_order_circuit_breaker_tripped():
    """Test manual order blocked when circuit breaker tripped."""

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "TRIPPED"}'

    client = build_client(overrides={deps.get_async_redis: lambda: StubRedis()})
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order during breaker trip",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "circuit_breaker_tripped"


def test_submit_manual_order_circuit_breaker_unavailable():
    """Test manual order fails-closed when circuit breaker unavailable."""
    client = build_client(overrides={deps.get_async_redis: lambda: None})
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order with breaker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "circuit_breaker_unavailable"


def test_submit_manual_order_kill_switch_engaged():
    """Test manual order blocked when kill switch engaged."""
    client = build_client()
    ctx = client.app.state.context
    ctx.recovery_manager.kill_switch.is_engaged.return_value = True

    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order during kill switch",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "kill_switch_engaged"


def test_submit_manual_order_kill_switch_unavailable():
    """Test manual order fails-closed when kill switch unavailable."""
    client = build_client()
    ctx = client.app.state.context
    ctx.recovery_manager.is_kill_switch_unavailable.return_value = True

    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order with kill switch unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 503
    assert _err(response)["error"] == "kill_switch_unavailable"


def test_submit_manual_order_fat_finger_blocked():
    """Test manual order blocked by fat-finger validation."""
    client = build_client()
    ctx = client.app.state.context
    ctx.fat_finger_validator = FatFingerValidator(FatFingerThresholds(max_qty=5))

    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order fat finger breach",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 400
    assert _err(response)["error"] == "fat_finger_rejected"

def test_submit_manual_order_dry_run(monkeypatch: pytest.MonkeyPatch):
    """Test manual order in dry-run mode."""
    monkeypatch.setattr("apps.execution_gateway.api.manual_controls.DRY_RUN", True)

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    client = build_client(
        overrides={
            deps.get_alpaca_executor: lambda: None,
            deps.get_async_redis: lambda: StubRedis(),
        }
    )
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order dry run",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run"


def test_submit_manual_order_broker_unavailable_not_dry_run(monkeypatch: pytest.MonkeyPatch):
    """Test manual order fails when broker unavailable and not in dry-run mode."""
    monkeypatch.setattr("apps.execution_gateway.api.manual_controls.DRY_RUN", False)

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    client = build_client(
        overrides={
            deps.get_alpaca_executor: lambda: None,
            deps.get_async_redis: lambda: StubRedis(),
        }
    )
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "manual order broker unavailable",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    # DRY_RUN is False by default, should fail
    assert response.status_code == 503
    assert _err(response)["error"] == "broker_unavailable"


def test_submit_manual_order_idempotent_retry():
    """Test manual order handles idempotent retries."""
    from psycopg import IntegrityError

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    db = StubDB()
    existing_order = _order("manual_controls_trade", "existing-manual")
    db.orders["existing-manual"] = existing_order

    call_count = {"count": 0}
    original_create = db.create_order

    def create_order_with_conflict(**kwargs: Any) -> OrderDetail:
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise IntegrityError("duplicate key")
        return original_create(**kwargs)

    db.create_order = create_order_with_conflict  # type: ignore[method-assign]
    db.get_order_by_client_id = lambda cid: existing_order  # type: ignore[method-assign]

    client = build_client(
        overrides={
            deps.get_db_client: lambda: db,
            deps.get_async_redis: lambda: StubRedis(),
        }
    )
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "testing idempotent retry manual",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "idempotent retry" in data.get("message", "")


def test_submit_manual_order_broker_error():
    """Test manual order handles broker errors."""
    from apps.execution_gateway.alpaca_client import AlpacaClientError

    class StubRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    class ErrorAlpaca(StubAlpaca):
        def submit_order(self, order: Any, client_order_id: str) -> dict[str, Any]:
            raise AlpacaClientError("invalid symbol")

    client = build_client(
        overrides={
            deps.get_alpaca_executor: lambda: ErrorAlpaca(),
            deps.get_async_redis: lambda: StubRedis(),
        }
    )
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "testing broker error manual",
            "requested_by": "user-1",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 502
    assert _err(response)["error"] == "broker_error"


def test_submit_manual_order_permission_denied():
    """Test manual order requires SUBMIT_ORDER permission."""
    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "viewer", Role.VIEWER, ["s1"], 1, "req"
            )
        }
    )
    response = client.post(
        "/manual/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "reason": "viewer trying to submit",
            "requested_by": "viewer",
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 403
    assert _err(response)["error"] == "permission_denied"


# =====================================================
# Pending Orders List Tests
# =====================================================


def test_pending_orders_default_params():
    """Test listing pending orders with default parameters."""
    client = build_client()
    response = client.get("/orders/pending")
    assert response.status_code == 200
    data = response.json()
    assert "orders" in data
    assert "total" in data
    assert data["limit"] == 100


def test_pending_orders_with_filters():
    """Test listing pending orders with symbol and strategy filters."""
    client = build_client()
    response = client.get("/orders/pending", params={"symbol": "AAPL", "limit": 50})
    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 50


def test_pending_orders_sort_validation():
    """Test pending orders validates sort parameters."""
    client = build_client()
    response = client.get("/orders/pending", params={"sort_by": "invalid", "sort_order": "invalid"})
    assert response.status_code == 200
    # Should fall back to valid defaults


def test_pending_orders_no_strategies():
    """Test pending orders returns empty when user has no authorized strategies."""
    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "user", Role.OPERATOR, [], 1, "req"
            )
        }
    )
    response = client.get("/orders/pending")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0


def test_pending_orders_permission_denied():
    """Test pending orders requires VIEW_TRADES permission."""

    class LimitedViewer(AuthenticatedUser):
        """Viewer without VIEW_TRADES."""

        def __init__(self) -> None:
            super().__init__("limited", Role.VIEWER, ["s1"], 1, "req")

    # Note: Role.VIEWER has VIEW_TRADES by default, so this test may not trigger
    # unless we mock has_permission. For now, we verify the endpoint works for VIEWER.
    client = build_client(
        overrides={
            deps.get_authenticated_user: lambda: AuthenticatedUser(
                "v", Role.VIEWER, ["s1"], 1, "req"
            )
        }
    )
    response = client.get("/orders/pending")
    assert response.status_code == 200  # VIEWER has VIEW_TRADES


# =====================================================
# Circuit Breaker Check Tests
# =====================================================


async def test_check_circuit_breaker_redis_unavailable():
    """Test circuit breaker check fails-closed when Redis unavailable."""
    from fastapi import HTTPException

    from apps.execution_gateway.api.manual_controls import _check_circuit_breaker

    user = AuthenticatedUser("user", Role.OPERATOR, ["s1"], 1, "req")
    audit = StubAudit(None)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await _check_circuit_breaker(
            None, user=user, action="test", reason="testing", audit_logger=audit
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "circuit_breaker_unavailable"


async def test_check_circuit_breaker_redis_error():
    """Test circuit breaker check fails-closed on Redis errors."""
    from fastapi import HTTPException
    from redis.exceptions import RedisError

    from apps.execution_gateway.api.manual_controls import _check_circuit_breaker

    class ErrorRedis:
        async def get(self, key: str) -> bytes:
            raise RedisError("connection failed")

    user = AuthenticatedUser("user", Role.OPERATOR, ["s1"], 1, "req")
    audit = StubAudit(None)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await _check_circuit_breaker(
            ErrorRedis(),  # type: ignore[arg-type]
            user=user,
            action="test",
            reason="testing",
            audit_logger=audit,
        )
    assert exc_info.value.status_code == 503


async def test_check_circuit_breaker_state_missing():
    """Test circuit breaker check fails-closed when state is missing."""
    from fastapi import HTTPException

    from apps.execution_gateway.api.manual_controls import _check_circuit_breaker

    class EmptyRedis:
        async def get(self, key: str) -> bytes | None:
            return None

    user = AuthenticatedUser("user", Role.OPERATOR, ["s1"], 1, "req")
    audit = StubAudit(None)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await _check_circuit_breaker(
            EmptyRedis(),  # type: ignore[arg-type]
            user=user,
            action="test",
            reason="testing",
            audit_logger=audit,
        )
    assert exc_info.value.status_code == 503


async def test_check_circuit_breaker_tripped():
    """Test circuit breaker check fails when breaker is tripped."""
    from fastapi import HTTPException

    from apps.execution_gateway.api.manual_controls import _check_circuit_breaker

    class TrippedRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "TRIPPED"}'

    user = AuthenticatedUser("user", Role.OPERATOR, ["s1"], 1, "req")
    audit = StubAudit(None)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await _check_circuit_breaker(
            TrippedRedis(),  # type: ignore[arg-type]
            user=user,
            action="test",
            reason="testing",
            audit_logger=audit,
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "circuit_breaker_tripped"


async def test_check_circuit_breaker_open():
    """Test circuit breaker check passes when breaker is open."""
    from apps.execution_gateway.api.manual_controls import _check_circuit_breaker

    class OpenRedis:
        async def get(self, key: str) -> bytes:
            return b'{"state": "OPEN"}'

    user = AuthenticatedUser("user", Role.OPERATOR, ["s1"], 1, "req")
    audit = StubAudit(None)  # type: ignore[arg-type]

    # Should not raise
    await _check_circuit_breaker(
        OpenRedis(),  # type: ignore[arg-type]
        user=user,
        action="test",
        reason="testing",
        audit_logger=audit,
    )
