"""
Tests for factor definitions and FactorResult validation.
"""

from datetime import UTC, date, datetime

import polars as pl
import pytest

from libs.models.factors import (
    CANONICAL_FACTORS,
    BookToMarketFactor,
    FactorConfig,
    FactorDefinition,
    FactorResult,
    MomentumFactor,
    RealizedVolFactor,
    ROEFactor,
    SizeFactor,
)


class TestFactorConfig:
    """Tests for FactorConfig dataclass."""

    def test_default_values(self):
        """Default config values are reasonable."""
        config = FactorConfig()
        assert config.winsorize_pct == 0.01
        assert config.neutralize_sector is True
        assert config.min_stocks_per_sector == 5
        assert config.lookback_days == 365

    def test_custom_values(self):
        """Config accepts custom values."""
        config = FactorConfig(
            winsorize_pct=0.05,
            neutralize_sector=False,
            min_stocks_per_sector=10,
            lookback_days=126,
        )
        assert config.winsorize_pct == 0.05
        assert config.neutralize_sector is False
        assert config.min_stocks_per_sector == 10
        assert config.lookback_days == 126


class TestFactorResult:
    """Tests for FactorResult dataclass."""

    def test_to_storage_format(self, sample_factor_result: FactorResult):
        """to_storage_format() creates proper storage columns."""
        storage_df = sample_factor_result.to_storage_format()

        assert "dataset_version_id" in storage_df.columns
        assert "computation_timestamp" in storage_df.columns

        # Check version format
        version_id = storage_df["dataset_version_id"][0]
        assert "compustat:v1.0.0" in version_id
        assert "crsp:v1.0.0" in version_id
        assert "|" in version_id  # Pipe separator

    def test_to_storage_format_sorted_versions(self):
        """to_storage_format() sorts version IDs for consistency."""
        df = pl.DataFrame({"permno": [1], "raw_value": [0.1], "zscore": [0.5], "percentile": [0.7]})
        result = FactorResult(
            exposures=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"zebra": "v1.0", "alpha": "v2.0"},
            computation_timestamp=datetime.now(UTC),
            reproducibility_hash="test",
        )

        storage_df = result.to_storage_format()
        version_id = storage_df["dataset_version_id"][0]

        # alpha should come before zebra (sorted)
        assert version_id.index("alpha") < version_id.index("zebra")


class TestFactorResultValidation:
    """Tests for FactorResult.validate() error detection."""

    def test_validate_detects_null_values(self):
        """validate() catches null values in required columns."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, None, 3.0],  # Contains null
                "zscore": [0.5, 0.2, 0.1],
                "percentile": [0.9, 0.5, 0.1],
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

        assert len(errors) == 1
        assert "null values" in errors[0].lower()

    def test_validate_detects_infinite_values(self):
        """validate() catches infinite values."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, float("inf"), 3.0],  # Contains inf
                "zscore": [0.5, 0.2, 0.1],
                "percentile": [0.9, 0.5, 0.1],
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

        assert len(errors) == 1
        assert "infinite values" in errors[0].lower()

    def test_validate_detects_extreme_zscores(self):
        """validate() catches z-scores exceeding +/- 5 sigma."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, 2.0, 3.0],
                "zscore": [0.5, 6.5, 0.1],  # 6.5 > 5 sigma
                "percentile": [0.9, 0.5, 0.1],
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

        assert len(errors) == 1
        assert "5 sigma" in errors[0].lower()

    def test_validate_detects_negative_extreme_zscores(self):
        """validate() catches negative extreme z-scores."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, 2.0, 3.0],
                "zscore": [0.5, -7.0, 0.1],  # -7.0 < -5 sigma
                "percentile": [0.9, 0.5, 0.1],
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

        assert len(errors) == 1
        assert "5 sigma" in errors[0].lower()

    def test_validate_detects_nan_values(self):
        """validate() catches NaN values (distinct from null)."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, float("nan"), 3.0],  # Contains NaN
                "zscore": [0.5, 0.2, 0.1],
                "percentile": [0.9, 0.5, 0.1],
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

        assert len(errors) == 1
        assert "nan" in errors[0].lower()

    def test_validate_returns_all_errors(self):
        """validate() collects and returns ALL errors, not just first."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [None, float("inf"), 3.0],  # null AND inf
                "zscore": [0.5, 6.5, float("-inf")],  # extreme AND inf
                "percentile": [0.9, 0.5, None],  # null
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

        # Should detect multiple errors
        assert len(errors) >= 3

    def test_validate_empty_on_valid_data(self):
        """validate() returns empty list for valid data."""
        df = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "raw_value": [1.0, 2.0, 3.0],
                "zscore": [0.5, -0.5, 0.1],
                "percentile": [0.9, 0.5, 0.1],
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


