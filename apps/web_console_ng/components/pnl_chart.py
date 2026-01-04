"""P&L chart visualization components for NiceGUI.

Renders equity curve and drawdown from pre-computed P&L data.
Ported from apps/web_console/components/pnl_chart.py (Streamlit).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.utils.formatters import parse_date_for_sort

# Type alias for P&L data - supports both dict and dataclass-like objects
DailyPnLLike = Mapping[str, Any] | Any


def _get_value(item: DailyPnLLike, key: str) -> Any:
    """Extract value from dict or object attribute."""
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _as_float(value: Any) -> tuple[float, bool]:
    """Convert to float with safe fallback for None/unknown/non-finite types.

    Returns:
        Tuple of (float_value, was_invalid) where was_invalid is True if
        the value was None, could not be converted, or is NaN/inf.
    """
    if value is None:
        return 0.0, True  # L-1 Fix: Track invalid values
    try:
        result = float(cast(float, value))
        if not math.isfinite(result):
            return 0.0, True  # Reject NaN/inf as invalid
        return result, False
    except (TypeError, ValueError):
        return 0.0, True  # L-1 Fix: Track invalid values


def _prepare_series(
    daily_pnl: Sequence[DailyPnLLike],
) -> tuple[list[str], list[float], list[float | None], int]:
    """Extract date, cumulative P&L, and drawdown series from P&L data.

    Args:
        daily_pnl: Sequence of P&L records with:
            - date: trade date
            - cumulative_realized_pl: cumulative realized P&L
            - drawdown_pct: drawdown percentage (negative for losses)

    Returns:
        Tuple of (dates, cumulative_values, drawdown_values, skipped_count)
        - skipped_count: Number of entries skipped due to invalid data.
        - drawdown_values may contain None for invalid drawdown (breaks line).
    """
    dates: list[str] = []
    cumulative: list[float] = []
    drawdowns: list[float | None] = []
    skipped_count = 0

    for entry in daily_pnl:
        # Validate date - skip entries with missing dates
        date_val = _get_value(entry, "date")
        if date_val is None:
            skipped_count += 1
            continue  # Skip entries with missing date

        # Skip entries with invalid cumulative P&L to avoid misleading 0.0 values
        # (0.0 could be confused with actual break-even)
        cum_val, cum_invalid = _as_float(_get_value(entry, "cumulative_realized_pl"))
        if cum_invalid:
            skipped_count += 1
            continue  # Skip entire entry if cumulative P&L is invalid

        dates.append(str(date_val))
        cumulative.append(cum_val)

        # For drawdown, use None for invalid values to break the line
        # (avoids misleading 0.0 which implies "no drawdown")
        dd_val, dd_invalid = _as_float(_get_value(entry, "drawdown_pct"))
        if dd_invalid:
            drawdowns.append(None)  # Break line for invalid data
        else:
            drawdowns.append(dd_val)

    # Sort by date for correct chronological ordering (input may be unsorted)
    # Use datetime parsing for proper sort order (handles non-ISO formats)
    if dates:
        sorted_data = sorted(
            zip(dates, cumulative, drawdowns, strict=False),
            key=lambda x: parse_date_for_sort(x[0]),
        )
        dates = [d for d, _, _ in sorted_data]
        cumulative = [c for _, c, _ in sorted_data]
        drawdowns = [dd for _, _, dd in sorted_data]

    return dates, cumulative, drawdowns, skipped_count


def render_pnl_equity_curve(daily_pnl: Sequence[DailyPnLLike]) -> None:
    """Render cumulative realized P&L line chart.

    Args:
        daily_pnl: Sequence of P&L records from StrategyScopedDataAccess.get_pnl_summary()
            Schema: {date, cumulative_realized_pl, drawdown_pct}
    """
    if not daily_pnl:
        ui.label("No performance data available for the selected range.").classes(
            "text-gray-500 text-center p-4"
        )
        return

    dates, cumulative, _, skipped_count = _prepare_series(daily_pnl)

    # Warn about data quality issues
    if skipped_count > 0:
        ui.label(
            f"Warning: {skipped_count} data point(s) skipped due to missing or invalid values."
        ).classes("text-yellow-600 text-sm mb-2")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=cumulative,
            mode="lines+markers",
            name="Cumulative Realized P&L",
            line={"color": "#2E86DE", "width": 3},
            marker={"size": 6},
        )
    )

    fig.update_layout(
        title="Equity Curve (Realized P&L Only)",
        xaxis_title="Date",
        yaxis_title="Cumulative Realized P&L",
        hovermode="x unified",
        margin={"l": 50, "r": 30, "t": 60, "b": 40},
    )

    ui.plotly(fig).classes("w-full")


def render_pnl_drawdown_chart(daily_pnl: Sequence[DailyPnLLike]) -> None:
    """Render drawdown area chart from pre-computed drawdown percentages.

    Args:
        daily_pnl: Sequence of P&L records from StrategyScopedDataAccess.get_pnl_summary()
            Schema: {date, cumulative_realized_pl, drawdown_pct}
    """
    if not daily_pnl:
        ui.label("No drawdown data available for the selected range.").classes(
            "text-gray-500 text-center p-4"
        )
        return

    dates, _, drawdowns, skipped_count = _prepare_series(daily_pnl)

    # Warn about data quality issues (drawdown may have None values that break the line)
    if skipped_count > 0:
        ui.label(
            f"Warning: {skipped_count} data point(s) skipped due to missing or invalid values."
        ).classes("text-yellow-600 text-sm mb-2")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=drawdowns,
            mode="lines",
            name="Drawdown %",
            line={"color": "#C0392B", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(192, 57, 43, 0.2)",
        )
    )

    fig.update_layout(
        title="Drawdown (Realized P&L Only)",
        xaxis_title="Date",
        yaxis_title="Drawdown %",
        hovermode="x unified",
        margin={"l": 50, "r": 30, "t": 60, "b": 40},
    )

    ui.plotly(fig).classes("w-full")


__all__ = [
    "render_pnl_equity_curve",
    "render_pnl_drawdown_chart",
    "DailyPnLLike",
]
