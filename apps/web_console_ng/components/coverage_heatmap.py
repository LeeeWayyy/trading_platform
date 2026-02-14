"""Coverage heatmap rendering component.

Provides three render functions:
    - ``render_coverage_controls``: Symbol filter, date range, resolution picker
    - ``render_coverage_heatmap``: Plotly heatmap + summary cards + gaps table
    - ``render_coverage_export``: CSV/JSON export button
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import plotly.graph_objects as go
from nicegui import ui

from libs.data.data_quality.coverage_analyzer import (
    CoverageAnalyzer,
    CoverageMatrix,
    CoverageStatus,
)

# Display thresholds and limits
HIGH_COVERAGE_THRESHOLD = 95.0
MEDIUM_COVERAGE_THRESHOLD = 80.0
MIN_HEATMAP_HEIGHT = 400
HEATMAP_ROW_HEIGHT = 20
HEATMAP_PADDING = 100
MAX_GAPS_DISPLAYED = 50


def render_coverage_controls(
    available_tickers: list[str],
    on_analyze: Callable[[list[str] | None, str | None, str | None, str], Any],
) -> None:
    """Render coverage analysis controls.

    Args:
        available_tickers: Authorized ticker symbols for multi-select.
        on_analyze: Callback(symbols, start_date_iso, end_date_iso, resolution).
    """
    if not available_tickers:
        ui.label(
            "No adjusted data found. Run the ETL pipeline first."
        ).classes("text-gray-500")
        return

    ui.label("Coverage Analysis").classes("font-bold mb-2")

    symbol_select = ui.select(
        label="Symbols (leave empty for all)",
        options=available_tickers,
        multiple=True,
        value=[],
    ).classes("w-64")

    start_input = ui.input(
        label="Start Date (YYYY-MM-DD)",
        value="",
    ).classes("w-48")

    end_input = ui.input(
        label="End Date (YYYY-MM-DD)",
        value="",
    ).classes("w-48")

    resolution_toggle = ui.toggle(
        ["Daily", "Weekly", "Monthly"], value="Monthly"
    ).classes("mt-2")

    async def _submit() -> None:
        syms = list(symbol_select.value) if symbol_select.value else None
        start_val = str(start_input.value).strip() or None
        end_val = str(end_input.value).strip() or None
        res_val = str(resolution_toggle.value).lower()
        await on_analyze(syms, start_val, end_val, res_val)

    ui.button(
        "Analyze Coverage", on_click=_submit, color="primary"
    ).classes("mt-4")


def render_coverage_heatmap(matrix: CoverageMatrix) -> None:
    """Render coverage heatmap with summary cards and gaps table.

    Args:
        matrix: The coverage analysis result to display.
    """
    # Notices
    for notice in matrix.notices:
        ui.label(notice).classes(
            "text-blue-600 bg-blue-50 px-3 py-1 rounded mb-2"
        )

    if matrix.skipped_file_count > 0:
        ui.label(
            f"{matrix.skipped_file_count} file(s) could not be read "
            f"and were excluded from analysis"
        ).classes("text-amber-600 bg-amber-50 px-3 py-1 rounded mb-2")

    # Summary cards
    summary = matrix.summary
    pct = summary.coverage_pct
    if pct >= HIGH_COVERAGE_THRESHOLD:
        pct_color = "text-green-600"
    elif pct >= MEDIUM_COVERAGE_THRESHOLD:
        pct_color = "text-amber-600"
    else:
        pct_color = "text-red-600"

    with ui.row().classes("gap-4 mb-4"):
        with ui.card().classes("p-3"):
            ui.label("Coverage").classes("text-sm text-gray-500")
            ui.label(f"{pct:.1f}%").classes(f"text-2xl font-bold {pct_color}")

        with ui.card().classes("p-3"):
            ui.label("Expected Cells").classes("text-sm text-gray-500")
            ui.label(str(summary.total_expected)).classes("text-xl font-bold")

        with ui.card().classes("p-3"):
            ui.label("Missing").classes("text-sm text-gray-500")
            ui.label(str(summary.total_missing)).classes(
                "text-xl font-bold text-red-600"
            )

        with ui.card().classes("p-3"):
            ui.label("Suspicious").classes("text-sm text-gray-500")
            ui.label(str(summary.total_suspicious)).classes(
                "text-xl font-bold text-amber-600"
            )

    # Heatmap
    if matrix.symbols and matrix.dates:
        status_to_value = {
            CoverageStatus.MISSING: 0.0,
            CoverageStatus.SUSPICIOUS: 0.33,
            CoverageStatus.COMPLETE: 0.67,
            CoverageStatus.NO_EXPECTATION: 1.0,
        }
        z = [
            [status_to_value[cell] for cell in row]
            for row in matrix.matrix
        ]
        text = [[cell.value for cell in row] for row in matrix.matrix]

        fig = go.Figure(
            data=go.Heatmap(
                z=z,
                x=[d.isoformat() for d in matrix.dates],
                y=matrix.symbols,
                colorscale=[
                    [0.0, "#ef4444"],
                    [0.165, "#ef4444"],
                    [0.165, "#f59e0b"],
                    [0.5, "#f59e0b"],
                    [0.5, "#22c55e"],
                    [0.835, "#22c55e"],
                    [0.835, "#e5e7eb"],
                    [1.0, "#e5e7eb"],
                ],
                zmin=0.0,
                zmax=1.0,
                hovertemplate=(
                    "Symbol: %{y}<br>Date: %{x}<br>"
                    "Status: %{text}<extra></extra>"
                ),
                text=text,
                colorbar={
                    "tickvals": [0.0, 0.33, 0.67, 1.0],
                    "ticktext": [
                        "Missing",
                        "Suspicious",
                        "Complete",
                        "No Expectation",
                    ],
                },
            )
        )
        fig.update_layout(
            title=f"Data Coverage ({pct:.1f}%) - {matrix.effective_resolution}",
            xaxis_title="Date",
            yaxis_title="Symbol",
            height=max(
                MIN_HEATMAP_HEIGHT,
                len(matrix.symbols) * HEATMAP_ROW_HEIGHT + HEATMAP_PADDING,
            ),
        )
        ui.plotly(fig).classes("w-full mb-4")
    else:
        ui.label("No data to display in heatmap").classes(
            "text-gray-500 mb-4"
        )

    # Gaps table
    if summary.gaps:
        ui.label("Data Gaps (sorted by size)").classes("font-bold mb-2")
        columns: list[dict[str, Any]] = [
            {"name": "symbol", "label": "Symbol", "field": "symbol", "sortable": True},
            {"name": "start", "label": "Start Date", "field": "start", "sortable": True},
            {"name": "end", "label": "End Date", "field": "end", "sortable": True},
            {"name": "days", "label": "Gap Days", "field": "days", "sortable": True},
        ]
        rows = [
            {
                "symbol": g.symbol,
                "start": g.start_date.isoformat(),
                "end": g.end_date.isoformat(),
                "days": g.gap_days,
            }
            for g in summary.gaps[:MAX_GAPS_DISPLAYED]
        ]
        ui.table(columns=columns, rows=rows).classes("w-full mb-4")
        if len(summary.gaps) > MAX_GAPS_DISPLAYED:
            ui.label(
                f"Showing {MAX_GAPS_DISPLAYED} of {len(summary.gaps)} gaps"
            ).classes("text-sm text-gray-500")


def render_coverage_export(
    matrix: CoverageMatrix,
    analyzer: CoverageAnalyzer,
) -> None:
    """Render export controls for coverage data.

    Args:
        matrix: The coverage matrix to export.
        analyzer: The analyzer instance for export generation.
    """
    with ui.row().classes("gap-2 mt-4"):
        fmt_toggle = ui.toggle(["CSV", "JSON"], value="CSV")

        def _export() -> None:
            fmt_val: Literal["csv", "json"] = (
                "csv" if fmt_toggle.value == "CSV" else "json"
            )
            content = analyzer.export_coverage_report(matrix, fmt=fmt_val)
            ext = fmt_val
            ui.download(
                content.encode("utf-8"),
                f"coverage_report.{ext}",
            )

        ui.button("Export Report", on_click=_export).classes("mt-1")


__all__ = [
    "render_coverage_controls",
    "render_coverage_export",
    "render_coverage_heatmap",
]
