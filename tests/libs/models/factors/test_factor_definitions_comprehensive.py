"""
Comprehensive unit tests for libs/models/factors/factor_definitions.py.

This test file provides extensive coverage for:
- Factor definition classes (all 5 canonical factors)
- Factor metadata and validation
- Factor computation interfaces
- Data model consistency
- Edge cases and error handling

Target: 85%+ branch coverage
"""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from libs.models.factors import (
    CANONICAL_FACTORS,
    BookToMarketFactor,
    FactorConfig,
    FactorResult,
    MomentumFactor,
    RealizedVolFactor,
    ROEFactor,
    SizeFactor,
)

# =============================================================================
# FactorConfig Tests
# =============================================================================


class TestFactorConfigEdgeCases:
    """Edge case tests for FactorConfig."""

    def test_config_with_zero_winsorize(self):
        """Config allows zero winsorization (no winsorizing)."""
        config = FactorConfig(winsorize_pct=0.0)
        assert config.winsorize_pct == 0.0

    def test_config_with_high_winsorize(self):
        """Config allows high winsorization percentages."""
        config = FactorConfig(winsorize_pct=0.10)
        assert config.winsorize_pct == 0.10

    def test_config_with_zero_min_stocks(self):
        """Config allows zero minimum stocks per sector."""
        config = FactorConfig(min_stocks_per_sector=0)
        assert config.min_stocks_per_sector == 0

    def test_config_with_short_lookback(self):
        """Config allows short lookback periods."""
        config = FactorConfig(lookback_days=30)
        assert config.lookback_days == 30

    def test_config_with_long_lookback(self):
        """Config allows long lookback periods."""
        config = FactorConfig(lookback_days=730)  # 2 years
        assert config.lookback_days == 730

    def test_config_report_date_column_none(self):
        """Config allows None for report_date_column (default)."""
        config = FactorConfig()
        assert config.report_date_column is None

    def test_config_report_date_column_custom(self):
        """Config accepts custom report date column name."""
        config = FactorConfig(report_date_column="rdq")
        assert config.report_date_column == "rdq"


# =============================================================================
# FactorResult Tests - Additional Coverage
# =============================================================================


