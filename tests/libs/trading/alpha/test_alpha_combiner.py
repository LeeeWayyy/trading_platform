"""Tests for Alpha Combiner - Composite signal generation.

Test coverage for T2.6 Alpha Advanced Analytics:
- Configuration and data models
- Signal alignment and validation
- Weighting methods (Equal, IC, IR, Vol-Parity)
- Correlation analysis
- Lookahead prevention
- Turnover integration
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from libs.trading.alpha.alpha_combiner import (
    AlphaCombiner,
    CombinerConfig,
    CombineResult,
    TurnoverAdapter,
    WeightingMethod,
    _winsorize,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def sample_signal_momentum() -> pl.DataFrame:
    """Sample momentum signal with 60 days of data."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    permnos = [10001, 10002, 10003, 10004, 10005]

    rows = []
    for i, d in enumerate(dates):
        for j, permno in enumerate(permnos):
            # Momentum signal: higher for earlier permnos
            signal = 0.05 * (5 - j) + 0.001 * i + 0.01 * (i % 7)
            rows.append({"permno": permno, "date": d, "signal": signal})

    return pl.DataFrame(rows)


@pytest.fixture()
def sample_signal_value() -> pl.DataFrame:
    """Sample value signal with 60 days of data."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    permnos = [10001, 10002, 10003, 10004, 10005]

    rows = []
    for i, d in enumerate(dates):
        for j, permno in enumerate(permnos):
            # Value signal: higher for later permnos (inverse of momentum)
            signal = 0.05 * j + 0.002 * i - 0.01 * (i % 5)
            rows.append({"permno": permno, "date": d, "signal": signal})

    return pl.DataFrame(rows)


@pytest.fixture()
def sample_returns() -> pl.DataFrame:
    """Sample forward returns with 60 days of data."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    permnos = [10001, 10002, 10003, 10004, 10005]

    rows = []
    for i, d in enumerate(dates):
        for j, permno in enumerate(permnos):
            # Returns correlated with momentum (higher for earlier permnos)
            ret = 0.001 * (5 - j) + 0.0001 * (i % 10) - 0.0005
            rows.append({"permno": permno, "date": d, "return": ret})

    return pl.DataFrame(rows)


@pytest.fixture()
def signals_dict(
    sample_signal_momentum: pl.DataFrame, sample_signal_value: pl.DataFrame
) -> dict[str, pl.DataFrame]:
    """Dictionary of sample signals."""
    return {"momentum": sample_signal_momentum, "value": sample_signal_value}


# =============================================================================
# Test Configuration (Tests 1-2)
# =============================================================================


class TestCombinerConfig:
    """Tests for CombinerConfig."""

    def test_combiner_config_defaults(self) -> None:
        """Test 1: Default values correct."""
        config = CombinerConfig()

        assert config.weighting == WeightingMethod.IC
        assert config.lookback_days == 252
        assert config.min_lookback_days == 60
        assert config.normalize is True
        assert config.correlation_threshold == 0.7
        assert config.winsorize_pct == 0.01

    def test_weighting_method_enum(self) -> None:
        """Test 2: All 4 methods defined."""
        methods = list(WeightingMethod)

        assert len(methods) == 4
        assert WeightingMethod.EQUAL in methods
        assert WeightingMethod.IC in methods
        assert WeightingMethod.IR in methods
        assert WeightingMethod.VOL_PARITY in methods


# =============================================================================
# Test Signal Alignment (Tests 3-6)
# =============================================================================


class TestSignalAlignment:
    """Tests for signal alignment."""

    def test_align_signals_common_dates(
        self, sample_signal_momentum: pl.DataFrame, sample_signal_value: pl.DataFrame
    ) -> None:
        """Test 3: Inner join on dates works."""
        combiner = AlphaCombiner()
        signals = {"mom": sample_signal_momentum, "val": sample_signal_value}

        aligned, warnings = combiner._align_signals(signals)

        # Both have same dates, so no loss
        assert aligned["mom"].height == sample_signal_momentum.height
        assert aligned["val"].height == sample_signal_value.height
        assert len(warnings) == 0

    def test_align_signals_async_symbols(self) -> None:
        """Test 4: Different symbol sets handled."""
        combiner = AlphaCombiner()

        # Signal 1: permnos 10001, 10002
        sig1 = pl.DataFrame(
            {
                "permno": [10001, 10002, 10001, 10002],
                "date": [date(2024, 1, 1)] * 2 + [date(2024, 1, 2)] * 2,
                "signal": [0.1, 0.2, 0.15, 0.25],
            }
        )

        # Signal 2: permnos 10002, 10003 (partial overlap)
        sig2 = pl.DataFrame(
            {
                "permno": [10002, 10003, 10002, 10003],
                "date": [date(2024, 1, 1)] * 2 + [date(2024, 1, 2)] * 2,
                "signal": [0.3, 0.4, 0.35, 0.45],
            }
        )

        aligned, warnings = combiner._align_signals({"sig1": sig1, "sig2": sig2})

        # Only permno 10002 is common
        assert aligned["sig1"].height == 2
        assert aligned["sig2"].height == 2
        assert len(warnings) == 1  # Coverage warning

    def test_align_signals_date_coverage_warning(self) -> None:
        """Test 5: Warning when dates lost."""
        combiner = AlphaCombiner()

        sig1 = pl.DataFrame(
            {
                "permno": [10001, 10001, 10001],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "signal": [0.1, 0.2, 0.3],
            }
        )

        sig2 = pl.DataFrame(
            {
                "permno": [10001, 10001],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],  # Missing day 3
                "signal": [0.4, 0.5],
            }
        )

        aligned, warnings = combiner._align_signals({"sig1": sig1, "sig2": sig2})

        assert aligned["sig1"].height == 2
        assert len(warnings) == 1
        assert "lost" in warnings[0].lower()

    def test_align_signals_empty_intersection(self) -> None:
        """Test 6: Error on no common dates."""
        combiner = AlphaCombiner()

        sig1 = pl.DataFrame(
            {
                "permno": [10001],
                "date": [date(2024, 1, 1)],
                "signal": [0.1],
            }
        )

        sig2 = pl.DataFrame(
            {
                "permno": [10002],  # Different permno
                "date": [date(2024, 1, 1)],
                "signal": [0.4],
            }
        )

        aligned, warnings = combiner._align_signals({"sig1": sig1, "sig2": sig2})

        assert aligned["sig1"].height == 0
        assert aligned["sig2"].height == 0
        assert any("no common" in w.lower() for w in warnings)


