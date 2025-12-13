"""Factor exposure visualization component for risk dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from apps.web_console.utils.validators import validate_exposures
from libs.risk.factor_covariance import CANONICAL_FACTOR_ORDER

# Factor display names (human-readable)
FACTOR_DISPLAY_NAMES = {
    "momentum_12_1": "Momentum (12-1)",
    "book_to_market": "Book-to-Market",
    "roe": "ROE (Quality)",
    "log_market_cap": "Size (Market Cap)",
    "realized_vol": "Volatility",
}


def _get_display_name(factor_name: str) -> str:
    """Get human-readable display name for factor."""
    return FACTOR_DISPLAY_NAMES.get(factor_name, factor_name)


def _get_exposure_color(exposure: float) -> str:
    """Get color based on exposure direction: green positive, red negative."""
    return "#27AE60" if exposure >= 0 else "#E74C3C"


def render_factor_exposure(
    exposures: Sequence[dict[str, Any]],
) -> go.Figure | None:
    """Render horizontal bar chart of factor exposures.

    Args:
        exposures: List of dicts with 'factor_name' and 'exposure' keys

    Returns:
        Plotly Figure or None if no valid data
    """
    # Validate exposures before rendering
    valid_exposures = validate_exposures(list(exposures))

    if not valid_exposures:
        st.info("No factor exposure data available.")
        return None

    # Build lookup for quick access
    exposure_map = {e["factor_name"]: float(e["exposure"]) for e in valid_exposures}

    # Order factors canonically and prepare data
    factors = []
    values = []
    colors = []

    for factor in CANONICAL_FACTOR_ORDER:
        exposure = exposure_map.get(factor, 0.0)
        factors.append(_get_display_name(factor))
        values.append(exposure)
        colors.append(_get_exposure_color(exposure))

    # Reverse for horizontal bar chart (top factor first)
    factors = factors[::-1]
    values = values[::-1]
    colors = colors[::-1]

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            y=factors,
            x=values,
            orientation="h",
            marker_color=colors,
            text=[f"{v:+.2%}" for v in values],
            textposition="outside",
            hovertemplate="%{y}: %{x:.2%}<extra></extra>",
        )
    )

    # Add zero reference line
    fig.add_vline(
        x=0,
        line_width=1,
        line_color="gray",
        line_dash="dash",
    )

    fig.update_layout(
        title="Factor Exposures",
        xaxis_title="Exposure (%)",
        yaxis_title="",
        hovermode="y unified",
        margin={"l": 120, "r": 80, "t": 60, "b": 40},
        xaxis={
            "tickformat": ".0%",
            "zeroline": True,
            "zerolinewidth": 2,
            "zerolinecolor": "gray",
        },
    )

    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    return fig


__all__ = [
    "render_factor_exposure",
    "FACTOR_DISPLAY_NAMES",
]
