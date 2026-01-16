"""
Unit tests for RiskChecker pre-trade validation.

Tests cover:
- Circuit breaker integration (blocking when TRIPPED)
- Blacklist enforcement
- Position size limits (absolute shares and % of portfolio)
- Portfolio exposure limits (total, long, short)
- Position calculation logic
- Edge cases and boundary conditions
"""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from libs.trading.risk_management.breaker import CircuitBreaker
from libs.trading.risk_management.checker import RiskChecker
from libs.trading.risk_management.config import (
    PortfolioLimits,
    PositionLimits,
    RiskConfig,
)
from libs.trading.risk_management.kill_switch import KillSwitch


class TestRiskCheckerInitialization:
    """Tests for RiskChecker initialization."""

    def test_initialization(self) -> None:
        """Test RiskChecker initializes with config and breaker."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)

        checker = RiskChecker(config=config, breaker=breaker)

        assert checker.config == config
        assert checker.breaker == breaker
        assert checker.kill_switch is None  # Optional, defaults to None

    def test_initialization_with_kill_switch(self) -> None:
        """Test RiskChecker initializes with optional kill switch."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)
        kill_switch = Mock(spec=KillSwitch)

        checker = RiskChecker(config=config, breaker=breaker, kill_switch=kill_switch)

        assert checker.config == config
        assert checker.breaker == breaker
        assert checker.kill_switch == kill_switch


class TestValidateOrderKillSwitch:
    """Tests for kill switch integration (T5.1).

    Kill switch is the HIGHEST priority check (step 0).
    When engaged, ALL trading is blocked regardless of other checks.
    """

    @pytest.fixture()
    def checker_with_kill_switch(self) -> tuple[RiskChecker, Mock, Mock]:
        """Create RiskChecker with mock kill switch and circuit breaker."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        kill_switch = Mock(spec=KillSwitch)
        checker = RiskChecker(config=config, breaker=breaker, kill_switch=kill_switch)
        return checker, breaker, kill_switch

    def test_order_blocked_when_kill_switch_engaged(
        self, checker_with_kill_switch: tuple[RiskChecker, Mock, Mock]
    ) -> None:
        """Test order blocked when kill switch is ENGAGED."""
        risk_checker, _breaker, kill_switch = checker_with_kill_switch
        kill_switch.is_engaged.return_value = True

        is_valid, reason = risk_checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        assert is_valid is False
        assert "Kill switch ENGAGED" in reason
        assert "All trading halted" in reason
        kill_switch.is_engaged.assert_called_once()

    def test_order_allowed_when_kill_switch_active(
        self, checker_with_kill_switch: tuple[RiskChecker, Mock, Mock]
    ) -> None:
        """Test order allowed when kill switch is ACTIVE (not engaged)."""
        risk_checker, _breaker, kill_switch = checker_with_kill_switch
        kill_switch.is_engaged.return_value = False

        is_valid, reason = risk_checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        assert is_valid is True
        assert reason == ""
        kill_switch.is_engaged.assert_called_once()

    def test_kill_switch_checked_before_circuit_breaker(
        self, checker_with_kill_switch: tuple[RiskChecker, Mock, Mock]
    ) -> None:
        """Test kill switch is checked BEFORE circuit breaker (step 0 vs step 1)."""
        risk_checker, breaker, kill_switch = checker_with_kill_switch
        # Both are in "bad" state
        kill_switch.is_engaged.return_value = True
        breaker.is_tripped.return_value = True
        breaker.get_trip_reason.return_value = "DAILY_LOSS_EXCEEDED"

        is_valid, reason = risk_checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        # Kill switch should be checked first, so its message should appear
        assert is_valid is False
        assert "Kill switch ENGAGED" in reason
        # Circuit breaker message should NOT appear (kill switch blocks first)
        assert "Circuit breaker" not in reason

    def test_order_allowed_when_kill_switch_is_none(self) -> None:
        """Test backwards compatibility: order allowed when kill_switch is None."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        # No kill switch provided (backwards compatibility)
        checker = RiskChecker(config=config, breaker=breaker)

        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        assert is_valid is True
        assert reason == ""


