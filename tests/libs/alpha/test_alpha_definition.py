"""Tests for alpha signal definition framework."""

from datetime import date

import polars as pl
import pytest

from libs.alpha.alpha_definition import (
    AlphaDefinition,
    AlphaResult,
    BaseAlpha,
)
from libs.alpha.exceptions import AlphaValidationError


class TestAlphaDefinition:
    """Tests for AlphaDefinition Protocol."""

    def test_protocol_conformance(self):
        """Test that BaseAlpha subclass satisfies Protocol."""

        class SimpleAlpha(BaseAlpha):
            @property
            def name(self) -> str:
                return "simple_alpha"

            @property
            def category(self) -> str:
                return "test"

            def _compute_raw(
                self, prices: pl.DataFrame, fundamentals, as_of_date
            ) -> pl.DataFrame:
                return pl.DataFrame({
                    "permno": [1, 2, 3],
                    "raw_signal": [1.0, 2.0, 3.0],
                })

        alpha = SimpleAlpha()
        assert isinstance(alpha, AlphaDefinition)

    def test_protocol_properties(self):
        """Test Protocol property requirements."""

        class TestAlpha(BaseAlpha):
            @property
            def name(self) -> str:
                return "test_alpha_v1"

            @property
            def category(self) -> str:
                return "momentum"

            def _compute_raw(self, prices, fundamentals, as_of_date):
                return pl.DataFrame({"permno": [], "raw_signal": []})

        alpha = TestAlpha()
        assert alpha.name == "test_alpha_v1"
        assert alpha.category == "momentum"
        assert alpha.universe_filter == "all"


class TestAlphaResult:
    """Tests for AlphaResult dataclass."""

    def test_basic_creation(self):
        """Test creating AlphaResult."""
        signals = pl.DataFrame({
            "permno": [1, 2, 3],
            "date": [date(2024, 1, 1)] * 3,
            "signal": [0.5, -0.3, 0.1],
        })

        result = AlphaResult(
            alpha_name="test_alpha",
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={"crsp": "v1.0.0"},
        )

        assert result.alpha_name == "test_alpha"
        assert result.as_of_date == date(2024, 1, 1)
        assert result.n_stocks == 3
        assert result.coverage == 1.0

    def test_coverage_with_nulls(self):
        """Test coverage calculation with null signals."""
        signals = pl.DataFrame({
            "permno": [1, 2, 3, 4, 5],
            "date": [date(2024, 1, 1)] * 5,
            "signal": [0.5, None, 0.1, None, 0.3],
        })

        result = AlphaResult(
            alpha_name="test",
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={},
        )

        assert result.n_stocks == 5
        assert result.coverage == pytest.approx(0.6)  # 3 valid out of 5

    def test_reproducibility_hash(self):
        """Test reproducibility hash is deterministic."""
        signals = pl.DataFrame({
            "permno": [1],
            "date": [date(2024, 1, 1)],
            "signal": [0.0],
        })

        result1 = AlphaResult(
            alpha_name="alpha_a",
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={"crsp": "v1.0.0", "compustat": "v2.0.0"},
        )

        result2 = AlphaResult(
            alpha_name="alpha_a",
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={"crsp": "v1.0.0", "compustat": "v2.0.0"},
        )

        # Same inputs should produce same hash
        assert result1.reproducibility_hash == result2.reproducibility_hash

    def test_different_inputs_different_hash(self):
        """Test different inputs produce different hash."""
        signals = pl.DataFrame({
            "permno": [1],
            "date": [date(2024, 1, 1)],
            "signal": [0.0],
        })

        result1 = AlphaResult(
            alpha_name="alpha_a",
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={"crsp": "v1.0.0"},
        )

        result2 = AlphaResult(
            alpha_name="alpha_b",  # Different name
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={"crsp": "v1.0.0"},
        )

        assert result1.reproducibility_hash != result2.reproducibility_hash

    def test_empty_signals(self):
        """Test AlphaResult with empty signals."""
        signals = pl.DataFrame(
            schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
        )

        result = AlphaResult(
            alpha_name="empty",
            as_of_date=date(2024, 1, 1),
            signals=signals,
            dataset_version_ids={},
        )

        assert result.n_stocks == 0
        assert result.coverage == 0.0


