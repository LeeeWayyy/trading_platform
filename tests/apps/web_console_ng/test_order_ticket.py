"""Tests for OrderTicketComponent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.web_console_ng.components.order_ticket import (
    BUYING_POWER_STALE_THRESHOLD_S,
    LIMITS_STALE_THRESHOLD_S,
    POSITION_STALE_THRESHOLD_S,
    PRICE_STALE_THRESHOLD_S,
    OrderTicketComponent,
    OrderTicketState,
)


class TestOrderTicketState:
    """Tests for OrderTicketState dataclass."""

    def test_default_values(self) -> None:
        """Default state has safe defaults."""
        state = OrderTicketState()

        assert state.symbol is None
        assert state.side == "buy"
        assert state.quantity is None
        assert state.order_type == "market"
        assert state.limit_price is None
        assert state.stop_price is None
        assert state.time_in_force == "day"

    def test_custom_values(self) -> None:
        """State accepts custom values."""
        state = OrderTicketState(
            symbol="AAPL",
            side="sell",
            quantity=100,
            order_type="limit",
            limit_price=Decimal("150.00"),
            time_in_force="gtc",
        )

        assert state.symbol == "AAPL"
        assert state.side == "sell"
        assert state.quantity == 100
        assert state.order_type == "limit"
        assert state.limit_price == Decimal("150.00")
        assert state.time_in_force == "gtc"


class TestOrderTicketInit:
    """Tests for OrderTicketComponent initialization."""

    def test_fail_closed_defaults(self) -> None:
        """Component initializes with fail-closed safety defaults."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=["alpha"],
        )

        # FAIL-CLOSED: Defaults to unsafe until confirmed otherwise
        assert comp._kill_switch_engaged is True
        assert comp._circuit_breaker_tripped is True
        assert comp._safety_state_loaded is False
        assert comp._limits_loaded is False

    def test_tab_session_id_unique(self) -> None:
        """Each instance gets unique tab session ID."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp1 = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        comp2 = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

        assert comp1._tab_session_id != comp2._tab_session_id
        assert len(comp1._tab_session_id) == 16


class TestOrderTicketSafetyChecks:
    """Tests for safety check methods."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        return comp

    def test_blocks_when_safety_not_loaded(self, component: OrderTicketComponent) -> None:
        """Submission blocked until safety state loaded."""
        component._safety_state_loaded = False

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Safety state loading" in reason

    def test_blocks_when_connection_readonly(self, component: OrderTicketComponent) -> None:
        """Submission blocked when connection is read-only."""
        component._safety_state_loaded = True
        component._connection_read_only = True

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Connection unavailable" in reason

    def test_set_connection_state_stores_read_only(
        self, component: OrderTicketComponent
    ) -> None:
        """set_connection_state stores the read-only state for later checks."""
        # Initially True (fail-closed default)
        assert component._connection_read_only is True

        # Update to connected (not read-only)
        component.set_connection_state("CONNECTED", is_read_only=False)
        assert component._connection_read_only is False

        # Update to disconnected (read-only)
        component.set_connection_state("DISCONNECTED", is_read_only=True)
        assert component._connection_read_only is True

    def test_kill_switch_update_respects_connection_state(
        self, component: OrderTicketComponent
    ) -> None:
        """Kill switch disable doesn't re-enable UI when connection is read-only."""
        # Setup: connection is read-only
        component._connection_read_only = True
        component._circuit_breaker_tripped = False
        component._ui_disabled = True  # Currently disabled

        # Kill switch disengages, but connection still read-only
        component.set_kill_switch_state(False, None)

        # Should still be disabled due to connection
        disabled, reason = component._should_disable_submission()
        assert disabled is True
        assert "Connection unavailable" in reason

    def test_circuit_breaker_update_respects_connection_state(
        self, component: OrderTicketComponent
    ) -> None:
        """Circuit breaker reset doesn't re-enable UI when connection is read-only."""
        # Setup: connection is read-only, safety loaded
        component._safety_state_loaded = True
        component._connection_read_only = True
        component._kill_switch_engaged = False
        component._ui_disabled = True  # Currently disabled

        # Circuit breaker resets, but connection still read-only
        component.set_circuit_breaker_state(False, None)

        # Should still be disabled due to connection
        disabled, reason = component._should_disable_submission()
        assert disabled is True
        assert "Connection unavailable" in reason

    def test_blocks_when_kill_switch_engaged(self, component: OrderTicketComponent) -> None:
        """Submission blocked when kill switch engaged."""
        component._safety_state_loaded = True
        component._connection_read_only = False  # Connection available
        component._kill_switch_engaged = True
        component._circuit_breaker_tripped = False

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Kill switch engaged" in reason

    def test_blocks_when_circuit_breaker_tripped(self, component: OrderTicketComponent) -> None:
        """Submission blocked when circuit breaker tripped."""
        component._safety_state_loaded = True
        component._connection_read_only = False  # Connection available
        component._kill_switch_engaged = False
        component._circuit_breaker_tripped = True

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Circuit breaker tripped" in reason

    def test_blocks_when_no_symbol(self, component: OrderTicketComponent) -> None:
        """Submission blocked when no symbol selected."""
        component._safety_state_loaded = True
        component._connection_read_only = False  # Connection available
        component._kill_switch_engaged = False
        component._circuit_breaker_tripped = False
        component._state.symbol = None

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Select a symbol" in reason

    def test_blocks_when_no_quantity(self, component: OrderTicketComponent) -> None:
        """Submission blocked when no quantity entered."""
        component._safety_state_loaded = True
        component._connection_read_only = False  # Connection available
        component._kill_switch_engaged = False
        component._circuit_breaker_tripped = False
        component._state.symbol = "AAPL"
        component._state.quantity = None

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Enter quantity" in reason

    def test_blocks_when_limits_not_loaded(self, component: OrderTicketComponent) -> None:
        """Submission blocked until risk limits loaded."""
        component._safety_state_loaded = True
        component._connection_read_only = False  # Connection available
        component._kill_switch_engaged = False
        component._circuit_breaker_tripped = False
        component._state.symbol = "AAPL"
        component._state.quantity = 100
        component._position_last_updated = datetime.now(UTC)
        component._price_last_updated = datetime.now(UTC)
        component._buying_power_last_updated = datetime.now(UTC)
        component._limits_loaded = False

        disabled, reason = component._should_disable_submission()

        assert disabled is True
        assert "Risk limits loading" in reason


