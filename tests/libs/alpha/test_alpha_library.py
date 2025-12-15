"""Tests for canonical alpha implementations."""

from datetime import date, timedelta

import polars as pl
import pytest

from libs.alpha.alpha_library import (
    CANONICAL_ALPHAS,
    MomentumAlpha,
    QualityAlpha,
    ReversalAlpha,
    ValueAlpha,
    VolatilityAlpha,
    create_alpha,
)


class TestMomentumAlpha:
    """Tests for MomentumAlpha."""

    @pytest.fixture()
    def price_data(self):
        """Create 12 months of price data."""
        base_date = date(2024, 1, 1)
        days = 300  # More than 252 trading days
        n_stocks = 10

        dates = []
        permnos = []
        returns = []

        for d in range(days):
            dt = base_date - timedelta(days=days - d)
            for stock in range(n_stocks):
                dates.append(dt)
                permnos.append(stock + 1)
                # Higher stock number = higher returns (for testing signal direction)
                returns.append(0.001 * (stock + 1))

        return pl.DataFrame(
            {
                "permno": permnos,
                "date": dates,
                "ret": returns,
                "prc": [100.0] * len(dates),
                "shrout": [1000.0] * len(dates),
            }
        )

    def test_momentum_alpha_name(self):
        """Test alpha name format."""
        alpha = MomentumAlpha(lookback_days=252, skip_days=21)
        assert alpha.name == "momentum_252_21"

    def test_momentum_category(self):
        """Test category is momentum."""
        alpha = MomentumAlpha()
        assert alpha.category == "momentum"

    def test_momentum_signal_direction(self, price_data):
        """Test higher past returns -> higher signal."""
        alpha = MomentumAlpha()
        result = alpha.compute(price_data, None, date(2024, 1, 1))

        # Stock 10 has highest returns, should have highest signal
        signals = result.sort("permno")
        first_signal = signals.filter(pl.col("permno") == 1).select("signal").item()
        last_signal = signals.filter(pl.col("permno") == 10).select("signal").item()

        assert last_signal > first_signal

    def test_momentum_empty_data(self):
        """Test handling of empty data."""
        alpha = MomentumAlpha()
        empty = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "ret": pl.Float64,
                "prc": pl.Float64,
                "shrout": pl.Float64,
            }
        )

        result = alpha.compute(empty, None, date(2024, 1, 1))
        assert result.height == 0


class TestReversalAlpha:
    """Tests for ReversalAlpha."""

    @pytest.fixture()
    def recent_price_data(self):
        """Create 1 month of price data."""
        base_date = date(2024, 1, 1)
        days = 30
        n_stocks = 10

        dates = []
        permnos = []
        returns = []

        for d in range(days):
            dt = base_date - timedelta(days=days - d)
            for stock in range(n_stocks):
                dates.append(dt)
                permnos.append(stock + 1)
                returns.append(0.002 * (stock + 1))  # Higher stock = higher return

        return pl.DataFrame(
            {
                "permno": permnos,
                "date": dates,
                "ret": returns,
            }
        )

    def test_reversal_alpha_name(self):
        """Test alpha name format."""
        alpha = ReversalAlpha(lookback_days=21)
        assert alpha.name == "reversal_21"

    def test_reversal_category(self):
        """Test category is reversal."""
        alpha = ReversalAlpha()
        assert alpha.category == "reversal"

    def test_reversal_signal_direction(self, recent_price_data):
        """Test reversal: higher recent returns -> lower signal (sell winners)."""
        alpha = ReversalAlpha()
        result = alpha.compute(recent_price_data, None, date(2024, 1, 1))

        # Stock 10 has highest recent returns, should have LOWEST signal (reversal)
        signals = result.sort("permno")
        first_signal = signals.filter(pl.col("permno") == 1).select("signal").item()
        last_signal = signals.filter(pl.col("permno") == 10).select("signal").item()

        assert first_signal > last_signal  # Reversal inverts signal


