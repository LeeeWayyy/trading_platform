"""Universe builder and listing UI components (P6T15/T15.1)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui
from pydantic import ValidationError

from libs.web_console_services.schemas.universe import (
    CustomUniverseDefinitionDTO,
    UniverseDetailDTO,
    UniverseFilterDTO,
    UniverseListItemDTO,
)

logger = logging.getLogger(__name__)


def render_universe_list(
    universes: list[UniverseListItemDTO],
    *,
    on_select: Callable[[str], Awaitable[None]] | None = None,
) -> Any:
    """Render universe listing as AG Grid.

    Args:
        universes: List of universe items to display.
        on_select: Callback when a row is clicked (receives universe_id).
    """
    rows = []
    for u in universes:
        rows.append({
            "id": u.id,
            "name": u.name,
            "type": u.universe_type.replace("_", " ").title(),
            "symbols": u.symbol_count,
            "symbols_approx": u.count_is_approximate,
            "last_updated": u.last_updated or "-",
            "base": u.base or "-",
        })

    grid_options: dict[str, Any] = {
        "columnDefs": [
            {
                "field": "name",
                "headerName": "Universe",
                "minWidth": 180,
                "sortable": True,
                "filter": True,
            },
            {
                "field": "type",
                "headerName": "Type",
                "minWidth": 100,
                "sortable": True,
            },
            {
                "field": "symbols",
                "headerName": "Symbols",
                "minWidth": 90,
                "sortable": True,
                ":valueFormatter": "params => params.value == null ? 'N/A'"
                " : (params.data?.symbols_approx ? '~' + params.value : '' + params.value)",
            },
            {
                "field": "last_updated",
                "headerName": "Last Updated",
                "minWidth": 120,
            },
            {
                "field": "base",
                "headerName": "Source",
                "minWidth": 100,
            },
        ],
        "rowData": rows,
        "domLayout": "autoHeight",
        "rowSelection": "single",
    }

    grid = ui.aggrid(grid_options).classes("w-full ag-theme-alpine-dark")

    if on_select:
        async def _on_row_click(e: Any) -> None:
            try:
                uid = e.args["data"]["id"]
            except (KeyError, TypeError, IndexError, AttributeError):
                return
            if uid:
                await on_select(uid)

        grid.on("rowClicked", _on_row_click)

    return grid


def render_universe_detail(
    detail: UniverseDetailDTO,
) -> Any:
    """Render universe detail view with constituent table."""
    if detail.crsp_unavailable or (detail.error_message and not detail.constituents):
        with ui.card().classes("w-full p-6 bg-amber-50 border border-amber-300"):
            with ui.column().classes("items-center gap-2"):
                ui.icon("block").classes("text-3xl text-amber-600")
                ui.label(
                    "CRSP Data Unavailable"
                    if detail.crsp_unavailable
                    else "Error"
                ).classes("text-lg font-bold text-amber-700")
                ui.label(
                    detail.error_message or "Cannot load universe data"
                ).classes("text-sm text-amber-600 text-center")
        return None

    with ui.row().classes("w-full gap-4 mb-3"):
        _metric_card("Symbols", str(detail.symbol_count))
        _metric_card("Type", detail.universe_type.replace("_", " ").title())
        if detail.as_of_date:
            _metric_card("As Of", str(detail.as_of_date))
        if detail.base_universe_id:
            _metric_card("Base", detail.base_universe_id)

    if detail.unresolved_tickers:
        _MAX_SHOWN = 10
        shown = detail.unresolved_tickers[:_MAX_SHOWN]
        remaining = len(detail.unresolved_tickers) - _MAX_SHOWN
        suffix = f" (+{remaining} more)" if remaining > 0 else ""
        with ui.card().classes("w-full p-2 bg-amber-50 border border-amber-200 mb-2"):
            ui.label(
                f"Unresolved tickers: {', '.join(shown)}{suffix}"
            ).classes("text-xs text-amber-700")

    if not detail.constituents:
        ui.label("No constituents found").classes("text-gray-500")
        return None

    rows = []
    for c in detail.constituents:
        mcap_display: str | float = "-"
        adv_display: str | float = "-"
        if c.market_cap is not None:
            # Convert from $thousands to $B for display
            mcap_display = round(c.market_cap / 1_000_000, 2)
        if c.adv_20d is not None:
            # Convert from $ notional to $M for display
            adv_display = round(c.adv_20d / 1_000_000, 2)
        rows.append({
            "permno": c.permno,
            "ticker": c.ticker or "-",
            "market_cap": mcap_display,
            "adv_20d": adv_display,
        })

    grid_options: dict[str, Any] = {
        "columnDefs": [
            {
                "field": "ticker",
                "headerName": "Ticker",
                "sortable": True,
                "filter": True,
                "pinned": "left",
                "minWidth": 100,
            },
            {
                "field": "permno",
                "headerName": "PERMNO",
                "sortable": True,
                "minWidth": 100,
            },
            {
                "field": "market_cap",
                "headerName": "Market Cap ($B)",
                "sortable": True,
                "minWidth": 140,
                ":valueFormatter": "params => params.value !== '-' && params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) + 'B' : '-'",
            },
            {
                "field": "adv_20d",
                "headerName": "ADV 20d ($M)",
                "sortable": True,
                "minWidth": 130,
                ":valueFormatter": "params => params.value !== '-' && params.value != null ? '$' + params.value.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) + 'M' : '-'",
            },
        ],
        "rowData": rows,
        "domLayout": "normal",
        "animateRows": True,
    }

    return ui.aggrid(grid_options).classes("w-full h-96 ag-theme-alpine-dark")


def render_universe_builder(
    *,
    on_save: Callable[[CustomUniverseDefinitionDTO], Awaitable[None]],
    on_cancel: Callable[[], Awaitable[None]],
    on_preview: Callable[[str, list[UniverseFilterDTO], list[str] | None], Awaitable[int | None]] | None = None,
) -> Callable[[], None]:
    """Render interactive universe builder dialog.

    Args:
        on_save: Callback with the definition when saved.
        on_cancel: Callback when cancelled.
        on_preview: Optional preview callback returning count.

    Returns:
        Cleanup callable that cancels pending preview tasks.
        Call before clearing the builder container to prevent
        orphaned async work.
    """
    with ui.card().classes("w-full p-4"):
        ui.label("Create New Universe").classes("text-xl font-bold mb-3")

        name_input = ui.input(
            label="Universe Name",
            placeholder="e.g., Liquid Large Cap",
        ).classes("w-full mb-2")

        base_options = {"SP500": "S&P 500", "R1000": "Russell 1000", "__manual__": "Manual Symbol List"}
        base_select = ui.select(
            options=base_options,
            value="SP500",
            label="Base Universe",
        ).classes("w-full mb-2")

        # Manual symbols textarea (hidden by default)
        manual_container = ui.column().classes("w-full")
        manual_input: list[ui.textarea | None] = [None]

        def _toggle_manual() -> None:
            manual_container.clear()
            if base_select.value == "__manual__":
                with manual_container:
                    manual_input[0] = ui.textarea(
                        label="Symbols (comma-separated)",
                        placeholder="AAPL, MSFT, GOOGL, ...",
                    ).classes("w-full mb-2")
            else:
                manual_input[0] = None

        def _on_base_change(_: Any) -> None:
            _toggle_manual()
            asyncio.ensure_future(_debounced_preview())

        base_select.on_value_change(_on_base_change)

        # Filters section
        ui.label("Filters").classes("text-sm font-semibold mt-2 mb-1")
        filters_container = ui.column().classes("w-full gap-1")
        filter_rows: list[dict[str, Any]] = []

        # Human-readable unit conversion: users enter values in $B / $M,
        # which are converted to CRSP-native units ($K for market cap,
        # $ notional for ADV) when building filter DTOs.
        _FIELD_UNITS: dict[str, tuple[str, float]] = {
            "market_cap": ("$B", 1_000_000),   # CRSP stores in $K; 1B = 1M $K
            "adv_20d": ("$M", 1_000_000),      # CRSP stores in $ notional; 1M = 1e6
        }

        def _add_filter_row() -> None:
            row_data: dict[str, Any] = {"field": "market_cap", "operator": "gt", "value": 0.0}
            filter_rows.append(row_data)
            with filters_container:
                with ui.row().classes("w-full items-center gap-2") as row_el:
                    field_sel = ui.select(
                        options={"market_cap": "Market Cap", "adv_20d": "ADV 20d"},
                        value="market_cap",
                    ).classes("w-32")
                    op_sel = ui.select(
                        options={"gt": ">", "gte": ">=", "lt": "<", "lte": "<="},
                        value="gt",
                    ).classes("w-20")
                    val_input = ui.number(
                        label="Value",
                        value=0.0,
                    ).classes("w-32")
                    unit_label = ui.label(
                        _FIELD_UNITS["market_cap"][0]
                    ).classes("text-xs text-gray-500 font-semibold")

                    def _update(rd: dict[str, Any] = row_data) -> None:
                        rd["field"] = field_sel.value
                        rd["operator"] = op_sel.value
                        # Convert from human-readable unit to CRSP-native unit
                        raw_val = val_input.value if val_input.value is not None else 0.0
                        _, multiplier = _FIELD_UNITS.get(
                            str(field_sel.value), ("", 1)
                        )
                        rd["value"] = raw_val * multiplier
                        asyncio.ensure_future(_debounced_preview())

                    def _on_field_change(_: Any) -> None:
                        unit_text, _ = _FIELD_UNITS.get(
                            str(field_sel.value), ("", 1)
                        )
                        unit_label.set_text(unit_text)
                        _update()

                    field_sel.on_value_change(_on_field_change)
                    op_sel.on_value_change(lambda _: _update())
                    val_input.on_value_change(lambda _: _update())

                    def _remove(
                        el: ui.row = row_el,
                        rd: dict[str, Any] = row_data,
                    ) -> None:
                        if rd in filter_rows:
                            filter_rows.remove(rd)
                        el.delete()
                        asyncio.ensure_future(_debounced_preview())

                    ui.button(icon="close", on_click=_remove).props(
                        "flat dense round"
                    ).classes("text-red-500")

        ui.button("+ Add Filter", on_click=_add_filter_row).props(
            "flat dense"
        ).classes("mb-2")

        # Exclude symbols
        exclude_input = ui.input(
            label="Exclude Symbols (comma-separated)",
            placeholder="TSLA, GME, ...",
        ).classes("w-full mb-2")

        # Preview count
        preview_label = ui.label("").classes("text-sm text-gray-500 mb-2")
        _preview_timer: list[Any] = [None]
        _preview_version: list[int] = [0]
        _disposed: list[bool] = [False]

        async def _run_preview() -> None:
            if _disposed[0]:
                return
            version = _preview_version[0]

            if on_preview is None or base_select.value == "__manual__":
                preview_label.set_text("")
                return
            try:
                filters = [
                    UniverseFilterDTO(
                        field=r["field"],
                        operator=r["operator"],
                        value=float(r["value"]),
                    )
                    for r in filter_rows
                    if r.get("value") is not None
                ]
                exclude_raw = str(exclude_input.value or "")
                exclude = [
                    s.strip().upper()
                    for s in exclude_raw.split(",")
                    if s.strip()
                ] or None
                count = await on_preview(str(base_select.value), filters, exclude)
                # Discard result if disposed or a newer preview was requested
                if _disposed[0] or version != _preview_version[0]:
                    return
                if count is not None:
                    preview_label.set_text(f"Preview: {count:,} symbols matching")
                else:
                    preview_label.set_text("")
            except Exception:
                logger.warning(
                    "universe_builder_preview_error",
                    extra={
                        "base_id": str(base_select.value),
                        "filter_count": len(filter_rows),
                    },
                    exc_info=True,
                )
                if _disposed[0] or version != _preview_version[0]:
                    return
                preview_label.set_text("Preview unavailable")

        # Debounced preview (500ms) — version increments immediately on
        # input change so any in-flight preview is invalidated before the
        # debounce timer fires (prevents briefly showing stale counts).
        async def _debounced_preview() -> None:
            _preview_version[0] += 1
            if _preview_timer[0] is not None:
                _preview_timer[0].cancel()

            async def _delayed() -> None:
                await asyncio.sleep(0.5)
                await _run_preview()

            _preview_timer[0] = asyncio.ensure_future(_delayed())

        def _cancel_preview() -> None:
            _disposed[0] = True
            if _preview_timer[0] is not None:
                _preview_timer[0].cancel()
                _preview_timer[0] = None

        # Register exclusion change handler (after _debounced_preview is defined)
        exclude_input.on_value_change(lambda _: asyncio.ensure_future(_debounced_preview()))

        # Trigger initial preview
        asyncio.ensure_future(_debounced_preview())

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-3"):

            async def _on_cancel() -> None:
                _cancel_preview()
                await on_cancel()

            ui.button("Cancel", on_click=_on_cancel).props("flat")

            async def _save() -> None:
                name = str(name_input.value or "").strip()
                if not name:
                    ui.notify("Universe name is required", type="warning")
                    return

                base_val = str(base_select.value)
                is_manual = base_val == "__manual__"

                manual_symbols: list[str] | None = None
                base_id: str | None = None

                if is_manual:
                    raw = str(manual_input[0].value if manual_input[0] else "")
                    manual_symbols = [
                        s.strip().upper()
                        for s in raw.split(",")
                        if s.strip()
                    ]
                    if not manual_symbols:
                        ui.notify("At least one symbol required", type="warning")
                        return
                else:
                    base_id = base_val

                try:
                    filters = [
                        UniverseFilterDTO(
                            field=r["field"],
                            operator=r["operator"],
                            value=float(r["value"]),
                        )
                        for r in filter_rows
                        if r.get("value") is not None
                    ]

                    exclude_raw = str(exclude_input.value or "")
                    exclude = [
                        s.strip().upper()
                        for s in exclude_raw.split(",")
                        if s.strip()
                    ]

                    definition = CustomUniverseDefinitionDTO(
                        name=name,
                        base_universe_id=base_id,
                        filters=filters,
                        exclude_symbols=exclude,
                        manual_symbols=manual_symbols,
                    )
                except (ValueError, TypeError, ValidationError) as exc:
                    ui.notify(str(exc), type="warning")
                    return

                # Cancel preview only after validation succeeds to avoid
                # permanently disabling preview on failed save attempts.
                # If on_save raises, re-enable preview so the builder
                # remains functional for retry.
                _cancel_preview()
                try:
                    await on_save(definition)
                except Exception:
                    _disposed[0] = False
                    raise

            ui.button("Save Universe", on_click=_save).props("color=primary")

    return _cancel_preview


def _metric_card(label: str, value: str) -> None:
    """Render a simple metric card."""
    with ui.card().classes("p-3 min-w-32"):
        ui.label(label).classes("text-sm text-gray-500")
        ui.label(value).classes("text-lg font-bold")


__all__ = [
    "render_universe_list",
    "render_universe_detail",
    "render_universe_builder",
]