class TestOrderTicketStalenessChecks:
    """Tests for data staleness checks."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        return OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

    def test_position_stale_when_none(self, component: OrderTicketComponent) -> None:
        """Position is stale when timestamp is None."""
        component._position_last_updated = None

        assert component._is_position_data_stale() is True

    def test_position_stale_when_old(self, component: OrderTicketComponent) -> None:
        """Position is stale when older than threshold."""
        component._position_last_updated = datetime.now(UTC) - timedelta(
            seconds=POSITION_STALE_THRESHOLD_S + 1
        )

        assert component._is_position_data_stale() is True

    def test_position_fresh_when_recent(self, component: OrderTicketComponent) -> None:
        """Position is fresh when within threshold."""
        component._position_last_updated = datetime.now(UTC) - timedelta(seconds=5)

        assert component._is_position_data_stale() is False

    def test_price_stale_when_none(self, component: OrderTicketComponent) -> None:
        """Price is stale when timestamp is None."""
        component._price_last_updated = None

        assert component._is_price_data_stale() is True

    def test_price_stale_when_old(self, component: OrderTicketComponent) -> None:
        """Price is stale when older than threshold."""
        component._price_last_updated = datetime.now(UTC) - timedelta(
            seconds=PRICE_STALE_THRESHOLD_S + 1
        )

        assert component._is_price_data_stale() is True

    def test_buying_power_stale_when_none(self, component: OrderTicketComponent) -> None:
        """Buying power is stale when timestamp is None."""
        component._buying_power_last_updated = None

        assert component._is_buying_power_stale() is True

    def test_buying_power_stale_when_old(self, component: OrderTicketComponent) -> None:
        """Buying power is stale when older than threshold."""
        component._buying_power_last_updated = datetime.now(UTC) - timedelta(
            seconds=BUYING_POWER_STALE_THRESHOLD_S + 1
        )

        assert component._is_buying_power_stale() is True

    def test_limits_stale_when_none(self, component: OrderTicketComponent) -> None:
        """Limits are stale when timestamp is None."""
        component._limits_last_updated = None

        assert component._is_limits_stale() is True

    def test_limits_stale_when_old(self, component: OrderTicketComponent) -> None:
        """Limits are stale when older than threshold."""
        component._limits_last_updated = datetime.now(UTC) - timedelta(
            seconds=LIMITS_STALE_THRESHOLD_S + 1
        )

        assert component._is_limits_stale() is True


class TestOrderTicketPriceValidation:
    """Tests for order type price validation."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        return OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

    def test_market_order_no_price_required(self, component: OrderTicketComponent) -> None:
        """Market orders don't require price fields."""
        component._state.order_type = "market"

        result = component._validate_order_type_prices()

        assert result is None

    def test_limit_order_requires_limit_price(self, component: OrderTicketComponent) -> None:
        """Limit orders require limit price."""
        component._state.order_type = "limit"
        component._state.limit_price = None

        result = component._validate_order_type_prices()

        assert result is not None
        assert "limit price" in result.lower()

    def test_limit_order_rejects_zero_price(self, component: OrderTicketComponent) -> None:
        """Limit orders reject zero price."""
        component._state.order_type = "limit"
        component._state.limit_price = Decimal("0")

        result = component._validate_order_type_prices()

        assert result is not None
        assert "positive" in result.lower()

    def test_stop_order_requires_stop_price(self, component: OrderTicketComponent) -> None:
        """Stop orders require stop price."""
        component._state.order_type = "stop"
        component._state.stop_price = None

        result = component._validate_order_type_prices()

        assert result is not None
        assert "stop price" in result.lower()

    def test_stop_limit_requires_both_prices(self, component: OrderTicketComponent) -> None:
        """Stop-limit orders require both prices."""
        component._state.order_type = "stop_limit"
        component._state.limit_price = Decimal("100")
        component._state.stop_price = None

        result = component._validate_order_type_prices()

        assert result is not None
        assert "stop price" in result.lower()

    def test_buy_stop_limit_price_relationship(self, component: OrderTicketComponent) -> None:
        """Buy stop-limit: limit must be <= stop."""
        component._state.order_type = "stop_limit"
        component._state.side = "buy"
        component._state.stop_price = Decimal("100")
        component._state.limit_price = Decimal("110")  # Above stop = invalid

        result = component._validate_order_type_prices()

        assert result is not None
        assert "at or below" in result.lower()

    def test_sell_stop_limit_price_relationship(self, component: OrderTicketComponent) -> None:
        """Sell stop-limit: limit must be >= stop."""
        component._state.order_type = "stop_limit"
        component._state.side = "sell"
        component._state.stop_price = Decimal("100")
        component._state.limit_price = Decimal("90")  # Below stop = invalid

        result = component._validate_order_type_prices()

        assert result is not None
        assert "at or above" in result.lower()

    def test_valid_stop_limit_passes(self, component: OrderTicketComponent) -> None:
        """Valid stop-limit configuration passes."""
        component._state.order_type = "stop_limit"
        component._state.side = "buy"
        component._state.stop_price = Decimal("100")
        component._state.limit_price = Decimal("95")  # Below stop = valid for buy

        result = component._validate_order_type_prices()

        assert result is None


