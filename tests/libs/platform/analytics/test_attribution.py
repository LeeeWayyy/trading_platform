"""Comprehensive tests for Factor Attribution Analysis.

Tests cover:
- Configuration validation
- Fama-French regression (FF3/FF5/FF6)
- Rolling factor exposure
- VIF multicollinearity detection
- Microcap and currency filters
- PIT compliance
- Serialization
- Error handling

Target: >90% code coverage
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from libs.platform.analytics.attribution import (
    FACTOR_COLS_BY_MODEL,
    FF3_FACTOR_COLS,
    FF5_FACTOR_COLS,
    FF6_FACTOR_COLS,
    AttributionResult,
    DataMismatchError,
    FactorAttribution,
    FactorAttributionConfig,
    InsufficientObservationsError,
    PITViolationError,
    ReturnDecompositionResult,
    RollingExposureResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def sample_ff_factors() -> pl.DataFrame:
    """Create sample Fama-French factor data."""
    np.random.seed(42)
    n_days = 300
    dates = [date(2020, 1, 1) + pl.duration(days=i) for i in range(n_days)]
    # Convert to proper date list
    dates = pl.date_range(date(2020, 1, 1), date(2020, 10, 26), eager=True).to_list()[:n_days]

    return pl.DataFrame(
        {
            "date": dates,
            "mkt_rf": np.random.normal(0.0004, 0.01, n_days),
            "smb": np.random.normal(0.0001, 0.005, n_days),
            "hml": np.random.normal(0.0001, 0.005, n_days),
            "rmw": np.random.normal(0.0001, 0.004, n_days),
            "cma": np.random.normal(0.0001, 0.004, n_days),
            "umd": np.random.normal(0.0002, 0.006, n_days),
            "rf": np.full(n_days, 0.0001),
        }
    )


@pytest.fixture()
def sample_portfolio_returns(sample_ff_factors: pl.DataFrame) -> pl.DataFrame:
    """Create sample portfolio returns matching factor dates."""
    np.random.seed(43)
    dates = sample_ff_factors["date"].to_list()
    returns = np.random.normal(0.0005, 0.015, len(dates))
    return pl.DataFrame(
        {
            "date": dates,
            "return": returns,
        }
    )


@pytest.fixture()
def sample_permno_returns() -> pl.DataFrame:
    """Create sample per-stock returns with permno."""
    np.random.seed(44)
    dates = pl.date_range(date(2020, 1, 1), date(2020, 10, 26), eager=True).to_list()[:100]
    permnos = [10001, 10002, 10003, 10004, 10005]

    rows = []
    for d in dates:
        for p in permnos:
            rows.append(
                {
                    "date": d,
                    "permno": p,
                    "return": np.random.normal(0.0005, 0.02),
                }
            )

    return pl.DataFrame(rows)


@pytest.fixture()
def sample_market_caps() -> pl.DataFrame:
    """Create sample market cap data."""
    np.random.seed(45)
    dates = pl.date_range(date(2020, 1, 1), date(2020, 10, 26), eager=True).to_list()[:100]
    permnos = [10001, 10002, 10003, 10004, 10005]

    # Different market caps: 10001 is microcap, others are larger
    base_caps = {
        10001: 50_000_000,  # $50M - below $100M threshold
        10002: 200_000_000,  # $200M
        10003: 500_000_000,  # $500M
        10004: 1_000_000_000,  # $1B
        10005: 5_000_000_000,  # $5B
    }

    rows = []
    for d in dates:
        for p in permnos:
            rows.append(
                {
                    "date": d,
                    "permno": p,
                    "market_cap": base_caps[p] * (1 + np.random.normal(0, 0.01)),
                }
            )

    return pl.DataFrame(rows)


@pytest.fixture()
def sample_currencies() -> pl.DataFrame:
    """Create sample currency data."""
    return pl.DataFrame(
        {
            "permno": [10001, 10002, 10003, 10004, 10005],
            "currency": ["USD", "USD", "USD", "CAD", "USD"],  # 10004 is CAD
        }
    )


@pytest.fixture()
def mock_ff_provider(sample_ff_factors: pl.DataFrame) -> MagicMock:
    """Create mock Fama-French provider."""
    provider = MagicMock()
    provider.get_factors.return_value = sample_ff_factors
    provider.data_version = "v1.0"
    return provider


@pytest.fixture()
def mock_crsp_provider(sample_market_caps: pl.DataFrame) -> MagicMock:
    """Create mock CRSP provider."""
    provider = MagicMock()
    # Return price data that will result in market caps
    prices = sample_market_caps.with_columns(
        [
            (pl.col("market_cap") / 1000 / 100).alias("prc"),  # price
            pl.lit(100.0).alias("shrout"),  # shares in thousands
        ]
    ).select(["date", "permno", "prc", "shrout"])
    provider.get_daily_prices.return_value = prices
    provider.data_version = "v2.0"
    return provider


# =============================================================================
# Configuration Tests
# =============================================================================


class TestFactorAttributionConfig:
    """Tests for FactorAttributionConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = FactorAttributionConfig()
        assert config.model == "ff5"
        assert config.window_trading_days == 252
        assert config.std_errors == "newey_west"
        assert config.min_observations == 60
        assert config.vif_threshold == 5.0
        assert config.min_market_cap_usd == 100_000_000
        assert config.market_cap_percentile == 0.20
        assert config.currency == "USD"

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = FactorAttributionConfig(
            model="ff3",
            std_errors="hc3",
            newey_west_lags=10,
            min_market_cap_usd=None,
        )
        assert config.model == "ff3"
        assert config.std_errors == "hc3"
        assert config.newey_west_lags == 10
        assert config.min_market_cap_usd is None

    def test_config_frozen(self) -> None:
        """Test that config is immutable."""
        config = FactorAttributionConfig()
        with pytest.raises(FrozenInstanceError):
            config.model = "ff3"  # type: ignore