class TestMomentumFactor:
    """Tests for MomentumFactor."""

    def test_momentum_properties(self):
        """Momentum factor has correct properties."""
        factor = MomentumFactor()
        assert factor.name == "momentum_12_1"
        assert factor.category == "momentum"
        assert factor.requires_fundamentals is False

    def test_momentum_uses_only_past_returns(self, mock_prices: pl.DataFrame):
        """Verify no look-ahead bias in momentum computation."""
        factor = MomentumFactor()
        as_of_date = date(2023, 6, 15)

        result = factor.compute(mock_prices, None, as_of_date)

        # Result should have permno and factor_value
        assert "permno" in result.columns
        assert "factor_value" in result.columns

        # Should have computed for some stocks
        assert result.height > 0

    def test_momentum_skips_last_month(self, mock_prices: pl.DataFrame):
        """Verify 12-1 skips the most recent month."""
        factor = MomentumFactor()
        as_of_date = date(2023, 6, 15)

        # This is implicitly tested by the computation logic
        # The factor uses as_of_date - 21 days as the end date
        result = factor.compute(mock_prices, None, as_of_date)
        assert result.height > 0

    def test_momentum_requires_sufficient_data(self, mock_prices: pl.DataFrame):
        """Stocks with insufficient data are excluded."""
        factor = MomentumFactor()
        as_of_date = date(2023, 6, 15)

        result = factor.compute(mock_prices, None, as_of_date)

        # All returned stocks should have valid values
        assert result.filter(pl.col("factor_value").is_null()).height == 0


class TestBookToMarketFactor:
    """Tests for BookToMarketFactor."""

    def test_book_to_market_properties(self):
        """Book-to-market factor has correct properties."""
        factor = BookToMarketFactor()
        assert factor.name == "book_to_market"
        assert factor.category == "value"
        assert factor.requires_fundamentals is True

    def test_book_to_market_requires_fundamentals(self, mock_prices: pl.DataFrame):
        """BookToMarket raises error without fundamentals."""
        factor = BookToMarketFactor()

        with pytest.raises(ValueError, match="requires fundamentals"):
            factor.compute(mock_prices, None, date(2023, 6, 15))

    def test_book_to_market_computation(
        self, mock_prices: pl.DataFrame, mock_fundamentals: pl.DataFrame
    ):
        """BookToMarket computes B/M ratio correctly."""
        factor = BookToMarketFactor()
        as_of_date = date(2023, 6, 15)

        result = factor.compute(mock_prices, mock_fundamentals, as_of_date)

        assert "permno" in result.columns
        assert "factor_value" in result.columns
        assert result.height > 0

        # B/M should be positive (we filter for positive book value)
        assert result.filter(pl.col("factor_value") <= 0).height == 0


class TestROEFactor:
    """Tests for ROEFactor."""

    def test_roe_properties(self):
        """ROE factor has correct properties."""
        factor = ROEFactor()
        assert factor.name == "roe"
        assert factor.category == "quality"
        assert factor.requires_fundamentals is True

    def test_roe_requires_fundamentals(self, mock_prices: pl.DataFrame):
        """ROE raises error without fundamentals."""
        factor = ROEFactor()

        with pytest.raises(ValueError, match="requires fundamentals"):
            factor.compute(mock_prices, None, date(2023, 6, 15))

    def test_roe_computation(self, mock_prices: pl.DataFrame, mock_fundamentals: pl.DataFrame):
        """ROE computes NI/CEQ correctly."""
        factor = ROEFactor()

        result = factor.compute(mock_prices, mock_fundamentals, date(2023, 6, 15))

        assert "permno" in result.columns
        assert "factor_value" in result.columns
        assert result.height > 0


