"""Tabbed panel for positions, working orders, fills, and history."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from apps.web_console_ng.components.symbol_filter import (
    SymbolFilterState,
    create_symbol_filter,
    normalize_symbol,
)
from apps.web_console_ng.core.grid_performance import GridPerformanceMonitor
from apps.web_console_ng.core.workspace_persistence import (
    DatabaseUnavailableError,
    get_workspace_service,
)
from apps.web_console_ng.ui.trading_layout import (
    apply_compact_grid_classes,
    apply_compact_grid_options,
)

logger = logging.getLogger(__name__)

TAB_POSITIONS = "positions"
TAB_WORKING = "working"
TAB_FILLS = "fills"
TAB_HISTORY = "history"

VALID_TABS = {TAB_POSITIONS, TAB_WORKING, TAB_FILLS, TAB_HISTORY}

TAB_TITLES = {
    TAB_POSITIONS: "Positions",
    TAB_WORKING: "Working",
    TAB_FILLS: "Fills",
    TAB_HISTORY: "History",
}

WORKING_ORDER_STATUSES = {
    "new",
    "pending_new",
    "partially_filled",
}


def format_tab_label(title: str, count: int | None) -> str:
    """Format tab label with optional count badge."""
    if not count or count <= 0:
        return title
    display = "99+" if count > 99 else str(count)
    return f"{title} ({display})"


def filter_items_by_symbol(items: list[dict[str, Any]], symbol: str | None) -> list[dict[str, Any]]:
    """Filter items by symbol if provided."""
    normalized = normalize_symbol(symbol)
    if not normalized:
        return list(items)
    return [item for item in items if str(item.get("symbol", "")).upper() == normalized]


def filter_working_orders(
    orders: list[dict[str, Any]],
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Filter orders to only working statuses (and optional symbol)."""
    working = [
        order
        for order in orders
        if str(order.get("status", "")).lower() in WORKING_ORDER_STATUSES
    ]
    return filter_items_by_symbol(working, symbol)


@dataclass
class TabbedPanelState:
    """State for the tabbed panel."""

    user_id: str | None
    panel_id: str = "tabbed_panel"
    active_tab: str = TAB_POSITIONS
    symbol_filter: str | None = None

    def normalize_tab(self, value: str | None) -> str:
        """Normalize tab value to a valid tab name."""
        if value in VALID_TABS:
            return value
        return TAB_POSITIONS

    async def load(self, *, service: Any | None = None) -> None:
        """Load persisted tab state."""
        if not self.user_id:
            return
        if service is None:
            service = get_workspace_service()
        try:
            state = await service.load_panel_state(self.user_id, self.panel_id)
        except DatabaseUnavailableError:
            logger.warning("panel_state_load_db_unavailable", extra={"panel_id": self.panel_id})
            return
        if not state:
            return
        active_tab = state.get("active_tab")
        self.active_tab = self.normalize_tab(active_tab)

    async def save(self, *, service: Any | None = None) -> None:
        """Persist tab state."""
        if not self.user_id:
            return
        if service is None:
            service = get_workspace_service()
        try:
            await service.save_panel_state(
                user_id=self.user_id,
                panel_id=self.panel_id,
                state={"active_tab": self.active_tab},
            )
        except DatabaseUnavailableError:
            logger.warning("panel_state_save_db_unavailable", extra={"panel_id": self.panel_id})


