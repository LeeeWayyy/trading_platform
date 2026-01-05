"""Trade Journal & Analysis page for NiceGUI web console (P5T8).

Provides trade history viewing with filtering, pagination, and export.

Features:
    - Trade history table with pagination
    - Date range filter (presets and custom)
    - Symbol and side filters
    - Trade statistics summary
    - CSV/Excel export with audit logging

PARITY: Mirrors UI layout from apps/web_console/pages/journal.py

NOTE: This page uses demo mode with placeholder data when services are unavailable.
Backend service integration requires database configuration.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from nicegui import run, ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.config import FEATURE_TRADE_JOURNAL
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, get_authorized_strategies, has_permission

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_RANGE_DAYS = 365


@ui.page("/journal")
@requires_auth
@main_layout
async def trade_journal_page() -> None:
    """Trade Journal & Analysis page."""
    user = get_current_user()

    # Page title
    ui.label("Trade Journal").classes("text-2xl font-bold mb-4")

    # Feature flag check
    if not FEATURE_TRADE_JOURNAL:
        with ui.card().classes("w-full p-6"):
            ui.label("Trade Journal feature is not available.").classes(
                "text-gray-500 text-center"
            )
            ui.label(
                "Set FEATURE_TRADE_JOURNAL=true to enable this feature."
            ).classes("text-gray-400 text-sm text-center")
        logger.info("trade_journal_feature_disabled")
        return

    # Permission check
    if not has_permission(user, Permission.VIEW_TRADES):
        ui.notify("Permission denied: VIEW_TRADES required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_TRADES required.").classes(
                "text-red-500 text-center"
            )
        logger.warning(
            "trade_journal_permission_denied",
            extra={"user_id": user.get("user_id"), "permission": "VIEW_TRADES"},
        )
        return

    # Get authorized strategies
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        with ui.card().classes("w-full p-6"):
            ui.label("You don't have access to any strategies. Contact administrator.").classes(
                "text-amber-600 text-center"
            )
        return

    # Get async db pool
    async_pool = get_db_pool()

    if async_pool is None:
        # Demo mode banner
        with ui.card().classes("w-full p-3 mb-4 bg-amber-50 border border-amber-300"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("info", color="amber-700")
                ui.label(
                    "Demo Mode: Database not configured. Configure DATABASE_URL."
                ).classes("text-amber-700")

        _render_demo_mode()
        return

    # Real mode with database
    await _render_trade_journal(user, authorized_strategies, async_pool)


async def _render_trade_journal(
    user: dict[str, Any],
    authorized_strategies: list[str],
    db_pool: AsyncConnectionPool,
) -> None:
    """Render the full trade journal with real service data."""
    # State with explicit typing
    state: dict[str, Any] = {
        "page": 0,
        "page_size": DEFAULT_PAGE_SIZE,
        "date_preset": "30 Days",
        "start_date": date.today() - timedelta(days=30),
        "end_date": date.today(),
        "symbol_filter": "",
        "side_filter": "All",
    }

    # Filters card
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Filters").classes("text-lg font-bold mb-2")

        with ui.row().classes("w-full gap-4 flex-wrap items-end"):
            # Date preset
            today = date.today()
            presets = {
                "7 Days": (today - timedelta(days=7), today),
                "30 Days": (today - timedelta(days=30), today),
                "90 Days": (today - timedelta(days=90), today),
                "YTD": (date(today.year, 1, 1), today),
                "Custom": None,
            }

            preset_select = ui.select(
                label="Date Range",
                options=list(presets.keys()),
                value=state["date_preset"],
            ).classes("w-32")

            # Custom date inputs (hidden by default)
            date_container = ui.row().classes("gap-2 items-center")
            with date_container:
                ui.label("From:").classes("text-sm")
                from_input = ui.date(
                    value=str(state["start_date"]),
                ).classes("w-36")
                ui.label("To:").classes("text-sm")
                to_input = ui.date(
                    value=str(state["end_date"]),
                ).classes("w-36")

            date_container.set_visibility(False)

            def update_date_visibility() -> None:
                date_container.set_visibility(preset_select.value == "Custom")

            preset_select.on_value_change(lambda _: update_date_visibility())

            # Symbol filter
            symbol_input = ui.input(
                label="Symbol",
                placeholder="e.g., AAPL",
            ).classes("w-24")

            # Side filter
            side_select = ui.select(
                label="Side",
                options=["All", "buy", "sell"],
                value="All",
            ).classes("w-24")

            # Page size
            page_size_select = ui.select(
                label="Per Page",
                options=[25, 50, 100],
                value=state["page_size"],
            ).classes("w-20")

            # Search button
            search_btn = ui.button("Search", icon="search").classes("self-end").props(
                "color=primary"
            )

    # Statistics container
    stats_container = ui.column().classes("w-full mb-4")

    # Trades table container
    trades_container = ui.column().classes("w-full mb-4")

    # Pagination container
    pagination_container = ui.row().classes("w-full justify-center gap-4 mb-4")

    # Export container
    export_container = ui.column().classes("w-full")

    async def load_data() -> None:
        """Load trades and statistics."""
        stats_container.clear()
        trades_container.clear()
        pagination_container.clear()
        export_container.clear()

        # Get filter values
        preset = preset_select.value
        if preset == "Custom":
            try:
                start_date = date.fromisoformat(from_input.value) if from_input.value else state["start_date"]
                end_date = date.fromisoformat(to_input.value) if to_input.value else state["end_date"]
            except ValueError:
                with trades_container:
                    ui.label("Invalid date format.").classes("text-red-500 p-4")
                return

            # Cap date range
            if (end_date - start_date).days > MAX_RANGE_DAYS:
                with trades_container:
                    ui.label(f"Date range capped to {MAX_RANGE_DAYS} days.").classes(
                        "text-amber-600 p-2"
                    )
                start_date = end_date - timedelta(days=MAX_RANGE_DAYS)
        else:
            preset_range = presets.get(preset)
            if preset_range:
                start_date, end_date = preset_range
            else:
                start_date, end_date = state["start_date"], state["end_date"]

        symbol_filter = symbol_input.value.upper().strip() or None
        side_choice = side_select.value
        side_filter = None if side_choice == "All" else side_choice
        page_size = min(int(page_size_select.value), MAX_PAGE_SIZE)
        offset = state["page"] * page_size

        # Import data access
        from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess

        data_access = StrategyScopedDataAccess(db_pool, None, user)

        # Load statistics
        with stats_container:
            with ui.spinner("dots"):
                ui.label("Loading statistics...")

        try:
            stats = await data_access.get_trade_stats(
                date_from=start_date,
                date_to=end_date + timedelta(days=1),
                symbol=symbol_filter,
                side=side_filter,
            )

            stats_container.clear()
            with stats_container:
                _render_trade_stats(stats)
        except Exception as exc:
            logger.exception("Failed to load trade stats")
            stats_container.clear()
            with stats_container:
                ui.label(f"Failed to load statistics: {exc}").classes("text-red-500 p-2")

        # Load trades
        with trades_container:
            with ui.spinner("dots"):
                ui.label("Loading trades...")

        try:
            trades = await data_access.get_trades(
                limit=page_size,
                offset=offset,
                date_from=start_date,
                date_to=end_date + timedelta(days=1),
                symbol=symbol_filter,
                side=side_filter,
            )

            trades_container.clear()
            with trades_container:
                _render_trade_table(trades, page_size, state["page"])

            # Pagination
            with pagination_container:
                ui.button(
                    "Previous",
                    icon="chevron_left",
                    on_click=lambda: prev_page(),
                ).props(f"{'disable' if state['page'] == 0 else ''}")

                ui.label(f"Page {state['page'] + 1}").classes("mx-4")

                ui.button(
                    "Next",
                    icon="chevron_right",
                    on_click=lambda: next_page(),
                ).props(f"{'disable' if len(trades) < page_size else ''}")

        except Exception as exc:
            logger.exception("Failed to load trades")
            trades_container.clear()
            with trades_container:
                ui.label(f"Failed to load trades: {exc}").classes("text-red-500 p-4")

        # Export section
        with export_container:
            await _render_export_section(
                data_access, user, start_date, end_date, symbol_filter, side_filter
            )

    async def prev_page() -> None:
        if state["page"] > 0:
            state["page"] -= 1
            await load_data()

    async def next_page() -> None:
        state["page"] += 1
        await load_data()

    def reset_pagination() -> None:
        state["page"] = 0

    # Connect search button
    async def on_search() -> None:
        reset_pagination()
        await load_data()

    search_btn.on_click(on_search)

    # Initial load
    await load_data()


def _render_trade_stats(stats: dict[str, Any]) -> None:
    """Render trade statistics cards."""
    with ui.card().classes("w-full p-4"):
        ui.label("Trade Statistics").classes("text-lg font-bold mb-2")

        with ui.row().classes("gap-4 flex-wrap"):
            _stat_card("Total Trades", str(stats.get("trade_count", 0)))
            _stat_card("Total Volume", f"${stats.get('total_volume', 0):,.2f}")
            _stat_card("Realized P&L", f"${stats.get('total_pnl', 0):,.2f}")
            _stat_card("Win Rate", f"{stats.get('win_rate', 0):.1%}")
            _stat_card("Avg Trade Size", f"${stats.get('avg_trade_size', 0):,.2f}")


def _stat_card(label: str, value: str) -> None:
    """Render a statistics card."""
    with ui.card().classes("p-3 min-w-28"):
        ui.label(label).classes("text-xs text-gray-500")
        ui.label(value).classes("text-lg font-bold")


def _render_trade_table(trades: list[dict[str, Any]], page_size: int, page: int) -> None:
    """Render trade history table."""
    with ui.card().classes("w-full p-4"):
        ui.label("Trade History").classes("text-lg font-bold mb-2")

        if not trades:
            ui.label("No trades found matching filters.").classes("text-gray-500 p-4")
            return

        columns: list[dict[str, Any]] = [
            {"name": "date", "label": "Date", "field": "date", "sortable": True},
            {"name": "symbol", "label": "Symbol", "field": "symbol", "sortable": True},
            {"name": "side", "label": "Side", "field": "side"},
            {"name": "qty", "label": "Qty", "field": "qty", "sortable": True},
            {"name": "price", "label": "Price", "field": "price", "sortable": True},
            {"name": "pnl", "label": "P&L", "field": "pnl", "sortable": True},
            {"name": "strategy", "label": "Strategy", "field": "strategy"},
        ]

        rows = []
        for trade in trades:
            executed_at = trade.get("executed_at")
            date_str = str(executed_at)[:19] if executed_at else "-"

            pnl = trade.get("realized_pnl", 0)
            pnl_str = f"${pnl:,.2f}" if pnl else "-"

            rows.append({
                "date": date_str,
                "symbol": trade.get("symbol", "-"),
                "side": trade.get("side", "-"),
                "qty": trade.get("qty", 0),
                "price": f"${trade.get('price', 0):,.2f}",
                "pnl": pnl_str,
                "strategy": trade.get("strategy_id", "-"),
            })

        ui.table(columns=columns, rows=rows).classes("w-full")

        ui.label(f"Showing {len(trades)} trades").classes("text-gray-500 text-sm mt-2")


async def _render_export_section(
    data_access: Any,
    user: dict[str, Any],
    start_date: date,
    end_date: date,
    symbol_filter: str | None,
    side_filter: str | None,
) -> None:
    """Render export section with permission and audit checks."""
    with ui.card().classes("w-full p-4"):
        ui.label("Export Trades").classes("text-lg font-bold mb-2")

        if not has_permission(user, Permission.EXPORT_DATA):
            ui.label("Export permission required. Contact administrator.").classes(
                "text-gray-500 p-2"
            )
            return

        with ui.row().classes("gap-4"):
            csv_btn = ui.button("Export to CSV", icon="download")
            excel_btn = ui.button("Export to Excel", icon="download")

        async def export_csv() -> None:
            await _do_export(
                data_access, user, "csv", start_date, end_date, symbol_filter, side_filter
            )

        async def export_excel() -> None:
            await _do_export(
                data_access, user, "xlsx", start_date, end_date, symbol_filter, side_filter
            )

        csv_btn.on_click(export_csv)
        excel_btn.on_click(export_excel)


async def _do_export(
    data_access: Any,
    user: dict[str, Any],
    export_type: str,
    start_date: date,
    end_date: date,
    symbol_filter: str | None,
    side_filter: str | None,
) -> None:
    """Execute export with streaming and audit logging."""
    user_id = user.get("user_id", "unknown")
    authorized_strategies = get_authorized_strategies(user)

    filters: dict[str, Any] = {}
    if symbol_filter:
        filters["symbol"] = symbol_filter
    if side_filter:
        filters["side"] = side_filter

    try:
        ui.notify(f"Exporting to {export_type.upper()}...", type="info")

        if export_type == "csv":
            content, row_count = await _export_csv(data_access, start_date, end_date, filters)
            filename = f"trades_{start_date}_{end_date}.csv"
        else:
            content, row_count = await _export_excel(data_access, start_date, end_date, filters)
            filename = f"trades_{start_date}_{end_date}.xlsx"

        # Audit log
        try:
            from apps.web_console.auth.audit_log import AuditLogger
            from apps.web_console_ng.core.database import get_db_pool

            db_pool = get_db_pool()
            if db_pool:
                audit_logger = AuditLogger(db_pool)
                await audit_logger.log_export(
                    user_id=user_id,
                    export_type=export_type,
                    resource_type="trades",
                    row_count=row_count,
                    metadata={
                        "date_from": str(start_date),
                        "date_to": str(end_date),
                        "filters": filters,
                        "strategy_ids": authorized_strategies,
                    },
                )
        except Exception:
            logger.warning("Failed to log export audit event", exc_info=True)

        logger.info(
            "trade_export_success",
            extra={
                "user_id": user_id,
                "export_type": export_type,
                "row_count": row_count,
                "date_range": f"{start_date}_{end_date}",
            },
        )

        # Download
        ui.download(content, filename)
        ui.notify(f"Exported {row_count} trades to {export_type.upper()}", type="positive")

    except Exception as exc:
        logger.error(
            "trade_export_failed",
            extra={"user_id": user_id, "export_type": export_type, "error": str(exc)},
            exc_info=True,
        )
        ui.notify(f"Export failed: {exc}", type="negative")


async def _export_csv(
    data_access: Any,
    start_date: date,
    end_date: date,
    filters: dict[str, Any],
) -> tuple[bytes, int]:
    """Export trades to CSV using streaming."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Symbol", "Side", "Qty", "Price", "Realized P&L", "Strategy"])

    row_count = 0
    async for trade in data_access.stream_trades_for_export(
        date_from=start_date,
        date_to=end_date + timedelta(days=1),
        **filters,
    ):
        writer.writerow([
            trade.get("executed_at"),
            trade.get("symbol"),
            trade.get("side"),
            trade.get("qty"),
            trade.get("price"),
            trade.get("realized_pnl"),
            trade.get("strategy_id"),
        ])
        row_count += 1

    return output.getvalue().encode("utf-8"), row_count


