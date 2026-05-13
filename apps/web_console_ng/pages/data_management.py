"""Data Management page for NiceGUI web console (P6T13).

Combines Data Sync, Data Explorer, and Data Quality into a unified dashboard.
All sections are wired to backend services with per-capability RBAC gating.

Features:
    - Data Sync: Sync status, manual sync, sync logs, schedule config
    - Data Explorer: Dataset browser, schema viewer, query editor
    - Data Quality: Validation results, anomaly alerts, trends, quarantine

Services:
    - DataSyncService: Sync status, logs, schedule, manual trigger
    - DataExplorerService: Dataset browsing, SQL queries, export
    - DataQualityService: Validation, anomaly alerts, trends, quarantine

TODO: Continue extracting Explorer and Quality tabs into independent component
modules under apps/web_console_ng/components/.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import plotly.graph_objects as go
from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components import data_manifest_panel as _data_manifest_panel
from apps.web_console_ng.components import data_quality_section as _data_quality_section
from apps.web_console_ng.components import data_readiness_section as _data_readiness_section
from apps.web_console_ng.components import data_sync_section as _data_sync_section
from apps.web_console_ng.components.data_management_common import (
    TREND_DATASETS as _TREND_DATASETS,
)
from apps.web_console_ng.components.data_management_common import (
    format_datetime as _format_datetime,
)
from apps.web_console_ng.components.data_management_common import (
    get_user_id_safe as _get_user_id_safe,
)
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.root_path import render_client_redirect, resolve_rooted_path_from_ui
from apps.web_console_ng.utils.session import get_or_create_client_id
from libs.data.data_quality.quality_scorer import (
    compute_quality_scores,
    compute_trend_summary,
    normalize_validation_status,
)
from libs.data.data_quality.validation import validate_quarantine_path
from libs.duckdb_catalog import DuckDBCatalog
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.web_console_services.alert_acknowledgment_store import (
    AlertAcknowledgmentStore,
    InMemoryAlertAcknowledgmentStore,
    PostgresAlertAcknowledgmentStore,
)
from libs.web_console_services.data_explorer_service import (
    DATA_EXPORT_RATE_LIMIT,
    DataExplorerService,
)
from libs.web_console_services.data_explorer_service import (
    RateLimitExceeded as ExplorerRateLimitExceeded,
)
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_DATASET_KEY,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
)
from libs.web_console_services.data_quality_service import DataQualityService
from libs.web_console_services.data_readiness_service import (
    HYBRID_CRSP_SIP_DATASET_KEY,
    DataReadinessService,
)
from libs.web_console_services.data_sync_service import DataSyncService
from libs.web_console_services.schemas.data_management import (
    DataPreviewDTO,
    DataReadinessDTO,
    DatasetInfoDTO,
    QueryResultDTO,
    QueryTemplateDTO,
    ReadinessWorkflow,
)

logger = logging.getLogger(__name__)

# Rate limits (displayed in UI messages)
MAX_QUERIES_PER_MINUTE = 10
INLINE_QUERY_RESULT_ROW_LIMIT = 500
_EXPORT_FORMAT_OPTIONS = {"csv": "CSV", "parquet": "Parquet"}

# Dataset name pattern for quarantine drill-down (64-char cap aligns with typical naming)
_DATASET_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Default data directory for quarantine path validation
_DATA_DIR = Path("data")

# Severity normalization: raw service values -> canonical UI levels
_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high": "high",
    "warning": "medium",
    "medium": "medium",
    "low": "low",
    "info": "low",
}

# Severity -> CSS classes for alert cards
_SEVERITY_COLORS: dict[str, str] = {
    "critical": "bg-red-200 border-red-600 text-red-800",
    "high": "bg-red-100 border-red-500 text-red-700",
    "medium": "bg-yellow-100 border-yellow-500 text-yellow-700",
    "low": "bg-blue-100 border-blue-500 text-blue-700",
}

# Acknowledged filter mapping: UI label -> service parameter
_ACK_MAP: dict[str, bool | None] = {"all": None, "unacked": False, "acked": True}

# Quality trend chart threshold lines
GOOD_QUALITY_THRESHOLD = 90.0
CRITICAL_QUALITY_THRESHOLD = 70.0

# Timer cleanup owner key (page-scoped, replaces only this page's callback)
_CLEANUP_OWNER_KEY = "data_management_timers"


def _resolve_alert_acknowledgment_store() -> AlertAcknowledgmentStore:
    """Return a durable acknowledgment store when Postgres is configured.

    Falls back to the in-memory store when the sync DB pool is unavailable, so
    the page can still render the quality drawer with acknowledgment controls
    marked unavailable (per the Phase 5 plan AC).
    """
    try:
        from apps.web_console_ng.core.dependencies import get_sync_db_pool

        pool = get_sync_db_pool()
    except (RuntimeError, ImportError) as exc:
        # Misconfiguration in production: acknowledgments will not persist.
        # The page still renders, and the Acknowledge button is gated on
        # quality_service.acknowledgments_persistent, but operators need to
        # see this in logs so they can wire up DATABASE_URL.
        logger.warning(
            "alert_acknowledgment_store_in_memory",
            extra={"reason": "sync_db_pool_unavailable", "error": str(exc)},
        )
        return InMemoryAlertAcknowledgmentStore()
    return PostgresAlertAcknowledgmentStore(db_pool=pool)


@ui.page("/data")
@requires_auth
@main_layout
async def data_management_page() -> None:
    """Data Management page with live service integration."""
    user = get_current_user()

    # Instantiate services at page-load time (not module level)
    sync_service = DataSyncService()
    explorer_service = DataExplorerService()
    manifest_service = DataManifestService()
    ack_store = _resolve_alert_acknowledgment_store()
    quality_service = DataQualityService(
        manifest_service=manifest_service,
        acknowledgment_store=ack_store,
    )
    readiness_service = DataReadinessService(manifest_service=manifest_service)

    # Page title
    ui.label("Data Management").classes("text-2xl font-bold mb-4")

    # Per-capability tab visibility
    has_sync = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_trigger = has_permission(user, Permission.TRIGGER_DATA_SYNC)
    has_schedule = has_permission(user, Permission.MANAGE_SYNC_SCHEDULE)
    show_sync_tab = has_sync or has_trigger or has_schedule

    has_view_datasets = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_query = has_permission(user, Permission.QUERY_DATA)
    show_explorer_tab = has_view_datasets or has_query

    has_quality = has_permission(user, Permission.VIEW_DATA_QUALITY)

    # Main tabs for the three data modules
    with ui.tabs().classes("w-full") as tabs:
        if show_sync_tab:
            tab_sync = ui.tab("Data Sync")
        if show_explorer_tab:
            tab_explorer = ui.tab("Data Explorer")
        if has_quality:
            tab_quality = ui.tab("Data Quality")

    # Determine default tab
    default_tab = None
    if show_sync_tab:
        default_tab = tab_sync
    elif show_explorer_tab:
        default_tab = tab_explorer
    elif has_quality:
        default_tab = tab_quality

    if default_tab is None:
        ui.label("No data permissions assigned. Contact your administrator.").classes(
            "text-gray-500"
        )
        return

    alpaca_sip_summary = await _render_manifest_transparency(
        user,
        manifest_service,
        readiness_service,
    )

    # Overlap guard flags (per-client scope — each page load creates a new function scope)
    _sync_refreshing = False
    _alerts_refreshing = False

    # Containers for refreshable content
    sync_status_container: ui.column | None = None
    alerts_container: ui.column | None = None
    scores_container: ui.column | None = None
    _load_alerts_fn: Callable[[], Any] | None = None

    with ui.tab_panels(tabs, value=default_tab).classes("w-full"):
        if show_sync_tab:
            with ui.tab_panel(tab_sync):
                sync_status_container = await _render_data_sync_section(user, sync_service)

        if show_explorer_tab:
            with ui.tab_panel(tab_explorer):
                await _render_data_explorer_section(user, explorer_service)

        if has_quality:
            with ui.tab_panel(tab_quality):
                (
                    alerts_container,
                    scores_container,
                    _load_alerts_fn,
                ) = await _render_data_quality_section(
                    user,
                    quality_service,
                    alpaca_sip_summary=alpaca_sip_summary,
                )

    # === Auto-refresh Timers ===
    async def refresh_sync_status() -> None:
        nonlocal _sync_refreshing
        if _sync_refreshing or sync_status_container is None:
            return
        _sync_refreshing = True
        try:
            if not has_permission(user, Permission.VIEW_DATA_SYNC):
                return
            statuses = await sync_service.get_sync_status(user)
            sync_status_container.clear()
            with sync_status_container:
                _build_sync_status_table(statuses)
        except Exception:
            logger.exception(
                "refresh_sync_status_failed",
                extra={
                    "service": "DataSyncService",
                    "method": "get_sync_status",
                    "user_id": _get_user_id_safe(user),
                },
            )
        finally:
            _sync_refreshing = False

    async def refresh_alerts() -> None:
        nonlocal _alerts_refreshing
        if _alerts_refreshing or _load_alerts_fn is None:
            return
        _alerts_refreshing = True
        try:
            await _load_alerts_fn()
        except Exception:
            logger.exception(
                "refresh_alerts_failed",
                extra={
                    "service": "DataQualityService",
                    "method": "get_anomaly_alerts",
                    "user_id": _get_user_id_safe(user),
                },
            )
        finally:
            _alerts_refreshing = False

    # Overlap guard for scores refresh
    _scores_refreshing = False

    async def refresh_scores() -> None:
        nonlocal _scores_refreshing
        if _scores_refreshing or scores_container is None:
            return
        _scores_refreshing = True
        try:
            scores_container.clear()
            with scores_container:
                await _build_quality_score_cards(user, quality_service)
        except Exception:
            logger.exception(
                "refresh_scores_failed",
                extra={
                    "service": "DataQualityService",
                    "method": "refresh_scores",
                    "user_id": _get_user_id_safe(user),
                },
            )
        finally:
            _scores_refreshing = False

    # Timers: sync (30s), alerts (60s), scores (60s)
    timer_sync = ui.timer(30.0, refresh_sync_status)
    timer_alerts = ui.timer(60.0, refresh_alerts)
    timer_scores = ui.timer(60.0, refresh_scores)

    # Unified timer cleanup
    async def _cleanup_timers() -> None:
        timer_sync.cancel()
        timer_alerts.cancel()
        timer_scores.cancel()

    # Register keyed cleanup callback
    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id, _cleanup_timers, owner_key=_CLEANUP_OWNER_KEY
        )


async def _render_manifest_transparency(
    user: dict[str, Any],
    manifest_service: DataManifestService,
    readiness_service: DataReadinessService,
) -> AlpacaSipManifestSummaryDTO | None:
    """Render Phase 1 manifest transparency for authorized Alpaca SIP users."""
    if not has_permission(user, Permission.VIEW_DATA_SYNC):
        return None
    has_alpaca_sip = has_dataset_permission(user, ALPACA_SIP_DATASET_KEY)
    has_hybrid = has_dataset_permission(user, HYBRID_CRSP_SIP_DATASET_KEY)
    if not has_alpaca_sip and not has_hybrid:
        return None

    alpaca_summary = None
    alpaca_summary_failed = False
    if has_alpaca_sip:
        try:
            alpaca_summary = await asyncio.to_thread(manifest_service.get_alpaca_sip_summary)
        except Exception:
            alpaca_summary_failed = True
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_alpaca_sip_summary",
                    "service": "DataManifestService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Manifest status temporarily unavailable", type="warning")
        else:
            _data_manifest_panel.render_manifest_transparency_panel(alpaca_summary)

    readiness_items = []
    readiness_failures = 0
    readiness_targets: list[tuple[str, ReadinessWorkflow]] = []
    failed_readiness_targets: list[str] = []
    if has_alpaca_sip and not alpaca_summary_failed:
        readiness_targets.append((ALPACA_SIP_DATASET_KEY, "simple_backtest"))
    if has_hybrid and not alpaca_summary_failed:
        readiness_targets.append((HYBRID_CRSP_SIP_DATASET_KEY, "hybrid_research_backtest"))

    async def _load_readiness_target(
        dataset: str,
        workflow: ReadinessWorkflow,
    ) -> tuple[DataReadinessDTO | None, str | None]:
        try:
            return (
                await readiness_service.get_readiness_async(
                    user,
                    dataset,
                    workflow,
                    alpaca_sip_summary=alpaca_summary,
                ),
                None,
            )
        except PermissionError:
            logger.warning(
                "readiness_permission_divergence",
                extra={
                    "dataset": dataset,
                    "workflow": workflow,
                    "service": "DataReadinessService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            return None, None
        except ValueError:
            logger.exception(
                "readiness_target_configuration_invalid",
                extra={
                    "dataset": dataset,
                    "workflow": workflow,
                    "service": "DataReadinessService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            return None, f"{dataset}:{workflow}"
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_readiness_async",
                    "service": "DataReadinessService",
                    "dataset": dataset,
                    "workflow": workflow,
                    "user_id": _get_user_id_safe(user),
                },
            )
            return None, f"{dataset}:{workflow}"

    if readiness_targets:
        readiness_results = await asyncio.gather(
            *(_load_readiness_target(dataset, workflow) for dataset, workflow in readiness_targets)
        )
        for readiness, failed_target in readiness_results:
            if readiness is not None:
                readiness_items.append(readiness)
            if failed_target is not None:
                readiness_failures += 1
                failed_readiness_targets.append(failed_target)
    if readiness_items:
        _data_readiness_section.render_readiness_section(readiness_items)
    if readiness_failures:
        logger.warning(
            "readiness_status_partial_failure",
            extra={
                "failure_count": readiness_failures,
                "failed_targets": failed_readiness_targets,
                "service": "DataReadinessService",
                "user_id": _get_user_id_safe(user),
            },
        )
        ui.notify("Some readiness checks are temporarily unavailable", type="warning")
    return alpaca_summary


# =============================================================================
# Data Sync Section
# =============================================================================


async def _render_data_sync_section(
    user: dict[str, Any],
    sync_service: DataSyncService,
) -> ui.column | None:
    return await _data_sync_section.render_data_sync_section(
        user, sync_service, ui_module=ui, logger_obj=logger
    )


async def _render_sync_status(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    has_trigger: bool,
) -> ui.column | None:
    return await _data_sync_section.render_sync_status(
        user,
        sync_service,
        has_view,
        has_trigger,
        ui_module=ui,
        logger_obj=logger,
    )


def _build_sync_status_table(statuses: list[Any]) -> None:
    _data_sync_section.build_sync_status_table(statuses, ui_module=ui)


async def _render_sync_logs(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
) -> None:
    await _data_sync_section.render_sync_logs(
        user, sync_service, has_view, ui_module=ui, logger_obj=logger
    )


def _build_sync_logs_table(logs: list[Any]) -> None:
    _data_sync_section.build_sync_logs_table(logs, ui_module=ui)


async def _render_sync_schedule(
    user: dict[str, Any],
    sync_service: DataSyncService,
    has_view: bool,
    has_manage: bool,
) -> None:
    await _data_sync_section.render_sync_schedule(
        user,
        sync_service,
        has_view,
        has_manage,
        ui_module=ui,
        logger_obj=logger,
    )


# =============================================================================
# Data Explorer Section
# =============================================================================


async def _render_data_explorer_section(
    user: dict[str, Any],
    explorer_service: DataExplorerService,
) -> None:
    """Render Data Explorer section with per-capability gating."""
    has_view_datasets = has_permission(user, Permission.VIEW_DATA_SYNC)
    has_query = has_permission(user, Permission.QUERY_DATA)
    has_export = has_permission(user, Permission.EXPORT_DATA)

    ui.label("Data Explorer").classes("text-xl font-bold mb-2")

    # Track selected dataset
    selected_dataset: dict[str, str | None] = {"value": None}
    dataset_map: dict[str, DatasetInfoDTO] = {}
    refresh_query_controls: Callable[[], None] | None = None
    refresh_adjustment_policy: Callable[[], None] | None = None

    with ui.row().classes("w-full gap-4"):
        # Dataset browser sidebar
        with ui.card().classes("w-64 p-4"):
            ui.label("Datasets").classes("font-bold mb-2")

            if has_view_datasets or has_query:
                datasets: list[DatasetInfoDTO]
                try:
                    datasets = await explorer_service.list_datasets(user)
                except PermissionError as e:
                    ui.notify(str(e), type="negative")
                    datasets = []
                except Exception:
                    logger.exception(
                        "service_call_failed",
                        extra={
                            "method": "list_datasets",
                            "service": "DataExplorerService",
                            "user_id": _get_user_id_safe(user),
                        },
                    )
                    ui.notify("Service temporarily unavailable", type="warning")
                    datasets = []

                dataset_names = [d.name for d in datasets]
                dataset_map.clear()
                dataset_map.update({d.name: d for d in datasets})

                if dataset_names:
                    selected_dataset["value"] = dataset_names[0]

                dataset_select = ui.select(
                    label="Select Dataset",
                    options=dataset_names,
                    value=dataset_names[0] if dataset_names else None,
                ).classes("w-full")

                ui.separator().classes("my-4")

                metadata_container = ui.column().classes("w-full")

                schema_container = ui.column().classes("w-full")

                def _show_dataset_info(ds_name: str | None) -> None:
                    """Show metadata for the selected dataset."""
                    metadata_container.clear()
                    if not ds_name or ds_name not in dataset_map:
                        return
                    info = dataset_map[ds_name]
                    with metadata_container:
                        ui.label("Dataset Info").classes("font-bold mb-1")
                        if info.description:
                            ui.label(info.description).classes("text-sm text-gray-600")
                        queryable_state = info.queryable_state
                        if queryable_state:
                            ui.label(f"State: {queryable_state}").classes("text-sm text-gray-600")
                        if info.tables:
                            ui.label(f"Tables: {', '.join(info.tables)}").classes(
                                "text-sm text-gray-600"
                            )
                        if info.row_count is not None:
                            ui.label(f"Rows: {info.row_count:,}").classes("text-sm text-gray-600")
                        if info.symbol_count is not None:
                            ui.label(f"Symbols: {info.symbol_count:,}").classes(
                                "text-sm text-gray-600"
                            )
                        if info.date_range:
                            start = info.date_range.get("start", "?")
                            end = info.date_range.get("end", "?")
                            ui.label(f"Range: {start} to {end}").classes("text-sm text-gray-600")
                        if info.availability_reason:
                            ui.label(info.availability_reason).classes("text-xs text-amber-600")
                        _render_adjustment_policy_summary(info)

                async def _load_schema(ds_name: str | None) -> None:
                    """Load schema preview for dataset (requires QUERY_DATA)."""
                    schema_container.clear()
                    if not ds_name or not has_query:
                        with schema_container:
                            if not has_query:
                                ui.label("Schema requires query permission").classes(
                                    "text-sm text-gray-400"
                                )
                        return
                    try:
                        preview = await explorer_service.get_dataset_preview(user, ds_name, limit=5)
                        with schema_container:
                            ui.label("Schema Preview").classes("font-bold mb-1")
                            for col in preview.columns:
                                ui.label(f"  {col}").classes("text-sm text-gray-600 font-mono")
                            _render_preview_adjustment_metadata(preview)
                    except ValueError as e:
                        with schema_container:
                            ui.label(str(e)).classes("text-sm text-amber-600")
                    except PermissionError:
                        with schema_container:
                            ui.label("Schema requires query permission").classes(
                                "text-sm text-gray-400"
                            )
                    except Exception:
                        logger.exception(
                            "schema_preview_failed",
                            extra={
                                "method": "get_dataset_preview",
                                "service": "DataExplorerService",
                                "dataset": ds_name,
                                "user_id": _get_user_id_safe(user),
                            },
                        )

                async def on_dataset_change(e: Any) -> None:
                    ds = str(e.value) if e.value else None
                    selected_dataset["value"] = ds
                    _show_dataset_info(ds)
                    await _load_schema(ds)
                    if refresh_query_controls is not None:
                        refresh_query_controls()
                    if refresh_adjustment_policy is not None:
                        refresh_adjustment_policy()

                dataset_select.on_value_change(on_dataset_change)

                # Initial load
                _show_dataset_info(selected_dataset["value"])
                await _load_schema(selected_dataset["value"])
            else:
                ui.label("Dataset listing requires data access permission").classes(
                    "text-gray-400 text-sm"
                )

        # Main content area
        with ui.column().classes("flex-1"):
            adjustment_policy_container = ui.column().classes("w-full mb-4")

            def _selected_dataset_info() -> DatasetInfoDTO | None:
                ds = selected_dataset["value"]
                if ds is None:
                    return None
                return dataset_map.get(ds)

            def _refresh_adjustment_policy() -> None:
                adjustment_policy_container.clear()
                info = _selected_dataset_info()
                if info is None:
                    return
                with adjustment_policy_container:
                    _render_adjustment_policy_summary(info)
                    _render_adjusted_preview_controls(info)

            refresh_adjustment_policy = _refresh_adjustment_policy
            _refresh_adjustment_policy()

            # Query editor
            with ui.card().classes("w-full p-4 mb-4"):
                ui.label("Query Editor").classes("font-bold mb-2")

                if has_query:
                    query_textarea = ui.textarea(
                        label="SQL Query",
                        placeholder="SELECT * FROM dataset LIMIT 10",
                        value="",
                    ).classes("w-full font-mono")

                    results_container = ui.column().classes("w-full mt-4")

                    template_items: dict[str, QueryTemplateDTO] = {}
                    template_select = ui.select(
                        label="Query Template",
                        options={},
                        value=None,
                    ).classes("w-full max-w-md")
                    export_format_select = None
                    if has_export:
                        export_format_select = ui.select(
                            label="Export Format",
                            options=_EXPORT_FORMAT_OPTIONS,
                            value="csv",
                        ).classes("w-36")

                    def refresh_query_templates() -> None:
                        info = _selected_dataset_info()
                        templates = info.query_templates if info is not None else []
                        template_items.clear()
                        options: dict[str, str] = {}
                        for idx, template in enumerate(templates):
                            key = str(idx)
                            template_items[key] = template
                            options[key] = str(template.label)
                        _set_select_options(template_select, options, next(iter(options), None))

                    refresh_query_controls = refresh_query_templates
                    refresh_query_templates()

                    with ui.row().classes("gap-2 mt-2"):

                        def load_query_template() -> None:
                            selected_key = (
                                str(template_select.value)
                                if template_select.value is not None
                                else None
                            )
                            template = (
                                template_items.get(selected_key)
                                if selected_key is not None
                                else None
                            )
                            if template is None:
                                ui.notify("No trusted query template available", type="warning")
                                return
                            query_textarea.value = str(template.sql)

                        def open_sql_explorer() -> None:
                            info = _selected_dataset_info()
                            handoff_url = info.sql_handoff_url if info is not None else None
                            if not handoff_url:
                                ui.notify(
                                    "SQL Explorer handoff requires trusted local data",
                                    type="warning",
                                )
                                return
                            ui.navigate.to(
                                resolve_rooted_path_from_ui(str(handoff_url), ui_module=ui)
                            )

                        async def export_query() -> None:
                            ds = selected_dataset["value"]
                            if not ds:
                                ui.notify("Please select a dataset", type="warning")
                                return
                            query_val = str(query_textarea.value).strip()
                            if not query_val:
                                ui.notify("Please enter a query", type="warning")
                                return
                            export_format = _export_format_value(
                                export_format_select.value if export_format_select else None
                            )
                            if export_format is None:
                                ui.notify("Please select a valid export format", type="warning")
                                return
                            try:
                                job = await explorer_service.export_data(
                                    user,
                                    ds,
                                    query_val,
                                    export_format,
                                )
                                ui.notify(f"Export queued: {job.id}", type="positive")
                            except ValueError as e:
                                ui.notify(f"Export error: {e}", type="negative")
                            except ExplorerRateLimitExceeded:
                                ui.notify(
                                    f"Rate limit: {DATA_EXPORT_RATE_LIMIT} exports/hour",
                                    type="warning",
                                )
                            except PermissionError as e:
                                ui.notify(str(e), type="negative")
                            except Exception:
                                logger.exception(
                                    "service_call_failed",
                                    extra={
                                        "method": "export_data",
                                        "service": "DataExplorerService",
                                        "dataset": ds,
                                        "user_id": _get_user_id_safe(user),
                                    },
                                )
                                ui.notify("Export temporarily unavailable", type="warning")

                        async def run_query() -> None:
                            ds = selected_dataset["value"]
                            if not ds:
                                ui.notify("Please select a dataset", type="warning")
                                return
                            queried_dataset_info = dataset_map.get(ds)
                            query_val = str(query_textarea.value).strip()
                            if not query_val:
                                ui.notify("Please enter a query", type="warning")
                                return
                            try:
                                result = await explorer_service.execute_query(
                                    user,
                                    ds,
                                    query_val,
                                    max_rows=INLINE_QUERY_RESULT_ROW_LIMIT,
                                )
                                results_container.clear()
                                with results_container:
                                    _build_query_results(
                                        result,
                                        dataset_info=queried_dataset_info,
                                    )
                            except ValueError as e:
                                ui.notify(f"Query error: {e}", type="negative")
                            except ExplorerRateLimitExceeded:
                                ui.notify(
                                    f"Rate limit: {MAX_QUERIES_PER_MINUTE} queries/minute",
                                    type="warning",
                                )
                            except PermissionError as e:
                                ui.notify(str(e), type="negative")
                            except Exception:
                                logger.exception(
                                    "service_call_failed",
                                    extra={
                                        "method": "execute_query",
                                        "service": "DataExplorerService",
                                        "dataset": ds,
                                        "user_id": _get_user_id_safe(user),
                                    },
                                )
                                ui.notify("Service temporarily unavailable", type="warning")

                        ui.button("Run Query", on_click=run_query, color="primary")
                        ui.button("Use Template", on_click=load_query_template).props("flat")
                        ui.button("Open in SQL Explorer", on_click=open_sql_explorer).props("flat")
                        if has_export:
                            ui.button("Export Results", on_click=export_query).props("flat")
                else:
                    ui.label("Query execution requires QUERY_DATA permission").classes(
                        "text-gray-400"
                    )


def _export_format_value(value: Any) -> Literal["csv", "parquet"] | None:
    text = str(value).strip().lower() if value is not None else ""
    if text == "csv":
        return "csv"
    if text == "parquet":
        return "parquet"
    return None


def _set_select_options(select: Any, options: dict[str, str], value: str | None) -> None:
    """Update NiceGUI select options across supported versions."""
    set_options = getattr(select, "set_options", None)
    if callable(set_options):
        set_options(options, value=value)
        return
    select.options = options
    select.value = value
    select.update()


def _render_adjustment_policy_summary(payload: DatasetInfoDTO | DataPreviewDTO) -> None:
    """Render raw/adjusted policy details carried by dataset or preview DTOs."""
    lines = _adjustment_policy_lines(payload)
    if not lines:
        return
    with ui.column().classes("w-full p-3 mt-2 border border-amber-200 bg-amber-50"):
        ui.label("Raw/Adjusted Policy").classes("font-bold text-sm text-amber-900")
        for line in lines:
            ui.label(line).classes("text-xs text-amber-800")


def _render_adjusted_preview_controls(dataset_info: DatasetInfoDTO) -> None:
    """Show the future adjusted preview affordance in a disabled state."""
    if dataset_info.canonical_storage_mode is None and dataset_info.backtest_handoff is None:
        return
    handoff = dataset_info.backtest_handoff
    unavailable_reason = "read_time_adjustment_layer_not_defined"
    if handoff is not None:
        unavailable_reason = handoff.adjusted_preview_unavailable_reason or unavailable_reason
    with ui.row().classes("w-full gap-2 items-end mt-2"):
        ui.select(
            label="Preview Mode",
            options={
                "raw": "Raw canonical",
                "adjusted": "Adjusted derived preview",
            },
            value="raw",
        ).classes("w-56").props("disable")
        ui.button("Adjusted Preview").props("flat disable")
        ui.label(unavailable_reason).classes("text-xs text-gray-500")


def _render_preview_adjustment_metadata(preview: DataPreviewDTO) -> None:
    """Render preview-level provenance and null-column reason codes."""
    _render_adjustment_policy_summary(preview)
    lines = _preview_provenance_lines(preview)
    if not lines:
        return
    with ui.column().classes("w-full p-3 mt-2 border border-gray-200 bg-gray-50"):
        ui.label("Preview Provenance").classes("font-bold text-sm text-gray-800")
        for line in lines:
            ui.label(line).classes("text-xs text-gray-600")


def _preview_provenance_lines(preview: DataPreviewDTO) -> list[str]:
    fields: tuple[tuple[str, str | None], ...] = (
        ("manifest_id", preview.manifest_id),
        ("manifest_reference", preview.manifest_reference),
        ("manifest_checksum", preview.manifest_checksum),
        ("provider_id", preview.provider_id),
        ("provider_version", preview.provider_version),
        ("source_feed", preview.source_feed),
    )
    lines: list[str] = []
    for label, value in fields:
        if value is not None and str(value):
            lines.append(f"{label}: {value}")
    return lines


def _adjustment_policy_lines(payload: DatasetInfoDTO | DataPreviewDTO) -> list[str]:
    lines: list[str] = []
    canonical_mode = payload.canonical_storage_mode
    read_time_mode = payload.read_time_adjustment_mode
    adjustment_mode = payload.adjustment_mode
    if canonical_mode is not None:
        lines.append(f"canonical_storage_mode: {canonical_mode}")
    if read_time_mode is not None:
        lines.append(f"read_time_adjustment_mode: {read_time_mode}")
    if adjustment_mode is not None:
        lines.append(f"adjustment_mode: {adjustment_mode}")

    null_reasons = payload.null_column_reasons
    for column, reason in sorted(null_reasons.items()):
        lines.append(f"{column}: {reason}")

    displayed_reasons = {str(reason) for reason in null_reasons.values()}
    warnings = sorted({str(warning) for warning in payload.warnings})
    for warning in warnings:
        if warning not in displayed_reasons and warning not in lines:
            lines.append(warning)

    handoff = payload.backtest_handoff
    if handoff is not None:
        roles = handoff.data_roles
        for role, provenance in sorted(roles.items()):
            dataset = provenance.dataset or "-"
            storage_mode = provenance.canonical_storage_mode or "-"
            read_mode = provenance.read_time_adjustment_mode or "-"
            lines.append(
                f"backtest role {role}: {dataset}; "
                f"storage={storage_mode}; read_time_adjustment={read_mode}"
            )
        reason_codes = handoff.reason_codes
        if reason_codes:
            lines.append("backtest_handoff_reasons: " + ", ".join(sorted(reason_codes)))
    return lines


def _build_query_results(
    result: QueryResultDTO,
    *,
    dataset_info: DatasetInfoDTO | None = None,
) -> None:
    """Build query results table from QueryResultDTO."""
    if dataset_info is not None:
        _render_adjustment_policy_summary(dataset_info)

    if not result.columns:
        ui.label("No results").classes("text-gray-500")
        return

    columns: list[dict[str, Any]] = [
        {"name": col, "label": col, "field": col, "sortable": True} for col in result.columns
    ]
    ui.table(columns=columns, rows=result.rows).classes("w-full")

    with ui.row().classes("gap-4 mt-2"):
        ui.label(f"Showing: {len(result.rows)} rows").classes("text-sm text-gray-600")
        if result.has_more:
            ui.label("(more results available)").classes("text-sm text-amber-600")


# =============================================================================
# Data Quality Section
# =============================================================================


async def _render_data_quality_section(
    user: dict[str, Any],
    quality_service: DataQualityService,
    *,
    alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
) -> tuple[ui.column | None, ui.column | None, Callable[[], Any] | None]:
    """Render Data Quality reports section.

    Returns:
        Tuple of (alerts_container, scores_container, load_alerts_fn) for
        auto-refresh timers. load_alerts_fn respects current filter state.
    """
    ui.label("Data Quality Reports").classes("text-xl font-bold mb-2")

    if has_permission(user, Permission.VIEW_DATA_QUALITY) and has_dataset_permission(
        user, ALPACA_SIP_DATASET_KEY
    ):
        try:
            alpaca_quality = await quality_service.get_alpaca_sip_quality_summary(
                user,
                alpaca_sip_summary=alpaca_sip_summary,
            )
            _data_quality_section.render_quality_summary(alpaca_quality)
        except PermissionError:
            logger.warning(
                "alpaca_sip_quality_permission_divergence",
                extra={
                    "method": "get_alpaca_sip_quality_summary",
                    "service": "DataQualityService",
                    "user_id": _get_user_id_safe(user),
                },
            )
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_alpaca_sip_quality_summary",
                    "service": "DataQualityService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Alpaca SIP quality inputs temporarily unavailable", type="warning")

    # Quality score cards at top of section
    scores_container = ui.column().classes("w-full mb-4")
    with scores_container:
        await _build_quality_score_cards(
            user,
            quality_service,
            alpaca_sip_summary=alpaca_sip_summary,
        )

    with ui.tabs().classes("w-full") as quality_tabs:
        tab_validation = ui.tab("Validation Results")
        tab_anomalies = ui.tab("Anomaly Alerts")
        tab_trends = ui.tab("Quality Trends")
        tab_quarantine = ui.tab("Quarantine Inspector")

    alerts_container: ui.column | None = None
    load_alerts_fn: Callable[[], Any] | None = None

    with ui.tab_panels(quality_tabs, value=tab_validation).classes("w-full"):
        with ui.tab_panel(tab_validation):
            await _render_validation_results(
                user,
                quality_service,
                alpaca_sip_summary=alpaca_sip_summary,
            )

        with ui.tab_panel(tab_anomalies):
            alerts_container, load_alerts_fn = await _render_anomaly_alerts(user, quality_service)

        with ui.tab_panel(tab_trends):
            await _render_quality_trends(user, quality_service)

        with ui.tab_panel(tab_quarantine):
            await _render_quarantine_inspector(user, quality_service)

    return alerts_container, scores_container, load_alerts_fn


async def _build_quality_score_cards(
    user: dict[str, Any],
    quality_service: DataQualityService,
    *,
    alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
) -> None:
    """Build quality score cards per dataset using compute_quality_scores()."""
    try:
        validations = await quality_service.get_validation_results(
            user,
            dataset=None,
            alpaca_sip_summary=alpaca_sip_summary,
        )
        alerts = await quality_service.get_anomaly_alerts(user, severity=None, acknowledged=None)
        quarantine = await quality_service.get_quarantine_status(user)
    except PermissionError as exc:
        ui.notify(str(exc), type="negative")
        return
    except Exception:
        logger.exception(
            "quality_score_load_failed",
            extra={
                "service": "DataQualityService",
                "user_id": _get_user_id_safe(user),
            },
        )
        ui.notify("Could not load quality scores", type="warning")
        return

    scores = compute_quality_scores(validations, alerts, quarantine)

    if not scores:
        ui.label("No quality data available").classes("text-gray-500")
        return

    ui.label("Quality Scores").classes("font-bold mb-2")
    with ui.row().classes("gap-4 flex-wrap"):
        for score in scores:
            # Color based on overall score
            if score.overall_score is None:
                color_cls = "text-gray-500"
                score_text = "N/A"
            elif score.overall_score >= 90.0:
                color_cls = "text-green-600"
                score_text = f"{score.overall_score:.1f}%"
            elif score.overall_score >= 70.0:
                color_cls = "text-amber-600"
                score_text = f"{score.overall_score:.1f}%"
            else:
                color_cls = "text-red-600"
                score_text = f"{score.overall_score:.1f}%"

            with ui.card().classes("p-4 min-w-[200px]"):
                ui.label(score.dataset).classes("font-bold text-lg")
                ui.label(score_text).classes(f"text-3xl font-bold {color_cls}")
                with ui.row().classes("gap-4 mt-2"):
                    rate_text = (
                        f"{score.validation_pass_rate:.0f}%"
                        if score.validation_pass_rate is not None
                        else "N/A"
                    )
                    ui.label(f"Pass Rate: {rate_text}").classes("text-sm text-gray-600")
                    ui.label(f"Anomalies: {score.anomaly_count}").classes("text-sm text-gray-600")
                    ui.label(f"Quarantine: {score.quarantine_count}").classes(
                        "text-sm text-gray-600"
                    )


async def _render_validation_results(
    user: dict[str, Any],
    quality_service: DataQualityService,
    *,
    alpaca_sip_summary: AlpacaSipManifestSummaryDTO | None = None,
) -> None:
    """Render validation results table with dataset filter."""
    ui.label("Recent Validation Results").classes("font-bold mb-2")

    dataset_filter = ui.select(
        label="Dataset Filter",
        options=["all", *_TREND_DATASETS],
        value="all",
    ).classes("w-40 mb-4")

    results_container = ui.column().classes("w-full")

    async def load_results() -> None:
        ds = None if dataset_filter.value == "all" else str(dataset_filter.value)
        try:
            results = await quality_service.get_validation_results(
                user,
                dataset=ds,
                alpaca_sip_summary=alpaca_sip_summary,
            )
            results_container.clear()
            with results_container:
                _build_validation_table(results)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_validation_results",
                    "service": "DataQualityService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    dataset_filter.on_value_change(lambda _: load_results())
    await load_results()


def _build_validation_table(results: list[Any]) -> None:
    """Build validation results table from ValidationResultDTO list."""
    _status_colors: dict[str, str] = {
        "passed": "text-green-600",
        "failed": "text-red-600",
        "warning": "text-amber-600",
    }

    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset"},
        {"name": "validation_type", "label": "Check", "field": "validation_type"},
        {"name": "status", "label": "Status", "field": "status"},
        {"name": "expected", "label": "Expected", "field": "expected"},
        {"name": "actual", "label": "Actual", "field": "actual"},
        {
            "name": "created_at",
            "label": "Timestamp",
            "field": "created_at",
            "sortable": True,
        },
    ]
    rows: list[dict[str, Any]] = []
    for r in results:
        normalized = normalize_validation_status(r.status)
        rows.append(
            {
                "dataset": r.dataset,
                "validation_type": r.validation_type,
                "status": normalized,
                "expected": str(r.expected_value) if r.expected_value is not None else "-",
                "actual": str(r.actual_value) if r.actual_value is not None else "-",
                "created_at": _format_datetime(r.created_at),
            }
        )
    ui.table(columns=columns, rows=rows).classes("w-full")


async def _render_anomaly_alerts(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> tuple[ui.column, Callable[[], Any]]:
    """Render anomaly alerts with filters.

    Returns:
        Tuple of (alerts_container, load_alerts_callable) so that the timer
        refresh can reuse the same filter-aware path.
    """
    ui.label("Anomaly Alerts").classes("font-bold mb-2")

    with ui.row().classes("gap-4 mb-4"):
        severity_filter = ui.select(
            label="Severity",
            options=["all", "critical", "high", "medium", "low"],
            value="all",
        ).classes("w-32")
        ack_filter = ui.select(
            label="Status",
            options=["all", "unacked", "acked"],
            value="unacked",
        ).classes("w-32")

    alerts_container = ui.column().classes("w-full")

    async def load_alerts() -> None:
        ack_mapped = _ACK_MAP[str(ack_filter.value)]
        sev_value = str(severity_filter.value)
        # Always fetch all severities from backend: backend labels (e.g. "warning")
        # differ from UI canonical labels (e.g. "medium"), so passing the UI value
        # directly would miss matching alerts. Client-side normalization handles
        # the mapping and filtering.
        # TODO(perf): Enhance service to accept severity list (e.g. ["warning",
        # "medium"]) for server-side filtering via reverse _SEVERITY_MAP lookup.
        try:
            raw_alerts = await quality_service.get_anomaly_alerts(
                user, severity=None, acknowledged=ack_mapped
            )
            filtered, sev_lookup = _normalize_and_filter_alerts(raw_alerts, sev_value)

            alerts_container.clear()
            with alerts_container:
                _build_anomaly_alert_cards(filtered, user, quality_service, sev_lookup)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_anomaly_alerts",
                    "service": "DataQualityService",
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    severity_filter.on_value_change(lambda _: load_alerts())
    ack_filter.on_value_change(lambda _: load_alerts())
    await load_alerts()

    return alerts_container, load_alerts


def _normalize_and_filter_alerts(
    raw_alerts: list[Any], severity_filter: str
) -> tuple[list[Any], dict[str, str]]:
    """Normalize severity values and apply client-side filter.

    Returns a tuple of (filtered_alerts, severity_lookup) where severity_lookup
    maps alert.id -> normalized severity string. This avoids mutating Pydantic
    DTO objects which reject undeclared attribute writes.
    """
    result: list[Any] = []
    severity_lookup: dict[str, str] = {}
    for alert in raw_alerts:
        normalized = _SEVERITY_MAP.get(alert.severity.lower(), alert.severity.lower())
        severity_lookup[alert.id] = normalized
        if severity_filter == "all" or normalized == severity_filter:
            result.append(alert)
    return result, severity_lookup


def _build_anomaly_alert_cards(
    alerts: list[Any],
    user: dict[str, Any],
    quality_service: DataQualityService,
    severity_lookup: dict[str, str],
) -> None:
    """Build alert cards from normalized AnomalyAlertDTO list."""
    ack_persistent = quality_service.acknowledgments_persistent
    user_can_ack = has_permission(user, Permission.ACKNOWLEDGE_ALERTS)

    if not alerts:
        ui.label("No alerts matching filters").classes("text-gray-500")
        return

    if user_can_ack and not ack_persistent:
        ui.label(
            "Acknowledgment controls are unavailable until server-side persistence is enabled"
        ).classes("text-xs text-amber-700 mb-2")

    for alert in alerts:
        sev = severity_lookup.get(alert.id, alert.severity)
        color_class = _SEVERITY_COLORS.get(sev, "bg-gray-100 border-gray-300 text-gray-700")
        with ui.card().classes(f"w-full p-4 mb-2 border-l-4 {color_class}"):
            with ui.row().classes("items-center gap-2"):
                ui.label(sev.upper()).classes("font-bold")
                ui.label(alert.metric).classes("text-sm")
                ui.label(_format_datetime(alert.created_at)).classes("text-sm text-gray-500")
                if alert.acknowledged:
                    ui.label("ACK").classes("text-xs bg-green-200 px-2 py-0.5 rounded")
            ui.label(alert.message).classes("mt-1")

            if alert.deviation_pct is not None:
                ui.label(
                    f"Deviation: {alert.deviation_pct:.1f}% "
                    f"(current: {alert.current_value}, expected: {alert.expected_value})"
                ).classes("text-sm text-gray-600 mt-1")

            if user_can_ack and ack_persistent and not alert.acknowledged:
                _alert_id = alert.id
                _alert_dataset = alert.dataset
                _alert_metric = alert.metric
                _alert_severity = alert.severity

                async def ack_alert(
                    aid: str = _alert_id,
                    ds: str = _alert_dataset,
                    metric: str = _alert_metric,
                    severity: str = _alert_severity,
                ) -> None:
                    try:
                        ack = await quality_service.acknowledge_alert(
                            user,
                            aid,
                            "Acknowledged via dashboard",
                            dataset=ds,
                            metric=metric,
                            severity=severity,
                        )
                        ui.notify(
                            f"Alert acknowledged by {ack.acknowledged_by}",
                            type="positive",
                        )
                    except PermissionError as e:
                        ui.notify(str(e), type="negative")
                    except Exception:
                        logger.exception(
                            "service_call_failed",
                            extra={
                                "method": "acknowledge_alert",
                                "service": "DataQualityService",
                                "alert_id": aid,
                                "user_id": _get_user_id_safe(user),
                            },
                        )
                        ui.notify("Service temporarily unavailable", type="warning")

                ui.button("Acknowledge", on_click=ack_alert).props("flat dense").classes("mt-1")


async def _render_quality_trends(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Render quality trend charts with dataset selector."""
    ui.label("Quality Trends").classes("font-bold mb-2")

    accessible = [ds for ds in _TREND_DATASETS if has_dataset_permission(user, ds)]
    if not accessible:
        ui.label("No accessible datasets for trend analysis.").classes("text-gray-500")
        return

    dataset_select = ui.select(
        label="Dataset",
        options=accessible,
        value=accessible[0],
    ).classes("w-40 mb-4")

    trend_container = ui.column().classes("w-full")

    async def load_trends() -> None:
        ds = str(dataset_select.value)
        try:
            trend = await quality_service.get_quality_trends(user, dataset=ds, days=30)
            trend_container.clear()
            with trend_container:
                _build_quality_trend_chart(trend)
        except PermissionError as e:
            ui.notify(str(e), type="negative")
        except Exception:
            logger.exception(
                "service_call_failed",
                extra={
                    "method": "get_quality_trends",
                    "service": "DataQualityService",
                    "dataset": ds,
                    "user_id": _get_user_id_safe(user),
                },
            )
            ui.notify("Service temporarily unavailable", type="warning")

    dataset_select.on_value_change(lambda _: load_trends())
    await load_trends()