class TestOrderTicketPositionLimits:
    """Tests for position limit checks."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        comp._state.symbol = "AAPL"
        comp._state.quantity = 100
        comp._state.side = "buy"
        comp._last_price = Decimal("150")
        return comp

    def test_no_limit_configured_passes(self, component: OrderTicketComponent) -> None:
        """No violation when no limits configured."""
        component._max_position_per_symbol = None
        component._max_notional_per_order = None
        component._max_total_exposure = None

        result = component._check_position_limits()

        assert result is None

    def test_buy_exceeds_position_limit(self, component: OrderTicketComponent) -> None:
        """Violation when buy would exceed position limit."""
        component._current_position = 50
        component._state.quantity = 100
        component._max_position_per_symbol = 100  # Limit is 100

        result = component._check_position_limits()

        assert result is not None
        assert "position limit" in result.lower()

    def test_sell_within_position_limit(self, component: OrderTicketComponent) -> None:
        """No violation when sell stays within limit."""
        component._current_position = 50
        component._state.side = "sell"
        component._state.quantity = 30
        component._max_position_per_symbol = 100

        result = component._check_position_limits()

        assert result is None

    def test_buy_exceeds_notional_limit(self, component: OrderTicketComponent) -> None:
        """Violation when buy exceeds notional limit."""
        component._state.quantity = 100
        component._last_price = Decimal("150")
        component._max_notional_per_order = Decimal("10000")  # 100 * 150 = 15000 > 10000

        result = component._check_position_limits()

        assert result is not None
        assert "notional" in result.lower()


class TestOrderTicketEffectivePrice:
    """Tests for effective order price calculation."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        comp._last_price = Decimal("150")
        return comp

    def test_market_uses_last_price(self, component: OrderTicketComponent) -> None:
        """Market orders use last traded price."""
        component._state.order_type = "market"

        result = component._get_effective_order_price()

        assert result == Decimal("150")

    def test_limit_uses_limit_price(self, component: OrderTicketComponent) -> None:
        """Limit orders use limit price."""
        component._state.order_type = "limit"
        component._state.limit_price = Decimal("145")

        result = component._get_effective_order_price()

        assert result == Decimal("145")

    def test_buy_stop_uses_max_for_worst_case(
        self, component: OrderTicketComponent
    ) -> None:
        """Buy stop orders use max(stop, last) for worst-case estimate."""
        component._state.order_type = "stop"
        component._state.side = "buy"
        component._state.stop_price = Decimal("160")  # Above last

        result = component._get_effective_order_price()

        assert result == Decimal("160")  # max(160, 150)

    def test_sell_stop_uses_min_for_worst_case(
        self, component: OrderTicketComponent
    ) -> None:
        """Sell stop orders use min(stop, last) for worst-case estimate.

        This prevents overstating notional for risk-reducing sell stops
        where the stop price is typically below market price.
        """
        component._state.order_type = "stop"
        component._state.side = "sell"
        component._state.stop_price = Decimal("140")  # Below last
        component._last_price = Decimal("150")

        result = component._get_effective_order_price()

        assert result == Decimal("140")  # min(140, 150) - sell at lower price

    def test_stop_limit_uses_limit_price(self, component: OrderTicketComponent) -> None:
        """Stop-limit orders use limit price."""
        component._state.order_type = "stop_limit"
        component._state.limit_price = Decimal("140")
        component._state.stop_price = Decimal("145")

        result = component._get_effective_order_price()

        assert result == Decimal("140")


