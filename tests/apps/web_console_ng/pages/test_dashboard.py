from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from apps.web_console_ng.pages import dashboard as dashboard_module


class DummyTradingClient:
    def __init__(self) -> None:
        self.calls = 0
        self.prices = [{"symbol": "AAPL", "price": 150.0}]

    async def fetch_market_prices(
        self,
        user_id: str,
        *,
        role: str | None = None,
        strategies: list[str] | None = None,
    ):
        self.calls += 1
        return list(self.prices)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset cache state before each test."""
    dashboard_module.MarketPriceCache._cache = {}
    dashboard_module.MarketPriceCache._in_flight = {}
    yield
    dashboard_module.MarketPriceCache._cache = {}
    dashboard_module.MarketPriceCache._in_flight = {}


@pytest.mark.asyncio()
async def test_market_price_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that cache returns cached data within TTL."""
    client = DummyTradingClient()
    strategies = ["alpha_baseline"]

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    first = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=strategies
    )
    assert client.calls == 1
    assert first == client.prices

    # Within TTL (4 seconds), should return cached data without fetching
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1001.0)
    second = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=strategies
    )
    assert client.calls == 1  # No new fetch
    assert second == client.prices


@pytest.mark.asyncio()
async def test_market_price_cache_error_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that cache returns stale data during error cooldown."""
    client = DummyTradingClient()
    strategies = ["alpha_baseline"]
    scope_key = ("admin", frozenset(strategies))

    # Pre-populate cache with stale data and error state
    dashboard_module.MarketPriceCache._cache[scope_key] = {
        "prices": [{"symbol": "AAPL", "price": 150.0}],
        "last_fetch": 0.0,
        "last_error": 1002.0,
    }

    # Time is within error cooldown (10 seconds)
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1005.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=strategies
    )

    # Should return cached data without fetching
    assert prices == [{"symbol": "AAPL", "price": 150.0}]
    assert client.calls == 0


@pytest.mark.asyncio()
async def test_market_price_cache_strategy_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that different strategy scopes have separate caches."""
    client = DummyTradingClient()

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)

    # User 1 with strategy A
    strategies_a = ["strategy_a"]
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="operator", strategies=strategies_a
    )
    assert client.calls == 1

    # User 2 with strategy B should trigger new fetch (different scope)
    strategies_b = ["strategy_b"]
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user2", role="operator", strategies=strategies_b
    )
    assert client.calls == 2  # New fetch for different scope

    # User 3 with same strategy A should use cached data
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user3", role="operator", strategies=strategies_a
    )
    assert client.calls == 2  # No new fetch, same scope as user1


def test_dashboard_has_trade_alias_route() -> None:
    """Dashboard page keeps '/' canonical and exposes '/trade' alias."""
    dashboard_source = inspect.getsource(dashboard_module.dashboard)
    alias_source = inspect.getsource(dashboard_module.dashboard_trade_alias)
    assert '@ui.page("/")' in dashboard_source
    assert '@ui.page("/trade")' in alias_source


def test_dashboard_session_expiry_redirects_to_login() -> None:
    source = inspect.getsource(dashboard_module.dashboard)
    assert 'with_root_path("/login"' in source


def test_dashboard_handoff_defers_market_data_release_until_replacement_context() -> None:
    source = inspect.getsource(dashboard_module.dashboard)
    assert "is_handoff = active_generation_id != order_context_generation_id" in source
    assert 'client.storage["deferred_market_data_release_symbols"]' in source


def test_trade_alias_redirect_target_preserves_safe_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dummy_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(
                    query_params=SimpleNamespace(
                        multi_items=lambda: [
                            ("symbol", "AAPL"),
                            ("qty", "5"),
                            ("side", "buy"),
                            ("foo", "ignored"),
                        ]
                    )
                )
            )
        )
    )
    monkeypatch.setattr(
        dashboard_module,
        "resolve_rooted_path_from_ui",
        lambda _path, *, ui_module: "/console",
    )

    target = dashboard_module._build_trade_alias_redirect_target(ui_module=dummy_ui)
    assert target == "/console?symbol=AAPL&qty=5&side=buy"


def test_workspace_circuit_breaker_control_states() -> None:
    assert dashboard_module.resolve_workspace_circuit_breaker_control(
        "OPEN",
        can_trip=True,
        can_reset=True,
    ) == (
        "lock",
        "normal",
        "Halt new order entries after confirmation",
        True,
        "trip",
    )

    assert dashboard_module.resolve_workspace_circuit_breaker_control(
        "TRIPPED",
        can_trip=True,
        can_reset=True,
    ) == (
        "lock_open",
        "danger",
        "Resume trading after confirmation",
        True,
        "reset",
    )

    assert dashboard_module.resolve_workspace_circuit_breaker_control(
        "UNKNOWN",
        can_trip=True,
        can_reset=True,
    ) == (
        "help_outline",
        "muted",
        "Breaker status unknown: check connection before changing state",
        False,
        "none",
    )


def test_dashboard_removes_standalone_circuit_breaker_page_from_quick_links() -> None:
    links = dashboard_module.resolve_workspace_quick_links(
        user_role="admin",
        feature_alerts_enabled=True,
        can_view_alerts=True,
        can_view_data_quality=True,
        feature_strategy_management_enabled=True,
        can_manage_strategies=True,
        feature_model_registry_enabled=True,
        can_view_models=True,
    )
    assert "/circuit-breaker" not in {path for _, path in links}
