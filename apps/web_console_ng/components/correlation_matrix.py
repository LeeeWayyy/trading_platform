"""Signal correlation matrix visualization component for NiceGUI.

Renders correlation matrix heatmap for alpha signals.
Ported from apps/web_console/components/signal_correlation_matrix.py (Streamlit).
"""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

logger = logging.getLogger(__name__)


def render_correlation_matrix(
    corr_matrix: pl.DataFrame | None,
    title: str = "Signal Correlation Matrix",
    height: int = 500,
) -> None:
    """Render signal correlation matrix heatmap.

    Args:
        corr_matrix: Polars DataFrame with first column 'signal' and remaining columns as signals
            - Data source: AlphaExplorerService.compute_correlation()
        title: Chart title
        height: Chart height in pixels
    """
    if corr_matrix is None or corr_matrix.is_empty():
        ui.label("Not enough data to compute correlations.").classes(
            "text-gray-500 text-center p-4"
        )
        return

    if "signal" not in corr_matrix.columns:
        ui.label("Correlation matrix missing 'signal' column.").classes(
            "text-red-600 text-center p-4"
        )
        return

    try:
        corr_pd = corr_matrix.to_pandas()
        signal_names = corr_pd["signal"].tolist()
        corr_pd = corr_pd.set_index("signal")

        # Use go.Figure with go.Heatmap for consistent charting API
        fig = go.Figure(
            data=go.Heatmap(
                z=corr_pd.values,
                x=signal_names,
                y=signal_names,
                colorscale="RdYlGn",
                zmin=-1,
                zmax=1,
                text=[[f"{v:.2f}" for v in row] for row in corr_pd.values],
                texttemplate="%{text}",
                hovertemplate=(
                    "Signal: %{x}<br>Signal: %{y}<br>Correlation: %{z:.2f}<extra></extra>"
                ),
            )
        )

        fig.update_layout(
            title=title,
            margin={"l": 40, "r": 40, "t": 60, "b": 40},
            xaxis_title="Signal",
            yaxis_title="Signal",
            height=height,
        )

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(
            "Correlation matrix rendering failed - invalid data",
            extra={
                "chart": "correlation_matrix",
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 text-center p-4")


__all__ = ["render_correlation_matrix"]