class TestFactorColumns:
    """Tests for factor column constants."""

    def test_ff3_columns(self) -> None:
        """Test FF3 column names."""
        assert FF3_FACTOR_COLS == ("mkt_rf", "smb", "hml")

    def test_ff5_columns(self) -> None:
        """Test FF5 column names."""
        assert FF5_FACTOR_COLS == ("mkt_rf", "smb", "hml", "rmw", "cma")

    def test_ff6_columns(self) -> None:
        """Test FF6 column names."""
        assert FF6_FACTOR_COLS == ("mkt_rf", "smb", "hml", "rmw", "cma", "umd")

    def test_factor_cols_by_model(self) -> None:
        """Test model to columns mapping."""
        assert FACTOR_COLS_BY_MODEL["ff3"] == FF3_FACTOR_COLS
        assert FACTOR_COLS_BY_MODEL["ff5"] == FF5_FACTOR_COLS
        assert FACTOR_COLS_BY_MODEL["ff6"] == FF6_FACTOR_COLS


# =============================================================================
# Regression Tests
# =============================================================================


class TestFactorAttributionFit:
    """Tests for fit() method."""

    def test_fit_ff3(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test FF3 regression."""
        config = FactorAttributionConfig(model="ff3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
            portfolio_id="test_portfolio",
        )

        assert isinstance(result, AttributionResult)
        assert result.portfolio_id == "test_portfolio"
        assert result.n_observations > 0
        assert "mkt_rf" in result.betas
        assert "smb" in result.betas
        assert "hml" in result.betas
        assert "rmw" not in result.betas  # FF3 doesn't have RMW

    def test_fit_ff5(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test FF5 regression."""
        config = FactorAttributionConfig(model="ff5", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert "mkt_rf" in result.betas
        assert "smb" in result.betas
        assert "hml" in result.betas
        assert "rmw" in result.betas
        assert "cma" in result.betas
        assert "umd" not in result.betas

    def test_fit_ff6(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test FF6 regression."""
        config = FactorAttributionConfig(model="ff6", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert "umd" in result.betas
        assert len(result.betas) == 6

    def test_fit_ols_se(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test OLS standard errors."""
        config = FactorAttributionConfig(std_errors="ols", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert result.regression_config["std_errors"] == "ols"

    def test_fit_hc3_se(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test HC3 standard errors."""
        config = FactorAttributionConfig(std_errors="hc3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert result.regression_config["std_errors"] == "hc3"

    def test_fit_newey_west_auto_lags(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test Newey-West with auto-computed lags."""
        config = FactorAttributionConfig(
            std_errors="newey_west",
            newey_west_lags=0,  # Auto
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert result.regression_config["newey_west_lags"] is not None
        assert result.regression_config["newey_west_lags"] >= 1

    def test_fit_newey_west_explicit_lags(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test Newey-West with explicit lags."""
        config = FactorAttributionConfig(
            std_errors="newey_west",
            newey_west_lags=10,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert result.regression_config["newey_west_lags"] == 10


# =============================================================================
# Filter Tests
# =============================================================================


class TestMicrocapFilter:
    """Tests for microcap filtering."""

    def test_microcap_filter_removes_small_stocks(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
    ) -> None:
        """Test that microcap filter removes small stocks."""
        config = FactorAttributionConfig(
            min_market_cap_usd=100_000_000,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            market_caps=sample_market_caps,
        )

        # permno 10001 ($50M) should be filtered out
        assert result.filter_stats.get("microcap_filter_applied") is True
        assert result.filter_stats.get("after_microcap", 0) < result.filter_stats.get("total", 0)

    def test_microcap_percentile_filter(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
    ) -> None:
        """Test percentile-based microcap filter."""
        config = FactorAttributionConfig(
            min_market_cap_usd=None,
            market_cap_percentile=0.20,  # Filter bottom 20%
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            market_caps=sample_market_caps,
        )

        assert result.filter_stats.get("microcap_filter_applied") is True

    def test_microcap_filter_disabled(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
    ) -> None:
        """Test that filter can be disabled."""
        config = FactorAttributionConfig(
            min_market_cap_usd=None,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
        )

        assert result.filter_stats.get("microcap_filter_applied") is False


class TestCurrencyFilter:
    """Tests for currency filtering."""

    def test_currency_filter_keeps_matching(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_currencies: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
    ) -> None:
        """Test that currency filter keeps matching currencies."""
        config = FactorAttributionConfig(
            currency="USD",
            min_market_cap_usd=None,
            market_cap_percentile=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            currencies=sample_currencies,
            market_caps=sample_market_caps,
        )

        # permno 10004 (CAD) should be filtered out
        assert result.filter_stats.get("currency_filter_applied") is True

    def test_currency_filter_disabled(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
    ) -> None:
        """Test that currency filter can be disabled."""
        config = FactorAttributionConfig(
            currency=None,
            min_market_cap_usd=None,
            market_cap_percentile=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
        )

        assert result.filter_stats.get("currency_filter_applied") is False

    def test_currency_filter_missing_data_error(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
    ) -> None:
        """Test error when currency filter enabled but no data."""
        config = FactorAttributionConfig(
            currency="USD",
            min_market_cap_usd=None,
            market_cap_percentile=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        with pytest.raises(ValueError, match="Currency filter.*enabled but"):
            attribution.fit(
                portfolio_returns=sample_permno_returns,
                start_date=date(2020, 1, 1),
                end_date=date(2020, 4, 10),
                currencies=None,  # No currency data
            )


# =============================================================================
# VIF Tests
# =============================================================================


class TestVIFCheck:
    """Tests for VIF multicollinearity check."""

    def test_vif_normal_factors(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test VIF with normal factor data."""
        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Normal data should not trigger VIF warnings
        # (may have some due to random correlations)
        assert isinstance(result.multicollinearity_warnings, list)

    def test_vif_identical_factors_warning(
        self,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test VIF warning for identical factors."""
        # Create factors where SMB = HML (perfect collinearity)
        np.random.seed(42)
        n_days = len(sample_portfolio_returns)
        dates = sample_portfolio_returns["date"].to_list()
        factor_vals = np.random.normal(0, 0.01, n_days)

        ff_factors = pl.DataFrame(
            {
                "date": dates,
                "mkt_rf": np.random.normal(0, 0.01, n_days),
                "smb": factor_vals,
                "hml": factor_vals,  # Identical to SMB
                "rmw": np.random.normal(0, 0.01, n_days),
                "cma": np.random.normal(0, 0.01, n_days),
                "rf": np.full(n_days, 0.0001),
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = ff_factors

        config = FactorAttributionConfig(model="ff5", currency=None)
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Should have VIF warnings due to identical factors
        assert len(result.multicollinearity_warnings) > 0

    def test_vif_constant_column_warning(
        self,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test VIF warning for constant column."""
        np.random.seed(42)
        n_days = len(sample_portfolio_returns)
        dates = sample_portfolio_returns["date"].to_list()

        ff_factors = pl.DataFrame(
            {
                "date": dates,
                "mkt_rf": np.random.normal(0, 0.01, n_days),
                "smb": np.full(n_days, 0.001),  # Constant!
                "hml": np.random.normal(0, 0.01, n_days),
                "rf": np.full(n_days, 0.0001),
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = ff_factors

        config = FactorAttributionConfig(model="ff3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Should have warning about constant column
        assert any(
            "Constant" in w or "constant" in w.lower() for w in result.multicollinearity_warnings
        )


# =============================================================================
# Rolling Exposure Tests
# =============================================================================


class TestRollingExposures:
    """Tests for compute_rolling_exposures()."""

    def test_rolling_monthly(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test monthly rolling exposures."""
        config = FactorAttributionConfig(
            rebalance_freq="monthly",
            window_trading_days=60,  # Smaller for test
            min_observations=30,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert isinstance(result, RollingExposureResult)
        assert result.exposures is not None
        assert len(result.exposures) > 0
        assert "date" in result.exposures.columns
        assert "factor_name" in result.exposures.columns
        assert "beta" in result.exposures.columns

    def test_rolling_weekly(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test weekly rolling exposures."""
        config = FactorAttributionConfig(
            rebalance_freq="weekly",
            window_trading_days=60,
            min_observations=30,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert result.exposures is not None

    def test_rolling_skipped_windows(
        self,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test that insufficient observation windows are skipped."""
        # Create sparse factor data
        sparse_dates = sample_portfolio_returns["date"].to_list()[::10]  # Every 10th day
        ff_factors = pl.DataFrame(
            {
                "date": sparse_dates,
                "mkt_rf": np.random.normal(0, 0.01, len(sparse_dates)),
                "smb": np.random.normal(0, 0.01, len(sparse_dates)),
                "hml": np.random.normal(0, 0.01, len(sparse_dates)),
                "rf": np.full(len(sparse_dates), 0.0001),
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = ff_factors

        config = FactorAttributionConfig(
            model="ff3",
            rebalance_freq="monthly",
            window_trading_days=252,
            min_observations=60,  # Too many for sparse data
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Should have skipped windows
        assert len(result.skipped_windows) > 0


# =============================================================================
# Return Decomposition Tests
# =============================================================================


class TestReturnDecomposition:
    """Tests for decompose_returns()."""

    def test_decomposition_reconciles(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test that decomposition adds up correctly."""
        config = FactorAttributionConfig(model="ff3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        # First fit
        attr_result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Then decompose
        decomp_result = attribution.decompose_returns(
            portfolio_returns=sample_portfolio_returns,
            attribution_result=attr_result,
        )

        assert decomp_result.decomposition is not None
        df = decomp_result.decomposition

        # Check columns exist
        assert "excess_return" in df.columns
        assert "alpha_contrib" in df.columns
        assert "total_factor_contrib" in df.columns
        assert "residual" in df.columns

        # Check reconciliation: excess_return ≈ alpha + factor_contrib + residual
        computed = (df["alpha_contrib"] + df["total_factor_contrib"] + df["residual"]).to_numpy()
        actual = df["excess_return"].to_numpy()

        np.testing.assert_allclose(computed, actual, rtol=1e-10)


# =============================================================================
# PIT Compliance Tests
# =============================================================================


class TestPITCompliance:
    """Tests for PIT (Point-in-Time) compliance."""

    def test_pit_violation_raises_error(
        self,
        mock_ff_provider: MagicMock,
    ) -> None:
        """Test that PIT violation raises error."""
        # Create portfolio with future dates
        future_portfolio = pl.DataFrame(
            {
                "date": [date(2025, 1, 1), date(2025, 1, 2)],
                "return": [0.01, -0.01],
            }
        )

        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        with pytest.raises(PITViolationError):
            attribution.fit(
                portfolio_returns=future_portfolio,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 2),
                as_of_date=date(2024, 12, 31),  # Earlier than data
            )

    def test_pit_bounds_end_date(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test that as_of_date bounds effective end date."""
        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),  # Beyond as_of_date
            as_of_date=date(2020, 6, 30),
        )

        # Should succeed but use bounded end date
        assert result.as_of_date == date(2020, 6, 30)


# =============================================================================
# Serialization Tests
# =============================================================================


class TestSerialization:
    """Tests for result serialization."""

    def test_attribution_result_to_registry_dict(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test AttributionResult serialization."""
        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        registry_dict = result.to_registry_dict()

        assert isinstance(registry_dict, dict)
        assert "schema_version" in registry_dict
        assert "betas" in registry_dict
        assert "alpha_annualized_bps" in registry_dict
        assert "computation_timestamp" in registry_dict

    def test_rolling_result_to_registry_dict(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test RollingExposureResult serialization."""
        config = FactorAttributionConfig(
            rebalance_freq="monthly",
            window_trading_days=60,
            min_observations=30,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        registry_dict = result.to_registry_dict()

        assert isinstance(registry_dict, dict)
        assert "exposures" in registry_dict
        assert "skipped_windows" in registry_dict

    def test_nan_to_none_conversion(self) -> None:
        """Test that NaN values are converted to None."""
        result = AttributionResult(
            alpha_annualized_bps=float("nan"),
        )

        registry_dict = result.to_registry_dict()
        assert registry_dict["alpha_annualized_bps"] is None


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    def test_insufficient_observations_error(
        self,
    ) -> None:
        """Test InsufficientObservationsError is raised."""
        # Create minimal data
        short_portfolio = pl.DataFrame(
            {
                "date": [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)],
                "return": [0.01, -0.01, 0.005],
            }
        )
        short_factors = pl.DataFrame(
            {
                "date": [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)],
                "mkt_rf": [0.01, -0.01, 0.005],
                "smb": [0.001, -0.001, 0.0005],
                "hml": [0.002, -0.002, 0.001],
                "rf": [0.0001, 0.0001, 0.0001],
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = short_factors

        config = FactorAttributionConfig(
            model="ff3",
            min_observations=60,  # More than 3
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        with pytest.raises(InsufficientObservationsError):
            attribution.fit(
                portfolio_returns=short_portfolio,
                start_date=date(2020, 1, 1),
                end_date=date(2020, 1, 3),
            )

    def test_data_mismatch_error(
        self,
    ) -> None:
        """Test DataMismatchError when no date overlap."""
        portfolio = pl.DataFrame(
            {
                "date": [date(2020, 1, 1), date(2020, 1, 2)],
                "return": [0.01, -0.01],
            }
        )
        factors = pl.DataFrame(
            {
                "date": [date(2021, 1, 1), date(2021, 1, 2)],  # Different year!
                "mkt_rf": [0.01, -0.01],
                "smb": [0.001, -0.001],
                "hml": [0.002, -0.002],
                "rf": [0.0001, 0.0001],
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = factors

        config = FactorAttributionConfig(model="ff3", min_observations=1, currency=None)
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        with pytest.raises(DataMismatchError):
            attribution.fit(
                portfolio_returns=portfolio,
                start_date=date(2020, 1, 1),
                end_date=date(2020, 1, 2),
            )


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests."""

    def test_full_pipeline_with_filters(
        self,
        mock_ff_provider: MagicMock,
        mock_crsp_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
        sample_currencies: pl.DataFrame,
    ) -> None:
        """Test full pipeline with all filters."""
        config = FactorAttributionConfig(
            model="ff5",
            min_market_cap_usd=100_000_000,
            market_cap_percentile=0.10,
            currency="USD",
        )
        attribution = FactorAttribution(
            ff_provider=mock_ff_provider,
            crsp_provider=mock_crsp_provider,
            config=config,
        )

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            market_caps=sample_market_caps,
            currencies=sample_currencies,
        )

        # Should complete successfully
        assert result.n_observations > 0
        assert result.filter_stats["microcap_filter_applied"] is True
        assert result.filter_stats["currency_filter_applied"] is True

    def test_version_tracking(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test that version IDs are tracked."""
        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
            portfolio_version="v1.0.0",
        )

        assert result.dataset_version_id != ""
        assert result.dataset_versions["portfolio"] == "v1.0.0"


# =============================================================================
# Additional Coverage Tests
# =============================================================================


class TestAdditionalCoverage:
    """Additional tests for coverage improvement."""

    def test_dashboard_dict_with_alpha(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test dashboard dict includes alpha_display."""
        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        dashboard_dict = result.to_dashboard_dict()
        assert "alpha_display" in dashboard_dict
        assert "bps" in dashboard_dict["alpha_display"]

    def test_value_weight_aggregation(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
    ) -> None:
        """Test value-weighted aggregation."""
        config = FactorAttributionConfig(
            aggregation_method="value_weight",
            min_market_cap_usd=None,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            market_caps=sample_market_caps,
        )

        assert result.n_observations > 0

    def test_crsp_market_cap_computation(
        self,
        mock_ff_provider: MagicMock,
        mock_crsp_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
    ) -> None:
        """Test market cap computation via CRSP provider."""
        config = FactorAttributionConfig(
            min_market_cap_usd=100_000_000,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(
            ff_provider=mock_ff_provider,
            crsp_provider=mock_crsp_provider,
            config=config,
        )

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            # No market_caps provided - should use CRSP
        )

        assert result.filter_stats.get("microcap_filter_applied") is True

    def test_rolling_daily_rebalance(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test daily rolling rebalance frequency."""
        config = FactorAttributionConfig(
            rebalance_freq="daily",
            window_trading_days=60,
            min_observations=30,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
        )

        assert result.exposures is not None
        # Daily should have more dates than monthly
        n_dates = result.exposures["date"].n_unique()
        assert n_dates > 10

    def test_rolling_with_permno_data(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
    ) -> None:
        """Test rolling exposures with permno-level data."""
        config = FactorAttributionConfig(
            rebalance_freq="monthly",
            window_trading_days=60,
            min_observations=30,
            min_market_cap_usd=None,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            market_caps=sample_market_caps,
        )

        assert result.exposures is not None

    def test_currency_version_tracking(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
        sample_currencies: pl.DataFrame,
        sample_market_caps: pl.DataFrame,
    ) -> None:
        """Test currency version is tracked."""
        config = FactorAttributionConfig(
            currency="USD",
            min_market_cap_usd=None,
            market_cap_percentile=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_permno_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 4, 10),
            currencies=sample_currencies,
            currency_version="curr_v1.0",
            market_caps=sample_market_caps,
        )

        assert result.dataset_versions.get("currencies") == "curr_v1.0"

    def test_decomposition_with_explicit_factors(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
        sample_ff_factors: pl.DataFrame,
    ) -> None:
        """Test decompose_returns with explicit factor data."""
        config = FactorAttributionConfig(model="ff3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        # First fit
        attr_result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Decompose with explicit factors
        decomp_result = attribution.decompose_returns(
            portfolio_returns=sample_portfolio_returns,
            attribution_result=attr_result,
            ff_factors=sample_ff_factors,  # Explicit factors
        )

        assert decomp_result.decomposition is not None

    def test_rolling_exposure_serialization(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test rolling exposure dashboard dict."""
        config = FactorAttributionConfig(
            rebalance_freq="monthly",
            window_trading_days=60,
            min_observations=30,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        dashboard_dict = result.to_dashboard_dict()
        assert "exposures" in dashboard_dict
        assert dashboard_dict["exposures"] is not None

    def test_decomposition_result_serialization(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test decomposition result serialization."""
        config = FactorAttributionConfig(model="ff3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        attr_result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        decomp_result = attribution.decompose_returns(
            portfolio_returns=sample_portfolio_returns,
            attribution_result=attr_result,
        )

        registry_dict = decomp_result.to_registry_dict()
        assert "decomposition" in registry_dict
        assert "attribution_result" in registry_dict

        dashboard_dict = decomp_result.to_dashboard_dict()
        assert dashboard_dict == registry_dict

    def test_check_multicollinearity_public_method(
        self,
        mock_ff_provider: MagicMock,
        sample_ff_factors: pl.DataFrame,
    ) -> None:
        """Test public check_multicollinearity method."""
        config = FactorAttributionConfig(currency=None)
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        factor_df = sample_ff_factors.select(["mkt_rf", "smb", "hml"])
        warnings = attribution.check_multicollinearity(factor_df)

        assert isinstance(warnings, list)

    def test_microcap_filter_error_no_data(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
    ) -> None:
        """Test error when microcap filter enabled without data."""
        config = FactorAttributionConfig(
            min_market_cap_usd=100_000_000,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        with pytest.raises(ValueError, match="Microcap filter enabled"):
            attribution.fit(
                portfolio_returns=sample_permno_returns,
                start_date=date(2020, 1, 1),
                end_date=date(2020, 4, 10),
                market_caps=None,  # No market caps
            )

    def test_value_weight_requires_market_caps(
        self,
        mock_ff_provider: MagicMock,
        sample_permno_returns: pl.DataFrame,
    ) -> None:
        """Test value_weight aggregation requires market caps."""
        config = FactorAttributionConfig(
            aggregation_method="value_weight",
            min_market_cap_usd=None,
            market_cap_percentile=None,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        with pytest.raises(ValueError, match="market_caps required"):
            attribution.fit(
                portfolio_returns=sample_permno_returns,
                start_date=date(2020, 1, 1),
                end_date=date(2020, 4, 10),
                market_caps=None,
            )

    def test_rolling_insufficient_windows(
        self,
    ) -> None:
        """Test that windows with insufficient observations are tracked."""
        # Create minimal data - only 1 observation but need 60
        portfolio = pl.DataFrame(
            {
                "date": [date(2020, 1, 1)],
                "return": [0.01],
            }
        )
        factors = pl.DataFrame(
            {
                "date": [date(2020, 1, 1)],
                "mkt_rf": [0.01],
                "smb": [0.001],
                "hml": [0.002],
                "rf": [0.0001],
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = factors

        config = FactorAttributionConfig(
            model="ff3",
            rebalance_freq="monthly",
            window_trading_days=252,
            min_observations=60,  # Needs 60 but only have 1
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=portfolio,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 1),
        )

        # Should have skipped windows tracked
        assert len(result.skipped_windows) > 0
        assert result.skipped_windows[0]["reason"] == "insufficient_observations"

        # Exposures should have NaN values for skipped windows
        assert result.exposures is not None
        assert result.exposures["beta"].is_nan().all()

    def test_rolling_no_overlap_window(
        self,
    ) -> None:
        """Test rolling exposures handles no-overlap windows."""
        # Create portfolio and factors with no overlapping dates
        portfolio = pl.DataFrame(
            {
                "date": [date(2020, 1, 1), date(2020, 1, 2)],
                "return": [0.01, 0.02],
            }
        )
        factors = pl.DataFrame(
            {
                "date": [date(2020, 2, 1), date(2020, 2, 2)],  # Different dates!
                "mkt_rf": [0.01, 0.02],
                "smb": [0.001, 0.002],
                "hml": [0.002, 0.003],
                "rf": [0.0001, 0.0001],
            }
        )

        mock_provider = MagicMock()
        mock_provider.get_factors.return_value = factors

        config = FactorAttributionConfig(
            model="ff3",
            rebalance_freq="monthly",
            window_trading_days=60,
            min_observations=1,
            currency=None,
        )
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        result = attribution.compute_rolling_exposures(
            portfolio_returns=portfolio,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 2, 28),
        )

        # Should have skipped windows due to no overlap
        assert len(result.skipped_windows) > 0

    def test_rolling_exposure_nan_serialization(
        self,
        mock_ff_provider: MagicMock,
    ) -> None:
        """Test NaN values are handled in serialization."""
        # Create result with NaN values
        exposures = pl.DataFrame(
            {
                "date": [date(2020, 1, 1)],
                "factor_name": ["mkt_rf"],
                "beta": [float("nan")],
                "t_stat": [float("nan")],
                "p_value": [float("nan")],
            }
        )

        result = RollingExposureResult(
            portfolio_id="test",
            exposures=exposures,
        )

        registry_dict = result.to_registry_dict()
        assert registry_dict["exposures"][0]["beta"] is None  # NaN converted to None

    def test_decomposition_nan_serialization(self) -> None:
        """Test decomposition result handles NaN serialization."""
        decomp = pl.DataFrame(
            {
                "date": [date(2020, 1, 1)],
                "portfolio_return": [float("nan")],
                "risk_free": [0.0001],
                "excess_return": [float("nan")],
                "alpha_contrib": [0.001],
                "residual": [float("nan")],
            }
        )

        result = ReturnDecompositionResult(
            portfolio_id="test",
            decomposition=decomp,
        )

        registry_dict = result.to_registry_dict()
        assert registry_dict["decomposition"][0]["portfolio_return"] is None

    def test_aggregated_portfolio_no_filter(
        self,
        mock_ff_provider: MagicMock,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test aggregated portfolio returns bypass filtering."""
        config = FactorAttributionConfig(
            currency="USD",  # Filter enabled but won't apply to aggregated data
        )
        attribution = FactorAttribution(ff_provider=mock_ff_provider, config=config)

        # No permno column = already aggregated
        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,  # No permno
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        # Should succeed - currency filter not applied to aggregated data
        assert result.n_observations > 0
        assert result.filter_stats.get("currency_filter_applied") is None

    def test_no_ff_version(
        self,
        sample_portfolio_returns: pl.DataFrame,
    ) -> None:
        """Test handling when FF provider has no version."""
        mock_provider = MagicMock(spec=[])  # No auto-created attributes
        mock_provider.get_factors = MagicMock(
            return_value=pl.DataFrame(
                {
                    "date": sample_portfolio_returns["date"].to_list(),
                    "mkt_rf": np.random.normal(0, 0.01, len(sample_portfolio_returns)),
                    "smb": np.random.normal(0, 0.01, len(sample_portfolio_returns)),
                    "hml": np.random.normal(0, 0.01, len(sample_portfolio_returns)),
                    "rf": np.full(len(sample_portfolio_returns), 0.0001),
                }
            )
        )
        # No data_version attribute

        config = FactorAttributionConfig(model="ff3", currency=None)
        attribution = FactorAttribution(ff_provider=mock_provider, config=config)

        result = attribution.fit(
            portfolio_returns=sample_portfolio_returns,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 10, 26),
        )

        assert result.dataset_versions.get("fama_french") is None