def _save_workbook_to_bytes(wb: Any) -> bytes:
    """Save workbook to bytes (CPU-intensive, run in thread pool)."""
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


async def _export_excel(
    data_access: Any,
    start_date: date,
    end_date: date,
    filters: dict[str, Any],
) -> tuple[bytes, int]:
    """Export trades to Excel using streaming write mode."""
    from openpyxl import Workbook  # type: ignore[import-untyped]

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Trades")
    ws.append(["Date", "Symbol", "Side", "Qty", "Price", "Realized P&L", "Strategy"])

    row_count = 0
    async for trade in data_access.stream_trades_for_export(
        date_from=start_date,
        date_to=end_date + timedelta(days=1),
        **filters,
    ):
        ws.append([
            trade.get("executed_at"),
            trade.get("symbol"),
            trade.get("side"),
            trade.get("qty"),
            trade.get("price"),
            trade.get("realized_pnl"),
            trade.get("strategy_id"),
        ])
        row_count += 1

    # Offload CPU-intensive workbook save to thread pool
    content = await run.io_bound(_save_workbook_to_bytes, wb)
    return content, row_count


def _render_demo_mode() -> None:
    """Render demo mode with placeholder data."""
    # Demo stats
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Trade Statistics").classes("text-lg font-bold mb-2")

        with ui.row().classes("gap-4 flex-wrap"):
            _stat_card("Total Trades", "1,234")
            _stat_card("Total Volume", "$2,456,789.00")
            _stat_card("Realized P&L", "$45,678.90")
            _stat_card("Win Rate", "54.2%")
            _stat_card("Avg Trade Size", "$1,992.50")

    # Demo table
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Trade History").classes("text-lg font-bold mb-2")

        demo_trades = [
            {"date": "2026-01-03 14:30:00", "symbol": "AAPL", "side": "buy", "qty": 100, "price": "$185.50", "pnl": "$250.00", "strategy": "momentum_v1"},
            {"date": "2026-01-03 14:25:00", "symbol": "MSFT", "side": "sell", "qty": 50, "price": "$375.25", "pnl": "-$125.00", "strategy": "value_v2"},
            {"date": "2026-01-03 14:20:00", "symbol": "GOOGL", "side": "buy", "qty": 25, "price": "$142.75", "pnl": "$75.00", "strategy": "momentum_v1"},
        ]

        columns = [
            {"name": "date", "label": "Date", "field": "date"},
            {"name": "symbol", "label": "Symbol", "field": "symbol"},
            {"name": "side", "label": "Side", "field": "side"},
            {"name": "qty", "label": "Qty", "field": "qty"},
            {"name": "price", "label": "Price", "field": "price"},
            {"name": "pnl", "label": "P&L", "field": "pnl"},
            {"name": "strategy", "label": "Strategy", "field": "strategy"},
        ]

        ui.table(columns=columns, rows=demo_trades).classes("w-full")

    ui.label("Configure DATABASE_URL to view real trade history.").classes(
        "text-gray-500 text-center mt-4"
    )


__all__ = ["trade_journal_page"]
