"""Tests for pre-trade risk checker."""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from libs.risk_management.checker import RiskChecker
from libs.risk_management.config import (
    RiskConfig,
    PositionLimits,
    PortfolioLimits,
)
from libs.risk_management.breaker import CircuitBreaker, CircuitBreakerState


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = MagicMock()
    redis._state = {}

    def mock_get(key):
        return redis._state.get(key)

    def mock_set(key, value, ttl=None):
        redis._state[key] = value

    redis.get = MagicMock(side_effect=mock_get)
    redis.set = MagicMock(side_effect=mock_set)
    return redis


@pytest.fixture
def breaker(mock_redis):
    """Circuit breaker instance."""
    return CircuitBreaker(redis_client=mock_redis)


@pytest.fixture
def config():
    """Default risk config."""
    return RiskConfig()


@pytest.fixture
def checker(config, breaker):
    """Risk checker instance."""
    return RiskChecker(config=config, breaker=breaker)


class TestRiskCheckerCircuitBreaker:
    """Test circuit breaker checks."""

    def test_order_allowed_when_breaker_open(self, checker):
        """Test order allowed when circuit breaker OPEN."""
        is_valid, reason = checker.validate_order("AAPL", "buy", 100)

        assert is_valid is True
        assert reason == ""

    def test_order_blocked_when_breaker_tripped(self, checker, breaker):
        """Test order blocked when circuit breaker TRIPPED."""
        breaker.trip("DAILY_LOSS_EXCEEDED")

        is_valid, reason = checker.validate_order("AAPL", "buy", 100)

        assert is_valid is False
        assert "Circuit breaker TRIPPED" in reason
        assert "DAILY_LOSS_EXCEEDED" in reason


class TestRiskCheckerBlacklist:
    """Test blacklist checks."""

    def test_order_allowed_for_non_blacklisted_symbol(self, breaker):
        """Test order allowed for non-blacklisted symbol."""
        config = RiskConfig(blacklist=["GME", "AMC"])
        checker = RiskChecker(config=config, breaker=breaker)

        is_valid, reason = checker.validate_order("AAPL", "buy", 100)

        assert is_valid is True

    def test_order_blocked_for_blacklisted_symbol(self, breaker):
        """Test order blocked for blacklisted symbol."""
        config = RiskConfig(blacklist=["GME", "AMC"])
        checker = RiskChecker(config=config, breaker=breaker)

        is_valid, reason = checker.validate_order("GME", "buy", 100)

        assert is_valid is False
        assert "GME is blacklisted" in reason


class TestRiskCheckerPositionLimits:
    """Test position size limit checks."""

    def test_order_allowed_within_position_limit(self, checker):
        """Test order allowed when within position limit (1000 shares default)."""
        is_valid, reason = checker.validate_order(
            "AAPL", "buy", 500, current_position=0
        )

        assert is_valid is True

    def test_order_blocked_exceeding_position_limit(self, checker):
        """Test order blocked when exceeding position limit."""
        # Default limit = 1000, order would create position of 1100
        is_valid, reason = checker.validate_order(
            "AAPL", "buy", 100, current_position=1000
        )

        assert is_valid is False
        assert "Position limit exceeded" in reason
        assert "1100 shares > 1000 max" in reason

    def test_order_allowed_at_exact_limit(self, checker):
        """Test order allowed when exactly at limit."""
        is_valid, reason = checker.validate_order(
            "AAPL", "buy", 1000, current_position=0
        )

        assert is_valid is True

    def test_position_limit_applies_to_short_positions(self, checker):
        """Test position limit applies to short positions (absolute value)."""
        # Short position of -1100 exceeds limit of 1000
        is_valid, reason = checker.validate_order(
            "AAPL", "sell", 100, current_position=-1000
        )

        assert is_valid is False
        assert "Position limit exceeded" in reason
        assert "1100 shares > 1000 max" in reason

    def test_sell_order_reducing_position_allowed(self, checker):
        """Test sell order reducing position is allowed."""
        # Sell reduces position from 900 to 400 (within limit)
        is_valid, reason = checker.validate_order(
            "AAPL", "sell", 500, current_position=900
        )

        assert is_valid is True


class TestRiskCheckerPositionPercentage:
    """Test position percentage limit checks."""

    def test_order_allowed_within_percentage_limit(self, breaker):
        """Test order allowed when within % limit (20% default)."""
        config = RiskConfig(
            position_limits=PositionLimits(max_position_pct=Decimal("0.20"))
        )
        checker = RiskChecker(config=config, breaker=breaker)

        # $20k position on $100k portfolio = 20% (at limit)
        is_valid, reason = checker.validate_order(
            "AAPL",
            "buy",
            100,
            current_position=0,
            current_price=Decimal("200.00"),
            portfolio_value=Decimal("100000.00"),
        )

        assert is_valid is True

    def test_order_blocked_exceeding_percentage_limit(self, breaker):
        """Test order blocked when exceeding % limit."""
        config = RiskConfig(
            position_limits=PositionLimits(max_position_pct=Decimal("0.20"))
        )
        checker = RiskChecker(config=config, breaker=breaker)

        # $30k position on $100k portfolio = 30% (exceeds 20%)
        is_valid, reason = checker.validate_order(
            "AAPL",
            "buy",
            150,
            current_position=0,
            current_price=Decimal("200.00"),
            portfolio_value=Decimal("100000.00"),
        )

        assert is_valid is False
        assert "exceed 20.0% of portfolio" in reason

    def test_percentage_check_skipped_without_price(self, checker):
        """Test % check skipped if price not provided."""
        # Large order but no price provided (only absolute limit applies)
        is_valid, reason = checker.validate_order(
            "AAPL", "buy", 500, current_position=0
        )

        assert is_valid is True  # Passes absolute limit (500 < 1000)


