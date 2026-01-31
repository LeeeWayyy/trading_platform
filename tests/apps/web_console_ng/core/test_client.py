from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from apps.web_console_ng import config
from apps.web_console_ng.core.client import AsyncTradingClient


@pytest.fixture(autouse=True)
def reset_trading_client() -> None:
    client = AsyncTradingClient.get()
    client._http_client = None
    # Also reset the singleton instance to ensure clean state
    AsyncTradingClient._instance = None


# ============================================================================
# Client Initialization and Lifecycle Tests
# ============================================================================


@pytest.mark.asyncio()
async def test_startup_creates_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that startup() creates an httpx.AsyncClient."""
    monkeypatch.setattr(config, "EXECUTION_GATEWAY_URL", "http://testserver")
    client = AsyncTradingClient.get()

    assert client._http_client is None
    await client.startup()

    assert client._http_client is not None
    assert isinstance(client._http_client, httpx.AsyncClient)

    await client.shutdown()


@pytest.mark.asyncio()
async def test_startup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that calling startup() twice doesn't create a new client."""
    monkeypatch.setattr(config, "EXECUTION_GATEWAY_URL", "http://testserver")
    client = AsyncTradingClient.get()

    await client.startup()
    first_client = client._http_client

    await client.startup()
    second_client = client._http_client

    assert first_client is second_client

    await client.shutdown()


@pytest.mark.asyncio()
async def test_shutdown_closes_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that shutdown() closes and clears the http client."""
    monkeypatch.setattr(config, "EXECUTION_GATEWAY_URL", "http://testserver")
    client = AsyncTradingClient.get()

    await client.startup()
    assert client._http_client is not None

    await client.shutdown()
    assert client._http_client is None


@pytest.mark.asyncio()
async def test_shutdown_is_idempotent() -> None:
    """Test that calling shutdown() when client is None is safe."""
    client = AsyncTradingClient.get()
    client._http_client = None

    # Should not raise
    await client.shutdown()
    assert client._http_client is None


def test_client_property_raises_when_not_initialized() -> None:
    """Test that accessing _client before startup() raises RuntimeError."""
    client = AsyncTradingClient.get()

    with pytest.raises(RuntimeError, match="Client not initialized"):
        _ = client._client


def test_get_returns_singleton() -> None:
    """Test that get() returns the same instance."""
    client1 = AsyncTradingClient.get()
    client2 = AsyncTradingClient.get()

    assert client1 is client2


# ============================================================================
# Auth Header Tests
# ============================================================================


def test_get_auth_headers_production_no_secret_empty_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test production mode without INTERNAL_TOKEN_SECRET and empty user_id."""
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)

    client = AsyncTradingClient.get()
    # In production without secret, empty user_id is allowed but no X-User-Id header
    headers = client._get_auth_headers(user_id="", role="viewer", strategies=["s1"])

    assert headers["X-User-Role"] == "viewer"
    assert "X-User-Id" not in headers  # Empty user_id means no header
    assert headers["X-User-Strategies"] == "s1"
    assert "X-User-Signature" not in headers


def test_get_auth_headers_debug_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setattr(config, "DEV_ROLE", "dev-role")
    monkeypatch.setattr(config, "DEV_STRATEGIES", ["strat-b", "strat-a"])
    monkeypatch.setattr(config, "DEV_USER_ID", "dev-user")
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)

    client = AsyncTradingClient.get()
    headers = client._get_auth_headers(user_id="", role=None, strategies=None)

    assert headers["X-User-Role"] == "dev-role"
    assert headers["X-User-Id"] == "dev-user"
    assert headers["X-User-Strategies"] == "strat-a,strat-b"
    assert "X-User-Signature" not in headers


def test_get_auth_headers_production_requires_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

    client = AsyncTradingClient.get()
    with pytest.raises(ValueError, match="User ID required"):
        client._get_auth_headers(user_id="", role="admin", strategies=["s1"])


def test_get_auth_headers_production_requires_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

    client = AsyncTradingClient.get()
    with pytest.raises(ValueError, match="Role required"):
        client._get_auth_headers(user_id="user-1", role=None, strategies=["s1"])


