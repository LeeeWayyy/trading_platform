"""Data Coverage Heatmap page for NiceGUI web console (P6T13/T13.2).

Visualizes data completeness across the ticker universe with a
symbol x date heatmap. Enables identification of data gaps.

Route: ``/data/coverage``
Permission: ``VIEW_DATA_QUALITY``
"""

from __future__ import annotations

import logging
from datetime import date

from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.coverage_heatmap import (
    render_coverage_controls,
    render_coverage_export,
    render_coverage_heatmap,
)
from apps.web_console_ng.ui.layout import main_layout
from libs.data.data_quality.coverage_analyzer import CoverageAnalyzer
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


@ui.page("/data/coverage")
@requires_auth
@main_layout
async def data_coverage_page() -> None:
    """Data Coverage Heatmap page."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_DATA_QUALITY):
        ui.label("Coverage analysis requires VIEW_DATA_QUALITY permission.").classes(
            "text-gray-500"
        )
        return

    ui.label("Data Coverage Heatmap").classes("text-2xl font-bold mb-4")
    ui.label(
        "Visualize data completeness across the ticker universe. "
        "Identify gaps and quality issues."
    ).classes("text-gray-600 mb-4")

    with ui.row().classes("gap-2 mb-4"):
        ui.link("PIT Inspector", "/data/inspector").classes(
            "text-blue-600 hover:underline"
        )

    analyzer = CoverageAnalyzer()
    available_tickers = analyzer.get_available_tickers()

    results_container = ui.column().classes("w-full mt-4")
    export_container = ui.column().classes("w-full")

    async def on_analyze(
        symbols: list[str] | None,
        start_str: str | None,
        end_str: str | None,
        resolution: str,
    ) -> None:
        # Filter symbols to authorized list
        if symbols is not None:
            authorized = [s for s in symbols if s in available_tickers]
            if not authorized:
                ui.notify("No authorized symbols selected", type="warning")
                return
            symbols = authorized

        # Parse dates
        start_date = None
        end_date = None
        try:
            if start_str:
                start_date = date.fromisoformat(start_str)
            if end_str:
                end_date = date.fromisoformat(end_str)
        except ValueError:
            ui.notify("Invalid date format. Use YYYY-MM-DD.", type="negative")
            return

        # Validate resolution
        if resolution not in ("daily", "weekly", "monthly"):
            resolution = "monthly"

        try:
            matrix = analyzer.analyze(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                resolution=resolution,  # type: ignore[arg-type]
            )
        except Exception:
            logger.exception(
                "coverage_analysis_failed",
                extra={"symbols": symbols, "resolution": resolution},
            )
            ui.notify(
                "Analysis failed. Check logs for details.", type="warning"
            )
            return

        results_container.clear()
        with results_container:
            render_coverage_heatmap(matrix)

        export_container.clear()
        with export_container:
            render_coverage_export(matrix, analyzer)

    with ui.row().classes("w-full gap-8"):
        with ui.card().classes("w-80 p-4"):
            render_coverage_controls(
                available_tickers=available_tickers,
                on_analyze=on_analyze,
            )

        with ui.column().classes("flex-1"):
            with results_container:
                ui.label(
                    "Configure analysis parameters and click Analyze."
                ).classes("text-gray-500")
            with export_container:
                pass


__all__ = ["data_coverage_page"]
