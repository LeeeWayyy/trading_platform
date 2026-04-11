"""Main trading dashboard with real-time metric cards."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from nicegui import Client, events, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.activity_feed import ActivityFeed
from apps.web_console_ng.components.data_health_widget import render_data_health
from apps.web_console_ng.components.execution_gate import (
    is_model_execution_safe,
    is_strategy_execution_safe,
    normalize_execution_status,
)
from apps.web_console_ng.components.fat_finger_validator import (
    FatFingerThresholds,
    FatFingerValidator,
)
from apps.web_console_ng.components.flatten_controls import FlattenControls
from apps.web_console_ng.components.hierarchical_orders import (
    HierarchicalOrdersState,
    on_cancel_parent_order,
)
from apps.web_console_ng.components.log_tail_panel import LogTailPanel
from apps.web_console_ng.components.metric_card import MetricCard
from apps.web_console_ng.components.one_click_handler import OneClickHandler
from apps.web_console_ng.components.order_audit_panel import show_order_audit_dialog
from apps.web_console_ng.components.order_entry_context import OrderEntryContext
from apps.web_console_ng.components.order_flow_panel import OrderFlowPanel
from apps.web_console_ng.components.order_modify_dialog import OrderModifyDialog
from apps.web_console_ng.components.orders_table import (
    create_hierarchical_orders_table,
    on_cancel_order,
    update_hierarchical_orders_table,
)
from apps.web_console_ng.components.positions_grid import (
    create_positions_grid,
    on_close_position,
    update_positions_grid,
)
from apps.web_console_ng.components.safety_gate import SafetyGate
from apps.web_console_ng.components.sparkline_renderer import create_sparkline_svg
from apps.web_console_ng.components.strategy_context import StrategyContextWidget
from apps.web_console_ng.components.tabbed_panel import (
    TAB_FILLS,
    TAB_HISTORY,
    TAB_POSITIONS,
    TAB_WORKING,
    TabbedPanel,
    TabbedPanelState,
    create_fills_grid,
    create_history_grid,
    create_tabbed_panel,
    filter_items_by_symbol,
    filter_working_orders,
)
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.connection_monitor import ConnectionMonitor
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.core.realtime import (
    RealtimeUpdater,
    circuit_breaker_channel,
    fills_channel,
    kill_switch_channel,
    orders_channel,
    position_channel,
)
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.core.sparkline_service import SparklineDataService
from apps.web_console_ng.core.state_manager import UserStateManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.trading_layout import compact_card, trading_grid
from apps.web_console_ng.utils.time import validate_and_normalize_symbol
from libs.core.common.db import acquire_connection
from libs.data.data_pipeline.health_monitor import get_health_monitor
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)
from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess

logger = logging.getLogger(__name__)

# Maximum fills to keep in memory to prevent unbounded growth
MAX_FILLS_ITEMS = 100
# Workspace live-data staleness threshold before interaction lock
WORKSPACE_DATA_STALE_THRESHOLD_S = 30.0

ScopeKey = tuple[str, frozenset[str]]


class _MetricStripValue:
    """Compact metric value used in workspace command strip."""

    def __init__(
        self,
        title: str,
        *,
        format_fn: Callable[[Any], str],
        color_fn: Callable[[Any], str] | None = None,
        enter_delay_ms: int = 0,
    ) -> None:
        self._format_fn = format_fn
        self._color_fn = color_fn
        self._last_update: float | None = None
        self._value_label: ui.label | None = None
        self._current_color: str | None = None

        metric = ui.element("div").classes("workspace-v2-command-metric workspace-v2-enter-item")
        if enter_delay_ms > 0:
            metric.style(f"animation-delay: {enter_delay_ms}ms")
        with metric:
            ui.label(title).classes("workspace-v2-command-label")
            self._value_label = ui.label("--").classes("workspace-v2-command-value")

    def update(self, value: Any) -> None:
        if self._value_label is None:
            return
        self._value_label.text = self._format_fn(value)
        if self._color_fn is not None:
            new_color = self._color_fn(value)
            if self._current_color:
                self._value_label.classes(remove=self._current_color)
            if new_color:
                self._value_label.classes(add=new_color)
            self._current_color = new_color
        self._value_label.classes(remove="opacity-55")
        self._last_update = time.time()

    def is_stale(self, threshold: float = 30.0) -> bool:
        if self._last_update is None:
            return False
        return (time.time() - self._last_update) > threshold

    def mark_stale(self) -> None:
        if self._value_label is not None:
            self._value_label.classes(add="opacity-55")


class _CommandStatusPill:
    """Compact status pill used in workspace command strip."""

    _tone_classes = {
        "muted": "workspace-v2-command-pill-muted",
        "normal": "workspace-v2-command-pill-normal",
        "warning": "workspace-v2-command-pill-warning",
        "danger": "workspace-v2-command-pill-danger",
    }

    def __init__(self, text: str = "--", *, enter_delay_ms: int = 0) -> None:
        self._pill: Any | None = None
        self._label: ui.label | None = None
        self._tone_class = self._tone_classes["muted"]
        self._pill = ui.element("div").classes(
            f"workspace-v2-command-pill workspace-v2-enter-item {self._tone_class}"
        )
        if enter_delay_ms > 0:
            self._pill.style(f"animation-delay: {enter_delay_ms}ms")
        with self._pill:
            self._label = ui.label(text).classes("workspace-v2-command-pill-text")

    def set_state(self, text: str, tone: str) -> None:
        if self._label is None or self._pill is None:
            return
        new_tone = self._tone_classes.get(tone, self._tone_classes["muted"])
        self._label.text = text
        if new_tone != self._tone_class:
            self._pill.classes(remove=self._tone_class)
            self._pill.classes(add=new_tone)
            self._tone_class = new_tone


def dispatch_trading_state_event(client_id: str | None, update: dict[str, Any]) -> None:
    """Dispatch trading state changes to the browser (fire-and-forget)."""
    try:
        payload = json.dumps(update)
        ui.run_javascript(
            f"window.dispatchEvent(new CustomEvent('trading_state_change', {{ detail: {payload} }}));"
        )
    except Exception as exc:
        logger.warning(
            "trading_state_dispatch_failed",
            extra={"client_id": client_id, "error": type(exc).__name__},
        )


def should_apply_strategy_context_result(
    *,
    refresh_generation: int,
    active_generation: int,
    expected_symbol: str | None,
    current_symbol: str | None,
) -> bool:
    """Return True only when async strategy-context result still matches live selection."""
    return refresh_generation == active_generation and expected_symbol == current_symbol


def plan_strategy_context_refresh_request(
    *,
    current_generation: int,
    task_running: bool,
    dashboard_closing: bool,
    invalidate_running: bool,
) -> tuple[int, bool, int | None]:
    """Plan refresh scheduling outcome.

    Returns: (next_generation, mark_pending, start_generation)
    """
    if dashboard_closing:
        return current_generation, False, None

    if task_running:
        next_generation = current_generation + (1 if invalidate_running else 0)
        return next_generation, True, None

    next_generation = current_generation + 1
    return next_generation, False, next_generation


def should_run_pending_strategy_context_refresh(
    *,
    refresh_pending: bool,
    dashboard_closing: bool,
) -> bool:
    """Return True if queued refresh should run after current task exits."""
    return refresh_pending and not dashboard_closing


def compute_workspace_data_staleness(
    *,
    last_live_data_at: float | None,
    now: float,
    threshold_s: float = WORKSPACE_DATA_STALE_THRESHOLD_S,
) -> tuple[bool, float]:
    """Return workspace data staleness state as (is_stale, age_seconds)."""
    if last_live_data_at is None:
        return (False, 0.0)
    age = max(0.0, now - last_live_data_at)
    return (age > threshold_s, age)


def determine_workspace_lock_state(
    *,
    connection_read_only: bool,
    connection_state: str,
    data_stale: bool,
    data_age_s: float,
) -> tuple[bool, str, str]:
    """Return workspace lock tuple: (locked, title, detail)."""
    if connection_read_only:
        return (
            True,
            f"Connection {connection_state}",
            (
                "Trading actions are locked while the workspace is read-only. "
                "Waiting for reconnection."
            ),
        )
    if data_stale:
        return (
            True,
            "Live data stale",
            (
                f"No live updates for {data_age_s:.0f}s. Trading actions are locked "
                "until stream freshness is restored."
            ),
        )
    return (False, "", "")


def resolve_workspace_connection_pill(
    *,
    state: str | None,
    is_read_only: bool,
) -> tuple[str, str]:
    """Return connection pill text/tone for workspace command strip."""
    normalized = str(state or "UNKNOWN").upper()
    if is_read_only or normalized in {"DISCONNECTED", "RECONNECTING", "DEGRADED"}:
        return (f"CONN {normalized}", "warning")
    if normalized == "CONNECTED":
        return ("CONN LIVE", "normal")
    return (f"CONN {normalized}", "muted")


def resolve_workspace_kill_switch_pill(state: str | None) -> tuple[str, str]:
    """Return kill-switch pill text/tone for workspace command strip."""
    normalized = str(state or "UNKNOWN").upper()
    if normalized == "ENGAGED":
        return ("KILL ENGAGED", "danger")
    if normalized == "DISENGAGED":
        return ("KILL DISARMED", "muted")
    return (f"KILL {normalized}", "warning")


def resolve_workspace_circuit_breaker_pill(state: str | None) -> tuple[str, str]:
    """Return circuit-breaker pill text/tone for workspace command strip."""
    normalized = str(state or "UNKNOWN").upper()
    if normalized == "TRIPPED":
        return ("CB TRIPPED", "danger")
    if normalized == "OPEN":
        return ("CB READY", "normal")
    if normalized == "QUIET_PERIOD":
        return ("CB QUIET", "warning")
    return (f"CB {normalized}", "muted")


def resolve_workspace_quick_links(
    *,
    user_role: str,
    feature_alerts_enabled: bool,
    can_view_alerts: bool,
    can_view_data_quality: bool,
    feature_strategy_management_enabled: bool,
    can_manage_strategies: bool,
    feature_model_registry_enabled: bool,
    can_view_models: bool,
) -> list[tuple[str, str]]:
    """Return workspace quick-link routes visible for the current user context."""
    links = [
        ("Manual", "/manual-order"),
        ("Positions", "/position-management"),
        ("Circuit", "/circuit-breaker"),
        ("Alerts", "/alerts"),
        ("Journal", "/journal"),
        ("Strategies", "/strategies"),
        ("Models", "/models"),
        ("Compare", "/compare"),
        ("Inspector", "/data/inspector"),
    ]
    visible_links: list[tuple[str, str]] = []
    for label, path in links:
        if path == "/position-management" and user_role == "viewer":
            continue
        if path == "/alerts" and (not feature_alerts_enabled or not can_view_alerts):
            continue
        if path == "/strategies" and (
            not feature_strategy_management_enabled or not can_manage_strategies
        ):
            continue
        if path == "/models" and (not feature_model_registry_enabled or not can_view_models):
            continue
        if path == "/data/inspector" and not can_view_data_quality:
            continue
        visible_links.append((label, path))
    return visible_links


def resolve_strategy_context_banner(
    *,
    strategy_status: str | None,
    model_status: str | None,
    gate_reason: str | None,
) -> str:
    """Return strategy/model context banner text using shared gate semantics."""
    strategy_safe = is_strategy_execution_safe(strategy_status)
    model_safe = is_model_execution_safe(model_status)
    if strategy_safe and model_safe:
        return "Execution context healthy."
    if gate_reason:
        return f"Execution context degraded: {gate_reason}"
    return "Execution context degraded: strategy/model state unresolved."


class MarketPriceCache:
    """Per-scope cache for market prices.

    The cache is keyed by (role, strategies) to prevent authorization leaks.
    Users with different strategy permissions or roles see only their authorized symbols.
    """

    # Cache entry: {scope_key: {"prices": [...], "last_fetch": float, "last_error": float}}
    _cache: dict[ScopeKey, dict[str, Any]] = {}
    _in_flight: dict[ScopeKey, asyncio.Task[list[dict[str, Any]]]] = {}
    _ttl: float = 4.0
    _error_cooldown: float = 10.0
    _lock = asyncio.Lock()

    @classmethod
    def _get_scope_key(cls, role: str | None, strategies: list[str] | None) -> ScopeKey:
        """Generate cache key from user's role + strategy scope."""
        role_key = role or "unknown"
        if not strategies:
            return role_key, frozenset()
        return role_key, frozenset(strategies)

    @classmethod
    def _scope_meta(cls, scope_key: ScopeKey) -> dict[str, Any]:
        role, strategies = scope_key
        return {"role": role, "strategies": sorted(strategies)}

    @classmethod
    async def get_prices(
        cls,
        client: AsyncTradingClient,
        *,
        user_id: str,
        role: str | None,
        strategies: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Get market prices for callers that only need the payload."""
        prices, _ = await cls.get_prices_with_freshness(
            client,
            user_id=user_id,
            role=role,
            strategies=strategies,
        )
        return prices

    @classmethod
    async def get_prices_with_freshness(
        cls,
        client: AsyncTradingClient,
        *,
        user_id: str,
        role: str | None,
        strategies: list[str] | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Get market prices, using per-scope cache if fresh.

        Returns a copy to prevent cross-session mutation.
        Implements failure backoff to avoid thundering herd on outages.
        Cache is scoped by user's strategy permissions to prevent authorization leaks.

        Returns:
            tuple[list[dict[str, Any]], bool]:
                prices and whether data should be considered fresh for live-workspace safety checks.
        """
        scope_key = cls._get_scope_key(role, strategies)

        async with cls._lock:
            now = time.time()
            cache_entry = cls._cache.get(scope_key, {})
            cached_prices = cache_entry.get("prices", [])
            last_fetch = cache_entry.get("last_fetch", 0.0)
            last_error = cache_entry.get("last_error", 0.0)

            # Return cached data during error cooldown
            if last_error and (now - last_error) < cls._error_cooldown:
                return ([dict(p) for p in cached_prices], False)

            # Return cached data if still fresh
            if (now - last_fetch) < cls._ttl and cached_prices:
                return ([dict(p) for p in cached_prices], True)

            # Check for in-flight request for this scope
            if scope_key in cls._in_flight:
                task = cls._in_flight[scope_key]
            else:
                fetch_prices = getattr(client, "fetch_market_prices", None)
                if not callable(fetch_prices):
                    logger.warning(
                        "market_prices_fetch_missing", extra={"reason": "method_missing"}
                    )
                    return ([dict(p) for p in cached_prices], False)
                task = asyncio.create_task(
                    fetch_prices(
                        user_id,
                        role=role,
                        strategies=strategies,
                    )
                )
                cls._in_flight[scope_key] = task

        try:
            prices = await task
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "market_price_cache_fetch_failed",
                extra={
                    "error": type(exc).__name__,
                    "detail": str(exc)[:100],
                    "scope": cls._scope_meta(scope_key),
                },
            )
            async with cls._lock:
                if scope_key not in cls._cache:
                    cls._cache[scope_key] = {}
                cls._cache[scope_key]["last_error"] = time.time()
                if cls._in_flight.get(scope_key) is task:
                    del cls._in_flight[scope_key]
                return ([dict(p) for p in cls._cache[scope_key].get("prices", [])], False)
        except (ValueError, KeyError) as exc:
            # Catch validation errors (e.g., malformed payload) to reset _in_flight
            # and allow recovery on next call
            logger.warning(
                "market_price_cache_fetch_validation_error",
                extra={
                    "error": type(exc).__name__,
                    "detail": str(exc)[:100],
                    "scope": cls._scope_meta(scope_key),
                },
            )
            async with cls._lock:
                if scope_key not in cls._cache:
                    cls._cache[scope_key] = {}
                cls._cache[scope_key]["last_error"] = time.time()
                if cls._in_flight.get(scope_key) is task:
                    del cls._in_flight[scope_key]
                return ([dict(p) for p in cls._cache[scope_key].get("prices", [])], False)

        async with cls._lock:
            if cls._in_flight.get(scope_key) is task:
                del cls._in_flight[scope_key]
            cls._cache[scope_key] = {
                "prices": list(prices),
                "last_fetch": time.time(),
                "last_error": 0.0,
            }
            return ([dict(p) for p in cls._cache[scope_key]["prices"]], True)