def test_get_auth_headers_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")
    monkeypatch.setattr("time.time", lambda: 1700000000)

    client = AsyncTradingClient.get()
    headers = client._get_auth_headers(user_id="user-1", role="trader", strategies=["b", "a"])

    payload_data = {
        "uid": "user-1",
        "role": "trader",
        "strats": "a,b",
        "ts": "1700000000",
    }
    payload = json.dumps(payload_data, separators=(",", ":"), sort_keys=True)
    expected_sig = hmac.new(b"secret", payload.encode("utf-8"), hashlib.sha256).hexdigest()

    assert headers["X-User-Strategies"] == "a,b"
    assert headers["X-Request-Timestamp"] == "1700000000"
    assert headers["X-User-Signature"] == expected_sig


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_kill_switch_status_maps_active(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/kill-switch/status").mock(
        return_value=httpx.Response(200, json={"state": "ACTIVE"})
    )

    result = await trading_client.fetch_kill_switch_status("user-1")

    assert result["state"] == "DISENGAGED"
    assert route.call_count == 1


def test_json_dict_requires_object() -> None:
    client = AsyncTradingClient.get()
    response = httpx.Response(200, json=["not", "a", "dict"])

    with pytest.raises(ValueError, match="Expected JSON object response"):
        client._json_dict(response)


# ============================================================================
# API Endpoint Tests - Positions
# ============================================================================


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_positions(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_positions calls the correct endpoint."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/positions").mock(
        return_value=httpx.Response(200, json={"positions": [], "total": 0})
    )

    result = await trading_client.fetch_positions("user-1")

    assert result == {"positions": [], "total": 0}
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_circuit_breaker_status(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_circuit_breaker_status calls the correct endpoint."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/circuit-breaker/status").mock(
        return_value=httpx.Response(200, json={"state": "OPEN", "tripped_at": None})
    )

    result = await trading_client.fetch_circuit_breaker_status("user-1")

    assert result == {"state": "OPEN", "tripped_at": None}
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_open_orders(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_open_orders calls the correct endpoint."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/orders/pending").mock(
        return_value=httpx.Response(200, json={"orders": [], "total": 0})
    )

    result = await trading_client.fetch_open_orders("user-1")

    assert result == {"orders": [], "total": 0}
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_recent_fills(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_recent_fills calls the correct endpoint with limit param."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/orders/recent-fills").mock(
        return_value=httpx.Response(200, json={"fills": [], "total": 0})
    )

    result = await trading_client.fetch_recent_fills("user-1", limit=25)

    assert result == {"fills": [], "total": 0}
    assert route.call_count == 1
    # Verify limit parameter was passed
    assert route.calls[0].request.url.params.get("limit") == "25"


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_realtime_pnl(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_realtime_pnl calls the correct endpoint."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/positions/pnl/realtime").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_unrealized_pl": 1000.0,
                "realized_pl_today": 500.0,
            },
        )
    )

    result = await trading_client.fetch_realtime_pnl("user-1")

    assert result["total_unrealized_pl"] == 1000.0
    assert result["realized_pl_today"] == 500.0
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_account_info(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_account_info calls the correct endpoint."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "buying_power": 50000.0,
                "cash": 10000.0,
                "portfolio_value": 100000.0,
            },
        )
    )

    result = await trading_client.fetch_account_info("user-1")

    assert result["buying_power"] == 50000.0
    assert result["cash"] == 10000.0
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_market_prices(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_market_prices calls the correct endpoint and expects array."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/market_prices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "AAPL", "price": 150.0},
                {"symbol": "GOOGL", "price": 140.0},
            ],
        )
    )

    result = await trading_client.fetch_market_prices("user-1")

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["symbol"] == "AAPL"
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_market_prices_requires_array(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_market_prices raises ValueError if response is not an array."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    respx.get("http://testserver/api/v1/market_prices").mock(
        return_value=httpx.Response(200, json={"not": "an array"})
    )

    with pytest.raises(ValueError, match="Expected JSON array response"):
        await trading_client.fetch_market_prices("user-1")


# ============================================================================
# API Endpoint Tests - Kill Switch
# ============================================================================


@pytest.mark.asyncio()
@respx.mock
async def test_engage_kill_switch(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test engage_kill_switch sends correct payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/kill-switch/engage").mock(
        return_value=httpx.Response(200, json={"status": "engaged"})
    )

    result = await trading_client.engage_kill_switch(
        user_id="user-1",
        reason="Emergency shutdown",
        details={"source": "web_console"},
    )

    assert result == {"status": "engaged"}
    assert route.call_count == 1
    # Verify payload
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["operator"] == "user-1"
    assert request_json["reason"] == "Emergency shutdown"
    assert request_json["details"] == {"source": "web_console"}