class TestOrderTicketStateCallbacks:
    """Tests for state update callbacks."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        return OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

    def test_set_kill_switch_state_engaged(self, component: OrderTicketComponent) -> None:
        """Kill switch state callback updates internal state."""
        component.set_kill_switch_state(engaged=True, reason="Test halt")

        assert component._kill_switch_engaged is True
        assert component._safety_state_loaded is True

    def test_set_kill_switch_state_disengaged(self, component: OrderTicketComponent) -> None:
        """Kill switch state callback clears engaged state."""
        component._circuit_breaker_tripped = False
        component._connection_monitor.is_read_only.return_value = False

        component.set_kill_switch_state(engaged=False, reason=None)

        assert component._kill_switch_engaged is False
        assert component._safety_state_loaded is True

    def test_set_circuit_breaker_state_tripped(self, component: OrderTicketComponent) -> None:
        """Circuit breaker state callback updates internal state."""
        component.set_circuit_breaker_state(tripped=True, reason="Drawdown breach")

        assert component._circuit_breaker_tripped is True

    def test_set_price_data_updates_for_selected_symbol(
        self, component: OrderTicketComponent
    ) -> None:
        """Price data callback updates when symbol matches."""
        component._state.symbol = "AAPL"
        timestamp = datetime.now(UTC)

        component.set_price_data("AAPL", Decimal("155.50"), timestamp)

        assert component._last_price == Decimal("155.50")
        assert component._price_last_updated == timestamp

    def test_set_price_data_ignores_other_symbol(self, component: OrderTicketComponent) -> None:
        """Price data callback ignores non-selected symbols."""
        component._state.symbol = "AAPL"
        component._last_price = Decimal("150")

        component.set_price_data("MSFT", Decimal("400"), datetime.now(UTC))

        assert component._last_price == Decimal("150")  # Unchanged

    def test_set_position_data_updates_for_selected_symbol(
        self, component: OrderTicketComponent
    ) -> None:
        """Position data callback updates when symbol matches."""
        component._state.symbol = "AAPL"
        timestamp = datetime.now(UTC)

        component.set_position_data("AAPL", 100, timestamp)

        assert component._current_position == 100
        assert component._position_last_updated == timestamp

    def test_set_buying_power_updates(self, component: OrderTicketComponent) -> None:
        """Buying power callback updates internal state."""
        timestamp = datetime.now(UTC)

        component.set_buying_power(Decimal("50000"), timestamp)

        assert component._buying_power == Decimal("50000")
        assert component._buying_power_last_updated == timestamp

    def test_set_risk_limits_updates(self, component: OrderTicketComponent) -> None:
        """Risk limits callback updates internal state."""
        timestamp = datetime.now(UTC)

        component.set_risk_limits(
            max_position_per_symbol=500,
            max_notional_per_order=Decimal("25000"),
            max_total_exposure=Decimal("100000"),
            timestamp=timestamp,
        )

        assert component._max_position_per_symbol == 500
        assert component._max_notional_per_order == Decimal("25000")
        assert component._max_total_exposure == Decimal("100000")
        assert component._limits_last_updated == timestamp
        assert component._limits_loaded is True

    def test_set_total_exposure_updates(self, component: OrderTicketComponent) -> None:
        """Total exposure callback updates internal state."""
        component.set_total_exposure(Decimal("75000"))

        assert component._current_total_exposure == Decimal("75000")

    def test_set_total_exposure_accepts_none(self, component: OrderTicketComponent) -> None:
        """Total exposure callback accepts None (fail-closed: blocks submission)."""
        component._current_total_exposure = Decimal("50000")

        component.set_total_exposure(None)

        assert component._current_total_exposure is None


class TestOrderTicketExposureLimits:
    """Tests for total exposure limit enforcement."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component with exposure limit configured."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        comp._state.symbol = "AAPL"
        comp._state.quantity = 100
        comp._state.side = "buy"
        comp._last_price = Decimal("150")
        comp._max_total_exposure = Decimal("100000")  # 100k limit
        return comp

    def test_exposure_check_passes_when_within_limit(
        self, component: OrderTicketComponent
    ) -> None:
        """Exposure check passes when projected exposure within limit."""
        component._current_position = 0
        component._current_total_exposure = Decimal("50000")  # Existing 50k

        # Order: 100 shares * $150 = $15,000
        # Projected: 50k existing + 15k new = 65k < 100k limit
        result = component._check_position_limits()

        assert result is None  # No violation

    def test_exposure_check_fails_when_exceeds_limit(
        self, component: OrderTicketComponent
    ) -> None:
        """Exposure check fails when projected exposure exceeds limit."""
        component._current_position = 0
        component._current_total_exposure = Decimal("90000")  # Existing 90k
        component._state.quantity = 100  # 100 * 150 = 15k

        # Projected: 90k + 15k = 105k > 100k limit
        result = component._check_position_limits()

        assert result is not None
        assert "exposure limit" in result.lower()

    def test_exposure_check_fails_when_exposure_unavailable(
        self, component: OrderTicketComponent
    ) -> None:
        """Exposure check fails (fail-closed) when exposure data unavailable."""
        component._current_total_exposure = None  # Unavailable

        result = component._check_position_limits()

        assert result is not None
        assert "verify exposure" in result.lower()

    def test_exposure_check_skipped_when_no_limit_configured(
        self, component: OrderTicketComponent
    ) -> None:
        """Exposure check skipped when no max_total_exposure configured."""
        component._max_total_exposure = None
        component._current_total_exposure = None  # Would fail if checked

        result = component._check_position_limits()

        # Should pass - exposure check not performed without limit
        assert result is None


