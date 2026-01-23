"""Main trading dashboard with real-time metric cards."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from nicegui import Client, events, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.activity_feed import ActivityFeed
from apps.web_console_ng.components.metric_card import MetricCard
from apps.web_console_ng.components.orders_table import (
    create_orders_table,
    on_cancel_order,
    update_orders_table,
)
from apps.web_console_ng.components.positions_grid import (
    create_positions_grid,
    on_close_position,
    update_positions_grid,
)
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.components.order_entry_context import OrderEntryContext
from apps.web_console_ng.core.connection_monitor import ConnectionMonitor
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.core.realtime import (
    RealtimeUpdater,
    circuit_breaker_channel,
    fills_channel,
    kill_switch_channel,
    orders_channel,
    position_channel,
)
from apps.web_console_ng.core.state_manager import UserStateManager
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.ui.trading_layout import compact_card, trading_grid
from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess

logger = logging.getLogger(__name__)

ScopeKey = tuple[str, frozenset[str]]


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
        """Get market prices, using per-scope cache if fresh.

        Returns a copy to prevent cross-session mutation.
        Implements failure backoff to avoid thundering herd on outages.
        Cache is scoped by user's strategy permissions to prevent authorization leaks.
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
                return [dict(p) for p in cached_prices]

            # Return cached data if still fresh
            if (now - last_fetch) < cls._ttl and cached_prices:
                return [dict(p) for p in cached_prices]

            # Check for in-flight request for this scope
            if scope_key in cls._in_flight:
                task = cls._in_flight[scope_key]
            else:
                fetch_prices = getattr(client, "fetch_market_prices", None)
                if not callable(fetch_prices):
                    logger.warning(
                        "market_prices_fetch_missing", extra={"reason": "method_missing"}
                    )
                    return [dict(p) for p in cached_prices]
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
                return [dict(p) for p in cls._cache[scope_key].get("prices", [])]
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
                return [dict(p) for p in cls._cache[scope_key].get("prices", [])]

        async with cls._lock:
            if cls._in_flight.get(scope_key) is task:
                del cls._in_flight[scope_key]
            cls._cache[scope_key] = {
                "prices": list(prices),
                "last_fetch": time.time(),
                "last_error": 0.0,
            }
            return [dict(p) for p in cls._cache[scope_key]["prices"]]


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

    # Metric cards
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

    # Order Entry Section - Responsive 3-column layout
    # Desktop: [Watchlist] [Chart] [Market Context + Order Ticket]
    # Mobile: Single column stacked
    with ui.element("div").classes(
        "grid gap-4 w-full mb-4 "
        "grid-cols-1 md:grid-cols-2 lg:grid-cols-[250px_1fr_350px]"
    ):
        # Left column: Watchlist (hidden on mobile)
        with ui.column().classes("hidden md:flex"):
            order_context.create_watchlist()

        # Middle column: Price Chart
        with ui.column().classes("min-h-[200px]"):
            order_context.create_price_chart(width=600, height=300)

        # Right column: Market Context + Order Ticket
        with ui.column().classes("gap-4"):
            order_context.create_market_context()
            order_context.create_order_ticket()

    # Positions grid
    with compact_card("Positions").classes("w-full"):
        positions_grid = create_positions_grid()

    # Orders + activity
    with trading_grid().classes("w-full"):
        with compact_card("Open Orders").classes("w-full"):
            orders_table = create_orders_table()

        with compact_card("Activity").classes("w-full"):
            last_sync_label = ui.label("Last sync: --").classes("text-xs text-gray-500 mb-2")
            activity_feed = ActivityFeed()

    position_symbols: set[str] | None = None
    order_ids: set[str] | None = None
    notified_missing_ids: set[str] = set()
    notified_malformed: set[int] = set()  # Dedupe malformed position notifications
    notified_filter_restores: set[str] = set()  # Dedupe filter restore toasts per grid
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}  # Prevent churn from transient snapshot gaps
    grid_update_lock = asyncio.Lock()
    kill_switch_engaged: bool | None = None  # Real-time cached state for instant UI response

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
        nonlocal position_symbols, order_ids
        try:
            async_pool = get_db_pool()
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
        async with grid_update_lock:
            position_symbols = await update_positions_grid(
                positions_grid,
                positions.get("positions", []),
                position_symbols,
                notified_malformed=notified_malformed,
            )
            order_ids = await update_orders_table(
                orders_table,
                orders.get("orders", []),
                order_ids,
                notified_missing_ids=notified_missing_ids,
                synthetic_id_map=synthetic_id_map,
                synthetic_id_miss_counts=synthetic_id_miss_counts,
                user_id=user_id,
                client_id=client_id,
            )
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
        nonlocal kill_switch_engaged
        try:
            ks_status = await trading_client.fetch_kill_switch_status(
                user_id,
                role=user_role,
                strategies=user_strategies,
            )
            kill_switch_engaged = _parse_kill_switch_state(ks_status.get("state"))
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "kill_switch_initial_check_failed",
                extra={"user_id": user_id, "error": type(exc).__name__},
            )
            # Use None (unknown) on API failure to preserve fail-open path in on_close_position
            # This allows risk-reducing closes during kill switch service outages
            kill_switch_engaged = None

    await check_initial_kill_switch()

    async def on_position_update(data: dict[str, Any]) -> None:
        nonlocal position_symbols
        if "total_unrealized_pl" in data:
            pnl_card.update(_coerce_float(data["total_unrealized_pl"]))
        if "total_positions" in data:
            positions_card.update(_coerce_int(data["total_positions"]))
        if "realized_pl_today" in data:
            realized_card.update(_coerce_float(data["realized_pl_today"]))
        if "buying_power" in data:
            bp_card.update(_coerce_float(data["buying_power"]))
        if "positions" in data:
            async with grid_update_lock:
                position_symbols = await update_positions_grid(
                    positions_grid,
                    data["positions"],
                    position_symbols,
                    notified_malformed=notified_malformed,
                )
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
        nonlocal kill_switch_engaged
        logger.info("kill_switch_update", extra={"client_id": client_id, "data": data})
        state = str(data.get("state", "")).upper()
        # Update cached state for instant UI responses; unknown stays None for fail-open closes
        kill_switch_engaged = _parse_kill_switch_state(state)
        dispatch_trading_state_event(client_id, {"killSwitchState": state})

    async def on_circuit_breaker_update(data: dict[str, Any]) -> None:
        logger.info("circuit_breaker_update", extra={"client_id": client_id, "data": data})
        state = str(data.get("state", "")).upper()
        dispatch_trading_state_event(client_id, {"circuitBreakerState": state})

    async def on_orders_update(data: dict[str, Any]) -> None:
        nonlocal order_ids
        if "orders" in data:
            async with grid_update_lock:
                order_ids = await update_orders_table(
                    orders_table,
                    data["orders"],
                    order_ids,
                    notified_missing_ids=notified_missing_ids,
                    synthetic_id_map=synthetic_id_map,
                    synthetic_id_miss_counts=synthetic_id_miss_counts,
                    user_id=user_id,
                    client_id=client_id,
                )

    async def on_fill_event(data: dict[str, Any]) -> None:
        normalized = _format_event_time(dict(data))
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

    ui.on("close_position", handle_close_position, args=["detail"])
    ui.on("cancel_order", handle_cancel_order, args=["detail"])
    ui.on("grid_filters_restored", handle_grid_filters_restored, args=["detail"])

    await realtime.subscribe(position_channel(user_id), on_position_update)
    await realtime.subscribe(kill_switch_channel(), on_kill_switch_update)
    await realtime.subscribe(circuit_breaker_channel(), on_circuit_breaker_update)
    await realtime.subscribe(orders_channel(user_id), on_orders_update)
    await realtime.subscribe(fills_channel(user_id), on_fill_event)

    async def update_market_data() -> None:
        _ = await MarketPriceCache.get_prices(
            trading_client,
            user_id=user_id,
            role=user_role,
            strategies=user_strategies,
        )

    market_timer = ui.timer(config.DASHBOARD_MARKET_POLL_SECONDS, update_market_data)
    timers.append(market_timer)

    async def check_stale_data() -> None:
        for card in [pnl_card, positions_card, realized_card, bp_card]:
            if card.is_stale(threshold=30.0):
                card.mark_stale()

    stale_timer = ui.timer(config.DASHBOARD_STALE_CHECK_SECONDS, check_stale_data)
    timers.append(stale_timer)

    def cleanup_timers() -> None:
        for timer in timers:
            timer.cancel()

    await lifecycle.register_cleanup_callback(client_id, cleanup_timers)
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
