"""Tests for OrderEntryContext."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from apps.web_console_ng.components.order_entry_context import (
    OrderEntryContext,
)


class TestOrderEntryContextInit:
    """Tests for OrderEntryContext initialization."""

    def test_initial_state(self) -> None:
        """Component initializes with correct default state."""
        realtime = MagicMock()
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()
        redis = MagicMock()

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            redis=redis,
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

        assert ctx._subscriptions == []
        assert ctx._timers == []
        assert ctx._disposed is False
        assert ctx._channel_owners == {}
        assert ctx._selected_symbol is None
        assert ctx._selection_version == 0

    def test_owner_constants_defined(self) -> None:
        """Owner constants are properly defined."""
        assert OrderEntryContext.OWNER_SELECTED_SYMBOL == "selected_symbol"
        assert OrderEntryContext.OWNER_WATCHLIST == "watchlist"
        assert OrderEntryContext.OWNER_POSITIONS == "positions"
        assert OrderEntryContext.OWNER_KILL_SWITCH == "kill_switch"
        assert OrderEntryContext.OWNER_CIRCUIT_BREAKER == "circuit_breaker"
        assert OrderEntryContext.OWNER_CONNECTION == "connection"

    def test_market_data_source_tag_is_bounded_and_valid(self) -> None:
        """Generated source tag must satisfy market-data service validation rules."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=MagicMock(),
            user_id="oauth|tenant:user@example.com",
            role="trader",
            strategies=["alpha"],
            client_id="browser-session-123",
        )

        assert len(ctx._market_data_source) <= 64
        assert re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", ctx._market_data_source)
        assert "oauth|tenant:user@example.com" not in ctx._market_data_source
        assert ctx._market_data_source.startswith(f"{OrderEntryContext.MARKET_DATA_SOURCE_PREFIX}:")

    def test_market_data_source_tag_is_recoverable_per_client(self) -> None:
        """Same user/client must derive the same source tag for orphan cleanup recovery."""
        common_kwargs = {
            "realtime_updater": MagicMock(),
            "trading_client": MagicMock(),
            "state_manager": MagicMock(),
            "connection_monitor": MagicMock(),
            "redis": MagicMock(),
            "user_id": "test-user",
            "role": "trader",
            "strategies": ["alpha"],
        }

        ctx_a = OrderEntryContext(**common_kwargs, client_id="client-a")
        ctx_a_repeat = OrderEntryContext(**common_kwargs, client_id="client-a")
        ctx_b = OrderEntryContext(**common_kwargs, client_id="client-b")

        assert ctx_a._market_data_source == ctx_a_repeat._market_data_source
        assert ctx_a._market_data_source != ctx_b._market_data_source


class TestOrderEntryContextComponentSetters:
    """Tests for component setter methods."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context for testing."""
        return OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=MagicMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

    def test_set_order_ticket(self, context: OrderEntryContext) -> None:
        """set_order_ticket stores component reference."""
        order_ticket = MagicMock()
        context.set_order_ticket(order_ticket)
        assert context._order_ticket is order_ticket

    def test_set_market_context(self, context: OrderEntryContext) -> None:
        """set_market_context stores component reference."""
        market_context = MagicMock()
        context.set_market_context(market_context)
        assert context._market_context is market_context

    def test_set_watchlist(self, context: OrderEntryContext) -> None:
        """set_watchlist stores component reference."""
        watchlist = MagicMock()
        context.set_watchlist(watchlist)
        assert context._watchlist is watchlist

    def test_set_price_chart(self, context: OrderEntryContext) -> None:
        """set_price_chart stores component reference."""
        price_chart = MagicMock()
        context.set_price_chart(price_chart)
        assert context._price_chart is price_chart

    def test_set_connection_state_callback(self, context: OrderEntryContext) -> None:
        """set_connection_state_callback stores callback reference."""
        callback = MagicMock()
        context.set_connection_state_callback(callback)
        assert context._connection_state_callback is callback

    def test_set_strategy_context_widget(self, context: OrderEntryContext) -> None:
        """set_strategy_context_widget stores component reference."""
        strategy_widget = MagicMock()
        context.set_strategy_context_widget(strategy_widget)
        assert context._strategy_context_widget is strategy_widget


