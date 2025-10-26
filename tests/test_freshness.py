"""
Unit tests for freshness checking module.

Tests cover:
- Fresh data validation (should pass)
- Stale data detection (should raise StalenessError)
- Missing timestamp column
- Timezone-aware timestamp requirement
- Empty DataFrame handling
"""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from libs.common.exceptions import StalenessError
from libs.data_pipeline.freshness import check_freshness, check_freshness_safe


class TestCheckFreshness:
    """Tests for check_freshness function."""

    def test_fresh_data_passes(self):
        """Fresh data within threshold should pass without raising."""
        # Create data with current timestamp
        df = pl.DataFrame({"symbol": ["AAPL"], "timestamp": [datetime.now(UTC)]})

        # Should not raise
        check_freshness(df, max_age_minutes=30)

    def test_stale_data_raises_error(self):
        """Stale data should raise StalenessError."""
        # Create data from 2 hours ago
        old_time = datetime.now(UTC) - timedelta(hours=2)
        df = pl.DataFrame({"symbol": ["AAPL"], "timestamp": [old_time]})

        with pytest.raises(
            StalenessError, match=r"Data is.*minutes old, exceeds threshold"
        ) as exc_info:
            check_freshness(df, max_age_minutes=30)

        # Verify error message contains useful details
        error_msg = str(exc_info.value)
        assert "120" in error_msg  # 120 minutes old
        assert "exceeds threshold of 30" in error_msg

    def test_missing_timestamp_column_raises_error(self):
        """DataFrame without timestamp column should raise ValueError."""
        df = pl.DataFrame({"symbol": ["AAPL"], "close": [150.0]})

        with pytest.raises(ValueError, match="timestamp"):
            check_freshness(df)

    def test_empty_dataframe_raises_error(self):
        """Empty DataFrame should raise ValueError."""
        df = pl.DataFrame(
            {
                "symbol": pl.Series([], dtype=pl.Utf8),
                "timestamp": pl.Series([], dtype=pl.Datetime(time_zone="UTC")),
            }
        )

        with pytest.raises(ValueError, match="empty"):
            check_freshness(df)

    def test_timezone_naive_timestamp_raises_error(self):
        """Timestamp without timezone should raise ValueError."""
        # Create timezone-naive timestamp
        df = pl.DataFrame({"symbol": ["AAPL"], "timestamp": [datetime.now()]})  # No timezone

        with pytest.raises(ValueError, match="timezone-aware"):
            check_freshness(df)

    def test_multiple_timestamps_uses_latest(self):
        """Should use the most recent timestamp in the DataFrame."""
        now = datetime.now(UTC)
        old = now - timedelta(hours=1)
        very_old = now - timedelta(hours=3)

        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL"],
                "timestamp": [very_old, old, now],  # Latest is fresh
            }
        )

        # Should pass because latest is fresh
        check_freshness(df, max_age_minutes=30)

    def test_custom_threshold(self):
        """Should respect custom freshness threshold."""
        # Data from 10 minutes ago
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        df = pl.DataFrame({"symbol": ["AAPL"], "timestamp": [old_time]})

        # Should pass with 30min threshold
        check_freshness(df, max_age_minutes=30)

        # Should fail with 5min threshold
        with pytest.raises(StalenessError):
            check_freshness(df, max_age_minutes=5)


class TestCheckFreshnessSafe:
    """Tests for check_freshness_safe function."""

    def test_fresh_data_returns_true(self):
        """Fresh data should return (True, None)."""
        df = pl.DataFrame({"symbol": ["AAPL"], "timestamp": [datetime.now(UTC)]})

        is_fresh, error_msg = check_freshness_safe(df, max_age_minutes=30)

        assert is_fresh is True
        assert error_msg is None

    def test_stale_data_returns_false_with_message(self):
        """Stale data should return (False, error_message)."""
        old_time = datetime.now(UTC) - timedelta(hours=2)
        df = pl.DataFrame({"symbol": ["AAPL"], "timestamp": [old_time]})

        is_fresh, error_msg = check_freshness_safe(df, max_age_minutes=30)

        assert is_fresh is False
        assert error_msg is not None
        assert "120" in error_msg  # 120 minutes

    def test_invalid_data_returns_false_by_default(self):
        """Invalid data should return False with default_to_stale=True."""
        df = pl.DataFrame({"symbol": ["AAPL"], "close": [150.0]})  # Missing timestamp

        is_fresh, error_msg = check_freshness_safe(df, max_age_minutes=30, default_to_stale=True)

        assert is_fresh is False
        assert "Freshness check failed" in error_msg

    def test_invalid_data_raises_with_default_to_stale_false(self):
        """Invalid data should raise when default_to_stale=False."""
        df = pl.DataFrame({"symbol": ["AAPL"], "close": [150.0]})  # Missing timestamp

        with pytest.raises(ValueError, match="timestamp"):
            check_freshness_safe(df, default_to_stale=False)
