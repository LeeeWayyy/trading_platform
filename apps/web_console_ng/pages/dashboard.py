"""Main trading dashboard with real-time metric cards."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
from nicegui import Client, app, events, run, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.auth.redirects import TRADE_REDIRECT_QUERY_KEYS, with_root_path
from apps.web_console_ng.components.data_health_widget import render_data_health
from apps.web_console_ng.components.execution_context import (
    build_execution_context_snapshot,
)
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
from apps.web_console_ng.core.audit import audit_log
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.connection_monitor import ConnectionMonitor
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.core.dependencies import get_sync_db_pool, get_sync_redis_client
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
from apps.web_console_ng.ui.root_path import render_client_redirect, resolve_rooted_path_from_ui
from apps.web_console_ng.utils.time import validate_and_normalize_symbol
from libs.core.common.db import acquire_connection
from libs.core.redis_client import RedisClient
from libs.data.data_pipeline.health_monitor import get_health_monitor
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)
from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess
from libs.web_console_services.cb_service import (
    RateLimitExceeded,
    RBACViolation,
    ValidationError,
)

if TYPE_CHECKING:
    from libs.web_console_services.cb_service import CircuitBreakerService

logger = logging.getLogger(__name__)

# Maximum fills to keep in memory to prevent unbounded growth
MAX_FILLS_ITEMS = 100
# Workspace live-data staleness threshold before interaction lock
WORKSPACE_DATA_STALE_THRESHOLD_S = 30.0
# Deterministic reason templates for legacy manual-controls payload compatibility.
CANCEL_ALL_REASON_TEMPLATE = "Trade workspace cancel-all for symbol {symbol}"
FLATTEN_ALL_REASON_TEMPLATE = "Trade workspace flatten-all positions ({count} visible)"
# Explicit UI allowlists prevent unknown roles from receiving bulk action controls.
# Backend manual-controls endpoints remain the source of truth for authorization.
_CANCEL_ALL_ALLOWED_ROLES = frozenset({"admin", "operator", "trader"})
_FLATTEN_ALL_ALLOWED_ROLES = frozenset({"admin"})
# Strategy resolution query lookback to keep orders scan bounded
STRATEGY_RESOLUTION_LOOKBACK_DAYS = 90
STRATEGY_RESOLUTION_CACHE_TTL_S = 5.0
STRATEGY_RESOLUTION_CACHE_MAX_ENTRIES = 1024
TRADE_WORKSPACE_CIRCUIT_TRIP_REASON = "MANUAL"
TRADE_WORKSPACE_CIRCUIT_RESET_REASON = "Trade workspace manual circuit reset confirmation"
ScopeKey = tuple[str, frozenset[str]]
StrategyResolutionScopeKey = tuple[str, ...]
StrategyResolutionCacheKey = tuple[StrategyResolutionScopeKey, str]

_strategy_resolution_cache: OrderedDict[
    StrategyResolutionCacheKey, tuple[str | None, str, float]
] = OrderedDict()
# Process-local cache is intentional: short TTL smooths per-worker query bursts without
# introducing cross-worker consistency requirements for deterministic DB lookups.
_strategy_resolution_cache_lock = threading.Lock()


def _get_trade_workspace_cb_service() -> CircuitBreakerService | None:
    """Return sync circuit-breaker service for trade-workspace manual controls.

    Redis is authoritative for circuit-breaker safety state. If Redis is not
    available, controls fail closed instead of constructing an alternate client.
    """
    if not hasattr(app.storage, "_trade_workspace_cb_service"):
        from libs.web_console_services.cb_service import CircuitBreakerService

        try:
            sync_pool = get_sync_db_pool()
        except RuntimeError:
            sync_pool = None
            logger.warning(
                "trade_workspace_sync_db_pool_unavailable",
                extra={"impact": "circuit breaker audit logging disabled"},
            )

        try:
            sync_redis: RedisClient = get_sync_redis_client()  # type: ignore[assignment]
        except RuntimeError:
            logger.error(
                "trade_workspace_redis_unavailable_fail_closed",
                extra={"impact": "circuit breaker trade control disabled"},
            )
            return None

        app.storage._trade_workspace_cb_service = CircuitBreakerService(  # type: ignore[attr-defined]  # noqa: B010
            sync_redis,
            sync_pool,
        )

    service: CircuitBreakerService = getattr(app.storage, "_trade_workspace_cb_service")  # noqa: B009
    return service


def _build_strategy_resolution_scope_key(
    authorized_strategy_scope: list[str],
) -> StrategyResolutionScopeKey:
    """Normalize strategy authorization scope into a deterministic cache key."""
    normalized = sorted(
        {
            strategy_id.strip()
            for strategy_id in authorized_strategy_scope
            if isinstance(strategy_id, str) and strategy_id.strip()
        }
    )
    return tuple(normalized)


def _get_strategy_resolution_from_shared_cache(
    *,
    scope_key: StrategyResolutionScopeKey,
    normalized_symbol: str,
) -> tuple[str | None, str] | None:
    cache_key = (scope_key, normalized_symbol)
    with _strategy_resolution_cache_lock:
        cached = _strategy_resolution_cache.get(cache_key)
        if cached is None:
            return None
        strategy_id, reason, cached_at = cached
        if (time.monotonic() - cached_at) > STRATEGY_RESOLUTION_CACHE_TTL_S:
            _strategy_resolution_cache.pop(cache_key, None)
            return None
        _strategy_resolution_cache.move_to_end(cache_key)
        return (strategy_id, reason)


def _set_strategy_resolution_in_shared_cache(
    *,
    scope_key: StrategyResolutionScopeKey,
    normalized_symbol: str,
    resolution: tuple[str | None, str],
) -> tuple[str | None, str]:
    cache_key = (scope_key, normalized_symbol)
    with _strategy_resolution_cache_lock:
        _strategy_resolution_cache.pop(cache_key, None)
        _strategy_resolution_cache[cache_key] = (
            resolution[0],
            resolution[1],
            time.monotonic(),
        )
        if len(_strategy_resolution_cache) > STRATEGY_RESOLUTION_CACHE_MAX_ENTRIES:
            _strategy_resolution_cache.popitem(last=False)
    return resolution


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
        self._last_update = time.monotonic()

    def is_stale(self, threshold: float = 30.0) -> bool:
        if self._last_update is None:
            return False
        return (time.monotonic() - self._last_update) > threshold

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


def should_enable_strategy_context_refresh(
    *,
    gate_enabled: bool,
    has_strategy_widget: bool,
) -> bool:
    """Enable strategy-context polling only when UI or execution gating consumes it."""
    return gate_enabled or has_strategy_widget


def resolve_model_gate_inputs(
    *,
    model_status: str | None,
    model_version: str | None,
    feature_model_registry_enabled: bool,
) -> tuple[str, str | None, bool]:
    """Normalize model context and whether model status should enforce execution gating."""
    if feature_model_registry_enabled:
        return normalize_execution_status(model_status), model_version, True
    return "ready", (model_version or "disabled"), False


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
    if normalized in {"DISENGAGED", "ACTIVE"}:
        return ("KILL DISARMED", "muted")
    return (f"KILL {normalized}", "warning")


def resolve_workspace_circuit_breaker_pill(state: str | None) -> tuple[str, str]:
    """Return circuit-breaker pill text/tone for workspace command strip."""
    normalized = str(state or "UNKNOWN").upper()
    if normalized == "TRIPPED":
        return ("CB TRIPPED", "danger")
    if normalized in {"OPEN", "QUIET_PERIOD"}:
        return ("CB READY", "normal")
    return (f"CB {normalized}", "muted")


def resolve_workspace_circuit_breaker_control(
    state: str | None,
    *,
    can_trip: bool,
    can_reset: bool,
) -> tuple[str, str, str, bool, str]:
    """Return icon, tone, tooltip, enabled flag, and action for CB ticket control."""
    normalized = str(state or "UNKNOWN").upper()
    if normalized == "TRIPPED":
        enabled = can_reset
        tooltip = (
            "Resume trading after confirmation"
            if enabled
            else "RESET_CIRCUIT permission required"
        )
        return ("lock_open", "danger", tooltip, enabled, "reset")
    if normalized in {"OPEN", "QUIET_PERIOD"}:
        enabled = can_trip
        tooltip = (
            "Halt new order entries after confirmation"
            if enabled
            else "TRIP_CIRCUIT permission required"
        )
        return ("lock", "normal", tooltip, enabled, "trip")
    return (
        "help_outline",
        "muted",
        "Breaker status unknown: check connection before changing state",
        False,
        "none",
    )


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
    base_links = [
        ("Alerts", "/alerts"),
        ("Journal", "/journal"),
        ("Strategies", "/strategies"),
        ("Compare", "/compare"),
        ("Inspector", "/data/inspector"),
    ]
    visible_links: list[tuple[str, str]] = []
    for label, path in base_links:
        if path == "/alerts" and (not feature_alerts_enabled or not can_view_alerts):
            continue
        if path == "/strategies" and (
            not feature_strategy_management_enabled or not can_manage_strategies
        ):
            continue
        if path == "/data/inspector" and not can_view_data_quality:
            continue
        visible_links.append((label, path))

    if (
        feature_model_registry_enabled
        and can_view_models
    ):
        visible_links.append(("Promote", "/research?tab=promote"))
    return visible_links


def build_cancel_all_orders_reason(symbol: str) -> str:
    """Return deterministic reason text for cancel-all payloads."""
    normalized_symbol = str(symbol).strip().upper() or "UNKNOWN"
    return CANCEL_ALL_REASON_TEMPLATE.format(symbol=normalized_symbol)


def build_flatten_all_positions_reason(*, positions_count: int) -> str:
    """Return deterministic reason text for flatten-all payloads."""
    safe_count = max(0, int(positions_count))
    return FLATTEN_ALL_REASON_TEMPLATE.format(count=safe_count)


def can_cancel_all_orders(*, user_role: str | None) -> bool:
    """Return True when role is authorized to run per-symbol cancel-all."""
    normalized_role = str(user_role or "").strip().lower()
    return normalized_role in _CANCEL_ALL_ALLOWED_ROLES


def can_flatten_all_positions(*, user_role: str | None) -> bool:
    """Return True when role is authorized to run flatten-all."""
    normalized_role = str(user_role or "").strip().lower()
    return normalized_role in _FLATTEN_ALL_ALLOWED_ROLES


def format_http_error_for_log(exc: httpx.HTTPStatusError) -> str:
    """Return compact non-sensitive HTTP error string for structured logging."""
    status_code = exc.response.status_code
    try:
        request_path = str(exc.request.url.path)
    except Exception:
        request_path = ""
    if request_path:
        return f"HTTP {status_code} {request_path}"
    return f"HTTP {status_code}"


def audit_http_status_details(exc: httpx.HTTPStatusError) -> dict[str, Any]:
    """Return non-sensitive HTTP fields suitable for audit payloads."""
    return {"status": int(exc.response.status_code)}


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
        request = getattr(client, "request", None)
        request_root_path = ""
        if request is not None:
            request_root_path = str(request.scope.get("root_path", ""))
        ui.navigate.to(with_root_path("/login", root_path=request_root_path))
        return

    lifecycle = ClientLifecycleManager.get()

    # client_id is per-websocket lifecycle key for cleanup tracking.
    client_id = client.storage.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id
        logger.debug("dashboard_generated_client_id", extra={"client_id": client_id})

    # Use a reconnect-stable market-data owner ID bound to this page client.
    market_data_owner_id = client.storage.get("market_data_owner_id")
    if not isinstance(market_data_owner_id, str) or not market_data_owner_id:
        market_data_owner_id = lifecycle.generate_client_id()
        client.storage["market_data_owner_id"] = market_data_owner_id
    # Track currently active order-context generation for reconnect handoff.
    order_context_generation_id = lifecycle.generate_client_id()
    client.storage["active_order_context_generation_id"] = order_context_generation_id

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
        client_id=market_data_owner_id,
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

    def _safe_storage_user_get(key: str) -> Any | None:
        """Read session storage safely when request context is temporarily unavailable."""
        try:
            return app.storage.user.get(key)
        except (RuntimeError, AttributeError, KeyError) as exc:
            logger.debug(
                "dashboard_storage_user_unavailable",
                extra={"client_id": client_id, "key": key, "error": str(exc)},
            )
            return None

    background_tasks: set[asyncio.Task[Any]] = set()

    def _handle_dashboard_task_done(task: asyncio.Task[Any]) -> None:
        background_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "dashboard_background_task_failed",
                extra={"client_id": client_id},
            )

    def _schedule_dashboard_task(task_coro: Coroutine[Any, Any, None]) -> None:
        """Track fire-and-forget UI tasks to prevent premature GC cancellation."""
        task = asyncio.create_task(task_coro)
        background_tasks.add(task)
        task.add_done_callback(_handle_dashboard_task_done)

    async def _handle_cancel_symbol_orders_click() -> None:
        """Run cancel dialog flow in request-bound UI context.

        This stays request-bound (instead of fire-and-forget) because the dialog
        path emits UI updates that require an active slot stack.
        """
        await _show_cancel_symbol_orders_dialog()

    async def _handle_flatten_all_positions_click() -> None:
        """Run flatten dialog flow in request-bound UI context.

        This stays request-bound (instead of fire-and-forget) because the dialog
        path emits UI updates that require an active slot stack.
        """
        await _show_flatten_all_positions_dialog()

    order_flow_panel = OrderFlowPanel(max_rows=12)
    strategy_context_widget: StrategyContextWidget | None = None
    tabs_host: ui.column | None = None
    log_tail_host: ui.column | None = None
    workspace_root: Any | None = None
    workspace_overlay: Any | None = None
    workspace_overlay_title: ui.label | None = None
    workspace_overlay_detail: ui.label | None = None
    cancel_symbol_orders_btn: ui.button | None = None
    flatten_all_positions_btn: ui.button | None = None
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
    session_clock_pill: _CommandStatusPill | None = None
    circuit_breaker_control_btn: ui.button | None = None
    circuit_breaker_control_tooltip: ui.tooltip | None = None
    circuit_breaker_control_tone = "workspace-v2-circuit-control-muted"
    workspace_circuit_breaker_state = "OPEN"
    can_view_alerts = has_permission(user, Permission.VIEW_ALERTS)
    can_view_data_quality = has_permission(user, Permission.VIEW_DATA_QUALITY)
    can_manage_strategies = has_permission(user, Permission.MANAGE_STRATEGIES)
    can_view_models = has_permission(user, Permission.VIEW_MODELS)
    can_trip_circuit = has_permission(user, Permission.TRIP_CIRCUIT)
    can_reset_circuit = has_permission(user, Permission.RESET_CIRCUIT)
    workspace_quick_links = resolve_workspace_quick_links(
        user_role=user_role,
        feature_alerts_enabled=config.FEATURE_ALERTS,
        can_view_alerts=can_view_alerts,
        can_view_data_quality=can_view_data_quality,
        feature_strategy_management_enabled=config.FEATURE_STRATEGY_MANAGEMENT,
        can_manage_strategies=can_manage_strategies,
        feature_model_registry_enabled=config.FEATURE_MODEL_REGISTRY,
        can_view_models=can_view_models,
    )

    def _update_workspace_circuit_breaker_control() -> None:
        nonlocal circuit_breaker_control_tone
        if circuit_breaker_control_btn is None:
            return
        icon, tone, tooltip, enabled, action = resolve_workspace_circuit_breaker_control(
            workspace_circuit_breaker_state,
            can_trip=can_trip_circuit,
            can_reset=can_reset_circuit,
        )
        tone_class = f"workspace-v2-circuit-control-{tone}"
        circuit_breaker_control_btn.set_icon(icon)
        circuit_breaker_control_btn.props(f"aria-label='{tooltip}' data-cb-action={action}")
        if circuit_breaker_control_tooltip is not None:
            circuit_breaker_control_tooltip.text = tooltip
        if circuit_breaker_control_tone != tone_class:
            circuit_breaker_control_btn.classes(remove=circuit_breaker_control_tone)
            circuit_breaker_control_btn.classes(add=tone_class)
            circuit_breaker_control_tone = tone_class
        if enabled:
            circuit_breaker_control_btn.enable()
        else:
            circuit_breaker_control_btn.disable()

    async def _refresh_circuit_breaker_from_service() -> None:
        nonlocal workspace_circuit_breaker_state
        cb_service = _get_trade_workspace_cb_service()
        if cb_service is None:
            workspace_circuit_breaker_state = "UNKNOWN"
            _update_workspace_circuit_breaker_pill()
            _update_workspace_circuit_breaker_control()
            return
        status = await run.io_bound(cb_service.get_status)
        workspace_circuit_breaker_state = str(status.get("state", "")).upper() or "UNKNOWN"
        _update_workspace_circuit_breaker_pill()
        _update_workspace_circuit_breaker_control()
        app.storage.user["global_circuit_state"] = workspace_circuit_breaker_state
        dispatch_trading_state_event(
            client_id,
            {"circuitBreakerState": workspace_circuit_breaker_state},
        )

    async def _confirm_circuit_breaker_action(action: str) -> None:
        """Confirm and execute manual CB trip/reset from the trade workspace."""
        if action not in {"trip", "reset"}:
            ui.notify("Circuit breaker state is not actionable", type="warning")
            return

        title = "HALT ALL TRADING?" if action == "trip" else "RESUME TRADING?"
        body = (
            "This immediately blocks all new order entries. Risk-reducing exits and "
            "stop-loss handling remain available. Confirm emergency halt?"
            if action == "trip"
            else (
                "You are re-enabling order submission immediately. Confirm manual "
                "recovery only after system conditions are normalized."
            )
        )
        confirm_label = "Halt Trading" if action == "trip" else "Resume Trading"
        confirm_color = "red" if action == "trip" else "green"

        with ui.dialog() as dialog, ui.card().classes("workspace-v2-circuit-dialog"):
            ui.label(title).classes("workspace-v2-circuit-dialog-title")
            ui.label(body).classes("workspace-v2-circuit-dialog-copy")
            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat")

                async def _execute() -> None:
                    cb_service = _get_trade_workspace_cb_service()
                    if cb_service is None:
                        ui.notify(
                            "Circuit breaker service unavailable; control disabled for safety",
                            type="negative",
                        )
                        dialog.close()
                        return
                    try:
                        if action == "trip":
                            await run.io_bound(
                                cb_service.trip,
                                TRADE_WORKSPACE_CIRCUIT_TRIP_REASON,
                                user,
                                acknowledged=True,
                            )
                            ui.notify("Circuit breaker tripped", type="positive")
                        else:
                            ui.notify("Resuming trading...", type="info")
                            await run.io_bound(
                                cb_service.reset,
                                TRADE_WORKSPACE_CIRCUIT_RESET_REASON,
                                user,
                                acknowledged=True,
                            )
                            ui.notify("Trading resumed", type="positive")
                        dialog.close()
                        await _refresh_circuit_breaker_from_service()
                    except RateLimitExceeded as exc:
                        ui.notify(f"Rate limit exceeded: {exc}", type="negative")
                    except (RBACViolation, ValidationError) as exc:
                        ui.notify(str(exc), type="negative")
                    except (RuntimeError, ValueError) as exc:
                        logger.exception(
                            "trade_workspace_circuit_breaker_action_failed",
                            extra={
                                "action": action,
                                "state": workspace_circuit_breaker_state,
                                "error": str(exc),
                            },
                        )
                        ui.notify("Circuit breaker action failed", type="negative")

                ui.button(confirm_label, on_click=_execute, color=confirm_color)
        dialog.open()

    async def _handle_circuit_breaker_control_click() -> None:
        _icon, _tone, _tooltip, enabled, action = resolve_workspace_circuit_breaker_control(
            workspace_circuit_breaker_state,
            can_trip=can_trip_circuit,
            can_reset=can_reset_circuit,
        )
        if not enabled:
            ui.notify(_tooltip, type="warning")
            return
        await _confirm_circuit_breaker_action(action)

    def _render_circuit_breaker_header_action() -> None:
        nonlocal circuit_breaker_control_btn, circuit_breaker_control_tooltip
        circuit_breaker_control_btn = ui.button(
            icon="help_outline",
            on_click=_handle_circuit_breaker_control_click,
        ).props("flat round dense").classes(
            "workspace-v2-circuit-control workspace-v2-circuit-control-muted"
        )
        with circuit_breaker_control_btn:
            circuit_breaker_control_tooltip = ui.tooltip(
                "Breaker status unknown: check connection before changing state"
            )
        _update_workspace_circuit_breaker_control()

    # Unified Execution Workspace is the only supported trading layout.
    with ui.element("section").classes("workspace-v2 w-full mb-3") as _workspace_root:
        workspace_root = _workspace_root
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
            session_clock_pill = _CommandStatusPill("UTC --:--:--", enter_delay_ms=320)

        with ui.element("div").classes("workspace-v2-body"):
            with ui.element("div").classes("workspace-v2-zone-b workspace-v2-enter-zone workspace-v2-enter-zone-b"):
                with ui.element("div").classes("workspace-v2-chart-pane"):
                    order_context.create_price_chart(fill_parent=True).classes(
                        "w-full h-full"
                    )
                with ui.element("div").classes("workspace-v2-microstructure"):
                    with ui.element("div").classes("workspace-v2-microstructure-dom"):
                        order_context.create_dom_ladder(levels=5).classes("h-full overflow-hidden")
                    with ui.element("div").classes("workspace-v2-microstructure-side"):
                        order_flow_panel.create().classes("h-full overflow-hidden")
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

                strategy_context_widget = StrategyContextWidget(
                    strategies=user_strategies,
                    show_strategy_link=(
                        config.FEATURE_STRATEGY_MANAGEMENT and can_manage_strategies
                    ),
                    show_model_link=(config.FEATURE_MODEL_REGISTRY and can_view_models),
                )
                strategy_context_widget.create()
                order_context.set_strategy_context_widget(strategy_context_widget)

                order_context.create_order_ticket(
                    show_execution_context_ribbon=False,
                    header_actions=_render_circuit_breaker_header_action,
                )

                with ui.element("div").classes("workspace-v2-panel"):
                    ui.label("Execution Actions").classes("workspace-v2-panel-title mb-1")
                    ui.label("Bulk risk controls").classes("workspace-v2-kv mb-2")
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        cancel_symbol_orders_btn = ui.button(
                            "Cancel Symbol Orders",
                            on_click=_handle_cancel_symbol_orders_click,
                            color="orange",
                        ).classes("workspace-v2-bulk-btn workspace-v2-bulk-btn-warning")
                        flatten_all_positions_btn = ui.button(
                            "Flatten All Positions",
                            on_click=_handle_flatten_all_positions_click,
                            color="red",
                        ).classes("workspace-v2-bulk-btn workspace-v2-bulk-btn-danger")

                log_tail_host = ui.column().classes("workspace-v2-log-area")

        with ui.element("div").classes("workspace-v2-bottom"):
            tabs_host = ui.column().classes("workspace-v2-tabs-area")

        with ui.element("div").classes("workspace-v2-overlay hidden") as workspace_overlay:
            with ui.card().classes("workspace-v2-overlay-card"):
                workspace_overlay_title = ui.label("Connection unavailable").classes(
                    "workspace-v2-overlay-title"
                )
                workspace_overlay_detail = ui.label(
                    "Trading actions are locked until the workspace stream recovers."
                ).classes("workspace-v2-overlay-detail")

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
    activity_feed: LogTailPanel

    if tabs_host is None or log_tail_host is None:
        raise RuntimeError("workspace layout hosts are not initialized")

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

    notified_missing_ids: set[str] = set()
    notified_malformed: set[int] = set()  # Dedupe malformed position notifications
    notified_filter_restores: set[str] = set()  # Dedupe filter restore toasts per grid
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}  # Prevent churn from transient snapshot gaps
    grid_update_lock = asyncio.Lock()
    kill_switch_engaged: bool | None = None  # Real-time cached state for instant UI response
    workspace_kill_switch_state = "UNKNOWN"
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
    bulk_action_in_progress = False
    cancel_dialog_open = False
    flatten_dialog_open = False
    bulk_action_handlers_ready = False

    def _set_bulk_action_buttons_enabled(enabled: bool) -> None:
        if cancel_symbol_orders_btn is not None:
            cancel_enabled = (
                enabled
                and bulk_action_handlers_ready
                and can_cancel_all_orders(user_role=user_role)
                and not workspace_connection_read_only
                and not cancel_dialog_open
            )
            if cancel_enabled:
                cancel_symbol_orders_btn.enable()
            else:
                cancel_symbol_orders_btn.disable()
        if flatten_all_positions_btn is not None:
            flatten_enabled = (
                enabled
                and bulk_action_handlers_ready
                and can_flatten_all_positions(user_role=user_role)
                and not workspace_connection_read_only
                and not flatten_dialog_open
                and kill_switch_engaged is False
            )
            if flatten_enabled:
                flatten_all_positions_btn.enable()
            else:
                flatten_all_positions_btn.disable()

    def _is_bulk_action_read_only(*, notify: bool = True) -> bool:
        if workspace_connection_read_only:
            if notify:
                ui.notify("Read-only mode: connection lost", type="warning")
            return True
        return False

    expects_cancel_button = can_cancel_all_orders(user_role=user_role)
    expects_flatten_button = can_flatten_all_positions(user_role=user_role)
    if (expects_cancel_button and cancel_symbol_orders_btn is None) or (
        expects_flatten_button and flatten_all_positions_btn is None
    ):
        logger.error(
            "dashboard_bulk_action_buttons_missing",
            extra={
                "client_id": client_id,
                "expects_cancel_button": expects_cancel_button,
                "expects_flatten_button": expects_flatten_button,
            },
        )

    _set_bulk_action_buttons_enabled(True)

    def _update_workspace_connection_pill() -> None:
        if connection_status_pill is None:
            return
        text, tone = resolve_workspace_connection_pill(
            state=workspace_connection_state,
            is_read_only=workspace_connection_read_only,
        )
        connection_status_pill.set_state(text, tone)

    def _update_workspace_kill_switch_pill() -> None:
        if kill_switch_status_pill is None:
            return
        text, tone = resolve_workspace_kill_switch_pill(workspace_kill_switch_state)
        kill_switch_status_pill.set_state(text, tone)

    def _update_workspace_circuit_breaker_pill() -> None:
        if circuit_breaker_status_pill is None:
            return
        text, tone = resolve_workspace_circuit_breaker_pill(workspace_circuit_breaker_state)
        circuit_breaker_status_pill.set_state(text, tone)
        _update_workspace_circuit_breaker_control()

    def _update_workspace_clock_pill() -> None:
        if session_clock_pill is None:
            return
        session_clock_pill.set_state(
            f"UTC {datetime.now(UTC).strftime('%H:%M:%S')}",
            "muted",
        )

    def _set_workspace_mask(*, locked: bool, title: str = "", detail: str = "") -> None:
        """Show/hide workspace interaction mask for safety-critical stale/disconnect states."""
        if workspace_root is None or workspace_overlay is None:
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
        _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

    order_context.set_connection_state_callback(_on_workspace_connection_state)
    _update_workspace_connection_pill()
    _update_workspace_kill_switch_pill()
    _update_workspace_circuit_breaker_pill()
    _update_workspace_clock_pill()
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
        realized_card.update(_coerce_float(pnl_data.get("realized_pl_today", 0)))
        buying_power = account_info.get("buying_power")
        if buying_power is None:
            buying_power = pnl_data.get("buying_power", 0)
        bp_card.update(_coerce_float(buying_power))
        positions_snapshot = list(positions.get("positions", []))
        positions_card.update(len(positions_snapshot))
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
            # get_trades returns newest->oldest; add_trades expects oldest->newest.
            order_flow_panel.add_trades(list(reversed(recent_trades)))

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
        if state in {"DISENGAGED", "ACTIVE"}:
            return False
        return None

    def _normalize_kill_switch_state(state_raw: Any) -> str:
        state = str(state_raw or "").upper() or "UNKNOWN"
        return "DISENGAGED" if state == "ACTIVE" else state

    async def check_initial_kill_switch() -> None:
        """Fetch initial kill switch status on page load."""
        nonlocal kill_switch_engaged, workspace_kill_switch_state
        try:
            ks_status = await trading_client.fetch_kill_switch_status(
                user_id,
                role=user_role,
                strategies=user_strategies,
            )
            state = _normalize_kill_switch_state(ks_status.get("state", ""))
            workspace_kill_switch_state = state
            kill_switch_engaged = _parse_kill_switch_state(state)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "kill_switch_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            cached_state = _normalize_kill_switch_state(
                app.storage.user.get("global_kill_switch_state")
            )
            if cached_state != "UNKNOWN":
                workspace_kill_switch_state = cached_state
            else:
                workspace_kill_switch_state = "UNKNOWN"
            # Preserve fail-open close behavior during kill-switch service outages.
            # Cached state is only for UI continuity; submission guards use live checks.
            kill_switch_engaged = None
        _update_workspace_kill_switch_pill()
        app.storage.user["global_kill_switch_state"] = workspace_kill_switch_state
        dispatch_trading_state_event(
            client_id, {"killSwitchState": workspace_kill_switch_state}
        )

    await check_initial_kill_switch()
    _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

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
            if workspace_circuit_breaker_state == "QUIET_PERIOD":
                workspace_circuit_breaker_state = "OPEN"
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "circuit_breaker_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            cached_state = str(app.storage.user.get("global_circuit_state", "")).upper()
            workspace_circuit_breaker_state = cached_state or "UNKNOWN"
            if workspace_circuit_breaker_state == "QUIET_PERIOD":
                workspace_circuit_breaker_state = "OPEN"
        _update_workspace_circuit_breaker_pill()
        app.storage.user["global_circuit_state"] = workspace_circuit_breaker_state
        dispatch_trading_state_event(
            client_id, {"circuitBreakerState": workspace_circuit_breaker_state}
        )

    await check_initial_circuit_breaker()

    async def _verify_kill_switch_disengaged_for_flatten() -> tuple[bool, str]:
        """Fail-closed kill-switch check for flatten-all operations."""
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
            _update_workspace_kill_switch_pill()
            app.storage.user["global_kill_switch_state"] = _normalize_kill_switch_state(state)
            if state not in {"DISENGAGED", "ACTIVE"}:
                return (False, "Cannot flatten: Kill Switch is not DISENGAGED")
            return (True, "")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            workspace_kill_switch_state = "UNKNOWN"
            kill_switch_engaged = None
            _update_workspace_kill_switch_pill()
            app.storage.user["global_kill_switch_state"] = workspace_kill_switch_state
            logger.warning(
                "flatten_all_kill_switch_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            return (False, "Cannot verify kill switch status - action blocked")
        except Exception as exc:
            workspace_kill_switch_state = "UNKNOWN"
            kill_switch_engaged = None
            _update_workspace_kill_switch_pill()
            app.storage.user["global_kill_switch_state"] = workspace_kill_switch_state
            logger.exception(
                "flatten_all_kill_switch_check_unexpected_error",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            return (False, "Cannot verify kill switch status - action blocked")

    async def _emit_audit_log(action: str, details: dict[str, Any]) -> None:
        """Emit audit logs without blocking the event loop."""
        await asyncio.to_thread(
            audit_log,
            action=action,
            user_id=user_id,
            details=details,
        )

    async def _emit_audit_log_safe(action: str, details: dict[str, Any]) -> None:
        """Emit audit logs with full context and re-raise on failure."""
        try:
            await _emit_audit_log(action, details)
        except Exception:
            logger.exception(
                "dashboard_audit_log_emit_failed",
                extra={"user_id": user_id, "action": action},
            )
            raise

    async def _add_activity_item_safe(item: dict[str, Any], *, event: str) -> None:
        """Append activity item and re-raise on failure."""
        try:
            await activity_feed.add_item(item)
        except Exception:
            logger.exception(
                "dashboard_activity_feed_add_failed",
                extra={"user_id": user_id, "event": event},
            )
            raise

    async def _show_cancel_symbol_orders_dialog() -> None:
        """Open confirmation dialog for per-symbol cancel-all action."""
        nonlocal bulk_action_in_progress, cancel_dialog_open
        if bulk_action_in_progress or cancel_dialog_open:
            return
        if not can_cancel_all_orders(user_role=user_role):
            ui.notify("Role is not authorized to cancel orders", type="warning")
            return
        if _is_bulk_action_read_only():
            return

        selected_symbol = order_context.get_selected_symbol() or _current_symbol_filter()
        symbol = str(selected_symbol or "").strip().upper()
        if not symbol:
            ui.notify("Select a symbol before cancelling orders", type="warning")
            return

        cancel_dialog_open = True
        _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

        with ui.dialog().props("persistent") as dialog, ui.card().classes("p-4 min-w-[430px]"):
            def _on_dialog_close() -> None:
                nonlocal cancel_dialog_open
                if not cancel_dialog_open:
                    return
                cancel_dialog_open = False
                _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

                def _delete_dialog() -> None:
                    try:
                        dialog.delete()
                    except Exception:
                        logger.debug(
                            "dashboard_cancel_dialog_delete_failed",
                            extra={"client_id": client_id},
                            exc_info=True,
                        )

                ui.timer(0, _delete_dialog, once=True)

            dialog.on("hide", _on_dialog_close)
            ui.label(f"Cancel all open orders for {symbol}?").classes("text-lg font-bold")
            ui.label(
                "All currently working orders for the selected symbol will be cancelled."
            ).classes("text-sm text-slate-300")
            ui.label(
                "This is a risk-reducing action and does not place new orders."
            ).classes("text-sm text-slate-400")

            with ui.row().classes("gap-4 mt-4 justify-end"):

                async def _confirm_cancel_all_symbol_orders() -> None:
                    nonlocal bulk_action_in_progress
                    if bulk_action_in_progress:
                        return
                    if _is_bulk_action_read_only(notify=False):
                        return

                    bulk_action_in_progress = True
                    confirm_btn.disable()
                    keep_orders_btn.disable()
                    _set_bulk_action_buttons_enabled(False)
                    requested_at = datetime.now(UTC).isoformat()
                    reason = build_cancel_all_orders_reason(symbol)

                    try:
                        result = await trading_client.cancel_all_orders(
                            symbol=symbol,
                            reason=reason,
                            requested_by=user_id,
                            requested_at=requested_at,
                            user_id=user_id,
                            role=user_role,
                            strategies=user_strategies,
                        )
                        cancelled_count = max(0, _coerce_int(result.get("cancelled_count"), 0))
                        ui.notify(
                            f"Cancelled {cancelled_count} order(s) for {symbol}",
                            type="positive",
                        )
                        dialog.close()
                        await _emit_audit_log_safe(
                            "cancel_all_orders",
                            {
                                "symbol": symbol,
                                "cancelled_count": cancelled_count,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )
                        await _add_activity_item_safe(
                            {
                                "timestamp": requested_at,
                                "type": "orders",
                                "status": "cancelled",
                                "symbol": symbol,
                                "message": f"Cancelled {cancelled_count} order(s) for {symbol}",
                            },
                            event="cancel_all_orders",
                        )
                        await load_initial_data()
                    except httpx.HTTPStatusError as exc:
                        http_error = format_http_error_for_log(exc)
                        logger.warning(
                            "dashboard_cancel_all_orders_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "status": exc.response.status_code,
                                "error": http_error,
                                "requested_at": requested_at,
                            },
                        )
                        await _emit_audit_log_safe(
                            "cancel_all_orders_failed",
                            {
                                "symbol": symbol,
                                "reason": reason,
                                "requested_at": requested_at,
                                **audit_http_status_details(exc),
                            },
                        )
                        ui.notify(
                            f"Failed to cancel: HTTP {exc.response.status_code}",
                            type="negative",
                        )
                    except httpx.RequestError as exc:
                        logger.warning(
                            "dashboard_cancel_all_orders_failed",
                            extra={
                                "user_id": user_id,
                                "symbol": symbol,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        await _emit_audit_log_safe(
                            "cancel_all_orders_failed",
                            {
                                "symbol": symbol,
                                "error": type(exc).__name__,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify("Failed to cancel: network error", type="negative")
                    finally:
                        bulk_action_in_progress = False
                        if cancel_dialog_open:
                            confirm_btn.enable()
                            keep_orders_btn.enable()
                        _set_bulk_action_buttons_enabled(True)

                confirm_btn = ui.button(
                    "Cancel Orders",
                    on_click=_confirm_cancel_all_symbol_orders,
                    color="orange",
                ).classes("text-black")
                keep_orders_btn = ui.button("Keep Orders", on_click=dialog.close)

        dialog.open()

    async def _show_flatten_all_positions_dialog() -> None:
        """Open confirmation dialog for global flatten-all action."""
        nonlocal bulk_action_in_progress, flatten_dialog_open
        if bulk_action_in_progress or flatten_dialog_open:
            return
        if not can_flatten_all_positions(user_role=user_role):
            ui.notify("Admin permission required to flatten all positions", type="negative")
            return
        if _is_bulk_action_read_only():
            return

        flatten_dialog_open = True
        _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

        with ui.dialog().props("persistent") as dialog, ui.card().classes("p-4 min-w-[470px]"):
            def _on_dialog_close() -> None:
                nonlocal flatten_dialog_open
                if not flatten_dialog_open:
                    return
                flatten_dialog_open = False
                _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

                def _delete_dialog() -> None:
                    try:
                        dialog.delete()
                    except Exception:
                        logger.debug(
                            "dashboard_flatten_dialog_delete_failed",
                            extra={"client_id": client_id},
                            exc_info=True,
                        )

                ui.timer(0, _delete_dialog, once=True)

            dialog.on("hide", _on_dialog_close)
            ui.label("Flatten all positions?").classes("text-lg font-bold text-red-500")
            ui.label(
                "This will submit market close orders for all visible positions."
            ).classes("text-sm text-slate-300")
            ui.label("Type FLATTEN to confirm.").classes("text-sm text-slate-400 mt-2")
            typed_confirm = ui.input("Type FLATTEN").classes("w-full font-mono")

            with ui.row().classes("gap-4 mt-4 justify-end"):

                async def _confirm_flatten_all_positions() -> None:
                    nonlocal bulk_action_in_progress
                    if bulk_action_in_progress:
                        return
                    if _is_bulk_action_read_only(notify=False):
                        return
                    if str(typed_confirm.value or "").strip() != "FLATTEN":
                        ui.notify("Type FLATTEN exactly to proceed", type="warning")
                        return

                    id_token = _safe_storage_user_get("id_token")
                    if not id_token:
                        ui.notify(
                            "Authentication token missing - please sign in again",
                            type="negative",
                        )
                        dialog.close()
                        return

                    bulk_action_in_progress = True
                    confirm_btn.disable()
                    cancel_btn.disable()
                    _set_bulk_action_buttons_enabled(False)
                    safe_to_proceed, block_reason = await _verify_kill_switch_disengaged_for_flatten()
                    if not safe_to_proceed:
                        ui.notify(block_reason, type="negative")
                        bulk_action_in_progress = False
                        confirm_btn.enable()
                        cancel_btn.enable()
                        _set_bulk_action_buttons_enabled(True)
                        return

                    requested_at = datetime.now(UTC).isoformat()
                    reason = build_flatten_all_positions_reason(
                        positions_count=len(positions_snapshot)
                    )

                    try:
                        result = await trading_client.flatten_all_positions(
                            reason=reason,
                            requested_by=user_id,
                            requested_at=requested_at,
                            id_token=str(id_token),
                            user_id=user_id,
                            role=user_role,
                            strategies=user_strategies,
                        )
                        positions_closed = max(0, _coerce_int(result.get("positions_closed"), 0))
                        ui.notify(
                            f"Flattened {positions_closed} position(s)",
                            type="positive",
                        )
                        dialog.close()
                        await _emit_audit_log_safe(
                            "flatten_all_positions",
                            {
                                "positions_closed": positions_closed,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )
                        await _add_activity_item_safe(
                            {
                                "timestamp": requested_at,
                                "type": "position_update",
                                "status": "flattened",
                                "symbol": "--",
                                "message": f"Flatten-all submitted ({positions_closed} closed)",
                            },
                            event="flatten_all_positions",
                        )
                        await load_initial_data()
                    except httpx.HTTPStatusError as exc:
                        http_error = format_http_error_for_log(exc)
                        logger.warning(
                            "dashboard_flatten_all_positions_failed",
                            extra={
                                "user_id": user_id,
                                "status": exc.response.status_code,
                                "error": http_error,
                                "requested_at": requested_at,
                            },
                        )
                        await _emit_audit_log_safe(
                            "flatten_all_positions_failed",
                            {
                                "reason": reason,
                                "requested_at": requested_at,
                                **audit_http_status_details(exc),
                            },
                        )
                        ui.notify(
                            f"Failed to flatten: HTTP {exc.response.status_code}",
                            type="negative",
                        )
                    except httpx.RequestError as exc:
                        logger.warning(
                            "dashboard_flatten_all_positions_failed",
                            extra={
                                "user_id": user_id,
                                "error": type(exc).__name__,
                                "requested_at": requested_at,
                            },
                        )
                        await _emit_audit_log_safe(
                            "flatten_all_positions_failed",
                            {
                                "error": type(exc).__name__,
                                "reason": reason,
                                "requested_at": requested_at,
                            },
                        )
                        ui.notify("Failed to flatten: network error", type="negative")
                    finally:
                        bulk_action_in_progress = False
                        if flatten_dialog_open:
                            confirm_btn.enable()
                            cancel_btn.enable()
                        _set_bulk_action_buttons_enabled(True)

                confirm_btn = ui.button(
                    "Flatten All",
                    on_click=_confirm_flatten_all_positions,
                    color="red",
                ).classes("text-white")
                cancel_btn = ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    bulk_action_handlers_ready = True
    _set_bulk_action_buttons_enabled(not bulk_action_in_progress)

    async def on_position_update(data: dict[str, Any]) -> None:
        nonlocal position_symbols, positions_snapshot
        _mark_workspace_live_data()
        _evaluate_workspace_mask()
        if "total_unrealized_pl" in data:
            pnl_card.update(_coerce_float(data["total_unrealized_pl"]))
        if "total_positions" in data and "positions" not in data:
            positions_card.update(_coerce_int(data["total_positions"]))
        if "realized_pl_today" in data:
            realized_card.update(_coerce_float(data["realized_pl_today"]))
        if "buying_power" in data:
            bp_card.update(_coerce_float(data["buying_power"]))
        if "positions" in data:
            positions_snapshot = list(data["positions"])
            positions_card.update(len(positions_snapshot))
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
        state = _normalize_kill_switch_state(data.get("state", ""))
        workspace_kill_switch_state = state
        # Update cached state for instant UI responses; unknown stays None for fail-open closes
        kill_switch_engaged = _parse_kill_switch_state(state)
        _update_workspace_kill_switch_pill()
        app.storage.user["global_kill_switch_state"] = workspace_kill_switch_state
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
        app.storage.user["global_circuit_state"] = workspace_circuit_breaker_state
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
        if has_fresh_market_data:
            _mark_workspace_live_data()
        _evaluate_workspace_mask()

    market_timer = ui.timer(config.DASHBOARD_MARKET_POLL_SECONDS, update_market_data)
    timers.append(market_timer)

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
    strategy_context_refresh_enabled = should_enable_strategy_context_refresh(
        gate_enabled=config.FEATURE_STRATEGY_MODEL_EXECUTION_GATING,
        has_strategy_widget=strategy_context_widget is not None,
    )
    strategy_resolution_scope_key = _build_strategy_resolution_scope_key(
        authorized_strategy_scope
    )

    def _on_order_context_symbol_changed(selected_symbol: str | None) -> None:
        nonlocal last_strategy_context_symbol
        order_flow_panel.set_symbol(selected_symbol)
        if strategy_context_widget is not None:
            strategy_context_widget.set_symbol(selected_symbol)
        if strategy_context_refresh_enabled and selected_symbol != last_strategy_context_symbol:
            last_strategy_context_symbol = selected_symbol
            _schedule_strategy_model_context_refresh(invalidate_running=True)

    _on_order_context_symbol_changed(last_strategy_context_symbol)

    async def _resolve_strategy_for_symbol(symbol: str) -> tuple[str | None, str]:
        """Resolve symbol -> strategy mapping using fail-closed uniqueness rules."""
        if len(authorized_strategy_scope) == 1:
            return (authorized_strategy_scope[0], "single_scope")
        if not authorized_strategy_scope:
            return (None, "scope_empty")
        if async_pool is None:
            return (None, "pool_unavailable")

        try:
            normalized_symbol = validate_and_normalize_symbol(symbol)
        except ValueError:
            return (None, "invalid_symbol")
        cached_resolution = _get_strategy_resolution_from_shared_cache(
            scope_key=strategy_resolution_scope_key,
            normalized_symbol=normalized_symbol,
        )
        if cached_resolution is not None:
            return cached_resolution

        strategy_lookback_start = datetime.now(UTC) - timedelta(
            days=STRATEGY_RESOLUTION_LOOKBACK_DAYS
        )

        sql = (
            "WITH latest_strategy_orders AS ( "
            "    SELECT DISTINCT ON (strategy_id) strategy_id, created_at "
            "    FROM orders "
            "    WHERE symbol = %s AND strategy_id IS NOT NULL "
            "    AND strategy_id = ANY(%s) "
            "    AND created_at >= %s "
            "    ORDER BY strategy_id, created_at DESC "
            ") "
            "SELECT strategy_id "
            "FROM latest_strategy_orders "
            "ORDER BY created_at DESC, strategy_id ASC "
            "LIMIT 2"
        )
        params: tuple[Any, ...] = (
            normalized_symbol,
            authorized_strategy_scope,
            strategy_lookback_start,
        )

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
            return (None, "query_failed")

        if not rows:
            return _set_strategy_resolution_in_shared_cache(
                scope_key=strategy_resolution_scope_key,
                normalized_symbol=normalized_symbol,
                resolution=(None, "no_history"),
            )
        if len(rows) != 1:
            return _set_strategy_resolution_in_shared_cache(
                scope_key=strategy_resolution_scope_key,
                normalized_symbol=normalized_symbol,
                resolution=(None, "ambiguous"),
            )

        first = rows[0]
        strategy_id = first.get("strategy_id") if isinstance(first, dict) else first[0]
        if not strategy_id:
            return _set_strategy_resolution_in_shared_cache(
                scope_key=strategy_resolution_scope_key,
                normalized_symbol=normalized_symbol,
                resolution=(None, "missing_strategy"),
            )
        return _set_strategy_resolution_in_shared_cache(
            scope_key=strategy_resolution_scope_key,
            normalized_symbol=normalized_symbol,
            resolution=(str(strategy_id), "resolved"),
        )

    async def _fetch_model_registry_context(strategy_id: str) -> tuple[str, str | None]:
        """Fetch model status/version for a strategy from model_registry."""
        if async_pool is None or not config.FEATURE_MODEL_REGISTRY:
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
        _stale, data_age_s = _is_workspace_data_stale()
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
                snapshot=build_execution_context_snapshot(
                    symbol=None,
                    strategy_id=None,
                    strategy_status="unknown",
                    model_status="unknown",
                    model_version=None,
                    signal_id=None,
                    data_freshness_s=data_age_s,
                    gate_reason="Select a symbol to resolve execution context",
                    freshness_threshold_s=WORKSPACE_DATA_STALE_THRESHOLD_S,
                ),
            )
            return

        strategy_id, resolution_reason = await _resolve_strategy_for_symbol(selected_symbol)
        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return
        if not strategy_id:
            unresolved_reason = "No unique strategy mapping for selected symbol"
            unresolved_gate_enabled = gate_enabled
            unresolved_gate_reason: str | None = unresolved_reason
            unresolved_banner = (
                f"{unresolved_reason}. Risk-increasing orders may be gated."
            )
            if (
                resolution_reason == "no_history"
                and authorized_strategy_scope
                and config.FEATURE_STRATEGY_SYMBOL_MONITORING_MODE
            ):
                unresolved_reason = "No strategy history for selected symbol yet"
                unresolved_gate_enabled = False
                unresolved_gate_reason = None
                unresolved_banner = (
                    f"{unresolved_reason}. Monitoring mode enabled while history is established."
                )
            elif resolution_reason == "no_history" and authorized_strategy_scope:
                unresolved_reason = "No strategy history for selected symbol yet"
                unresolved_gate_reason = (
                    "No symbol ownership history available; execution remains gated"
                )
                unresolved_banner = (
                    f"{unresolved_reason}. Execution remains gated until symbol ownership is established."
                )
            order_context.dispatch_strategy_model_context(
                strategy_status="unknown",
                model_status="unknown",
                gate_enabled=unresolved_gate_enabled,
                gate_reason=unresolved_gate_reason,
                strategy_label="Strategy: unresolved",
                model_label="Model: unresolved",
                banner=unresolved_banner,
                snapshot=build_execution_context_snapshot(
                    symbol=selected_symbol,
                    strategy_id=None,
                    strategy_status="unknown",
                    model_status="unknown",
                    model_version=None,
                    signal_id=None,
                    data_freshness_s=data_age_s,
                    gate_reason=unresolved_gate_reason,
                    freshness_threshold_s=WORKSPACE_DATA_STALE_THRESHOLD_S,
                ),
            )
            return

        strategy_status = "unknown"
        model_status = "unknown"
        model_version: str | None = None
        signal_id: str | None = None
        reason_parts: list[str] = []
        db_model_context_reason: str | None = None
        payload_has_model_context = False

        async def _fetch_strategy_payload() -> dict[str, Any]:
            return await trading_client.fetch_strategy_status(
                strategy_id,
                user_id=user_id,
                role=user_role,
                strategies=user_strategies,
            )

        strategy_payload_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(
            _fetch_strategy_payload()
        )
        db_model_context_task: asyncio.Task[tuple[str, str | None]] = asyncio.create_task(
            _fetch_model_registry_context(strategy_id)
        )

        strategy_payload_result: dict[str, Any] | BaseException
        db_model_context_result: tuple[str, str | None] | BaseException
        try:
            (
                strategy_payload_result,
                db_model_context_result,
            ) = await asyncio.gather(
                strategy_payload_task,
                db_model_context_task,
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            strategy_payload_task.cancel()
            db_model_context_task.cancel()
            await asyncio.gather(
                strategy_payload_task,
                db_model_context_task,
                return_exceptions=True,
            )
            raise

        strategy_payload: dict[str, Any] | None = None
        if isinstance(strategy_payload_result, BaseException):
            if isinstance(strategy_payload_result, asyncio.CancelledError):
                raise strategy_payload_result
            reason_parts.append(
                "strategy status unavailable "
                f"({type(strategy_payload_result).__name__})"
            )
        else:
            strategy_payload = strategy_payload_result

        db_model_status: str = "unknown"
        db_model_version: str | None = None
        if isinstance(db_model_context_result, BaseException):
            if isinstance(db_model_context_result, asyncio.CancelledError):
                raise db_model_context_result
            db_model_context_reason = (
                "model registry context unavailable "
                f"({type(db_model_context_result).__name__})"
            )
        else:
            db_model_status, db_model_version = db_model_context_result

        if _is_strategy_context_refresh_stale(refresh_generation, selected_symbol):
            return

        if strategy_payload is not None:
            strategy_status = normalize_execution_status(strategy_payload.get("status"))
            payload_model_status = strategy_payload.get("model_status")
            if payload_model_status:
                model_status = normalize_execution_status(payload_model_status)
            payload_model_version = strategy_payload.get("model_version")
            if payload_model_version:
                model_version = str(payload_model_version).strip()
            payload_has_model_context = bool(payload_model_status or payload_model_version)
            payload_signal_id = strategy_payload.get("signal_id")
            if payload_signal_id:
                signal_id = str(payload_signal_id).strip()

        if model_status == "unknown":
            model_status = db_model_status
        if model_version is None:
            model_version = db_model_version

        model_status, model_version, enforce_model_gate = resolve_model_gate_inputs(
            model_status=model_status,
            model_version=model_version,
            feature_model_registry_enabled=config.FEATURE_MODEL_REGISTRY,
        )
        if db_model_context_reason:
            model_context_required = strategy_payload is None or (
                enforce_model_gate and not payload_has_model_context
            )
            if model_context_required:
                reason_parts.append(db_model_context_reason)
        strategy_safe = is_strategy_execution_safe(strategy_status)
        model_safe = is_model_execution_safe(model_status)

        gate_reason: str | None = None
        if not strategy_safe:
            gate_reason = f"strategy is {strategy_status.upper()}"
        elif enforce_model_gate and not model_safe:
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
            snapshot=build_execution_context_snapshot(
                symbol=selected_symbol,
                strategy_id=strategy_id,
                strategy_status=strategy_status,
                model_status=model_status,
                model_version=model_version,
                signal_id=signal_id,
                data_freshness_s=data_age_s,
                gate_reason=gate_reason,
                freshness_threshold_s=WORKSPACE_DATA_STALE_THRESHOLD_S,
            ),
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

    order_context.register_symbol_change_callback(_on_order_context_symbol_changed)

    if strategy_context_refresh_enabled:
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

    async def cleanup_background_tasks() -> None:
        if not background_tasks:
            return
        pending = list(background_tasks)
        for task in pending:
            task.cancel()
        background_tasks.clear()
        await asyncio.gather(*pending, return_exceptions=True)

    await lifecycle.register_cleanup_callback(client_id, cleanup_timers)
    await lifecycle.register_cleanup_callback(client_id, cleanup_strategy_context_task)
    await lifecycle.register_cleanup_callback(client_id, cleanup_background_tasks)
    await lifecycle.register_cleanup_callback(client_id, realtime.cleanup)

    # Register cleanup for OrderEntryContext before initialization so disconnects
    # during startup still run teardown.
    async def cleanup_order_context() -> None:
        try:
            active_generation_id = client.storage.get("active_order_context_generation_id")
            is_handoff = active_generation_id != order_context_generation_id
            deferred_release_symbols = await order_context.dispose(
                release_market_data_symbols=not is_handoff
            )
            if not is_handoff:
                if client.storage.get("active_order_context_ref") is order_context:
                    client.storage.pop("active_order_context_ref", None)
                    client.storage.pop("active_order_context_generation_id", None)
            elif deferred_release_symbols:
                latest_generation_id = client.storage.get("active_order_context_generation_id")
                latest_context_ref = client.storage.get("active_order_context_ref")
                latest_active_context = (
                    latest_context_ref
                    if isinstance(latest_context_ref, OrderEntryContext)
                    else None
                )
                can_adopt_into_latest_context = (
                    latest_generation_id != order_context_generation_id
                    and latest_active_context is not None
                    and latest_active_context is not order_context
                    and not getattr(latest_active_context, "_disposed", False)
                )
                if can_adopt_into_latest_context:
                    assert latest_active_context is not None
                    try:
                        await latest_active_context.adopt_deferred_market_data_releases(
                            sorted(deferred_release_symbols)
                        )
                        return
                    except Exception:
                        logger.debug(
                            "deferred_market_data_adoption_failed; persisting for retry",
                            extra={"client_id": client_id},
                            exc_info=True,
                        )
                deferred_symbols_raw = client.storage.get("deferred_market_data_release_symbols", [])
                existing_symbols = (
                    [symbol for symbol in deferred_symbols_raw if isinstance(symbol, str)]
                    if isinstance(deferred_symbols_raw, list)
                    else []
                )
                client.storage["deferred_market_data_release_symbols"] = sorted(
                    set(existing_symbols).union(deferred_release_symbols)
                )
        except Exception as exc:
            logger.warning(
                "order_context_dispose_failed",
                extra={"client_id": client_id, "error": str(exc)},
            )

    await lifecycle.register_cleanup_callback(client_id, cleanup_order_context)

    # Initialize OrderEntryContext AFTER UI creation (per spec lifecycle pattern)
    # This starts timers, loads data, and establishes subscriptions
    try:
        await order_context.initialize()
        client.storage["active_order_context_ref"] = order_context
        deferred_symbols_raw = client.storage.pop("deferred_market_data_release_symbols", [])
        deferred_symbols = (
            [symbol for symbol in deferred_symbols_raw if isinstance(symbol, str)]
            if isinstance(deferred_symbols_raw, list)
            else []
        )
        if deferred_symbols:
            await order_context.adopt_deferred_market_data_releases(deferred_symbols)
    except Exception as exc:
        logger.error(
            "order_context_init_failed",
            extra={"client_id": client_id, "error": str(exc)},
        )
        ui.notify(
            "Order entry initialization failed - some features may be unavailable",
            type="warning",
        )


@ui.page("/trade")
@requires_auth
async def dashboard_trade_alias() -> None:
    """Legacy alias route for canonical trade workspace."""
    render_client_redirect(
        _build_trade_alias_redirect_target(ui_module=ui),
        ui_module=ui,
        message="Redirecting to Trade workspace...",
    )


def _build_trade_alias_redirect_target(*, ui_module: Any) -> str:
    """Build canonical trade redirect target while preserving safe query params."""
    target = resolve_rooted_path_from_ui("/", ui_module=ui_module)
    try:
        request = ui_module.context.client.request
    except (AttributeError, RuntimeError) as exc:
        logger.debug(
            "trade_alias_request_unavailable",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return target
    if request is None:
        return target

    query_params = getattr(request, "query_params", None)
    if query_params is None:
        return target
    multi_items = getattr(query_params, "multi_items", None)
    if callable(multi_items):
        query_items = list(multi_items())
    else:
        items = getattr(query_params, "items", None)
        query_items = list(items()) if callable(items) else []
    safe_items = [(key, value) for key, value in query_items if key in TRADE_REDIRECT_QUERY_KEYS]
    if not safe_items:
        return target
    return f"{target}?{urlencode(safe_items, doseq=True)}"


__all__ = ["dashboard", "dashboard_trade_alias", "MarketPriceCache"]