class TestStrategyModelContextDispatch:
    """Tests strategy/model context dispatching to child components."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with strategy-relevant components."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=MagicMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        ctx._strategy_context_widget = MagicMock()
        return ctx

    def test_dispatch_strategy_model_context_forwards_to_ticket_and_widget(
        self, context: OrderEntryContext
    ) -> None:
        """Dispatch forwards normalized state to both consumers."""
        context.dispatch_strategy_model_context(
            strategy_status="active",
            model_status="testing",
            gate_enabled=True,
            gate_reason="manual review",
            strategy_label="Strategy: alpha",
            model_label="Model: v2.1",
            banner="Execution context healthy.",
        )

        context._order_ticket.set_strategy_model_context.assert_called_once_with(
            strategy_status="active",
            model_status="testing",
            gate_enabled=True,
            gate_reason="manual review",
        )
        context._strategy_context_widget.set_status.assert_called_once_with(
            strategy_status="active",
            model_status="testing",
            gate_enabled=True,
            gate_reason="manual review",
            strategy_label="Strategy: alpha",
            model_label="Model: v2.1",
            banner="Execution context healthy.",
        )

    def test_dispatch_strategy_model_context_handles_missing_components(
        self, context: OrderEntryContext
    ) -> None:
        """Dispatch is a no-op when target components are absent."""
        context._order_ticket = None
        context._strategy_context_widget = None

        context.dispatch_strategy_model_context(
            strategy_status=None,
            model_status=None,
            gate_enabled=False,
        )


class TestOrderEntryContextInitialize:
    """Tests for initialize() startup behavior."""

    @pytest.mark.asyncio()
    async def test_initialize_bootstraps_connected_state_when_unset(self) -> None:
        """initialize() seeds CONNECTED state if no connection event arrives."""
        realtime = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=connection_monitor,
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        ctx._order_ticket.initialize = AsyncMock()
        ctx._order_ticket.set_connection_state = MagicMock()
        callback = MagicMock()
        ctx.set_connection_state_callback(callback)

        ctx._subscribe_to_kill_switch_channel = AsyncMock()
        ctx._subscribe_to_circuit_breaker_channel = AsyncMock()
        ctx._subscribe_to_connection_channel = AsyncMock()
        ctx._subscribe_to_positions_channel = AsyncMock()
        ctx._fetch_initial_safety_state = AsyncMock()
        ctx._load_initial_risk_limits = AsyncMock()

        with patch("apps.web_console_ng.components.order_entry_context.ui.timer") as timer_mock:
            timer_mock.return_value = MagicMock()
            await ctx.initialize()

        assert ctx._cached_connection_state == "CONNECTED"
        ctx._order_ticket.set_connection_state.assert_called_with("CONNECTED", False)
        callback.assert_called_with("CONNECTED", False)

    @pytest.mark.asyncio()
    async def test_initialize_bootstraps_disconnected_state_when_read_only(self) -> None:
        """initialize() seeds DISCONNECTED state when monitor is read-only."""
        realtime = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = True

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=connection_monitor,
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        ctx._order_ticket.initialize = AsyncMock()
        ctx._order_ticket.set_connection_state = MagicMock()

        ctx._subscribe_to_kill_switch_channel = AsyncMock()
        ctx._subscribe_to_circuit_breaker_channel = AsyncMock()
        ctx._subscribe_to_connection_channel = AsyncMock()
        ctx._subscribe_to_positions_channel = AsyncMock()
        ctx._fetch_initial_safety_state = AsyncMock()
        ctx._load_initial_risk_limits = AsyncMock()

        with patch("apps.web_console_ng.components.order_entry_context.ui.timer") as timer_mock:
            timer_mock.return_value = MagicMock()
            await ctx.initialize()

        assert ctx._cached_connection_state == "DISCONNECTED"
        ctx._order_ticket.set_connection_state.assert_called_with("DISCONNECTED", True)

    @pytest.mark.asyncio()
    async def test_initialize_auto_selects_first_watchlist_symbol(self) -> None:
        """initialize() auto-selects the first watchlist symbol when none selected."""
        realtime = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=connection_monitor,
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._watchlist = MagicMock()
        ctx._watchlist.initialize = AsyncMock()
        ctx._watchlist.get_symbols.return_value = ["SPY", "QQQ"]
        ctx._order_ticket = MagicMock()
        ctx._order_ticket.initialize = AsyncMock()
        ctx._order_ticket.set_connection_state = MagicMock()
        ctx.on_symbol_selected = AsyncMock()

        ctx._subscribe_to_kill_switch_channel = AsyncMock()
        ctx._subscribe_to_circuit_breaker_channel = AsyncMock()
        ctx._subscribe_to_connection_channel = AsyncMock()
        ctx._subscribe_to_positions_channel = AsyncMock()
        ctx._fetch_initial_safety_state = AsyncMock()
        ctx._load_initial_risk_limits = AsyncMock()

        with patch("apps.web_console_ng.components.order_entry_context.ui.timer") as timer_mock:
            timer_mock.return_value = MagicMock()
            await ctx.initialize()

        ctx.on_symbol_selected.assert_awaited_once_with("SPY")

    @pytest.mark.asyncio()
    async def test_initialize_preserves_restored_ticket_symbol(self) -> None:
        """Restored pending-form symbol should override watchlist default auto-select."""
        realtime = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=connection_monitor,
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._watchlist = MagicMock()
        ctx._watchlist.initialize = AsyncMock()
        ctx._watchlist.get_symbols.return_value = ["SPY", "QQQ"]
        ctx._order_ticket = MagicMock()
        ctx._order_ticket.initialize = AsyncMock()
        ctx._order_ticket.set_connection_state = MagicMock()
        ctx._order_ticket.get_current_symbol.return_value = "TSLA"

        async def _select_symbol(symbol: str | None) -> None:
            ctx._selected_symbol = symbol

        ctx.on_symbol_selected = AsyncMock(side_effect=_select_symbol)

        ctx._subscribe_to_kill_switch_channel = AsyncMock()
        ctx._subscribe_to_circuit_breaker_channel = AsyncMock()
        ctx._subscribe_to_connection_channel = AsyncMock()
        ctx._subscribe_to_positions_channel = AsyncMock()
        ctx._fetch_initial_safety_state = AsyncMock()
        ctx._load_initial_risk_limits = AsyncMock()

        with patch("apps.web_console_ng.components.order_entry_context.ui.timer") as timer_mock:
            timer_mock.return_value = MagicMock()
            await ctx.initialize()

        ctx.on_symbol_selected.assert_awaited_once_with("TSLA")

    @pytest.mark.asyncio()
    async def test_initialize_falls_back_to_watchlist_when_restored_symbol_noops(self) -> None:
        """Fallback should run when restored symbol selection silently no-ops."""
        realtime = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=connection_monitor,
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._watchlist = MagicMock()
        ctx._watchlist.initialize = AsyncMock()
        ctx._watchlist.get_symbols.return_value = ["SPY", "QQQ"]
        ctx._order_ticket = MagicMock()
        ctx._order_ticket.initialize = AsyncMock()
        ctx._order_ticket.set_connection_state = MagicMock()
        ctx._order_ticket.get_current_symbol.return_value = "TSLA"
        ctx.on_symbol_selected = AsyncMock(side_effect=[None, None])

        ctx._subscribe_to_kill_switch_channel = AsyncMock()
        ctx._subscribe_to_circuit_breaker_channel = AsyncMock()
        ctx._subscribe_to_connection_channel = AsyncMock()
        ctx._subscribe_to_positions_channel = AsyncMock()
        ctx._fetch_initial_safety_state = AsyncMock()
        ctx._load_initial_risk_limits = AsyncMock()

        with patch("apps.web_console_ng.components.order_entry_context.ui.timer") as timer_mock:
            timer_mock.return_value = MagicMock()
            await ctx.initialize()

        assert ctx.on_symbol_selected.await_args_list == [call("TSLA"), call("SPY")]

    @pytest.mark.asyncio()
    async def test_initialize_does_not_schedule_market_data_sync_before_late_failures(self) -> None:
        """Initialization failures before completion must not trigger market-data sync."""
        realtime = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        ctx = OrderEntryContext(
            realtime_updater=realtime,
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=connection_monitor,
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        ctx._order_ticket.initialize = AsyncMock()
        ctx._order_ticket.set_connection_state = MagicMock()
        ctx._order_ticket.set_circuit_breaker_state = MagicMock()
        ctx._order_ticket.set_kill_switch_state = MagicMock()

        ctx._subscribe_to_kill_switch_channel = AsyncMock()
        ctx._subscribe_to_circuit_breaker_channel = AsyncMock()
        ctx._subscribe_to_connection_channel = AsyncMock()
        ctx._subscribe_to_positions_channel = AsyncMock()
        ctx._fetch_initial_safety_state = AsyncMock()
        ctx._load_initial_risk_limits = AsyncMock(side_effect=RuntimeError("boom"))
        ctx._schedule_market_data_sync = MagicMock()

        with patch("apps.web_console_ng.components.order_entry_context.ui.timer") as timer_mock:
            timer_mock.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="boom"):
                await ctx.initialize()

        ctx._schedule_market_data_sync.assert_not_called()


class TestFetchInitialSafetyState:
    """Tests for _fetch_initial_safety_state method."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked order ticket."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        return ctx

    @pytest.mark.asyncio()
    async def test_circuit_breaker_open_state(self, context: OrderEntryContext) -> None:
        """OPEN circuit breaker with valid reset_at is not tripped."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps(
                    {
                        "state": "OPEN",
                        "reset_at": "2024-01-01T12:00:00Z",
                    }
                ),
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(False, None)

    @pytest.mark.asyncio()
    async def test_circuit_breaker_tripped_state(self, context: OrderEntryContext) -> None:
        """TRIPPED circuit breaker is tripped."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps(
                    {
                        "state": "TRIPPED",
                        "trip_reason": "Drawdown exceeded",
                    }
                ),
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            True, "Drawdown exceeded"
        )

    @pytest.mark.asyncio()
    async def test_circuit_breaker_quiet_period(self, context: OrderEntryContext) -> None:
        """Legacy QUIET_PERIOD is treated as open after quiet period removal."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps({"state": "QUIET_PERIOD"}),
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            False, None
        )

    @pytest.mark.asyncio()
    async def test_circuit_breaker_never_tripped(self, context: OrderEntryContext) -> None:
        """OPEN circuit breaker that was never tripped (no reset_at, no tripped_at) is valid."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps({"state": "OPEN"}),
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(False, None)

    @pytest.mark.asyncio()
    async def test_circuit_breaker_missing_key(self, context: OrderEntryContext) -> None:
        """Missing circuit breaker key is fail-closed."""
        context._redis.get = AsyncMock(
            side_effect=[
                None,  # Missing CB key
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            True, "Initial state: Unknown/missing"
        )

    @pytest.mark.asyncio()
    async def test_kill_switch_active_state(self, context: OrderEntryContext) -> None:
        """ACTIVE kill switch with valid disengaged_at is not engaged."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps({"state": "OPEN"}),
                json.dumps(
                    {
                        "state": "ACTIVE",
                        "disengaged_at": "2024-01-01T12:00:00Z",
                    }
                ),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_kill_switch_state.assert_called_with(False, None)

    @pytest.mark.asyncio()
    async def test_kill_switch_engaged_state(self, context: OrderEntryContext) -> None:
        """ENGAGED kill switch is engaged."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps({"state": "OPEN"}),
                json.dumps(
                    {
                        "state": "ENGAGED",
                        "engagement_reason": "Manual halt",
                    }
                ),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_kill_switch_state.assert_called_with(True, "Manual halt")

    @pytest.mark.asyncio()
    async def test_kill_switch_never_engaged(self, context: OrderEntryContext) -> None:
        """ACTIVE kill switch that was never engaged (no disengaged_at, no engaged_at) is valid."""
        context._redis.get = AsyncMock(
            side_effect=[
                json.dumps({"state": "OPEN"}),
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_kill_switch_state.assert_called_with(False, None)

    @pytest.mark.asyncio()
    async def test_timeout_is_fail_closed(self, context: OrderEntryContext) -> None:
        """Timeout fetching safety state is fail-closed."""

        async def slow_get(key: str) -> None:
            await asyncio.sleep(10)

        context._redis.get = slow_get  # type: ignore[method-assign]

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            True, "Safety state fetch timed out"
        )
        context._order_ticket.set_kill_switch_state.assert_called_with(
            True, "Safety state fetch timed out"
        )

    @pytest.mark.asyncio()
    async def test_invalid_json_is_fail_closed(self, context: OrderEntryContext) -> None:
        """Invalid JSON is fail-closed."""
        context._redis.get = AsyncMock(
            side_effect=[
                "not-valid-json",
                json.dumps({"state": "ACTIVE"}),
            ]
        )

        await context._fetch_initial_safety_state()

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            True, "Initial state: Invalid data"
        )


class TestRiskLimitsRefresh:
    """Tests for risk limits refresh mechanism."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked order ticket."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        return ctx

    def test_refresh_does_nothing_when_disposed(self, context: OrderEntryContext) -> None:
        """_refresh_risk_limits does nothing when disposed."""
        context._disposed = True

        # Should not raise or create task
        context._refresh_risk_limits()

        # No call to order_ticket since disposed
        context._order_ticket.set_risk_limits.assert_not_called()

    def test_refresh_does_nothing_without_order_ticket(self, context: OrderEntryContext) -> None:
        """_refresh_risk_limits does nothing without order ticket."""
        context._order_ticket = None

        # Should not raise
        context._refresh_risk_limits()

    @pytest.mark.asyncio()
    async def test_refresh_creates_task_for_async_load(self, context: OrderEntryContext) -> None:
        """_refresh_risk_limits creates async task for loading."""
        context._refresh_risk_limits()

        # Give the event loop a chance to run the task
        await asyncio.sleep(0.01)

        # Order ticket should have risk limits set
        context._order_ticket.set_risk_limits.assert_called_once()

    def test_refresh_interval_constant(self) -> None:
        """Refresh interval is configured to stay under staleness threshold."""
        # Staleness threshold is 5 minutes (300s), refresh should be under that
        assert OrderEntryContext.RISK_LIMITS_REFRESH_INTERVAL_S < 300
        # Should refresh frequently enough to never go stale
        assert OrderEntryContext.RISK_LIMITS_REFRESH_INTERVAL_S <= 240


class TestVerifySafetyState:
    """Tests for verify safety state methods."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context for testing."""
        return OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

    @pytest.mark.asyncio()
    async def test_verify_circuit_breaker_open(self, context: OrderEntryContext) -> None:
        """OPEN circuit breaker returns True."""
        context._redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "state": "OPEN",
                    "reset_at": "2024-01-01T12:00:00Z",
                }
            )
        )

        result = await context._verify_circuit_breaker_safe()

        assert result is True

    @pytest.mark.asyncio()
    async def test_verify_circuit_breaker_tripped(self, context: OrderEntryContext) -> None:
        """TRIPPED circuit breaker returns False."""
        context._redis.get = AsyncMock(return_value=json.dumps({"state": "TRIPPED"}))

        result = await context._verify_circuit_breaker_safe()

        assert result is False

    @pytest.mark.asyncio()
    async def test_verify_circuit_breaker_legacy_quiet_period(
        self, context: OrderEntryContext
    ) -> None:
        """Legacy QUIET_PERIOD is safe after quiet-period removal."""
        context._redis.get = AsyncMock(return_value=json.dumps({"state": "QUIET_PERIOD"}))

        result = await context._verify_circuit_breaker_safe()

        assert result is True

    @pytest.mark.asyncio()
    async def test_verify_circuit_breaker_missing(self, context: OrderEntryContext) -> None:
        """Missing circuit breaker returns False."""
        context._redis.get = AsyncMock(return_value=None)

        result = await context._verify_circuit_breaker_safe()

        assert result is False

    @pytest.mark.asyncio()
    async def test_verify_kill_switch_active(self, context: OrderEntryContext) -> None:
        """ACTIVE kill switch returns True."""
        context._redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "state": "ACTIVE",
                    "disengaged_at": "2024-01-01T12:00:00Z",
                }
            )
        )

        result = await context._verify_kill_switch_safe()

        assert result is True

    @pytest.mark.asyncio()
    async def test_verify_kill_switch_engaged(self, context: OrderEntryContext) -> None:
        """ENGAGED kill switch returns False."""
        context._redis.get = AsyncMock(return_value=json.dumps({"state": "ENGAGED"}))

        result = await context._verify_kill_switch_safe()

        assert result is False

    @pytest.mark.asyncio()
    async def test_verify_kill_switch_timeout(self, context: OrderEntryContext) -> None:
        """Timeout returns False (fail-closed)."""

        async def slow_get(key: str) -> None:
            await asyncio.sleep(10)

        context._redis.get = slow_get  # type: ignore[method-assign]

        result = await context._verify_kill_switch_safe()

        assert result is False