class TestFactorResultEdgeCases:
    """Edge case tests for FactorResult."""

    def test_to_storage_format_single_version(self):
        """to_storage_format() handles single dataset version."""
        df = pl.DataFrame({"permno": [1], "raw_value": [0.1], "zscore": [0.5], "percentile": [0.7]})
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        storage_df = result.to_storage_format()
        version_id = storage_df["dataset_version_id"][0]

        assert version_id == "crsp:v1.0.0"

    def test_to_storage_format_many_versions(self):
        """to_storage_format() handles multiple dataset versions."""
        df = pl.DataFrame({"permno": [1], "raw_value": [0.1], "zscore": [0.5], "percentile": [0.7]})
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={
                "crsp": "v1.0.0",
                "compustat": "v2.0.0",
                "fama_french": "v3.0.0",
                "taq": "v4.0.0",
            },
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        storage_df = result.to_storage_format()
        version_id = storage_df["dataset_version_id"][0]

        # All versions should be present
        assert "crsp:v1.0.0" in version_id
        assert "compustat:v2.0.0" in version_id
        assert "fama_french:v3.0.0" in version_id
        assert "taq:v4.0.0" in version_id
        # Should be sorted alphabetically
        assert version_id.index("compustat") < version_id.index("crsp")
        assert version_id.index("crsp") < version_id.index("fama_french")

    def test_to_storage_format_preserves_all_columns(self):
        """to_storage_format() preserves all original columns."""
        df = pl.DataFrame(
            {
                "permno": [1, 2],
                "date": [date(2023, 6, 30), date(2023, 6, 30)],
                "factor_name": ["momentum_12_1", "momentum_12_1"],
                "raw_value": [0.1, 0.2],
                "zscore": [0.5, 1.0],
                "percentile": [0.7, 0.9],
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        storage_df = result.to_storage_format()

        # Original columns should be preserved
        assert "permno" in storage_df.columns
        assert "date" in storage_df.columns
        assert "factor_name" in storage_df.columns
        assert "raw_value" in storage_df.columns
        assert "zscore" in storage_df.columns
        assert "percentile" in storage_df.columns
        # New columns should be added
        assert "dataset_version_id" in storage_df.columns
        assert "computation_timestamp" in storage_df.columns

    def test_to_storage_format_empty_exposures(self):
        """to_storage_format() handles empty DataFrame."""
        df = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "raw_value": pl.Float64,
                "zscore": pl.Float64,
                "percentile": pl.Float64,
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        storage_df = result.to_storage_format()

        assert storage_df.height == 0
        assert "dataset_version_id" in storage_df.columns
        assert "computation_timestamp" in storage_df.columns


class TestFactorResultValidationEdgeCases:
    """Edge case tests for FactorResult.validate()."""

    def test_validate_with_missing_columns(self):
        """validate() handles DataFrames missing some columns gracefully."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, 2.0, 3.0],
                # Missing zscore and percentile
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        # Should not raise - gracefully handles missing columns
        errors = result.validate()
        assert isinstance(errors, list)

    def test_validate_with_all_nulls(self):
        """validate() detects when all values are null."""
        # Use explicit Float64 type with nulls to avoid polars dtype issues
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": pl.Series([None, None, None], dtype=pl.Float64),
                "zscore": pl.Series([None, None, None], dtype=pl.Float64),
                "percentile": pl.Series([None, None, None], dtype=pl.Float64),
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        errors = result.validate()

        # Should detect nulls in all three columns
        assert len(errors) >= 3
        assert any("raw_value" in e and "null" in e.lower() for e in errors)
        assert any("zscore" in e and "null" in e.lower() for e in errors)
        assert any("percentile" in e and "null" in e.lower() for e in errors)

    def test_validate_with_mixed_infinity(self):
        """validate() detects both positive and negative infinity."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4],
                "raw_value": [1.0, float("inf"), float("-inf"), 3.0],
                "zscore": [0.5, 0.2, 0.1, 0.0],
                "percentile": [0.9, 0.5, 0.1, 0.3],
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        errors = result.validate()

        assert len(errors) >= 1
        assert "infinite" in errors[0].lower()

    def test_validate_boundary_zscore_exactly_5(self):
        """validate() allows z-scores exactly at +/- 5 (boundary case)."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, 2.0, 3.0],
                "zscore": [5.0, -5.0, 0.0],  # Exactly at boundary
                "percentile": [0.9, 0.1, 0.5],
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        errors = result.validate()

        # Should NOT trigger extreme z-score error (5.0 is allowed, >5 is not)
        zscore_errors = [e for e in errors if "5 sigma" in e.lower()]
        assert len(zscore_errors) == 0

    def test_validate_boundary_zscore_just_over_5(self):
        """validate() catches z-scores just slightly over +/- 5."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, 2.0, 3.0],
                "zscore": [5.001, -5.001, 0.0],  # Just over boundary
                "percentile": [0.9, 0.1, 0.5],
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        errors = result.validate()

        assert len(errors) >= 1
        assert "5 sigma" in errors[0].lower()

    def test_validate_empty_dataframe(self):
        """validate() returns no errors for empty (but valid schema) DataFrame."""
        df = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "raw_value": pl.Float64,
                "zscore": pl.Float64,
                "percentile": pl.Float64,
            }
        )
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp": "v1.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        errors = result.validate()

        assert len(errors) == 0


# =============================================================================
# MomentumFactor Tests - Additional Coverage
# =============================================================================


