"""Factor contribution chart component for NiceGUI.

Visualizes factor attribution decomposition from Fama-French analysis.
P6T10: Track 10 - Quantile & Attribution Analytics
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import plotly.graph_objects as go
from nicegui import ui

if TYPE_CHECKING:
    from libs.platform.analytics.attribution import AttributionResult

logger = logging.getLogger(__name__)

# Human-readable factor names
FACTOR_DISPLAY_NAMES = {
    "mkt_rf": "Market (Mkt-RF)",
    "smb": "Size (SMB)",
    "hml": "Value (HML)",
    "rmw": "Profitability (RMW)",
    "cma": "Investment (CMA)",
    "umd": "Momentum (UMD)",
}

# Factor colors
FACTOR_COLORS = {
    "mkt_rf": "#1f77b4",  # Blue
    "smb": "#ff7f0e",  # Orange
    "hml": "#2ca02c",  # Green
    "rmw": "#d62728",  # Red
    "cma": "#9467bd",  # Purple
    "umd": "#8c564b",  # Brown
}


def _get_factor_display_name(factor: str) -> str:
    """Get human-readable display name for factor."""
    return FACTOR_DISPLAY_NAMES.get(factor, factor)


def _get_factor_color(factor: str) -> str:
    """Get color for factor."""
    return FACTOR_COLORS.get(factor, "#7f7f7f")


def render_attribution_summary(
    result: AttributionResult | None,
) -> None:
    """Render summary card with key attribution metrics.

    Args:
        result: AttributionResult from FactorAttribution.fit().
    """
    if result is None:
        ui.label("No attribution data available").classes("text-gray-500 p-4")
        return

    with ui.card().classes("w-full p-4"):
        ui.label("Factor Attribution Summary").classes("text-lg font-bold mb-4")

        # Alpha section
        with ui.row().classes("gap-8 mb-4"):
            with ui.column():
                ui.label("Alpha (Annualized)").classes("text-gray-500 text-sm")
                alpha_val = result.alpha_annualized_bps
                alpha_color = "text-green-600" if alpha_val >= 0 else "text-red-600"
                ui.label(f"{alpha_val:+.1f} bps").classes(f"text-2xl font-bold {alpha_color}")

            with ui.column():
                ui.label("Alpha t-stat").classes("text-gray-500 text-sm")
                t_stat = result.alpha_t_stat
                sig_marker = "*" if abs(t_stat) >= 1.96 else ""
                ui.label(f"{t_stat:.2f}{sig_marker}").classes("text-xl")

            with ui.column():
                ui.label("R-squared (adj)").classes("text-gray-500 text-sm")
                ui.label(f"{result.r_squared_adj:.2%}").classes("text-xl")

            with ui.column():
                ui.label("Observations").classes("text-gray-500 text-sm")
                ui.label(f"{result.n_observations}").classes("text-xl")

        # Diagnostics
        with ui.row().classes("gap-4 text-sm text-gray-600"):
            ui.label(f"Durbin-Watson: {result.durbin_watson:.2f}")
            ui.label(f"Residual Vol: {result.residual_vol_annualized:.2%}")

        # Warnings
        if result.multicollinearity_warnings:
            with ui.expansion("Warnings", icon="warning").classes("mt-4 text-yellow-600"):
                for warning in result.multicollinearity_warnings:
                    ui.label(warning).classes("text-sm")


def render_factor_loadings_chart(
    result: AttributionResult | None,
    height: int = 350,
) -> None:
    """Render horizontal bar chart of factor loadings (betas).

    Args:
        result: AttributionResult from FactorAttribution.fit().
        height: Chart height in pixels.
    """
    if result is None:
        ui.label("No factor loading data available").classes("text-gray-500 p-4")
        return

    if not result.betas:
        ui.label("No factor loadings in result").classes("text-gray-500 p-4")
        return

    try:
        # Prepare data
        factors = []
        betas = []
        t_stats = []
        colors = []

        for factor, beta in result.betas.items():
            if beta is not None and beta == beta:  # NaN check (NaN != NaN)
                factors.append(_get_factor_display_name(factor))
                betas.append(beta)
                t_stats.append(result.beta_t_stats.get(factor, 0))
                colors.append(_get_factor_color(factor))

        if not factors:
            ui.label("All factor loadings are NaN").classes("text-gray-500 p-4")
            return

        # Reverse for horizontal bar chart (first factor at top)
        factors = factors[::-1]
        betas = betas[::-1]
        t_stats = t_stats[::-1]
        colors = colors[::-1]

        # Create figure
        fig = go.Figure()

        fig.add_trace(
            go.Bar(
                y=factors,
                x=betas,
                orientation="h",
                marker_color=colors,
                text=[f"{b:.2f} (t={t:.1f})" for b, t in zip(betas, t_stats, strict=False)],
                textposition="outside",
                hovertemplate="%{y}: %{x:.3f}<extra></extra>",
            )
        )

        # Add zero reference line
        fig.add_vline(x=0, line_width=1, line_color="gray", line_dash="dash")

        fig.update_layout(
            title="Factor Loadings (Beta)",
            xaxis_title="Beta",
            yaxis_title="",
            height=height,
            showlegend=False,
            margin={"l": 150, "r": 80, "t": 60, "b": 40},
        )

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, TypeError) as e:
        logger.warning(
            "Factor loadings chart failed",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 p-4")


def render_factor_table(
    result: AttributionResult | None,
) -> None:
    """Render detailed table of factor statistics.

    Args:
        result: AttributionResult from FactorAttribution.fit().
    """
    if result is None or not result.betas:
        ui.label("No factor data available").classes("text-gray-500 p-4")
        return

    columns = [
        {"name": "factor", "label": "Factor", "field": "factor", "align": "left"},
        {"name": "beta", "label": "Beta", "field": "beta", "align": "right"},
        {"name": "t_stat", "label": "t-stat", "field": "t_stat", "align": "right"},
        {"name": "p_value", "label": "p-value", "field": "p_value", "align": "right"},
        {"name": "sig", "label": "Sig.", "field": "sig", "align": "center"},
    ]

    rows = []
    for factor, beta in result.betas.items():
        t_stat = result.beta_t_stats.get(factor, 0)
        p_value = result.beta_p_values.get(factor, 1)

        # Significance stars
        if p_value < 0.001:
            sig = "***"
        elif p_value < 0.01:
            sig = "**"
        elif p_value < 0.05:
            sig = "*"
        else:
            sig = ""

        rows.append(
            {
                "factor": _get_factor_display_name(factor),
                "beta": f"{beta:.3f}" if beta == beta else "NaN",  # NaN check
                "t_stat": f"{t_stat:.2f}" if t_stat == t_stat else "NaN",
                "p_value": f"{p_value:.4f}" if p_value == p_value else "NaN",
                "sig": sig,
            }
        )

    ui.table(columns=columns, rows=rows, row_key="factor").classes("w-full")

    # Legend for significance
    ui.label("Significance: * p<0.05, ** p<0.01, *** p<0.001").classes(
        "text-gray-500 text-xs mt-2"
    )


__all__ = [
    "render_attribution_summary",
    "render_factor_loadings_chart",
    "render_factor_table",
    "FACTOR_DISPLAY_NAMES",
]
