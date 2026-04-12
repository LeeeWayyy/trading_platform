"""Universe Management page for NiceGUI web console (P6T15/T15.1)."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nicegui import ui

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.universe_analytics import (
    render_universe_analytics,
    render_universe_comparison,
)
from apps.web_console_ng.components.universe_builder import (
    render_universe_builder,
    render_universe_detail,
    render_universe_list,
)
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.utils.session import get_or_create_client_id
from libs.data.data_providers.universe import CRSPUnavailableError
from libs.data.universe_manager import ConflictError, UniverseManager
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.web_console_services.schemas.universe import (
    CustomUniverseDefinitionDTO,
    UniverseFilterDTO,
)
from libs.web_console_services.universe_service import UniverseService, safe_user_id

logger = logging.getLogger(__name__)


# Paths — overridable via env vars for non-default deployments
_UNIVERSES_DIR = Path(os.environ.get("UNIVERSES_DIR", "data/universes"))
_UNIVERSES_DIR_FALLBACK = Path(os.environ.get("UNIVERSES_DIR_FALLBACK", "/tmp/universes"))
_CRSP_DAILY_PATH = Path(os.environ.get("CRSP_DAILY_PATH", "data/wrds/crsp/daily"))
_DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data"))

# Module-level singleton to preserve enrichment cache across requests
_SERVICE: UniverseService | None = None
_SERVICE_LOCK = threading.Lock()
_PROVIDER_RETRY_AFTER: float = 0.0  # monotonic timestamp; skip re-init until then
_PROVIDER_RETRY_INTERVAL: float = 60.0  # seconds between provider re-init attempts


def _ensure_writable_directory(path: Path) -> bool:
    """Return True when path exists and is writable by current process."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".universe_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_universe_dir() -> Path:
    """Resolve writable universes directory with fallback for containerized envs."""
    if _ensure_writable_directory(_UNIVERSES_DIR):
        return _UNIVERSES_DIR

    if _ensure_writable_directory(_UNIVERSES_DIR_FALLBACK):
        logger.warning(
            "universe_dir_fallback_in_use",
            extra={
                "preferred_dir": str(_UNIVERSES_DIR),
                "fallback_dir": str(_UNIVERSES_DIR_FALLBACK),
            },
        )
        return _UNIVERSES_DIR_FALLBACK

    raise PermissionError(
        f"No writable universes directory available: {_UNIVERSES_DIR} or {_UNIVERSES_DIR_FALLBACK}"
    )


def _init_providers() -> tuple[Any | None, Any | None]:
    """Attempt to initialise universe and CRSP providers.

    Returns (universe_provider, crsp_provider) — either may be None.
    """
    universe_provider = None
    crsp_provider = None

    try:
        from libs.data.data_providers.universe import UniverseProvider

        universe_provider = UniverseProvider()
    except (CRSPUnavailableError, ImportError, FileNotFoundError, OSError) as e:
        logger.info(
            "universe_provider_unavailable",
            extra={"error": type(e).__name__},
        )

    try:
        from libs.data.data_providers.crsp_local_provider import CRSPLocalProvider
        from libs.data.data_quality.manifest import ManifestManager

        if _CRSP_DAILY_PATH.exists():
            manifest_mgr = ManifestManager(data_root=_DATA_ROOT)
            crsp_provider = CRSPLocalProvider(
                storage_path=_CRSP_DAILY_PATH,
                manifest_manager=manifest_mgr,
            )
    except (ImportError, FileNotFoundError, OSError) as e:
        logger.info(
            "crsp_provider_unavailable",
            extra={"error": type(e).__name__},
        )

    return universe_provider, crsp_provider