class TestMomentumFactorEdgeCases:
    """Edge case tests for MomentumFactor."""

    def test_momentum_with_no_data_in_window(self):
        """Momentum returns empty result when no data in lookback window."""
        factor = MomentumFactor()

        # Prices far in the past (won't be in lookback window)
        old_prices = pl.DataFrame(
            {
                "date": [date(2020, 1, 1), date(2020, 1, 2)],
                "permno": [10001, 10001],
                "ret": [0.01, 0.02],
                "prc": [100.0, 102.0],
                "vol": [1000, 1000],
                "shrout": [1000, 1000],
            }
        )

        result = factor.compute(old_prices, None, date(2023, 6, 15))

        # Should return empty result
        assert result.height == 0

    def test_momentum_with_insufficient_observations(self):
        """Momentum excludes stocks with < 120 observations."""
        factor = MomentumFactor()

        # Only 50 days of data (below 120 threshold)
        sparse_prices = []
        for i in range(50):
            sparse_prices.append(
                {
                    "date": date(2022, 12, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": 0.001 * i,
                    "prc": 100.0 + i,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(sparse_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should exclude this stock
        assert result.height == 0

    def test_momentum_with_negative_returns(self):
        """Momentum correctly handles negative cumulative returns."""
        factor = MomentumFactor()

        # Generate data with consistent negative returns
        negative_prices = []
        for i in range(250):  # Enough observations
            negative_prices.append(
                {
                    "date": date(2022, 6, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": -0.001,  # Consistent negative returns
                    "prc": 100.0 - i * 0.1,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(negative_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should have result with negative momentum
        assert result.height > 0
        assert result["factor_value"][0] < 0

    def test_momentum_with_exact_120_observations(self):
        """Momentum includes stocks with exactly 120 observations (boundary)."""
        factor = MomentumFactor()

        # Exactly 120 days of data
        exact_prices = []
        for i in range(120):
            exact_prices.append(
                {
                    "date": date(2022, 12, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": 0.001,
                    "prc": 100.0 + i * 0.1,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(exact_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should include this stock (>= 120 threshold)
        assert result.height > 0


# =============================================================================
# BookToMarketFactor Tests - Additional Coverage
# =============================================================================


class TestBookToMarketFactorEdgeCases:
    """Edge case tests for BookToMarketFactor."""

    def test_book_to_market_with_zero_market_cap(self):
        """BookToMarket excludes stocks with zero market cap."""
        factor = BookToMarketFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [0.0],  # Zero price
                "shrout": [1000],
                "ret": [0.0],
                "vol": [0],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [1000.0],
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should be empty (zero market cap filtered)
        assert result.height == 0

    def test_book_to_market_with_negative_book_value(self):
        """BookToMarket excludes stocks with negative book value."""
        factor = BookToMarketFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [-1000.0],  # Negative equity
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should be empty (negative book value filtered)
        assert result.height == 0

    def test_book_to_market_with_zero_book_value(self):
        """BookToMarket excludes stocks with zero book value."""
        factor = BookToMarketFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [0.0],  # Zero equity
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should be empty (zero book value filtered)
        assert result.height == 0

    def test_book_to_market_with_negative_price(self):
        """BookToMarket handles negative prices (bid/ask midpoint) using abs()."""
        factor = BookToMarketFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [-100.0],  # Negative price (bid/ask midpoint indicator)
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [1000.0],
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should compute successfully using abs(price)
        assert result.height > 0
        assert result["factor_value"][0] > 0

    def test_book_to_market_respects_90_day_lag_boundary(self):
        """BookToMarket applies exactly 90 days filing lag."""
        factor = BookToMarketFactor()
        as_of_date = date(2023, 6, 15)
        filing_cutoff = as_of_date - timedelta(days=90)

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        # Fundamentals exactly at 90-day boundary (should be included)
        fundamentals_at_boundary = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [filing_cutoff],
                "ceq": [1000.0],
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals_at_boundary, as_of_date)

        # Should include data at boundary
        assert result.height > 0


# =============================================================================
# ROEFactor Tests - Additional Coverage
# =============================================================================


class TestROEFactorEdgeCases:
    """Edge case tests for ROEFactor."""

    def test_roe_with_negative_net_income(self):
        """ROE correctly handles negative net income (loss)."""
        factor = ROEFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [1000.0],
                "ni": [-100.0],  # Loss
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should compute successfully with negative ROE
        assert result.height > 0
        assert result["factor_value"][0] < 0

    def test_roe_with_zero_equity(self):
        """ROE excludes stocks with zero common equity."""
        factor = ROEFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [0.0],  # Zero equity
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should be empty (zero equity filtered)
        assert result.height == 0

    def test_roe_with_negative_equity(self):
        """ROE excludes stocks with negative common equity."""
        factor = ROEFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [date(2023, 1, 1)],
                "ceq": [-1000.0],  # Negative equity
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, fundamentals, date(2023, 6, 15))

        # Should be empty (negative equity filtered)
        assert result.height == 0

    def test_roe_respects_90_day_filing_lag(self):
        """ROE applies 90-day filing lag for point-in-time correctness."""
        factor = ROEFactor()
        as_of_date = date(2023, 6, 15)

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [100.0],
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        # Recent fundamentals (within 90 days) should be excluded
        recent_fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [as_of_date - timedelta(days=30)],
                "ceq": [1000.0],
                "ni": [100.0],
            }
        )

        result = factor.compute(prices, recent_fundamentals, as_of_date)

        # Should be empty (filing lag not met)
        assert result.height == 0


# =============================================================================
# SizeFactor Tests - Additional Coverage
# =============================================================================


class TestSizeFactorEdgeCases:
    """Edge case tests for SizeFactor."""

    def test_size_with_zero_market_cap(self):
        """Size excludes stocks with zero market cap."""
        factor = SizeFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [0.0],  # Zero price
                "shrout": [1000],
                "ret": [0.0],
                "vol": [0],
            }
        )

        result = factor.compute(prices, None, date(2023, 6, 15))

        # Should be empty (zero market cap filtered)
        assert result.height == 0

    def test_size_with_negative_market_cap(self):
        """Size handles negative prices (bid/ask midpoint) using abs()."""
        factor = SizeFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [-100.0],  # Negative price
                "shrout": [1000],
                "ret": [0.0],
                "vol": [1000],
            }
        )

        result = factor.compute(prices, None, date(2023, 6, 15))

        # Should compute successfully using abs(price)
        assert result.height > 0
        assert result["factor_value"][0] > 0

    def test_size_with_very_small_market_cap(self):
        """Size handles very small market caps (penny stocks)."""
        factor = SizeFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [0.01],  # Penny stock
                "shrout": [100],  # Small float
                "ret": [0.0],
                "vol": [1000],
            }
        )

        result = factor.compute(prices, None, date(2023, 6, 15))

        # Should compute successfully
        assert result.height > 0
        # Market cap = 0.01 * 100 * 1000 = 1000, log(1000) ≈ 6.9, which is positive
        # So we just check it's a reasonable value (small but still positive due to the 1000 multiplier)
        assert result["factor_value"][0] > 0

    def test_size_with_very_large_market_cap(self):
        """Size handles very large market caps (mega-caps)."""
        factor = SizeFactor()

        prices = pl.DataFrame(
            {
                "date": [date(2023, 6, 1)],
                "permno": [10001],
                "prc": [1000.0],
                "shrout": [10_000_000],  # 10B shares outstanding
                "ret": [0.0],
                "vol": [1000000],
            }
        )

        result = factor.compute(prices, None, date(2023, 6, 15))

        # Should compute successfully
        assert result.height > 0
        # Log of large market cap should be large
        assert result["factor_value"][0] > 20  # Log of trillions


# =============================================================================
# RealizedVolFactor Tests - Additional Coverage
# =============================================================================


class TestRealizedVolFactorEdgeCases:
    """Edge case tests for RealizedVolFactor."""

    def test_realized_vol_with_insufficient_observations(self):
        """Realized vol excludes stocks with < 40 observations."""
        factor = RealizedVolFactor()

        # Only 30 days of data (below 40 threshold)
        sparse_prices = []
        for i in range(30):
            sparse_prices.append(
                {
                    "date": date(2023, 5, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": 0.001 * i,
                    "prc": 100.0 + i,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(sparse_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should exclude this stock
        assert result.height == 0

    def test_realized_vol_with_exact_40_observations(self):
        """Realized vol includes stocks with exactly 40 observations (boundary)."""
        factor = RealizedVolFactor()

        # Exactly 40 days of data
        exact_prices = []
        for i in range(40):
            exact_prices.append(
                {
                    "date": date(2023, 5, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": 0.001,
                    "prc": 100.0 + i * 0.1,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(exact_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should include this stock (>= 40 threshold)
        assert result.height > 0

    def test_realized_vol_with_zero_volatility(self):
        """Realized vol handles stocks with zero volatility (constant returns)."""
        factor = RealizedVolFactor()

        # All returns are identical (zero variance)
        constant_prices = []
        for i in range(60):
            constant_prices.append(
                {
                    "date": date(2023, 4, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": 0.001,  # Constant return
                    "prc": 100.0,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(constant_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should compute successfully (volatility = 0 or very small)
        assert result.height > 0
        # Volatility should be very close to zero
        assert result["factor_value"][0] < 0.01

    def test_realized_vol_with_high_volatility(self):
        """Realized vol correctly computes high volatility."""
        factor = RealizedVolFactor()

        # Large random returns
        np.random.seed(42)
        volatile_prices = []
        for i in range(60):
            volatile_prices.append(
                {
                    "date": date(2023, 4, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": np.random.normal(0, 0.05),  # High volatility
                    "prc": 100.0,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(volatile_prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should compute successfully with high annualized vol
        assert result.height > 0
        # Annualized vol should be significant (> 20%)
        assert result["factor_value"][0] > 0.2

    def test_realized_vol_annualization_factor(self):
        """Realized vol applies correct annualization (sqrt(252))."""
        factor = RealizedVolFactor()

        # Generate data with known daily volatility
        known_vol = 0.02  # 2% daily std dev
        prices = []
        for i in range(60):
            prices.append(
                {
                    "date": date(2023, 4, 1) + timedelta(days=i),
                    "permno": 10001,
                    "ret": np.random.normal(0, known_vol),
                    "prc": 100.0,
                    "vol": 1000,
                    "shrout": 1000,
                }
            )
        df = pl.DataFrame(prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Annualized vol should be roughly daily_vol * sqrt(252) ≈ 0.02 * 15.87 ≈ 0.32
        assert result.height > 0
        annualized_vol = result["factor_value"][0]
        # Allow some tolerance due to randomness
        assert 0.1 < annualized_vol < 0.6


# =============================================================================
# FactorDefinition Protocol Tests
# =============================================================================


class TestFactorDefinitionProtocol:
    """Tests for FactorDefinition protocol conformance."""

    @pytest.mark.parametrize(
        ("factor_name", "factor_cls"),
        [
            ("momentum_12_1", MomentumFactor),
            ("book_to_market", BookToMarketFactor),
            ("roe", ROEFactor),
            ("log_market_cap", SizeFactor),
            ("realized_vol", RealizedVolFactor),
        ],
    )
    def test_factor_has_all_protocol_properties(self, factor_name: str, factor_cls: type):
        """All factors implement required protocol properties."""
        factor = factor_cls()

        # Check all protocol properties
        assert hasattr(factor, "name")
        assert hasattr(factor, "category")
        assert hasattr(factor, "description")
        assert hasattr(factor, "requires_fundamentals")
        assert hasattr(factor, "compute")

        # Check properties return correct types
        assert isinstance(factor.name, str)
        assert isinstance(factor.category, str)
        assert isinstance(factor.description, str)
        assert isinstance(factor.requires_fundamentals, bool)

        # Check name matches expected
        assert factor.name == factor_name

    @pytest.mark.parametrize(
        ("factor_cls", "expected_category"),
        [
            (MomentumFactor, "momentum"),
            (BookToMarketFactor, "value"),
            (ROEFactor, "quality"),
            (SizeFactor, "size"),
            (RealizedVolFactor, "low_vol"),
        ],
    )
    def test_factor_has_correct_category(self, factor_cls: type, expected_category: str):
        """All factors have correct category classification."""
        factor = factor_cls()
        assert factor.category == expected_category

    @pytest.mark.parametrize(
        ("factor_cls", "requires_fundamentals"),
        [
            (MomentumFactor, False),
            (BookToMarketFactor, True),
            (ROEFactor, True),
            (SizeFactor, False),
            (RealizedVolFactor, False),
        ],
    )
    def test_factor_fundamentals_requirement(self, factor_cls: type, requires_fundamentals: bool):
        """Factors correctly declare fundamental data requirements."""
        factor = factor_cls()
        assert factor.requires_fundamentals == requires_fundamentals

    def test_factor_description_is_informative(self):
        """All factors have non-empty, informative descriptions."""
        for _name, factor_cls in CANONICAL_FACTORS.items():
            factor = factor_cls()
            desc = factor.description
            assert len(desc) > 10  # At least somewhat descriptive
            # Check first character is uppercase letter or digit (ROE starts with digit in "12-month")
            assert desc[0].isupper() or desc[0].isdigit()


# =============================================================================
# CANONICAL_FACTORS Registry Tests
# =============================================================================


class TestCanonicalFactorsRegistryExtended:
    """Extended tests for CANONICAL_FACTORS registry."""

    def test_registry_keys_match_factor_names(self):
        """Registry keys match the factor.name property."""
        for key, factor_cls in CANONICAL_FACTORS.items():
            factor = factor_cls()
            assert key == factor.name

    def test_registry_contains_only_classes(self):
        """Registry values are classes, not instances."""
        for _name, factor_cls in CANONICAL_FACTORS.items():
            assert isinstance(factor_cls, type)

    def test_all_registered_factors_instantiable(self):
        """All registered factors can be instantiated without args."""
        for _name, factor_cls in CANONICAL_FACTORS.items():
            try:
                factor = factor_cls()
                assert factor is not None
            except Exception as e:
                pytest.fail(f"Failed to instantiate {factor_cls.__name__}: {e}")

    def test_registry_has_expected_count(self):
        """Registry contains exactly 5 canonical factors."""
        assert len(CANONICAL_FACTORS) == 5

    def test_registry_covers_all_categories(self):
        """Registry includes at least one factor from each category."""
        categories = {factor_cls().category for factor_cls in CANONICAL_FACTORS.values()}
        expected_categories = {"momentum", "value", "quality", "size", "low_vol"}
        assert categories == expected_categories


# =============================================================================
# Integration Tests - Multiple Stocks
# =============================================================================


class TestFactorComputationMultipleStocks:
    """Integration tests with multiple stocks."""

    def test_momentum_handles_multiple_stocks(self):
        """Momentum computes correctly for multiple stocks."""
        factor = MomentumFactor()

        # Generate data for 3 stocks
        prices = []
        for permno in [10001, 10002, 10003]:
            for i in range(250):
                prices.append(
                    {
                        "date": date(2022, 6, 1) + timedelta(days=i),
                        "permno": permno,
                        "ret": 0.001 * (permno - 10000),  # Different returns per stock
                        "prc": 100.0 + i * 0.1,
                        "vol": 1000,
                        "shrout": 1000,
                    }
                )
        df = pl.DataFrame(prices)

        result = factor.compute(df, None, date(2023, 6, 15))

        # Should have results for all 3 stocks
        assert result.height == 3
        # Different stocks should have different factor values
        assert len(result["factor_value"].unique()) == 3

    def test_factors_return_consistent_schema(self, mock_prices: pl.DataFrame):
        """All factors return DataFrames with consistent schema."""
        expected_columns = {"permno", "factor_value"}

        for _name, factor_cls in CANONICAL_FACTORS.items():
            factor = factor_cls()

            # Skip factors requiring fundamentals for this test
            if factor.requires_fundamentals:
                continue

            result = factor.compute(mock_prices, None, date(2023, 6, 15))

            # Check schema
            assert set(result.columns) == expected_columns
            # Check types
            assert result.schema["permno"] == pl.Int64
            assert result.schema["factor_value"] == pl.Float64