def _build_quality_trend_chart(trend: Any) -> None:
    """Build Plotly trend chart with threshold lines and trend summary cards."""
    if not trend.data_points:
        with ui.card().classes("w-full p-4"):
            ui.label(f"Quality Trends - {trend.dataset} ({trend.period_days}d)").classes(
                "text-lg mb-4"
            )
            ui.label("No trend data available yet").classes("text-gray-500")
        return

    # Build Plotly chart with threshold lines
    fig = go.Figure()
    unique_metrics = list({p.metric for p in trend.data_points})
    for metric_name in sorted(unique_metrics):
        metric_points = [p for p in trend.data_points if p.metric == metric_name]
        metric_points.sort(key=lambda p: p.date)
        fig.add_trace(
            go.Scatter(
                x=[p.date for p in metric_points],
                y=[p.value for p in metric_points],
                mode="lines+markers",
                name=metric_name,
            )
        )

    fig.add_hline(
        y=GOOD_QUALITY_THRESHOLD,
        line_dash="dash",
        line_color="green",
        annotation_text=f"Good ({GOOD_QUALITY_THRESHOLD:.0f})",
        annotation_position="top right",
    )
    fig.add_hline(
        y=CRITICAL_QUALITY_THRESHOLD,
        line_dash="dash",
        line_color="red",
        annotation_text=f"Critical ({CRITICAL_QUALITY_THRESHOLD:.0f})",
        annotation_position="bottom right",
    )
    fig.update_layout(
        title=f"Quality Trends - {trend.dataset}",
        xaxis_title="Date",
        yaxis_title="Score",
    )
    ui.plotly(fig).classes("w-full")

    # Compute trend summary per metric using quality_scorer
    for metric_name in sorted(unique_metrics):
        summary = compute_trend_summary(trend, metric_name)

        # Trend arrow and color
        _direction_display: dict[str, tuple[str, str]] = {
            "improving": ("\u2191", "text-green-600"),
            "stable": ("\u2192", "text-gray-600"),
            "degrading": ("\u2193", "text-red-600"),
            "insufficient_data": ("\u2014", "text-gray-400"),
        }
        arrow, direction_color = _direction_display.get(
            summary.trend_direction, ("\u2014", "text-gray-400")
        )

        if len(unique_metrics) > 1:
            ui.label(f"Metric: {metric_name}").classes("font-bold mt-4 mb-2")

        with ui.row().classes("gap-4 mt-2"):
            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("Current Score").classes("text-sm text-gray-500")
                current_text = (
                    f"{summary.current_score:.1f}%" if summary.current_score is not None else "N/A"
                )
                ui.label(current_text).classes("text-3xl font-bold text-green-600")

            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("7-Day Average").classes("text-sm text-gray-500")
                avg7_text = f"{summary.avg_7d:.1f}%" if summary.avg_7d is not None else "N/A"
                ui.label(avg7_text).classes("text-3xl font-bold text-green-600")

            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("30-Day Average").classes("text-sm text-gray-500")
                avg30_text = f"{summary.avg_30d:.1f}%" if summary.avg_30d is not None else "N/A"
                ui.label(avg30_text).classes("text-3xl font-bold text-green-600")

            with ui.card().classes("flex-1 p-4 text-center"):
                ui.label("Trend").classes("text-sm text-gray-500")
                ui.label(f"{arrow} {summary.trend_direction}").classes(
                    f"text-xl font-bold {direction_color}"
                )

        # Degradation alert
        if summary.degradation_alert:
            with ui.card().classes("w-full p-4 mt-2 bg-amber-100 border-l-4 border-amber-500"):
                ui.label(
                    f"Quality degradation detected: 7-day average is significantly "
                    f"below 30-day average for {trend.dataset}"
                ).classes("text-amber-800 font-bold")


