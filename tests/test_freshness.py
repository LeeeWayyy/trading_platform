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

from libs.core.common.exceptions import StalenessError
from libs.data.data_pipeline.freshness import check_freshness, check_freshness_safe


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
            StalenessError, match=r"Data is.*minutes old.*exceeds threshold"
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


class TestCheckModes:
    """Tests for different check modes (T5.6 fix)."""

    def test_latest_mode_passes_with_mixed_data(self):
        """Latest mode should pass if most recent timestamp is fresh."""
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)

        # 999 stale rows but 1 fresh - should pass with "latest" mode
        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 10 + ["MSFT"],
                "timestamp": [stale] * 10 + [now],
            }
        )

        # Default mode "latest" should pass
        check_freshness(df, max_age_minutes=30, check_mode="latest")

    def test_oldest_mode_fails_with_mixed_data(self):
        """Oldest mode should fail if any data is stale."""
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)

        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "timestamp": [stale, now],
            }
        )

        # "oldest" mode should fail because AAPL is stale
        with pytest.raises(StalenessError, match="oldest timestamp"):
            check_freshness(df, max_age_minutes=30, check_mode="oldest")

    def test_oldest_mode_passes_when_all_fresh(self):
        """Oldest mode should pass when all data is fresh."""
        now = datetime.now(UTC)
        recent = now - timedelta(minutes=5)

        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "timestamp": [recent, now],
            }
        )

        # All fresh, should pass
        check_freshness(df, max_age_minutes=30, check_mode="oldest")

    def test_median_mode_with_outliers(self):
        """Median mode should be robust to outliers."""
        now = datetime.now(UTC)
        recent = now - timedelta(minutes=5)
        very_stale = now - timedelta(days=1)

        # 5 fresh, 1 very stale - median should be fresh
        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "TSLA", "AMZN", "META"],
                "timestamp": [recent, recent, recent, now, now, very_stale],
            }
        )

        # Median is fresh, should pass
        check_freshness(df, max_age_minutes=30, check_mode="median")

    def test_per_symbol_mode_passes_when_enough_fresh(self):
        """Per-symbol mode should pass when >= min_fresh_pct symbols are fresh."""
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)

        # 9 fresh symbols, 1 stale = 90% fresh
        symbols = [f"SYM{i}" for i in range(10)]
        timestamps = [now] * 9 + [stale]

        df = pl.DataFrame({"symbol": symbols, "timestamp": timestamps})

        # Should pass with 90% threshold
        check_freshness(df, max_age_minutes=30, check_mode="per_symbol", min_fresh_pct=0.9)

    def test_per_symbol_mode_fails_when_too_many_stale(self):
        """Per-symbol mode should fail when < min_fresh_pct symbols are fresh."""
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)

        # 8 fresh symbols, 2 stale = 80% fresh
        symbols = [f"SYM{i}" for i in range(10)]
        timestamps = [now] * 8 + [stale, stale]

        df = pl.DataFrame({"symbol": symbols, "timestamp": timestamps})

        # Should fail with 90% threshold
        with pytest.raises(StalenessError, match="80.0% of symbols are fresh"):
            check_freshness(df, max_age_minutes=30, check_mode="per_symbol", min_fresh_pct=0.9)

    def test_per_symbol_mode_requires_symbol_column(self):
        """Per-symbol mode should require 'symbol' column."""
        now = datetime.now(UTC)
        df = pl.DataFrame({"timestamp": [now]})

        with pytest.raises(ValueError, match="per_symbol mode requires 'symbol' column"):
            check_freshness(df, max_age_minutes=30, check_mode="per_symbol")

    def test_per_symbol_mode_groups_by_symbol(self):
        """Per-symbol mode should use latest timestamp per symbol."""
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)

        # AAPL has stale and fresh - should use fresh (latest per symbol)
        # MSFT only has stale
        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "MSFT"],
                "timestamp": [stale, now, stale],
            }
        )

        # 1/2 = 50% fresh, should fail at 90%
        with pytest.raises(StalenessError, match="50.0% of symbols are fresh"):
            check_freshness(df, max_age_minutes=30, check_mode="per_symbol", min_fresh_pct=0.9)

        # Should pass at 50%
        check_freshness(df, max_age_minutes=30, check_mode="per_symbol", min_fresh_pct=0.5)

    def test_default_mode_is_latest(self):
        """Default mode should be 'latest' for backwards compatibility."""
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)

        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "timestamp": [stale, now],  # One stale, one fresh
            }
        )

        # Default should be "latest", which passes
        check_freshness(df, max_age_minutes=30)  # No check_mode arg