class TestRiskCheckerCalculateNewPosition:
    """Test new position calculation."""

    def test_buy_order_increases_long_position(self, checker):
        """Test buy order increases long position."""
        new_pos = checker._calculate_new_position(100, "buy", 50)
        assert new_pos == 150

    def test_sell_order_decreases_long_position(self, checker):
        """Test sell order decreases long position."""
        new_pos = checker._calculate_new_position(100, "sell", 50)
        assert new_pos == 50

    def test_sell_order_creates_short_position(self, checker):
        """Test sell order can create short position."""
        new_pos = checker._calculate_new_position(100, "sell", 200)
        assert new_pos == -100

    def test_buy_order_reduces_short_position(self, checker):
        """Test buy order reduces short position."""
        new_pos = checker._calculate_new_position(-100, "buy", 50)
        assert new_pos == -50

    def test_invalid_side_raises_error(self, checker):
        """Test invalid side raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            checker._calculate_new_position(100, "invalid", 50)
        assert "Invalid side" in str(exc_info.value)


class TestRiskCheckerPortfolioExposure:
    """Test portfolio exposure checks."""

    def test_exposure_within_limits(self, checker):
        """Test portfolio exposure within all limits."""
        positions = [
            ("AAPL", 100, Decimal("200.00")),  # $20k long
            ("MSFT", 200, Decimal("300.00")),  # $60k long
            ("GOOGL", -50, Decimal("150.00")),  # $7.5k short
        ]
        # Total: $87.5k, Long: $80k, Short: $7.5k (all within default limits)

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is True
        assert reason == ""

    def test_exposure_exceeds_total_limit(self, breaker):
        """Test total exposure exceeds limit."""
        config = RiskConfig(
            portfolio_limits=PortfolioLimits(max_total_notional=Decimal("50000.00"))
        )
        checker = RiskChecker(config=config, breaker=breaker)

        positions = [
            ("AAPL", 100, Decimal("300.00")),  # $30k
            ("MSFT", 100, Decimal("300.00")),  # $30k
        ]
        # Total: $60k > $50k limit

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is False
        assert "Total exposure exceeds limit" in reason
        assert "$60000.00 > $50000.00" in reason

    def test_exposure_exceeds_long_limit(self, breaker):
        """Test long exposure exceeds limit."""
        config = RiskConfig(
            portfolio_limits=PortfolioLimits(max_long_exposure=Decimal("50000.00"))
        )
        checker = RiskChecker(config=config, breaker=breaker)

        positions = [
            ("AAPL", 100, Decimal("300.00")),  # $30k
            ("MSFT", 100, Decimal("300.00")),  # $30k
        ]
        # Long: $60k > $50k limit

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is False
        assert "Long exposure exceeds limit" in reason

    def test_exposure_exceeds_short_limit(self, breaker):
        """Test short exposure exceeds limit."""
        config = RiskConfig(
            portfolio_limits=PortfolioLimits(max_short_exposure=Decimal("10000.00"))
        )
        checker = RiskChecker(config=config, breaker=breaker)

        positions = [
            ("AAPL", -100, Decimal("200.00")),  # $20k short
        ]
        # Short: $20k > $10k limit

        is_valid, reason = checker.check_portfolio_exposure(positions)

        assert is_valid is False
        assert "Short exposure exceeds limit" in reason

    def test_empty_portfolio(self, checker):
        """Test empty portfolio passes all checks."""
        is_valid, reason = checker.check_portfolio_exposure([])

        assert is_valid is True


class TestRiskCheckerPriority:
    """Test check priority ordering."""

    def test_circuit_breaker_checked_first(self, breaker):
        """Test circuit breaker checked before other limits."""
        # Create config with blacklist
        config = RiskConfig(blacklist=["AAPL"])
        checker = RiskChecker(config=config, breaker=breaker)

        # Trip breaker
        breaker.trip("TEST")

        # Order for blacklisted symbol
        is_valid, reason = checker.validate_order("AAPL", "buy", 100)

        # Circuit breaker reason should appear (not blacklist)
        assert is_valid is False
        assert "Circuit breaker TRIPPED" in reason
        assert "blacklist" not in reason.lower()

    def test_blacklist_checked_before_limits(self, breaker):
        """Test blacklist checked before position limits."""
        config = RiskConfig(blacklist=["GME"])
        checker = RiskChecker(config=config, breaker=breaker)

        # Order that would exceed limit for blacklisted symbol
        is_valid, reason = checker.validate_order("GME", "buy", 2000)

        # Blacklist reason should appear (not position limit)
        assert is_valid is False
        assert "blacklist" in reason.lower()
        assert "Position limit" not in reason