class TestValueAlpha:
    """Tests for ValueAlpha."""

    @pytest.fixture()
    def value_data(self):
        """Create price and fundamental data for value alpha."""
        as_of_date = date(2024, 1, 1)

        prices = pl.DataFrame(
            {
                "permno": list(range(1, 11)),
                "date": [as_of_date] * 10,
                "prc": [100.0] * 10,  # Same price
                "shrout": [float(i * 1000) for i in range(1, 11)],  # Varying market cap
                "ret": [0.01] * 10,
            }
        )

        # Higher book equity for higher permno
        fundamentals = pl.DataFrame(
            {
                "permno": list(range(1, 11)),
                "datadate": [as_of_date - timedelta(days=100)] * 10,  # PIT-correct
                "ceq": [float(i * 50000) for i in range(1, 11)],  # Book equity
            }
        )

        return prices, fundamentals

    def test_value_alpha_name(self):
        """Test alpha name."""
        alpha = ValueAlpha()
        assert alpha.name == "value_bm"

    def test_value_category(self):
        """Test category is value."""
        alpha = ValueAlpha()
        assert alpha.category == "value"

    def test_value_requires_fundamentals(self):
        """Test value alpha returns empty without fundamentals."""
        alpha = ValueAlpha()
        prices = pl.DataFrame(
            {
                "permno": [1, 2],
                "date": [date(2024, 1, 1)] * 2,
                "prc": [100.0, 100.0],
                "shrout": [1000.0, 1000.0],
                "ret": [0.01, 0.02],
            }
        )

        result = alpha.compute(prices, None, date(2024, 1, 1))
        assert result.height == 0

    def test_value_signal_computed(self, value_data):
        """Test value signal is computed from B/M."""
        prices, fundamentals = value_data
        alpha = ValueAlpha()

        result = alpha.compute(prices, fundamentals, date(2024, 1, 1))

        assert result.height > 0
        assert "signal" in result.columns


class TestQualityAlpha:
    """Tests for QualityAlpha."""

    @pytest.fixture()
    def quality_data(self):
        """Create fundamental data for quality alpha."""
        as_of_date = date(2024, 1, 1)

        prices = pl.DataFrame(
            {
                "permno": list(range(1, 11)),
                "date": [as_of_date] * 10,
                "prc": [100.0] * 10,
                "shrout": [1000.0] * 10,
                "ret": [0.01] * 10,
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": list(range(1, 11)),
                "datadate": [as_of_date - timedelta(days=100)] * 10,
                "ni": [float(i * 1000) for i in range(1, 11)],  # Net income
                "ceq": [10000.0] * 10,  # Common equity
                "revt": [float(i * 5000) for i in range(1, 11)],  # Revenue
                "cogs": [float(i * 2000) for i in range(1, 11)],  # Cost of goods sold
                "at": [50000.0] * 10,  # Total assets
            }
        )

        return prices, fundamentals

    def test_quality_alpha_name_roe(self):
        """Test alpha name for ROE metric."""
        alpha = QualityAlpha(metric="roe")
        assert alpha.name == "quality_roe"

    def test_quality_alpha_name_gp(self):
        """Test alpha name for GP metric."""
        alpha = QualityAlpha(metric="gp")
        assert alpha.name == "quality_gp"

    def test_quality_category(self):
        """Test category is quality."""
        alpha = QualityAlpha()
        assert alpha.category == "quality"

    def test_quality_signal_roe(self, quality_data):
        """Test ROE quality signal."""
        prices, fundamentals = quality_data
        alpha = QualityAlpha(metric="roe")

        result = alpha.compute(prices, fundamentals, date(2024, 1, 1))

        assert result.height > 0
        # Higher net income -> higher ROE -> higher signal
        signals = result.sort("permno")
        first_signal = signals.filter(pl.col("permno") == 1).select("signal").item()
        last_signal = signals.filter(pl.col("permno") == 10).select("signal").item()
        assert last_signal > first_signal

    def test_quality_signal_gp(self, quality_data):
        """Test Gross Profitability quality signal."""
        prices, fundamentals = quality_data
        alpha = QualityAlpha(metric="gp")

        result = alpha.compute(prices, fundamentals, date(2024, 1, 1))

        assert result.height > 0