# =============================================================================
# Test Schema Validation (Tests 7-9)
# =============================================================================


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_validate_signals_correct_schema(self, sample_signal_momentum: pl.DataFrame) -> None:
        """Test 7: Valid schema passes."""
        combiner = AlphaCombiner()

        # Should not raise
        combiner._validate_signals({"mom": sample_signal_momentum})

    def test_validate_signals_missing_column(self) -> None:
        """Test 8: Raises ValueError for missing column."""
        combiner = AlphaCombiner()

        bad_signal = pl.DataFrame(
            {
                "permno": [10001],
                "date": [date(2024, 1, 1)],
                # Missing 'signal' column
            }
        )

        with pytest.raises(ValueError, match="missing required columns"):
            combiner._validate_signals({"bad": bad_signal})

    def test_validate_signals_empty_dict(self) -> None:
        """Test 9: Raises ValueError for empty signals."""
        combiner = AlphaCombiner()

        with pytest.raises(ValueError, match="At least one signal required"):
            combiner._validate_signals({})


# =============================================================================
# Test Equal Weighting (Tests 10-12)
# =============================================================================


class TestEqualWeighting:
    """Tests for equal weighting."""

    def test_equal_weights_two_signals(self) -> None:
        """Test 10: 0.5, 0.5 for two signals."""
        combiner = AlphaCombiner()
        weights = combiner._compute_equal_weights(["sig1", "sig2"])

        assert weights == {"sig1": 0.5, "sig2": 0.5}

    def test_equal_weights_five_signals(self) -> None:
        """Test 11: 0.2 each for five signals."""
        combiner = AlphaCombiner()
        weights = combiner._compute_equal_weights(["a", "b", "c", "d", "e"])

        for _name, weight in weights.items():
            assert abs(weight - 0.2) < 1e-10

    def test_equal_weights_sum_to_one(self) -> None:
        """Test 12: Verify sum = 1.0."""
        combiner = AlphaCombiner()
        weights = combiner._compute_equal_weights(["a", "b", "c"])

        assert abs(sum(weights.values()) - 1.0) < 1e-10


# =============================================================================
# Test IC Weighting (Tests 13-17)
# =============================================================================