class TabbedPanel:
    """Tabbed panel UI with lazy-loaded content and badge counts."""

    def __init__(
        self,
        *,
        state: TabbedPanelState,
        tabs: ui.tabs,
        tab_map: dict[str, ui.tab],
        tab_containers: dict[str, ui.element],
        grid_factories: dict[str, Callable[[], ui.aggrid]],
        symbol_filter: SymbolFilterState,
        on_tab_change: Callable[[str], None] | None = None,
    ) -> None:
        self.state = state
        self._tabs = tabs
        self._tab_map = tab_map
        self._tab_containers = tab_containers
        self._grid_factories = grid_factories
        self._grids: dict[str, ui.aggrid] = {}
        self.symbol_filter = symbol_filter
        self._on_tab_change = on_tab_change

    def set_badge_count(self, tab_name: str, count: int | None) -> None:
        """Update badge count for a tab label."""
        tab = self._tab_map.get(tab_name)
        title = TAB_TITLES.get(tab_name, tab_name.title())
        if tab is None:
            return
        tab.set_label(format_tab_label(title, count))

    def get_grid(self, tab_name: str) -> ui.aggrid | None:
        """Get grid instance if created."""
        return self._grids.get(tab_name)

    def ensure_tab(self, tab_name: str) -> ui.aggrid | None:
        """Ensure a tab's content is created (lazy load)."""
        if tab_name in self._grids:
            return self._grids[tab_name]
        factory = self._grid_factories.get(tab_name)
        container = self._tab_containers.get(tab_name)
        if factory is None or container is None:
            logger.warning("tabbed_panel_missing_factory", extra={"tab": tab_name})
            return None
        with container:
            grid = factory()
        self._grids[tab_name] = grid
        return grid

    def _handle_tab_change(self, value: Any) -> None:
        """Handle tab change events."""
        new_tab = self.state.normalize_tab(str(value) if value is not None else None)
        if new_tab == self.state.active_tab:
            return
        self.state.active_tab = new_tab
        self.ensure_tab(new_tab)
        if self._on_tab_change is not None:
            self._on_tab_change(new_tab)
        asyncio.create_task(self._safe_save_state())

    async def _safe_save_state(self) -> None:
        """Save state with exception handling to avoid unhandled task errors."""
        try:
            await self.state.save()
        except Exception as exc:
            logger.warning(
                "tabbed_panel_state_save_failed",
                extra={"error": type(exc).__name__, "message": str(exc)},
            )


def create_tabbed_panel(
    positions_grid_factory: Callable[[], ui.aggrid],
    orders_grid_factory: Callable[[], ui.aggrid],
    fills_grid_factory: Callable[[], ui.aggrid],
    history_grid_factory: Callable[[], ui.aggrid],
    *,
    state: TabbedPanelState,
    symbol_options: list[str] | None = None,
    on_filter_change: Callable[[str | None], None] | None = None,
    on_tab_change: Callable[[str], None] | None = None,
) -> TabbedPanel:
    """Create the tabbed panel with lazy-loaded tabs."""

    state.active_tab = state.normalize_tab(state.active_tab)

    def _on_filter_change(value: str | None) -> None:
        state.symbol_filter = value
        if on_filter_change is not None:
            on_filter_change(value)

    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center justify-between gap-2"):
            filter_state = create_symbol_filter(
                symbol_options, value=state.symbol_filter, on_change=_on_filter_change
            )
            ui.element("div").classes("flex-1")

        with ui.tabs(value=state.active_tab).classes("w-full") as tabs:
            tab_positions = ui.tab(name=TAB_POSITIONS, label=TAB_TITLES[TAB_POSITIONS])
            tab_working = ui.tab(name=TAB_WORKING, label=TAB_TITLES[TAB_WORKING])
            tab_fills = ui.tab(name=TAB_FILLS, label=TAB_TITLES[TAB_FILLS])
            tab_history = ui.tab(name=TAB_HISTORY, label=TAB_TITLES[TAB_HISTORY])

    tab_map = {
        TAB_POSITIONS: tab_positions,
        TAB_WORKING: tab_working,
        TAB_FILLS: tab_fills,
        TAB_HISTORY: tab_history,
    }

    tab_containers: dict[str, ui.element] = {}

    with ui.tab_panels(tabs, value=state.active_tab).classes("w-full"):
        for tab_name in (TAB_POSITIONS, TAB_WORKING, TAB_FILLS, TAB_HISTORY):
            with ui.tab_panel(tab_name):
                container = ui.column().classes("w-full")
                tab_containers[tab_name] = container

    panel = TabbedPanel(
        state=state,
        tabs=tabs,
        tab_map=tab_map,
        tab_containers=tab_containers,
        grid_factories={
            TAB_POSITIONS: positions_grid_factory,
            TAB_WORKING: orders_grid_factory,
            TAB_FILLS: fills_grid_factory,
            TAB_HISTORY: history_grid_factory,
        },
        symbol_filter=filter_state,
        on_tab_change=on_tab_change,
    )

    tabs.on_value_change(lambda event: panel._handle_tab_change(getattr(event, "value", None)))

    # Lazily create the initial tab content
    panel.ensure_tab(state.active_tab)
    return panel


