"""Main trading dashboard with real-time metric cards."""

from __future__ import annotations

import asyncio
import json
import logging
import time
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
from apps.web_console_ng.core.realtime import (
    RealtimeUpdater,
    circuit_breaker_channel,
    fills_channel,
    kill_switch_channel,
    orders_channel,
    position_channel,
)
from apps.web_console_ng.ui.layout import main_layout

logger = logging.getLogger(__name__)


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
    """Shared cache for market prices across all client sessions.

    Assumes market prices are not user-specific.
    """

    _prices: list[dict[str, Any]] = []
    _last_fetch: float = 0.0
    _last_error: float = 0.0
    _ttl: float = 4.0
    _error_cooldown: float = 10.0
    _lock = asyncio.Lock()
    _in_flight: asyncio.Task[list[dict[str, Any]]] | None = None

    @classmethod
    async def get_prices(
        cls,
        client: AsyncTradingClient,
        *,
        user_id: str,
        role: str | None,
        strategies: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Get market prices, using cache if fresh.

        Returns a copy to prevent cross-session mutation.
        Implements failure backoff to avoid thundering herd on outages.
        """
        async with cls._lock:
            now = time.time()
            if cls._last_error and (now - cls._last_error) < cls._error_cooldown:
                return [dict(p) for p in cls._prices]

            if (now - cls._last_fetch) < cls._ttl and cls._prices:
                return [dict(p) for p in cls._prices]

            if cls._in_flight is not None:
                task = cls._in_flight
            else:
                fetch_prices = getattr(client, "fetch_market_prices", None)
                if not callable(fetch_prices):
                    logger.warning("market_prices_fetch_missing", extra={"reason": "method_missing"})
                    return [dict(p) for p in cls._prices]
                cls._in_flight = asyncio.create_task(
                    fetch_prices(
                        user_id,
                        role=role,
                        strategies=strategies,
                    )
                )
                task = cls._in_flight

        try:
            prices = await task
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "market_price_cache_fetch_failed",
                extra={"error": type(exc).__name__, "detail": str(exc)[:100]},
            )
            async with cls._lock:
                cls._last_error = time.time()
                if cls._in_flight is task:
                    cls._in_flight = None
                return [dict(p) for p in cls._prices]
        except Exception as exc:
            # Catch non-HTTP exceptions (e.g., ValueError from malformed payload)
            # to reset _in_flight and allow recovery on next call
            logger.warning(
                "market_price_cache_fetch_unexpected_error",
                extra={"error": type(exc).__name__, "detail": str(exc)[:100]},
            )
            async with cls._lock:
                cls._last_error = time.time()
                if cls._in_flight is task:
                    cls._in_flight = None
                return [dict(p) for p in cls._prices]

        async with cls._lock:
            if cls._in_flight is task:
                cls._in_flight = None
            cls._prices = list(prices)
            cls._last_fetch = time.time()
            cls._last_error = 0.0
            return [dict(p) for p in cls._prices]


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
        logger.warning("dashboard_missing_user_id", extra={"client_id": client.storage.get("client_id")})
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

    def _coerce_float(value: Any, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
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
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value.replace(",", "").strip()))
            except ValueError:
                return default
        return default

    # Metric cards
    with ui.row().classes("w-full gap-4 mb-6 flex-wrap"):
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

    # Positions grid
    with ui.card().classes("w-full mb-6"):
        ui.label("Positions").classes("text-lg font-bold mb-2")
        positions_grid = create_positions_grid()

    # Orders + activity
    with ui.row().classes("w-full gap-4"):
        with ui.card().classes("flex-1"):
            ui.label("Open Orders").classes("text-lg font-bold mb-2")
            orders_table = create_orders_table()

        with ui.card().classes("w-80"):
            activity_feed = ActivityFeed()

    position_symbols: set[str] | None = None
    order_ids: set[str] | None = None
    notified_missing_ids: set[str] = set()
    notified_malformed: set[int] = set()  # Dedupe malformed position notifications
    synthetic_id_map: dict[str, str] = {}
    synthetic_id_miss_counts: dict[str, int] = {}  # Prevent churn from transient snapshot gaps
    grid_update_lock = asyncio.Lock()
    kill_switch_engaged: bool | None = None  # Real-time cached state for instant UI response

    async def load_initial_data() -> None:
        nonlocal position_symbols, order_ids
        try:
            pnl_data, positions, orders, account_info = await asyncio.gather(
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

    await load_initial_data()

    async def check_initial_kill_switch() -> None:
        """Fetch initial kill switch status on page load."""
        nonlocal kill_switch_engaged
        try:
            ks_status = await trading_client.fetch_kill_switch_status(user_id, role=user_role)
            state = str(ks_status.get("state", "")).upper()
            # Fail-closed: only mark as safe if explicitly DISENGAGED
            kill_switch_engaged = state != "DISENGAGED"
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
            await activity_feed.add_item(data["event"])

    def _extract_event_detail(args: Any) -> dict[str, Any]:
        if isinstance(args, dict):
            return args
        if isinstance(args, list) and args and isinstance(args[0], dict):
            return args[0]
        return {}

    async def on_kill_switch_update(data: dict[str, Any]) -> None:
        nonlocal kill_switch_engaged
        logger.info("kill_switch_update", extra={"client_id": client_id, "data": data})
        state = str(data.get("state", "")).upper()
        # Update cached state for instant UI responses (fail-closed: treat unknown as engaged)
        kill_switch_engaged = state != "DISENGAGED"
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
        await activity_feed.add_item(data)

    async def handle_close_position(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        symbol = str(detail.get("symbol", "")).strip()
        qty = detail.get("qty", 0)
        if not symbol:
            ui.notify("Cannot close position: missing symbol", type="negative")
            return
        # Pass cached kill switch state for instant UI response
        await on_close_position(
            symbol, qty, user_id, user_role, kill_switch_engaged=kill_switch_engaged
        )

    async def handle_cancel_order(event: events.GenericEventArguments) -> None:
        detail = _extract_event_detail(event.args)
        order_id = str(detail.get("client_order_id", "")).strip()
        symbol = str(detail.get("symbol", "")).strip() or "unknown"
        broker_order_id = str(detail.get("broker_order_id", "")).strip() or None
        await on_cancel_order(order_id, symbol, user_id, user_role, broker_order_id=broker_order_id)

    ui.on("close_position", handle_close_position, args=["detail"])
    ui.on("cancel_order", handle_cancel_order, args=["detail"])

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


__all__ = ["dashboard", "MarketPriceCache"]