class TestBaseAlpha:
    """Tests for BaseAlpha implementation."""

    @pytest.fixture
    def sample_prices(self):
        """Create sample price data."""
        dates = [date(2024, 1, i) for i in range(1, 11)] * 5
        permnos = [i for i in range(1, 6) for _ in range(10)]
        return pl.DataFrame({
            "permno": permnos,
            "date": dates,
            "ret": [0.01 * (p + d % 10) for p, d in zip(permnos, range(len(dates)))],
            "prc": [100.0] * 50,
            "shrout": [1000.0] * 50,
        })

    @pytest.fixture
    def simple_alpha(self):
        """Create simple test alpha."""

        class SimpleAlpha(BaseAlpha):
            @property
            def name(self) -> str:
                return "simple"

            @property
            def category(self) -> str:
                return "test"

            def _compute_raw(self, prices, fundamentals, as_of_date):
                # Use permno as raw signal
                return prices.filter(pl.col("date") == as_of_date).select([
                    pl.col("permno"),
                    pl.col("permno").cast(pl.Float64).alias("raw_signal"),
                ])

        return SimpleAlpha()

    def test_compute_returns_correct_schema(self, simple_alpha, sample_prices):
        """Test compute returns DataFrame with correct columns."""
        result = simple_alpha.compute(sample_prices, None, date(2024, 1, 5))

        assert set(result.columns) == {"permno", "date", "signal"}
        assert result.schema["permno"] == pl.Int64
        assert result.schema["date"] == pl.Date
        assert result.schema["signal"] == pl.Float64

    def test_zscore_normalization(self, simple_alpha, sample_prices):
        """Test signals are z-score normalized."""
        result = simple_alpha.compute(sample_prices, None, date(2024, 1, 5))

        # Z-scores should have mean ~0 and std ~1
        mean = result.select(pl.col("signal").mean()).item()
        std = result.select(pl.col("signal").std()).item()

        assert abs(mean) < 1e-10
        assert abs(std - 1.0) < 0.01

    def test_winsorization(self):
        """Test extreme values are winsorized."""

        class OutlierAlpha(BaseAlpha):
            @property
            def name(self) -> str:
                return "outlier"

            @property
            def category(self) -> str:
                return "test"

            def _compute_raw(self, prices, fundamentals, as_of_date):
                # Create data with outliers
                return pl.DataFrame({
                    "permno": list(range(100)),
                    "raw_signal": [1.0] * 98 + [100.0, -100.0],  # Extreme outliers
                })

        alpha = OutlierAlpha(winsorize_pct=0.02)
        prices = pl.DataFrame({
            "permno": list(range(100)),
            "date": [date(2024, 1, 1)] * 100,
            "ret": [0.01] * 100,
        })

        result = alpha.compute(prices, None, date(2024, 1, 1))

        # After winsorization and z-score, max should be limited
        max_abs = result.select(pl.col("signal").abs().max()).item()
        assert max_abs < 5.0  # Reasonable z-score range

    def test_empty_prices(self, simple_alpha):
        """Test handling of empty price data."""
        empty_prices = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "ret": pl.Float64,
                "prc": pl.Float64,
                "shrout": pl.Float64,
            }
        )

        result = simple_alpha.compute(empty_prices, None, date(2024, 1, 1))
        assert result.height == 0

    def test_inf_in_raw_signal_returns_zeros(self):
        """Test that inf in raw signal results in zero signals (std=nan handling)."""

        class InfiniteAlpha(BaseAlpha):
            def __init__(self):
                super().__init__(winsorize_pct=0)

            @property
            def name(self) -> str:
                return "infinite"

            @property
            def category(self) -> str:
                return "test"

            def _compute_raw(self, prices, fundamentals, as_of_date):
                # Inf in raw signal causes std to be nan
                return pl.DataFrame({
                    "permno": list(range(50)),
                    "raw_signal": [1.0] * 49 + [float("inf")],
                })

        alpha = InfiniteAlpha()
        prices = pl.DataFrame({
            "permno": list(range(50)),
            "date": [date(2024, 1, 1)] * 50,
            "ret": [0.01] * 50,
        })

        # When std is nan (due to inf), code returns zeros instead of raising
        result = alpha.compute(prices, None, date(2024, 1, 1))
        assert result.height == 50
        # All signals should be 0 due to nan std handling
        assert result.select(pl.col("signal").sum()).item() == 0.0

    def test_universe_filter_all(self):
        """Test 'all' universe filter returns all stocks."""
        df = pl.DataFrame({
            "permno": list(range(100)),
            "market_cap": list(range(100)),
        })

        filtered = BaseAlpha.filter_universe(df, "all")
        assert filtered.height == 100

    def test_universe_filter_large_cap(self):
        """Test large_cap filter returns top quintile."""
        df = pl.DataFrame({
            "permno": list(range(100)),
            "market_cap": list(range(100)),
        })

        filtered = BaseAlpha.filter_universe(df, "large_cap")
        # Top 20% of 100 stocks = 20 stocks
        assert filtered.height == 20

        # Should be highest market caps
        min_cap = filtered.select(pl.col("market_cap").min()).item()
        assert min_cap >= 80

    def test_universe_filter_small_cap(self):
        """Test small_cap filter returns bottom quintile."""
        df = pl.DataFrame({
            "permno": list(range(100)),
            "market_cap": list(range(100)),
        })

        filtered = BaseAlpha.filter_universe(df, "small_cap")
        # Bottom 20% of 100 stocks = 20 stocks
        assert filtered.height == 20

        # Should be lowest market caps
        max_cap = filtered.select(pl.col("market_cap").max()).item()
        assert max_cap < 20

    def test_universe_filter_mid_cap(self):
        """Test mid_cap filter returns middle quintiles."""
        df = pl.DataFrame({
            "permno": list(range(100)),
            "market_cap": list(range(100)),
        })

        filtered = BaseAlpha.filter_universe(df, "mid_cap")
        # Middle 60% of 100 stocks = 60 stocks
        assert filtered.height == 60

    def test_universe_filter_missing_column(self):
        """Test universe filter handles missing market_cap column."""
        df = pl.DataFrame({
            "permno": list(range(100)),
            # No market_cap column
        })

        filtered = BaseAlpha.filter_universe(df, "large_cap")
        assert filtered.height == 100  # Returns all when column missing

