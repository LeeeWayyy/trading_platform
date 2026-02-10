"""Tests for libs/analytics/metrics.py (P6T12 shared infrastructure)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from libs.analytics.metrics import compute_tracking_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_returns(start: date, returns: list[float]) -> pl.DataFrame:
    """Build a DataFrame with {date, return} from a list of daily returns."""
    dates = [start + timedelta(days=i) for i in range(len(returns))]
    return pl.DataFrame({"date": dates, "return": returns})


# ===================================================================
# compute_tracking_error - inner join mode (pre_aligned=False)
# ===================================================================
class TestComputeTrackingErrorInnerJoin:
    def test_identical_series_returns_zero(self) -> None:
        """Identical return series should have TE = 0.0."""
        start = date(2024, 1, 1)
        df = _make_returns(start, [0.01, 0.02, -0.01, 0.005])
        result = compute_tracking_error(df, df)
        assert result == 0.0

    def test_known_tracking_error(self) -> None:
        """Verify TE against hand-calculated value."""
        start = date(2024, 1, 1)
        a = _make_returns(start, [0.01, 0.02, -0.01, 0.03, 0.00])
        b = _make_returns(start, [0.02, 0.01, 0.00, 0.02, -0.01])

        # diffs: [-0.01, 0.01, -0.01, 0.01, 0.01]
        diffs = [-0.01, 0.01, -0.01, 0.01, 0.01]
        mean_diff = sum(diffs) / 5  # 0.002
        var = sum((d - mean_diff) ** 2 for d in diffs) / 4  # ddof=1
        expected = math.sqrt(var) * math.sqrt(252)

        result = compute_tracking_error(a, b)
        assert result is not None
        assert abs(result - expected) < 1e-10

    def test_partial_date_overlap(self) -> None:
        """Only overlapping dates should be used."""
        a = _make_returns(date(2024, 1, 1), [0.01, 0.02, 0.03])
        b = _make_returns(date(2024, 1, 2), [0.02, 0.01, 0.04])
        # Overlap: Jan 2 and Jan 3
        result = compute_tracking_error(a, b)
        assert result is not None

    def test_no_overlap_returns_none(self) -> None:
        """Non-overlapping dates should return None."""
        a = _make_returns(date(2024, 1, 1), [0.01, 0.02])
        b = _make_returns(date(2024, 2, 1), [0.01, 0.02])
        result = compute_tracking_error(a, b)
        assert result is None

    def test_single_overlap_returns_none(self) -> None:
        """Only 1 overlapping date means < 2, returns None."""
        a = _make_returns(date(2024, 1, 1), [0.01, 0.02])
        b = _make_returns(date(2024, 1, 2), [0.03])
        result = compute_tracking_error(a, b)
        assert result is None

    def test_null_values_dropped(self) -> None:
        """Rows with null returns should be dropped before TE computation."""
        start = date(2024, 1, 1)
        a = pl.DataFrame({
            "date": [start, start + timedelta(days=1), start + timedelta(days=2), start + timedelta(days=3)],
            "return": [0.01, None, 0.03, 0.02],
        })
        b = _make_returns(start, [0.02, 0.01, 0.02, 0.01])
        result = compute_tracking_error(a, b)
        # Should compute from 3 valid rows (rows 0, 2, 3)
        assert result is not None

    def test_nan_values_dropped(self) -> None:
        """Rows with NaN returns should be dropped."""
        start = date(2024, 1, 1)
        a = _make_returns(start, [0.01, float("nan"), 0.03, 0.02])
        b = _make_returns(start, [0.02, 0.01, 0.02, 0.01])
        result = compute_tracking_error(a, b)
        assert result is not None

    def test_empty_dataframe_returns_none(self) -> None:
        """Empty input should return None."""
        empty = pl.DataFrame({"date": [], "return": []}).cast({"date": pl.Date, "return": pl.Float64})
        other = _make_returns(date(2024, 1, 1), [0.01, 0.02])
        result = compute_tracking_error(empty, other)
        assert result is None


# ===================================================================
# compute_tracking_error - pre-aligned mode (pre_aligned=True)
# ===================================================================
class TestComputeTrackingErrorPreAligned:
    def test_pre_aligned_identical(self) -> None:
        """Pre-aligned identical series should return 0.0."""
        start = date(2024, 1, 1)
        df = _make_returns(start, [0.01, 0.02, -0.01])
        result = compute_tracking_error(df, df, pre_aligned=True)
        assert result == 0.0

    def test_pre_aligned_known_value(self) -> None:
        """Pre-aligned mode produces same result as inner join when dates match."""
        start = date(2024, 1, 1)
        a = _make_returns(start, [0.01, 0.02, -0.01, 0.03])
        b = _make_returns(start, [0.02, 0.01, 0.00, 0.01])

        inner_result = compute_tracking_error(a, b, pre_aligned=False)
        aligned_result = compute_tracking_error(a, b, pre_aligned=True)
        assert inner_result is not None
        assert aligned_result is not None
        assert abs(inner_result - aligned_result) < 1e-10

    def test_pre_aligned_drops_nulls(self) -> None:
        """Pre-aligned mode should still drop null/NaN values."""
        start = date(2024, 1, 1)
        a = pl.DataFrame({
            "date": [start, start + timedelta(days=1), start + timedelta(days=2)],
            "return": [0.01, None, 0.03],
        })
        b = _make_returns(start, [0.02, 0.01, 0.02])
        result = compute_tracking_error(a, b, pre_aligned=True)
        # 2 valid rows remain (rows 0, 2)
        assert result is not None

    def test_pre_aligned_single_valid_row_returns_none(self) -> None:
        """With only 1 valid row after null filtering, returns None."""
        start = date(2024, 1, 1)
        a = pl.DataFrame({
            "date": [start, start + timedelta(days=1)],
            "return": [0.01, None],
        })
        b = pl.DataFrame({
            "date": [start, start + timedelta(days=1)],
            "return": [None, 0.02],
        })
        result = compute_tracking_error(a, b, pre_aligned=True)
        assert result is None
