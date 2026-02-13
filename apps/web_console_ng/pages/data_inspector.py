"""Point-in-Time Data Inspector page for NiceGUI web console (P6T13/T13.1).

Provides a lookup tool to inspect what market data was available as of a
specific "knowledge date", enabling look-ahead bias detection.

Route: ``/data/inspector``
Permission: ``VIEW_DATA_QUALITY``
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.pit_lookup import (
    render_pit_lookup_form,
    render_pit_results,
)
from apps.web_console_ng.ui.layout import main_layout
from libs.data.data_quality.pit_inspector import PITInspector
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


@ui.page("/data/inspector")
@requires_auth
@main_layout
async def data_inspector_page() -> None:
    """Point-in-Time Data Inspector page."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_DATA_QUALITY):
        ui.label("Data inspection requires VIEW_DATA_QUALITY permission.").classes(
            "text-gray-500"
        )
        return

    ui.label("Point-in-Time Data Inspector").classes("text-2xl font-bold mb-4")
    ui.label(
        "Inspect what market data was available as of a specific date. "
        "Detect look-ahead bias in backtests."
    ).classes("text-gray-600 mb-4")

    with ui.row().classes("gap-2 mb-4"):
        ui.link("Coverage Heatmap", "/data/coverage").classes(
            "text-blue-600 hover:underline"
        )

    inspector = PITInspector()
    available_tickers = inspector.get_available_tickers()
    min_date, max_date = inspector.get_date_range()

    results_container = ui.column().classes("w-full mt-4")

    async def on_submit(ticker: str, date_str: str, lookback_days: int) -> None:
        # Validate ticker is in authorized list
        if ticker not in available_tickers:
            ui.notify(f"Unauthorized ticker: {ticker}", type="negative")
            return

        # Parse date
        try:
            knowledge_date = date.fromisoformat(date_str)
        except ValueError:
            ui.notify(f"Invalid date format: {date_str}", type="negative")
            return

        # Look up — offload to worker thread to avoid blocking event loop
        try:
            result = await asyncio.to_thread(
                inspector.lookup, ticker, knowledge_date, lookback_days
            )
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
            return
        except Exception:
            logger.exception(
                "pit_lookup_failed",
                extra={"ticker": ticker, "knowledge_date": date_str},
            )
            ui.notify("Lookup failed. Check logs for details.", type="warning")
            return

        results_container.clear()
        with results_container:
            render_pit_results(result)

    with ui.row().classes("w-full gap-8"):
        # Left: Lookup form
        with ui.card().classes("w-80 p-4"):
            render_pit_lookup_form(
                available_tickers=available_tickers,
                min_date=min_date.isoformat() if min_date else None,
                max_date=max_date.isoformat() if max_date else None,
                on_submit=on_submit,
            )

        # Right: Results
        with ui.column().classes("flex-1"):
            with results_container:
                ui.label(
                    "Select a ticker and date to inspect point-in-time data."
                ).classes("text-gray-500")


__all__ = ["data_inspector_page"]
