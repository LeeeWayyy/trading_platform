"""Universe analytics UI components (P6T15/T15.2).

Render functions for universe summary statistics, distribution charts,
sector/factor visualizations, and side-by-side universe comparison.
"""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from libs.web_console_services.schemas.universe import (
    UniverseAnalyticsDTO,
    UniverseComparisonDTO,
)


def render_universe_analytics(analytics: UniverseAnalyticsDTO) -> None:
    """Render analytics panel: summary cards, histograms, sector pie, factor bar."""

    # Summary cards
    with ui.row().classes("w-full gap-4 mb-3"):
        _metric_card("Symbols", str(analytics.symbol_count))
        _metric_card(
            "Avg Market Cap",
            _format_dollars_billions(analytics.avg_market_cap),
        )
        _metric_card(
            "Median ADV",
            _format_dollars_millions(analytics.median_adv),
        )
        _metric_card(
            "Total Market Cap",
            _format_dollars_trillions(analytics.total_market_cap),
        )

    # Charts in 2x2 grid
    with ui.row().classes("w-full gap-4 mb-3"):
        with ui.column().classes("flex-1"):
            _render_market_cap_histogram(analytics.market_cap_distribution)
        with ui.column().classes("flex-1"):
            _render_adv_histogram(analytics.adv_distribution)

    with ui.row().classes("w-full gap-4 mb-3"):
        with ui.column().classes("flex-1"):
            if analytics.is_sector_mock:
                _mock_data_badge()
            _render_sector_pie(analytics.sector_distribution)
        with ui.column().classes("flex-1"):
            if analytics.is_factor_mock:
                _mock_data_badge()
            _render_factor_bar(analytics.factor_exposure)


def render_universe_comparison(comparison: UniverseComparisonDTO) -> None:
    """Render side-by-side comparison table with overlap metrics."""

    a = comparison.universe_a_stats
    b = comparison.universe_b_stats

    with ui.card().classes("w-full p-4"):
        ui.label("Universe Comparison").classes("text-xl font-bold mb-3")

        rows = [
            {
                "metric": "Symbols",
                "universe_a": str(a.symbol_count),
                "universe_b": str(b.symbol_count),
                "delta": _format_delta_int(a.symbol_count, b.symbol_count),
            },
            {
                "metric": "Avg Market Cap",
                "universe_a": _format_dollars_billions(a.avg_market_cap),
                "universe_b": _format_dollars_billions(b.avg_market_cap),
                "delta": _format_delta_pct(a.avg_market_cap, b.avg_market_cap),
            },
            {
                "metric": "Median ADV",
                "universe_a": _format_dollars_millions(a.median_adv),
                "universe_b": _format_dollars_millions(b.median_adv),
                "delta": _format_delta_pct(a.median_adv, b.median_adv),
            },
            {
                "metric": "Total Market Cap",
                "universe_a": _format_dollars_trillions(a.total_market_cap),
                "universe_b": _format_dollars_trillions(b.total_market_cap),
                "delta": _format_delta_pct(a.total_market_cap, b.total_market_cap),
            },
            {
                "metric": "Overlap",
                "universe_a": f"{comparison.overlap_count} symbols",
                "universe_b": f"{comparison.overlap_pct:.1f}% of smaller",
                "delta": "",
            },
        ]

        grid_options: dict[str, Any] = {
            "columnDefs": [
                {
                    "field": "metric",
                    "headerName": "Metric",
                    "minWidth": 150,
                    "pinned": "left",
                },
                {
                    "field": "universe_a",
                    "headerName": a.universe_id,
                    "minWidth": 150,
                },
                {
                    "field": "universe_b",
                    "headerName": b.universe_id,
                    "minWidth": 150,
                },
                {
                    "field": "delta",
                    "headerName": "Delta (A→B)",
                    "minWidth": 120,
                },
            ],
            "rowData": rows,
            "domLayout": "autoHeight",
        }

        ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark")


# ----------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------