async def _render_quarantine_inspector(
    user: dict[str, Any],
    quality_service: DataQualityService,
) -> None:
    """Render quarantine inspector with drill-down preview via DuckDB."""
    ui.label("Quarantine Inspector").classes("font-bold mb-2")

    try:
        entries = await quality_service.get_quarantine_status(user)
    except PermissionError as exc:
        ui.notify(str(exc), type="negative")
        return
    except Exception:
        logger.exception(
            "service_call_failed",
            extra={
                "method": "get_quarantine_status",
                "service": "DataQualityService",
                "user_id": _get_user_id_safe(user),
            },
        )
        ui.notify("Service temporarily unavailable", type="warning")
        return

    if not entries:
        ui.label("No quarantine entries").classes("text-gray-500")
        return

    # Group entries by dataset
    by_dataset: dict[str, list[Any]] = {}
    for entry in entries:
        by_dataset.setdefault(entry.dataset, []).append(entry)

    preview_container = ui.column().classes("w-full mt-4")

    for ds_name in sorted(by_dataset):
        ds_entries = by_dataset[ds_name]
        with ui.expansion(f"{ds_name} ({len(ds_entries)} entries)").classes("w-full mb-2"):
            for entry in ds_entries:
                with ui.card().classes("w-full p-3 mb-2 border-l-4 border-amber-400"):
                    with ui.row().classes("items-center gap-4"):
                        ui.label(entry.reason).classes("font-bold")
                        ui.label(entry.quarantine_path).classes("text-sm text-gray-500 font-mono")
                        ui.label(_format_datetime(entry.created_at)).classes(
                            "text-sm text-gray-400"
                        )

                    # Drill-down button
                    _entry = entry

                    async def inspect_entry(
                        qe: Any = _entry,
                    ) -> None:
                        preview_container.clear()
                        with preview_container:
                            await _load_quarantine_preview(qe)

                    ui.button("Inspect", on_click=inspect_entry).props("flat dense").classes("mt-1")


