"""Point-in-Time lookup form and results rendering component.

Provides two render functions:
    - ``render_pit_lookup_form``: Ticker dropdown, date picker, lookback slider
    - ``render_pit_results``: Summary card, data tables, timeline chart
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from typing import Any

import plotly.graph_objects as go
from nicegui import ui

from libs.data.data_quality.pit_inspector import PITLookupResult


def render_pit_lookup_form(
    available_tickers: list[str],
    min_date: str | None,
    max_date: str | None,
    on_submit: Callable[[str, str, int], Any],
) -> None:
    """Render the PIT lookup form.

    Args:
        available_tickers: Authorized ticker symbols for dropdown.
        min_date: Earliest available date (ISO format) or None.
        max_date: Latest available date (ISO format) or None.
        on_submit: Callback(ticker, knowledge_date_iso, lookback_days).
    """
    if not available_tickers:
        ui.label(
            "No adjusted data found. Run the ETL pipeline first."
        ).classes("text-gray-500")
        return

    ui.label("Point-in-Time Lookup").classes("font-bold mb-2")

    ticker_select = ui.select(
        label="Ticker",
        options=available_tickers,
        value=available_tickers[0],
    ).classes("w-48")

    from datetime import datetime as dt_type

    default_date = max_date or dt_type.now(UTC).date().isoformat()
    date_input = ui.input(
        label="Knowledge Date (YYYY-MM-DD)",
        value=default_date,
    ).classes("w-48")

    if min_date and max_date:
        ui.label(f"Data range: {min_date} to {max_date}").classes(
            "text-sm text-gray-500"
        )

    lookback_slider = ui.slider(
        min=1, max=3650, value=365
    ).classes("w-64")

    lookback_label = ui.label("365 calendar days (~1.0 years)").classes(
        "text-sm text-gray-500"
    )

    def _update_label() -> None:
        val = int(lookback_slider.value)
        lookback_label.text = f"{val} calendar days (~{val / 365:.1f} years)"

    lookback_slider.on_value_change(lambda _: _update_label())

    async def _submit() -> None:
        ticker_val = str(ticker_select.value)
        date_val = str(date_input.value)
        lookback_val = int(lookback_slider.value)
        if not ticker_val:
            ui.notify("Please select a ticker", type="warning")
            return
        if not date_val:
            ui.notify("Please enter a knowledge date", type="warning")
            return
        await on_submit(ticker_val, date_val, lookback_val)

    ui.button(
        "Inspect Point-in-Time", on_click=_submit, color="primary"
    ).classes("mt-4")


def render_pit_results(result: PITLookupResult) -> None:
    """Render PIT lookup results.

    Args:
        result: The lookup result to display.
    """
    # Summary card
    with ui.card().classes("w-full p-4 mb-4"):
        ui.label(
            f"{result.ticker} as of {result.knowledge_date}"
        ).classes("text-xl font-bold")

        # Status badge
        if result.has_look_ahead_risk:
            ui.label("Look-ahead risk detected!").classes(
                "text-red-600 font-bold bg-red-100 px-2 py-1 rounded mt-2"
            )
        else:
            ui.label("No look-ahead bias").classes(
                "text-green-600 font-bold bg-green-100 px-2 py-1 rounded mt-2"
            )

        with ui.row().classes("gap-8 mt-4"):
            with ui.column():
                ui.label("Latest Data Available").classes(
                    "text-sm text-gray-500"
                )
                if result.latest_available_date:
                    ui.label(str(result.latest_available_date)).classes(
                        "font-bold"
                    )
                    if result.days_stale is not None and result.days_stale > 0:
                        ui.label(
                            f"{result.days_stale} trading days stale"
                        ).classes("text-sm text-amber-600")
                else:
                    ui.label("No data").classes("text-gray-500")

            with ui.column():
                ui.label("Data Points Available").classes(
                    "text-sm text-gray-500"
                )
                ui.label(str(result.total_rows_available)).classes("font-bold")

            with ui.column():
                ui.label("Future Partitions").classes(
                    "text-sm text-gray-500"
                )
                ui.label(str(result.future_partition_count)).classes("font-bold")

    # Available data table
    if result.data_available:
        ui.label("Available Data (Known)").classes("font-bold mb-2")
        show_rows = result.data_available[:50]
        columns: list[dict[str, Any]] = [
            {"name": "date", "label": "Date", "field": "date", "sortable": True},
            {"name": "open", "label": "Open", "field": "open"},
            {"name": "high", "label": "High", "field": "high"},
            {"name": "low", "label": "Low", "field": "low"},
            {"name": "close", "label": "Close", "field": "close"},
            {"name": "volume", "label": "Volume", "field": "volume"},
            {"name": "run_date", "label": "Run Date", "field": "run_date"},
        ]
        rows = [
            {
                "date": str(p.market_date),
                "open": f"{p.open:.2f}",
                "high": f"{p.high:.2f}",
                "low": f"{p.low:.2f}",
                "close": f"{p.close:.2f}",
                "volume": p.volume,
                "run_date": str(p.run_date),
            }
            for p in show_rows
        ]
        ui.table(columns=columns, rows=rows).classes("w-full mb-4")
        if len(result.data_available) > 50:
            ui.label(
                f"Showing 50 of {len(result.data_available)} rows"
            ).classes("text-sm text-gray-500")
    else:
        ui.label("No data available before this date").classes(
            "text-gray-500 mb-4"
        )

    # Future data warning
    if result.has_look_ahead_risk and result.data_future:
        with ui.card().classes(
            "w-full p-4 mb-4 bg-red-50 border-l-4 border-red-500"
        ):
            ui.label(
                f"The following data points were NOT yet available on "
                f"{result.knowledge_date} but exist in the dataset. "
                f"Using them would introduce look-ahead bias."
            ).classes("text-red-700 font-bold mb-2")
            ui.label(
                f"Showing sample of {len(result.data_future)} rows from "
                f"{result.future_partition_count} future partition(s)"
            ).classes("text-sm text-red-600 mb-2")

            future_columns: list[dict[str, Any]] = [
                {"name": "date", "label": "Date", "field": "date"},
                {"name": "close", "label": "Close", "field": "close"},
                {"name": "run_date", "label": "Run Date", "field": "run_date"},
            ]
            future_rows = [
                {
                    "date": str(p.market_date),
                    "close": f"{p.close:.2f}",
                    "run_date": str(p.run_date),
                }
                for p in result.data_future
            ]
            ui.table(columns=future_columns, rows=future_rows).classes("w-full")

    # Timeline visualization
    if result.data_available or result.data_future:
        fig = go.Figure()

        if result.data_available:
            avail_dates = [str(p.market_date) for p in result.data_available]
            avail_close = [p.close for p in result.data_available]
            fig.add_trace(
                go.Scatter(
                    x=avail_dates,
                    y=avail_close,
                    mode="lines+markers",
                    name="Available (known)",
                    line={"color": "blue"},
                )
            )

        if result.data_future:
            future_dates = [str(p.market_date) for p in result.data_future]
            future_close = [p.close for p in result.data_future]
            fig.add_trace(
                go.Scatter(
                    x=future_dates,
                    y=future_close,
                    mode="lines+markers",
                    name="Future (look-ahead)",
                    line={"color": "red", "dash": "dash"},
                )
            )

        fig.add_vline(
            x=str(result.knowledge_date),
            line_dash="dash",
            line_color="gray",
            annotation_text="Knowledge Cutoff",
        )
        fig.update_layout(
            title=f"PIT Timeline - {result.ticker}",
            xaxis_title="Date",
            yaxis_title="Close Price",
        )
        ui.plotly(fig).classes("w-full")


__all__ = ["render_pit_lookup_form", "render_pit_results"]