def _get_service() -> UniverseService:
    """Get or create the module-level UniverseService singleton.

    Preserves the UniverseManager enrichment cache across page loads.
    If providers were unavailable at first init, retries on subsequent calls.
    Thread-safe via ``_SERVICE_LOCK``.

    NOTE: This uses a synchronous lock intentionally.  On the first call,
    the lock is held while providers are constructed (lightweight — no network
    I/O).  On subsequent calls with missing providers the lock is briefly held
    for retry hydration (also lightweight, throttled by ``_PROVIDER_RETRY_AFTER``).
    Once both providers are available, calls return the cached singleton with
    only an uncontended lock acquire.  NiceGUI runs a single event loop per
    worker, so the brief startup/retry cost is acceptable.
    """
    global _SERVICE  # noqa: PLW0603

    global _PROVIDER_RETRY_AFTER  # noqa: PLW0603

    with _SERVICE_LOCK:
        if _SERVICE is not None:
            mgr = _SERVICE.manager
            # Retry provider hydration if either was unavailable at init
            # (throttled to avoid log spam when CRSP is persistently down)
            if mgr.universe_provider is None or mgr.crsp_provider is None:
                if time.monotonic() >= _PROVIDER_RETRY_AFTER:
                    universe_provider, crsp_provider = _init_providers()
                    if universe_provider is not None and mgr.universe_provider is None:
                        mgr.universe_provider = universe_provider
                    if crsp_provider is not None and mgr.crsp_provider is None:
                        mgr.crsp_provider = crsp_provider
                    # Schedule next retry if still missing
                    if mgr.universe_provider is None or mgr.crsp_provider is None:
                        _PROVIDER_RETRY_AFTER = (
                            time.monotonic() + _PROVIDER_RETRY_INTERVAL
                        )
            return _SERVICE

        universe_provider, crsp_provider = _init_providers()
        manager = UniverseManager(
            universes_dir=_resolve_universe_dir(),
            universe_provider=universe_provider,
            crsp_provider=crsp_provider,
        )
        _SERVICE = UniverseService(manager)
        return _SERVICE


