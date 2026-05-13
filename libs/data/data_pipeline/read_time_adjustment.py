"""Read-time adjustment engine for raw canonical market data.

The engine derives adjusted preview/result columns from immutable raw inputs.
It does not rewrite canonical parquet storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl

READ_TIME_ADJUSTMENT_MODE_UNAVAILABLE: Literal["unavailable"] = "unavailable"
READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED: Literal["split_adjusted"] = "split_adjusted"
READ_TIME_ADJUSTMENT_AVAILABLE_REASON = "split_adjusted_read_time_available"
READ_TIME_NO_SPLIT_ACTIONS_REASON = "split_adjusted_no_split_actions_in_scope"
READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON = "split_adjusted_invalid_split_actions_skipped"

ReadTimeAdjustmentMode = Literal["split_adjusted"]

_PRICE_REQUIRED_COLUMNS = frozenset({"symbol", "date", "open", "high", "low", "close", "volume"})
_PRICE_COLUMNS = ("open", "high", "low", "close")
_CORP_ACTION_REQUIRED_COLUMNS = frozenset({"symbol", "ca_type", "old_rate", "new_rate"})
_CORP_ACTION_DATE_COLUMNS = ("ex_date", "process_date")
_SUPPORTED_SPLIT_CA_TYPES = (
    "forward_split",
    "forward_splits",
    "reverse_split",
    "reverse_splits",
    "split",
    "splits",
    "stock_split",
    "stock_splits",
    "unit_split",
    "unit_splits",
)


@dataclass(frozen=True)
class ReadTimeAdjustmentResult:
    """Derived read-time adjustment output and stable reason metadata."""

    frame: pl.DataFrame
    mode: ReadTimeAdjustmentMode
    reason_codes: tuple[str, ...]
    split_action_count: int
    skipped_action_count: int


def derive_split_adjusted_prices(
    prices: pl.DataFrame,
    corporate_actions: pl.DataFrame,
) -> ReadTimeAdjustmentResult:
    """Derive split-adjusted OHLCV and return columns from raw SIP inputs.

    The returned frame preserves raw ``open/high/low/close/volume`` columns,
    adds ``adj_open/adj_high/adj_low/adj_close/adj_volume`` plus ``ret``, and
    records that the output was derived at read time.
    """
    _require_columns(prices, _PRICE_REQUIRED_COLUMNS, label="prices")
    _require_columns(corporate_actions, _CORP_ACTION_REQUIRED_COLUMNS, label="corporate_actions")
    if not any(column in corporate_actions.columns for column in _CORP_ACTION_DATE_COLUMNS):
        raise ValueError(
            "corporate_actions missing required action date column: "
            "one of ex_date or process_date"
        )

    if prices.is_empty():
        return ReadTimeAdjustmentResult(
            frame=_empty_adjusted_frame(prices),
            mode=READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
            reason_codes=(READ_TIME_NO_SPLIT_ACTIONS_REASON,),
            split_action_count=0,
            skipped_action_count=0,
        )

    normalized_prices = _normalize_prices(prices)
    _ensure_unique_price_keys(normalized_prices)
    split_actions, skipped_action_count = _split_actions(corporate_actions)
    split_factor = _split_factor_frame(normalized_prices, split_actions)
    adjusted = (
        normalized_prices.join(split_factor, on=["symbol", "date"], how="left")
        .with_columns(pl.col("__split_factor").fill_null(1.0))
        .with_columns(
            [
                pl.col("__split_factor").alias("split_adjustment_factor"),
                (pl.col("open") / pl.col("__split_factor")).alias("adj_open"),
                (pl.col("high") / pl.col("__split_factor")).alias("adj_high"),
                (pl.col("low") / pl.col("__split_factor")).alias("adj_low"),
                (pl.col("close") / pl.col("__split_factor")).alias("adj_close"),
                (pl.col("volume") * pl.col("__split_factor")).alias("adj_volume"),
                pl.lit(READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED).alias("read_time_adjustment_mode"),
                pl.lit("alpaca_sip_daily+alpaca_sip_corp_actions").alias("derived_from"),
            ]
        )
        .sort(["symbol", "date"])
        .with_columns(pl.col("adj_close").shift(1).over("symbol").alias("__prev_adj_close"))
        .with_columns(
            pl.when(pl.col("__prev_adj_close").is_not_null() & (pl.col("__prev_adj_close") != 0))
            .then((pl.col("adj_close") / pl.col("__prev_adj_close")) - 1.0)
            .otherwise(None)
            .alias("ret")
        )
        .sort("__input_order")
        .drop(["__input_order", "__split_factor", "__prev_adj_close"])
    )

    reason_codes: set[str] = {READ_TIME_ADJUSTMENT_AVAILABLE_REASON}
    if split_actions.is_empty():
        reason_codes.add(READ_TIME_NO_SPLIT_ACTIONS_REASON)
    if skipped_action_count:
        reason_codes.add(READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON)

    return ReadTimeAdjustmentResult(
        frame=adjusted,
        mode=READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
        reason_codes=tuple(sorted(reason_codes)),
        split_action_count=len(split_actions),
        skipped_action_count=skipped_action_count,
    )


def _normalize_prices(prices: pl.DataFrame) -> pl.DataFrame:
    return prices.with_row_index("__input_order").with_columns(
        [
            pl.col("symbol").cast(pl.Utf8).str.strip_chars().str.to_uppercase(),
            pl.col("date").cast(pl.Date, strict=False),
            *[
                pl.col(column).cast(pl.Float64, strict=False)
                for column in (*_PRICE_COLUMNS, "volume")
            ],
        ]
    )


def _split_actions(corporate_actions: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    if corporate_actions.is_empty():
        return _empty_split_actions(), 0

    date_exprs = [
        pl.col(column).cast(pl.Date, strict=False)
        for column in _CORP_ACTION_DATE_COLUMNS
        if column in corporate_actions.columns
    ]
    normalized = corporate_actions.with_columns(
        [
            pl.col("symbol").cast(pl.Utf8).str.strip_chars().str.to_uppercase(),
            pl.col("ca_type")
            .cast(pl.Utf8)
            .str.strip_chars()
            .str.to_lowercase()
            .str.replace_all(r"\s+", "_"),
            pl.coalesce(date_exprs).alias("date"),
            pl.col("old_rate").cast(pl.Float64, strict=False),
            pl.col("new_rate").cast(pl.Float64, strict=False),
        ]
    )
    splits = normalized.filter(pl.col("ca_type").is_in(_SUPPORTED_SPLIT_CA_TYPES))
    valid_splits = splits.filter(
        pl.col("date").is_not_null()
        & pl.col("old_rate").is_not_null()
        & pl.col("new_rate").is_not_null()
        & pl.col("old_rate").is_finite()
        & pl.col("new_rate").is_finite()
        & (pl.col("old_rate") > 0)
        & (pl.col("new_rate") > 0)
    )
    skipped_action_count = len(splits) - len(valid_splits)
    if valid_splits.is_empty():
        return _empty_split_actions(), skipped_action_count

    split_actions = (
        valid_splits.select(
            [
                "symbol",
                "date",
                (pl.col("new_rate") / pl.col("old_rate")).alias("split_ratio"),
            ]
        )
        .group_by(["symbol", "date"])
        .agg(pl.col("split_ratio").product())
        .sort(["symbol", "date"])
    )
    return split_actions, skipped_action_count


def _split_factor_frame(prices: pl.DataFrame, split_actions: pl.DataFrame) -> pl.DataFrame:
    price_rows = prices.select(["symbol", "date"]).with_columns(
        [
            pl.lit(1.0).alias("split_ratio"),
            pl.lit(True).alias("__is_price_row"),
        ]
    )
    action_rows = split_actions.with_columns(pl.lit(False).alias("__is_price_row"))
    timeline = (
        pl.concat([price_rows, action_rows], how="vertical")
        .group_by(["symbol", "date"])
        .agg(
            [
                pl.col("split_ratio").product(),
                pl.col("__is_price_row").max(),
            ]
        )
        .sort(["symbol", "date"])
        .with_columns(
            pl.col("split_ratio")
            .reverse()
            .cum_prod()
            .shift(1, fill_value=1.0)
            .reverse()
            .over("symbol")
            .alias("__split_factor")
        )
    )
    return timeline.filter(pl.col("__is_price_row")).select(["symbol", "date", "__split_factor"])


def _empty_adjusted_frame(prices: pl.DataFrame) -> pl.DataFrame:
    frame = prices.clone()
    for column in (
        "split_adjustment_factor",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adj_volume",
        "ret",
    ):
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None).cast(pl.Float64).alias(column))
    if "read_time_adjustment_mode" not in frame.columns:
        frame = frame.with_columns(pl.lit(None).cast(pl.Utf8).alias("read_time_adjustment_mode"))
    if "derived_from" not in frame.columns:
        frame = frame.with_columns(pl.lit(None).cast(pl.Utf8).alias("derived_from"))
    return frame


def _empty_split_actions() -> pl.DataFrame:
    return pl.DataFrame({"symbol": [], "date": [], "split_ratio": []}).with_columns(
        [
            pl.col("symbol").cast(pl.Utf8),
            pl.col("date").cast(pl.Date),
            pl.col("split_ratio").cast(pl.Float64),
        ]
    )


def _ensure_unique_price_keys(prices: pl.DataFrame) -> None:
    duplicates = (
        prices.group_by(["symbol", "date"])
        .agg(pl.len().alias("__duplicate_count"))
        .filter(pl.col("__duplicate_count") > 1)
    )
    if duplicates.is_empty():
        return

    sample = duplicates.select(["symbol", "date"]).head(1).to_dicts()[0]
    raise ValueError(
        "prices contain duplicate symbol/date rows: "
        f"symbol={sample['symbol']} date={sample['date']}"
    )


def _require_columns(frame: pl.DataFrame, required: frozenset[str], *, label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


__all__ = [
    "READ_TIME_ADJUSTMENT_AVAILABLE_REASON",
    "READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED",
    "READ_TIME_ADJUSTMENT_MODE_UNAVAILABLE",
    "READ_TIME_INVALID_SPLIT_ACTIONS_SKIPPED_REASON",
    "READ_TIME_NO_SPLIT_ACTIONS_REASON",
    "ReadTimeAdjustmentResult",
    "derive_split_adjusted_prices",
]