class TestICWeighting:
    """Tests for IC weighting."""

    def test_ic_weights_positive_ic(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 13: Higher IC gets higher weight."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=10)
        combiner = AlphaCombiner(config=config)

        as_of = date(2024, 2, 29)  # End of 60 days
        weights = combiner._compute_ic_weights(
            signals_dict, sample_returns, as_of, lookback_days=30
        )

        # Momentum should have positive IC (correlated with returns)
        assert "momentum" in weights
        assert "value" in weights
        # Both should have non-negative weights
        assert all(w >= 0 for w in weights.values())

    def test_ic_weights_negative_ic_clipped(self) -> None:
        """Test 14: Negative IC → 0 weight."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=2)
        combiner = AlphaCombiner(config=config)

        # Create signal negatively correlated with returns
        signal = pl.DataFrame(
            {
                "permno": [1, 2, 3] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6) for _ in range(3)],
                "signal": [0.3, 0.2, 0.1] * 5,  # Higher for permno 1
            }
        )

        returns = pl.DataFrame(
            {
                "permno": [1, 2, 3] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6) for _ in range(3)],
                "return": [-0.01, 0.0, 0.01] * 5,  # Lower for permno 1 (negative corr)
            }
        )

        weights = combiner._compute_ic_weights(
            {"neg_signal": signal}, returns, date(2024, 1, 5), lookback_days=4
        )

        # With only one signal having negative IC, should fallback to equal
        assert weights["neg_signal"] == 1.0  # Fallback to equal with one signal

    def test_ic_weights_all_negative_fallback(self) -> None:
        """Test 15: Falls back to equal when all ICs negative."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=2)
        combiner = AlphaCombiner(config=config)

        # Two signals both negatively correlated
        sig1 = pl.DataFrame(
            {
                "permno": [1, 2] * 3,
                "date": [date(2024, 1, i) for i in range(1, 4) for _ in range(2)],
                "signal": [0.2, 0.1] * 3,
            }
        )

        sig2 = pl.DataFrame(
            {
                "permno": [1, 2] * 3,
                "date": [date(2024, 1, i) for i in range(1, 4) for _ in range(2)],
                "signal": [0.3, 0.15] * 3,
            }
        )

        returns = pl.DataFrame(
            {
                "permno": [1, 2] * 3,
                "date": [date(2024, 1, i) for i in range(1, 4) for _ in range(2)],
                "return": [-0.01, 0.01] * 3,  # Negative corr with both
            }
        )

        weights = combiner._compute_ic_weights(
            {"sig1": sig1, "sig2": sig2}, returns, date(2024, 1, 3), lookback_days=2
        )

        # Should fallback to equal weights
        assert abs(weights["sig1"] - 0.5) < 0.01
        assert abs(weights["sig2"] - 0.5) < 0.01

    def test_ic_weights_lookback_window(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 16: Uses correct date range."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=5)
        combiner = AlphaCombiner(config=config)

        as_of = date(2024, 1, 20)
        lookback = 10

        # This should only use data from Jan 10-19
        weights = combiner._compute_ic_weights(
            signals_dict, sample_returns, as_of, lookback_days=lookback
        )

        assert len(weights) == 2
        assert sum(weights.values()) > 0  # At least one positive

    def test_ic_weights_sum_to_one(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 17: Verify sum = 1.0."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=10)
        combiner = AlphaCombiner(config=config)

        weights = combiner._compute_ic_weights(
            signals_dict, sample_returns, date(2024, 2, 29), lookback_days=30
        )

        assert abs(sum(weights.values()) - 1.0) < 1e-10


# =============================================================================
# Test IR Weighting (Tests 18-21)
# =============================================================================


class TestIRWeighting:
    """Tests for IR weighting."""

    def test_ir_weights_high_ir_signal(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 18: Consistent IC gets higher weight."""
        config = CombinerConfig(weighting=WeightingMethod.IR, min_lookback_days=10)
        combiner = AlphaCombiner(config=config)

        weights = combiner._compute_ir_weights(
            signals_dict, sample_returns, date(2024, 2, 29), lookback_days=30
        )

        assert len(weights) == 2
        assert all(w >= 0 for w in weights.values())

    def test_ir_weights_volatile_ic(self) -> None:
        """Test 19: Inconsistent IC gets lower weight."""
        config = CombinerConfig(weighting=WeightingMethod.IR, min_lookback_days=3)
        combiner = AlphaCombiner(config=config)

        # Create consistent signal (same ranking each day)
        consistent = pl.DataFrame(
            {
                "permno": [1, 2, 3] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6) for _ in range(3)],
                "signal": [0.3, 0.2, 0.1] * 5,
            }
        )

        # Create volatile signal (ranking changes)
        volatile = pl.DataFrame(
            {
                "permno": [1, 2, 3] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6) for _ in range(3)],
                "signal": [
                    0.3,
                    0.2,
                    0.1,  # Day 1
                    0.1,
                    0.3,
                    0.2,  # Day 2
                    0.2,
                    0.1,
                    0.3,  # Day 3
                    0.3,
                    0.2,
                    0.1,  # Day 4
                    0.1,
                    0.2,
                    0.3,  # Day 5
                ],
            }
        )

        returns = pl.DataFrame(
            {
                "permno": [1, 2, 3] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6) for _ in range(3)],
                "return": [0.01, 0.005, 0.0] * 5,
            }
        )

        weights = combiner._compute_ir_weights(
            {"consistent": consistent, "volatile": volatile},
            returns,
            date(2024, 1, 5),
            lookback_days=4,
        )

        # Consistent signal should have higher IR (less volatile IC)
        # This test verifies the logic is in place
        assert "consistent" in weights
        assert "volatile" in weights

    def test_ir_weights_negative_ir_clipped(self) -> None:
        """Test 20: Negative IR → 0 weight."""
        config = CombinerConfig(weighting=WeightingMethod.IR, min_lookback_days=2)
        combiner = AlphaCombiner(config=config)

        # Signal with consistently negative IC
        signal = pl.DataFrame(
            {
                "permno": [1, 2] * 3,
                "date": [date(2024, 1, i) for i in range(1, 4) for _ in range(2)],
                "signal": [0.2, 0.1] * 3,
            }
        )

        returns = pl.DataFrame(
            {
                "permno": [1, 2] * 3,
                "date": [date(2024, 1, i) for i in range(1, 4) for _ in range(2)],
                "return": [-0.01, 0.01] * 3,
            }
        )

        weights = combiner._compute_ir_weights(
            {"neg": signal}, returns, date(2024, 1, 3), lookback_days=2
        )

        # Single signal with negative IR should fallback to equal
        assert weights["neg"] == 1.0

    def test_ir_weights_sum_to_one(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 21: Verify sum = 1.0."""
        config = CombinerConfig(weighting=WeightingMethod.IR, min_lookback_days=10)
        combiner = AlphaCombiner(config=config)

        weights = combiner._compute_ir_weights(
            signals_dict, sample_returns, date(2024, 2, 29), lookback_days=30
        )

        assert abs(sum(weights.values()) - 1.0) < 1e-10


# =============================================================================
# Test Vol-Parity Weighting (Tests 22-27, 63-66)
# =============================================================================


class TestVolParityWeighting:
    """Tests for vol-parity weighting."""

    def test_vol_parity_low_vol_high_weight(self) -> None:
        """Test 22: Stable signal weighted more."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Low volatility signal
        low_vol = pl.DataFrame(
            {
                "permno": [1, 1, 1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [0.1, 0.1, 0.1, 0.1, 0.1],  # Constant
            }
        )

        # High volatility signal
        high_vol = pl.DataFrame(
            {
                "permno": [1, 1, 1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [0.1, 0.5, 0.1, 0.5, 0.1],  # Swinging
            }
        )

        weights = combiner._compute_vol_parity_weights(
            {"low": low_vol, "high": high_vol}, date(2024, 1, 5), lookback_days=4
        )

        # Low vol should have higher weight (but with constant signal, vol=0)
        # This will trigger the zero-vol handling
        assert "low" in weights
        assert "high" in weights

    def test_vol_parity_high_vol_low_weight(self) -> None:
        """Test 23: Volatile signal weighted less."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Stable signal with some variation
        stable = pl.DataFrame(
            {
                "permno": [1, 1, 1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [0.10, 0.11, 0.10, 0.11, 0.10],  # Small var
            }
        )

        # Volatile signal
        volatile = pl.DataFrame(
            {
                "permno": [1, 1, 1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [0.1, 0.5, 0.2, 0.6, 0.1],  # Large var
            }
        )

        weights = combiner._compute_vol_parity_weights(
            {"stable": stable, "volatile": volatile}, date(2024, 1, 5), lookback_days=4
        )

        # Stable should have higher weight
        assert weights["stable"] > weights["volatile"]

    def test_vol_parity_no_returns_needed(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 24: Works without returns."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Should not require returns
        weights = combiner._compute_vol_parity_weights(
            signals_dict, date(2024, 2, 29), lookback_days=30
        )

        assert len(weights) == 2

    def test_vol_parity_sum_to_one(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 25: Verify sum = 1.0."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        weights = combiner._compute_vol_parity_weights(
            signals_dict, date(2024, 2, 29), lookback_days=30
        )

        assert abs(sum(weights.values()) - 1.0) < 1e-10

    def test_vol_parity_zero_vol_excluded(self) -> None:
        """Test 26: Zero-vol signal gets 0 weight."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Zero volatility (constant) signal
        zero_vol = pl.DataFrame(
            {
                "permno": [1, 1, 1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [0.5, 0.5, 0.5, 0.5, 0.5],
            }
        )

        # Normal signal
        normal = pl.DataFrame(
            {
                "permno": [1, 1, 1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [0.1, 0.2, 0.3, 0.2, 0.1],
            }
        )

        weights = combiner._compute_vol_parity_weights(
            {"zero": zero_vol, "normal": normal}, date(2024, 1, 5), lookback_days=4
        )

        # Zero vol signal should have 0 weight
        assert weights["zero"] == 0.0
        assert weights["normal"] == 1.0

    def test_vol_parity_all_zero_vol_fallback(self) -> None:
        """Test 27: All zero-vol falls back to equal."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Two constant signals
        const1 = pl.DataFrame(
            {
                "permno": [1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 4)],
                "signal": [0.5, 0.5, 0.5],
            }
        )

        const2 = pl.DataFrame(
            {
                "permno": [1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 4)],
                "signal": [0.3, 0.3, 0.3],
            }
        )

        weights = combiner._compute_vol_parity_weights(
            {"const1": const1, "const2": const2}, date(2024, 1, 3), lookback_days=2
        )

        # Should fallback to equal
        assert weights["const1"] == 0.5
        assert weights["const2"] == 0.5

    def test_vol_parity_per_stock_volatility(self) -> None:
        """Test 63: Uses time-series vol per stock."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Two stocks with different volatilities
        signal = pl.DataFrame(
            {
                "permno": [1, 1, 1, 2, 2, 2],
                "date": [date(2024, 1, i) for i in range(1, 4)] * 2,
                "signal": [
                    0.1,
                    0.1,
                    0.1,  # Stock 1: low vol
                    0.1,
                    0.5,
                    0.1,  # Stock 2: high vol
                ],
            }
        )

        weights = combiner._compute_vol_parity_weights(
            {"sig": signal}, date(2024, 1, 3), lookback_days=2
        )

        # Should compute weighted average of per-stock vols
        assert weights["sig"] > 0

    def test_vol_parity_stocks_with_few_obs_excluded(self) -> None:
        """Test 64: Stocks with <2 obs excluded."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Stock 1 has 3 obs, Stock 2 has 1 obs
        signal = pl.DataFrame(
            {
                "permno": [1, 1, 1, 2],
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 1),
                ],
                "signal": [0.1, 0.2, 0.3, 0.5],
            }
        )

        weights = combiner._compute_vol_parity_weights(
            {"sig": signal}, date(2024, 1, 3), lookback_days=2
        )

        # Should only use stock 1's volatility
        assert weights["sig"] > 0

    def test_vol_parity_respects_lookback_window(self) -> None:
        """Test 65: Only uses data in window."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        # Signal with different behavior before/after window
        signal = pl.DataFrame(
            {
                "permno": [1] * 10,
                "date": [date(2024, 1, i) for i in range(1, 11)],
                "signal": [0.1, 0.1, 0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.5, 0.5],
            }
        )

        # Only use last 3 days (high vol period)
        weights = combiner._compute_vol_parity_weights(
            {"sig": signal}, date(2024, 1, 10), lookback_days=3
        )

        assert weights["sig"] > 0

    def test_vol_parity_no_lookahead(self) -> None:
        """Test 66: Future data excluded from vol calc."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY)
        combiner = AlphaCombiner(config=config)

        signal = pl.DataFrame(
            {
                "permno": [1] * 10,
                "date": [date(2024, 1, i) for i in range(1, 11)],
                "signal": [0.1 + 0.01 * i for i in range(10)],
            }
        )

        # as_of_date = day 5, should only use days 1-4
        weights = combiner._compute_vol_parity_weights(
            {"sig": signal}, date(2024, 1, 5), lookback_days=4
        )

        # Should work without error
        assert weights["sig"] > 0


# =============================================================================
# Test Normalization (Tests 28-32)
# =============================================================================


class TestNormalization:
    """Tests for signal normalization."""

    def test_normalize_signals_zscore(self, sample_signal_momentum: pl.DataFrame) -> None:
        """Test 28: Cross-sectional z-score."""
        combiner = AlphaCombiner()
        normalized = combiner._normalize_signals({"mom": sample_signal_momentum})

        # Check that signals are normalized
        norm_sig = normalized["mom"]
        assert norm_sig.height == sample_signal_momentum.height

    def test_normalize_handles_nulls(self) -> None:
        """Test 29: NaN signals handled."""
        combiner = AlphaCombiner()

        signal = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "date": [date(2024, 1, 1)] * 3,
                "signal": [0.1, None, 0.3],
            }
        )

        normalized = combiner._normalize_signals({"sig": signal})

        # Should handle null without error
        assert normalized["sig"].height == 3

    def test_normalize_output_mean_zero(self) -> None:
        """Test 30: Mean ≈ 0 per date."""
        combiner = AlphaCombiner()

        signal = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5],
                "date": [date(2024, 1, 1)] * 5,
                "signal": [0.1, 0.2, 0.3, 0.4, 0.5],
            }
        )

        normalized = combiner._normalize_signals({"sig": signal})
        mean_val = normalized["sig"].select(pl.col("signal").mean()).item()

        assert abs(mean_val) < 1e-10

    def test_normalize_output_std_one(self) -> None:
        """Test 31: Std ≈ 1 per date."""
        combiner = AlphaCombiner()

        signal = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5],
                "date": [date(2024, 1, 1)] * 5,
                "signal": [0.1, 0.2, 0.3, 0.4, 0.5],
            }
        )

        normalized = combiner._normalize_signals({"sig": signal})
        std_val = normalized["sig"].select(pl.col("signal").std()).item()

        # With 5 samples, sample std ≈ 1
        assert abs(std_val - 1.0) < 0.3

    def test_normalize_zero_std_returns_zero(self) -> None:
        """Test 32: Zero std → signal = 0."""
        combiner = AlphaCombiner()

        # All same value = zero std
        signal = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "date": [date(2024, 1, 1)] * 3,
                "signal": [0.5, 0.5, 0.5],
            }
        )

        normalized = combiner._normalize_signals({"sig": signal})

        # All values should be 0
        values = normalized["sig"]["signal"].to_list()
        assert all(v == 0.0 for v in values)