@ui.page("/research/universes")
@requires_auth
@main_layout
async def universes_page() -> None:
    """Render universe management dashboard."""
    user = get_current_user()

    if not has_permission(user, Permission.VIEW_UNIVERSES):
        ui.notify("Permission denied: VIEW_UNIVERSES required", type="negative")
        with ui.card().classes("w-full p-6"):
            ui.label("Permission denied: VIEW_UNIVERSES required.").classes(
                "text-red-500 text-center"
            )
        return

    can_manage = has_permission(user, Permission.MANAGE_UNIVERSES)
    can_query_crsp = has_dataset_permission(user, "crsp")
    try:
        service = await asyncio.to_thread(_get_service)
    except Exception:
        logger.exception("universe_service_init_failed")
        with ui.card().classes("w-full p-6"):
            ui.label("Universe service is temporarily unavailable.").classes(
                "text-red-500 text-center"
            )
        return

    ui.label("Universe Manager").classes("text-2xl font-bold mb-2")

    # State containers
    list_container = ui.column().classes("w-full")
    detail_container = ui.column().classes("w-full")
    analytics_container = ui.column().classes("w-full")
    builder_container = ui.column().classes("w-full")
    comparison_container = ui.column().classes("w-full")

    _selected_universe: list[str | None] = [None]
    _builder_cleanup: list[Any] = [None]  # Callable | None
    _on_list_refreshed: list[Any] = [None]  # Callable | None (set later)
    _refresh_lock = asyncio.Lock()
    _refresh_requested = asyncio.Event()
    _cached_as_of: list[tuple[float, date] | None] = [None]
    _AS_OF_TTL: float = 300.0  # 5 minutes

    def _as_of() -> date:
        """Current NY-local date adjusted to the most recent trading day.

        Uses ``America/New_York`` timezone so late-evening UTC doesn't
        jump to a future session.  Uses ``MarketHours.is_trading_day()``
        (exchange_calendars) when available to skip both weekends and US
        market holidays. Falls back to weekend-only adjustment if the
        calendar library is unavailable.
        Looks back up to 10 days to find a valid trading session.
        Finally clamps to the latest CRSP manifest ``end_date`` so pages
        don't query dates that haven't been ingested yet.

        Result is cached for 5 minutes to avoid redundant manifest I/O.
        """
        if _cached_as_of[0] is not None:
            ts, cached_date = _cached_as_of[0]
            if time.monotonic() - ts < _AS_OF_TTL:
                return cached_date

        today = datetime.now(ZoneInfo("America/New_York")).date()
        result = today
        try:
            from libs.common.market_hours import MarketHours

            candidate = today
            for _ in range(10):
                if MarketHours.is_trading_day("NYSE", candidate):
                    result = candidate
                    break
                candidate -= timedelta(days=1)
            else:
                # Exhausted search — fall through to weekend heuristic
                result = today
        except Exception:
            logger.debug("market_calendar_unavailable_for_as_of", exc_info=True)

        # Weekend-only fallback (only if MarketHours didn't resolve)
        if result == today:
            weekday = today.weekday()
            if weekday == 5:  # Saturday
                result = today - timedelta(days=1)
            elif weekday == 6:  # Sunday
                result = today - timedelta(days=2)

        # Clamp to latest available CRSP date to avoid querying un-ingested data
        try:
            from libs.data.data_quality.manifest import ManifestManager

            manifest_mgr = ManifestManager(data_root=_DATA_ROOT)
            crsp_manifest = manifest_mgr.load_manifest("crsp_daily")
            if crsp_manifest is not None and crsp_manifest.end_date < result:
                result = crsp_manifest.end_date
        except Exception:
            logger.debug("manifest_clamp_unavailable", exc_info=True)

        _cached_as_of[0] = (time.monotonic(), result)
        return result

    async def refresh_list() -> None:
        """Refresh the universe list.

        Uses ``asyncio.Lock`` with an ``asyncio.Event`` so that a
        refresh requested while one is already running is guaranteed
        to be replayed once the lock is released.  Unlike a boolean
        pending flag, an ``Event`` cannot be lost between the final
        ``is_set()`` check and the lock release because asyncio is
        single-threaded and no ``await`` separates the two.
        """
        _refresh_requested.set()
        if _refresh_lock.locked():
            return
        async with _refresh_lock:
            while _refresh_requested.is_set():
                _refresh_requested.clear()
                list_container.clear()
                # Rehydrate user context each refresh to detect mid-session
                # permission revocations.
                current_user = get_current_user()
                if not has_permission(current_user, Permission.VIEW_UNIVERSES):
                    with list_container:
                        ui.label("Permission revoked: VIEW_UNIVERSES required.").classes(
                            "text-red-500 text-center"
                        )
                    # Clear all data and hide controls on revocation
                    detail_container.clear()
                    analytics_container.clear()
                    comparison_container.clear()
                    _teardown_builder()
                    manage_bar.set_visibility(False)
                    compare_expansion.set_visibility(False)
                    continue
                # Re-evaluate control visibility each refresh so mid-session
                # grants/revocations are reflected without page reload.
                manage_bar.set_visibility(
                    has_permission(current_user, Permission.MANAGE_UNIVERSES)
                )
                compare_expansion.set_visibility(
                    has_dataset_permission(current_user, "crsp")
                )
                try:
                    universes = await service.get_universe_list(
                        current_user, as_of_date=_as_of()
                    )
                except PermissionError as exc:
                    ui.notify(str(exc), type="negative")
                    continue
                except Exception:
                    logger.exception(
                        "universe_list_failed",
                        extra={"user_id": safe_user_id(current_user)},
                    )
                    ui.notify("Failed to load universes", type="warning")
                    continue

                with list_container:
                    if not universes:
                        ui.label("No universes available").classes("text-gray-500")
                    else:
                        render_universe_list(
                            universes,
                            on_select=_on_select,
                        )

                # Update comparison selectors if callback registered
                if _on_list_refreshed[0] is not None:
                    try:
                        await _on_list_refreshed[0](universes)
                    except Exception:
                        logger.warning(
                            "on_list_refreshed_callback_failed", exc_info=True,
                        )

    def _teardown_builder() -> None:
        """Cancel pending preview tasks before clearing builder container."""
        if _builder_cleanup[0] is not None:
            _builder_cleanup[0]()
            _builder_cleanup[0] = None
        builder_container.clear()

    async def _on_select(universe_id: str) -> None:
        """Handle universe selection — show detail."""
        _selected_universe[0] = universe_id
        detail_container.clear()
        analytics_container.clear()
        comparison_container.clear()
        _teardown_builder()

        with detail_container:
            ui.label("Loading...").classes("text-gray-500")

        # Rehydrate user context for mid-session RBAC enforcement.
        current_user = get_current_user()
        try:
            detail = await service.get_universe_detail(current_user, universe_id, _as_of())
        except Exception:
            # Guard: another selection may have occurred during await
            if _selected_universe[0] != universe_id:
                return
            detail_container.clear()
            with detail_container:
                ui.label("Failed to load universe detail").classes("text-red-500")
            logger.exception(
                "universe_detail_failed",
                extra={
                    "universe_id": universe_id,
                    "user_id": safe_user_id(current_user),
                    "as_of": _as_of().isoformat(),
                },
            )
            return

        # Guard against stale response from earlier selection
        if _selected_universe[0] != universe_id:
            return
        detail_container.clear()
        with detail_container:
            with ui.row().classes("w-full items-center gap-2 mb-2"):
                ui.label(detail.name).classes("text-xl font-semibold")
                if has_permission(current_user, Permission.MANAGE_UNIVERSES) and detail.universe_type == "custom":
                    async def _delete_current() -> None:
                        await _on_delete(universe_id)

                    ui.button(
                        icon="delete",
                        on_click=_delete_current,
                    ).props("flat dense round color=negative").tooltip(
                        "Delete Universe"
                    )
            render_universe_detail(detail)

            # Analytics button (P6T15/T15.2) — re-check dataset permission
            if has_dataset_permission(current_user, "crsp"):
                async def _show_analytics() -> None:
                    # Rehydrate user context for mid-session RBAC enforcement.
                    current_user = get_current_user()
                    if not has_permission(current_user, Permission.VIEW_UNIVERSES):
                        ui.notify("Permission revoked: VIEW_UNIVERSES required", type="negative")
                        return
                    analytics_container.clear()
                    with analytics_container:
                        ui.label("Loading analytics...").classes("text-gray-500")
                    try:
                        analytics = await service.get_universe_analytics(
                            current_user, universe_id, _as_of()
                        )
                    except PermissionError as exc:
                        analytics_container.clear()
                        with analytics_container:
                            ui.label(str(exc)).classes("text-red-500")
                        return
                    except Exception:
                        analytics_container.clear()
                        with analytics_container:
                            ui.label("Analytics unavailable").classes("text-red-500")
                        logger.exception(
                            "universe_analytics_failed",
                            extra={
                                "user_id": safe_user_id(current_user),
                                "universe_id": universe_id,
                                "as_of": _as_of().isoformat(),
                            },
                        )
                        return
                    # Guard stale selection
                    if _selected_universe[0] != universe_id:
                        return
                    analytics_container.clear()
                    with analytics_container:
                        if analytics.error_message:
                            ui.label(analytics.error_message).classes("text-red-500")
                        else:
                            render_universe_analytics(analytics)

                ui.button(
                    "Show Analytics",
                    on_click=_show_analytics,
                ).props("flat color=primary").classes("mt-2")

    async def _on_delete(universe_id: str) -> None:
        """Handle universe deletion."""
        # Rehydrate user context to enforce mid-session RBAC revocation.
        current_user = get_current_user()
        if not has_permission(current_user, Permission.MANAGE_UNIVERSES):
            ui.notify("Permission revoked: MANAGE_UNIVERSES required", type="negative")
            return
        try:
            await service.delete_custom_universe(current_user, universe_id)
            ui.notify(f"Universe '{universe_id}' deleted", type="positive")
            _selected_universe[0] = None
            detail_container.clear()
            analytics_container.clear()
            comparison_container.clear()
            await refresh_list()
        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
        except FileNotFoundError:
            ui.notify("Universe not found", type="warning")
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
        except Exception:
            logger.exception(
                "universe_delete_failed",
                extra={
                    "user_id": safe_user_id(current_user),
                    "universe_id": universe_id,
                },
            )
            ui.notify("Delete failed", type="negative")

    async def _show_builder() -> None:
        """Show the universe builder form."""
        _teardown_builder()
        _selected_universe[0] = None
        detail_container.clear()
        analytics_container.clear()
        comparison_container.clear()

        async def _preview(
            base_id: str,
            filters: list[UniverseFilterDTO],
            exclude_symbols: list[str] | None = None,
        ) -> int | None:
            # Rehydrate user context for mid-session RBAC enforcement.
            current_user = get_current_user()
            if not has_permission(current_user, Permission.VIEW_UNIVERSES):
                logger.warning(
                    "universe_preview_denied",
                    extra={"user_id": safe_user_id(current_user)},
                )
                return None
            try:
                return await service.preview_filter(
                    current_user, base_id, filters, _as_of(),
                    exclude_symbols=exclude_symbols,
                )
            except PermissionError:
                logger.warning(
                    "universe_preview_denied",
                    extra={"user_id": safe_user_id(current_user)},
                )
                return None
            except CRSPUnavailableError:
                logger.warning(
                    "universe_preview_crsp_unavailable",
                    extra={"user_id": safe_user_id(current_user), "base_id": base_id},
                )
                return None
            except Exception:
                logger.warning(
                    "universe_preview_failed",
                    extra={
                        "user_id": safe_user_id(current_user),
                        "base_id": base_id,
                        "filter_count": len(filters),
                    },
                    exc_info=True,
                )
                return None

        with builder_container:
            _builder_cleanup[0] = render_universe_builder(
                on_save=_on_save,
                on_cancel=_on_cancel_builder,
                on_preview=_preview,
            )

    async def _on_save(definition: CustomUniverseDefinitionDTO) -> None:
        """Handle universe save."""
        # Rehydrate user context to enforce mid-session RBAC revocation.
        current_user = get_current_user()
        if not has_permission(current_user, Permission.MANAGE_UNIVERSES):
            ui.notify("Permission revoked: MANAGE_UNIVERSES required", type="negative")
            raise PermissionError("Permission revoked")
        try:
            uid = await service.create_custom_universe(current_user, definition)
            ui.notify(f"Universe '{uid}' created", type="positive")
            _teardown_builder()
            await refresh_list()
        except ConflictError as exc:
            ui.notify(str(exc), type="warning")
            raise  # Re-raise so builder re-enables preview
        except PermissionError as exc:
            ui.notify(str(exc), type="negative")
            raise  # Re-raise so builder re-enables preview
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            raise  # Re-raise so builder re-enables preview
        except Exception:
            logger.exception(
                "universe_create_failed",
                extra={"user_id": safe_user_id(current_user)},
            )
            ui.notify("Create failed", type="negative")
            raise  # Re-raise so builder re-enables preview

    async def _on_cancel_builder() -> None:
        """Cancel builder form."""
        _teardown_builder()

    # Top action bar — created unconditionally, visibility driven per-refresh.
    manage_bar = ui.row().classes("w-full justify-end mb-2")
    manage_bar.set_visibility(can_manage)
    with manage_bar:
        ui.button(
            "+ Create New Universe",
            on_click=_show_builder,
        ).props("color=primary")

    # Comparison mode (P6T15/T15.2) — created unconditionally, visibility
    # driven per-refresh so mid-session grants surface without reload.
    compare_expansion = ui.expansion("Compare Universes", icon="compare").classes("w-full mb-2")
    compare_expansion.set_visibility(can_query_crsp)
    compare_a_select: ui.select | None = None
    compare_b_select: ui.select | None = None
    with compare_expansion:
            with ui.row().classes("gap-4 items-end"):
                compare_a_select = ui.select(
                    options={},
                    label="Universe A",
                ).classes("w-48")
                compare_b_select = ui.select(
                    options={},
                    label="Universe B",
                ).classes("w-48")

                async def _compare() -> None:
                    # Rehydrate user context for mid-session RBAC enforcement.
                    current_user = get_current_user()
                    if not has_permission(current_user, Permission.VIEW_UNIVERSES):
                        ui.notify("Permission revoked: VIEW_UNIVERSES required", type="negative")
                        return
                    comparison_container.clear()
                    a_id = compare_a_select.value if compare_a_select else None
                    b_id = compare_b_select.value if compare_b_select else None
                    if not a_id or not b_id:
                        ui.notify("Select two universes to compare", type="warning")
                        return
                    req_a, req_b = str(a_id), str(b_id)
                    with comparison_container:
                        ui.label("Comparing...").classes("text-gray-500")
                    try:
                        result = await service.compare_universes(
                            current_user, req_a, req_b, _as_of()
                        )
                    except PermissionError as exc:
                        comparison_container.clear()
                        with comparison_container:
                            ui.label(str(exc)).classes("text-red-500")
                        return
                    except Exception:
                        comparison_container.clear()
                        with comparison_container:
                            ui.label("Comparison failed").classes("text-red-500")
                        logger.exception(
                            "universe_compare_failed",
                            extra={
                                "user_id": safe_user_id(current_user),
                                "universe_a": req_a,
                                "universe_b": req_b,
                                "as_of": _as_of().isoformat(),
                            },
                        )
                        return
                    # Guard against stale response if user changed selections
                    cur_a = str(compare_a_select.value) if compare_a_select and compare_a_select.value else None
                    cur_b = str(compare_b_select.value) if compare_b_select and compare_b_select.value else None
                    if cur_a != req_a or cur_b != req_b:
                        comparison_container.clear()
                        return
                    comparison_container.clear()
                    with comparison_container:
                        if result.error_message:
                            ui.label(result.error_message).classes("text-red-500")
                        else:
                            render_universe_comparison(result)

                ui.button("Compare", on_click=_compare).props("color=primary")

    # Stash selectors for refresh_list to update
    _compare_selects: list[ui.select | None] = [compare_a_select, compare_b_select]

    async def _update_compare_options(
        universes: list[Any],
    ) -> None:
        """Update comparison selectors with current universe list."""
        options = {u.id: u.name for u in universes}
        for sel in _compare_selects:
            if sel is not None:
                sel.options = options
                if sel.value not in options:
                    sel.value = None
                sel.update()

    _on_list_refreshed[0] = _update_compare_options

    await refresh_list()

    # Register builder cleanup on client disconnect so in-flight preview
    # tasks don't outlive the page/session.
    lifecycle = ClientLifecycleManager.get()
    client_id = get_or_create_client_id()
    if client_id:
        await lifecycle.register_client(client_id)
        await lifecycle.register_cleanup_callback(
            client_id,
            _teardown_builder,
            owner_key="universes_builder",
        )


__all__ = ["universes_page"]