def _format_delta_int(val_a: int, val_b: int) -> str:
    """Format integer delta as signed string (B - A)."""
    diff = val_b - val_a
    if diff == 0:
        return "0"
    return f"+{diff}" if diff > 0 else str(diff)


def _format_delta_pct(val_a: float, val_b: float) -> str:
    """Format percentage delta relative to A (B vs A)."""
    if val_a == 0.0:
        return "N/A" if val_b != 0.0 else "0%"
    pct = (val_b - val_a) / val_a * 100.0
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _metric_card(label: str, value: str) -> None:
    """Render a summary metric card."""
    with ui.card().classes("p-3 min-w-40"):
        ui.label(label).classes("text-sm text-gray-500")
        ui.label(value).classes("text-2xl font-bold")


def _mock_data_badge() -> None:
    """Render a 'Mock Data' badge with amber styling."""
    ui.label("Mock Data").classes(
        "inline-block px-3 py-1 rounded bg-amber-100 "
        "text-amber-700 text-xs font-semibold mb-3"
    )


def _format_dollars_billions(value_thousands: float) -> str:
    """Convert $thousands (CRSP) to $B display string."""
    billions = value_thousands / 1_000_000
    return f"${billions:,.1f}B"


def _format_dollars_millions(value_notional: float) -> str:
    """Convert $ notional to $M display string."""
    millions = value_notional / 1_000_000
    return f"${millions:,.1f}M"


def _format_dollars_trillions(value_thousands: float) -> str:
    """Convert $thousands (CRSP) to $T display string."""
    trillions = value_thousands / 1_000_000_000
    return f"${trillions:,.1f}T"


def _render_market_cap_histogram(distribution: list[float]) -> None:
    """Render market cap distribution histogram with log-scale x-axis."""
    if not distribution:
        ui.label("No market cap data available").classes("text-gray-500")
        return

    fig = go.Figure(
        data=[
            go.Histogram(
                x=distribution,
                nbinsx=10,
                marker_color="#3b82f6",
            )
        ]
    )
    fig.update_layout(
        title="Market Cap Distribution",
        xaxis_title="Market Cap ($K)",
        yaxis_title="Count",
        xaxis_type="log",
        height=300,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
    )
    ui.plotly(fig).classes("w-full")


def _render_adv_histogram(distribution: list[float]) -> None:
    """Render ADV distribution histogram with log-scale x-axis."""
    if not distribution:
        ui.label("No ADV data available").classes("text-gray-500")
        return

    fig = go.Figure(
        data=[
            go.Histogram(
                x=distribution,
                nbinsx=10,
                marker_color="#8b5cf6",
            )
        ]
    )
    fig.update_layout(
        title="ADV (20d) Distribution",
        xaxis_title="ADV ($)",
        yaxis_title="Count",
        xaxis_type="log",
        height=300,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
    )
    ui.plotly(fig).classes("w-full")


def _render_sector_pie(sector_distribution: dict[str, float]) -> None:
    """Render sector distribution pie chart."""
    if not sector_distribution:
        ui.label("No sector data available").classes("text-gray-500")
        return

    labels = list(sector_distribution.keys())
    values = list(sector_distribution.values())

    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.3)])
    fig.update_layout(
        title="Sector Distribution",
        height=300,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
    )
    ui.plotly(fig).classes("w-full")


def _render_factor_bar(factor_exposure: dict[str, float]) -> None:
    """Render factor exposure bar chart."""
    if not factor_exposure:
        ui.label("No factor data available").classes("text-gray-500")
        return

    factors = list(factor_exposure.keys())
    exposures = list(factor_exposure.values())
    colors = ["#22c55e" if v >= 0 else "#ef4444" for v in exposures]

    fig = go.Figure(
        data=[
            go.Bar(
                x=factors,
                y=exposures,
                marker_color=colors,
            )
        ]
    )
    fig.update_layout(
        title="Factor Exposure",
        xaxis_title="Factor",
        yaxis_title="Exposure",
        height=300,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
    )
    ui.plotly(fig).classes("w-full")


__all__ = [
    "render_universe_analytics",
    "render_universe_comparison",
]
