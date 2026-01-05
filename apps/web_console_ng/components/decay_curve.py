"""Decay curve visualization component for NiceGUI.

Renders signal decay curve showing IC at multiple horizons.
Ported from apps/web_console/components/decay_curve.py (Streamlit).
"""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

logger = logging.getLogger(__name__)


def render_decay_curve(
    decay_curve: pl.DataFrame | None,
    half_life: float | None = None,
    title: str = "Signal Decay Curve",
    height: int = 350,
) -> None:
    """Render decay curve showing IC at multiple horizons.

    Args:
        decay_curve: DataFrame with columns [horizon, ic, rank_ic]
            - Data source: AlphaExplorerService.get_decay_curve()
        half_life: Optional half-life in days for annotation
        title: Chart title
        height: Chart height in pixels
    """
    if decay_curve is None or decay_curve.is_empty():
        ui.label("No decay curve data available for this signal.").classes(
            "text-gray-500 text-center p-4"
        )
        return

    # Validate required columns
    required_cols = {"horizon", "ic", "rank_ic"}
    missing_cols = required_cols - set(decay_curve.columns)
    if missing_cols:
        ui.label(f"Data schema error: missing columns {missing_cols}").classes(
            "text-red-600 text-center p-4"
        )
        return

    try:
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=decay_curve["horizon"].to_list(),
                y=decay_curve["rank_ic"].to_list(),
                name="Rank IC",
                mode="lines+markers",
                line={"color": "blue", "width": 2},
                marker={"size": 8},
            )
        )

        fig.add_trace(
            go.Scatter(
                x=decay_curve["horizon"].to_list(),
                y=decay_curve["ic"].to_list(),
                name="Pearson IC",
                mode="lines+markers",
                line={"color": "gray", "width": 1, "dash": "dot"},
                marker={"size": 6},
            )
        )

        fig.add_hline(y=0, line_dash="dash", line_color="gray")

        if half_life is not None:
            fig.add_vline(x=half_life, line_dash="dot", line_color="red")
            fig.add_annotation(
                x=half_life,
                y=0.5,
                text=f"Half-life: {half_life:.1f}d",
                showarrow=True,
                arrowhead=2,
            )

        fig.update_layout(
            title=title,
            xaxis_title="Horizon (days)",
            yaxis_title="IC",
            legend={
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "xanchor": "right",
                "x": 1,
            },
            height=height,
            hovermode="x unified",
        )

        ui.plotly(fig).classes("w-full")

    except Exception:
        logger.exception("decay_curve_render_error")
        ui.notify("Unable to render decay curve. Please try again.", type="negative")


__all__ = ["render_decay_curve"]
