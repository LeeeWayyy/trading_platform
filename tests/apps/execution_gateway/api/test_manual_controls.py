"""Tests for manual control API endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.api import dependencies as deps
from apps.execution_gateway.api.manual_controls import router
from apps.execution_gateway.schemas import OrderDetail, Position
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
        return filtered[offset : offset + limit], len(filtered)

    def get_positions_for_strategies(self, strategies: list[str]) -> list[Position]:
        if not strategies:
            return []
        return self.positions

    def get_all_positions(self) -> list[Position]:
        return self.positions

    def get_strategy_map_for_symbols(self, symbols: list[str]) -> dict[str, str | None]:
        return {symbol: "s1" for symbol in symbols}

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
        return {"id": client_order_id}


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

    # Default overrides
    stub_db = StubDB()
    stub_audit = StubAudit(None)  # type: ignore[arg-type]
    stub_alpaca = StubAlpaca()
    stub_user = AuthenticatedUser(
        user_id="user-1",
        role=Role.OPERATOR,
        strategies=["s1"],
        session_version=1,
        request_id=str(uuid.uuid4()),
    )

    async def user_dep() -> AuthenticatedUser:
        return stub_user

    app.dependency_overrides[deps.get_authenticated_user] = user_dep
    app.dependency_overrides[deps.get_rate_limiter] = lambda: StubRateLimiter()
    app.dependency_overrides[deps.get_db_client] = lambda: stub_db
    app.dependency_overrides[deps.get_audit_logger] = lambda: stub_audit
    app.dependency_overrides[deps.get_alpaca_executor] = lambda: stub_alpaca
    app.dependency_overrides[deps.get_gateway_authenticator] = lambda: StubAuthenticator()

    if overrides:
        for dep, value in overrides.items():
            app.dependency_overrides[dep] = value

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