async def _load_quarantine_preview(entry: Any) -> None:
    """Load quarantine data preview via DuckDB with path validation."""
    # Step 1: Validate path (CPU-only, no filesystem I/O)
    try:
        safe_path = validate_quarantine_path(entry.quarantine_path, _DATA_DIR)
    except ValueError as exc:
        ui.label(f"Path validation failed: {exc}").classes("text-red-600")
        return

    # Step 2: Sanitize dataset name (CPU-only regex check)
    if not _DATASET_PATTERN.match(entry.dataset):
        ui.label(f"Invalid dataset name: {entry.dataset!r}").classes("text-red-600")
        return

    # Steps 3-4: Filesystem checks + DuckDB query in worker thread
    def _validate_and_query() -> tuple[str, Any]:
        """Run filesystem validation and DuckDB query (sync, worker thread).

        Returns (status, result) where status is "ok", "path_escape",
        "no_dir", "no_file", or "error".
        """
        # TOCTOU re-validation at point of use
        quarantine_root = (_DATA_DIR / "quarantine").resolve()
        if not safe_path.resolve().is_relative_to(quarantine_root):
            return ("path_escape", None)

        if not safe_path.exists():
            return ("no_dir", None)

        # Quarantine dirs contain symbol-named files ({SYMBOL}.parquet),
        # not dataset-named files. Glob for any parquet files to preview.
        parquet_files = sorted(safe_path.glob("*.parquet"))
        if not parquet_files:
            return ("no_file", None)

        # Read up to 100 rows across all files in the partition
        file_paths = [str(f) for f in parquet_files]
        with DuckDBCatalog() as catalog:
            result = catalog.query(
                "SELECT * FROM read_parquet(?) LIMIT 100",
                params=[file_paths],
            )
            return ("ok", result)

    try:
        status, result = await asyncio.to_thread(_validate_and_query)

        if status == "path_escape":
            ui.label("Path validation failed at access time").classes("text-red-600")
            return
        if status == "no_dir":
            ui.label(
                "Preview unavailable — quarantine directory does not exist "
                "yet. Full drill-down available when the quality service is "
                "DB-backed."
            ).classes("text-gray-500 italic")
            return
        if status == "no_file":
            ui.label(
                f"Preview unavailable — {entry.dataset}.parquet not found "
                f"in quarantine directory. Full drill-down available when "
                f"the quality service is DB-backed."
            ).classes("text-gray-500 italic")
            return

        # Display preview table (result is a polars DataFrame)
        if len(result) == 0:
            ui.label("No matching data for this entry").classes("text-gray-500")
            return

        ui.label(f"Quarantine Preview: {entry.dataset} — {len(result)} rows").classes(
            "font-bold mb-2"
        )
        columns: list[dict[str, Any]] = [
            {"name": col, "label": col, "field": col, "sortable": True} for col in result.columns
        ]
        rows = result.to_dicts()
        ui.table(columns=columns, rows=rows).classes("w-full")

    except Exception:
        logger.exception(
            "quarantine_preview_failed",
            extra={
                "dataset": entry.dataset,
                "quarantine_path": entry.quarantine_path,
            },
        )
        ui.label("Preview unavailable — error loading quarantine data.").classes("text-red-600")


__all__ = ["data_management_page"]


@ui.page("/data/management")
@requires_auth
async def data_management_alias_page() -> None:
    """Legacy alias route for Data Hub."""
    render_client_redirect(
        resolve_rooted_path_from_ui("/data", ui_module=ui),
        ui_module=ui,
        message="Redirecting to Data Hub...",
    )
