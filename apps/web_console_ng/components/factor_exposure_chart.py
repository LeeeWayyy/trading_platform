"""Factor exposure visualization component for NiceGUI risk dashboard.

Ported from apps/web_console/components/factor_exposure_chart.py (Streamlit).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from libs.core.common.validators import validate_exposures
from libs.trading.risk.factor_covariance import CANONICAL_FACTOR_ORDER

# Ensure asset_growth is included for full factor coverage (P2 fix)
# RiskService returns 6 factors, so chart must display all 6
DEFAULT_FACTOR_ORDER = [
    "log_market_cap",
    "book_to_market",
    "momentum_12_1",
    "realized_vol",
    "roe",
    "asset_growth",
]

# Merge CANONICAL_FACTOR_ORDER with DEFAULT_FACTOR_ORDER, preserving order
# FALLBACK BEHAVIOR: If CANONICAL_FACTOR_ORDER is empty/None, use DEFAULT_FACTOR_ORDER only
# This ensures UI never shows empty chart even if libs/risk not configured
_chart_factor_order = list(dict.fromkeys((CANONICAL_FACTOR_ORDER or []) + DEFAULT_FACTOR_ORDER))

# Factor display names (human-readable)
# L-2: Unknown factors fall back to raw factor name (see _get_display_name)
FACTOR_DISPLAY_NAMES = {
    "momentum_12_1": "Momentum (12-1)",
    "book_to_market": "Book-to-Market",
    "roe": "ROE (Quality)",
    "log_market_cap": "Size (Market Cap)",
    "realized_vol": "Volatility",
    "asset_growth": "Asset Growth",
}

# Chart colors
COLOR_GREEN = "#27AE60"  # Positive exposure
COLOR_RED = "#E74C3C"  # Negative exposure


def _get_display_name(factor_name: str) -> str:
    """Get human-readable display name for factor.

    L-2 Fallback: Returns raw factor_name if not in FACTOR_DISPLAY_NAMES.
    """
    return FACTOR_DISPLAY_NAMES.get(factor_name, factor_name)


def _get_exposure_color(exposure: float) -> str:
    """Get color based on exposure direction: green positive, red negative."""
    return COLOR_GREEN if exposure >= 0 else COLOR_RED


def render_factor_exposure(
    exposures: Sequence[dict[str, Any]] | None,
) -> None:
    """Render horizontal bar chart of factor exposures.

    Args:
        exposures: List of dicts with 'factor_name' and 'exposure' keys
    """
    # Validate exposures before rendering (T6.2 Validator requirement)
    valid_exposures = validate_exposures(list(exposures or []))

    if not valid_exposures:
        ui.label("No factor exposure data available.").classes("text-gray-500 p-4")
        return

    # Build lookup for quick access (safe float conversion)
    exposure_map: dict[str, float] = {}
    for e in valid_exposures:
        try:
            exposure = float(e["exposure"])
            if not math.isfinite(exposure):
                continue  # Skip NaN/inf values
            exposure_map[e["factor_name"]] = exposure
        except (TypeError, ValueError, KeyError):
            continue  # Skip invalid entries

    # Order factors canonically and prepare data
    # M-2 Fix: Track missing factors separately to avoid misrepresenting absent data as 0.0
    factors = []
    values = []
    colors = []
    missing_factors = []

    for factor in _chart_factor_order:
        if factor in exposure_map:
            exposure = exposure_map[factor]
            factors.append(_get_display_name(factor))
            values.append(exposure)
            colors.append(_get_exposure_color(exposure))
        else:
            missing_factors.append(_get_display_name(factor))

    # Handle any extra factors not in canonical order (L-2: unknown factor support)
    for factor_name in exposure_map:
        if factor_name not in _chart_factor_order:
            exposure = exposure_map[factor_name]
            factors.append(_get_display_name(factor_name))  # Falls back to raw name
            values.append(exposure)
            colors.append(_get_exposure_color(exposure))

    # Guard against all exposures being invalid after float conversion
    if not values:
        ui.label("No valid factor exposure data.").classes("text-gray-500 p-4")
        return

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

    ui.plotly(fig).classes("w-full")

    # Show warning if canonical factors are missing (M-2 Fix)
    if missing_factors:
        ui.label(f"Data unavailable for: {', '.join(missing_factors)}").classes(
            "text-yellow-600 text-sm mt-2"
        )


__all__ = [
    "render_factor_exposure",
    "FACTOR_DISPLAY_NAMES",
]
