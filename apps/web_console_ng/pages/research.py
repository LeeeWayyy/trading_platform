"""Consolidated research workspace (Discover / Validate / Promote)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from nicegui import run, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.core.dependencies import get_sync_db_pool, get_sync_redis_client
from apps.web_console_ng.core.request_query import get_request_query_param
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.permissions import Permission, has_permission, is_admin

if TYPE_CHECKING:
    from libs.web_console_services.research_workspace_service import (
        LifecycleRow,
        ResearchWorkspaceService,
    )

logger = logging.getLogger(__name__)

TAB_DISCOVER = "discover"
TAB_VALIDATE = "validate"
TAB_PROMOTE = "promote"
VALID_TABS = {TAB_DISCOVER, TAB_VALIDATE, TAB_PROMOTE}
VALID_VALIDATE_BACKTEST_TABS = {"new", "running", "results"}
LIFECYCLE_FAILED = "FAILED"
LIFECYCLE_LIVE = "LIVE"
LIFECYCLE_SHADOW = "SHADOW"
LIFECYCLE_CANDIDATE = "CANDIDATE"
LIFECYCLE_ARCHIVED = "ARCHIVED"
_research_workspace_service_cache: ResearchWorkspaceService | None = None
_research_workspace_service_registry_dir: Path | None = None
_research_workspace_service_import_error: str | None = None


def _resolve_accessible_tabs(
    *,
    can_view_discover: bool,
    can_view_validate: bool,
    can_view_promote: bool,
) -> list[str]:
    """Return ordered list of accessible research workspace tabs."""
    tabs: list[str] = []
    if can_view_discover:
        tabs.append(TAB_DISCOVER)
    if can_view_validate:
        tabs.append(TAB_VALIDATE)
    if can_view_promote:
        tabs.append(TAB_PROMOTE)
    return tabs


def _resolve_selected_tab(*, requested_tab: str, accessible_tabs: list[str]) -> str:
    """Choose selected tab while failing open to first accessible tab."""
    if not accessible_tabs:
        return TAB_DISCOVER
    return requested_tab if requested_tab in set(accessible_tabs) else accessible_tabs[0]


def _should_load_lifecycle_rows(*, can_view_promote: bool) -> bool:
    """Lifecycle rows are needed only for Promote-capable sessions."""
    return can_view_promote


def _get_requested_research_tab() -> str:
    """Resolve requested tab from query string."""
    try:
        request = ui.context.client.request
    except Exception:
        return TAB_DISCOVER
    if request is None:
        return TAB_DISCOVER
    raw_tab = get_request_query_param(
        request=request,
        key="tab",
        default=TAB_DISCOVER,
    )
    normalized = str(raw_tab or TAB_DISCOVER).strip().lower()
    return normalized if normalized in VALID_TABS else TAB_DISCOVER


def _get_research_workspace_service() -> ResearchWorkspaceService | None:
    """Get process-local workspace adapter service."""
    global _research_workspace_service_cache
    global _research_workspace_service_registry_dir
    global _research_workspace_service_import_error

    if _research_workspace_service_import_error is not None:
        return None
    try:
        from libs.web_console_services.research_workspace_service import (
            ResearchWorkspaceService,
        )
    except ModuleNotFoundError as exc:
        _research_workspace_service_import_error = exc.name or "unknown"
        logger.warning(
            "research_workspace_service_import_failed",
            extra={"missing_dependency": _research_workspace_service_import_error},
        )
        return None

    registry_dir = Path(config.MODEL_REGISTRY_DIR)
    if (
        _research_workspace_service_cache is None
        or _research_workspace_service_registry_dir != registry_dir
    ):
        _research_workspace_service_cache = ResearchWorkspaceService(
            registry_dir=registry_dir
        )
        _research_workspace_service_registry_dir = registry_dir

    return _research_workspace_service_cache


def _build_validate_backtest_link(*, signal_id: str, source: str = "alpha_explorer") -> str:
    """Build a research workspace link that pre-fills Validate/New backtest form."""
    return "/research?" + urlencode(
        {
            "tab": TAB_VALIDATE,
            "backtest_tab": "new",
            "signal_id": signal_id,
            "source": source,
        }
    )


def _get_requested_validate_backtest_tab() -> str:
    """Resolve selected backtest sub-tab from research query string."""
    try:
        request = ui.context.client.request
    except Exception:
        return "new"
    if request is None:
        return "new"
    raw_tab = get_request_query_param(
        request=request,
        key="backtest_tab",
        default="new",
    )
    normalized = str(raw_tab or "new").strip().lower()
    return normalized if normalized in VALID_VALIDATE_BACKTEST_TABS else "new"


def _lifecycle_pill_classes(label: str) -> str:
    normalized = label.strip().upper()
    if normalized == LIFECYCLE_LIVE:
        return "workspace-v2-pill workspace-v2-pill-positive"
    if normalized in {LIFECYCLE_FAILED}:
        return "workspace-v2-pill workspace-v2-pill-negative"
    if normalized in {LIFECYCLE_SHADOW, LIFECYCLE_CANDIDATE}:
        return "workspace-v2-pill workspace-v2-pill-warning"
    if normalized == LIFECYCLE_ARCHIVED:
        return "workspace-v2-pill"
    return "workspace-v2-pill workspace-v2-pill-warning"


def _readiness_label(mean_ic: float | None, icir: float | None) -> tuple[str, str]:
    """Simple readiness chip from available signal metrics."""
    if mean_ic is None or icir is None:
        return ("UNKNOWN", "workspace-v2-pill")
    if mean_ic >= 0.02 and icir >= 0.5:
        return ("READY", "workspace-v2-pill workspace-v2-pill-positive")
    return ("REVIEW", "workspace-v2-pill workspace-v2-pill-warning")


def _discover_candidate_rows(rows: list[LifecycleRow]) -> list[LifecycleRow]:
    """Return linked candidate rows surfaced in Discover tab."""
    discover_labels = {LIFECYCLE_CANDIDATE, LIFECYCLE_SHADOW}
    filtered = [row for row in rows if row.linked and row.lifecycle_label in discover_labels]
    return sorted(filtered, key=lambda row: (row.strategy_name, row.version))


def _resolve_promote_action(row: LifecycleRow, *, can_manage: bool) -> str | None:
    """Return promote action allowed by row state and role."""
    if not can_manage or not row.linked:
        return None
    if row.ops_status in {"inactive", "testing"}:
        return "ACTIVATE"
    if row.ops_status == "active":
        return "DEACTIVATE"
    return None


async def _render_validate_tab(user: dict[str, Any]) -> None:
    from apps.web_console_ng.pages import backtest as backtest_page

    prefill = backtest_page.get_backtest_prefill_from_request()
    requested_backtest_tab = _get_requested_validate_backtest_tab()

    with ui.card().classes("w-full p-4 border border-slate-800 bg-slate-900/35"):
        ui.label("Validate").classes("text-lg font-semibold text-slate-100")
        ui.label(
            "Backtest workflows are embedded here (new, running, results, comparison)."
        ).classes("text-xs text-slate-400 mb-3")

        if not config.FEATURE_BACKTEST_MANAGER:
            ui.label("Backtest Manager feature is disabled.").classes("text-slate-300")
            ui.label("Set FEATURE_BACKTEST_MANAGER=true to enable.").classes(
                "text-xs text-slate-500"
            )
            return

        try:
            db_pool = get_sync_db_pool()
            redis_client = get_sync_redis_client()
        except RuntimeError as error:
            ui.label(f"Infrastructure unavailable: {error}").classes("text-red-400")
            return

        with ui.tabs().classes("w-full") as tabs:
            tab_new = ui.tab("New Backtest")
            tab_running = ui.tab("Running Jobs")
            tab_results = ui.tab("Results")
        tab_map = {
            "new": tab_new,
            "running": tab_running,
            "results": tab_results,
        }
        selected_tab = tab_map.get(requested_backtest_tab, tab_new)

        with ui.tab_panels(tabs, value=selected_tab).classes("w-full"):
            with ui.tab_panel(tab_new):
                await backtest_page.render_new_backtest_form(user, prefill=prefill)
            with ui.tab_panel(tab_running):
                await backtest_page.render_running_jobs(
                    user,
                    db_pool,
                    redis_client,
                )
            with ui.tab_panel(tab_results):
                await backtest_page.render_backtest_results(
                    user,
                    db_pool,
                    redis_client,
                )


async def _render_discover_rows(service: ResearchWorkspaceService) -> None:
    rows = await run.io_bound(service.list_research_signals, limit=300)
    if not rows:
        ui.label("No research signals found.").classes("text-slate-400")
        return

    with ui.column().classes("w-full gap-2"):
        for row in rows:
            readiness, readiness_classes = _readiness_label(row.mean_ic, row.icir)
            with ui.card().classes("w-full p-0 border border-slate-800 bg-slate-900/35"):
                with ui.row().classes("w-full items-center justify-between px-3 py-2 border-b border-slate-800"):
                    ui.label(row.display_name).classes("text-sm font-semibold text-slate-100")
                    with ui.row().classes("items-center gap-1"):
                        ui.label(row.research_status.upper()).classes("workspace-v2-pill")
                        ui.label(readiness).classes(readiness_classes)
                with ui.row().classes("w-full gap-3 px-3 py-2 text-xs text-slate-400"):
                    ui.label(f"strategy={row.strategy_name}").classes("workspace-v2-data-mono")
                    ui.label(f"version={row.version}").classes("workspace-v2-data-mono")
                    ui.label(
                        f"signal_id={row.signal_id[:12]}"
                    ).classes("workspace-v2-data-mono")
                    if row.backtest_job_id:
                        ui.label(f"backtest={row.backtest_job_id[:12]}").classes(
                            "workspace-v2-data-mono"
                        )
                with ui.row().classes("w-full px-3 pb-3"):
                    ui.button(
                        "Backtest",
                        on_click=lambda sid=row.signal_id: ui.navigate.to(
                            _build_validate_backtest_link(signal_id=sid)
                        ),
                    ).props("flat")


def _render_discover_candidate_rows(rows: list[LifecycleRow]) -> None:
    """Render candidate model rows below alpha inventory in Discover."""
    candidates = _discover_candidate_rows(rows)
    with ui.card().classes("w-full p-4 border border-slate-800 bg-slate-900/35 mt-3"):
        ui.label("Candidate Models").classes("text-sm font-semibold text-slate-100")
        ui.label("Linked ops/research candidates ready for validation and promotion.").classes(
            "text-xs text-slate-400 mb-2"
        )
        if not candidates:
            ui.label("No linked candidate models found.").classes("text-xs text-slate-500")
            return

        for row in candidates:
            with ui.row().classes("w-full items-center justify-between py-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{row.strategy_name} · {row.version}").classes(
                        "workspace-v2-data-mono text-xs text-slate-200"
                    )
                    ui.label(row.lifecycle_label).classes(
                        _lifecycle_pill_classes(row.lifecycle_label)
                    )
                with ui.row().classes("items-center gap-2 text-[11px] text-slate-500"):
                    ui.label(f"ops={row.ops_status or '—'}").classes("workspace-v2-data-mono")
                    ui.label(f"research={row.research_status or '—'}").classes(
                        "workspace-v2-data-mono"
                    )
                    if row.signal_id:
                        ui.button(
                            "Backtest",
                            on_click=lambda sid=row.signal_id: ui.navigate.to(
                                _build_validate_backtest_link(signal_id=sid)
                            ),
                        ).props("flat dense")


async def _render_promote_rows(
    *,
    rows: list[LifecycleRow],
    model_service: Any,
    user: dict[str, Any],
    can_manage: bool,
) -> None:
    from apps.web_console_ng.pages import models as models_page

    if not rows:
        ui.label("No lifecycle rows available.").classes("text-slate-400")
        return

    with ui.column().classes("w-full gap-2"):
        for row in rows:
            with ui.card().classes("w-full p-0 border border-slate-800 bg-slate-900/35"):
                with ui.row().classes("w-full items-center justify-between px-3 py-2 border-b border-slate-800"):
                    ui.label(f"{row.strategy_name} · {row.version}").classes(
                        "text-sm font-semibold text-slate-100 workspace-v2-data-mono"
                    )
                    ui.label(row.lifecycle_label).classes(
                        _lifecycle_pill_classes(row.lifecycle_label)
                    )
                with ui.row().classes("w-full gap-2 px-3 py-2 text-xs text-slate-400"):
                    ui.label(f"ops={row.ops_status or '—'}").classes("workspace-v2-data-mono")
                    ui.label(f"research={row.research_status or '—'}").classes(
                        "workspace-v2-data-mono"
                    )
                    ui.label(f"link={row.linkage_key}").classes("workspace-v2-data-mono")
                with ui.row().classes("w-full gap-2 px-3 pb-2 text-[11px] text-slate-500"):
                    ui.label(
                        f"backtest_job_id={row.backtest_job_id or '—'}"
                    ).classes("workspace-v2-data-mono")
                    ui.label(f"snapshot_id={row.snapshot_id or '—'}").classes(
                        "workspace-v2-data-mono"
                    )
                    ui.label(
                        f"dataset_version_ids={row.dataset_version_ids or {}}"
                    ).classes("workspace-v2-data-mono")
                    ui.label(f"config_hash={row.config_hash or '—'}").classes(
                        "workspace-v2-data-mono"
                    )

                with ui.row().classes("w-full px-3 pb-3 gap-2"):
                    action = _resolve_promote_action(row, can_manage=can_manage)
                    if action:
                        action_name = action

                        async def on_manage(
                            sn: str = row.strategy_name,
                            ver: str = row.version,
                            action_name: str = action_name,
                        ) -> None:
                            await models_page.show_model_action_dialog(
                                model_service,
                                sn,
                                ver,
                                action_name,
                                user,
                            )

                        ui.button(
                            "Activate" if action == "ACTIVATE" else "Deactivate",
                            on_click=on_manage,
                        ).props("outline")
                    else:
                        ui.label("Non-actionable").classes("text-xs text-slate-500")


@ui.page("/research")
@requires_auth
@main_layout
async def research_workspace_page() -> None:
    """Research workspace with Discover / Validate / Promote tabs."""
    user = get_current_user()

    if not config.FEATURE_RESEARCH_WORKSPACE:
        ui.label("Research Workspace feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_RESEARCH_WORKSPACE=true to enable.").classes("text-gray-500")
        return

    can_view_discover = (
        config.FEATURE_ALPHA_EXPLORER
        and has_permission(user, Permission.VIEW_ALPHA_SIGNALS)
    )
    can_view_validate = has_permission(user, Permission.VIEW_PNL)
    can_view_promote = (
        config.FEATURE_MODEL_REGISTRY
        and has_permission(user, Permission.VIEW_MODELS)
    )
    accessible_tabs = _resolve_accessible_tabs(
        can_view_discover=can_view_discover,
        can_view_validate=can_view_validate,
        can_view_promote=can_view_promote,
    )
    if not accessible_tabs:
        ui.label(
            "Permission denied: one of VIEW_ALPHA_SIGNALS, VIEW_PNL, "
            "or VIEW_MODELS(with FEATURE_MODEL_REGISTRY) required"
        ).classes("text-red-500 text-lg")
        return

    async_pool = get_db_pool()
    model_service: Any | None = None
    workspace_service: ResearchWorkspaceService | None = None
    if can_view_discover or can_view_promote:
        workspace_service = _get_research_workspace_service()
    can_manage = is_admin(user)
    requested_tab = _get_requested_research_tab()
    selected_tab_id = _resolve_selected_tab(
        requested_tab=requested_tab,
        accessible_tabs=accessible_tabs,
    )
    lifecycle_rows: list[LifecycleRow] = []
    if _should_load_lifecycle_rows(can_view_promote=can_view_promote):
        if workspace_service is None:
            ui.notify(
                "Model lifecycle unavailable: research registry dependency missing",
                type="warning",
            )
        elif async_pool is not None:
            from apps.web_console_ng.pages import models as models_page

            model_service = models_page.get_model_registry_service(async_pool)
            try:
                lifecycle_rows = await workspace_service.list_lifecycle_rows(
                    user=user,
                    model_service=model_service,
                )
            except Exception:
                logger.exception("research_workspace_lifecycle_rows_failed")
                ui.notify("Failed to load model lifecycle rows", type="warning")
        elif can_view_promote:
            ui.notify("Model registry unavailable: database not configured", type="warning")

    ui.label("Research Workspace").classes("text-2xl font-bold mb-2")
    ui.label(
        "Consolidated Discover / Validate / Promote surface (legacy routes redirect here)."
    ).classes("text-xs text-slate-400 mb-3")

    tab_map: dict[str, Any] = {}
    with ui.tabs().classes("w-full") as tabs:
        if TAB_DISCOVER in accessible_tabs:
            tab_map[TAB_DISCOVER] = ui.tab("Discover")
        if TAB_VALIDATE in accessible_tabs:
            tab_map[TAB_VALIDATE] = ui.tab("Validate")
        if TAB_PROMOTE in accessible_tabs:
            tab_map[TAB_PROMOTE] = ui.tab("Promote")
    selected_tab = tab_map[selected_tab_id]

    with ui.tab_panels(tabs, value=selected_tab).classes("w-full"):
        if TAB_DISCOVER in tab_map:
            with ui.tab_panel(tab_map[TAB_DISCOVER]):
                with ui.card().classes("w-full p-4 border border-slate-800 bg-slate-900/35 mb-3"):
                    ui.label("Discover").classes("text-lg font-semibold text-slate-100")
                    ui.label(
                        "Alpha inventory and candidate model rows with readiness hints."
                    ).classes("text-xs text-slate-400")
                if workspace_service is None:
                    ui.label(
                        "Discover unavailable: research registry dependency missing."
                    ).classes("text-slate-400")
                else:
                    await _render_discover_rows(workspace_service)
                if workspace_service is not None and lifecycle_rows:
                    _render_discover_candidate_rows(lifecycle_rows)

        if TAB_VALIDATE in tab_map:
            with ui.tab_panel(tab_map[TAB_VALIDATE]):
                await _render_validate_tab(user)

        if TAB_PROMOTE in tab_map:
            with ui.tab_panel(tab_map[TAB_PROMOTE]):
                if workspace_service is None:
                    ui.label("Research registry unavailable. Contact administrator.").classes(
                        "text-slate-400"
                    )
                elif model_service is None:
                    ui.label("Model registry unavailable. Contact administrator.").classes(
                        "text-slate-400"
                    )
                else:
                    await _render_promote_rows(
                        rows=lifecycle_rows,
                        model_service=model_service,
                        user=user,
                        can_manage=can_manage,
                    )