class TestSafetyStateCallbacks:
    """Tests for safety state update callbacks."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked order ticket."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        return ctx

    @pytest.mark.asyncio()
    async def test_kill_switch_update_active(self, context: OrderEntryContext) -> None:
        """ACTIVE kill switch update sets engaged=False."""
        await context._on_kill_switch_update(
            {
                "state": "ACTIVE",
                "disengaged_at": "2024-01-01T12:00:00Z",
            }
        )

        context._order_ticket.set_kill_switch_state.assert_called_with(False, None)

    @pytest.mark.asyncio()
    async def test_kill_switch_update_engaged(self, context: OrderEntryContext) -> None:
        """ENGAGED kill switch update sets engaged=True."""
        await context._on_kill_switch_update(
            {
                "state": "ENGAGED",
                "engagement_reason": "Manual halt",
            }
        )

        context._order_ticket.set_kill_switch_state.assert_called_with(True, "Manual halt")

    @pytest.mark.asyncio()
    async def test_kill_switch_update_invalid_type(self, context: OrderEntryContext) -> None:
        """Invalid data type is fail-closed."""
        await context._on_kill_switch_update("not-a-dict")  # type: ignore[arg-type]

        context._order_ticket.set_kill_switch_state.assert_called_with(
            True, "Invalid kill switch payload"
        )

    @pytest.mark.asyncio()
    async def test_kill_switch_update_malformed_state(self, context: OrderEntryContext) -> None:
        """Malformed state is fail-closed."""
        await context._on_kill_switch_update({"state": "UNKNOWN"})

        context._order_ticket.set_kill_switch_state.assert_called_with(
            True, "Malformed kill switch state"
        )

    @pytest.mark.asyncio()
    async def test_circuit_breaker_update_open(self, context: OrderEntryContext) -> None:
        """OPEN circuit breaker update sets tripped=False."""
        await context._on_circuit_breaker_update(
            {
                "state": "OPEN",
                "reset_at": "2024-01-01T12:00:00Z",
            }
        )

        context._order_ticket.set_circuit_breaker_state.assert_called_with(False, None)

    @pytest.mark.asyncio()
    async def test_circuit_breaker_update_tripped(self, context: OrderEntryContext) -> None:
        """TRIPPED circuit breaker update sets tripped=True."""
        await context._on_circuit_breaker_update(
            {
                "state": "TRIPPED",
                "trip_reason": "Drawdown exceeded",
            }
        )

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            True, "Drawdown exceeded"
        )

    @pytest.mark.asyncio()
    async def test_circuit_breaker_update_quiet_period(self, context: OrderEntryContext) -> None:
        """Legacy QUIET_PERIOD update is treated as open."""
        await context._on_circuit_breaker_update({"state": "QUIET_PERIOD"})

        context._order_ticket.set_circuit_breaker_state.assert_called_with(
            False, None
        )

    @pytest.mark.asyncio()
    async def test_disposed_ignores_updates(self, context: OrderEntryContext) -> None:
        """Disposed context ignores updates."""
        context._disposed = True

        await context._on_kill_switch_update({"state": "ENGAGED"})
        await context._on_circuit_breaker_update({"state": "TRIPPED"})

        context._order_ticket.set_kill_switch_state.assert_not_called()
        context._order_ticket.set_circuit_breaker_state.assert_not_called()


class TestConnectionStateCallback:
    """Tests for connection state update callback."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked order ticket."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        return ctx

    @pytest.mark.asyncio()
    async def test_connection_connected(self, context: OrderEntryContext) -> None:
        """CONNECTED state sets is_read_only=False."""
        callback = MagicMock()
        context.set_connection_state_callback(callback)

        await context._on_connection_update({"state": "CONNECTED"})

        context._order_ticket.set_connection_state.assert_called_with("CONNECTED", False)
        callback.assert_called_with("CONNECTED", False)

    @pytest.mark.asyncio()
    async def test_connection_disconnected(self, context: OrderEntryContext) -> None:
        """DISCONNECTED state sets is_read_only=True."""
        await context._on_connection_update({"state": "DISCONNECTED"})

        context._order_ticket.set_connection_state.assert_called_with("DISCONNECTED", True)

    @pytest.mark.asyncio()
    async def test_connection_reconnecting(self, context: OrderEntryContext) -> None:
        """RECONNECTING state sets is_read_only=True."""
        await context._on_connection_update({"state": "RECONNECTING"})

        context._order_ticket.set_connection_state.assert_called_with("RECONNECTING", True)

    @pytest.mark.asyncio()
    async def test_connection_degraded(self, context: OrderEntryContext) -> None:
        """DEGRADED state sets is_read_only=True."""
        await context._on_connection_update({"state": "DEGRADED"})

        context._order_ticket.set_connection_state.assert_called_with("DEGRADED", True)

    @pytest.mark.asyncio()
    async def test_connection_unknown_state(self, context: OrderEntryContext) -> None:
        """Unknown state sets is_read_only=True."""
        await context._on_connection_update({"state": "INVALID"})

        context._order_ticket.set_connection_state.assert_called_with("INVALID", True)

    @pytest.mark.asyncio()
    async def test_connection_invalid_payload(self, context: OrderEntryContext) -> None:
        """Invalid payload is treated as UNKNOWN."""
        callback = MagicMock()
        context.set_connection_state_callback(callback)

        await context._on_connection_update("not-a-dict")  # type: ignore[arg-type]

        context._order_ticket.set_connection_state.assert_called_with("UNKNOWN", True)
        callback.assert_called_with("UNKNOWN", True)