class TestValidateOrderCircuitBreaker:
    """Tests for circuit breaker integration."""

    @pytest.fixture()
    def checker(self):
        """Create RiskChecker with mock circuit breaker."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)
        return RiskChecker(config=config, breaker=breaker), breaker

    def test_order_blocked_when_circuit_breaker_tripped(self, checker):
        """Test order blocked when circuit breaker is TRIPPED."""
        risk_checker, breaker = checker
        breaker.is_tripped.return_value = True
        breaker.get_trip_reason.return_value = "DAILY_LOSS_EXCEEDED"

        is_valid, reason = risk_checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        assert is_valid is False
        assert "Circuit breaker TRIPPED" in reason
        assert "DAILY_LOSS_EXCEEDED" in reason

    def test_order_allowed_when_circuit_breaker_open(self, checker):
        """Test order allowed when circuit breaker is OPEN."""
        risk_checker, breaker = checker
        breaker.is_tripped.return_value = False

        is_valid, reason = risk_checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        assert is_valid is True
        assert reason == ""


class TestValidateOrderBlacklist:
    """Tests for blacklist enforcement."""

    def test_order_blocked_for_blacklisted_symbol(self):
        """Test order blocked for blacklisted symbol."""
        config = RiskConfig(blacklist=["GME", "AMC"])
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        checker = RiskChecker(config=config, breaker=breaker)

        is_valid, reason = checker.validate_order(symbol="GME", side="buy", qty=10)

        assert is_valid is False
        assert "blacklisted" in reason
        assert "GME" in reason

    def test_order_allowed_for_non_blacklisted_symbol(self):
        """Test order allowed for non-blacklisted symbol."""
        config = RiskConfig(blacklist=["GME", "AMC"])
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        checker = RiskChecker(config=config, breaker=breaker)

        is_valid, reason = checker.validate_order(symbol="AAPL", side="buy", qty=100)

        assert is_valid is True
        assert reason == ""


class TestValidateOrderPositionSizeLimits:
    """Tests for position size limits (absolute shares)."""

    @pytest.fixture()
    def checker(self):
        """Create RiskChecker with custom position limits."""
        config = RiskConfig(
            position_limits=PositionLimits(
                max_position_size=500,  # Max 500 shares
                max_position_pct=Decimal("0.20"),  # Max 20% of portfolio
            )
        )
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        return RiskChecker(config=config, breaker=breaker)

    def test_buy_order_exceeds_position_limit(self, checker):
        """Test buy order that would exceed max position size."""
        # Current: 400 shares long, Order: buy 200 → New: 600 (exceeds 500 limit)
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="buy", qty=200, current_position=400
        )

        assert is_valid is False
        assert "Position limit exceeded" in reason
        assert "600" in reason
        assert "500" in reason

    def test_sell_order_exceeds_position_limit(self, checker):
        """Test sell order that would exceed max position size (short)."""
        # Current: -400 shares short, Order: sell 200 → New: -600 (exceeds 500 limit)
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="sell", qty=200, current_position=-400
        )

        assert is_valid is False
        assert "Position limit exceeded" in reason
        assert "600" in reason

    def test_order_within_position_limit(self, checker):
        """Test order within position size limit."""
        # Current: 300 shares, Order: buy 100 → New: 400 (within 500 limit)
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=300
        )

        assert is_valid is True
        assert reason == ""

    def test_order_exactly_at_position_limit(self, checker):
        """Test order that reaches exactly the position limit."""
        # Current: 400 shares, Order: buy 100 → New: 500 (exactly at limit)
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=400
        )

        assert is_valid is True
        assert reason == ""


class TestValidateOrderPositionPercentLimits:
    """Tests for position size limits (% of portfolio)."""

    @pytest.fixture()
    def checker(self):
        """Create RiskChecker with position % limits."""
        config = RiskConfig(
            position_limits=PositionLimits(
                max_position_size=10000,  # High enough to not trigger
                max_position_pct=Decimal("0.20"),  # Max 20% of portfolio
            )
        )
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        return RiskChecker(config=config, breaker=breaker)

    def test_order_exceeds_portfolio_percentage_limit(self, checker):
        """Test order that would exceed max portfolio % limit."""
        # Portfolio: $50k, Max 20% = $10k
        # Current: 0, Order: buy 100 @ $200 = $20k notional (40% of portfolio)
        is_valid, reason = checker.validate_order(
            symbol="AAPL",
            side="buy",
            qty=100,
            current_position=0,
            current_price=Decimal("200.00"),
            portfolio_value=Decimal("50000.00"),
        )

        assert is_valid is False
        assert "exceed" in reason
        assert "20.0%" in reason
        assert "$20000.00" in reason
        assert "$10000.00" in reason

    def test_order_within_portfolio_percentage_limit(self, checker):
        """Test order within portfolio % limit."""
        # Portfolio: $50k, Max 20% = $10k
        # Current: 0, Order: buy 40 @ $200 = $8k notional (16% of portfolio)
        is_valid, reason = checker.validate_order(
            symbol="AAPL",
            side="buy",
            qty=40,
            current_position=0,
            current_price=Decimal("200.00"),
            portfolio_value=Decimal("50000.00"),
        )

        assert is_valid is True
        assert reason == ""

    def test_percentage_check_skipped_when_price_not_provided(self, checker):
        """Test % check skipped when price not provided."""
        # Large order that would fail % check, but no price provided
        is_valid, reason = checker.validate_order(
            symbol="AAPL",
            side="buy",
            qty=1000,
            current_position=0,
            current_price=None,  # No price
            portfolio_value=Decimal("50000.00"),
        )

        # Should pass (only absolute share limit applies)
        assert is_valid is True

    def test_percentage_check_skipped_when_portfolio_value_not_provided(self, checker):
        """Test % check skipped when portfolio_value not provided."""
        is_valid, reason = checker.validate_order(
            symbol="AAPL",
            side="buy",
            qty=1000,
            current_position=0,
            current_price=Decimal("200.00"),
            portfolio_value=None,  # No portfolio value
        )

        # Should pass (only absolute share limit applies)
        assert is_valid is True


class TestCalculateNewPosition:
    """Tests for position calculation logic."""

    @pytest.fixture()
    def checker(self):
        """Create basic RiskChecker."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)
        return RiskChecker(config=config, breaker=breaker)

    def test_buy_increases_long_position(self, checker):
        """Test buy order increases long position."""
        new_position = checker._calculate_new_position(current_position=100, side="buy", qty=50)
        assert new_position == 150

    def test_sell_decreases_long_position(self, checker):
        """Test sell order decreases long position."""
        new_position = checker._calculate_new_position(current_position=100, side="sell", qty=50)
        assert new_position == 50

    def test_sell_creates_short_position(self, checker):
        """Test sell order creates short position."""
        new_position = checker._calculate_new_position(current_position=100, side="sell", qty=150)
        assert new_position == -50

    def test_buy_reduces_short_position(self, checker):
        """Test buy order reduces short position."""
        new_position = checker._calculate_new_position(current_position=-100, side="buy", qty=50)
        assert new_position == -50

    def test_buy_flips_to_long_position(self, checker):
        """Test buy order flips short to long."""
        new_position = checker._calculate_new_position(current_position=-50, side="buy", qty=100)
        assert new_position == 50

    def test_invalid_side_raises_error(self, checker):
        """Test invalid side raises ValueError."""
        with pytest.raises(ValueError, match="Invalid side"):
            checker._calculate_new_position(current_position=0, side="invalid", qty=100)