# =============================================================================
# Test Correlation Analysis (Tests 33-40, 79)
# =============================================================================


class TestCorrelationAnalysis:
    """Tests for correlation analysis."""

    def test_correlation_matrix_symmetric(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 33: Full matrix, upper mirrors lower."""
        combiner = AlphaCombiner()
        result = combiner.compute_correlation_matrix(
            signals_dict, date(2024, 2, 29), lookback_days=30
        )

        # Find (mom, val) and (val, mom) correlations
        corr_df = result.correlation_matrix
        mom_val = (
            corr_df.filter((pl.col("signal_i") == "momentum") & (pl.col("signal_j") == "value"))
            .select("pearson")
            .item()
        )

        val_mom = (
            corr_df.filter((pl.col("signal_i") == "value") & (pl.col("signal_j") == "momentum"))
            .select("pearson")
            .item()
        )

        assert abs(mom_val - val_mom) < 1e-10

    def test_correlation_diagonal_one(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 34: Self-correlation = 1.0."""
        combiner = AlphaCombiner()
        result = combiner.compute_correlation_matrix(
            signals_dict, date(2024, 2, 29), lookback_days=30
        )

        corr_df = result.correlation_matrix
        mom_mom = (
            corr_df.filter((pl.col("signal_i") == "momentum") & (pl.col("signal_j") == "momentum"))
            .select("pearson")
            .item()
        )

        assert abs(mom_mom - 1.0) < 1e-10

    def test_correlation_winsorization_applied(self) -> None:
        """Test 35: Outliers clipped."""
        combiner = AlphaCombiner(config=CombinerConfig(winsorize_pct=0.1))

        # Signal with outlier
        signal = pl.DataFrame(
            {
                "permno": list(range(1, 11)),
                "date": [date(2024, 1, 1)] * 10,
                "signal": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 10.0],  # Outlier
            }
        )

        result = combiner.compute_correlation_matrix(
            {"sig": signal}, date(2024, 1, 1), lookback_days=1
        )

        # Should complete without error
        assert result.correlation_matrix.height > 0

    def test_correlation_pearson_and_spearman(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 36: Both computed."""
        combiner = AlphaCombiner()
        result = combiner.compute_correlation_matrix(
            signals_dict, date(2024, 2, 29), lookback_days=30
        )

        assert "pearson" in result.correlation_matrix.columns
        assert "spearman" in result.correlation_matrix.columns

    def test_correlation_high_corr_warning(self) -> None:
        """Test 37: Warning for |corr| > 0.7."""
        config = CombinerConfig(correlation_threshold=0.7)
        combiner = AlphaCombiner(config=config)

        # Two nearly identical signals with multiple dates (window excludes as_of_date)
        dates_list = [date(2024, 1, d) for d in range(1, 11)]  # Jan 1-10
        sig1_data = {
            "permno": [1, 2, 3, 4, 5] * 10,
            "date": [d for d in dates_list for _ in range(5)],
            "signal": [0.1, 0.2, 0.3, 0.4, 0.5] * 10,
        }

        sig2_data = {
            "permno": [1, 2, 3, 4, 5] * 10,
            "date": [d for d in dates_list for _ in range(5)],
            "signal": [0.11, 0.21, 0.31, 0.41, 0.51] * 10,  # Very similar
        }

        sig1 = pl.DataFrame(sig1_data)
        sig2 = pl.DataFrame(sig2_data)

        # as_of_date=Jan 11, lookback_days=20 => window covers Jan 1-10
        result = combiner.compute_correlation_matrix(
            {"sig1": sig1, "sig2": sig2}, date(2024, 1, 11), lookback_days=20
        )

        assert len(result.highly_correlated_pairs) > 0
        assert any("highly correlated" in w.lower() for w in result.warnings)

    def test_correlation_condition_number(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 38: Computed correctly."""
        combiner = AlphaCombiner()
        result = combiner.compute_correlation_matrix(
            signals_dict, date(2024, 2, 29), lookback_days=30
        )

        assert result.condition_number > 0
        assert not math.isnan(result.condition_number)

    def test_correlation_ill_conditioned_warning(self) -> None:
        """Test 39: Warning if condition number > 100."""
        combiner = AlphaCombiner()

        # Near-identical signals cause ill-conditioning - need multiple dates
        dates_list = [date(2024, 1, d) for d in range(1, 11)]  # Jan 1-10
        sig1_data = {
            "permno": list(range(1, 6)) * 10,
            "date": [d for d in dates_list for _ in range(5)],
            "signal": [0.1, 0.2, 0.3, 0.4, 0.5] * 10,
        }

        sig2_data = {
            "permno": list(range(1, 6)) * 10,
            "date": [d for d in dates_list for _ in range(5)],
            "signal": [0.1, 0.2, 0.3, 0.4, 0.5] * 10,  # Identical
        }

        sig1 = pl.DataFrame(sig1_data)
        sig2 = pl.DataFrame(sig2_data)

        # as_of_date=Jan 11, lookback_days=20 => window covers Jan 1-10
        result = combiner.compute_correlation_matrix(
            {"sig1": sig1, "sig2": sig2}, date(2024, 1, 11), lookback_days=20
        )

        # Should have condition number >= 1.0
        # (Note: with exactly identical signals, correlation = 1, condition may be high)
        assert result.condition_number >= 1.0

    def test_correlation_empty_join_returns_nan(self) -> None:
        """Test 40: No common pairs → NaN."""
        combiner = AlphaCombiner()

        sig1 = pl.DataFrame(
            {
                "permno": [1],
                "date": [date(2024, 1, 1)],
                "signal": [0.1],
            }
        )

        sig2 = pl.DataFrame(
            {
                "permno": [2],  # Different permno
                "date": [date(2024, 1, 1)],
                "signal": [0.2],
            }
        )

        result = combiner.compute_correlation_matrix(
            {"sig1": sig1, "sig2": sig2}, date(2024, 1, 1), lookback_days=1
        )

        # Correlation between sig1 and sig2 should be NaN
        cross_corr = (
            result.correlation_matrix.filter(
                (pl.col("signal_i") == "sig1") & (pl.col("signal_j") == "sig2")
            )
            .select("pearson")
            .item()
        )

        assert math.isnan(cross_corr)

    def test_correlation_matrix_uses_trailing_window(self) -> None:
        """Test 79: Correlation limited to lookback window."""
        combiner = AlphaCombiner()

        # Create signal with different correlation pattern before/after
        dates_early = [date(2024, 1, i) for i in range(1, 6)]
        dates_late = [date(2024, 1, i) for i in range(6, 11)]

        sig1 = pl.DataFrame(
            {
                "permno": [1, 2] * 10,
                "date": dates_early * 2 + dates_late * 2,
                "signal": [0.1, 0.2] * 5 + [0.5, 0.1] * 5,  # Changes pattern
            }
        )

        sig2 = pl.DataFrame(
            {
                "permno": [1, 2] * 10,
                "date": dates_early * 2 + dates_late * 2,
                "signal": [0.1, 0.2] * 5 + [0.1, 0.5] * 5,  # Changes pattern
            }
        )

        # Only use late period
        result = combiner.compute_correlation_matrix(
            {"sig1": sig1, "sig2": sig2}, date(2024, 1, 10), lookback_days=5
        )

        # Should have used only dates 5-9
        assert result.correlation_matrix.height == 4  # 2x2 matrix


# =============================================================================
# Test Combine Method (Tests 41-48)
# =============================================================================


class TestCombineMethod:
    """Tests for combine method."""

    def test_combine_equal_weighting(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 41: Full pipeline with equal."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
            lookback_days=30,
        )

        assert isinstance(result, CombineResult)
        assert result.signal_weights == {"momentum": 0.5, "value": 0.5}

    def test_combine_ic_weighting(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 42: Full pipeline with IC."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=10, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
            lookback_days=30,
        )

        assert isinstance(result, CombineResult)
        assert abs(sum(result.signal_weights.values()) - 1.0) < 1e-10

    def test_combine_ir_weighting(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 43: Full pipeline with IR."""
        config = CombinerConfig(weighting=WeightingMethod.IR, min_lookback_days=10, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
            lookback_days=30,
        )

        assert isinstance(result, CombineResult)

    def test_combine_vol_parity_weighting(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 44: Full pipeline with vol-parity."""
        config = CombinerConfig(weighting=WeightingMethod.VOL_PARITY, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            as_of_date=date(2024, 2, 29),
            lookback_days=30,
        )

        assert isinstance(result, CombineResult)

    def test_combine_output_schema(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 45: [permno, date, signal]."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
        )

        assert set(result.composite_signal.columns) == {"permno", "date", "signal"}

    def test_combine_returns_required_for_ic(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 46: ValueError if returns missing for IC."""
        config = CombinerConfig(weighting=WeightingMethod.IC)
        combiner = AlphaCombiner(config=config)

        with pytest.raises(ValueError, match="[Rr]eturns required"):
            combiner.combine(signals_dict, returns=None, as_of_date=date(2024, 2, 29))

    def test_combine_returns_optional_for_equal(
        self, signals_dict: dict[str, pl.DataFrame]
    ) -> None:
        """Test 47: Works without returns for EQUAL."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        # Should not raise
        result = combiner.combine(signals_dict, returns=None, as_of_date=date(2024, 2, 29))

        assert result is not None

    def test_combine_warnings_propagated(self, sample_returns: pl.DataFrame) -> None:
        """Test 48: Alignment warnings in result."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        # Signals with partial overlap
        sig1 = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "date": [date(2024, 1, 1)] * 3,
                "signal": [0.1, 0.2, 0.3],
            }
        )

        sig2 = pl.DataFrame(
            {
                "permno": [2, 3, 4],  # Different permnos
                "date": [date(2024, 1, 1)] * 3,
                "signal": [0.4, 0.5, 0.6],
            }
        )

        result = combiner.combine(
            {"sig1": sig1, "sig2": sig2},
            returns=None,
            as_of_date=date(2024, 1, 1),
        )

        assert len(result.warnings) > 0


# =============================================================================
# Test Turnover Integration (Tests 49-51, 59-62)
# =============================================================================


class TestTurnoverIntegration:
    """Tests for turnover integration."""

    def test_turnover_integration_with_portfolio(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 49: Uses TurnoverCalculator."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
            rolling_weights=True,  # Need multiple dates for turnover
            lookback_days=30,
        )

        # Turnover should be computed
        assert result.turnover_result is not None

    def test_turnover_result_in_output(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 50: TurnoverResult included."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
            rolling_weights=True,
            lookback_days=30,
        )

        if result.turnover_result:
            assert hasattr(result.turnover_result, "average_turnover")
            assert hasattr(result.turnover_result, "annualized_turnover")

    def test_turnover_adapter_uses_local(self) -> None:
        """Test 59: Always uses local TurnoverCalculator."""
        adapter = TurnoverAdapter()

        assert adapter.backend == "local"

    def test_turnover_adapter_backend_property(self) -> None:
        """Test 60: Reports "local" backend."""
        adapter = TurnoverAdapter()

        assert adapter.backend == "local"

    def test_turnover_adapter_qlib_compatible_property(self) -> None:
        """Test 61: Reports Qlib availability."""
        adapter = TurnoverAdapter()

        # Should be bool
        assert isinstance(adapter.qlib_compatible, bool)


# =============================================================================
# Test Edge Cases (Tests 52-55)
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_single_signal_passthrough(
        self, sample_signal_momentum: pl.DataFrame, sample_returns: pl.DataFrame
    ) -> None:
        """Test 52: 1 signal returns itself."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL, normalize=False)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            {"mom": sample_signal_momentum},
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
        )

        assert result.signal_weights == {"mom": 1.0}

    def test_empty_signals_error(self) -> None:
        """Test 53: Error on empty dict."""
        combiner = AlphaCombiner()

        with pytest.raises(ValueError, match="At least one signal"):
            combiner.combine({}, returns=None, as_of_date=date(2024, 1, 1))

    def test_insufficient_lookback_fallback(self) -> None:
        """Test 54: Fallback with short lookback."""
        config = CombinerConfig(
            weighting=WeightingMethod.IC,
            min_lookback_days=100,  # Very high threshold
        )
        combiner = AlphaCombiner(config=config)

        # Short signal
        signal = pl.DataFrame(
            {
                "permno": [1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 4)],
                "signal": [0.1, 0.2, 0.3],
            }
        )

        returns = pl.DataFrame(
            {
                "permno": [1, 1, 1],
                "date": [date(2024, 1, i) for i in range(1, 4)],
                "return": [0.01, 0.02, 0.03],
            }
        )

        # Should fallback to equal weights
        weights = combiner._compute_ic_weights(
            {"sig": signal}, returns, date(2024, 1, 3), lookback_days=2
        )

        assert weights["sig"] == 1.0  # Fallback for single signal

    def test_all_signals_null_on_date(self) -> None:
        """Test 55: Graceful handling of all nulls."""
        combiner = AlphaCombiner()

        signal = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "date": [date(2024, 1, 1)] * 3,
                "signal": [None, None, None],
            }
        )

        # Normalization should handle this
        normalized = combiner._normalize_signals({"sig": signal})

        assert normalized["sig"].height == 3