class TestVolatilityAlpha:
    """Tests for VolatilityAlpha."""

    @pytest.fixture()
    def vol_data(self):
        """Create price data with varying volatility."""
        base_date = date(2024, 1, 1)
        days = 260
        n_stocks = 10

        dates = []
        permnos = []
        returns = []

        import random

        random.seed(42)

        for d in range(days):
            dt = base_date - timedelta(days=days - d)
            for stock in range(n_stocks):
                dates.append(dt)
                permnos.append(stock + 1)
                # Higher stock number = higher volatility
                vol_scale = 0.005 * (stock + 1)
                returns.append(random.gauss(0, vol_scale))

        return pl.DataFrame(
            {
                "permno": permnos,
                "date": dates,
                "ret": returns,
            }
        )

    def test_volatility_alpha_name(self):
        """Test alpha name format."""
        alpha = VolatilityAlpha(lookback_days=252)
        assert alpha.name == "low_vol_252"

    def test_volatility_category(self):
        """Test category is volatility."""
        alpha = VolatilityAlpha()
        assert alpha.category == "volatility"

    def test_volatility_signal_direction(self, vol_data):
        """Test low volatility -> higher signal (low vol premium)."""
        alpha = VolatilityAlpha()
        result = alpha.compute(vol_data, None, date(2024, 1, 1))

        # Stock 1 has lowest volatility, should have highest signal
        signals = result.sort("permno")
        first_signal = signals.filter(pl.col("permno") == 1).select("signal").item()
        last_signal = signals.filter(pl.col("permno") == 10).select("signal").item()

        assert first_signal > last_signal  # Low vol = high signal


class TestCanonicalAlphasRegistry:
    """Tests for CANONICAL_ALPHAS registry."""

    def test_all_alphas_registered(self):
        """Test all 5 canonical alphas are in registry."""
        expected = {"momentum", "reversal", "value", "quality", "volatility"}
        assert set(CANONICAL_ALPHAS.keys()) == expected

    def test_registry_types(self):
        """Test registry values are alpha classes."""
        for _name, cls in CANONICAL_ALPHAS.items():
            assert issubclass(cls, object)
            instance = cls()
            assert hasattr(instance, "compute")
            assert hasattr(instance, "name")
            assert hasattr(instance, "category")


class TestCreateAlpha:
    """Tests for create_alpha factory function."""

    def test_create_momentum(self):
        """Test creating momentum alpha."""
        alpha = create_alpha("momentum", lookback_days=252, skip_days=21)
        assert isinstance(alpha, MomentumAlpha)
        assert alpha.name == "momentum_252_21"

    def test_create_reversal(self):
        """Test creating reversal alpha."""
        alpha = create_alpha("reversal", lookback_days=21)
        assert isinstance(alpha, ReversalAlpha)

    def test_create_value(self):
        """Test creating value alpha."""
        alpha = create_alpha("value")
        assert isinstance(alpha, ValueAlpha)

    def test_create_quality(self):
        """Test creating quality alpha."""
        alpha = create_alpha("quality", metric="roe")
        assert isinstance(alpha, QualityAlpha)

    def test_create_volatility(self):
        """Test creating volatility alpha."""
        alpha = create_alpha("volatility", lookback_days=252)
        assert isinstance(alpha, VolatilityAlpha)

    def test_create_unknown_raises(self):
        """Test creating unknown alpha raises ValueError."""
        with pytest.raises(ValueError, match="Unknown alpha") as exc_info:
            create_alpha("unknown_alpha")

        assert "Unknown alpha" in str(exc_info.value)
        assert "momentum" in str(exc_info.value)  # Should list available