class TestCheckPortfolioExposure:
    """Tests for portfolio-level exposure limits."""

    @pytest.fixture()
    def checker(self):
        """Create RiskChecker with portfolio limits."""
        config = RiskConfig(
            portfolio_limits=PortfolioLimits(
                max_total_notional=Decimal("100000.00"),
                max_long_exposure=Decimal("80000.00"),
                max_short_exposure=Decimal("20000.00"),
            )
        )
        breaker = Mock(spec=CircuitBreaker)
        return RiskChecker(config=config, breaker=breaker)

    def test_portfolio_within_all_limits(self, checker):
        """Test portfolio within all exposure limits."""
        positions = [
            ("AAPL", 100, Decimal("200.00")),  # $20k long
            ("MSFT", 150, Decimal("300.00")),  # $45k long
            ("GOOGL", -20, Decimal("500.00")),  # $10k short
        ]
        # Total: $75k, Long: $65k, Short: $10k (all within limits)

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is True
        assert reason == ""

    def test_total_exposure_exceeds_limit(self, checker):
        """Test total exposure exceeds limit."""
        positions = [
            ("AAPL", 200, Decimal("200.00")),  # $40k long
            ("MSFT", 200, Decimal("300.00")),  # $60k long
            ("GOOGL", -50, Decimal("500.00")),  # $25k short
        ]
        # Total: $125k (exceeds $100k limit), Long: $100k, Short: $25k

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is False
        assert "Total exposure exceeds limit" in reason
        assert "$125000.00" in reason
        assert "$100000.00" in reason

    def test_long_exposure_exceeds_limit(self, checker):
        """Test long exposure exceeds limit."""
        positions = [
            ("AAPL", 300, Decimal("200.00")),  # $60k long
            ("MSFT", 100, Decimal("300.00")),  # $30k long
            ("GOOGL", -20, Decimal("500.00")),  # $10k short
        ]
        # Total: $100k, Long: $90k (exceeds $80k limit), Short: $10k

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is False
        assert "Long exposure exceeds limit" in reason
        assert "$90000.00" in reason
        assert "$80000.00" in reason

    def test_short_exposure_exceeds_limit(self, checker):
        """Test short exposure exceeds limit."""
        positions = [
            ("AAPL", 100, Decimal("200.00")),  # $20k long
            ("MSFT", 100, Decimal("300.00")),  # $30k long
            ("GOOGL", -100, Decimal("250.00")),  # $25k short (exceeds $20k limit)
        ]
        # Total: $75k, Long: $50k, Short: $25k (exceeds $20k limit)

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is False
        assert "Short exposure exceeds limit" in reason
        assert "$25000.00" in reason
        assert "$20000.00" in reason

    def test_empty_portfolio(self, checker):
        """Test empty portfolio passes all checks."""
        positions = []

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is True
        assert reason == ""

    def test_flat_positions_not_counted(self, checker):
        """Test positions with qty=0 are not counted."""
        positions = [
            ("AAPL", 100, Decimal("200.00")),  # $20k long
            ("MSFT", 0, Decimal("300.00")),  # $0 (flat)
            ("GOOGL", -20, Decimal("500.00")),  # $10k short
        ]
        # Total: $30k, Long: $20k, Short: $10k

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is True


class TestValidateOrderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture()
    def checker(self):
        """Create basic RiskChecker."""
        config = RiskConfig()
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        return RiskChecker(config=config, breaker=breaker)

    def test_flat_position_buy_order(self, checker):
        """Test buy order from flat position."""
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )
        assert is_valid is True

    def test_flat_position_sell_order(self, checker):
        """Test sell order from flat position (creates short)."""
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="sell", qty=100, current_position=0
        )
        assert is_valid is True

    def test_reducing_order_allowed(self, checker):
        """Test reducing order always allowed (risk-reducing)."""
        # Current: 800 shares long (within 1000 limit), Sell 200 → 600 (reduces risk)
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="sell", qty=200, current_position=800
        )
        assert is_valid is True

    def test_circuit_breaker_check_happens_first(self):
        """Test circuit breaker check happens before other checks."""
        # Even with blacklisted symbol, circuit breaker checked first
        config = RiskConfig(blacklist=["AAPL"])
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = True
        breaker.get_trip_reason.return_value = "MANUAL"
        checker = RiskChecker(config=config, breaker=breaker)

        is_valid, reason = checker.validate_order(symbol="AAPL", side="buy", qty=100)

        # Should fail on circuit breaker, not blacklist
        assert is_valid is False
        assert "Circuit breaker" in reason
        assert "blacklist" not in reason.lower()
"""
P0 Coverage Tests for RiskChecker - Additional branch coverage to reach 95%+ target.

Missing branches from coverage report (75% → 95%):
- Line 215->229: Position size limit check (when _skip_position_limit=False)
- Lines 408-449: validate_order_with_reservation method
- Lines 468-473: confirm_reservation method
- Lines 493-498: release_reservation method
"""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from libs.trading.risk_management.breaker import CircuitBreaker, CircuitBreakerState
from libs.trading.risk_management.checker import RiskChecker
from libs.trading.risk_management.config import PositionLimits, PortfolioLimits, RiskConfig
from libs.trading.risk_management.kill_switch import KillSwitch
from libs.trading.risk_management.position_reservation import (
    PositionReservation,
    ReservationResult,
)


