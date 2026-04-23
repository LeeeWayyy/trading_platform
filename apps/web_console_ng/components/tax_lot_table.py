"""Tax lot AG Grid table component for the tax lots page."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import ui

from libs.web_console_services.tax_lot_service import TaxLot


def render_tax_lot_table(
    lots: list[TaxLot],
    *,
    wash_sale_lot_ids: set[str] | None = None,
    on_close: Callable[[str], Awaitable[None]] | None = None,
    on_select: Callable[[str], Awaitable[None]] | None = None,
    can_close: bool = False,
) -> Any:
    """Render tax lots as an AG Grid table.

    Args:
        lots: List of TaxLot dataclass instances.
        wash_sale_lot_ids: Set of lot_id strings that have active wash sale adjustments.
        on_close: Async callback for closing a lot (lot_id).
        on_select: Async callback for selecting a lot (lot_id).
        can_close: Whether the user has permission to close lots.
    """
    wash_ids = wash_sale_lot_ids or set()
    now = datetime.now(UTC)

    rows: list[dict[str, Any]] = []
    for lot in lots:
        days_held = (now - lot.acquisition_date).days if lot.acquisition_date else 0
        holding_period = "Long-term" if days_held > 365 else "Short-term"
        has_wash_sale = lot.lot_id in wash_ids

        rows.append(
            {
                "lot_id": lot.lot_id,
                "symbol": lot.symbol,
                "quantity": float(lot.quantity),
                "cost_basis": float(lot.cost_basis),
                "cost_per_share": float(lot.cost_basis / lot.quantity) if lot.quantity else 0.0,
                "acquisition_date": lot.acquisition_date.strftime("%Y-%m-%d"),
                "strategy_id": lot.strategy_id or "-",
                "status": lot.status,
                "holding_period": holding_period,
                "days_held": days_held,
                "wash_sale": "Yes" if has_wash_sale else "",
            }
        )

    column_defs: list[dict[str, Any]] = [
        {"headerName": "Symbol", "field": "symbol", "minWidth": 110, "width": 120, "filter": True},
        {
            "headerName": "Quantity",
            "field": "quantity",
            "minWidth": 100,
            "width": 110,
            "type": "numericColumn",
            ":valueFormatter": "x => x.value != null ? x.value.toFixed(2) : '-'",
        },
        {
            "headerName": "Cost Basis",
            "field": "cost_basis",
            "minWidth": 120,
            "width": 130,
            "type": "numericColumn",
            ":valueFormatter": "x => x.value != null ? '$' + x.value.toFixed(2) : '-'",
        },
        {
            "headerName": "Cost/Share",
            "field": "cost_per_share",
            "minWidth": 120,
            "width": 130,
            "type": "numericColumn",
            ":valueFormatter": "x => x.value != null ? '$' + x.value.toFixed(2) : '-'",
        },
        {"headerName": "Acquired", "field": "acquisition_date", "minWidth": 120, "width": 130},
        {"headerName": "Strategy", "field": "strategy_id", "minWidth": 140, "flex": 1},
        {"headerName": "Status", "field": "status", "minWidth": 90, "width": 100},
        {"headerName": "Holding", "field": "holding_period", "minWidth": 120, "width": 130},
        {
            "headerName": "Wash Sale",
            "field": "wash_sale",
            "minWidth": 110,
            "width": 120,
            ":cellStyle": "params => params.value === 'Yes' ? {'color': 'red', 'fontWeight': 'bold'} : {}",
        },
    ]

    grid_options: dict[str, Any] = {
        "columnDefs": column_defs,
        "rowData": rows,
        "defaultColDef": {
            "sortable": True,
            "resizable": True,
            "minWidth": 100,
            "suppressSizeToFit": False,
        },
        "rowSelection": "single" if (on_select or (on_close and can_close)) else None,
        "domLayout": "normal",
    }

    grid = ui.aggrid(grid_options).classes("w-full h-96 ag-theme-alpine-dark")

    if on_select:

        async def _on_row_click(e: Any) -> None:
            try:
                lot_id = e.args["data"]["lot_id"]
            except (KeyError, TypeError, IndexError):
                return
            await on_select(lot_id)

        grid.on("rowClicked", _on_row_click)

    # Close-lot action button (admin only)
    if on_close and can_close:

        async def _close_selected() -> None:
            selected = await grid.get_selected_rows()
            if selected and len(selected) == 1:
                await on_close(selected[0]["lot_id"])
            else:
                ui.notify("Select one lot to close", type="warning")

        ui.button("Close Lot", on_click=_close_selected, icon="close", color="orange").props(
            "flat"
        ).classes("mt-2")

    return grid


__all__ = ["render_tax_lot_table"]
