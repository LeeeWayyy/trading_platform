"""Tests for risk configuration models."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from libs.risk_management.config import (
    LossLimits,
    PortfolioLimits,
    PositionLimits,
    RiskConfig,
)


class TestPositionLimits:
    """Test PositionLimits model."""

    def test_default_values(self):
        """Test default position limits."""
        limits = PositionLimits()
        assert limits.max_position_size == 1000
        assert limits.max_position_pct == Decimal("0.20")

    def test_custom_values(self):
        """Test custom position limits."""
        limits = PositionLimits(
            max_position_size=500, max_position_pct=Decimal("0.15")
        )
        assert limits.max_position_size == 500
        assert limits.max_position_pct == Decimal("0.15")

    def test_validation_min_position_size(self):
        """Test position size must be >= 1."""
        with pytest.raises(ValidationError):
            PositionLimits(max_position_size=0)

        with pytest.raises(ValidationError):
            PositionLimits(max_position_size=-1)

    def test_validation_position_pct_range(self):
        """Test position pct must be between 0.01 and 1.00."""
        with pytest.raises(ValidationError):
            PositionLimits(max_position_pct=Decimal("0.00"))  # Too low

        with pytest.raises(ValidationError):
            PositionLimits(max_position_pct=Decimal("1.50"))  # Too high

    def test_validation_position_pct_min_valid(self):
        """Test minimum valid position pct (0.01)."""
        limits = PositionLimits(max_position_pct=Decimal("0.01"))
        assert limits.max_position_pct == Decimal("0.01")

    def test_validation_position_pct_max_valid(self):
        """Test maximum valid position pct (1.00)."""
        limits = PositionLimits(max_position_pct=Decimal("1.00"))
        assert limits.max_position_pct == Decimal("1.00")


class TestPortfolioLimits:
    """Test PortfolioLimits model."""

    def test_default_values(self):
        """Test default portfolio limits."""
        limits = PortfolioLimits()
        assert limits.max_total_notional == Decimal("100000.00")
        assert limits.max_long_exposure == Decimal("80000.00")
        assert limits.max_short_exposure == Decimal("20000.00")

    def test_custom_values(self):
        """Test custom portfolio limits."""
        limits = PortfolioLimits(
            max_total_notional=Decimal("50000.00"),
            max_long_exposure=Decimal("40000.00"),
            max_short_exposure=Decimal("10000.00"),
        )
        assert limits.max_total_notional == Decimal("50000.00")
        assert limits.max_long_exposure == Decimal("40000.00")
        assert limits.max_short_exposure == Decimal("10000.00")

    def test_validation_min_total_notional(self):
        """Test total notional must be >= $1000."""
        with pytest.raises(ValidationError):
            PortfolioLimits(max_total_notional=Decimal("999.99"))

        # Valid minimum
        limits = PortfolioLimits(max_total_notional=Decimal("1000.00"))
        assert limits.max_total_notional == Decimal("1000.00")

    def test_validation_non_negative_exposures(self):
        """Test exposures cannot be negative."""
        with pytest.raises(ValidationError):
            PortfolioLimits(max_long_exposure=Decimal("-1.00"))

        with pytest.raises(ValidationError):
            PortfolioLimits(max_short_exposure=Decimal("-1.00"))

    def test_zero_exposure_valid(self):
        """Test zero exposure is valid (no long or short allowed)."""
        limits = PortfolioLimits(
            max_long_exposure=Decimal("0.00"), max_short_exposure=Decimal("0.00")
        )
        assert limits.max_long_exposure == Decimal("0.00")
        assert limits.max_short_exposure == Decimal("0.00")


class TestLossLimits:
    """Test LossLimits model."""

    def test_default_values(self):
        """Test default loss limits."""
        limits = LossLimits()
        assert limits.daily_loss_limit == Decimal("5000.00")
        assert limits.max_drawdown_pct == Decimal("0.10")

    def test_custom_values(self):
        """Test custom loss limits."""
        limits = LossLimits(
            daily_loss_limit=Decimal("2000.00"), max_drawdown_pct=Decimal("0.05")
        )
        assert limits.daily_loss_limit == Decimal("2000.00")
        assert limits.max_drawdown_pct == Decimal("0.05")

    def test_validation_daily_loss_non_negative(self):
        """Test daily loss limit must be >= 0."""
        with pytest.raises(ValidationError):
            LossLimits(daily_loss_limit=Decimal("-1.00"))

        # Zero is valid (no loss limit)
        limits = LossLimits(daily_loss_limit=Decimal("0.00"))
        assert limits.daily_loss_limit == Decimal("0.00")

    def test_validation_drawdown_pct_range(self):
        """Test max drawdown must be between 0.01 and 0.50."""
        with pytest.raises(ValidationError):
            LossLimits(max_drawdown_pct=Decimal("0.00"))  # Too low

        with pytest.raises(ValidationError):
            LossLimits(max_drawdown_pct=Decimal("0.51"))  # Too high

        # Min valid
        limits = LossLimits(max_drawdown_pct=Decimal("0.01"))
        assert limits.max_drawdown_pct == Decimal("0.01")

        # Max valid
        limits = LossLimits(max_drawdown_pct=Decimal("0.50"))
        assert limits.max_drawdown_pct == Decimal("0.50")


class TestRiskConfig:
    """Test RiskConfig aggregation model."""

    def test_default_config(self):
        """Test default risk config."""
        config = RiskConfig()
        assert config.position_limits.max_position_size == 1000
        assert config.portfolio_limits.max_total_notional == Decimal("100000.00")
        assert config.loss_limits.daily_loss_limit == Decimal("5000.00")
        assert config.blacklist == []

    def test_custom_config(self):
        """Test custom risk config."""
        config = RiskConfig(
            position_limits=PositionLimits(max_position_size=500),
            blacklist=["GME", "AMC"],
        )
        assert config.position_limits.max_position_size == 500
        assert "GME" in config.blacklist
        assert "AMC" in config.blacklist

    def test_blacklist_case_sensitive(self):
        """Test blacklist is case-sensitive."""
        config = RiskConfig(blacklist=["AAPL"])
        assert "AAPL" in config.blacklist
        assert "aapl" not in config.blacklist

    def test_custom_all_limits(self):
        """Test custom config with all limits specified."""
        config = RiskConfig(
            position_limits=PositionLimits(
                max_position_size=200, max_position_pct=Decimal("0.10")
            ),
            portfolio_limits=PortfolioLimits(
                max_total_notional=Decimal("50000.00"),
                max_long_exposure=Decimal("40000.00"),
                max_short_exposure=Decimal("10000.00"),
            ),
            loss_limits=LossLimits(
                daily_loss_limit=Decimal("2000.00"), max_drawdown_pct=Decimal("0.05")
            ),
            blacklist=["GME", "AMC", "TSLA"],
        )

        # Verify all limits
        assert config.position_limits.max_position_size == 200
        assert config.position_limits.max_position_pct == Decimal("0.10")
        assert config.portfolio_limits.max_total_notional == Decimal("50000.00")
        assert config.portfolio_limits.max_long_exposure == Decimal("40000.00")
        assert config.portfolio_limits.max_short_exposure == Decimal("10000.00")
        assert config.loss_limits.daily_loss_limit == Decimal("2000.00")
        assert config.loss_limits.max_drawdown_pct == Decimal("0.05")
        assert len(config.blacklist) == 3

    def test_empty_blacklist_default(self):
        """Test blacklist defaults to empty list."""
        config = RiskConfig()
        assert config.blacklist == []
        assert isinstance(config.blacklist, list)