@ui.page("/")
@requires_auth
@main_layout
async def dashboard(client: Client) -> None:
    """Main trading dashboard with real-time updates."""
    trading_client = AsyncTradingClient.get()
    user = get_current_user()
    user_id = str(user.get("user_id") or user.get("username") or "").strip()
    user_role = str(user.get("role") or "viewer")
    strategies = user.get("strategies") or []
    if isinstance(strategies, str):
        user_strategies = [strategies]
    else:
        user_strategies = [str(strategy) for strategy in strategies if strategy]

    if not user_id:
        logger.warning(
            "dashboard_missing_user_id", extra={"client_id": client.storage.get("client_id")}
        )
        ui.notify("Session expired - please log in again", type="negative")
        ui.navigate.to("/login")
        return

    lifecycle = ClientLifecycleManager.get()

    # Get or generate client_id (may not be set yet if WebSocket hasn't connected)
    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id
        logger.debug("dashboard_generated_client_id", extra={"client_id": client_id})

    realtime = RealtimeUpdater(client_id, client)
    timers: list[ui.timer] = []

    # Create dependencies for OrderEntryContext
    redis_store = get_redis_store()
    redis_client = await redis_store.get_master()
    sparkline_service = SparklineDataService(redis_client)
    async_pool = get_db_pool()
    state_manager = UserStateManager(
        user_id=user_id,
        role=user_role,
        strategies=user_strategies,
    )
    connection_monitor = ConnectionMonitor()

    # Create OrderEntryContext for centralized subscription management
    order_context = OrderEntryContext(
        realtime_updater=realtime,
        trading_client=trading_client,
        state_manager=state_manager,
        connection_monitor=connection_monitor,
        redis=redis_client,
        user_id=user_id,
        role=user_role,
        strategies=user_strategies,
    )

    # Create OneClickHandler dependencies and wire it up (P6T7)
    fat_finger_validator = FatFingerValidator(
        default_thresholds=FatFingerThresholds(
            max_notional=Decimal("100000"),  # $100K per order
            max_qty=10000,  # 10K shares
            max_adv_pct=Decimal("5"),  # 5% of ADV
        ),
    )
    safety_gate = SafetyGate(
        client=trading_client,
        user_id=user_id,
        user_role=user_role,
        strategies=user_strategies,
    )
    one_click_handler = OneClickHandler(
        trading_client=trading_client,
        fat_finger_validator=fat_finger_validator,
        safety_gate=safety_gate,
        state_manager=state_manager,
        user_id=user_id,
        user_role=user_role,
        strategies=user_strategies,
    )
    order_context.set_one_click_handler(one_click_handler)

    # Create FlattenControls for position row actions (P6T7)
    flatten_controls = FlattenControls(
        safety_gate=safety_gate,
        trading_client=trading_client,
        fat_finger_validator=fat_finger_validator,
        strategies=user_strategies,
    )
    order_context.set_flatten_controls(flatten_controls)

    def _coerce_float(value: Any, default: float = 0.0) -> float:
        if isinstance(value, (int, float, Decimal)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", "").strip())
            except ValueError:
                return default
        return default

    def _coerce_int(value: Any, default: int = 0) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, (float, Decimal)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value.replace(",", "").strip()))
            except ValueError:
                return default
        return default

    use_workspace_v2 = config.FEATURE_UNIFIED_EXECUTION_WORKSPACE

    order_flow_panel = OrderFlowPanel(max_rows=12)
    strategy_context_widget: StrategyContextWidget | None = None
    tabs_host: ui.column | None = None
    log_tail_host: ui.column | None = None
    workspace_root: Any | None = None
    workspace_overlay: Any | None = None
    workspace_overlay_title: ui.label | None = None
    workspace_overlay_detail: ui.label | None = None
    authorized_strategy_scope = [
        str(strategy).strip()
        for strategy in get_authorized_strategies(user)
        if str(strategy).strip()
    ]
    if not authorized_strategy_scope:
        authorized_strategy_scope = list(user_strategies)
    pnl_card: _MetricStripValue | MetricCard
    positions_card: _MetricStripValue | MetricCard
    realized_card: _MetricStripValue | MetricCard
    bp_card: _MetricStripValue | MetricCard
    connection_status_pill: _CommandStatusPill | None = None
    kill_switch_status_pill: _CommandStatusPill | None = None
    circuit_breaker_status_pill: _CommandStatusPill | None = None
    workspace_quick_links = resolve_workspace_quick_links(
        user_role=user_role,
        feature_alerts_enabled=config.FEATURE_ALERTS,
        can_view_alerts=has_permission(user, Permission.VIEW_ALERTS),
        can_view_data_quality=has_permission(user, Permission.VIEW_DATA_QUALITY),
        feature_strategy_management_enabled=config.FEATURE_STRATEGY_MANAGEMENT,
        can_manage_strategies=has_permission(user, Permission.MANAGE_STRATEGIES),
        feature_model_registry_enabled=config.FEATURE_MODEL_REGISTRY,
        can_view_models=has_permission(user, Permission.VIEW_MODELS),
    )

    # Metrics strip/cards
    if use_workspace_v2:
        with ui.element("section").classes("workspace-v2 w-full mb-3") as workspace_root:
            with ui.element("div").classes("workspace-v2-command-strip"):
                pnl_card = _MetricStripValue(
                    "UNR P&L",
                    format_fn=lambda v: f"${v:,.2f}",
                    color_fn=lambda v: "positive" if v >= 0 else "negative",
                    enter_delay_ms=40,
                )
                positions_card = _MetricStripValue(
                    "POSITIONS",
                    format_fn=lambda v: str(v),
                    enter_delay_ms=80,
                )
                realized_card = _MetricStripValue(
                    "REALIZED",
                    format_fn=lambda v: f"${v:,.2f}",
                    color_fn=lambda v: "positive" if v >= 0 else "negative",
                    enter_delay_ms=120,
                )
                bp_card = _MetricStripValue(
                    "BUYING POWER",
                    format_fn=lambda v: f"${v:,.2f}",
                    enter_delay_ms=160,
                )
                connection_status_pill = _CommandStatusPill("CONN --", enter_delay_ms=200)
                kill_switch_status_pill = _CommandStatusPill("KILL --", enter_delay_ms=240)
                circuit_breaker_status_pill = _CommandStatusPill("CB --", enter_delay_ms=280)

            with ui.element("div").classes("workspace-v2-body"):
                with ui.element("div").classes("workspace-v2-zone-b workspace-v2-enter-zone workspace-v2-enter-zone-b"):
                    with ui.element("div").classes("workspace-v2-chart-pane"):
                        order_context.create_price_chart(width=960, height=420).classes(
                            "w-full h-full"
                        )
                    with ui.element("div").classes("workspace-v2-microstructure"):
                        order_flow_panel.create().classes("h-full overflow-hidden")
                        order_context.create_dom_ladder(levels=5).classes("h-full overflow-hidden")
                        order_context.create_market_context().classes(
                            "h-full overflow-hidden workspace-v2-market-context"
                        )

                with ui.element("div").classes("workspace-v2-zone-c workspace-v2-enter-zone workspace-v2-enter-zone-c"):
                    with ui.element("div").classes("workspace-v2-panel"):
                        ui.label("Watchlist").classes("workspace-v2-panel-title mb-1")
                        order_context.create_watchlist().classes("w-full")

                    if workspace_quick_links:
                        with ui.element("div").classes("workspace-v2-panel"):
                            ui.label("Quick Panels").classes("workspace-v2-panel-title mb-1")
                            with ui.row().classes("w-full gap-1 flex-wrap"):
                                for quick_label, quick_path in workspace_quick_links:
                                    with ui.link(target=quick_path).classes("workspace-v2-quick-link"):
                                        ui.label(quick_label).classes("workspace-v2-kv")

                    strategy_context_widget = StrategyContextWidget(strategies=user_strategies)
                    strategy_context_widget.create()
                    order_context.set_strategy_context_widget(strategy_context_widget)

                    order_context.create_order_ticket()

                    tabs_host = ui.column().classes("workspace-v2-tabs-area")
                    log_tail_host = ui.column().classes("shrink-0")

            with ui.element("div").classes("workspace-v2-overlay hidden") as workspace_overlay:
                with ui.card().classes("workspace-v2-overlay-card"):
                    workspace_overlay_title = ui.label("Connection unavailable").classes(
                        "workspace-v2-overlay-title"
                    )
                    workspace_overlay_detail = ui.label(
                        "Trading actions are locked until the workspace stream recovers."
                    ).classes("workspace-v2-overlay-detail")
    else:
        with trading_grid().classes("w-full mb-2"):
            pnl_card = MetricCard(
                title="Unrealized P&L",
                format_fn=lambda v: f"${v:,.2f}",
                color_fn=lambda v: "text-green-600" if v >= 0 else "text-red-600",
            )
            positions_card = MetricCard(
                title="Positions",
                format_fn=lambda v: str(v),
            )
            realized_card = MetricCard(
                title="Realized (Today)",
                format_fn=lambda v: f"${v:,.2f}",
                color_fn=lambda v: "text-green-600" if v >= 0 else "text-red-600",
            )
            bp_card = MetricCard(
                title="Buying Power",
                format_fn=lambda v: f"${v:,.2f}",
            )

        with ui.element("div").classes(
            "grid gap-4 w-full mb-4 " "grid-cols-1 md:grid-cols-2 lg:grid-cols-[250px_1fr_350px]"
        ):
            with ui.column().classes("hidden md:flex"):
                order_context.create_watchlist()

            with ui.column().classes("min-h-[200px] gap-3"):
                order_context.create_price_chart(width=600, height=300)
                with ui.element("div").classes("h-[340px] min-h-[280px] grid grid-rows-2 gap-2"):
                    order_flow_panel.create().classes("h-full")
                    order_context.create_dom_ladder(levels=5).classes("h-full overflow-hidden")

            with ui.column().classes("gap-4"):
                order_context.create_market_context()
                order_context.create_order_ticket()

    # Data Health Widget (P6T12.4) - in expandable card
    health_container = ui.expansion("Data Health", icon="monitor_heart").classes(
        "w-full mb-4"
    )

    async def _refresh_health() -> None:
        """Refresh data health widget via HealthMonitor.

        Filters sources to the current user's authorized strategies to
        prevent the global singleton from leaking cross-user signal
        health status.
        """
        monitor = get_health_monitor()
        all_sources = await monitor.check_all()
        # Filter: show generic sources (Price/Volume) plus only
        # Signal sources for strategies this user is authorized for
        user_strats = set(get_authorized_strategies(user)) if user else set()
        sources = [
            s for s in all_sources
            if not s.name.startswith("Signal: ")
            or s.name.removeprefix("Signal: ") in user_strats
        ]
        health_container.clear()
        with health_container:
            render_data_health(sources)

    # Register health sources and do initial load
    async def _setup_health_widget() -> None:
        monitor = get_health_monitor()

        # Redis-based health checks — guarded by has_source() to avoid
        # redundant lambda allocation on repeat page loads (the singleton
        # already skips duplicate registrations, but this avoids the
        # closure creation cost).
        async def _check_redis_key(key: str) -> datetime | None:
            if redis_client is None:
                return None
            val = await redis_client.get(key)
            if val is None:
                return None
            try:
                dt = datetime.fromisoformat(
                    val.decode() if isinstance(val, bytes) else str(val)
                )
                # Normalize naive timestamps to UTC to prevent TypeError
                # when HealthMonitor computes age against aware datetime
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except (ValueError, AttributeError):
                return None

        if not monitor.has_source("Price Data"):
            monitor.register_source(
                "Price Data", "price",
                lambda: _check_redis_key("market:last_update:prices"),
            )
        if not monitor.has_source("Volume Data"):
            monitor.register_source(
                "Volume Data", "volume",
                lambda: _check_redis_key("market:last_update:volume"),
            )

        # Signal checks per authorized strategy
        def _make_signal_checker(s: str) -> Callable[[], Coroutine[Any, Any, datetime | None]]:
            return lambda: _check_redis_key(f"signal:last_update:{s}")

        strategies = get_authorized_strategies(user) if user else []
        for strat in strategies:
            source_name = f"Signal: {strat}"
            if not monitor.has_source(source_name):
                monitor.register_source(
                    source_name, "signal",
                    _make_signal_checker(strat),
                )

        # NOTE: Fundamental data source intentionally not registered here.
        # No ETL pipeline currently writes the "market:last_update:fundamentals"
        # heartbeat key, so registering it would always show ERROR status.
        # Register when a fundamental data pipeline is implemented.

        await _refresh_health()

    await _setup_health_widget()

    # Auto-refresh health widget every 10s
    health_timer = ui.timer(10.0, _refresh_health)
    await lifecycle.register_cleanup_callback(client_id, lambda: health_timer.cancel())

    panel_state = TabbedPanelState(user_id=user_id)
    await panel_state.load()

    hierarchical_state = HierarchicalOrdersState(
        user_id=user_id,
        panel_id=f"hierarchical_orders.{user_id}",
    )
    await hierarchical_state.load()

    positions_grid: ui.aggrid | None = None
    orders_table: ui.aggrid | None = None
    fills_grid: ui.aggrid | None = None
    history_grid: ui.aggrid | None = None
    tabbed_panel: TabbedPanel | None = None

    # Declare variables used in filter change handler (must be before function definition)
    position_symbols: set[str] | None = None
    order_ids: set[str] | None = None

    def _build_positions_grid() -> ui.aggrid:
        nonlocal positions_grid
        positions_grid = create_positions_grid()
        return positions_grid

    def _build_orders_grid() -> ui.aggrid:
        nonlocal orders_table
        orders_table = create_hierarchical_orders_table(
            expanded_parent_ids=sorted(hierarchical_state.expanded_parent_ids),
        )
        return orders_table

    def _build_fills_grid() -> ui.aggrid:
        nonlocal fills_grid
        fills_grid = create_fills_grid()
        return fills_grid

    def _build_history_grid() -> ui.aggrid:
        nonlocal history_grid
        history_grid = create_history_grid()
        return history_grid

    def _handle_filter_change(_: str | None) -> None:
        nonlocal position_symbols, order_ids
        position_symbols = None
        order_ids = None
        # Use locked version to prevent interleaving with realtime updates
        asyncio.create_task(_locked_refresh_all_grids())

    def _handle_tab_change(tab_name: str) -> None:
        # Use locked version to prevent interleaving with realtime updates
        asyncio.create_task(_locked_refresh_tab(tab_name))

    last_sync_label: ui.label
    activity_feed: ActivityFeed | LogTailPanel

    if use_workspace_v2 and tabs_host is not None and log_tail_host is not None:
        with tabs_host:
            with ui.element("div").classes("workspace-v2-panel w-full flex-1 min-h-0"):
                tabbed_panel = create_tabbed_panel(
                    _build_positions_grid,
                    _build_orders_grid,
                    _build_fills_grid,
                    _build_history_grid,
                    state=panel_state,
                    on_filter_change=_handle_filter_change,
                    on_tab_change=_handle_tab_change,
                )

        with log_tail_host:
            last_sync_label = ui.label("Last sync: --").classes(
                "workspace-v2-kv workspace-v2-data-mono mb-1"
            )
            activity_feed = LogTailPanel(max_items=180)
            activity_feed.create(title="Tail Logs")
    else:
        with trading_grid().classes("w-full"):
            with compact_card().classes("w-full"):
                tabbed_panel = create_tabbed_panel(
                    _build_positions_grid,
                    _build_orders_grid,
                    _build_fills_grid,
                    _build_history_grid,
                    state=panel_state,
                    on_filter_change=_handle_filter_change,
                    on_tab_change=_handle_tab_change,
                )

            with compact_card("Activity").classes("w-full"):
                last_sync_label = ui.label("Last sync: --").classes("text-xs text-gray-500 mb-2")
                activity_feed = ActivityFeed()

    notified_missing_ids: set[str] = set()
    notified_malformed: set[int] = set()  # Dedupe malformed position notifications
    notified_filter_restores: set[str] = set()  # Dedupe filter restore toasts per grid
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}  # Prevent churn from transient snapshot gaps
    grid_update_lock = asyncio.Lock()
    kill_switch_engaged: bool | None = None  # Real-time cached state for instant UI response
    workspace_kill_switch_state = "UNKNOWN"
    workspace_circuit_breaker_state = "UNKNOWN"
    modify_dialog = OrderModifyDialog(
        trading_client=trading_client,
        user_id=user_id,
        user_role=user_role,
    )

    positions_snapshot: list[dict[str, Any]] = []
    orders_snapshot: list[dict[str, Any]] = []
    fills_snapshot: list[dict[str, Any]] = []
    history_snapshot: list[dict[str, Any]] = []
    workspace_last_live_data_at: float | None = None
    workspace_connection_state = "CONNECTED"
    workspace_connection_read_only = False

    def _update_workspace_connection_pill() -> None:
        if not use_workspace_v2 or connection_status_pill is None:
            return
        text, tone = resolve_workspace_connection_pill(
            state=workspace_connection_state,
            is_read_only=workspace_connection_read_only,
        )
        connection_status_pill.set_state(text, tone)

    def _update_workspace_kill_switch_pill() -> None:
        if not use_workspace_v2 or kill_switch_status_pill is None:
            return
        text, tone = resolve_workspace_kill_switch_pill(workspace_kill_switch_state)
        kill_switch_status_pill.set_state(text, tone)

    def _update_workspace_circuit_breaker_pill() -> None:
        if not use_workspace_v2 or circuit_breaker_status_pill is None:
            return
        text, tone = resolve_workspace_circuit_breaker_pill(workspace_circuit_breaker_state)
        circuit_breaker_status_pill.set_state(text, tone)

    def _set_workspace_mask(*, locked: bool, title: str = "", detail: str = "") -> None:
        """Show/hide workspace interaction mask for safety-critical stale/disconnect states."""
        if not use_workspace_v2 or workspace_root is None or workspace_overlay is None:
            return

        if locked:
            workspace_root.classes(add="workspace-v2-locked")
            workspace_overlay.classes(remove="hidden")
            if workspace_overlay_title is not None:
                workspace_overlay_title.text = title
            if workspace_overlay_detail is not None:
                workspace_overlay_detail.text = detail
        else:
            workspace_root.classes(remove="workspace-v2-locked")
            workspace_overlay.classes(add="hidden")

    def _mark_workspace_live_data() -> None:
        """Record latest successful live update timestamp for stale masking."""
        nonlocal workspace_last_live_data_at
        workspace_last_live_data_at = time.monotonic()

    def _is_workspace_data_stale(now: float | None = None) -> tuple[bool, float]:
        """Return (is_stale, age_seconds)."""
        current = now if now is not None else time.monotonic()
        return compute_workspace_data_staleness(
            last_live_data_at=workspace_last_live_data_at,
            now=current,
            threshold_s=WORKSPACE_DATA_STALE_THRESHOLD_S,
        )

    def _evaluate_workspace_mask() -> None:
        """Lock interactive workspace zones when connection is read-only or live data is stale."""
        if not use_workspace_v2:
            return

        stale, age_s = _is_workspace_data_stale()
        locked, title, detail = determine_workspace_lock_state(
            connection_read_only=workspace_connection_read_only,
            connection_state=workspace_connection_state,
            data_stale=stale,
            data_age_s=age_s,
        )
        _set_workspace_mask(locked=locked, title=title, detail=detail)

    def _on_workspace_connection_state(state: str, is_read_only: bool) -> None:
        """Bridge OrderEntryContext connection updates into dashboard workspace mask."""
        nonlocal workspace_connection_state, workspace_connection_read_only
        workspace_connection_state = str(state or "UNKNOWN").upper()
        workspace_connection_read_only = bool(is_read_only)
        _update_workspace_connection_pill()
        _evaluate_workspace_mask()

    order_context.set_connection_state_callback(_on_workspace_connection_state)
    _update_workspace_connection_pill()
    _update_workspace_kill_switch_pill()
    _update_workspace_circuit_breaker_pill()
    _evaluate_workspace_mask()

    def _current_symbol_filter() -> str | None:
        if tabbed_panel is None:
            return None
        return tabbed_panel.symbol_filter.value

    def _update_filter_options() -> None:
        if tabbed_panel is None:
            return
        symbols = {
            str(item.get("symbol")).upper()
            for item in positions_snapshot + orders_snapshot
            if item.get("symbol")
        }
        tabbed_panel.symbol_filter.update_options(sorted(symbols))

    async def _attach_sparklines(
        positions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not positions:
            return []
        symbols = [str(item.get("symbol", "")).strip() for item in positions if item.get("symbol")]
        sparkline_map = await sparkline_service.get_sparkline_map(user_id, symbols)
        enriched: list[dict[str, Any]] = []
        for position in positions:
            position_copy = dict(position)
            symbol = str(position_copy.get("symbol", "")).strip()
            history = sparkline_map.get(symbol, [])
            position_copy["pnl_history"] = history
            position_copy["sparkline_svg"] = create_sparkline_svg(history)
            enriched.append(position_copy)
        return enriched

    async def _refresh_positions_grid() -> None:
        nonlocal position_symbols
        if positions_grid is None:
            return
        filtered = filter_items_by_symbol(positions_snapshot, _current_symbol_filter())
        filtered = await _attach_sparklines(filtered)
        position_symbols = await update_positions_grid(
            positions_grid,
            filtered,
            position_symbols,
            notified_malformed=notified_malformed,
        )

    async def _refresh_orders_grid() -> None:
        nonlocal order_ids
        if orders_table is None:
            return
        symbol_filter = _current_symbol_filter()
        filtered_orders = filter_working_orders(orders_snapshot, symbol_filter)
        use_maps = symbol_filter is None
        # When a symbol filter is active, also filter all_orders to prevent
        # parent rows from other symbols being injected into the hierarchy
        filtered_all_orders = (
            filter_items_by_symbol(orders_snapshot, symbol_filter)
            if symbol_filter
            else orders_snapshot
        )
        order_ids, parent_ids = await update_hierarchical_orders_table(
            orders_table,
            filtered_orders,
            order_ids,
            notified_missing_ids=notified_missing_ids,
            synthetic_id_map=synthetic_id_map if use_maps else None,
            synthetic_id_miss_counts=synthetic_id_miss_counts if use_maps else None,
            user_id=user_id,
            client_id=client_id,
            all_orders=filtered_all_orders,
        )
        # Only prune expansion state when NO filter is active to avoid losing
        # state for orders that are simply hidden by the current filter
        if _current_symbol_filter() is None and hierarchical_state.prune(parent_ids):
            await hierarchical_state.save()
            ui.run_javascript(
                f"window._hierarchicalOrdersExpanded = {json.dumps(sorted(hierarchical_state.expanded_parent_ids))};"
                "if (window.HierarchicalOrdersGrid && window._hierarchicalOrdersGridApi)"
                " window.HierarchicalOrdersGrid.restoreExpansion(window._hierarchicalOrdersGridApi, window._hierarchicalOrdersExpanded);"
            )

    async def _refresh_fills_grid() -> None:
        if fills_grid is None:
            return
        filtered = filter_items_by_symbol(fills_snapshot, _current_symbol_filter())
        fills_grid.run_grid_method("setGridOption", "rowData", filtered, timeout=5)

    async def _refresh_history_grid() -> None:
        if history_grid is None:
            return
        filtered = filter_items_by_symbol(history_snapshot, _current_symbol_filter())
        history_grid.run_grid_method("setGridOption", "rowData", filtered, timeout=5)

    async def _refresh_tab_content(tab_name: str) -> None:
        if tab_name == TAB_POSITIONS:
            await _refresh_positions_grid()
        elif tab_name == TAB_WORKING:
            await _refresh_orders_grid()
        elif tab_name == TAB_FILLS:
            await _refresh_fills_grid()
        elif tab_name == TAB_HISTORY:
            await _refresh_history_grid()

    async def _locked_refresh_all_grids() -> None:
        """Refresh all grids with lock protection to prevent interleaving."""
        async with grid_update_lock:
            await _refresh_positions_grid()
            await _refresh_orders_grid()
            await _refresh_fills_grid()
            await _refresh_history_grid()

    async def _locked_refresh_tab(tab_name: str) -> None:
        """Refresh single tab with lock protection."""
        async with grid_update_lock:
            await _refresh_tab_content(tab_name)

    def _format_event_time(event: dict[str, Any]) -> dict[str, Any]:
        """Ensure activity events include a HH:MM time field."""
        if "time" in event and event.get("time"):
            return event
        timestamp = event.get("timestamp")
        if not timestamp:
            return event
        try:
            ts_str = str(timestamp)
            if ts_str.endswith("Z"):
                ts_str = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            event["time"] = dt.astimezone(UTC).strftime("%H:%M")
        except (ValueError, TypeError, AttributeError) as e:
            # Invalid timestamp format - skip time formatting
            logger.debug(
                "Failed to parse event timestamp",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "timestamp": event.get("timestamp"),
                },
            )
        return event

    def _update_last_sync_label(events: list[dict[str, Any]]) -> None:
        now = datetime.now(UTC)
        label = now.strftime("%H:%M:%S UTC")
        if events:
            # Use most recent fill timestamp for clarity
            latest = events[0].get("timestamp")
            if latest:
                label = f"{str(latest).replace('Z', '')} UTC"
        last_sync_label.text = f"Last sync: {label}"

    async def load_initial_data() -> None:
        nonlocal position_symbols, order_ids, positions_snapshot, orders_snapshot, fills_snapshot
        try:
            trades_task = None
            if async_pool is not None:
                data_access = StrategyScopedDataAccess(async_pool, None, user)
                trades_task = data_access.get_trades(limit=activity_feed.MAX_ITEMS, offset=0)
            else:
                trades_task = asyncio.sleep(0, result=[])

            pnl_data, positions, orders, account_info, recent_trades = await asyncio.gather(
                trading_client.fetch_realtime_pnl(
                    user_id,
                    role=user_role,
                    strategies=user_strategies,
                ),
                trading_client.fetch_positions(
                    user_id,
                    role=user_role,
                    strategies=user_strategies,
                ),
                trading_client.fetch_open_orders(
                    user_id,
                    role=user_role,
                    strategies=user_strategies,
                ),
                trading_client.fetch_account_info(
                    user_id,
                    role=user_role,
                    strategies=user_strategies,
                ),
                trades_task,
            )
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "load_initial_data_failed",
                extra={"client_id": client_id, "status": exc.response.status_code},
            )
            ui.notify(f"Failed to load dashboard: HTTP {exc.response.status_code}", type="negative")
            return
        except httpx.RequestError as exc:
            logger.exception(
                "load_initial_data_failed",
                extra={"client_id": client_id, "error": type(exc).__name__},
            )
            ui.notify("Failed to load dashboard: network error", type="negative")
            return

        pnl_card.update(_coerce_float(pnl_data.get("total_unrealized_pl", 0)))
        positions_card.update(_coerce_int(pnl_data.get("total_positions", 0)))
        realized_card.update(_coerce_float(pnl_data.get("realized_pl_today", 0)))
        buying_power = account_info.get("buying_power")
        if buying_power is None:
            buying_power = pnl_data.get("buying_power", 0)
        bp_card.update(_coerce_float(buying_power))
        positions_snapshot = list(positions.get("positions", []))
        orders_snapshot = list(orders.get("orders", []))
        await sparkline_service.record_positions(user_id, positions_snapshot)

        fills_snapshot = []
        if isinstance(recent_trades, list):
            for idx, trade in enumerate(recent_trades[:MAX_FILLS_ITEMS]):
                fills_snapshot.append(
                    {
                        "id": trade.get("id") or f"fill-{idx}",
                        "time": trade.get("executed_at"),
                        "symbol": trade.get("symbol"),
                        "side": trade.get("side"),
                        "qty": trade.get("qty"),
                        "price": trade.get("price"),
                        "status": trade.get("status") or "filled",
                    }
                )
            order_flow_panel.add_trades(recent_trades)

        _update_filter_options()
        if tabbed_panel is not None:
            tabbed_panel.set_badge_count(TAB_POSITIONS, len(positions_snapshot))
            tabbed_panel.set_badge_count(TAB_WORKING, len(filter_working_orders(orders_snapshot)))
            tabbed_panel.set_badge_count(TAB_FILLS, len(fills_snapshot))

        async with grid_update_lock:
            await _refresh_positions_grid()
            await _refresh_orders_grid()
            await _refresh_fills_grid()
            await _refresh_history_grid()

        recent_events = []
        if isinstance(recent_trades, list):
            for trade in recent_trades:
                event = {
                    "timestamp": trade.get("executed_at"),
                    "symbol": trade.get("symbol"),
                    "side": trade.get("side"),
                    "qty": trade.get("qty"),
                    "price": trade.get("price"),
                    "status": "filled",
                }
                recent_events.append(event)
        normalized_events = [_format_event_time(dict(event)) for event in recent_events]
        _update_last_sync_label(normalized_events)
        await activity_feed.add_items(normalized_events, highlight=False)
        _mark_workspace_live_data()
        _evaluate_workspace_mask()

    await load_initial_data()

    def _parse_kill_switch_state(state_raw: Any) -> bool | None:
        state = str(state_raw or "").upper()
        if state == "ENGAGED":
            return True
        if state == "DISENGAGED":
            return False
        return None

    async def check_initial_kill_switch() -> None:
        """Fetch initial kill switch status on page load."""
        nonlocal kill_switch_engaged, workspace_kill_switch_state
        try:
            ks_status = await trading_client.fetch_kill_switch_status(
                user_id,
                role=user_role,
                strategies=user_strategies,
            )
            state = str(ks_status.get("state", "")).upper() or "UNKNOWN"
            workspace_kill_switch_state = state
            kill_switch_engaged = _parse_kill_switch_state(state)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "kill_switch_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            # Use None (unknown) on API failure to preserve fail-open path in on_close_position
            # This allows risk-reducing closes during kill switch service outages
            kill_switch_engaged = None
            workspace_kill_switch_state = "UNKNOWN"
        _update_workspace_kill_switch_pill()

    await check_initial_kill_switch()

    async def check_initial_circuit_breaker() -> None:
        """Fetch initial circuit breaker status on page load."""
        nonlocal workspace_circuit_breaker_state
        try:
            cb_status = await trading_client.fetch_circuit_breaker_status(
                user_id,
                role=user_role,
                strategies=user_strategies,
            )
            workspace_circuit_breaker_state = str(cb_status.get("state", "")).upper() or "UNKNOWN"
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "circuit_breaker_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            workspace_circuit_breaker_state = "UNKNOWN"
        _update_workspace_circuit_breaker_pill()

    await check_initial_circuit_breaker()

    async def on_position_update(data: dict[str, Any]) -> None:
        nonlocal position_symbols, positions_snapshot
        _mark_workspace_live_data()
        _evaluate_workspace_mask()
        if "total_unrealized_pl" in data:
            pnl_card.update(_coerce_float(data["total_unrealized_pl"]))
        if "total_positions" in data:
            positions_card.update(_coerce_int(data["total_positions"]))
        if "realized_pl_today" in data:
            realized_card.update(_coerce_float(data["realized_pl_today"]))
        if "buying_power" in data:
            bp_card.update(_coerce_float(data["buying_power"]))
        if "positions" in data:
            positions_snapshot = list(data["positions"])
            await sparkline_service.record_positions(user_id, positions_snapshot)
            _update_filter_options()
            if tabbed_panel is not None:
                tabbed_panel.set_badge_count(TAB_POSITIONS, len(positions_snapshot))
            async with grid_update_lock:
                await _refresh_positions_grid()
        if "event" in data:
            # Normalize event time for consistent display (same as fills channel)
            event = dict(data["event"])
            event = _format_event_time(event)
            if "type" not in event:
                event["type"] = "position_update"  # Enable client-side filtering
            _update_last_sync_label([event])
            await activity_feed.add_item(event)

    def _extract_event_detail(args: Any) -> dict[str, Any]:
        if isinstance(args, dict):
            return args
        if isinstance(args, list) and args and isinstance(args[0], dict):
            return args[0]
        return {}

    async def handle_grid_filters_restored(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        grid_id = str(detail.get("gridId", "")).strip() or "grid"
        if grid_id in notified_filter_restores:
            return
        notified_filter_restores.add(grid_id)
        filter_count_raw: Any = detail.get("filterCount")
        filter_count: int | None
        try:
            filter_count = int(filter_count_raw) if filter_count_raw is not None else None
        except (TypeError, ValueError):
            filter_count = None
        if filter_count is not None and filter_count > 0:
            message = f"Filters active on {grid_id} ({filter_count}). Review before trading."
        else:
            message = f"Filters active on {grid_id}. Review before trading."
        ui.notify(message, type="warning", timeout=12000)
        logger.info(
            "grid_filters_restored_notice",
            extra={"client_id": client_id, "grid_id": grid_id, "filter_count": filter_count},
        )

    async def on_kill_switch_update(data: dict[str, Any]) -> None:
        nonlocal kill_switch_engaged, workspace_kill_switch_state
        logger.info("kill_switch_update", extra={"client_id": client_id, "data": data})
        state = str(data.get("state", "")).upper()
        workspace_kill_switch_state = state or "UNKNOWN"
        # Update cached state for instant UI responses; unknown stays None for fail-open closes
        kill_switch_engaged = _parse_kill_switch_state(state)
        _update_workspace_kill_switch_pill()
        dispatch_trading_state_event(client_id, {"killSwitchState": state})
        await activity_feed.add_item(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "type": "kill_switch",
                "status": state.lower() if state else "unknown",
                "symbol": "--",
                "message": f"Kill switch state: {state or 'UNKNOWN'}",
            }
        )

    async def on_circuit_breaker_update(data: dict[str, Any]) -> None:
        nonlocal workspace_circuit_breaker_state
        logger.info("circuit_breaker_update", extra={"client_id": client_id, "data": data})
        state = str(data.get("state", "")).upper()
        workspace_circuit_breaker_state = state or "UNKNOWN"
        _update_workspace_circuit_breaker_pill()
        dispatch_trading_state_event(client_id, {"circuitBreakerState": state})
        await activity_feed.add_item(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "type": "circuit_breaker",
                "status": state.lower() if state else "unknown",
                "symbol": "--",
                "message": f"Circuit breaker state: {state or 'UNKNOWN'}",
            }
        )

    async def on_orders_update(data: dict[str, Any]) -> None:
        nonlocal order_ids, orders_snapshot
        _mark_workspace_live_data()
        _evaluate_workspace_mask()
        if "orders" in data:
            orders_snapshot = list(data["orders"])
            _update_filter_options()
            if tabbed_panel is not None:
                tabbed_panel.set_badge_count(
                    TAB_WORKING, len(filter_working_orders(orders_snapshot))
                )
            async with grid_update_lock:
                await _refresh_orders_grid()
            await activity_feed.add_item(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "type": "orders",
                    "status": "updated",
                    "symbol": "--",
                    "message": f"Working orders refreshed ({len(orders_snapshot)})",
                }
            )

    async def on_fill_event(data: dict[str, Any]) -> None:
        nonlocal fills_snapshot
        _mark_workspace_live_data()
        _evaluate_workspace_mask()
        normalized = _format_event_time(dict(data))
        fills_snapshot.insert(
            0,
            {
                "id": normalized.get("id") or f"fill-{len(fills_snapshot) + 1}",
                "time": normalized.get("timestamp"),
                "symbol": normalized.get("symbol"),
                "side": normalized.get("side"),
                "qty": normalized.get("qty"),
                "price": normalized.get("price"),
                "status": normalized.get("status") or "filled",
            },
        )
        # Cap fills_snapshot to prevent unbounded memory growth
        if len(fills_snapshot) > MAX_FILLS_ITEMS:
            fills_snapshot = fills_snapshot[:MAX_FILLS_ITEMS]
        if tabbed_panel is not None:
            tabbed_panel.set_badge_count(TAB_FILLS, len(fills_snapshot))
        async with grid_update_lock:
            await _refresh_fills_grid()
        order_flow_panel.add_trade(normalized)
        _update_last_sync_label([normalized])
        await activity_feed.add_item(normalized)

    async def handle_close_position(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        symbol = str(detail.get("symbol", "")).strip()
        qty = detail.get("qty", 0)
        if not symbol:
            ui.notify("Cannot close position: missing symbol", type="negative")
            return

        # SECURITY: Fetch fresh kill switch state before critical operation
        # Cached state may be stale (race condition between page load and button click)
        # Fresh API call ensures we have authoritative state for this decision
        fresh_ks_state: bool | None = kill_switch_engaged  # Fallback to cached if API fails
        try:
            ks_status = await trading_client.fetch_kill_switch_status(
                user_id,
                role=user_role,
                strategies=user_strategies,
            )
            state = str(ks_status.get("state", "")).upper()
            fresh_ks_state = _parse_kill_switch_state(state)
            logger.debug(
                "kill_switch_fresh_check",
                extra={"client_id": client_id, "state": state, "cached": kill_switch_engaged},
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            # On API failure, preserve fail-open for risk-reducing closes (use cached or None)
            logger.warning(
                "kill_switch_fresh_check_failed",
                extra={"client_id": client_id, "error": type(exc).__name__},
            )

        await on_close_position(
            symbol,
            qty,
            user_id,
            user_role,
            kill_switch_engaged=fresh_ks_state,
            strategies=user_strategies,
        )

    async def handle_cancel_order(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        order_id = str(detail.get("client_order_id", "")).strip()
        symbol = str(detail.get("symbol", "")).strip() or "unknown"
        broker_order_id = str(detail.get("broker_order_id", "")).strip() or None
        await on_cancel_order(order_id, symbol, user_id, user_role, broker_order_id=broker_order_id)

    async def handle_cancel_parent_order(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        parent_order_id = str(detail.get("parent_order_id", "")).strip()
        symbol = str(detail.get("symbol", "")).strip() or "unknown"

        # SECURITY: Fetch child orders directly from API by parent_order_id instead
        # of fetching all orders. This is more efficient for users with many orders
        # and ensures we're operating on the most up-to-date data.
        server_children: list[dict[str, Any]] = []
        if parent_order_id:
            try:
                fresh_orders_response = await trading_client.fetch_open_orders(
                    user_id,
                    role=user_role,
                    strategies=user_strategies,
                    parent_order_id=parent_order_id,
                )
                server_children = fresh_orders_response.get("orders", [])
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "cancel_parent_order_fetch_failed",
                    extra={
                        "parent_order_id": parent_order_id,
                        "status_code": exc.response.status_code,
                    },
                )
                ui.notify("Failed to fetch current orders", type="negative")
                return
            except httpx.RequestError as exc:
                logger.warning(
                    "cancel_parent_order_request_error",
                    extra={
                        "parent_order_id": parent_order_id,
                        "error": type(exc).__name__,
                    },
                )
                ui.notify("Network error fetching orders", type="negative")
                return

        await on_cancel_parent_order(
            parent_order_id or None,
            symbol,
            server_children,
            user_id,
            user_role,
        )

    async def handle_dom_price_click(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        symbol = str(detail.get("symbol", "")).strip()
        side = str(detail.get("side", "")).strip().lower()
        price = detail.get("price")
        if not symbol:
            ui.notify("Order book click missing symbol", type="warning")
            return
        if side not in {"buy", "sell"}:
            ui.notify("Order book click has invalid side", type="warning")
            return
        await order_context.handle_dom_price_click(symbol, side, price)

    async def handle_hierarchical_expansion(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        expanded_ids = detail.get("expanded_ids") or []
        if isinstance(expanded_ids, list):
            hierarchical_state.update_expanded(expanded_ids)
            await hierarchical_state.save()
            ui.run_javascript(
                f"window._hierarchicalOrdersExpanded = {json.dumps(expanded_ids)};"
                "if (window.HierarchicalOrdersGrid && window._hierarchicalOrdersGridApi)"
                " window.HierarchicalOrdersGrid.restoreExpansion(window._hierarchicalOrdersGridApi, window._hierarchicalOrdersExpanded);"
            )

    async def handle_modify_order(event: events.GenericEventArguments) -> None:
        if user_role == "viewer":
            ui.notify("Viewers cannot modify orders", type="warning")
            return
        detail = _extract_event_detail(event.args)
        if not detail.get("client_order_id"):
            ui.notify("Cannot modify order: missing client_order_id", type="negative")
            return
        modify_dialog.open(detail)

    async def handle_show_order_audit(event: events.GenericEventArguments) -> None:
        """Show order audit trail dialog (P6T8)."""
        detail = _extract_event_detail(event.args)
        client_order_id = str(detail.get("client_order_id", "")).strip()
        if not client_order_id:
            ui.notify("Cannot show audit: missing order ID", type="negative")
            return
        await show_order_audit_dialog(
            client_order_id=client_order_id,
            user_id=user_id,
            role=user_role,
            strategies=user_strategies,
        )

    # NOTE: OneClickHandler is wired to order_context.set_one_click_handler() above
    # for cached state sync. JS one-click events (shift/ctrl/alt clicks) require
    # dom_ladder.js to emit modifier key info - currently not implemented.
    # When JS is updated, add: ui.on("one_click", handle_one_click, args=["detail"])

    ui.on("close_position", handle_close_position, args=["detail"])
    ui.on("cancel_order", handle_cancel_order, args=["detail"])
    ui.on("cancel_parent_order", handle_cancel_parent_order, args=["detail"])
    ui.on("dom_price_click", handle_dom_price_click, args=["detail"])
    ui.on("modify_order", handle_modify_order, args=["detail"])
    ui.on("show_order_audit", handle_show_order_audit, args=["detail"])  # P6T8
    ui.on("grid_filters_restored", handle_grid_filters_restored, args=["detail"])
    ui.on("hierarchical_orders_expansion", handle_hierarchical_expansion, args=["detail"])

    await realtime.subscribe(position_channel(user_id), on_position_update)
    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)
    await realtime.subscribe(circuit_breaker_channel(), on_circuit_breaker_update)
    await realtime.subscribe(orders_channel(user_id), on_orders_update)
    await realtime.subscribe(fills_channel(user_id), on_fill_event)

    async def update_market_data() -> None:
        _, has_fresh_market_data = await MarketPriceCache.get_prices_with_freshness(
            trading_client,
            user_id=user_id,
            role=user_role,
            strategies=user_strategies,
        )
        _mark_workspace_live_data()
        _evaluate_workspace_mask()

    market_timer = ui.timer(config.DASHBOARD_MARKET_POLL_SECONDS, update_market_data)
    timers.append(market_timer)

    if use_workspace_v2:
        clock_timer = ui.timer(1.0, _update_workspace_clock_pill)
        timers.append(clock_timer)

    async def check_stale_data() -> None:
        for card in [pnl_card, positions_card, realized_card, bp_card]:
            if card.is_stale(threshold=30.0):
                card.mark_stale()
        _evaluate_workspace_mask()

    stale_timer = ui.timer(config.DASHBOARD_STALE_CHECK_SECONDS, check_stale_data)
    timers.append(stale_timer)

    strategy_context_refresh_generation = 0
    strategy_context_refresh_task: asyncio.Task[None] | None = None
    strategy_context_refresh_pending = False
    strategy_context_dashboard_closing = False
    last_strategy_context_symbol: str | None = order_context.get_selected_symbol()

    def sync_order_flow_symbol() -> None:
        nonlocal last_strategy_context_symbol
        selected_symbol = order_context.get_selected_symbol()
        order_flow_panel.set_symbol(selected_symbol)
        if strategy_context_widget is not None:
            strategy_context_widget.set_symbol(selected_symbol)
        if selected_symbol != last_strategy_context_symbol:
            last_strategy_context_symbol = selected_symbol
            _schedule_strategy_model_context_refresh(invalidate_running=True)

    flow_symbol_timer = ui.timer(0.5, sync_order_flow_symbol)
    timers.append(flow_symbol_timer)

    async def _resolve_strategy_for_symbol(symbol: str) -> str | None:
        """Resolve symbol -> strategy mapping using fail-closed uniqueness rules."""
        if len(authorized_strategy_scope) == 1:
            return authorized_strategy_scope[0]
        if async_pool is None:
            return None

        try:
            normalized_symbol = validate_and_normalize_symbol(symbol)
        except ValueError:
            return None

        sql = (
            "SELECT strategy_id "
            "FROM orders "
            "WHERE symbol = %s AND strategy_id IS NOT NULL "
        )
        params: tuple[Any, ...]
        if authorized_strategy_scope:
            sql += "AND strategy_id = ANY(%s) "
            params = (normalized_symbol, authorized_strategy_scope)
        else:
            params = (normalized_symbol,)
        sql += "GROUP BY strategy_id ORDER BY strategy_id LIMIT 2"

        try:
            async with acquire_connection(async_pool) as conn:
                cursor = await conn.execute(sql, params)
                rows = await cursor.fetchall()
        except Exception as exc:
            logger.warning(
                "strategy_symbol_resolution_failed",
                extra={
                    "client_id": client_id,
                    "symbol": symbol,
                    "error": type(exc).__name__,
                },
            )
            return None

        if len(rows) != 1:
            return None

        first = rows[0]
        strategy_id = first.get("strategy_id") if isinstance(first, dict) else first[0]
        if not strategy_id:
            return None
        return str(strategy_id)

    async def _fetch_model_registry_context(strategy_id: str) -> tuple[str, str | None]:
        """Fetch model status/version for a strategy from model_registry."""
        if async_pool is None:
            return ("unknown", None)

        try:
            async with acquire_connection(async_pool) as conn:
                cursor = await conn.execute(
                    """
                    SELECT status, version
                    FROM model_registry
                    WHERE strategy_name = %s
                    ORDER BY
                        CASE
                            WHEN status = 'active' THEN 1
                            WHEN status = 'testing' THEN 2
                            WHEN status = 'inactive' THEN 3
                            WHEN status = 'failed' THEN 4
                            ELSE 5
                        END,
                        activated_at DESC NULLS LAST,
                        created_at DESC
                    LIMIT 1
                    """,
                    (strategy_id,),
                )
                row = await cursor.fetchone()
        except Exception as exc:
            logger.warning(
                "strategy_model_context_fetch_failed",
                extra={
                    "client_id": client_id,
                    "strategy_id": strategy_id,
                    "error": type(exc).__name__,
                },
            )
            return ("unknown", None)

        if row is None:
            return ("unknown", None)

        if isinstance(row, dict):
            status_raw = row.get("status")
            version_raw = row.get("version")
        else:
            status_raw = row[0]
            version_raw = row[1]

        status = normalize_execution_status(status_raw)
        version = str(version_raw).strip() if version_raw else None
        return (status, version)

    def _build_strategy_context_banner(
        *,
        strategy_status: str,
        model_status: str,
        gate_reason: str | None,
    ) -> str:
        return resolve_strategy_context_banner(
            strategy_status=strategy_status,
            model_status=model_status,
            gate_reason=gate_reason,
        )

    def _is_strategy_context_refresh_stale(
        refresh_generation: int,
        expected_symbol: str | None,
    ) -> bool:
        return not should_apply_strategy_context_result(
            refresh_generation=refresh_generation,
            active_generation=strategy_context_refresh_generation,
            expected_symbol=expected_symbol,
            current_symbol=order_context.get_selected_symbol(),
        )

    async def _refresh_strategy_model_context(refresh_generation: int) -> None:
        selected_symbol = order_context.get_selected_symbol()
        if strategy_context_widget is not None:
            strategy_context_widget.set_symbol(selected_symbol)
        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return

        gate_enabled = config.FEATURE_STRATEGY_MODEL_EXECUTION_GATING
        if not selected_symbol:
            if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
                return
            order_context.dispatch_strategy_model_context(
                strategy_status="unknown",
                model_status="unknown",
                gate_enabled=gate_enabled,
                gate_reason="Select a symbol to resolve execution context",
                strategy_label="Strategy: --",
                model_label="Model: --",
                banner="Select a symbol to resolve strategy/model execution context.",
            )
            return

        strategy_id = await _resolve_strategy_for_symbol(selected_symbol)
        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return
        if not strategy_id:
            unresolved_reason = "No unique strategy mapping for selected symbol"
            order_context.dispatch_strategy_model_context(
                strategy_status="unknown",
                model_status="unknown",
                gate_enabled=gate_enabled,
                gate_reason=unresolved_reason,
                strategy_label="Strategy: unresolved",
                model_label="Model: unresolved",
                banner=f"{unresolved_reason}. Risk-increasing orders may be gated.",
            )
            return

        strategy_status = "unknown"
        model_status = "unknown"
        model_version: str | None = None
        reason_parts: list[str] = []

        try:
            strategy_payload = await trading_client.fetch_strategy_status(
                strategy_id,
                user_id=user_id,
                role=user_role,
                strategies=user_strategies,
            )
            strategy_status = normalize_execution_status(strategy_payload.get("status"))
            payload_model_status = strategy_payload.get("model_status")
            if payload_model_status:
                model_status = normalize_execution_status(payload_model_status)
            payload_model_version = strategy_payload.get("model_version")
            if payload_model_version:
                model_version = str(payload_model_version).strip()
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
            reason_parts.append(f"strategy status unavailable ({type(exc).__name__})")
        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return

        db_model_status, db_model_version = await _fetch_model_registry_context(strategy_id)
        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return
        if model_status == "unknown":
            model_status = db_model_status
        if model_version is None:
            model_version = db_model_version

        strategy_safe = is_strategy_execution_safe(strategy_status)
        model_safe = is_model_execution_safe(model_status)

        gate_reason: str | None = None
        if not strategy_safe:
            gate_reason = f"strategy is {strategy_status.upper()}"
        elif not model_safe:
            gate_reason = f"model is {model_status.upper()}"
        if reason_parts:
            context_reason = "; ".join(reason_parts)
            gate_reason = f"{gate_reason}; {context_reason}" if gate_reason else context_reason

        strategy_label = f"Strategy: {strategy_id}"
        model_label = f"Model: {model_version or 'unassigned'}"
        banner = _build_strategy_context_banner(
            strategy_status=strategy_status,
            model_status=model_status,
            gate_reason=gate_reason,
        )

        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return
        order_context.dispatch_strategy_model_context(
            strategy_status=strategy_status,
            model_status=model_status,
            gate_enabled=gate_enabled,
            gate_reason=gate_reason,
            strategy_label=strategy_label,
            model_label=model_label,
            banner=banner,
        )

    async def _run_strategy_model_context_refresh(refresh_generation: int) -> None:
        nonlocal strategy_context_refresh_task, strategy_context_refresh_pending
        try:
            await _refresh_strategy_model_context(refresh_generation)
        finally:
            strategy_context_refresh_task = None

            should_run_pending = should_run_pending_strategy_context_refresh(
                refresh_pending=strategy_context_refresh_pending,
                dashboard_closing=strategy_context_dashboard_closing,
            )
            strategy_context_refresh_pending = False
            if should_run_pending:
                _start_strategy_model_context_refresh(strategy_context_refresh_generation)

    def _start_strategy_model_context_refresh(refresh_generation: int) -> None:
        nonlocal strategy_context_refresh_task
        strategy_context_refresh_task = asyncio.create_task(
            _run_strategy_model_context_refresh(refresh_generation)
        )

    def _schedule_strategy_model_context_refresh(*, invalidate_running: bool = False) -> None:
        nonlocal strategy_context_dashboard_closing
        nonlocal strategy_context_refresh_generation
        nonlocal strategy_context_refresh_pending
        nonlocal strategy_context_refresh_task
        next_generation, mark_pending, start_generation = plan_strategy_context_refresh_request(
            current_generation=strategy_context_refresh_generation,
            task_running=bool(
                strategy_context_refresh_task and not strategy_context_refresh_task.done()
            ),
            dashboard_closing=strategy_context_dashboard_closing,
            invalidate_running=invalidate_running,
        )
        strategy_context_refresh_generation = next_generation
        if mark_pending:
            strategy_context_refresh_pending = True
            return
        if start_generation is not None:
            _start_strategy_model_context_refresh(start_generation)

    _schedule_strategy_model_context_refresh()
    strategy_context_timer = ui.timer(
        config.DASHBOARD_STRATEGY_CONTEXT_REFRESH_SECONDS,
        lambda: _schedule_strategy_model_context_refresh(invalidate_running=False),
    )
    timers.append(strategy_context_timer)

    def cleanup_timers() -> None:
        nonlocal strategy_context_dashboard_closing, strategy_context_refresh_pending
        strategy_context_dashboard_closing = True
        strategy_context_refresh_pending = False
        for timer in timers:
            timer.cancel()
        if strategy_context_refresh_task and not strategy_context_refresh_task.done():
            strategy_context_refresh_task.cancel()

    async def cleanup_strategy_context_task() -> None:
        if strategy_context_refresh_task and not strategy_context_refresh_task.done():
            strategy_context_refresh_task.cancel()
            try:
                await strategy_context_refresh_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "strategy_context_refresh_task_cleanup_failed",
                    extra={"client_id": client_id},
                    exc_info=True,
                )

    await lifecycle.register_cleanup_callback(client_id, cleanup_timers)
    await lifecycle.register_cleanup_callback(client_id, cleanup_strategy_context_task)
    await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)

    # Initialize OrderEntryContext AFTER UI creation (per spec lifecycle pattern)
    # This starts timers, loads data, and establishes subscriptions
    try:
        await order_context.initialize()
    except Exception as exc:
        logger.error(
            "order_context_init_failed",
            extra={"client_id": client_id, "error": str(exc)},
        )
        ui.notify(
            "Order entry initialization failed - some features may be unavailable",
            type="warning",
        )

    # Register cleanup for OrderEntryContext on disconnect
    async def cleanup_order_context() -> None:
        try:
            await order_context.dispose()
        except Exception as exc:
            logger.warning(
                "order_context_dispose_failed",
                extra={"client_id": client_id, "error": str(exc)},
            )

    await lifecycle.register_cleanup_callback(client_id, cleanup_order_context)


__all__ = ["dashboard", "MarketPriceCache"]
