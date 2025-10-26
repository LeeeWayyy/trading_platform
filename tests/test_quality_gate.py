"""
Unit tests for quality gate (outlier detection) module.

Tests cover:
- Normal data (no outliers)
- Outlier detection (30% threshold)
- Corporate action awareness (large moves with CA = OK)
- Quarantine data with reason column
- Multiple symbols
- Edge cases (empty data, missing columns)
"""

from datetime import date

import polars as pl
import pytest

from libs.common.exceptions import OutlierError
from libs.data_pipeline.quality_gate import check_quality, detect_outliers


class TestDetectOutliers:
    """Tests for detect_outliers function."""

    def test_normal_data_no_outliers(self):
        """Normal price movements should pass quality gate."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 4,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15"],
                "close": [150.0, 151.5, 152.0, 151.0],  # Small changes
            }
        )

        good, quarantine = detect_outliers(df, threshold=0.30)

        assert len(good) == 4
        assert len(quarantine) == 0

    def test_outlier_without_ca_gets_quarantined(self):
        """Large move without corporate action should be flagged."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [150.0, 225.0, 226.0],  # 50% jump on Jan 11
            }
        )

        good, quarantine = detect_outliers(df, threshold=0.30)

        # Jan 10 and 12 should be good, Jan 11 should be quarantined
        assert len(good) == 2
        assert len(quarantine) == 1

        # Check quarantine has reason column
        assert "reason" in quarantine.columns
        assert "outlier_daily_return" in quarantine["reason"][0]

    def test_outlier_with_ca_passes(self):
        """Large move WITH corporate action should pass."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [150.0, 225.0, 226.0],  # 50% jump
            }
        )

        # Corporate action on Jan 11 explains the large move
        ca = pl.DataFrame({"symbol": ["AAPL"], "date": ["2024-01-11"]})

        good, quarantine = detect_outliers(df, ca_df=ca, threshold=0.30)

        # All should pass (CA explains the jump)
        assert len(good) == 3
        assert len(quarantine) == 0

    def test_first_row_never_outlier(self):
        """First row per symbol cannot be flagged (no prior close)."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": ["2024-01-10", "2024-01-11"],
                "close": [1000.0, 150.0],  # First row is abnormally high
            }
        )

        good, quarantine = detect_outliers(df, threshold=0.30)

        # Second row is -85% drop, should be flagged
        # First row cannot be flagged
        assert len(quarantine) == 1
        quarantined_date = quarantine["date"][0]
        assert quarantined_date == date(2024, 1, 11)

    def test_multiple_symbols_handled_separately(self):
        """Each symbol's returns should be calculated independently."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "MSFT", "MSFT"],
                "date": ["2024-01-10", "2024-01-11", "2024-01-10", "2024-01-11"],
                "close": [150.0, 225.0, 100.0, 101.0],  # AAPL outlier, MSFT normal
            }
        )

        good, quarantine = detect_outliers(df, threshold=0.30)

        # MSFT both rows good, AAPL first row good, AAPL second row outlier
        assert len(good) == 3
        assert len(quarantine) == 1

        # Quarantined should be AAPL
        assert quarantine["symbol"][0] == "AAPL"

    def test_custom_threshold(self):
        """Should respect custom threshold."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [150.0, 180.0, 181.0],  # 20% jump
            }
        )

        # 20% is below 30% threshold (passes)
        good, quarantine = detect_outliers(df, threshold=0.30)
        assert len(quarantine) == 0

        # 20% is above 10% threshold (fails)
        good, quarantine = detect_outliers(df, threshold=0.10)
        assert len(quarantine) == 1

    def test_negative_returns_flagged(self):
        """Large negative moves should also be flagged."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [150.0, 75.0, 76.0],  # -50% drop
            }
        )

        good, quarantine = detect_outliers(df, threshold=0.30)

        assert len(quarantine) == 1
        assert "0.5" in quarantine["reason"][0]  # 50% move

    def test_empty_dataframe(self):
        """Empty DataFrame should return two empty DataFrames."""
        df = pl.DataFrame(
            {
                "symbol": pl.Series([], dtype=pl.Utf8),
                "date": pl.Series([], dtype=pl.Date),
                "close": pl.Series([], dtype=pl.Float64),
            }
        )

        good, quarantine = detect_outliers(df)

        assert len(good) == 0
        assert len(quarantine) == 0

    def test_missing_columns_raises_error(self):
        """Missing required columns should raise ValueError."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "open": [150.0],
                # Missing date and close
            }
        )

        with pytest.raises(ValueError, match="missing required columns"):
            detect_outliers(df)

    def test_reason_column_format(self):
        """Quarantined data should have properly formatted reason."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [100.0, 145.0, 146.0],  # 45% jump
            }
        )

        good, quarantine = detect_outliers(df, threshold=0.30)

        reason = quarantine["reason"][0]
        assert reason.startswith("outlier_daily_return_")
        assert "0.45" in reason or "0.44" in reason  # Allow rounding


class TestCheckQuality:
    """Tests for check_quality convenience function."""

    def test_normal_data_returns_all(self):
        """Normal data should return all rows."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [150.0, 151.5, 152.0],
            }
        )

        result = check_quality(df)

        assert len(result) == 3

    def test_raise_on_outliers_true_raises(self):
        """Should raise OutlierError if outliers found and flag is True."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 3,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "close": [150.0, 225.0, 226.0],  # Outlier
            }
        )

        with pytest.raises(OutlierError) as exc_info:
            check_quality(df, raise_on_outliers=True)

        # Error should have useful context
        error_msg = str(exc_info.value)
        assert "Detected 1 outlier" in error_msg
        assert "AAPL" in error_msg

    def test_raise_on_outliers_false_filters(self):
        """Should filter outliers and return clean data if flag is False."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 4,
                "date": ["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15"],
                "close": [150.0, 151.0, 225.0, 226.0],  # Outlier on Jan 12
            }
        )

        result = check_quality(df, raise_on_outliers=False)

        # Should return Jan 10, 11, and 15 (Jan 12 outlier filtered)
        assert len(result) == 3
        assert date(2024, 1, 12) not in result["date"].to_list()
