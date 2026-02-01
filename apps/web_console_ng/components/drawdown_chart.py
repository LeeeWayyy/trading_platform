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

    # Step 1: Compute wealth index and running maximum (peak tracker)
    # Initialize at W₀=1.0 to capture first-day drops
    analysis_df = sorted_df.with_columns(
        [
            (1 + pl.col("return")).cum_prod().alias("wealth"),
        ]
    ).with_columns(
        [
            # Running max starting from 1.0 (W₀)
            pl.concat([pl.lit(1.0), pl.col("wealth")])
            .slice(0, pl.len())
            .cum_max()
            .alias("running_peak"),
        ]
    )

    # Step 2: Compute drawdown at each point
    analysis_df = analysis_df.with_columns(
        [
            (pl.col("wealth") / pl.col("running_peak") - 1).alias("drawdown"),
            # Flag: are we in a drawdown (wealth < running_peak)?
            (pl.col("wealth") < pl.col("running_peak")).alias("in_drawdown"),
        ]
    )

    # Step 3: Identify drawdown period boundaries using run-length encoding
    # A new period starts when in_drawdown changes from False to True
    analysis_df = analysis_df.with_columns(
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
    )

    # Step 4: Assign period IDs using cumsum of period starts
    analysis_df = analysis_df.with_columns(
        [
            pl.col("period_start").cum_sum().alias("period_id"),
        ]
    )

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

    # Step 6: Find peak date (the date just before drawdown started, or first date if starts at beginning)
    # Join back with analysis_df to find the peak
    dates_list = analysis_df["date"].to_list()
    wealth_list = analysis_df["wealth"].to_list()
    running_peak_list = analysis_df["running_peak"].to_list()
    # O(1) date lookup dict instead of O(N) list.index()
    date_to_idx = {d: i for i, d in enumerate(dates_list)}

    periods: list[DrawdownPeriod] = []

    for row in period_stats.iter_rows(named=True):
        max_dd = row["max_drawdown"]
        trough_date = row["trough_date"]
        first_dd_date = row["first_drawdown_date"]
        last_dd_date = row["last_drawdown_date"]

        # Skip if drawdown doesn't meet minimum depth threshold
        if abs(max_dd) < min_depth:
            continue

        # Find peak date: the last date before first_drawdown_date where wealth == running_peak
        # Or use first date if drawdown starts immediately
        first_dd_idx = date_to_idx.get(first_dd_date, 0)
        peak_date_val = dates_list[0]  # Default to first date (W₀=1.0 peak)
        peak_wealth_val = 1.0

        if first_dd_idx > 0:
            # Look back to find the peak
            for j in range(first_dd_idx - 1, -1, -1):
                if wealth_list[j] >= running_peak_list[first_dd_idx - 1]:
                    peak_date_val = dates_list[j]
                    peak_wealth_val = wealth_list[j]
                    break

        # Find recovery date: first date after last_drawdown_date where in_drawdown=False
        # Check if there's a recovery
        last_dd_idx = date_to_idx.get(last_dd_date, len(dates_list) - 1)
        recovery_date_val: date | None = None

        if last_dd_idx < len(dates_list) - 1:
            # Check if next point is a recovery
            next_wealth = wealth_list[last_dd_idx + 1]
            if next_wealth >= peak_wealth_val:
                recovery_date_val = dates_list[last_dd_idx + 1]

        # Compute duration
        duration = (
            (recovery_date_val - peak_date_val).days
            if recovery_date_val
            else (dates_list[-1] - peak_date_val).days
        )

        periods.append(
            DrawdownPeriod(
                peak_date=peak_date_val,
                trough_date=trough_date,
                recovery_date=recovery_date_val,
                max_drawdown=max_dd,
                duration_days=duration,
            )
        )

    # Sort by severity (worst first)
    return sorted(periods, key=lambda p: p.max_drawdown)


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

        # Compute wealth index and drawdown
        # Initialize running_max from W₀=1.0 to capture first-day drops
        # Use with_columns context to avoid mypy issues with standalone concat
        temp_df = sorted_df.with_columns([
            (1 + pl.col("return")).cum_prod().alias("wealth"),
        ]).with_columns([
            pl.concat([pl.lit(1.0), pl.col("wealth")])
            .slice(0, pl.len())
            .cum_max()
            .alias("running_max"),
        ]).with_columns([
            pl.when(pl.col("running_max") == 0)
            .then(-1.0)
            .otherwise(pl.col("wealth") / pl.col("running_max") - 1)
            .alias("drawdown"),
        ])
        wealth = temp_df["wealth"]
        drawdown = temp_df["drawdown"]

        chart_df = sorted_df.with_columns(
            wealth.alias("wealth"),
            drawdown.alias("drawdown"),
        )
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