class TestOrderTicketBuyingPowerImpact:
    """Tests for buying power impact calculation."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        comp._state.symbol = "AAPL"
        comp._last_price = Decimal("100")
        comp._buying_power = Decimal("10000")
        return comp

    def test_impact_no_quantity(self, component: OrderTicketComponent) -> None:
        """Impact returns None when no quantity."""
        component._state.quantity = None

        result = component._calculate_buying_power_impact()

        assert result["notional"] is None
        assert result["percentage"] is None

    def test_impact_calculation(self, component: OrderTicketComponent) -> None:
        """Impact calculates correctly."""
        component._state.quantity = 50  # 50 * 100 = 5000

        result = component._calculate_buying_power_impact()

        assert result["notional"] == Decimal("5000")
        assert result["percentage"] == Decimal("50")  # 5000/10000 * 100
        assert result["remaining"] == Decimal("5000")  # 10000 - 5000
        assert result["warning"] is False  # 50% is not > 50%

    def test_impact_warning_over_50_percent(self, component: OrderTicketComponent) -> None:
        """Impact warns when over 50% of buying power."""
        component._state.quantity = 60  # 60 * 100 = 6000 = 60%

        result = component._calculate_buying_power_impact()

        assert result["percentage"] == Decimal("60")
        assert result["warning"] is True

    def test_impact_no_buying_power(self, component: OrderTicketComponent) -> None:
        """Impact handles missing buying power."""
        component._state.quantity = 50
        component._buying_power = None

        result = component._calculate_buying_power_impact()

        assert result["notional"] == Decimal("5000")
        assert result["percentage"] is None
        assert result["warning"] is True


class TestOrderTicketIdempotency:
    """Tests for idempotent order submission."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component with mocked state manager."""
        from unittest.mock import AsyncMock

        client = MagicMock()
        state_manager = MagicMock()
        state_manager.restore_state = AsyncMock(return_value={})
        state_manager.save_pending_form = AsyncMock()
        connection_monitor = MagicMock()

        return OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

    @pytest.mark.asyncio()
    async def test_generates_new_intent_id_when_none_stored(
        self, component: OrderTicketComponent
    ) -> None:
        """New intent ID generated when no pending form exists."""
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 100
        component._state.order_type = "market"

        intent_id = await component._get_or_create_client_order_id()

        assert intent_id is not None
        assert len(intent_id) == 32  # UUID hex without dashes

    @pytest.mark.asyncio()
    async def test_reuses_stored_intent_when_form_matches(
        self, component: OrderTicketComponent
    ) -> None:
        """Stored intent ID reused when form state matches."""
        from unittest.mock import AsyncMock

        stored_intent = "abc123def456abc123def456abc12345"
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 100
        component._state.order_type = "market"
        component._state.limit_price = None
        component._state.stop_price = None

        # Mock restore_state to return a stored intent
        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "client_order_id": stored_intent,
                            "symbol": "AAPL",
                            "side": "buy",
                            "quantity": 100,
                            "order_type": "market",
                            "limit_price": "",
                            "stop_price": "",
                            "time_in_force": "day",
                        }
                    }
                }
            }
        )

        intent_id = await component._get_or_create_client_order_id()

        assert intent_id == stored_intent

    @pytest.mark.asyncio()
    async def test_generates_new_intent_when_form_changed(
        self, component: OrderTicketComponent
    ) -> None:
        """New intent ID generated when form state differs from stored."""
        from unittest.mock import AsyncMock

        stored_intent = "abc123def456abc123def456abc12345"
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 200  # Different quantity
        component._state.order_type = "market"
        component._state.limit_price = None
        component._state.stop_price = None

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "client_order_id": stored_intent,
                            "symbol": "AAPL",
                            "side": "buy",
                            "quantity": 100,  # Stored quantity different
                            "order_type": "market",
                            "limit_price": "",
                            "stop_price": "",
                            "time_in_force": "day",
                        }
                    }
                }
            }
        )

        intent_id = await component._get_or_create_client_order_id()

        # Should generate new intent, not reuse stored
        assert intent_id != stored_intent
        assert len(intent_id) == 32

    @pytest.mark.asyncio()
    async def test_generates_new_intent_when_tif_changed(
        self, component: OrderTicketComponent
    ) -> None:
        """New intent ID generated when time_in_force differs from stored."""
        from unittest.mock import AsyncMock

        stored_intent = "abc123def456abc123def456abc12345"
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 100
        component._state.order_type = "market"
        component._state.limit_price = None
        component._state.stop_price = None
        component._state.time_in_force = "gtc"  # Different TIF

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "client_order_id": stored_intent,
                            "symbol": "AAPL",
                            "side": "buy",
                            "quantity": 100,
                            "order_type": "market",
                            "limit_price": "",
                            "stop_price": "",
                            "time_in_force": "day",  # Stored TIF different
                        }
                    }
                }
            }
        )

        intent_id = await component._get_or_create_client_order_id()

        # Should generate new intent due to TIF difference
        assert intent_id != stored_intent
        assert len(intent_id) == 32

    def test_generate_intent_id_format(self, component: OrderTicketComponent) -> None:
        """Intent ID is a valid 32-char hex string."""
        intent_id = component._generate_intent_id()

        assert len(intent_id) == 32
        # Should be valid hex
        int(intent_id, 16)  # Will raise ValueError if invalid

    def test_tab_session_isolation(self) -> None:
        """Different component instances have different tab session IDs."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()

        comp1 = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )
        comp2 = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

        assert comp1._tab_session_id != comp2._tab_session_id


class TestOrderTicketFormRecovery:
    """Tests for form recovery after reconnection."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create test component with mocked state manager."""
        from unittest.mock import AsyncMock

        client = MagicMock()
        state_manager = MagicMock()
        state_manager.restore_state = AsyncMock(return_value={})
        state_manager.clear_pending_form = AsyncMock()
        connection_monitor = MagicMock()

        return OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=[],
        )

    @pytest.mark.asyncio()
    async def test_restores_valid_form_state(self, component: OrderTicketComponent) -> None:
        """Valid form state is restored correctly."""
        from unittest.mock import AsyncMock, patch

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "symbol": "AAPL",
                            "side": "sell",
                            "quantity": 50,
                            "order_type": "limit",
                            "limit_price": "150.00",
                            "stop_price": "",
                            "time_in_force": "gtc",
                            "client_order_id": "test123",
                        }
                    }
                }
            }
        )

        with patch("apps.web_console_ng.components.order_ticket.ui.notify"):
            await component._restore_pending_form()

        assert component._state.symbol == "AAPL"
        assert component._state.side == "sell"
        assert component._state.quantity == 50
        assert component._state.order_type == "limit"
        assert component._state.limit_price == Decimal("150.00")
        assert component._state.time_in_force == "gtc"
        assert component._pending_client_order_id == "test123"

    @pytest.mark.asyncio()
    async def test_handles_empty_pending_forms(self, component: OrderTicketComponent) -> None:
        """Empty pending forms don't crash recovery."""
        from unittest.mock import AsyncMock

        component._state_manager.restore_state = AsyncMock(return_value={})

        # Should not raise
        await component._restore_pending_form()

        # State should remain default
        assert component._state.symbol is None

    @pytest.mark.asyncio()
    async def test_handles_invalid_symbol(self, component: OrderTicketComponent) -> None:
        """Invalid symbol in stored form is handled gracefully."""
        from unittest.mock import AsyncMock, patch

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "symbol": "INVALID***SYMBOL",
                            "side": "buy",
                            "quantity": 100,
                            "order_type": "market",
                        }
                    }
                }
            }
        )

        with patch("apps.web_console_ng.components.order_ticket.ui.notify"):
            await component._restore_pending_form()

        # Invalid symbol should be normalized to None
        assert component._state.symbol is None

    @pytest.mark.asyncio()
    async def test_handles_invalid_quantity(self, component: OrderTicketComponent) -> None:
        """Invalid quantity in stored form is handled gracefully."""
        from unittest.mock import AsyncMock, patch

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "symbol": "AAPL",
                            "side": "buy",
                            "quantity": -50,  # Negative = invalid
                            "order_type": "market",
                        }
                    }
                }
            }
        )

        with patch("apps.web_console_ng.components.order_ticket.ui.notify"):
            await component._restore_pending_form()

        # Invalid quantity should be None
        assert component._state.quantity is None

    @pytest.mark.asyncio()
    async def test_handles_invalid_decimal(self, component: OrderTicketComponent) -> None:
        """Invalid decimal prices are handled gracefully."""
        from unittest.mock import AsyncMock, patch

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "symbol": "AAPL",
                            "side": "buy",
                            "quantity": 100,
                            "order_type": "limit",
                            "limit_price": "not-a-number",
                        }
                    }
                }
            }
        )

        with patch("apps.web_console_ng.components.order_ticket.ui.notify"):
            await component._restore_pending_form()

        # Invalid price should be None
        assert component._state.limit_price is None

    @pytest.mark.asyncio()
    async def test_defaults_unknown_enum_values(self, component: OrderTicketComponent) -> None:
        """Unknown enum values default to safe values."""
        from unittest.mock import AsyncMock, patch

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "symbol": "AAPL",
                            "side": "unknown_side",  # Invalid
                            "quantity": 100,
                            "order_type": "exotic_order",  # Invalid
                            "time_in_force": "forever",  # Invalid
                        }
                    }
                }
            }
        )

        with patch("apps.web_console_ng.components.order_ticket.ui.notify"):
            await component._restore_pending_form()

        # Should default to safe values
        assert component._state.side == "buy"
        assert component._state.order_type == "market"
        assert component._state.time_in_force == "day"

    @pytest.mark.asyncio()
    async def test_restore_syncs_input_controls(self, component: OrderTicketComponent) -> None:
        """Restore syncs all input controls from state, not just labels."""
        from unittest.mock import AsyncMock, patch

        # Mock UI inputs
        component._symbol_input = MagicMock()
        component._side_toggle = MagicMock()
        component._quantity_input = MagicMock()
        component._order_type_select = MagicMock()
        component._limit_price_input = MagicMock()
        component._stop_price_input = MagicMock()
        component._time_in_force_select = MagicMock()

        form_key = f"order_entry:{component._tab_session_id}"
        component._state_manager.restore_state = AsyncMock(
            return_value={
                "pending_forms": {
                    form_key: {
                        "data": {
                            "client_order_id": "test123",
                            "symbol": "AAPL",
                            "side": "sell",
                            "quantity": 50,
                            "order_type": "limit",
                            "limit_price": "150.00",
                            "stop_price": "",
                            "time_in_force": "gtc",
                        }
                    }
                }
            }
        )

        with patch("apps.web_console_ng.components.order_ticket.ui.notify"):
            await component._restore_pending_form()

        # Verify all input controls were synced
        component._symbol_input.set_value.assert_called_with("AAPL")
        component._side_toggle.set_value.assert_called_with("sell")
        component._quantity_input.set_value.assert_called_with(50)
        component._order_type_select.set_value.assert_called_with("limit")
        component._time_in_force_select.set_value.assert_called_with("gtc")
        # Limit price should be visible and set
        component._limit_price_input.classes.assert_any_call(remove="hidden")