class TestRiskCheckerPositionReservation:
    """Tests for atomic position reservation integration."""

    @pytest.fixture()
    def mock_breaker(self):
        """Create mock circuit breaker."""
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        breaker.get_trip_reason.return_value = None
        return breaker

    @pytest.fixture()
    def mock_position_reservation(self):
        """Create mock position reservation."""
        return Mock(spec=PositionReservation)

    @pytest.fixture()
    def config(self):
        """Create risk config."""
        return RiskConfig(
            position_limits=PositionLimits(
                max_position_size=1000, max_position_pct=Decimal("0.2")
            ),
            portfolio_limits=PortfolioLimits(
                max_total_notional=Decimal("100000.00"),
                max_long_exposure=Decimal("80000.00"),
                max_short_exposure=Decimal("20000.00"),
            ),
            blacklist=[],
        )

    def test_validate_order_with_reservation_success(
        self, config, mock_breaker, mock_position_reservation
    ):
        """Test validate_order_with_reservation when all checks pass."""
        # Setup successful reservation
        reservation_result = ReservationResult(
            success=True,
            token="test-token-123",
            reason="",
            previous_position=0,
            new_position=100,
        )
        mock_position_reservation.reserve.return_value = reservation_result

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Validate order with reservation
        is_valid, reason, result = checker.validate_order_with_reservation(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        # Verify success
        assert is_valid is True
        assert reason == ""
        assert result is not None
        assert result.success is True
        assert result.token == "test-token-123"

        # Verify reservation was attempted with correct parameters
        mock_position_reservation.reserve.assert_called_once_with(
            symbol="AAPL",
            side="buy",
            qty=100,
            max_limit=1000,  # config.position_limits.max_position_size
            current_position=0,
        )

    def test_validate_order_with_reservation_breaker_tripped(
        self, config, mock_breaker, mock_position_reservation
    ):
        """Test validate_order_with_reservation when circuit breaker tripped."""
        # Circuit breaker tripped
        mock_breaker.is_tripped.return_value = True
        mock_breaker.get_trip_reason.return_value = "DAILY_LOSS_EXCEEDED"

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Validate order with reservation
        is_valid, reason, result = checker.validate_order_with_reservation(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        # Verify blocked by circuit breaker (no reservation attempted)
        assert is_valid is False
        assert "Circuit breaker TRIPPED" in reason
        assert result is None

        # Verify reservation was NOT attempted
        mock_position_reservation.reserve.assert_not_called()

    def test_validate_order_with_reservation_blacklist(
        self, config, mock_breaker, mock_position_reservation
    ):
        """Test validate_order_with_reservation when symbol blacklisted."""
        # Blacklist AAPL
        config.blacklist = ["AAPL"]

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Validate order with reservation
        is_valid, reason, result = checker.validate_order_with_reservation(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        # Verify blocked by blacklist (no reservation attempted)
        assert is_valid is False
        assert "blacklisted" in reason
        assert result is None

        # Verify reservation was NOT attempted
        mock_position_reservation.reserve.assert_not_called()

    def test_validate_order_with_reservation_reservation_failed(
        self, config, mock_breaker, mock_position_reservation
    ):
        """Test validate_order_with_reservation when reservation fails."""
        # Setup failed reservation
        reservation_result = ReservationResult(
            success=False,
            token=None,
            reason="Position limit exceeded: 1100 > 1000",
            previous_position=1000,
            new_position=1100,
        )
        mock_position_reservation.reserve.return_value = reservation_result

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Validate order with reservation
        is_valid, reason, result = checker.validate_order_with_reservation(
            symbol="AAPL", side="buy", qty=100, current_position=1000
        )

        # Verify blocked by reservation failure
        assert is_valid is False
        assert "Position limit exceeded" in reason
        assert result is not None
        assert result.success is False

        # Verify reservation was attempted
        mock_position_reservation.reserve.assert_called_once()

    def test_validate_order_with_reservation_no_reservation_configured(
        self, config, mock_breaker
    ):
        """Test validate_order_with_reservation when position_reservation is None."""
        # Checker without position reservation
        checker = RiskChecker(config=config, breaker=mock_breaker, position_reservation=None)

        # Validate order with reservation
        is_valid, reason, result = checker.validate_order_with_reservation(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        # Verify success (fallback to standard validation)
        assert is_valid is True
        assert reason == ""
        assert result is None  # No reservation result when not configured

    def test_validate_order_with_reservation_with_price_check(
        self, config, mock_breaker, mock_position_reservation
    ):
        """Test validate_order_with_reservation with price and portfolio checks."""
        # Setup successful reservation
        reservation_result = ReservationResult(
            success=True,
            token="test-token-456",
            reason="",
            previous_position=0,
            new_position=25,
        )
        mock_position_reservation.reserve.return_value = reservation_result

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Validate order with price and portfolio value
        # 25 shares * $200/share = $5,000 notional, 10% of $50,000 portfolio (within 20% limit)
        is_valid, reason, result = checker.validate_order_with_reservation(
            symbol="AAPL",
            side="buy",
            qty=25,
            current_position=0,
            current_price=Decimal("200.00"),
            portfolio_value=Decimal("50000.00"),
        )

        # Verify success (25 * $200 = $5k notional, 10% of $50k portfolio, within 20% limit)
        assert is_valid is True
        assert reason == ""
        assert result is not None
        assert result.success is True


class TestRiskCheckerConfirmRelease:
    """Tests for confirm and release reservation methods."""

    @pytest.fixture()
    def mock_breaker(self):
        """Create mock circuit breaker."""
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        return breaker

    @pytest.fixture()
    def mock_position_reservation(self):
        """Create mock position reservation."""
        return Mock(spec=PositionReservation)

    @pytest.fixture()
    def config(self):
        """Create risk config."""
        return RiskConfig()

    def test_confirm_reservation_success(self, config, mock_breaker, mock_position_reservation):
        """Test confirm_reservation when successful."""
        # Setup successful confirm
        confirm_result = ReservationResult(
            success=True,
            token="test-token",
            reason="",
            previous_position=0,
            new_position=100,
        )
        mock_position_reservation.confirm.return_value = confirm_result

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Confirm reservation
        result = checker.confirm_reservation("AAPL", "test-token")

        # Verify success
        assert result is True
        mock_position_reservation.confirm.assert_called_once_with("AAPL", "test-token")

    def test_confirm_reservation_no_position_reservation(self, config, mock_breaker):
        """Test confirm_reservation when position_reservation is None."""
        checker = RiskChecker(config=config, breaker=mock_breaker, position_reservation=None)

        # Confirm reservation (should return False)
        result = checker.confirm_reservation("AAPL", "test-token")

        assert result is False

    def test_release_reservation_success(self, config, mock_breaker, mock_position_reservation):
        """Test release_reservation when successful."""
        # Setup successful release
        release_result = ReservationResult(
            success=True,
            token="test-token",
            reason="",
            previous_position=100,
            new_position=0,
        )
        mock_position_reservation.release.return_value = release_result

        checker = RiskChecker(
            config=config,
            breaker=mock_breaker,
            position_reservation=mock_position_reservation,
        )

        # Release reservation
        result = checker.release_reservation("AAPL", "test-token")

        # Verify success
        assert result is True
        mock_position_reservation.release.assert_called_once_with("AAPL", "test-token")

    def test_release_reservation_no_position_reservation(self, config, mock_breaker):
        """Test release_reservation when position_reservation is None."""
        checker = RiskChecker(config=config, breaker=mock_breaker, position_reservation=None)

        # Release reservation (should return False)
        result = checker.release_reservation("AAPL", "test-token")

        assert result is False


class TestRiskCheckerPositionLimitSkip:
    """Tests for position limit skip behavior when using atomic reservation."""

    @pytest.fixture()
    def mock_breaker(self):
        """Create mock circuit breaker."""
        breaker = Mock(spec=CircuitBreaker)
        breaker.is_tripped.return_value = False
        return breaker

    @pytest.fixture()
    def config(self):
        """Create risk config with low position limit."""
        return RiskConfig(
            position_limits=PositionLimits(max_position_size=50, max_position_pct=Decimal("0.2"))
        )

    def test_position_limit_not_skipped_without_reservation(self, config, mock_breaker):
        """Test position limit check enforced when no reservation configured."""
        checker = RiskChecker(config=config, breaker=mock_breaker, position_reservation=None)

        # Order would exceed position limit (100 > 50)
        is_valid, reason = checker.validate_order(
            symbol="AAPL", side="buy", qty=100, current_position=0
        )

        # Verify blocked by position limit
        assert is_valid is False
        assert "Position limit exceeded" in reason
        assert "100 shares > 50 max" in reason

    def test_position_limit_skipped_in_validate_order_with_skip_flag(self, config, mock_breaker):
        """Test position limit check skipped when _skip_position_limit=True."""
        checker = RiskChecker(config=config, breaker=mock_breaker, position_reservation=None)

        # Order would exceed position limit (100 > 50), but skip flag set
        is_valid, reason = checker.validate_order(
            symbol="AAPL",
            side="buy",
            qty=100,
            current_position=0,
            _skip_position_limit=True,
        )

        # Verify NOT blocked by position limit (check was skipped)
        assert is_valid is True
        assert reason == ""