@pytest.mark.asyncio()
@respx.mock
async def test_engage_kill_switch_without_details(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test engage_kill_switch without optional details."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/kill-switch/engage").mock(
        return_value=httpx.Response(200, json={"status": "engaged"})
    )

    result = await trading_client.engage_kill_switch(
        user_id="user-1",
        reason="Emergency shutdown",
    )

    assert result == {"status": "engaged"}
    request_json = json.loads(route.calls[0].request.content)
    assert "details" not in request_json


@pytest.mark.asyncio()
@respx.mock
async def test_disengage_kill_switch(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test disengage_kill_switch sends correct payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/kill-switch/disengage").mock(
        return_value=httpx.Response(200, json={"status": "disengaged"})
    )

    result = await trading_client.disengage_kill_switch(
        user_id="user-1",
        notes="All clear, resuming trading",
    )

    assert result == {"status": "disengaged"}
    assert route.call_count == 1
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["operator"] == "user-1"
    assert request_json["notes"] == "All clear, resuming trading"


@pytest.mark.asyncio()
@respx.mock
async def test_disengage_kill_switch_without_notes(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test disengage_kill_switch without optional notes."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/kill-switch/disengage").mock(
        return_value=httpx.Response(200, json={"status": "disengaged"})
    )

    result = await trading_client.disengage_kill_switch(user_id="user-1")

    assert result == {"status": "disengaged"}
    request_json = json.loads(route.calls[0].request.content)
    assert "notes" not in request_json


# ============================================================================
# API Endpoint Tests - Orders
# ============================================================================


@pytest.mark.asyncio()
@respx.mock
async def test_cancel_order(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test cancel_order calls the correct endpoint with audit payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/orders/order-123/cancel").mock(
        return_value=httpx.Response(200, json={"status": "cancelled"})
    )

    result = await trading_client.cancel_order(
        order_id="order-123",
        user_id="user-1",
        reason="Test cancel reason",
        requested_by="user-1",
        requested_at="2026-01-30T00:00:00+00:00",
    )

    assert result == {"status": "cancelled"}
    assert route.call_count == 1
    # Verify payload was sent
    request = route.calls[0].request
    import json

    payload = json.loads(request.content)
    assert payload["reason"] == "Test cancel reason"
    assert payload["requested_by"] == "user-1"
    assert payload["requested_at"] == "2026-01-30T00:00:00+00:00"


@pytest.mark.asyncio()
@respx.mock
async def test_submit_order(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit_order sends correct payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "new-order-123"})
    )

    order_data = {
        "symbol": "AAPL",
        "qty": 10,
        "side": "buy",
        "type": "market",
    }
    result = await trading_client.submit_order(order_data=order_data, user_id="user-1")

    assert result == {"order_id": "new-order-123"}
    assert route.call_count == 1
    request_json = json.loads(route.calls[0].request.content)
    assert request_json == order_data


@pytest.mark.asyncio()
@respx.mock
async def test_submit_manual_order(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test submit_manual_order sends correct payload to manual endpoint."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/manual/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "manual-order-123"})
    )

    order_data = {
        "symbol": "AAPL",
        "qty": 5,
        "side": "sell",
        "type": "limit",
        "limit_price": 155.0,
    }
    result = await trading_client.submit_manual_order(order_data=order_data, user_id="user-1")

    assert result == {"order_id": "manual-order-123"}
    assert route.call_count == 1


# ============================================================================
# API Endpoint Tests - Reconciliation
# ============================================================================


@pytest.mark.asyncio()
@respx.mock
async def test_run_fills_backfill(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test run_fills_backfill sends correct payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/reconciliation/fills-backfill").mock(
        return_value=httpx.Response(200, json={"backfilled": 10})
    )

    result = await trading_client.run_fills_backfill(
        user_id="user-1",
        lookback_hours=24,
        recalc_all_trades=True,
    )

    assert result == {"backfilled": 10}
    assert route.call_count == 1
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["lookback_hours"] == 24
    assert request_json["recalc_all_trades"] is True


@pytest.mark.asyncio()
@respx.mock
async def test_run_fills_backfill_default_params(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test run_fills_backfill with default parameters."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/reconciliation/fills-backfill").mock(
        return_value=httpx.Response(200, json={"backfilled": 5})
    )

    result = await trading_client.run_fills_backfill(user_id="user-1")

    assert result == {"backfilled": 5}
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["recalc_all_trades"] is False
    assert "lookback_hours" not in request_json


