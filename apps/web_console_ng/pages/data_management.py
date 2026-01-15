"""Data Management page for NiceGUI web console (P5T7).

Combines Data Sync, Data Explorer, and Data Quality into a unified dashboard.

Features:
    - Data Sync: Sync status, manual sync, sync logs, schedule config
    - Data Explorer: Dataset browser, schema viewer, query editor
    - Data Quality: Validation results, anomaly alerts, trends

PARITY: Mirrors UI layout from:
- apps/web_console/pages/data_sync.py
- apps/web_console/pages/data_explorer.py
- apps/web_console/pages/data_quality.py

NOTE: This page currently displays PLACEHOLDER DATA for UI demonstration purposes.
Backend service integration (DataSyncService, DataExplorerService, DataQualityService)
is planned for a future phase. The UI layout and permission checks are production-ready.

TODO(P5T7-followup): Wire up DataSyncService for real sync status and manual triggers
TODO(P5T7-followup): Integrate DataExplorerService for actual query execution
TODO(P5T7-followup): Connect DataQualityService for live validation results
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# Timeouts
FETCH_TIMEOUT_SECONDS = 10.0
TRIGGER_TIMEOUT_SECONDS = 10.0

# Rate limits
MAX_QUERIES_PER_MINUTE = 10
MAX_EXPORTS_PER_HOUR = 5


@ui.page("/data")
@requires_auth
@main_layout
async def data_management_page() -> None:
    """Data Management page."""
    user = get_current_user()

    # Get async db pool
    async_pool = get_db_pool()

    # Page title
    ui.label("Data Management").classes("text-2xl font-bold mb-4")

    # Demo mode banner - backend services not yet integrated
    with ui.card().classes("w-full p-3 mb-4 bg-amber-50 border border-amber-300"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("info", color="amber-700")
            ui.label(
                "Demo Mode: Displaying placeholder data. Backend service integration pending."
            ).classes("text-amber-700")

    # Main tabs for the three data modules
    with ui.tabs().classes("w-full") as tabs:
        tab_sync = ui.tab("Data Sync")
        tab_explorer = ui.tab("Data Explorer")
        tab_quality = ui.tab("Data Quality")

    with ui.tab_panels(tabs, value=tab_sync).classes("w-full"):
        with ui.tab_panel(tab_sync):
            await _render_data_sync_section(user, async_pool)

        with ui.tab_panel(tab_explorer):
            await _render_data_explorer_section(user, async_pool)

        with ui.tab_panel(tab_quality):
            await _render_data_quality_section(user, async_pool)


# === Data Sync Section ===


async def _render_data_sync_section(user: dict[str, Any], db_pool: AsyncConnectionPool | None) -> None:
    """Render Data Sync dashboard section."""
    if not has_permission(user, Permission.VIEW_DATA_SYNC):
        ui.label("Permission denied: VIEW_DATA_SYNC required").classes("text-red-500")
        return

    ui.label("Data Sync Dashboard").classes("text-xl font-bold mb-2")

    # Sub-tabs for sync features
    with ui.tabs().classes("w-full") as sync_tabs:
        tab_status = ui.tab("Sync Status")
        tab_logs = ui.tab("Sync Logs")
        tab_schedule = ui.tab("Schedule Config")

    with ui.tab_panels(sync_tabs, value=tab_status).classes("w-full"):
        with ui.tab_panel(tab_status):
            await _render_sync_status(user)

        with ui.tab_panel(tab_logs):
            await _render_sync_logs(user)

        with ui.tab_panel(tab_schedule):
            await _render_sync_schedule(user)


async def _render_sync_status(user: dict[str, Any]) -> None:
    """Render sync status table."""
    ui.label("Dataset Sync Status").classes("font-bold mb-2")

    # Mock data for demonstration
    statuses: list[dict[str, Any]] = [
        {"dataset": "market_data", "last_sync": datetime.now(), "status": "success", "records": 15000},
        {"dataset": "fundamentals", "last_sync": datetime.now(), "status": "success", "records": 5000},
        {"dataset": "signals", "last_sync": datetime.now(), "status": "pending", "records": 0},
    ]

    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset", "sortable": True},
        {"name": "last_sync", "label": "Last Sync", "field": "last_sync", "sortable": True},
        {"name": "status", "label": "Status", "field": "status"},
        {"name": "records", "label": "Records", "field": "records", "sortable": True},
    ]

    rows: list[dict[str, Any]] = []
    for s in statuses:
        last_sync_val = s.get("last_sync")
        last_sync_str = "-"
        if last_sync_val is not None and hasattr(last_sync_val, "isoformat"):
            last_sync_str = last_sync_val.isoformat()
        rows.append({
            "dataset": s["dataset"],
            "last_sync": last_sync_str,
            "status": s["status"],
            "records": s["records"],
        })

    ui.table(columns=columns, rows=rows).classes("w-full")

    # Manual sync section
    if has_permission(user, Permission.TRIGGER_DATA_SYNC):
        ui.separator().classes("my-4")
        ui.label("Manual Sync").classes("font-bold mb-2")

        with ui.row().classes("gap-4 items-end"):
            dataset_select = ui.select(
                label="Dataset",
                options=[s["dataset"] for s in statuses],
                value=statuses[0]["dataset"] if statuses else None,
            ).classes("w-48")

            reason_input = ui.input(
                label="Reason",
                placeholder="Why run this sync now?",
            ).classes("w-64")

            async def trigger_sync() -> None:
                if not reason_input.value:
                    ui.notify("Please provide a reason for audit logging", type="warning")
                    return

                ui.notify(f"Sync triggered for {dataset_select.value}", type="positive")
                reason_input.value = ""

            ui.button("Trigger Sync", on_click=trigger_sync, color="primary")


async def _render_sync_logs(user: dict[str, Any]) -> None:
    """Render sync logs viewer."""
    ui.label("Recent Sync Logs").classes("font-bold mb-2")

    # Mock logs
    logs: list[dict[str, Any]] = [
        {"timestamp": datetime.now(), "dataset": "market_data", "action": "sync_completed", "duration": "45s"},
        {"timestamp": datetime.now(), "dataset": "fundamentals", "action": "sync_completed", "duration": "30s"},
        {"timestamp": datetime.now(), "dataset": "signals", "action": "sync_started", "duration": "-"},
    ]

    columns: list[dict[str, Any]] = [
        {"name": "timestamp", "label": "Timestamp", "field": "timestamp", "sortable": True},
        {"name": "dataset", "label": "Dataset", "field": "dataset"},
        {"name": "action", "label": "Action", "field": "action"},
        {"name": "duration", "label": "Duration", "field": "duration"},
    ]

    rows: list[dict[str, Any]] = []
    for log in logs:
        ts_val = log.get("timestamp")
        ts_str = "-"
        if ts_val is not None and hasattr(ts_val, "isoformat"):
            ts_str = ts_val.isoformat()
        rows.append({
            "timestamp": ts_str,
            "dataset": log["dataset"],
            "action": log["action"],
            "duration": log["duration"],
        })

    ui.table(columns=columns, rows=rows).classes("w-full")


async def _render_sync_schedule(user: dict[str, Any]) -> None:
    """Render sync schedule configuration."""
    ui.label("Sync Schedule").classes("font-bold mb-2")

    if not has_permission(user, Permission.MANAGE_SYNC_SCHEDULE):
        ui.label("Schedule editing requires MANAGE_SYNC_SCHEDULE permission").classes("text-gray-500")

    # Display current schedules
    schedules: list[dict[str, Any]] = [
        {"dataset": "market_data", "cron": "0 */6 * * *", "enabled": True},
        {"dataset": "fundamentals", "cron": "0 0 * * *", "enabled": True},
        {"dataset": "signals", "cron": "*/30 * * * *", "enabled": False},
    ]

    for sched in schedules:
        with ui.card().classes("w-full p-4 mb-2"):
            with ui.row().classes("items-center gap-4"):
                ui.label(str(sched["dataset"])).classes("font-bold w-32")
                ui.label(f"Cron: {sched['cron']}").classes("text-gray-600")
                status = "Enabled" if sched["enabled"] else "Disabled"
                status_color = "text-green-600" if sched["enabled"] else "text-red-600"
                ui.label(status).classes(status_color)


# === Data Explorer Section ===


async def _render_data_explorer_section(user: dict[str, Any], db_pool: AsyncConnectionPool | None) -> None:
    """Render Data Explorer section."""
    if not has_permission(user, Permission.QUERY_DATA):
        ui.label("Permission denied: QUERY_DATA required").classes("text-red-500")
        return

    ui.label("Data Explorer").classes("text-xl font-bold mb-2")

    with ui.row().classes("w-full gap-4"):
        # Dataset browser sidebar
        with ui.card().classes("w-64 p-4"):
            ui.label("Datasets").classes("font-bold mb-2")

            datasets = ["market_data", "fundamentals", "signals", "positions", "orders"]
            ui.select(
                label="Select Dataset",
                options=datasets,
                value=datasets[0],
            ).classes("w-full")

            ui.separator().classes("my-4")

            ui.label("Schema Preview").classes("font-bold mb-2")
            ui.label("• date: DATE").classes("text-sm text-gray-600")
            ui.label("• symbol: VARCHAR").classes("text-sm text-gray-600")
            ui.label("• value: NUMERIC").classes("text-sm text-gray-600")

        # Main content area
        with ui.column().classes("flex-1"):
            # Query editor
            with ui.card().classes("w-full p-4 mb-4"):
                ui.label("Query Editor").classes("font-bold mb-2")

                ui.textarea(
                    label="SQL Query",
                    placeholder="SELECT * FROM market_data LIMIT 10",
                    value="SELECT * FROM market_data LIMIT 10",
                ).classes("w-full font-mono")

                with ui.row().classes("gap-2 mt-2"):
                    async def run_query() -> None:
                        ui.notify("Query executed (demo mode)", type="positive")

                    ui.button("Run Query", on_click=run_query, color="primary")

                    if has_permission(user, Permission.EXPORT_DATA):
                        async def export_data() -> None:
                            ui.notify("Export started (demo mode)", type="positive")

                        ui.button("Export Results", on_click=export_data).props("flat")

            # Results preview
            with ui.card().classes("w-full p-4"):
                ui.label("Results Preview").classes("font-bold mb-2")

                # Mock results
                columns: list[dict[str, Any]] = [
                    {"name": "date", "label": "Date", "field": "date"},
                    {"name": "symbol", "label": "Symbol", "field": "symbol"},
                    {"name": "value", "label": "Value", "field": "value"},
                ]

                rows: list[dict[str, Any]] = [
                    {"date": "2024-01-15", "symbol": "AAPL", "value": "175.50"},
                    {"date": "2024-01-15", "symbol": "GOOGL", "value": "142.30"},
                    {"date": "2024-01-15", "symbol": "MSFT", "value": "395.80"},
                ]

                ui.table(columns=columns, rows=rows).classes("w-full")


# === Data Quality Section ===


async def _render_data_quality_section(user: dict[str, Any], db_pool: AsyncConnectionPool | None) -> None:
    """Render Data Quality reports section."""
    if not has_permission(user, Permission.VIEW_DATA_QUALITY):
        ui.label("Permission denied: VIEW_DATA_QUALITY required").classes("text-red-500")
        return

    ui.label("Data Quality Reports").classes("text-xl font-bold mb-2")

    # Sub-tabs for quality features
    with ui.tabs().classes("w-full") as quality_tabs:
        tab_validation = ui.tab("Validation Results")
        tab_anomalies = ui.tab("Anomaly Alerts")
        tab_trends = ui.tab("Quality Trends")
        tab_coverage = ui.tab("Data Coverage")

    with ui.tab_panels(quality_tabs, value=tab_validation).classes("w-full"):
        with ui.tab_panel(tab_validation):
            await _render_validation_results()

        with ui.tab_panel(tab_anomalies):
            await _render_anomaly_alerts()

        with ui.tab_panel(tab_trends):
            await _render_quality_trends()

        with ui.tab_panel(tab_coverage):
            await _render_data_coverage()


async def _render_validation_results() -> None:
    """Render validation results table."""
    ui.label("Recent Validation Results").classes("font-bold mb-2")

    # Mock validation results
    results: list[dict[str, Any]] = [
        {"dataset": "market_data", "check": "null_check", "status": "passed", "timestamp": datetime.now()},
        {"dataset": "market_data", "check": "range_check", "status": "passed", "timestamp": datetime.now()},
        {"dataset": "fundamentals", "check": "null_check", "status": "failed", "timestamp": datetime.now()},
    ]

    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset"},
        {"name": "check", "label": "Check", "field": "check"},
        {"name": "status", "label": "Status", "field": "status"},
        {"name": "timestamp", "label": "Timestamp", "field": "timestamp", "sortable": True},
    ]

    rows: list[dict[str, Any]] = []
    for r in results:
        ts_val = r.get("timestamp")
        ts_str = "-"
        if ts_val is not None and hasattr(ts_val, "isoformat"):
            ts_str = ts_val.isoformat()
        rows.append({
            "dataset": r["dataset"],
            "check": r["check"],
            "status": r["status"],
            "timestamp": ts_str,
        })

    ui.table(columns=columns, rows=rows).classes("w-full")


async def _render_anomaly_alerts() -> None:
    """Render anomaly alerts feed."""
    ui.label("Anomaly Alerts").classes("font-bold mb-2")

    # Mock anomalies
    anomalies: list[dict[str, Any]] = [
        {"severity": "high", "message": "Missing data for AAPL on 2024-01-15", "detected": datetime.now()},
        {"severity": "medium", "message": "Unusual price spike for TSLA", "detected": datetime.now()},
        {"severity": "low", "message": "Stale data detected in fundamentals", "detected": datetime.now()},
    ]

    for anomaly in anomalies:
        severity_colors = {
            "high": "bg-red-100 border-red-500 text-red-700",
            "medium": "bg-yellow-100 border-yellow-500 text-yellow-700",
            "low": "bg-blue-100 border-blue-500 text-blue-700",
        }
        severity_val = str(anomaly.get("severity", "unknown"))
        color_class = severity_colors.get(severity_val, "bg-gray-100")

        with ui.card().classes(f"w-full p-4 mb-2 border-l-4 {color_class}"):
            with ui.row().classes("items-center gap-2"):
                ui.label(severity_val.upper()).classes("font-bold")
                detected_val = anomaly.get("detected")
                detected_str = "-"
                if detected_val is not None and hasattr(detected_val, "isoformat"):
                    detected_str = detected_val.isoformat()
                ui.label(detected_str).classes("text-sm text-gray-500")
            ui.label(str(anomaly.get("message", ""))).classes("mt-1")


async def _render_quality_trends() -> None:
    """Render quality trend charts."""
    ui.label("Quality Trends").classes("font-bold mb-2")

    with ui.card().classes("w-full p-4"):
        ui.label("Data Quality Score Over Time").classes("text-lg mb-4")

        # Simple metric cards instead of charts
        with ui.row().classes("gap-4"):
            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("Current Score").classes("text-sm text-gray-500")
                ui.label("98.5%").classes("text-3xl font-bold text-green-600")
            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("7-Day Average").classes("text-sm text-gray-500")
                ui.label("97.2%").classes("text-3xl font-bold text-green-600")
            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("30-Day Average").classes("text-sm text-gray-500")
                ui.label("96.8%").classes("text-3xl font-bold text-green-600")


async def _render_data_coverage() -> None:
    """Render data coverage information."""
    ui.label("Data Coverage").classes("font-bold mb-2")

    # Coverage by dataset
    coverage_data: list[dict[str, Any]] = [
        {"dataset": "market_data", "coverage": 99.5, "missing_days": 2},
        {"dataset": "fundamentals", "coverage": 98.0, "missing_days": 7},
        {"dataset": "signals", "coverage": 95.5, "missing_days": 15},
    ]

    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset"},
        {"name": "coverage", "label": "Coverage %", "field": "coverage", "sortable": True},
        {"name": "missing_days", "label": "Missing Days (30d)", "field": "missing_days", "sortable": True},
    ]

    rows: list[dict[str, Any]] = [
        {
            "dataset": c["dataset"],
            "coverage": f"{c['coverage']}%",
            "missing_days": c["missing_days"],
        }
        for c in coverage_data
    ]

    ui.table(columns=columns, rows=rows).classes("w-full")


__all__ = ["data_management_page"]
