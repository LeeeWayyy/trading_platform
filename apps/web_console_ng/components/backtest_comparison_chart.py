"""Backtest comparison charts and metrics diff (P6T12.2).

Provides:
- Equity curve overlay for 2-5 backtests (Plotly)
- Color-coded metrics diff table (green = best, red = worst)
- Tracking error vs baseline display

Used by ``_render_backtest_results()`` in ``backtest.py`` when
comparison mode is active.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

from libs.analytics.metrics import compute_tracking_error
from libs.trading.backtest.cost_model import CostSummary

# ---------------------------------------------------------------------------
# Metric directionality: True = higher is better, False = lower is better
# ---------------------------------------------------------------------------
HIGHER_IS_BETTER: dict[str, bool] = {
    "Mean IC": True,
    "ICIR": True,
    "Hit Rate": True,
    "Coverage": True,
    "Total Return": True,
    "Sharpe": True,
    "Max Drawdown": False,
    "Avg Turnover": False,
    "Total Cost": False,
}

# Chart colour palette for up to 5 backtests
CHART_COLORS = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
]


# ---------------------------------------------------------------------------
# Metric computation helpers
# ---------------------------------------------------------------------------
def compute_max_drawdown(returns: list[float]) -> float | None:
    """Max drawdown from a daily return series.

    ``E_t = cumprod(1 + r_t)``, ``DD_t = E_t / max(E_{0..t}) - 1``,
    max drawdown = ``abs(min(DD_t))``.  Returned as positive fraction.
    """
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r
        if equity > peak:
            peak = equity
        dd = (equity / peak) - 1.0
        if dd < max_dd:
            max_dd = dd
    return abs(max_dd) if max_dd < 0.0 else 0.0


def compute_total_return(returns: list[float]) -> float | None:
    """Compounded total return: ``prod(1 + r) - 1``."""
    if not returns:
        return None
    cumulative = 1.0
    for r in returns:
        cumulative *= 1.0 + r
    return cumulative - 1.0


def compute_sharpe(returns: list[float]) -> float | None:
    """Annualized Sharpe ratio: ``mean(r) / std(r, ddof=1) * sqrt(252)``.

    Returns ``None`` if fewer than 2 observations or ``std == 0``.
    """
    n = len(returns)
    if n < 2:
        return None
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(variance)
    if std_r == 0.0:
        return None
    return (mean_r / std_r) * math.sqrt(252)


# ---------------------------------------------------------------------------
# Equity Curve Overlay
# ---------------------------------------------------------------------------
def render_comparison_equity_curves(
    returns_map: dict[str, pl.DataFrame],
    basis_label: str = "gross",
) -> None:
    """Render overlaid cumulative return curves for compared backtests.

    Args:
        returns_map: Keys are job display labels, values are DataFrames
            with ``{date, return}`` schema.
        basis_label: ``"net"`` or ``"gross"`` for chart title.
    """
    if len(returns_map) < 2:
        ui.label("Select at least 2 backtests with return data").classes("text-gray-500")
        return

    # Inner join on dates across all backtests
    dfs = list(returns_map.values())
    labels = list(returns_map.keys())

    # Find common dates via sequential inner join
    common_dates: set[date] = set(dfs[0]["date"].to_list())
    for df in dfs[1:]:
        common_dates &= set(df["date"].to_list())

    if len(common_dates) == 0:
        ui.label("No overlapping dates for chart").classes("text-amber-600")
        return

    sorted_dates = sorted(common_dates)
    min_date = sorted_dates[0]
    max_date = sorted_dates[-1]

    fig = go.Figure()

    for i, (label, df) in enumerate(zip(labels, dfs, strict=True)):
        # Filter to common dates and sort
        aligned = df.filter(pl.col("date").is_in(sorted_dates)).sort("date")

        # Prepend synthetic day-0 row (return = 0)
        day_before = min_date - timedelta(days=1)
        baseline_row = pl.DataFrame(
            {"date": [day_before], "return": [0.0]},
            schema={"date": pl.Date, "return": pl.Float64},
        )
        aligned = pl.concat([baseline_row, aligned])

        # Compute cumulative return: cumprod(1 + r) - 1
        cum_returns = aligned.with_columns(
            (pl.col("return") + 1.0).cum_prod().alias("cumulative")
        ).with_columns((pl.col("cumulative") - 1.0).alias("cumulative"))

        fig.add_trace(
            go.Scatter(
                x=cum_returns["date"].to_list(),
                y=cum_returns["cumulative"].to_list(),
                mode="lines",
                name=label,
                line={"color": CHART_COLORS[i % len(CHART_COLORS)]},
            )
        )

    fig.update_layout(
        title=f"Cumulative Returns Comparison ({basis_label})",
        xaxis_title="Date",
        yaxis_title="Cumulative Return",
        yaxis_tickformat=".1%",
        legend={"orientation": "h", "y": -0.15},
        height=450,
        margin={"l": 50, "r": 20, "t": 40, "b": 60},
    )
    ui.plotly(fig).classes("w-full")
    ui.label(
        f"Overlapping period: {min_date} to {max_date}, {len(sorted_dates)} dates"
    ).classes("text-xs text-gray-500 mt-1")


# ---------------------------------------------------------------------------
# Metrics Diff Table
# ---------------------------------------------------------------------------
def render_comparison_metrics_diff(
    metrics_list: list[dict[str, Any]],
    show_cost_column: bool = True,
) -> None:
    """Render color-coded metrics diff table for compared backtests.

    Each dict in ``metrics_list`` must contain:
    - ``label``: Display name for the backtest
    - Metric keys (all optional, ``None`` renders as "N/A"):
      ``Mean IC``, ``ICIR``, ``Hit Rate``, ``Coverage``,
      ``Total Return``, ``Sharpe``, ``Max Drawdown``,
      ``Avg Turnover``, ``Total Cost``

    Args:
        metrics_list: List of metric dicts, one per backtest.
        show_cost_column: Whether to include the Total Cost column.
    """
    if len(metrics_list) < 2:
        return

    metric_keys = [
        "Mean IC", "ICIR", "Hit Rate", "Coverage",
        "Total Return", "Sharpe", "Max Drawdown", "Avg Turnover",
    ]
    if show_cost_column:
        metric_keys.append("Total Cost")

    # Build columns
    columns: list[dict[str, Any]] = [
        {"name": "metric", "label": "Metric", "field": "metric", "align": "left"},
    ]
    for i, m in enumerate(metrics_list):
        columns.append({
            "name": f"bt_{i}",
            "label": m.get("label", f"Backtest {i + 1}"),
            "field": f"bt_{i}",
            "align": "right",
        })

    # Build rows with color coding
    rows: list[dict[str, Any]] = []
    for key in metric_keys:
        row: dict[str, Any] = {"metric": key}
        values: list[tuple[int, float | None]] = []

        for i, m in enumerate(metrics_list):
            val = m.get(key)
            values.append((i, val))
            row[f"bt_{i}"] = _format_metric(key, val)

        # Color coding: find best and worst
        numeric_vals = [(idx, v) for idx, v in values if v is not None]
        if len(numeric_vals) >= 2 and key in HIGHER_IS_BETTER:
            higher_better = HIGHER_IS_BETTER[key]
            sorted_vals = sorted(numeric_vals, key=lambda x: x[1], reverse=higher_better)
            best_idx = sorted_vals[0][0]
            worst_idx = sorted_vals[-1][0]
            # Skip color-coding when best and worst are tied
            # (same value → same rank, no meaningful differentiation)
            if best_idx != worst_idx and sorted_vals[0][1] != sorted_vals[-1][1]:
                from html import escape as _esc

                row[f"bt_{best_idx}"] = f'<span style="color: #16a34a; font-weight: 600">{_esc(str(row[f"bt_{best_idx}"]))}</span>'
                row[f"bt_{worst_idx}"] = f'<span style="color: #dc2626; font-weight: 600">{_esc(str(row[f"bt_{worst_idx}"]))}</span>'

        rows.append(row)

    table = ui.table(columns=columns, rows=rows).classes("w-full")
    table.props('dense flat :rows-per-page-options="[0]"')
    # Enable HTML rendering in cells
    for i in range(len(metrics_list)):
        table.add_slot(
            f"body-cell-bt_{i}",
            '<q-td :props="props"><span v-html="props.value"></span></q-td>',
        )

    ui.label(
        "Metrics reflect each backtest's full period; chart shows overlapping dates only."
    ).classes("text-xs text-gray-400 mt-1")


def _format_metric(key: str, value: float | None) -> str:
    """Format a single metric value for display."""
    if value is None:
        return "N/A"
    if key in ("Hit Rate", "Coverage"):
        return f"{value * 100:.1f}%"
    if key in ("Total Return",):
        return f"{value * 100:.2f}%"
    if key in ("Max Drawdown",):
        return f"{value * 100:.2f}%"
    if key in ("Avg Turnover",):
        return f"{value:.2%}"
    if key == "Total Cost":
        return f"${value:,.0f}"
    if key in ("Mean IC",):
        return f"{value:.4f}"
    if key in ("ICIR", "Sharpe"):
        return f"{value:.2f}"
    return f"{value:.4f}"


# ---------------------------------------------------------------------------
# Tracking Error Display
# ---------------------------------------------------------------------------
def render_tracking_error_vs_baseline(
    returns_map: dict[str, pl.DataFrame],
    labels: list[str],
) -> None:
    """Render tracking error of each backtest vs the baseline.

    The first entry in ``labels`` with available returns is used as
    the baseline.  Each subsequent backtest gets a pairwise TE.

    Args:
        returns_map: Keys are labels, values are DataFrames with
            ``{date, return}`` schema.
        labels: Ordered list of labels (first with data = baseline).
    """
    # Find baseline: first label with available returns
    baseline_label: str | None = None
    baseline_df: pl.DataFrame | None = None
    for lbl in labels:
        if lbl in returns_map and len(returns_map[lbl]) > 0:
            baseline_label = lbl
            baseline_df = returns_map[lbl]
            break

    if baseline_label is None or baseline_df is None:
        ui.label("Tracking error unavailable - no return data").classes(
            "text-gray-500 text-sm"
        )
        return

    with ui.column().classes("gap-1"):
        ui.label(f"Tracking Error vs Baseline ({baseline_label})").classes(
            "text-sm font-semibold"
        )
        for lbl in labels:
            if lbl == baseline_label:
                continue
            if lbl not in returns_map or len(returns_map[lbl]) == 0:
                ui.label(f"  {lbl}: N/A (no return data)").classes("text-xs text-gray-400")
                continue

            te = compute_tracking_error(returns_map[lbl], baseline_df)
            if te is None:
                ui.label(f"  {lbl}: Insufficient data").classes("text-xs text-gray-400")
            else:
                ui.label(f"  {lbl}: {te:.2%} (annualized)").classes("text-xs")


# ---------------------------------------------------------------------------
# Metric extraction helper
# ---------------------------------------------------------------------------
def build_comparison_metrics(
    job: dict[str, Any],
    label: str,
    return_series: list[float] | None,
    cost_summary_raw: dict[str, Any] | None,
    basis: str,
) -> dict[str, Any]:
    """Build a metric dict for one backtest in the comparison.

    Uses DB summary fields where available and computes from return
    series as fallback.

    Args:
        job: DB job row from ``_get_user_jobs_sync()``.
        label: Display label for the backtest.
        return_series: Daily returns list (may be None if unavailable).
        cost_summary_raw: Raw ``cost_summary`` dict from DB (may be None).
        basis: ``"net"`` or ``"gross"`` - which basis is active.
    """
    metrics: dict[str, Any] = {"label": label}

    # DB summary fields (always available for completed jobs)
    metrics["Mean IC"] = job.get("mean_ic")
    metrics["ICIR"] = job.get("icir")
    metrics["Hit Rate"] = job.get("hit_rate")
    metrics["Coverage"] = job.get("coverage")
    metrics["Avg Turnover"] = job.get("average_turnover")

    # Cost summary fields
    cs = None
    if cost_summary_raw and isinstance(cost_summary_raw, dict):
        try:
            cs = CostSummary.from_dict(cost_summary_raw)
        except (KeyError, TypeError, ValueError):
            cs = None

    # Computed metrics: prefer cost_summary, fallback to return series
    if cs is not None:
        if basis == "net":
            metrics["Total Return"] = cs.total_net_return
            metrics["Sharpe"] = cs.net_sharpe
            metrics["Max Drawdown"] = cs.net_max_drawdown
        else:
            metrics["Total Return"] = cs.total_gross_return
            metrics["Sharpe"] = cs.gross_sharpe
            metrics["Max Drawdown"] = cs.gross_max_drawdown

        # Total Cost: only show if key exists in raw dict
        if cost_summary_raw is not None and "total_cost_usd" in cost_summary_raw:
            metrics["Total Cost"] = cs.total_cost_usd
        else:
            metrics["Total Cost"] = None
    else:
        metrics["Total Cost"] = None

    # Fallback to return series for metrics still None
    if return_series is not None:
        if metrics.get("Total Return") is None:
            metrics["Total Return"] = compute_total_return(return_series)
        if metrics.get("Sharpe") is None:
            metrics["Sharpe"] = compute_sharpe(return_series)
        if metrics.get("Max Drawdown") is None:
            metrics["Max Drawdown"] = compute_max_drawdown(return_series)

    return metrics


# ---------------------------------------------------------------------------
# Live vs Backtest Overlay Chart (T12.3)
# ---------------------------------------------------------------------------
def render_live_vs_backtest_overlay(
    overlay_result: Any,
    basis_label: str = "net",
) -> None:
    """Render the live vs backtest overlay chart.

    Args:
        overlay_result: ``OverlayResult`` from ``LiveVsBacktestAnalyzer``.
        basis_label: ``"net"`` or ``"gross"`` for chart title.
    """
    from libs.analytics.live_vs_backtest import AlertLevel

    result = overlay_result

    if len(result.live_cumulative) == 0 or len(result.backtest_cumulative) == 0:
        ui.label(result.alert_message).classes("text-amber-600 text-sm")
        return

    fig = go.Figure()

    # Backtest curve (dashed)
    bt_dates = result.backtest_cumulative["date"].to_list()
    bt_cum = result.backtest_cumulative["cumulative_return"].to_list()
    fig.add_trace(
        go.Scatter(
            x=bt_dates,
            y=bt_cum,
            mode="lines",
            name="Backtest",
            line={"color": "#9ca3af", "dash": "dash", "width": 2},
        )
    )

    # Live curve (solid)
    live_dates = result.live_cumulative["date"].to_list()
    live_cum = result.live_cumulative["cumulative_return"].to_list()
    fig.add_trace(
        go.Scatter(
            x=live_dates,
            y=live_cum,
            mode="lines",
            name="Live",
            line={"color": "#2563eb", "width": 2},
        )
    )

    # Shaded divergence region
    fig.add_trace(
        go.Scatter(
            x=bt_dates + live_dates[::-1],
            y=bt_cum + live_cum[::-1],
            fill="toself",
            fillcolor="rgba(239, 68, 68, 0.1)",
            line={"width": 0},
            showlegend=False,
            name="Divergence",
        )
    )

    fig.update_layout(
        title=f"Live vs Backtest Overlay ({basis_label})",
        xaxis_title="Date",
        yaxis_title="Cumulative Return",
        yaxis_tickformat=".1%",
        legend={"orientation": "h", "y": -0.15},
        height=450,
        margin={"l": 50, "r": 20, "t": 40, "b": 60},
    )
    ui.plotly(fig).classes("w-full")

    # Alert badge
    alert_colors = {
        AlertLevel.NONE: ("bg-green-100 text-green-700", "check_circle"),
        AlertLevel.YELLOW: ("bg-amber-100 text-amber-700", "warning"),
        AlertLevel.RED: ("bg-red-100 text-red-700", "error"),
    }
    badge_class, badge_icon = alert_colors.get(
        result.alert_level,
        ("bg-gray-100 text-gray-700", "info"),
    )

    with ui.row().classes("items-center gap-2 mt-2"):
        ui.icon(badge_icon).classes(badge_class.split()[1])
        ui.badge(result.alert_message).classes(badge_class)

    # Metrics summary
    with ui.row().classes("gap-4 mt-1 text-xs"):
        if result.tracking_error_annualized is not None:
            ui.label(f"TE: {result.tracking_error_annualized:.2%} (ann.)")
        if result.cumulative_divergence is not None:
            ui.label(f"Cum. Divergence: {result.cumulative_divergence:.2%}")
        if result.divergence_start_date is not None:
            ui.label(f"Divergence start: {result.divergence_start_date}")

    # Date coverage
    if len(live_dates) > 0:
        ui.label(
            f"Alignment window: {live_dates[0]} to {live_dates[-1]}, "
            f"{len(live_dates)} dates"
        ).classes("text-xs text-gray-500 mt-1")


__all__ = [
    "build_comparison_metrics",
    "compute_max_drawdown",
    "compute_sharpe",
    "compute_total_return",
    "render_comparison_equity_curves",
    "render_comparison_metrics_diff",
    "render_live_vs_backtest_overlay",
    "render_tracking_error_vs_baseline",
]