def create_fills_grid() -> ui.aggrid:
    """Create AG Grid for fills (placeholder)."""
    column_defs = [
        {
            "field": "time",
            "headerName": "Time (UTC)",
            ":valueFormatter": "x => new Date(x.value).toLocaleTimeString('en-US', {timeZone: 'UTC', hour12: false})",
        },
        {"field": "symbol", "headerName": "Symbol", "width": 100},
        {
            "field": "side",
            "headerName": "Side",
            "width": 80,
            "cellStyle": {
                "function": "params.value === 'buy' ? {color: 'var(--profit)'} : {color: 'var(--loss)'}"
            },
        },
        {"field": "qty", "headerName": "Qty", "width": 80},
        {
            "field": "price",
            "headerName": "Price",
            ":valueFormatter": "x => (x.value == null) ? '$--.--' : '$' + Number(x.value).toFixed(2)",
        },
        {
            "field": "status",
            "headerName": "Status",
            ":cellRenderer": "window.statusBadgeRenderer",
            "width": 100,
        },
    ]

    options = apply_compact_grid_options(
        {
            "columnDefs": column_defs,
            "rowData": [],
            "domLayout": "autoHeight",
            ":getRowId": "params => params.data.id",
            "asyncTransactionWaitMillis": 50,
            "suppressAnimationFrame": False,
            "animateRows": True,
            ":onGridReady": "params => { window._fillsGridApi = params.api; if (window.GridThrottle) window.GridThrottle.registerAsyncGrid('fills_grid'); }",
            ":onAsyncTransactionsFlushed": "params => { if (window.GridThrottle) window.GridThrottle.recordTransactionResult(params.api, 'fills_grid', params.results); }",
            ":onRowDataUpdated": "params => { if (window.GridThrottle) window.GridThrottle.recordUpdate(params.api, 'fills_grid'); }",
        }
    )
    grid = ui.aggrid(options).classes("w-full ag-theme-alpine-dark")
    apply_compact_grid_classes(grid)

    monitor = GridPerformanceMonitor("fills_grid")
    monitor.attach_to_grid(grid)

    grid._ready_event = asyncio.Event()  # type: ignore[attr-defined]
    grid.on("gridReady", lambda _: grid._ready_event.set())  # type: ignore[attr-defined]

    return grid


def create_history_grid() -> ui.aggrid:
    """Create AG Grid for order history (placeholder)."""
    column_defs = [
        {
            "field": "time",
            "headerName": "Time (UTC)",
            ":valueFormatter": "x => new Date(x.value).toLocaleTimeString('en-US', {timeZone: 'UTC', hour12: false})",
        },
        {"field": "symbol", "headerName": "Symbol", "width": 100},
        {
            "field": "side",
            "headerName": "Side",
            "width": 80,
            "cellStyle": {
                "function": "params.value === 'buy' ? {color: 'var(--profit)'} : {color: 'var(--loss)'}"
            },
        },
        {"field": "qty", "headerName": "Qty", "width": 80},
        {
            "field": "price",
            "headerName": "Price",
            ":valueFormatter": "x => (x.value == null) ? '$--.--' : '$' + Number(x.value).toFixed(2)",
        },
        {
            "field": "status",
            "headerName": "Status",
            ":cellRenderer": "window.statusBadgeRenderer",
            "width": 100,
        },
    ]

    options = apply_compact_grid_options(
        {
            "columnDefs": column_defs,
            "rowData": [],
            "domLayout": "autoHeight",
            ":getRowId": "params => params.data.id",
            "asyncTransactionWaitMillis": 50,
            "suppressAnimationFrame": False,
            "animateRows": True,
            ":onGridReady": "params => { window._historyGridApi = params.api; if (window.GridThrottle) window.GridThrottle.registerAsyncGrid('history_grid'); }",
            ":onAsyncTransactionsFlushed": "params => { if (window.GridThrottle) window.GridThrottle.recordTransactionResult(params.api, 'history_grid', params.results); }",
            ":onRowDataUpdated": "params => { if (window.GridThrottle) window.GridThrottle.recordUpdate(params.api, 'history_grid'); }",
        }
    )
    grid = ui.aggrid(options).classes("w-full ag-theme-alpine-dark")
    apply_compact_grid_classes(grid)

    monitor = GridPerformanceMonitor("history_grid")
    monitor.attach_to_grid(grid)

    grid._ready_event = asyncio.Event()  # type: ignore[attr-defined]
    grid.on("gridReady", lambda _: grid._ready_event.set())  # type: ignore[attr-defined]

    return grid


__all__ = [
    "TabbedPanelState",
    "TabbedPanel",
    "create_tabbed_panel",
    "create_fills_grid",
    "create_history_grid",
    "filter_items_by_symbol",
    "filter_working_orders",
    "format_tab_label",
    "TAB_POSITIONS",
    "TAB_WORKING",
    "TAB_FILLS",
    "TAB_HISTORY",
]