class TestOrderTicketClearForm:
    """Tests for _clear_form() behavior."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create component with mocked UI for testing."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=["alpha"],
        )

        # Mock UI inputs
        comp._symbol_input = MagicMock()
        comp._side_toggle = MagicMock()
        comp._quantity_input = MagicMock()
        comp._order_type_select = MagicMock()
        comp._limit_price_input = MagicMock()
        comp._stop_price_input = MagicMock()
        comp._time_in_force_select = MagicMock()

        return comp

    @pytest.mark.asyncio()
    async def test_clear_resets_tif_select(self, component: OrderTicketComponent) -> None:
        """_clear_form() resets TIF select to default value."""
        # Set non-default TIF
        component._state.time_in_force = "gtc"

        await component._clear_form()

        # TIF select should be reset to "day"
        component._time_in_force_select.set_value.assert_called_with("day")
        # State should also be reset
        assert component._state.time_in_force == "day"

    @pytest.mark.asyncio()
    async def test_clear_resets_all_inputs(self, component: OrderTicketComponent) -> None:
        """_clear_form() resets all input controls to defaults."""
        # Set some non-default values
        component._state.symbol = "AAPL"
        component._state.side = "sell"
        component._state.quantity = 100
        component._state.order_type = "limit"

        await component._clear_form()

        component._symbol_input.set_value.assert_called_with("")
        component._side_toggle.set_value.assert_called_with("buy")
        component._quantity_input.set_value.assert_called_with(None)
        component._order_type_select.set_value.assert_called_with("market")
        component._time_in_force_select.set_value.assert_called_with("day")


class TestOrderTicketPreviewSnapshot:
    """Tests for preview snapshot validation (idempotency guard)."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create component for testing."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=["alpha"],
        )
        return comp

    def test_no_snapshot_returns_false(self, component: OrderTicketComponent) -> None:
        """Validation fails if no snapshot exists."""
        component._preview_snapshot = None

        assert component._validate_preview_snapshot() is False

    def test_matching_snapshot_returns_true(self, component: OrderTicketComponent) -> None:
        """Validation passes when current state matches snapshot."""
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 100
        component._state.order_type = "limit"
        component._state.limit_price = Decimal("150.00")
        component._state.stop_price = None
        component._state.time_in_force = "day"

        component._preview_snapshot = {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 100,
            "order_type": "limit",
            "limit_price": "150.00",
            "stop_price": "",
            "time_in_force": "day",
        }

        assert component._validate_preview_snapshot() is True

    def test_symbol_changed_returns_false(self, component: OrderTicketComponent) -> None:
        """Validation fails if symbol changed since preview."""
        component._state.symbol = "GOOGL"  # Changed
        component._state.side = "buy"
        component._state.quantity = 100
        component._state.order_type = "market"
        component._state.limit_price = None
        component._state.stop_price = None
        component._state.time_in_force = "day"

        component._preview_snapshot = {
            "symbol": "AAPL",  # Original
            "side": "buy",
            "quantity": 100,
            "order_type": "market",
            "limit_price": "",
            "stop_price": "",
            "time_in_force": "day",
        }

        assert component._validate_preview_snapshot() is False

    def test_quantity_changed_returns_false(self, component: OrderTicketComponent) -> None:
        """Validation fails if quantity changed since preview."""
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 200  # Changed
        component._state.order_type = "market"
        component._state.limit_price = None
        component._state.stop_price = None
        component._state.time_in_force = "day"

        component._preview_snapshot = {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 100,  # Original
            "order_type": "market",
            "limit_price": "",
            "stop_price": "",
            "time_in_force": "day",
        }

        assert component._validate_preview_snapshot() is False

    def test_side_changed_returns_false(self, component: OrderTicketComponent) -> None:
        """Validation fails if side changed since preview."""
        component._state.symbol = "AAPL"
        component._state.side = "sell"  # Changed
        component._state.quantity = 100
        component._state.order_type = "market"
        component._state.limit_price = None
        component._state.stop_price = None
        component._state.time_in_force = "day"

        component._preview_snapshot = {
            "symbol": "AAPL",
            "side": "buy",  # Original
            "quantity": 100,
            "order_type": "market",
            "limit_price": "",
            "stop_price": "",
            "time_in_force": "day",
        }

        assert component._validate_preview_snapshot() is False

    def test_price_changed_returns_false(self, component: OrderTicketComponent) -> None:
        """Validation fails if price changed since preview."""
        component._state.symbol = "AAPL"
        component._state.side = "buy"
        component._state.quantity = 100
        component._state.order_type = "limit"
        component._state.limit_price = Decimal("160.00")  # Changed
        component._state.stop_price = None
        component._state.time_in_force = "day"

        component._preview_snapshot = {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 100,
            "order_type": "limit",
            "limit_price": "150.00",  # Original
            "stop_price": "",
            "time_in_force": "day",
        }

        assert component._validate_preview_snapshot() is False