class TestPositionUpdateCallback:
    """Tests for position update callback."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked order ticket."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        ctx._selected_symbol = "AAPL"
        return ctx

    @pytest.mark.asyncio()
    async def test_position_update_for_selected_symbol(self, context: OrderEntryContext) -> None:
        """Position update for selected symbol dispatches to OrderTicket."""
        await context._on_position_update(
            {
                "positions": [{"symbol": "AAPL", "qty": 100}],
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        call_args = context._order_ticket.set_position_data.call_args
        assert call_args[0][0] == "AAPL"
        assert call_args[0][1] == 100
        assert call_args[0][2] is not None

    @pytest.mark.asyncio()
    async def test_position_update_symbol_not_in_list(self, context: OrderEntryContext) -> None:
        """Symbol not in positions sets qty=0 (position closed)."""
        await context._on_position_update(
            {
                "positions": [{"symbol": "MSFT", "qty": 50}],
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        call_args = context._order_ticket.set_position_data.call_args
        assert call_args[0][0] == "AAPL"
        assert call_args[0][1] == 0

    @pytest.mark.asyncio()
    async def test_position_update_invalid_qty(self, context: OrderEntryContext) -> None:
        """Invalid qty sets qty=0 and clears timestamp."""
        await context._on_position_update(
            {
                "positions": [{"symbol": "AAPL", "qty": "not-a-number"}],
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        call_args = context._order_ticket.set_position_data.call_args
        assert call_args[0][0] == "AAPL"
        assert call_args[0][1] == 0
        assert call_args[0][2] is None  # Timestamp cleared on invalid qty

    @pytest.mark.asyncio()
    async def test_position_update_missing_timestamp(self, context: OrderEntryContext) -> None:
        """Missing timestamp passes None (data marked stale)."""
        await context._on_position_update(
            {
                "positions": [{"symbol": "AAPL", "qty": 100}],
            }
        )

        call_args = context._order_ticket.set_position_data.call_args
        assert call_args[0][2] is None


class TestPriceUpdateCallback:
    """Tests for price update callback."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with all mocked components."""
        ctx = OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = MagicMock()
        ctx._market_context = MagicMock()
        ctx._price_chart = MagicMock()
        ctx._watchlist = MagicMock()
        ctx._selected_symbol = "AAPL"
        return ctx

    @pytest.mark.asyncio()
    async def test_price_update_dispatches_to_all_components(
        self, context: OrderEntryContext
    ) -> None:
        """Price update for selected symbol dispatches to all components."""
        await context._on_price_update(
            {
                "symbol": "AAPL",
                "price": "150.50",
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        # OrderTicket
        context._order_ticket.set_price_data.assert_called_once()
        call_args = context._order_ticket.set_price_data.call_args
        assert call_args[0][0] == "AAPL"
        assert call_args[0][1] == Decimal("150.50")
        assert call_args[0][2] is not None

        # MarketContext
        context._market_context.set_price_data.assert_called_once()

        # PriceChart
        context._price_chart.set_price_data.assert_called_once()

        # Watchlist (receives all symbols)
        context._watchlist.set_symbol_price_data.assert_called_once()

    @pytest.mark.asyncio()
    async def test_price_update_different_symbol_only_watchlist(
        self, context: OrderEntryContext
    ) -> None:
        """Price update for different symbol only goes to watchlist."""
        await context._on_price_update(
            {
                "symbol": "MSFT",
                "price": "350.00",
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        # OrderTicket, MarketContext, PriceChart should NOT be called
        context._order_ticket.set_price_data.assert_not_called()
        context._market_context.set_price_data.assert_not_called()
        context._price_chart.set_price_data.assert_not_called()

        # Watchlist should be called
        context._watchlist.set_symbol_price_data.assert_called_once()

    @pytest.mark.asyncio()
    async def test_price_update_invalid_price(self, context: OrderEntryContext) -> None:
        """Invalid price sets price=None and clears timestamp."""
        await context._on_price_update(
            {
                "symbol": "AAPL",
                "price": "not-a-number",
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        call_args = context._order_ticket.set_price_data.call_args
        assert call_args[0][1] is None  # Price is None
        assert call_args[0][2] is None  # Timestamp also cleared

    @pytest.mark.asyncio()
    async def test_price_update_negative_price(self, context: OrderEntryContext) -> None:
        """Negative price sets price=None."""
        await context._on_price_update(
            {
                "symbol": "AAPL",
                "price": "-50.00",
                "timestamp": "2024-01-01T12:00:00Z",
            }
        )

        call_args = context._order_ticket.set_price_data.call_args
        assert call_args[0][1] is None

    @pytest.mark.asyncio()
    async def test_price_update_applies_symbol_quantity_rules(
        self, context: OrderEntryContext
    ) -> None:
        """Selected-symbol price updates can carry quantity rule metadata."""
        await context._on_price_update(
            {
                "symbol": "AAPL",
                "price": "150.00",
                "timestamp": "2024-01-01T12:00:00Z",
                "qty_step": 100,
                "min_qty": 100,
                "qty_unit": "lots",
            }
        )

        context._order_ticket.set_quantity_rules.assert_called_once_with(
            qty_step=100,
            min_qty=100,
            qty_unit="lots",
            qty_unit_size=1,
        )

    @pytest.mark.asyncio()
    async def test_price_update_ignores_overflow_quantity_rule_metadata(
        self, context: OrderEntryContext
    ) -> None:
        """Malformed huge metadata must not break update dispatch."""
        await context._on_price_update(
            {
                "symbol": "AAPL",
                "price": "150.00",
                "timestamp": "2024-01-01T12:00:00Z",
                "qty_step": "1e309",
                "min_qty": "100",
                "qty_unit": "lots",
            }
        )

        context._order_ticket.set_price_data.assert_called_once()
        context._order_ticket.set_quantity_rules.assert_called_once_with(
            qty_step=1,
            min_qty=100,
            qty_unit="lots",
            qty_unit_size=1,
        )


class TestSymbolSelection:
    """Tests for symbol selection."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked components."""
        ctx = OrderEntryContext(
            realtime_updater=AsyncMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = AsyncMock()
        ctx._market_context = AsyncMock()
        ctx._price_chart = AsyncMock()
        return ctx

    @pytest.mark.asyncio()
    async def test_symbol_selection_updates_state(self, context: OrderEntryContext) -> None:
        """Symbol selection updates internal state."""
        await context.on_symbol_selected("AAPL")

        assert context._selected_symbol == "AAPL"

    @pytest.mark.asyncio()
    async def test_symbol_selection_normalizes_symbol(self, context: OrderEntryContext) -> None:
        """Symbol is normalized to uppercase."""
        await context.on_symbol_selected("aapl")

        assert context._selected_symbol == "AAPL"

    @pytest.mark.asyncio()
    async def test_symbol_selection_invalid_symbol(self, context: OrderEntryContext) -> None:
        """Invalid symbol is rejected."""
        await context.on_symbol_selected("INVALID-SYMBOL-TOO-LONG!")

        assert context._selected_symbol is None

    @pytest.mark.asyncio()
    async def test_symbol_selection_notifies_components(self, context: OrderEntryContext) -> None:
        """Symbol selection notifies all child components."""
        await context.on_symbol_selected("AAPL")

        context._order_ticket.on_symbol_changed.assert_called_with("AAPL")
        context._market_context.on_symbol_changed.assert_called_with("AAPL")
        context._price_chart.on_symbol_changed.assert_called_with("AAPL")

    @pytest.mark.asyncio()
    async def test_symbol_selection_notifies_registered_symbol_callbacks(
        self, context: OrderEntryContext
    ) -> None:
        """Symbol selection emits callbacks used by dashboard panels."""
        callback = MagicMock()
        context.register_symbol_change_callback(callback)

        await context.on_symbol_selected("AAPL")

        callback.assert_called_once_with("AAPL")

    def test_register_symbol_change_callback_deduplicates(
        self, context: OrderEntryContext
    ) -> None:
        """Duplicate registrations keep one callback instance."""
        callback = MagicMock()

        context.register_symbol_change_callback(callback)
        context.register_symbol_change_callback(callback)

        assert context._symbol_change_callbacks == [callback]

    @pytest.mark.asyncio()
    async def test_symbol_selection_applies_cached_quantity_rules(
        self, context: OrderEntryContext
    ) -> None:
        """Selecting a symbol applies previously cached quantity rules."""
        context._order_ticket.set_quantity_rules = MagicMock()
        context._symbol_quantity_rules["AAPL"] = (100, 100, "lots", 1)

        await context.on_symbol_selected("AAPL")

        context._order_ticket.set_quantity_rules.assert_called_once_with(
            qty_step=100,
            min_qty=100,
            qty_unit="lots",
            qty_unit_size=1,
        )

    @pytest.mark.asyncio()
    async def test_symbol_selection_none_clears_state(self, context: OrderEntryContext) -> None:
        """None symbol clears selection."""
        context._selected_symbol = "AAPL"

        await context.on_symbol_selected(None)

        assert context._selected_symbol is None

    @pytest.mark.asyncio()
    async def test_same_symbol_selection_no_op(self, context: OrderEntryContext) -> None:
        """Selecting same symbol is no-op."""
        context._selected_symbol = "AAPL"

        await context.on_symbol_selected("AAPL")

        # No component calls should be made
        context._order_ticket.on_symbol_changed.assert_not_called()

    @pytest.mark.asyncio()
    async def test_symbol_selection_increments_version(self, context: OrderEntryContext) -> None:
        """Symbol selection increments version for race prevention."""
        initial_version = context._selection_version

        await context.on_symbol_selected("AAPL")

        assert context._selection_version == initial_version + 1


class TestChannelOwnership:
    """Tests for channel ownership management."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context for testing."""
        return OrderEntryContext(
            realtime_updater=AsyncMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

    @pytest.mark.asyncio()
    async def test_acquire_channel_first_owner(self, context: OrderEntryContext) -> None:
        """First owner subscribes to channel."""
        callback = AsyncMock()

        await context._acquire_channel("test:channel", "owner1", callback)

        assert "test:channel" in context._channel_owners
        assert "owner1" in context._channel_owners["test:channel"]
        assert "test:channel" in context._subscriptions
        context._realtime.subscribe.assert_called_once()

    @pytest.mark.asyncio()
    async def test_acquire_channel_second_owner(self, context: OrderEntryContext) -> None:
        """Second owner adds ownership without re-subscribing."""
        callback = AsyncMock()

        await context._acquire_channel("test:channel", "owner1", callback)
        context._realtime.subscribe.reset_mock()

        await context._acquire_channel("test:channel", "owner2", callback)

        assert "owner1" in context._channel_owners["test:channel"]
        assert "owner2" in context._channel_owners["test:channel"]
        # No additional subscribe call
        context._realtime.subscribe.assert_not_called()

    @pytest.mark.asyncio()
    async def test_release_channel_not_last_owner(self, context: OrderEntryContext) -> None:
        """Releasing non-last owner doesn't unsubscribe."""
        callback = AsyncMock()

        await context._acquire_channel("test:channel", "owner1", callback)
        await context._acquire_channel("test:channel", "owner2", callback)

        await context._release_channel("test:channel", "owner1")

        assert "test:channel" in context._channel_owners
        assert "owner2" in context._channel_owners["test:channel"]
        assert "owner1" not in context._channel_owners["test:channel"]
        context._realtime.unsubscribe.assert_not_called()

    @pytest.mark.asyncio()
    async def test_release_channel_last_owner(self, context: OrderEntryContext) -> None:
        """Releasing last owner unsubscribes."""
        callback = AsyncMock()

        await context._acquire_channel("test:channel", "owner1", callback)
        await context._release_channel("test:channel", "owner1")

        assert "test:channel" not in context._channel_owners
        assert "test:channel" not in context._subscriptions
        context._realtime.unsubscribe.assert_called_once()

    @pytest.mark.asyncio()
    async def test_release_channel_offloads_market_data_release(self, context: OrderEntryContext) -> None:
        """Price channel release should not block on upstream unsubscribe retries."""
        callback = AsyncMock()
        await context._acquire_channel("price.updated.AAPL", "owner1", callback)

        release_started = asyncio.Event()
        release_continue = asyncio.Event()

        async def fake_release(symbol: str) -> None:
            assert symbol == "AAPL"
            release_started.set()
            await release_continue.wait()

        context._release_market_data_streaming = fake_release  # type: ignore[method-assign]
        context._schedule_market_data_sync = MagicMock()

        await asyncio.wait_for(context._release_channel("price.updated.AAPL", "owner1"), timeout=0.1)
        await asyncio.wait_for(release_started.wait(), timeout=0.1)

        # Sync should trigger only after background release completes.
        context._schedule_market_data_sync.assert_not_called()
        release_continue.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        context._schedule_market_data_sync.assert_called_once()

    @pytest.mark.asyncio()
    async def test_callback_mismatch_raises(self, context: OrderEntryContext) -> None:
        """Different callback for same channel raises ValueError."""
        callback1 = AsyncMock()
        callback2 = AsyncMock()

        await context._acquire_channel("test:channel", "owner1", callback1)

        with pytest.raises(ValueError, match="Callback mismatch"):
            await context._acquire_channel("test:channel", "owner2", callback2)

    @pytest.mark.asyncio()
    async def test_same_bound_method_callback_allowed(self, context: OrderEntryContext) -> None:
        """Same bound method accessed twice is allowed (equality not identity).

        Regression test: bound methods create new objects on each access,
        so using identity (is not) would fail even for the same method.
        We use equality (!=) which compares the underlying function and instance.
        """

        # Create a helper class with a bound method
        class Helper:
            async def callback(self, data: dict) -> None:
                pass

        helper = Helper()

        # Pass the same bound method twice (but accessed separately)
        # This simulates watchlist and selected symbol sharing a price callback
        await context._acquire_channel("prices:AAPL", "watchlist", helper.callback)

        # This should NOT raise - same method, different access creates new object
        # but they are equal via __eq__
        await context._acquire_channel("prices:AAPL", "selected", helper.callback)

        # Both owners should be registered
        assert "watchlist" in context._channel_owners["prices:AAPL"]
        assert "selected" in context._channel_owners["prices:AAPL"]


class TestMarketDataSync:
    """Tests for market-data stream synchronization helpers."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        return OrderEntryContext(
            realtime_updater=AsyncMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

    def test_collect_owned_price_symbols_filters_and_sorts(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {
            "price.updated.msft": {"watchlist"},
            "price.updated.AAPL": {"selected_symbol"},
            "orders:test-user": {"orders"},
        }

        symbols = context._collect_owned_price_symbols()

        assert symbols == ["AAPL", "MSFT"]

    def test_collect_owned_price_symbols_skips_pending_subscriptions(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {
            "price.updated.msft": {"watchlist"},
            "price.updated.AAPL": {"selected_symbol"},
        }
        context._pending_subscribes = {"price.updated.msft": MagicMock()}

        symbols = context._collect_owned_price_symbols()

        assert symbols == ["AAPL"]

    @pytest.mark.asyncio()
    async def test_sync_market_data_streaming_batches_symbols(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {
            "price.updated.MSFT": {"watchlist"},
            "price.updated.AAPL": {"watchlist"},
        }
        context._client.subscribe_market_data_symbols = AsyncMock()

        await context._sync_market_data_streaming()

        context._client.subscribe_market_data_symbols.assert_awaited_once_with(
            ["AAPL", "MSFT"],
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
            source=context._market_data_source,
        )

    @pytest.mark.asyncio()
    async def test_acquire_channel_defers_market_data_sync_while_initializing(
        self, context: OrderEntryContext
    ) -> None:
        context._initializing = True
        context._schedule_market_data_sync = MagicMock()
        callback = AsyncMock()

        await context._acquire_channel("price.updated.AAPL", "watchlist", callback)

        context._schedule_market_data_sync.assert_not_called()
        assert context._last_synced_market_data_symbols is None

    @pytest.mark.asyncio()
    async def test_acquire_channel_schedules_market_data_sync_after_initialize(
        self, context: OrderEntryContext
    ) -> None:
        context._initializing = False
        context._schedule_market_data_sync = MagicMock()
        callback = AsyncMock()

        await context._acquire_channel("price.updated.AAPL", "watchlist", callback)

        context._schedule_market_data_sync.assert_called_once()

    @pytest.mark.asyncio()
    async def test_schedule_market_data_sync_requeues_when_inflight(
        self, context: OrderEntryContext
    ) -> None:
        """A sync request arriving mid-flight should trigger one follow-up sync."""
        run_count = 0
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def fake_sync() -> None:
            nonlocal run_count
            run_count += 1
            if run_count == 1:
                first_started.set()
                await release_first.wait()

        context._sync_market_data_streaming = fake_sync  # type: ignore[method-assign]

        context._schedule_market_data_sync()
        await first_started.wait()
        context._schedule_market_data_sync()

        assert context._market_data_sync_pending is True
        release_first.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        if context._market_data_sync_task is not None:
            await context._market_data_sync_task

        assert run_count == 2
        assert context._market_data_sync_pending is False

    @pytest.mark.asyncio()
    async def test_schedule_market_data_sync_backoff_retries_after_failure(
        self, context: OrderEntryContext
    ) -> None:
        """Failed sync should retry once after backoff instead of hot-looping immediately."""
        run_count = 0
        context.MARKET_DATA_SYNC_RETRY_DELAY_S = 0

        async def fake_sync() -> None:
            nonlocal run_count
            run_count += 1
            if run_count == 1:
                context._market_data_sync_pending = True
                context._market_data_sync_backoff_required = True

        context._sync_market_data_streaming = fake_sync  # type: ignore[method-assign]

        context._schedule_market_data_sync()

        # First sync completes and schedules delayed retry.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert context._market_data_retry_task is not None

        await context._market_data_retry_task
        if context._market_data_sync_task is not None:
            await context._market_data_sync_task

        assert run_count == 2
        assert context._market_data_sync_pending is False

    @pytest.mark.asyncio()
    async def test_sync_market_data_streaming_tracks_race_during_subscribe(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {"price.updated.AAPL": {"watchlist"}}

        async def fake_subscribe(*args: object, **kwargs: object) -> None:
            # Simulate symbol ownership changing while subscribe call is in-flight.
            context._channel_owners = {}

        context._client.subscribe_market_data_symbols = AsyncMock(side_effect=fake_subscribe)

        await context._sync_market_data_streaming()

        assert context._market_data_sync_pending is True
        assert context._last_synced_market_data_symbols is None
        assert context._pending_market_data_unsubscribes == {"AAPL"}

    @pytest.mark.asyncio()
    async def test_sync_market_data_streaming_retries_pending_unsubscribes_when_empty(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {}
        context._last_synced_market_data_symbols = ("AAPL",)
        context._pending_market_data_unsubscribes = {"AAPL"}
        context._client.unsubscribe_market_data_symbol = AsyncMock(
            return_value={"message": "ok", "remaining_subscriptions": 0}
        )

        await context._sync_market_data_streaming()

        context._client.unsubscribe_market_data_symbol.assert_awaited_once_with(
            "AAPL",
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
            source=context._market_data_source,
        )
        assert context._pending_market_data_unsubscribes == set()
        assert context._last_synced_market_data_symbols == ()

    @pytest.mark.asyncio()
    async def test_sync_market_data_streaming_continues_after_stale_release_failure(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {"price.updated.MSFT": {"watchlist"}}
        context._last_synced_market_data_symbols = ("AAPL",)
        context._client.unsubscribe_market_data_symbol = AsyncMock(
            side_effect=[RuntimeError("down"), RuntimeError("still-down")]
        )
        context._client.subscribe_market_data_symbols = AsyncMock(
            return_value={"message": "ok", "subscribed_symbols": ["MSFT"], "total_subscriptions": 1}
        )
        context.MARKET_DATA_UNSUBSCRIBE_RETRY_DELAY_S = 0

        await context._sync_market_data_streaming()

        context._client.subscribe_market_data_symbols.assert_awaited_once_with(
            ["MSFT"],
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
            source=context._market_data_source,
        )
        assert context._pending_market_data_unsubscribes == {"AAPL"}
        assert context._market_data_sync_pending is True

    @pytest.mark.asyncio()
    async def test_sync_market_data_streaming_marks_pending_after_subscribe_failure(
        self, context: OrderEntryContext
    ) -> None:
        context._channel_owners = {"price.updated.AAPL": {"watchlist"}}
        context._client.subscribe_market_data_symbols = AsyncMock(
            side_effect=RuntimeError("temporary upstream outage")
        )

        with pytest.raises(RuntimeError, match="temporary upstream outage"):
            await context._sync_market_data_streaming()

        assert context._last_synced_market_data_symbols is None
        assert context._market_data_sync_pending is True

    @pytest.mark.asyncio()
    async def test_release_market_data_streaming_uses_session_source(
        self,
        context: OrderEntryContext,
    ) -> None:
        context._client.unsubscribe_market_data_symbol = AsyncMock()

        await context._release_market_data_streaming("AAPL")

        context._client.unsubscribe_market_data_symbol.assert_awaited_once_with(
            "AAPL",
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
            source=context._market_data_source,
        )

    @pytest.mark.asyncio()
    async def test_release_market_data_streaming_skips_when_unsubscribe_missing(
        self,
        context: OrderEntryContext,
    ) -> None:
        context._client.unsubscribe_market_data_symbol = None  # type: ignore[assignment]

        await context._release_market_data_streaming("AAPL")

        assert context._pending_market_data_unsubscribes == set()

    @pytest.mark.asyncio()
    async def test_release_market_data_streaming_retries_and_persists_failed_symbol(
        self,
        context: OrderEntryContext,
    ) -> None:
        context._client.unsubscribe_market_data_symbol = AsyncMock(
            side_effect=[RuntimeError("network"), RuntimeError("still-down")]
        )

        with pytest.raises(RuntimeError, match="still-down"):
            await context._release_market_data_streaming("AAPL")

        assert context._client.unsubscribe_market_data_symbol.await_count == 2
        assert context._pending_market_data_unsubscribes == {"AAPL"}
        assert context._last_synced_market_data_symbols is None
        assert context._market_data_sync_pending is True

    @pytest.mark.asyncio()
    async def test_resubscribe_forces_market_data_sync_even_with_same_symbols(
        self,
        context: OrderEntryContext,
    ) -> None:
        callback = AsyncMock()
        context._subscriptions = ["price.updated.AAPL"]
        context._channel_owners = {"price.updated.AAPL": {"watchlist"}}
        context._channel_callbacks = {"price.updated.AAPL": callback}
        context._last_synced_market_data_symbols = ("AAPL",)
        context._schedule_market_data_sync = MagicMock()

        await context._resubscribe_all_channels()

        context._realtime.subscribe.assert_awaited_once_with("price.updated.AAPL", callback)
        assert context._last_synced_market_data_symbols is None
        context._schedule_market_data_sync.assert_called_once()


class TestDispose:
    """Tests for dispose/cleanup."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context with mocked components."""
        ctx = OrderEntryContext(
            realtime_updater=AsyncMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )
        ctx._order_ticket = AsyncMock()
        ctx._market_context = AsyncMock()
        ctx._price_chart = AsyncMock()
        ctx._watchlist = AsyncMock()
        ctx._client.unsubscribe_market_data_symbol = AsyncMock()
        return ctx

    @pytest.mark.asyncio()
    async def test_dispose_sets_flag(self, context: OrderEntryContext) -> None:
        """dispose() sets disposed flag."""
        await context.dispose()

        assert context._disposed is True

    @pytest.mark.asyncio()
    async def test_dispose_clears_state(self, context: OrderEntryContext) -> None:
        """dispose() clears internal state."""
        context._subscriptions = ["channel1", "channel2"]
        context._channel_owners = {"channel1": {"owner1"}}
        context._channel_callbacks = {"channel1": MagicMock()}

        await context.dispose()

        assert context._subscriptions == []
        assert context._channel_owners == {}
        assert context._channel_callbacks == {}

    @pytest.mark.asyncio()
    async def test_dispose_cancels_timers(self, context: OrderEntryContext) -> None:
        """dispose() cancels all tracked timers."""
        mock_timer1 = MagicMock()
        mock_timer2 = MagicMock()
        context._timers = [mock_timer1, mock_timer2]

        await context.dispose()

        mock_timer1.cancel.assert_called_once()
        mock_timer2.cancel.assert_called_once()
        assert context._timers == []

    @pytest.mark.asyncio()
    async def test_dispose_unsubscribes_channels(self, context: OrderEntryContext) -> None:
        """dispose() unsubscribes from all channels."""
        context._subscriptions = ["channel1", "channel2"]

        await context.dispose()

        assert context._realtime.unsubscribe.call_count == 2

    @pytest.mark.asyncio()
    async def test_dispose_releases_owned_market_data_sources(
        self, context: OrderEntryContext
    ) -> None:
        """dispose() should release per-session market-data sources for owned symbols."""
        context._subscriptions = ["price.updated.AAPL", "price.updated.msft"]
        context._channel_owners = {
            "price.updated.AAPL": {"watchlist"},
            "price.updated.msft": {"selected_symbol"},
            "orders:test-user": {"orders"},
        }

        await context.dispose()

        context._client.unsubscribe_market_data_symbol.assert_has_awaits(
            [
                call(
                    "AAPL",
                    user_id="test-user",
                    role="trader",
                    strategies=["alpha"],
                    source=context._market_data_source,
                ),
                call(
                    "MSFT",
                    user_id="test-user",
                    role="trader",
                    strategies=["alpha"],
                    source=context._market_data_source,
                ),
            ],
            any_order=True,
        )

    @pytest.mark.asyncio()
    async def test_dispose_releases_pending_market_data_unsubscribes(
        self, context: OrderEntryContext
    ) -> None:
        """dispose() should also flush symbols queued for retry unsubscribe."""
        context._pending_market_data_unsubscribes = {"AAPL"}

        await context.dispose()

        context._client.unsubscribe_market_data_symbol.assert_awaited_once_with(
            "AAPL",
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
            source=context._market_data_source,
        )

    @pytest.mark.asyncio()
    async def test_dispose_disposes_components(self, context: OrderEntryContext) -> None:
        """dispose() disposes all child components."""
        await context.dispose()

        context._order_ticket.dispose.assert_called_once()
        context._market_context.dispose.assert_called_once()
        context._price_chart.dispose.assert_called_once()
        context._watchlist.dispose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_dispose_idempotent(self, context: OrderEntryContext) -> None:
        """dispose() is idempotent."""
        await context.dispose()
        await context.dispose()

        # Should only dispose once
        assert context._order_ticket.dispose.call_count == 1

    @pytest.mark.asyncio()
    async def test_dispose_cancels_pending_futures(self, context: OrderEntryContext) -> None:
        """dispose() cancels pending subscribe futures."""
        future = asyncio.get_running_loop().create_future()
        context._pending_subscribes = {"test:channel": future}

        await context.dispose()

        assert future.done()
        with pytest.raises(asyncio.CancelledError):
            future.result()

    @pytest.mark.asyncio()
    async def test_dispose_cancels_market_data_retry_task(self, context: OrderEntryContext) -> None:
        """dispose() cancels any queued market-data backoff retry task."""
        retry_task = asyncio.create_task(asyncio.sleep(60))
        context._market_data_retry_task = retry_task

        await context.dispose()
        await asyncio.sleep(0)

        assert retry_task.cancelled()
        assert context._market_data_retry_task is None

    @pytest.mark.asyncio()
    async def test_dispose_cancels_market_data_release_tasks(self, context: OrderEntryContext) -> None:
        """dispose() cancels in-flight async release tasks."""
        context.MARKET_DATA_RELEASE_FLUSH_TIMEOUT_S = 0
        release_task = asyncio.create_task(asyncio.sleep(60))
        context._market_data_release_tasks.add(release_task)

        await context.dispose()
        await asyncio.sleep(0)

        assert release_task.cancelled()
        assert context._market_data_release_tasks == set()

    @pytest.mark.asyncio()
    async def test_dispose_flushes_completed_market_data_release_tasks(
        self, context: OrderEntryContext
    ) -> None:
        """dispose() should allow already-running release tasks to finish cleanly."""
        release_started = asyncio.Event()
        allow_release_complete = asyncio.Event()

        async def slow_release() -> None:
            release_started.set()
            await allow_release_complete.wait()

        release_task = asyncio.create_task(slow_release())
        context._market_data_release_tasks.add(release_task)

        dispose_task = asyncio.create_task(context.dispose())
        await release_started.wait()
        assert not dispose_task.done()

        allow_release_complete.set()
        await dispose_task

        assert release_task.done()
        assert not release_task.cancelled()
        assert context._market_data_release_tasks == set()

    @pytest.mark.asyncio()
    async def test_dispose_cancels_risk_refresh_task(self, context: OrderEntryContext) -> None:
        """dispose() cancels running risk refresh task."""

        # Create a slow task
        async def slow_task() -> None:
            await asyncio.sleep(100)

        task = asyncio.create_task(slow_task())
        context._risk_refresh_task = task

        await context.dispose()

        assert task.cancelled() or context._risk_refresh_task is None


class TestGetters:
    """Tests for getter methods."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context for testing."""
        return OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

    def test_get_selected_symbol(self, context: OrderEntryContext) -> None:
        """get_selected_symbol returns current selection."""
        assert context.get_selected_symbol() is None

        context._selected_symbol = "AAPL"

        assert context.get_selected_symbol() == "AAPL"

    def test_get_verify_circuit_breaker(self, context: OrderEntryContext) -> None:
        """get_verify_circuit_breaker returns callable."""
        callback = context.get_verify_circuit_breaker()

        # Verify it returns the verification method (bound methods compare equal)
        assert callback.__name__ == "_verify_circuit_breaker_safe"
        assert callable(callback)

    def test_get_verify_kill_switch(self, context: OrderEntryContext) -> None:
        """get_verify_kill_switch returns callable."""
        callback = context.get_verify_kill_switch()

        # Verify it returns the verification method (bound methods compare equal)
        assert callback.__name__ == "_verify_kill_switch_safe"
        assert callable(callback)


class TestFactoryMethods:
    """Tests for component factory methods."""

    @pytest.fixture()
    def context(self) -> OrderEntryContext:
        """Create context for testing."""
        return OrderEntryContext(
            realtime_updater=MagicMock(),
            trading_client=MagicMock(),
            state_manager=MagicMock(),
            connection_monitor=MagicMock(),
            redis=AsyncMock(),
            user_id="test-user",
            role="trader",
            strategies=["alpha"],
        )

    def test_create_watchlist_stores_reference(self, context: OrderEntryContext) -> None:
        """create_watchlist stores component reference."""
        with patch("apps.web_console_ng.components.watchlist.WatchlistComponent") as MockWatchlist:
            mock_instance = MagicMock()
            MockWatchlist.return_value = mock_instance

            context.create_watchlist()

            assert context._watchlist is mock_instance
            mock_instance.create.assert_called_once()

    def test_create_watchlist_wires_callbacks(self, context: OrderEntryContext) -> None:
        """create_watchlist wires up callbacks correctly."""
        with patch("apps.web_console_ng.components.watchlist.WatchlistComponent") as MockWatchlist:
            context.create_watchlist()

            call_kwargs = MockWatchlist.call_args.kwargs
            # Use __name__ comparison as bound methods create new objects each access
            assert call_kwargs["on_symbol_selected"].__name__ == "on_symbol_selected"
            assert call_kwargs["on_subscribe_symbol"].__name__ == "on_watchlist_subscribe_request"
            assert (
                call_kwargs["on_unsubscribe_symbol"].__name__ == "on_watchlist_unsubscribe_request"
            )

    def test_create_market_context_stores_reference(self, context: OrderEntryContext) -> None:
        """create_market_context stores component reference."""
        with patch(
            "apps.web_console_ng.components.market_context.MarketContextComponent"
        ) as MockMarket:
            mock_instance = MagicMock()
            MockMarket.return_value = mock_instance

            context.create_market_context()

            assert context._market_context is mock_instance
            mock_instance.create.assert_called_once()

    def test_create_price_chart_stores_reference(self, context: OrderEntryContext) -> None:
        """create_price_chart stores component reference."""
        with patch("apps.web_console_ng.components.price_chart.PriceChartComponent") as MockChart:
            mock_instance = MagicMock()
            MockChart.return_value = mock_instance

            context.create_price_chart(width=800, height=400)

            assert context._price_chart is mock_instance
            mock_instance.create.assert_called_once_with(
                width=800,
                height=400,
                fill_parent=False,
            )

    def test_create_order_ticket_stores_reference(self, context: OrderEntryContext) -> None:
        """create_order_ticket stores component reference."""
        with patch(
            "apps.web_console_ng.components.order_ticket.OrderTicketComponent"
        ) as MockTicket:
            mock_instance = MagicMock()
            MockTicket.return_value = mock_instance

            context.create_order_ticket()

            assert context._order_ticket is mock_instance
            mock_instance.create.assert_called_once()

    def test_create_order_ticket_wires_verification_callbacks(
        self, context: OrderEntryContext
    ) -> None:
        """create_order_ticket wires up safety verification callbacks."""
        with patch(
            "apps.web_console_ng.components.order_ticket.OrderTicketComponent"
        ) as MockTicket:
            context.create_order_ticket()

            call_kwargs = MockTicket.call_args.kwargs
            assert call_kwargs["verify_circuit_breaker"].__name__ == "_verify_circuit_breaker_safe"
            assert call_kwargs["verify_kill_switch"].__name__ == "_verify_kill_switch_safe"
            assert call_kwargs["user_id"] == "test-user"
            assert call_kwargs["role"] == "trader"
            assert call_kwargs["strategies"] == ["alpha"]

    # NOTE: test_on_market_context_price_updated_* tests were removed because
    # _on_market_context_price_updated method was removed to avoid redundant
    # double-dispatch of price updates. OrderEntryContext._on_price_update
    # now directly updates OrderTicket.