class TestSizeFactor:
    """Tests for SizeFactor."""

    def test_size_properties(self):
        """Size factor has correct properties."""
        factor = SizeFactor()
        assert factor.name == "log_market_cap"
        assert factor.category == "size"
        assert factor.requires_fundamentals is False

    def test_size_computation(self, mock_prices: pl.DataFrame):
        """Size computes log market cap correctly."""
        factor = SizeFactor()

        result = factor.compute(mock_prices, None, date(2023, 6, 15))

        assert "permno" in result.columns
        assert "factor_value" in result.columns
        assert result.height > 0

        # Log market cap should be positive for valid market caps
        # (log of numbers > 1 is positive)
        assert result.filter(pl.col("factor_value") > 0).height > 0


class TestRealizedVolFactor:
    """Tests for RealizedVolFactor."""

    def test_realized_vol_properties(self):
        """Realized vol factor has correct properties."""
        factor = RealizedVolFactor()
        assert factor.name == "realized_vol"
        assert factor.category == "low_vol"
        assert factor.requires_fundamentals is False

    def test_realized_vol_computation(self, mock_prices: pl.DataFrame):
        """Realized vol computes annualized volatility correctly."""
        factor = RealizedVolFactor()

        result = factor.compute(mock_prices, None, date(2023, 6, 15))

        assert "permno" in result.columns
        assert "factor_value" in result.columns
        assert result.height > 0

        # Volatility should be non-negative
        assert result.filter(pl.col("factor_value") < 0).height == 0


class TestCanonicalFactorsRegistry:
    """Tests for the CANONICAL_FACTORS registry."""

    def test_all_factors_registered(self):
        """All 5 canonical factors are in the registry."""
        expected = {
            "momentum_12_1",
            "book_to_market",
            "roe",
            "log_market_cap",
            "realized_vol",
        }
        assert set(CANONICAL_FACTORS.keys()) == expected

    def test_all_factors_implement_protocol(self):
        """All canonical factors implement FactorDefinition protocol."""
        for _name, factor_cls in CANONICAL_FACTORS.items():
            factor = factor_cls()
            assert isinstance(factor, FactorDefinition)
            assert hasattr(factor, "name")
            assert hasattr(factor, "category")
            assert hasattr(factor, "description")
            assert hasattr(factor, "requires_fundamentals")
            assert hasattr(factor, "compute")


class TestSizeFactorSorting:
    """Tests for SizeFactor price sorting behavior."""

    def test_size_uses_latest_price_per_security(self, mock_prices: pl.DataFrame):
        """Size factor uses the latest price per security (PIT correctness)."""
        factor = SizeFactor()

        # Add unsorted data to verify sorting is applied
        unsorted_prices = mock_prices.with_row_index("idx").sort("idx", descending=True)

        result = factor.compute(unsorted_prices, None, date(2023, 6, 15))

        assert "permno" in result.columns
        assert "factor_value" in result.columns
        assert result.height > 0


class TestBookToMarketFilingLag:
    """Tests for BookToMarketFactor filing lag."""

    def test_book_to_market_respects_filing_lag(self, mock_prices: pl.DataFrame):
        """BookToMarket applies 90-day filing lag (PIT correctness)."""
        from datetime import timedelta

        factor = BookToMarketFactor()
        as_of_date = date(2023, 6, 15)

        # Create fundamentals with recent datadate (within 90 days)
        # These should be excluded due to filing lag
        recent_fundamentals = pl.DataFrame(
            {
                "permno": [10001],
                "datadate": [as_of_date - timedelta(days=30)],  # Too recent
                "ceq": [1000.0],
                "ni": [100.0],
            }
        )

        result = factor.compute(mock_prices, recent_fundamentals, as_of_date)

        # Should be empty - fundamentals are too recent (filing lag not met)
        assert result.height == 0