class TestOrderTicketUIDisable:
    """Tests for UI disable behavior."""

    @pytest.fixture()
    def component(self) -> OrderTicketComponent:
        """Create component with mock UI elements for testing."""
        client = MagicMock()
        state_manager = MagicMock()
        connection_monitor = MagicMock()
        connection_monitor.is_read_only.return_value = False

        comp = OrderTicketComponent(
            trading_client=client,
            state_manager=state_manager,
            connection_monitor=connection_monitor,
            user_id="user1",
            role="trader",
            strategies=["alpha"],
        )

        # Mock UI elements
        comp._symbol_input = MagicMock()
        comp._side_toggle = MagicMock()
        comp._quantity_input = MagicMock()
        comp._order_type_select = MagicMock()
        comp._limit_price_input = MagicMock()
        comp._stop_price_input = MagicMock()
        comp._time_in_force_select = MagicMock()
        comp._quantity_presets = MagicMock()
        comp._submit_button = MagicMock()
        comp._submit_button._button = MagicMock()
        comp._clear_button = MagicMock()
        comp._disabled_banner = MagicMock()

        return comp

    def test_disable_disables_all_inputs(self, component: OrderTicketComponent) -> None:
        """All form inputs are disabled when trading is disabled."""
        component._set_ui_disabled(True, "Kill switch engaged")

        component._symbol_input.set_enabled.assert_called_with(False)
        component._side_toggle.set_enabled.assert_called_with(False)
        component._quantity_input.set_enabled.assert_called_with(False)
        component._order_type_select.set_enabled.assert_called_with(False)
        component._limit_price_input.set_enabled.assert_called_with(False)
        component._stop_price_input.set_enabled.assert_called_with(False)
        component._time_in_force_select.set_enabled.assert_called_with(False)
        component._quantity_presets.set_enabled.assert_called_with(False)
        component._submit_button._button.set_enabled.assert_called_with(False)
        component._clear_button.set_enabled.assert_called_with(False)

    def test_enable_enables_all_inputs(self, component: OrderTicketComponent) -> None:
        """All form inputs are enabled when trading is enabled."""
        component._set_ui_disabled(False, "")

        component._symbol_input.set_enabled.assert_called_with(True)
        component._side_toggle.set_enabled.assert_called_with(True)
        component._quantity_input.set_enabled.assert_called_with(True)
        component._order_type_select.set_enabled.assert_called_with(True)
        component._limit_price_input.set_enabled.assert_called_with(True)
        component._stop_price_input.set_enabled.assert_called_with(True)
        component._time_in_force_select.set_enabled.assert_called_with(True)
        component._quantity_presets.set_enabled.assert_called_with(True)
        component._submit_button._button.set_enabled.assert_called_with(True)
        component._clear_button.set_enabled.assert_called_with(True)

    def test_disable_shows_banner(self, component: OrderTicketComponent) -> None:
        """Disabled banner is shown with reason when disabled."""
        component._set_ui_disabled(True, "Circuit breaker tripped")

        component._disabled_banner.set_text.assert_called_with("Circuit breaker tripped")
        component._disabled_banner.classes.assert_called_with(remove="hidden")

    def test_enable_hides_banner(self, component: OrderTicketComponent) -> None:
        """Disabled banner is hidden when enabled."""
        component._set_ui_disabled(False, "")

        component._disabled_banner.classes.assert_called_with(add="hidden")
