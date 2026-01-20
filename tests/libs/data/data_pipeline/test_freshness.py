"""Tests for data freshness checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from libs.core.common.exceptions import StalenessError
from libs.data.data_pipeline import freshness


@pytest.fixture()
def fixed_now() -> datetime:
    """Capture a stable reference time for this test."""
    return datetime.now(UTC)


def _df_with_timestamps(
    timestamps: list[datetime], symbols: list[str] | None = None
) -> pl.DataFrame:
    data: dict[str, object] = {
        "timestamp": pl.Series(timestamps, dtype=pl.Datetime(time_zone="UTC"))
    }
    if symbols is not None:
        data["symbol"] = symbols
    return pl.DataFrame(data)


def test_check_freshness_latest_passes_when_recent(fixed_now: datetime) -> None:
    df = _df_with_timestamps([fixed_now - timedelta(minutes=10)])
    freshness.check_freshness(df, max_age_minutes=30, check_mode="latest")


def test_check_freshness_oldest_fails_on_stale_row(fixed_now: datetime) -> None:
    df = _df_with_timestamps(
        [fixed_now - timedelta(minutes=10), fixed_now - timedelta(minutes=120)]
    )
    with pytest.raises(StalenessError):
        freshness.check_freshness(df, max_age_minutes=30, check_mode="oldest")


def test_check_freshness_median_fails_when_median_stale(fixed_now: datetime) -> None:
    df = _df_with_timestamps(
        [
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=45),
            fixed_now - timedelta(minutes=60),
        ]
    )
    with pytest.raises(StalenessError):
        freshness.check_freshness(df, max_age_minutes=30, check_mode="median")


def test_check_freshness_per_symbol_threshold_enforced(fixed_now: datetime) -> None:
    df = _df_with_timestamps(
        [
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=120),
            fixed_now - timedelta(minutes=120),
        ],
        symbols=["AAPL", "MSFT", "GOOG", "TSLA"],
    )

    with pytest.raises(StalenessError) as exc:
        freshness.check_freshness(
            df,
            max_age_minutes=30,
            check_mode="per_symbol",
            min_fresh_pct=0.75,
        )

    assert "below threshold" in str(exc.value)
    assert "Stale symbols" in str(exc.value)


def test_check_freshness_per_symbol_passes_when_enough_fresh(fixed_now: datetime) -> None:
    df = _df_with_timestamps(
        [
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=5),
            fixed_now - timedelta(minutes=120),
        ],
        symbols=["AAPL", "MSFT", "GOOG", "TSLA"],
    )

    freshness.check_freshness(
        df,
        max_age_minutes=30,
        check_mode="per_symbol",
        min_fresh_pct=0.75,
    )


def test_check_freshness_missing_timestamp_column() -> None:
    df = pl.DataFrame({"symbol": ["AAPL"]})
    with pytest.raises(ValueError, match="timestamp"):
        freshness.check_freshness(df)


def test_check_freshness_empty_dataframe() -> None:
    df = pl.DataFrame({"timestamp": pl.Series([], dtype=pl.Datetime(time_zone="UTC"))})
    with pytest.raises(ValueError, match="empty DataFrame"):
        freshness.check_freshness(df)


def test_check_freshness_requires_timezone_aware() -> None:
    naive_dt = datetime(2026, 1, 17, 11, 0, 0)
    df = pl.DataFrame({"timestamp": [naive_dt]})
    with pytest.raises(ValueError, match="timezone-aware"):
        freshness.check_freshness(df)


def test_check_freshness_per_symbol_requires_symbol_column(fixed_now: datetime) -> None:
    df = _df_with_timestamps([fixed_now - timedelta(minutes=5)])
    with pytest.raises(ValueError, match="per_symbol"):
        freshness.check_freshness(df, check_mode="per_symbol")


def test_check_freshness_invalid_mode(fixed_now: datetime) -> None:
    df = _df_with_timestamps([fixed_now - timedelta(minutes=5)])
    with pytest.raises(ValueError, match="Invalid check_mode"):
        freshness.check_freshness(df, check_mode="bogus")


def test_check_freshness_safe_returns_false_on_stale(fixed_now: datetime) -> None:
    df = _df_with_timestamps([fixed_now - timedelta(minutes=120)])
    is_fresh, msg = freshness.check_freshness_safe(df, max_age_minutes=30)
    assert is_fresh is False
    assert msg is not None


def test_check_freshness_safe_handles_value_error_by_default() -> None:
    df = pl.DataFrame({"symbol": ["AAPL"]})
    is_fresh, msg = freshness.check_freshness_safe(df)
    assert is_fresh is False
    assert msg is not None
    assert "Freshness check failed" in msg


def test_check_freshness_safe_reraises_when_configured() -> None:
    df = pl.DataFrame({"symbol": ["AAPL"]})
    with pytest.raises(ValueError, match="'timestamp' column"):
        freshness.check_freshness_safe(df, default_to_stale=False)
