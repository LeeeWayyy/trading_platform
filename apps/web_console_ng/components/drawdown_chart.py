"""Drawdown visualization component for NiceGUI.

Renders maximum drawdown over time as a Plotly area chart.
Ported from apps/web_console/components/drawdown_chart.py (Streamlit).

P6T10: Added underwater chart and drawdown period extraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrawdownPeriod:
    """Individual drawdown period metadata.

    Precise Definitions:
    - peak_date: Date where wealth index reaches new high (W_t > M_{t-1})
    - trough_date: Date of minimum wealth during this drawdown
    - recovery_date: First date where W_t >= M_peak (None if ongoing)
    - max_drawdown: Most negative drawdown (e.g., -0.284 for -28.4%)
    - duration_days: Calendar days from peak to recovery (includes weekends)
    """

    peak_date: date
    trough_date: date
    recovery_date: date | None
    max_drawdown: float  # Negative, e.g., -0.284
    duration_days: int  # Calendar days (labeled as such in UI)


def _detect_period_boundaries(analysis_df: pl.DataFrame) -> pl.DataFrame:
    """Detect drawdown period boundaries using run-length encoding.

    Identifies transitions in the in_drawdown flag:
    - period_start: True when transitioning from non-drawdown to drawdown
    - period_end: True when transitioning from drawdown to non-drawdown
    - period_id: Cumulative sum of period starts (unique ID per period)

    Args:
        analysis_df: DataFrame with 'in_drawdown' boolean column.

    Returns:
        DataFrame with added columns: period_start, period_end, period_id.
    """
    return analysis_df.with_columns(
        [
            # Detect transitions: False->True = start of drawdown
            (pl.col("in_drawdown") & ~pl.col("in_drawdown").shift(1).fill_null(False)).alias(
                "period_start"
            ),
            # Detect transitions: True->False = recovery (end of drawdown)
            (~pl.col("in_drawdown") & pl.col("in_drawdown").shift(1).fill_null(False)).alias(
                "period_end"
            ),
        ]
    ).with_columns(
        [
            pl.col("period_start").cum_sum().alias("period_id"),
        ]
    )


def _compute_wealth_and_drawdown(sorted_df: pl.DataFrame) -> pl.DataFrame:
    """Compute wealth index and drawdown from sorted daily returns.

    Shared helper to avoid code duplication between compute_drawdown_periods
    and render_drawdown_underwater.

    Args:
        sorted_df: DataFrame with 'return' column, already sorted by date
                   and filtered for finite values.

    Returns:
        DataFrame with added columns:
        - wealth: Cumulative wealth index (1 + r).cum_prod()
        - running_peak: M_t = max(1.0, max(W_1, ..., W_t))
        - drawdown: (W_t / M_t) - 1, always <= 0
    """
    return sorted_df.with_columns(
        (1 + pl.col("return")).cum_prod().alias("wealth"),
    ).with_columns(
        # Running max starting from W₀=1.0: M_t = max(1.0, max(W_1, ..., W_t))
        pl.max_horizontal(1.0, pl.col("wealth").cum_max()).alias("running_peak"),
    ).with_columns(
        # Drawdown = W_t / M_t - 1 (handle div by zero for complete loss)
        pl.when(pl.col("running_peak") == 0)
        .then(-1.0)
        .otherwise(pl.col("wealth") / pl.col("running_peak") - 1)
        .alias("drawdown"),
    )


def render_drawdown_chart(
    daily_returns: pl.DataFrame | None,
    title: str = "Drawdown",
    height: int = 300,
) -> None:
    """Render drawdown chart from daily returns.

    Args:
        daily_returns: DataFrame with columns: date, return
            - Data source: StrategyScopedDataAccess.get_performance_returns()
            - Schema: {date: date, return: float (e.g. 0.01 = 1%)}
        title: Chart title
        height: Chart height in pixels

    Drawdown is computed as:
    - cumulative = (1 + r).cumprod()
    - running_max = cumulative.cummax()
    - drawdown = (cumulative - running_max) / running_max
    """
    if daily_returns is None or daily_returns.height == 0:
        ui.label("No return data available for drawdown chart").classes(
            "text-gray-500 text-center p-4"
        )
        return

    # Validate required columns
    required_cols = {"date", "return"}
    missing_cols = required_cols - set(daily_returns.columns)
    if missing_cols:
        ui.notify(f"Missing columns: {missing_cols}", type="negative")
        return

    try:
        # Sort by date
        sorted_df = daily_returns.sort("date")

        # Filter out non-finite returns (NaN/inf can poison cumulative series)
        original_count = sorted_df.height
        sorted_df = sorted_df.filter(pl.col("return").is_finite())
        filtered_count = original_count - sorted_df.height
        if filtered_count > 0:
            ui.label(f"Warning: {filtered_count} invalid return value(s) excluded.").classes(
                "text-yellow-600 text-sm mb-2"
            )

        if sorted_df.height == 0:
            ui.label("No valid return data for drawdown chart.").classes(
                "text-gray-500 text-center p-4"
            )
            return

        # Compute cumulative wealth (1 + cumulative return)
        cumulative = (1 + sorted_df["return"]).cum_prod()

        # Running maximum
        running_max = cumulative.cum_max()

        # Drawdown = (current - peak) / peak
        # LOW fix: Avoid division by zero when running_max is 0 (100% loss scenario)
        # If running_max is 0, drawdown is -1.0 (complete loss)
        drawdown = (
            pl.when(running_max == 0).then(-1.0).otherwise((cumulative - running_max) / running_max)
        )

        # Add columns
        chart_df = sorted_df.with_columns(drawdown.alias("drawdown"))

        # Convert to pandas for plotly
        chart_pd = chart_df.select(["date", "drawdown"]).to_pandas()

        # Create plotly figure
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["drawdown"] * 100,  # Convert to percentage
                mode="lines",
                name="Drawdown",
                line={"color": "#d62728", "width": 1.5},
                fill="tozeroy",
                fillcolor="rgba(214, 39, 40, 0.3)",
            )
        )

        # Find max drawdown for annotation (guard against empty/NaN series)
        if not chart_pd.empty and not chart_pd["drawdown"].isnull().all():
            max_dd = chart_pd["drawdown"].min()
            max_dd_idx = chart_pd["drawdown"].idxmin()
            max_dd_date = chart_pd.loc[max_dd_idx, "date"]

            fig.add_annotation(
                x=max_dd_date,
                y=max_dd * 100,
                text=f"Max DD: {max_dd * 100:.1f}%",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=-40,
            )

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            height=height,
            showlegend=False,
            hovermode="x unified",
            yaxis={"tickformat": ".1f", "ticksuffix": "%"},
        )

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(
            "Drawdown chart rendering failed - invalid data",
            extra={"chart": "drawdown", "error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 text-center p-4")


def compute_drawdown_periods(
    daily_returns: pl.DataFrame,
    min_depth: float = 0.05,
) -> list[DrawdownPeriod]:
    """Extract drawdown periods using wealth index.

    Algorithm (state machine):
    1. Compute wealth index: W_t = prod(1 + r_i), W_0 = 1.0
    2. Compute running max: M_t = max(W_i for i in [0,t])
    3. Compute drawdown: D_t = W_t / M_t - 1

    State Machine:
    - PEAK_SEARCH: Track running max. If W_t > M_{t-1}: new peak.
                   If W_t < M_t (first drop): enter IN_DRAWDOWN.
    - IN_DRAWDOWN: Track trough (min W_t since peak).
                   If W_t >= M_peak: RECOVERED, record period.
                   If end of data: mark as ongoing.

    Equal-peak handling: New peak only if W_t > M_{t-1} (strictly greater).

    Args:
        daily_returns: DataFrame with columns [date, return].
        min_depth: Minimum drawdown depth to record (default 5%).

    Returns:
        List of DrawdownPeriod sorted by max_drawdown (worst first).
    """
    if daily_returns is None or daily_returns.height == 0:
        return []

    # Sort and filter
    sorted_df = daily_returns.sort("date").filter(pl.col("return").is_finite())
    if sorted_df.height == 0:
        return []

    # ============================================================
    # VECTORIZED DRAWDOWN PERIOD DETECTION
    # Uses Polars window functions instead of Python loop for ~10-100x speedup
    # ============================================================

    # Step 1-2: Compute wealth, running_peak, and drawdown using shared helper
    analysis_df = _compute_wealth_and_drawdown(sorted_df)

    # Add in_drawdown flag for period detection
    analysis_df = analysis_df.with_columns(
        (pl.col("wealth") < pl.col("running_peak")).alias("in_drawdown"),
    )

    # Step 3-4: Identify drawdown period boundaries using helper
    analysis_df = _detect_period_boundaries(analysis_df)

    # Step 5: For each period, compute statistics using window functions
    # Only consider rows where in_drawdown=True (actual drawdown periods)
    drawdown_rows = analysis_df.filter(pl.col("in_drawdown"))

    if drawdown_rows.height == 0:
        return []

    # Compute per-period statistics
    period_stats = drawdown_rows.group_by("period_id").agg(
        [
            # Trough: minimum wealth in the period
            pl.col("wealth").min().alias("trough_wealth"),
            pl.col("drawdown").min().alias("max_drawdown"),
            # Trough date: date of minimum wealth
            pl.col("date")
            .filter(pl.col("wealth") == pl.col("wealth").min())
            .first()
            .alias("trough_date"),
            # First date in drawdown (for finding peak before it)
            pl.col("date").first().alias("first_drawdown_date"),
            # Last date in drawdown
            pl.col("date").last().alias("last_drawdown_date"),
        ]
    )

    # Step 6: Find peak and recovery dates using vectorized Polars operations
    # Add row index for positional lookups
    analysis_with_idx = analysis_df.with_row_index("row_idx")

    # Get the first date (for periods starting at the beginning)
    first_date = analysis_df["date"].head(1).item()
    last_date = analysis_df["date"].tail(1).item()

    # Find peak dates: for each period, peak is the row just before first_drawdown_date
    # where wealth equals running_peak (not in drawdown)
    # Join period_stats with analysis_df to find the row index of first_drawdown_date
    period_with_idx = period_stats.join(
        analysis_with_idx.select(["date", "row_idx"]),
        left_on="first_drawdown_date",
        right_on="date",
        how="left",
    ).rename({"row_idx": "first_dd_idx"})

    # Also get last_drawdown_date index
    period_with_idx = period_with_idx.join(
        analysis_with_idx.select(["date", "row_idx"]),
        left_on="last_drawdown_date",
        right_on="date",
        how="left",
    ).rename({"row_idx": "last_dd_idx"})

    # Find peak dates by looking at the row before first_dd_idx
    # Peak is where running_peak was set (last row before drawdown where wealth=running_peak)
    non_drawdown_rows = analysis_with_idx.filter(~pl.col("in_drawdown")).select([
        "row_idx", "date", "wealth"
    ])

    # For each period, find the latest non-drawdown row before first_dd_idx
    # Use join_asof with backward strategy instead of O(M*N) cross join
    non_drawdown_sorted = non_drawdown_rows.sort("row_idx")
    period_for_asof = period_with_idx.select(["period_id", "first_dd_idx"]).sort("first_dd_idx")

    peak_candidates = period_for_asof.join_asof(
        non_drawdown_sorted,
        left_on="first_dd_idx",
        right_on="row_idx",
        strategy="backward",
    ).filter(
        # Ensure we found a valid peak (row_idx < first_dd_idx)
        pl.col("row_idx").is_not_null() & (pl.col("row_idx") < pl.col("first_dd_idx"))
    ).select([
        "period_id",
        pl.col("date").alias("peak_date"),
        pl.col("wealth").alias("peak_wealth"),
    ])

    # Handle periods that start at the beginning (no row before first_dd_idx)
    # These have no peak_date from the join, default to first_date with wealth=1.0
    period_with_peak = period_with_idx.join(
        peak_candidates,
        on="period_id",
        how="left",
    ).with_columns([
        pl.col("peak_date").fill_null(pl.lit(first_date)),
        pl.col("peak_wealth").fill_null(1.0),
    ])

    # Find recovery dates: first non-drawdown row after last_dd_idx where wealth >= peak_wealth
    # Use iterative approach over periods (small set) instead of O(M*N) cross join
    # This is memory-efficient since periods are typically few (tens) vs many non-drawdown rows
    recovery_dates: list[dict[str, object]] = []
    for period_row in period_with_peak.iter_rows(named=True):
        period_id = period_row["period_id"]
        last_dd_idx = period_row["last_dd_idx"]
        peak_wealth = period_row["peak_wealth"]

        # Find first non-drawdown row after period where wealth recovered
        recovery_df = non_drawdown_sorted.filter(
            (pl.col("row_idx") > last_dd_idx)
            & (pl.col("wealth") >= peak_wealth)
        ).head(1)

        if recovery_df.height > 0:
            recovery_dates.append({
                "period_id": period_id,
                "recovery_date": recovery_df["date"].item(),
            })

    recovery_candidates = pl.DataFrame(recovery_dates) if recovery_dates else pl.DataFrame(schema={
        "period_id": pl.Int64,
        "recovery_date": pl.Date,
    })

    # Join recovery dates
    period_final = period_with_peak.join(
        recovery_candidates,
        on="period_id",
        how="left",
    )

    # Filter by minimum depth threshold
    period_final = period_final.filter(pl.col("max_drawdown").abs() >= min_depth)

    if period_final.height == 0:
        return []

    # Compute duration and build result list
    period_final = period_final.with_columns([
        pl.when(pl.col("recovery_date").is_not_null())
        .then((pl.col("recovery_date") - pl.col("peak_date")).dt.total_days())
        .otherwise((pl.lit(last_date) - pl.col("peak_date")).dt.total_days())
        .alias("duration_days"),
    ])

    # Sort by severity (worst first) and convert to list of DrawdownPeriod
    period_final = period_final.sort("max_drawdown")

    # Convert to DrawdownPeriod objects using to_dicts() for efficient batch conversion
    periods = [
        DrawdownPeriod(
            peak_date=row["peak_date"],
            trough_date=row["trough_date"],
            recovery_date=row["recovery_date"],
            max_drawdown=row["max_drawdown"],
            duration_days=int(row["duration_days"]) if row["duration_days"] is not None else 0,
        )
        for row in period_final.select([
            "peak_date", "trough_date", "recovery_date", "max_drawdown", "duration_days"
        ]).to_dicts()
    ]

    return periods


def render_drawdown_underwater(
    daily_returns: pl.DataFrame | None,
    title: str = "Drawdown (Underwater View)",
    height: int = 350,
) -> None:
    """Render inverted drawdown chart with duration annotations.

    Uses wealth index: W_t = prod(1 + r_i), starting at W_0 = 1.0
    Drawdown = W_t / M_t - 1 (always <= 0)

    Args:
        daily_returns: DataFrame with columns [date, return].
        title: Chart title.
        height: Chart height in pixels.
    """
    if daily_returns is None or daily_returns.height == 0:
        ui.label("No return data available for underwater chart").classes(
            "text-gray-500 text-center p-4"
        )
        return

    required_cols = {"date", "return"}
    missing_cols = required_cols - set(daily_returns.columns)
    if missing_cols:
        ui.notify(f"Missing columns: {missing_cols}", type="negative")
        return

    try:
        sorted_df = daily_returns.sort("date").filter(pl.col("return").is_finite())
        if sorted_df.height == 0:
            ui.label("No valid return data for underwater chart").classes(
                "text-gray-500 text-center p-4"
            )
            return

        # Compute wealth index and drawdown using shared helper
        chart_df = _compute_wealth_and_drawdown(sorted_df)
        chart_pd = chart_df.select(["date", "drawdown"]).to_pandas()

        # Get drawdown periods for annotations
        periods = compute_drawdown_periods(daily_returns, min_depth=0.05)

        fig = go.Figure()

        # Main underwater area chart
        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["drawdown"] * 100,
                mode="lines",
                name="Drawdown",
                line={"color": "#d62728", "width": 1.5},
                fill="tozeroy",
                fillcolor="rgba(214, 39, 40, 0.3)",
                hovertemplate="%{x}<br>Drawdown: %{y:.1f}%<extra></extra>",
            )
        )

        # Add zero line
        fig.add_hline(y=0, line_dash="solid", line_color="gray", line_width=1)

        # Annotate top drawdowns
        for i, period in enumerate(periods[:3]):  # Top 3
            duration_label = (
                f"{period.duration_days} days"
                if period.recovery_date
                else f"{period.duration_days}+ days"
            )
            fig.add_annotation(
                x=period.trough_date,
                y=period.max_drawdown * 100,
                text=f"{period.max_drawdown * 100:.1f}%<br>{duration_label}",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=-30 - (i * 15),  # Stagger annotations
                font={"size": 10},
            )

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            height=height,
            showlegend=False,
            hovermode="x unified",
            yaxis={"tickformat": ".1f", "ticksuffix": "%", "autorange": True},
        )

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(
            "Underwater chart rendering failed",
            extra={"chart": "underwater", "error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 text-center p-4")


def render_drawdown_periods_table(
    periods: list[DrawdownPeriod] | None,
    top_n: int = 5,
) -> None:
    """Render table of top drawdown periods.

    Duration shown in calendar days (includes weekends/holidays).

    Args:
        periods: List of DrawdownPeriod from compute_drawdown_periods().
        top_n: Maximum number of periods to show.
    """
    if not periods:
        ui.label("No significant drawdown periods found").classes("text-gray-500 text-center p-4")
        return

    # Take top N by severity (already sorted)
    display_periods = periods[:top_n]

    columns = [
        {"name": "rank", "label": "#", "field": "rank", "align": "center"},
        {"name": "peak", "label": "Peak", "field": "peak", "align": "left"},
        {"name": "trough", "label": "Trough", "field": "trough", "align": "left"},
        {"name": "recovery", "label": "Recovery", "field": "recovery", "align": "left"},
        {"name": "depth", "label": "Depth", "field": "depth", "align": "right"},
        {
            "name": "duration",
            "label": "Duration (cal. days)",
            "field": "duration",
            "align": "right",
        },
    ]

    rows = []
    for i, p in enumerate(display_periods, 1):
        recovery_str = str(p.recovery_date) if p.recovery_date else "Ongoing"
        duration_str = str(p.duration_days) if p.recovery_date else f"{p.duration_days}+"

        rows.append(
            {
                "rank": i,
                "peak": str(p.peak_date),
                "trough": str(p.trough_date),
                "recovery": recovery_str,
                "depth": f"{p.max_drawdown * 100:.1f}%",
                "duration": duration_str,
            }
        )

    ui.table(columns=columns, rows=rows, row_key="rank").classes("w-full")


__all__ = [
    "render_drawdown_chart",
    "DrawdownPeriod",
    "compute_drawdown_periods",
    "render_drawdown_underwater",
    "render_drawdown_periods_table",
]