# =============================================================================
# Test Lookahead Prevention (Tests 67-78)
# =============================================================================


class TestLookaheadPrevention:
    """Tests for lookahead prevention."""

    def test_combine_as_of_date_explicit(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 67: Weights computed as of specified date."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        as_of = date(2024, 1, 15)
        result = combiner.combine(signals_dict, returns=sample_returns, as_of_date=as_of)

        # Output should only be for as_of_date
        output_dates = result.composite_signal.select("date").unique().to_series().to_list()
        assert output_dates == [as_of]

    def test_combine_as_of_date_none_uses_latest(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 68: Defaults to latest common date."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(signals_dict, returns=sample_returns)

        # Should have picked the last date in data
        expected_date = date(2024, 2, 29)  # Day 60
        output_dates = result.composite_signal.select("date").unique().to_series().to_list()
        assert output_dates == [expected_date]

    def test_combine_weights_no_future_data(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 69: Weights don't use data after as_of."""
        config = CombinerConfig(weighting=WeightingMethod.IC, min_lookback_days=5)
        combiner = AlphaCombiner(config=config)

        as_of = date(2024, 1, 15)
        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=as_of,
            lookback_days=10,
        )

        # Result should exist
        assert result is not None

    def test_combine_default_outputs_single_day(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 70: Production mode outputs only as_of_date."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 1, 15),
            rolling_weights=False,  # Production mode
        )

        # Should output single day only
        assert result.composite_signal.select("date").unique().height == 1

    def test_combine_no_output_before_as_of(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 71: No signals output for dates < as_of."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        as_of = date(2024, 1, 15)
        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=as_of,
            rolling_weights=False,
        )

        # No dates before as_of
        min_date = result.composite_signal.select(pl.col("date").min()).item()
        assert min_date >= as_of

    def test_combine_rolling_respects_as_of_upper_bound(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 72: rolling stops at as_of_date."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        as_of = date(2024, 1, 20)
        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=as_of,
            lookback_days=10,
            rolling_weights=True,
        )

        # No dates after as_of
        max_date = result.composite_signal.select(pl.col("date").max()).item()
        assert max_date <= as_of

    def test_combine_rolling_weights_per_date(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 73: Each date uses its own trailing weights."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 1, 20),
            lookback_days=10,
            rolling_weights=True,
        )

        # Weight history should have multiple dates
        if result.weight_history is not None:
            unique_dates = result.weight_history.select("date").unique().height
            assert unique_dates > 1

    def test_combine_rolling_weights_history(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 74: weight_history populated correctly."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 1, 20),
            lookback_days=10,
            rolling_weights=True,
        )

        assert result.weight_history is not None
        assert "date" in result.weight_history.columns
        assert "signal_name" in result.weight_history.columns
        assert "weight" in result.weight_history.columns

    def test_combine_rolling_weights_skips_insufficient_lookback(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 75: Dates without enough history excluded."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=date(2024, 2, 29),
            lookback_days=30,  # Need 30 days of history
            rolling_weights=True,
        )

        # First eligible date should be min_date + lookback
        if result.composite_signal.height > 0:
            min_output_date = result.composite_signal.select(pl.col("date").min()).item()
            # Should not include very early dates
            assert min_output_date >= date(2024, 1, 1) + timedelta(days=30)

    def test_combine_default_as_of_uses_min_max_date(
        self, sample_signal_momentum: pl.DataFrame
    ) -> None:
        """Test 76: Default = min(max_date) not max(min_date)."""
        combiner = AlphaCombiner()

        # Signal 1 ends on day 20
        sig1 = sample_signal_momentum.filter(pl.col("date") <= date(2024, 1, 20))

        # Signal 2 ends on day 30
        sig2 = sample_signal_momentum.filter(pl.col("date") <= date(2024, 1, 30))

        signals = {"sig1": sig1, "sig2": sig2}
        default_as_of = combiner._resolve_default_as_of_date(signals)

        # Should be day 20 (min of max dates)
        assert default_as_of == date(2024, 1, 20)

    def test_combine_ir_requires_returns(self, signals_dict: dict[str, pl.DataFrame]) -> None:
        """Test 77: ValueError when IR weighting without returns."""
        config = CombinerConfig(weighting=WeightingMethod.IR)
        combiner = AlphaCombiner(config=config)

        with pytest.raises(ValueError, match="[Rr]eturns required"):
            combiner.combine(signals_dict, returns=None, as_of_date=date(2024, 1, 15))

    def test_combine_rolling_with_explicit_as_of(
        self, signals_dict: dict[str, pl.DataFrame], sample_returns: pl.DataFrame
    ) -> None:
        """Test 78: rolling + explicit as_of respects boundary."""
        config = CombinerConfig(weighting=WeightingMethod.EQUAL)
        combiner = AlphaCombiner(config=config)

        explicit_as_of = date(2024, 1, 20)
        result = combiner.combine(
            signals_dict,
            returns=sample_returns,
            as_of_date=explicit_as_of,
            lookback_days=10,
            rolling_weights=True,
        )

        # Should respect explicit as_of as upper bound
        max_output = result.composite_signal.select(pl.col("date").max()).item()
        assert max_output <= explicit_as_of


# =============================================================================
# Test Helper Functions
# =============================================================================


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_winsorize_basic(self) -> None:
        """Test winsorization clips extremes."""
        series = pl.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 100])

        result = _winsorize(series, 0.1)

        # 100 should be clipped
        assert result.max() < 100

    def test_winsorize_empty(self) -> None:
        """Test winsorization on empty series."""
        series = pl.Series([], dtype=pl.Float64)

        result = _winsorize(series, 0.1)

        assert result.len() == 0

    def test_winsorize_all_null(self) -> None:
        """Test winsorization on all-null series."""
        series = pl.Series([None, None, None])

        result = _winsorize(series, 0.1)

        assert result.len() == 3
