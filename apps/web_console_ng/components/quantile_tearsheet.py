"""Quantile Tearsheet component for NiceGUI.

Visualizes signal validation using Rank IC and quantile spread metrics.
P6T10: Track 10 - Quantile & Attribution Analytics
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import plotly.graph_objects as go
from nicegui import ui

if TYPE_CHECKING:
    from libs.trading.backtest.quantile_analysis import QuantileResult

logger = logging.getLogger(__name__)


def render_rank_ic_summary(result: QuantileResult | None) -> None:
    """Render Rank IC summary card.

    Args:
        result: QuantileResult from QuantileAnalyzer.
    """
    if result is None:
        ui.label("No Rank IC data available").classes("text-gray-500 p-4")
        return

    with ui.card().classes("w-full p-4"):
        ui.label("Rank IC (Information Coefficient)").classes("text-lg font-bold mb-4")

        # Main IC metrics
        with ui.row().classes("gap-8 mb-4"):
            with ui.column():
                ui.label("Mean Rank IC").classes("text-gray-500 text-sm")
                ic_color = "text-green-600" if result.mean_rank_ic > 0 else "text-red-600"
                ui.label(f"{result.mean_rank_ic:.4f}").classes(f"text-2xl font-bold {ic_color}")

            with ui.column():
                ui.label("t-statistic").classes("text-gray-500 text-sm")
                # Significance marker
                sig_marker = ""
                if abs(result.rank_ic_t_stat) >= 2.58:
                    sig_marker = " ***"
                elif abs(result.rank_ic_t_stat) >= 1.96:
                    sig_marker = " **"
                elif abs(result.rank_ic_t_stat) >= 1.65:
                    sig_marker = " *"
                ui.label(f"{result.rank_ic_t_stat:.2f}{sig_marker}").classes("text-xl")

            with ui.column():
                ui.label("IC Std").classes("text-gray-500 text-sm")
                ui.label(f"{result.rank_ic_std:.4f}").classes("text-xl")

            with ui.column():
                ui.label("IC > 0").classes("text-gray-500 text-sm")
                pct_color = "text-green-600" if result.rank_ic_positive_pct > 50 else "text-red-600"
                ui.label(f"{result.rank_ic_positive_pct:.1f}%").classes(f"text-xl {pct_color}")

        # Metadata
        with ui.row().classes("gap-4 text-sm text-gray-600"):
            ui.label(f"Valid Dates: {result.n_dates}")
            ui.label(f"Skipped: {result.n_dates_skipped}")
            if result.period_start and result.period_end:
                ui.label(f"Period: {result.period_start} to {result.period_end}")

        # IC interpretation
        with ui.card().classes("w-full bg-blue-50 p-3 mt-4"):
            ui.label("Interpretation").classes("text-sm font-semibold text-blue-800 mb-1")
            if result.mean_rank_ic > 0.05:
                interpretation = "Strong predictive signal (IC > 0.05)"
                icon = "check_circle"
                color = "text-green-700"
            elif result.mean_rank_ic > 0.02:
                interpretation = "Moderate predictive signal (0.02 < IC < 0.05)"
                icon = "info"
                color = "text-blue-700"
            elif result.mean_rank_ic > 0:
                interpretation = "Weak positive signal (0 < IC < 0.02)"
                icon = "warning"
                color = "text-yellow-700"
            else:
                interpretation = "Negative or no predictive value (IC <= 0)"
                icon = "error"
                color = "text-red-700"

            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes(color)
                ui.label(interpretation).classes(f"text-sm {color}")


def render_quantile_returns_chart(
    result: QuantileResult | None,
    height: int = 350,
) -> None:
    """Render horizontal bar chart of quantile returns.

    Args:
        result: QuantileResult from QuantileAnalyzer.
        height: Chart height in pixels.
    """
    if result is None or not result.quantile_returns:
        ui.label("No quantile return data available").classes("text-gray-500 p-4")
        return

    try:
        # Prepare data
        quantiles = sorted(result.quantile_returns.keys())
        returns = [result.quantile_returns[q] * 100 for q in quantiles]  # Convert to %

        # Color gradient: red for lowest, green for highest
        n = len(quantiles)
        colors = []
        for i, _ in enumerate(quantiles):
            # Gradient from red (Q1) to green (Q5)
            r = int(255 * (1 - i / (n - 1))) if n > 1 else 128
            g = int(255 * (i / (n - 1))) if n > 1 else 128
            colors.append(f"rgb({r}, {g}, 100)")

        # Quantile labels
        labels = []
        for q in quantiles:
            if q == 1:
                labels.append(f"Q{q} (Shorts)")
            elif q == n:
                labels.append(f"Q{q} (Longs)")
            else:
                labels.append(f"Q{q}")

        fig = go.Figure()

        fig.add_trace(
            go.Bar(
                y=labels,
                x=returns,
                orientation="h",
                marker_color=colors,
                text=[f"{r:+.2f}%" for r in returns],
                textposition="outside",
                hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
            )
        )

        # Add zero line
        fig.add_vline(x=0, line_width=1, line_color="gray", line_dash="dash")

        fig.update_layout(
            title="Mean Forward Return by Quantile",
            xaxis_title="Mean Return (%)",
            yaxis_title="",
            height=height,
            showlegend=False,
            margin={"l": 120, "r": 80, "t": 60, "b": 40},
        )

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, TypeError) as e:
        logger.warning(
            "quantile_returns_chart_failed",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 p-4")


def render_long_short_spread(result: QuantileResult | None) -> None:
    """Render long/short spread summary.

    Args:
        result: QuantileResult from QuantileAnalyzer.
    """
    if result is None:
        ui.label("No spread data available").classes("text-gray-500 p-4")
        return

    with ui.card().classes("w-full p-4"):
        ui.label("Long/Short Spread").classes("text-lg font-bold mb-4")

        with ui.row().classes("gap-8 items-center"):
            with ui.column():
                ui.label("Spread (Q_high - Q_low)").classes("text-gray-500 text-sm")
                spread_pct = result.long_short_spread * 100
                spread_color = "text-green-600" if spread_pct > 0 else "text-red-600"
                ui.label(f"{spread_pct:+.2f}%").classes(f"text-2xl font-bold {spread_color}")

            # Min observations per quantile
            if result.n_observations_per_quantile:
                min_obs = min(result.n_observations_per_quantile.values())
                with ui.column():
                    ui.label("Min Obs/Quantile").classes("text-gray-500 text-sm")
                    obs_color = "text-green-600" if min_obs >= 100 else "text-yellow-600"
                    ui.label(f"{min_obs:,}").classes(f"text-xl {obs_color}")

        # Universe and signal info
        if result.universe_name or result.signal_name:
            with ui.row().classes("gap-4 text-sm text-gray-600 mt-4"):
                if result.signal_name:
                    ui.label(f"Signal: {result.signal_name}")
                if result.universe_name:
                    ui.label(f"Universe: {result.universe_name}")


def render_quantile_observations_table(result: QuantileResult | None) -> None:
    """Render table of observations per quantile.

    Args:
        result: QuantileResult from QuantileAnalyzer.
    """
    if result is None or not result.n_observations_per_quantile:
        ui.label("No observation data available").classes("text-gray-500 p-4")
        return

    columns = [
        {"name": "quantile", "label": "Quantile", "field": "quantile", "align": "center"},
        {"name": "observations", "label": "Observations", "field": "observations", "align": "right"},
        {"name": "mean_return", "label": "Mean Return", "field": "mean_return", "align": "right"},
    ]

    rows = []
    quantiles = sorted(result.n_observations_per_quantile.keys())
    n = len(quantiles)

    for q in quantiles:
        # Label
        if q == 1:
            label = f"Q{q} (Shorts)"
        elif q == n:
            label = f"Q{q} (Longs)"
        else:
            label = f"Q{q}"

        # Return
        ret = result.quantile_returns.get(q, 0)
        ret_str = f"{ret * 100:+.2f}%"

        rows.append({
            "quantile": label,
            "observations": f"{result.n_observations_per_quantile[q]:,}",
            "mean_return": ret_str,
        })

    ui.table(columns=columns, rows=rows, row_key="quantile").classes("w-full")


def render_quantile_tearsheet(
    result: QuantileResult | None,
) -> None:
    """Render complete quantile tearsheet.

    Args:
        result: QuantileResult from QuantileAnalyzer.
    """
    if result is None:
        with ui.card().classes("w-full p-6"):
            ui.label("Quantile Analysis Not Available").classes("text-xl font-bold text-gray-600")
            ui.label(
                "Run the backtest with universe signals to enable quantile analysis."
            ).classes("text-gray-500")
        return

    # Rank IC Summary (primary metric)
    render_rank_ic_summary(result)

    ui.separator().classes("my-4")

    # Quantile Returns Chart
    with ui.column().classes("w-full"):
        render_quantile_returns_chart(result, height=350)

    ui.separator().classes("my-4")

    # Long/Short Spread
    render_long_short_spread(result)

    ui.separator().classes("my-4")

    # Observations Table
    with ui.column().classes("w-full"):
        ui.label("Quantile Statistics").classes("text-lg font-semibold mb-2")
        render_quantile_observations_table(result)


__all__ = [
    "render_rank_ic_summary",
    "render_quantile_returns_chart",
    "render_long_short_spread",
    "render_quantile_observations_table",
    "render_quantile_tearsheet",
]
