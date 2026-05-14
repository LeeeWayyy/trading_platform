"""Shared helpers for dataframe-oriented data pipeline code."""

from __future__ import annotations

import polars as pl


def normalized_symbols_from_frame(
    frame: pl.DataFrame,
    *,
    column: str = "symbol",
) -> list[str]:
    """Return sorted, unique, normalized symbols from a Polars frame."""
    if frame.is_empty() or column not in frame.columns:
        return []

    symbols = (
        frame.get_column(column)
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .drop_nulls()
        .unique()
        .sort()
        .to_list()
    )
    return [symbol for symbol in symbols if isinstance(symbol, str) and symbol]


__all__ = ["normalized_symbols_from_frame"]