# ============================================================================
# API Endpoint Tests - Manual Controls (T5.3)
# ============================================================================


@pytest.mark.asyncio()
@respx.mock
async def test_close_position(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test close_position sends correct payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/positions/AAPL/close").mock(
        return_value=httpx.Response(
            200,
            json={"status": "closed", "order_id": "close-order-123", "qty_to_close": 100},
        )
    )

    result = await trading_client.close_position(
        symbol="aapl",  # Test lowercase is uppercased
        reason="Risk management - reducing exposure",
        requested_by="user-1",
        requested_at="2024-01-15T10:00:00Z",
        user_id="user-1",
        qty=50,
    )

    assert result["status"] == "closed"
    assert result["order_id"] == "close-order-123"
    assert route.call_count == 1
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["reason"] == "Risk management - reducing exposure"
    assert request_json["requested_by"] == "user-1"
    assert request_json["requested_at"] == "2024-01-15T10:00:00Z"
    assert request_json["qty"] == 50


@pytest.mark.asyncio()
@respx.mock
async def test_close_position_without_qty(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test close_position without optional qty (full close)."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/positions/AAPL/close").mock(
        return_value=httpx.Response(200, json={"status": "closed"})
    )

    result = await trading_client.close_position(
        symbol="AAPL",
        reason="Full position close",
        requested_by="user-1",
        requested_at="2024-01-15T10:00:00Z",
        user_id="user-1",
    )

    assert result == {"status": "closed"}
    request_json = json.loads(route.calls[0].request.content)
    assert "qty" not in request_json


@pytest.mark.asyncio()
@respx.mock
async def test_cancel_all_orders(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test cancel_all_orders sends correct payload."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/orders/cancel-all").mock(
        return_value=httpx.Response(
            200,
            json={
                "cancelled_count": 3,
                "order_ids": ["order-1", "order-2", "order-3"],
            },
        )
    )

    result = await trading_client.cancel_all_orders(
        symbol="aapl",  # Test lowercase is uppercased
        reason="Market volatility - canceling all",
        requested_by="user-1",
        requested_at="2024-01-15T10:00:00Z",
        user_id="user-1",
    )

    assert result["cancelled_count"] == 3
    assert len(result["order_ids"]) == 3
    assert route.call_count == 1
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["symbol"] == "AAPL"
    assert request_json["reason"] == "Market volatility - canceling all"


@pytest.mark.asyncio()
@respx.mock
async def test_flatten_all_positions(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test flatten_all_positions sends correct payload with MFA token."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.post("http://testserver/api/v1/positions/flatten-all").mock(
        return_value=httpx.Response(
            200,
            json={"positions_closed": 5, "orders_created": 5},
        )
    )

    result = await trading_client.flatten_all_positions(
        reason="Emergency flatten all positions",
        requested_by="user-1",
        requested_at="2024-01-15T10:00:00Z",
        id_token="mfa-token-123",
        user_id="user-1",
    )

    assert result["positions_closed"] == 5
    assert result["orders_created"] == 5
    assert route.call_count == 1
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["reason"] == "Emergency flatten all positions"
    assert request_json["requested_by"] == "user-1"
    assert request_json["requested_at"] == "2024-01-15T10:00:00Z"
    assert request_json["id_token"] == "mfa-token-123"


# ============================================================================
# API Endpoint Tests - Kill Switch Status Edge Cases
# ============================================================================


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_kill_switch_status_engaged(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_kill_switch_status returns ENGAGED state as-is."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/kill-switch/status").mock(
        return_value=httpx.Response(200, json={"state": "ENGAGED"})
    )

    result = await trading_client.fetch_kill_switch_status("user-1")

    # ENGAGED should remain ENGAGED (not mapped)
    assert result["state"] == "ENGAGED"
    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_kill_switch_status_disengaged(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test fetch_kill_switch_status returns DISENGAGED state as-is."""
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    respx.get("http://testserver/api/v1/kill-switch/status").mock(
        return_value=httpx.Response(200, json={"state": "DISENGAGED"})
    )

    result = await trading_client.fetch_kill_switch_status("user-1")

    # DISENGAGED should remain DISENGAGED
    assert result["state"] == "DISENGAGED"
