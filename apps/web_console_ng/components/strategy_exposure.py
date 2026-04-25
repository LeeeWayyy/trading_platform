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
    ui.plotly(build_exposure_chart_figure(exposures)).classes("w-full")


def build_exposure_chart_figure(exposures: list[StrategyExposureDTO]) -> go.Figure:
    """Build stacked bar chart figure for Long/Short by strategy."""
    if not exposures:
        fig = go.Figure()
        fig.update_layout(
            title="Long vs Short by Strategy",
            height=320,
            margin={"l": 10, "r": 10, "t": 40, "b": 20},
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[
                {
                    "text": "No exposure data available",
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0.5,
                    "y": 0.5,
                    "showarrow": False,
                    "font": {"size": 13, "color": "#94a3b8"},
                }
            ],
        )
        return fig

    strategies = [e.strategy for e in exposures]
    long_values = [e.long_notional for e in exposures]
    short_values = [-e.short_notional for e in exposures]  # negative for visual stacking
    bar_width = [0.45 for _ in exposures]

    fig = go.Figure(
        data=[
            go.Bar(
                name="Long",
                x=strategies,
                y=long_values,
                marker_color="#22c55e",
                width=bar_width,
            ),
            go.Bar(
                name="Short",
                x=strategies,
                y=short_values,
                marker_color="#ef4444",
                width=bar_width,
            ),
        ]
    )
    fig.update_layout(
        title="Long vs Short by Strategy",
        barmode="relative",
        bargap=0.35,
        xaxis={"title": "Strategy", "type": "category", "categoryorder": "array", "categoryarray": strategies},
        yaxis_title="Notional ($)",
        height=320,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
    )
    return fig


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
    *,
    include_total: bool = True,
) -> Any:
    """Render AG Grid with per-strategy exposure breakdown + TOTAL row."""
    rows = build_exposure_rows(exposures, total, include_total=include_total)

    dollar_fmt = "params => params.value != null ? (params.value >= 0 ? '+$' : '-$') + Math.abs(params.value).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'"
    pct_fmt = "params => params.value != null ? (params.value >= 0 ? '+' : '') + params.value.toFixed(1) + '%' : '-'"
    fit_columns_once_js = (
        "params => { "
        "const root = params.api.getGui ? params.api.getGui() : null; "
        "const width = root && root.clientWidth ? root.clientWidth : 0; "
        "if (width > 0) { params.api.sizeColumnsToFit(); } "
        "}"
    )
    bind_fit_columns_js = (
        "params => { "
        "const fit = () => { "
        "const root = params.api.getGui ? params.api.getGui() : null; "
        "const width = root && root.clientWidth ? root.clientWidth : 0; "
        "if (width > 0) { params.api.sizeColumnsToFit(); } "
        "}; "
        "requestAnimationFrame(fit); "
        "setTimeout(fit, 120); "
        "if (!params.api.__wcExposureFitBound) { "
        "params.api.__wcExposureFitBound = true; "
        "params.api.addEventListener('gridSizeChanged', fit); "
        "} "
        "}"
    )

    grid_options = {
        "defaultColDef": {
            "resizable": True,
        },
        "columnDefs": [
            {
                "field": "strategy",
                "headerName": "Strategy",
                "pinned": "left",
                "minWidth": 150,
                "flex": 1.6,
                ":cellClass": "params => params.value === 'TOTAL' ? 'font-bold' : ''",
            },
            {
                "field": "net",
                "headerName": "Net ($)",
                ":valueFormatter": dollar_fmt,
                "minWidth": 110,
                "flex": 1.0,
                ":cellClass": "params => params.value >= 0 ? 'text-green-600' : 'text-red-600'",
            },
            {
                "field": "gross",
                "headerName": "Gross ($)",
                ":valueFormatter": "params => params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'",
                "minWidth": 110,
                "flex": 1.0,
            },
            {
                "field": "long",
                "headerName": "Long ($)",
                ":valueFormatter": "params => params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'",
                "minWidth": 105,
                "flex": 1.0,
                "cellClass": "text-green-600",
            },
            {
                "field": "short",
                "headerName": "Short ($)",
                ":valueFormatter": "params => params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '-'",
                "minWidth": 105,
                "flex": 1.0,
                "cellClass": "text-red-600",
            },
            {
                "field": "net_pct",
                "headerName": "Net %",
                ":valueFormatter": pct_fmt,
                "minWidth": 95,
                "flex": 0.8,
                ":cellClass": "params => params.value >= 0 ? 'text-green-600' : 'text-red-600'",
            },
            {
                "field": "positions",
                "headerName": "# Pos",
                "minWidth": 96,
                "flex": 0.8,
            },
        ],
        "rowData": rows,
        "domLayout": "normal",
        "animateRows": True,
        ":onGridReady": bind_fit_columns_js,
        ":onFirstDataRendered": fit_columns_once_js,
    }

    return ui.aggrid(grid_options).classes("w-full min-w-0 pr-1 ag-theme-alpine-dark")


def build_exposure_rows(
    exposures: list[StrategyExposureDTO],
    total: TotalExposureDTO,
    *,
    include_total: bool = True,
) -> list[dict[str, Any]]:
    """Build AG Grid rows with per-strategy breakdown + TOTAL row."""
    # Do not render synthetic totals before first successful fetch.
    # Prefer the explicit placeholder marker when present, while still
    # handling legacy all-zero payloads that omit it.
    if not exposures and (
        total.is_placeholder
        or (
            total.strategy_count <= 0
            and total.long_total == 0.0
            and total.short_total == 0.0
            and total.gross_total == 0.0
            and total.net_total == 0.0
            and total.net_pct == 0.0
        )
    ):
        return []

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

    if include_total:
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
    return rows


__all__ = [
    "render_exposure_summary_cards",
    "render_exposure_chart",
    "build_exposure_chart_figure",
    "build_exposure_rows",
    "render_bias_warning",
    "render_data_quality_warning",
    "render_exposure_unavailable",
    "render_exposure_grid",
]
