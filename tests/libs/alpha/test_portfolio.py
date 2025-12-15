"""Tests for portfolio weight conversion and turnover."""

from datetime import date

import polars as pl
import pytest

from libs.alpha.portfolio import SignalToWeight, TurnoverCalculator


class TestSignalToWeight:
    """Tests for SignalToWeight converter."""

    @pytest.fixture()
    def sample_signals(self):
        """Create sample signals."""
        return pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5],
                "date": [date(2024, 1, 1)] * 5,
                "signal": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        )

    def test_zscore_weights_sum_to_zero(self, sample_signals):
        """Test z-score weights are dollar-neutral (sum to 0)."""
        converter = SignalToWeight(method="zscore")
        weights = converter.convert(sample_signals)

        weight_sum = weights.select(pl.col("weight").sum()).item()
        assert abs(weight_sum) < 1e-10

    def test_zscore_weights_leverage(self, sample_signals):
        """Test z-score weights respect target leverage."""
        converter = SignalToWeight(method="zscore", target_leverage=2.0)
        weights = converter.convert(sample_signals)

        abs_sum = weights.select(pl.col("weight").abs().sum()).item()
        assert abs_sum == pytest.approx(2.0, abs=0.01)

    def test_quantile_weights_structure(self, sample_signals):
        """Test quantile weights have correct structure."""
        converter = SignalToWeight(method="quantile", n_quantiles=5)
        weights = converter.convert(sample_signals)

        # Top quantile should be positive
        top = weights.filter(pl.col("permno") == 5).select("weight").item()
        assert top > 0

        # Bottom quantile should be negative
        bottom = weights.filter(pl.col("permno") == 1).select("weight").item()
        assert bottom < 0

    def test_rank_weights_dollar_neutral(self, sample_signals):
        """Test rank weights are dollar-neutral."""
        converter = SignalToWeight(method="rank")
        weights = converter.convert(sample_signals)

        weight_sum = weights.select(pl.col("weight").sum()).item()
        assert abs(weight_sum) < 1e-10

    def test_long_only_weights(self, sample_signals):
        """Test long-only constraint."""
        converter = SignalToWeight(method="zscore", long_only=True)
        weights = converter.convert(sample_signals)

        min_weight = weights.select(pl.col("weight").min()).item()
        assert min_weight >= 0

    def test_empty_signals(self):
        """Test handling of empty signals."""
        converter = SignalToWeight()
        empty = pl.DataFrame(schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64})
        weights = converter.convert(empty)

        assert weights.height == 0

    def test_null_signals_excluded(self):
        """Test null signals are excluded."""
        signals = pl.DataFrame(
            {
                "permno": [1, 2, 3, 4, 5],
                "date": [date(2024, 1, 1)] * 5,
                "signal": [1.0, None, 3.0, None, 5.0],
            }
        )
        converter = SignalToWeight()
        weights = converter.convert(signals)

        # Should only have 3 weights
        assert weights.height == 3

    def test_multiple_dates(self):
        """Test weights computed per date."""
        signals = pl.DataFrame(
            {
                "permno": [1, 2, 1, 2],
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                ],
                "signal": [1.0, 2.0, 3.0, 4.0],
            }
        )
        converter = SignalToWeight()
        weights = converter.convert(signals)

        # Each date should sum to 0
        for d in [date(2024, 1, 1), date(2024, 1, 2)]:
            day_sum = weights.filter(pl.col("date") == d).select(pl.col("weight").sum()).item()
            assert abs(day_sum) < 1e-10


class TestTurnoverCalculator:
    """Tests for TurnoverCalculator."""

    @pytest.fixture()
    def turnover_calc(self):
        """Create calculator."""
        return TurnoverCalculator()

    def test_daily_turnover_basic(self, turnover_calc):
        """Test basic daily turnover calculation."""
        weights = pl.DataFrame(
            {
                "permno": [1, 2, 1, 2],
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                ],
                "weight": [0.5, -0.5, 0.3, -0.3],
            }
        )

        daily = turnover_calc.compute_daily_turnover(weights)

        # First day: consistent formula sum(|w_t - w_{t-1}|) / 2 = sum(|w_t|) / 2
        # (|0.5| + |-0.5|) / 2 = 1.0 / 2 = 0.5
        first_turnover = daily.filter(pl.col("date") == date(2024, 1, 1)).select("turnover").item()
        assert first_turnover == pytest.approx(0.5)

        # Second day: |0.3 - 0.5| + |-0.3 - (-0.5)| = 0.2 + 0.2 = 0.4 / 2 = 0.2
        second_turnover = daily.filter(pl.col("date") == date(2024, 1, 2)).select("turnover").item()
        assert second_turnover == pytest.approx(0.2)

    def test_average_turnover(self, turnover_calc):
        """Test average turnover calculation."""
        weights = pl.DataFrame(
            {
                "permno": [1, 2, 1, 2, 1, 2],
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 3),
                ],
                "weight": [0.5, -0.5, 0.3, -0.3, 0.4, -0.4],
            }
        )

        avg = turnover_calc.compute_average_turnover(weights)
        # Excludes first day, average of day 2 and day 3 turnover
        assert avg > 0

    def test_empty_weights(self, turnover_calc):
        """Test handling of empty weights."""
        empty = pl.DataFrame(schema={"permno": pl.Int64, "date": pl.Date, "weight": pl.Float64})
        daily = turnover_calc.compute_daily_turnover(empty)
        assert daily.height == 0

    def test_single_day(self, turnover_calc):
        """Test handling of single day."""
        weights = pl.DataFrame(
            {
                "permno": [1, 2],
                "date": [date(2024, 1, 1)] * 2,
                "weight": [0.5, -0.5],
            }
        )
        avg = turnover_calc.compute_average_turnover(weights)
        assert avg == 0.0

    def test_turnover_result(self, turnover_calc):
        """Test full turnover result."""
        weights = pl.DataFrame(
            {
                "permno": [1, 2, 1, 2],
                "date": [
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                ],
                "weight": [0.5, -0.5, 0.3, -0.3],
            }
        )

        result = turnover_calc.compute_turnover_result(weights)

        assert result.daily_turnover.height == 2
        assert result.average_turnover >= 0
        assert result.annualized_turnover == result.average_turnover * 252
