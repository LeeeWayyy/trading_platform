"""Shared metric helpers for backtest comparison and live overlay.

Used by:
- T12.2 (backtest comparison): ``compute_tracking_error(pre_aligned=False)``
  with inner-join alignment on date.
- T12.3 (live vs backtest overlay): ``compute_tracking_error(pre_aligned=True)``
  when the caller has already aligned via left-join + zero-fill.
"""

from __future__ import annotations

import math

import polars as pl


def compute_tracking_error(
    returns_a: pl.DataFrame,
    returns_b: pl.DataFrame,
    *,
    pre_aligned: bool = False,
) -> float | None:
    """Compute annualized tracking error between two return series.

    Both inputs must have ``{date, return}`` schema.

    Args:
        returns_a: First return series with ``date`` and ``return`` columns.
        returns_b: Second return series with ``date`` and ``return`` columns.
        pre_aligned: If ``True``, skip internal date alignment (caller has
            already aligned, e.g. via left-join + zero-fill in T12.3).
            If ``False`` (default), perform inner-join on ``date``.

    Returns:
        Annualized tracking error (``std(diff, ddof=1) * sqrt(252)``),
        or ``None`` if fewer than 2 valid dates after filtering.
        Returns ``0.0`` if ``std == 0`` (identical return series).
    """
    if pre_aligned:
        # Caller guarantees row-aligned DataFrames (same dates in same order,
        # e.g. after left-join + zero-fill in T12.3).  Just pair columns
        # directly and drop rows where either side is null/NaN.
        aligned = pl.DataFrame(
            {
                "return": returns_a["return"],
                "return_b": returns_b["return"],
            }
        ).filter(
            pl.col("return").is_not_null()
            & pl.col("return").is_not_nan()
            & pl.col("return_b").is_not_null()
            & pl.col("return_b").is_not_nan()
        )
    else:
        # Inner join on date
        aligned = returns_a.select("date", "return").join(
            returns_b.select("date", pl.col("return").alias("return_b")),
            on="date",
        )
        # Drop rows with null/NaN returns
        aligned = aligned.filter(
            pl.col("return").is_not_null()
            & pl.col("return").is_not_nan()
            & pl.col("return_b").is_not_null()
            & pl.col("return_b").is_not_nan()
        )

    if len(aligned) < 2:
        return None

    # Compute tracking error: std(R_a - R_b, ddof=1) * sqrt(252)
    diff = (aligned["return"] - aligned["return_b"]).to_list()
    n = len(diff)
    mean_diff = sum(diff) / n
    variance = sum((d - mean_diff) ** 2 for d in diff) / (n - 1)
    std_diff = math.sqrt(variance)

    if std_diff == 0.0:
        return 0.0

    return std_diff * math.sqrt(252)


__all__ = ["compute_tracking_error"]
