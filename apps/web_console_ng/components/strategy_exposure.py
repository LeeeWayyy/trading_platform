"""Strategy exposure UI components (P6T15/T15.3)."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from libs.web_console_services.schemas.exposure import (
    StrategyExposureDTO,
    TotalExposureDTO,
)


def render_exposure_summary_cards(total: TotalExposureDTO) -> None:
    """Render 4 summary metric cards for Net/Gross/Long/Short."""
    with ui.row().classes("w-full gap-4 mb-3"):
        _metric_card(
            "Net",
            total.net_total,
            color="text-blue-600" if total.net_total >= 0 else "text-red-600",
            prefix="+" if total.net_total > 0 else ("-" if total.net_total < 0 else ""),
        )
        _metric_card("Gross", total.gross_total)
        _metric_card("Long", total.long_total, color="text-green-600")
        _metric_card("Short", total.short_total, color="text-red-600")


def _metric_card(
    label: str,
    value: float,
    *,
    color: str = "",
    prefix: str = "",
) -> None:
    """Render a single metric card."""
    with ui.card().classes("p-3 min-w-40"):
        ui.label(label).classes("text-sm text-gray-500")
        formatted = f"{prefix}${abs(value):,.0f}"
        ui.label(formatted).classes(f"text-2xl font-bold {color}")


def render_exposure_chart(exposures: list[StrategyExposureDTO]) -> None:
    """Render stacked bar chart with Long (green) and Short (red) per strategy."""
    if not exposures:
        return

    strategies = [e.strategy for e in exposures]
    long_values = [e.long_notional for e in exposures]
    short_values = [-e.short_notional for e in exposures]  # negative for visual stacking

    fig = go.Figure(
        data=[
            go.Bar(
                name="Long",
                x=strategies,
                y=long_values,
                marker_color="#22c55e",
            ),
            go.Bar(
                name="Short",
                x=strategies,
                y=short_values,
                marker_color="#ef4444",
            ),
        ]
    )
    fig.update_layout(
        title="Long vs Short by Strategy",
        barmode="relative",
        xaxis_title="Strategy",
        yaxis_title="Notional ($)",
        height=320,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
    )
    ui.plotly(fig).classes("w-full")


def render_bias_warning(total: TotalExposureDTO) -> None:
    """Render directional bias warning if threshold exceeded."""
    if not total.bias_warning:
        return

    if total.bias_severity == "red":
        color_classes = "bg-red-100 text-red-700 border-red-300"
    else:
        color_classes = "bg-amber-100 text-amber-700 border-amber-300"

    with ui.card().classes(f"w-full p-3 border {color_classes}"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning").classes("text-lg")
            ui.label(total.bias_warning).classes("text-sm font-semibold")


def render_data_quality_warning(total: TotalExposureDTO) -> None:
    """Render partial data quality warning if applicable."""
    if not total.data_quality_warning:
        return

    with ui.card().classes("w-full p-2 bg-amber-50 border border-amber-200"):
        ui.label(total.data_quality_warning).classes("text-xs text-amber-700")


def render_exposure_unavailable(total: TotalExposureDTO) -> None:
    """Render explicit 'unavailable' state when positions exist but cannot be attributed.

    Shown when ``total.is_partial`` is True and no per-strategy exposures could
    be computed.  Prevents misleading $0 totals that look like "flat risk"
    when the real state is "unknown risk".
    """
    with ui.card().classes("w-full p-6 bg-amber-50 border border-amber-300"):
        with ui.column().classes("items-center gap-2"):
            ui.icon("visibility_off").classes("text-3xl text-amber-600")
            ui.label("Exposure Unavailable").classes(
                "text-lg font-bold text-amber-700"
            )
            ui.label(
                "Positions exist but cannot be attributed to strategies. "
                "Numeric totals are suppressed to avoid misrepresenting risk."
            ).classes("text-sm text-amber-600 text-center")
            if total.data_quality_warning:
                ui.label(total.data_quality_warning).classes(
                    "text-xs text-amber-500 mt-1"
                )


def render_exposure_grid(
    exposures: list[StrategyExposureDTO],
    total: TotalExposureDTO,
) -> Any:
    """Render AG Grid with per-strategy exposure breakdown + TOTAL row."""
    rows = []
    for e in exposures:
        rows.append(
            {
                "strategy": e.strategy,
                "net": e.net_notional,
                "gross": e.gross_notional,
                "long": e.long_notional,
                "short": e.short_notional,
                "net_pct": e.net_pct,
                "positions": e.position_count,
            }
        )

    # TOTAL row
    rows.append(
        {
            "strategy": "TOTAL",
            "net": total.net_total,
            "gross": total.gross_total,
            "long": total.long_total,
            "short": total.short_total,
            "net_pct": total.net_pct,
            "positions": sum(e.position_count for e in exposures),
        }
    )

    dollar_fmt = "params => params.value != null ? (params.value >= 0 ? '+$' : '-$') + Math.abs(params.value).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'"
    pct_fmt = "params => params.value != null ? (params.value >= 0 ? '+' : '') + params.value.toFixed(1) + '%' : '-'"

    grid_options = {
        "columnDefs": [
            {
                "field": "strategy",
                "headerName": "Strategy",
                "pinned": "left",
                "minWidth": 160,
                ":cellClass": "params => params.value === 'TOTAL' ? 'font-bold' : ''",
            },
            {
                "field": "net",
                "headerName": "Net ($)",
                ":valueFormatter": dollar_fmt,
                "minWidth": 130,
                ":cellClass": "params => params.value >= 0 ? 'text-green-600' : 'text-red-600'",
            },
            {
                "field": "gross",
                "headerName": "Gross ($)",
                ":valueFormatter": "params => params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'",
                "minWidth": 130,
            },
            {
                "field": "long",
                "headerName": "Long ($)",
                ":valueFormatter": "params => params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'",
                "minWidth": 120,
                "cellClass": "text-green-600",
            },
            {
                "field": "short",
                "headerName": "Short ($)",
                ":valueFormatter": "params => params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'",
                "minWidth": 120,
                "cellClass": "text-red-600",
            },
            {
                "field": "net_pct",
                "headerName": "Net %",
                ":valueFormatter": pct_fmt,
                "minWidth": 100,
                ":cellClass": "params => params.value >= 0 ? 'text-green-600' : 'text-red-600'",
            },
            {
                "field": "positions",
                "headerName": "# Pos",
                "minWidth": 80,
            },
        ],
        "rowData": rows,
        "domLayout": "normal",
        "animateRows": True,
    }

    return ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark")


__all__ = [
    "render_exposure_summary_cards",
    "render_exposure_chart",
    "render_bias_warning",
    "render_data_quality_warning",
    "render_exposure_unavailable",
    "render_exposure_grid",
]
