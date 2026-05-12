"""Tests for read-time adjustment derivation from raw canonical inputs."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from libs.data.data_pipeline.read_time_adjustment import (
    READ_TIME_ADJUSTMENT_AVAILABLE_REASON,
    READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
    READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON,
    READ_TIME_NO_SPLIT_ACTIONS_REASON,
    derive_split_adjusted_prices,
)


def _prices() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2020, 8, 28), date(2020, 8, 31), date(2020, 9, 1)],
            "open": [500.0, 125.0, 130.0],
            "high": [504.0, 126.0, 132.0],
            "low": [496.0, 124.0, 129.0],
            "close": [500.0, 125.0, 130.0],
            "volume": [1_000_000.0, 4_000_000.0, 3_800_000.0],
        }
    )


def _corp_actions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "ca_type": ["stock_split"],
            "ex_date": [date(2020, 8, 31)],
            "process_date": [date(2020, 8, 30)],
            "old_rate": [1.0],
            "new_rate": [4.0],
        }
    )


def test_split_adjustment_derives_adjusted_close_and_returns() -> None:
    result = derive_split_adjusted_prices(_prices(), _corp_actions())

    assert result.mode == READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED
    assert result.reason_codes == (READ_TIME_ADJUSTMENT_AVAILABLE_REASON,)
    assert result.split_action_count == 1
    assert result.frame["close"].to_list() == [500.0, 125.0, 130.0]
    assert result.frame["adj_close"].to_list() == [125.0, 125.0, 130.0]
    assert result.frame["split_adjustment_factor"].to_list() == [4.0, 1.0, 1.0]
    assert result.frame["adj_volume"].to_list() == [4_000_000.0, 4_000_000.0, 3_800_000.0]
    assert result.frame["ret"].to_list()[0] is None
    assert result.frame["ret"].to_list()[1:] == pytest.approx([0.0, 0.04])
    assert result.frame["read_time_adjustment_mode"].to_list() == [
        READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
        READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
        READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
    ]


def test_split_adjustment_applies_non_trading_day_action_to_prior_rows() -> None:
    corp_actions = _corp_actions().with_columns(pl.lit(date(2020, 8, 30)).alias("ex_date"))

    result = derive_split_adjusted_prices(_prices(), corp_actions)

    assert result.frame["adj_close"].to_list() == [125.0, 125.0, 130.0]
    assert result.frame["ret"].to_list()[0] is None
    assert result.frame["ret"].to_list()[1:] == pytest.approx([0.0, 0.04])


def test_no_split_actions_still_derives_trusted_identity_price_path() -> None:
    corp_actions = _corp_actions().with_columns(pl.lit("cash_dividend").alias("ca_type"))

    result = derive_split_adjusted_prices(_prices(), corp_actions)

    assert READ_TIME_NO_SPLIT_ACTIONS_REASON in result.reason_codes
    assert result.frame["adj_close"].to_list() == [500.0, 125.0, 130.0]
    assert result.frame["ret"].to_list()[0] is None
    assert result.frame["ret"].to_list()[1:] == pytest.approx([-0.75, 0.04])


def test_invalid_split_actions_are_skipped_with_reason_code() -> None:
    corp_actions = _corp_actions().with_columns(pl.lit(None).cast(pl.Float64).alias("new_rate"))

    result = derive_split_adjusted_prices(_prices(), corp_actions)

    assert result.skipped_action_count == 1
    assert READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON in result.reason_codes
    assert READ_TIME_NO_SPLIT_ACTIONS_REASON in result.reason_codes


def test_malformed_split_rates_are_skipped_with_reason_code() -> None:
    corp_actions = _corp_actions().with_columns(pl.lit("not-a-rate").alias("old_rate"))

    result = derive_split_adjusted_prices(_prices(), corp_actions)

    assert result.skipped_action_count == 1
    assert READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON in result.reason_codes
    assert result.frame["adj_close"].to_list() == [500.0, 125.0, 130.0]


def test_malformed_split_dates_are_skipped_with_reason_code() -> None:
    corp_actions = _corp_actions().with_columns(
        [
            pl.lit("not-a-date").alias("ex_date"),
            pl.lit(None).cast(pl.Date).alias("process_date"),
        ]
    )

    result = derive_split_adjusted_prices(_prices(), corp_actions)

    assert result.skipped_action_count == 1
    assert READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON in result.reason_codes
    assert result.frame["adj_close"].to_list() == [500.0, 125.0, 130.0]


def test_missing_corporate_action_date_column_fails_closed() -> None:
    corp_actions = _corp_actions().drop(["ex_date", "process_date"])

    with pytest.raises(ValueError, match="action date column"):
        derive_split_adjusted_prices(_prices(), corp_actions)
