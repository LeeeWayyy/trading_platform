"""Central orchestrator for order entry components.

SUBSCRIPTION OWNERSHIP MODEL:

OrderEntryContext is the SINGLE OWNER of all real-time subscriptions.
Child components (OrderTicket, MarketContext, Watchlist, PriceChart) do NOT
subscribe directly to Redis channels. Instead:

1. OrderEntryContext subscribes to all required channels
2. OrderEntryContext dispatches updates to components via typed callbacks
3. Components expose setter methods for receiving state updates
4. OrderEntryContext tracks ALL subscriptions and timers for cleanup

This avoids:
- Duplicate subscriptions to the same channel
- Lifecycle management confusion
- Memory leaks from orphaned subscriptions
- Race conditions on page exit

Data Flow:
  Redis Pub/Sub -> RealtimeUpdater -> OrderEntryContext -> Component callbacks
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from nicegui import ui

from apps.web_console_ng.utils.time import (
    parse_iso_timestamp,
    validate_and_normalize_symbol,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from redis.asyncio import Redis as AsyncRedis

    from apps.web_console_ng.components.market_context import MarketContextComponent
    from apps.web_console_ng.components.order_ticket import OrderTicketComponent
    from apps.web_console_ng.components.price_chart import PriceChartComponent
    from apps.web_console_ng.components.watchlist import WatchlistComponent
    from apps.web_console_ng.core.client import AsyncTradingClient
    from apps.web_console_ng.core.connection_monitor import ConnectionMonitor
    from apps.web_console_ng.core.realtime import RealtimeUpdater
    from apps.web_console_ng.core.state_manager import UserStateManager

logger = logging.getLogger(__name__)

# Connection states that disable trading
READ_ONLY_CONNECTION_STATES = {"DISCONNECTED", "RECONNECTING", "DEGRADED"}
# Only "CONNECTED" state allows trading
READ_WRITE_CONNECTION_STATES = {"CONNECTED"}
# Connection states that indicate true disconnect (require resubscribe on recovery)
# DEGRADED is read-only but connection is still alive - no resubscribe needed
DISCONNECTED_STATES = {"DISCONNECTED", "RECONNECTING"}


class OrderEntryContext:
    """Coordinates all order entry components with proper lifecycle management.

    SUBSCRIPTION OWNERSHIP MODEL:

    Multiple owners (watchlist, selected_symbol) can subscribe to the same channel.
    We track owners per channel and only unsubscribe when all owners release it.
    This prevents:
    - Double subscriptions (we subscribe only once, track multiple owners)
    - Premature unsubscribe (we only unsubscribe when no owners remain)
    """

    # Owner constants
    OWNER_SELECTED_SYMBOL = "selected_symbol"
    OWNER_WATCHLIST = "watchlist"
    OWNER_POSITIONS = "positions"
    OWNER_KILL_SWITCH = "kill_switch"
    OWNER_CIRCUIT_BREAKER = "circuit_breaker"
    OWNER_CONNECTION = "connection"

    def __init__(
        self,
        realtime_updater: RealtimeUpdater,
        trading_client: AsyncTradingClient,
        state_manager: UserStateManager,
        connection_monitor: ConnectionMonitor,
        redis: AsyncRedis,
        user_id: str,
        role: str,
        strategies: list[str],
    ) -> None:
        """Initialize OrderEntryContext.

        Args:
            realtime_updater: Redis pub/sub wrapper for real-time updates.
            trading_client: HTTP client for API calls.
            state_manager: Redis state manager for form recovery.
            connection_monitor: Connection health monitor.
            redis: Async Redis client for authoritative safety state checks.
            user_id: User ID for API calls and subscriptions.
            role: User role for authorization.
            strategies: Strategies for position filtering.
        """
        self._realtime = realtime_updater
        self._client = trading_client
        self._state_manager = state_manager
        self._connection_monitor = connection_monitor
        self._redis = redis
        self._user_id = user_id
        self._role = role
        self._strategies = strategies

        # Subscription tracking
        self._subscriptions: list[str] = []
        self._timers: list[ui.timer] = []
        self._disposed: bool = False

        # SUBSCRIPTION OWNERSHIP TRACKING (refcount per channel)
        self._channel_owners: dict[str, set[str]] = {}
        self._subscription_lock = asyncio.Lock()
        # Pending subscribe futures (concurrent acquirers await the same future)
        self._pending_subscribes: dict[str, asyncio.Future[None]] = {}
        # Channel -> callback registry for reconnect resubscription
        self._channel_callbacks: dict[str, Callable[[dict[str, Any]], Any]] = {}
        # Failed subscriptions to retry on reconnect
        self._failed_subscriptions: dict[str, tuple[set[str], Callable[..., Any]]] = {}

        # Track current selected channel for unsubscribe
        self._current_selected_channel: str | None = None

        # Child components (set externally via set_* methods)
        self._order_ticket: OrderTicketComponent | None = None
        self._market_context: MarketContextComponent | None = None
        self._watchlist: WatchlistComponent | None = None
        self._price_chart: PriceChartComponent | None = None

        # Current selected symbol (shared state)
        self._selected_symbol: str | None = None

        # SYMBOL SELECTION VERSION (race prevention)
        self._selection_version: int = 0

        # Connection state tracking for reconnect detection
        self._last_connection_state: str | None = None

    # =========================================================================
    # Component Setters
    # =========================================================================

    def set_order_ticket(self, component: OrderTicketComponent) -> None:
        """Set the OrderTicket component reference."""
        self._order_ticket = component

    def set_market_context(self, component: MarketContextComponent) -> None:
        """Set the MarketContext component reference."""
        self._market_context = component

    def set_watchlist(self, component: WatchlistComponent) -> None:
        """Set the Watchlist component reference."""
        self._watchlist = component

    def set_price_chart(self, component: PriceChartComponent) -> None:
        """Set the PriceChart component reference."""
        self._price_chart = component

    # =========================================================================
    # Component Factory Methods
    # =========================================================================

    def create_watchlist(self) -> Any:
        """Create and configure the Watchlist component.

        Creates the component, wires up callbacks, stores reference, and returns
        the UI card for embedding in the dashboard layout.

        Returns:
            The Watchlist UI card (nicegui element).
        """
        from apps.web_console_ng.components.watchlist import WatchlistComponent

        self._watchlist = WatchlistComponent(
            trading_client=self._client,
            on_symbol_selected=self.on_symbol_selected,
            on_subscribe_symbol=self.on_watchlist_subscribe_request,
            on_unsubscribe_symbol=self.on_watchlist_unsubscribe_request,
        )
        return self._watchlist.create()

    def create_market_context(self) -> Any:
        """Create and configure the MarketContext component.

        Creates the component, wires up callbacks, stores reference, and returns
        the UI card for embedding in the dashboard layout.

        Returns:
            The MarketContext UI card (nicegui element).
        """
        from apps.web_console_ng.components.market_context import MarketContextComponent

        self._market_context = MarketContextComponent(
            trading_client=self._client,
            on_price_updated=self._on_market_context_price_updated,
        )
        return self._market_context.create()

    def create_price_chart(self, width: int = 600, height: int = 300) -> Any:
        """Create and configure the PriceChart component.

        Creates the component, stores reference, and returns the UI element
        for embedding in the dashboard layout.

        Args:
            width: Chart width in pixels.
            height: Chart height in pixels.

        Returns:
            The PriceChart UI element (nicegui element).
        """
        from apps.web_console_ng.components.price_chart import PriceChartComponent

        self._price_chart = PriceChartComponent(
            trading_client=self._client,
        )
        return self._price_chart.create(width=width, height=height)

    def create_order_ticket(self) -> Any:
        """Create and configure the OrderTicket component.

        Creates the component, wires up safety verification callbacks,
        stores reference, and returns the UI card for embedding.

        Returns:
            The OrderTicket UI card (nicegui element).
        """
        from apps.web_console_ng.components.order_ticket import OrderTicketComponent

        self._order_ticket = OrderTicketComponent(
            trading_client=self._client,
            state_manager=self._state_manager,
            connection_monitor=self._connection_monitor,
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
            on_symbol_selected=self.on_symbol_selected,
            verify_circuit_breaker=self.get_verify_circuit_breaker(),
            verify_kill_switch=self.get_verify_kill_switch(),
        )
        return self._order_ticket.create()

    async def _on_market_context_price_updated(
        self, symbol: str, price: Decimal | None, timestamp: datetime | None
    ) -> None:
        """Handle price update from MarketContext to forward to OrderTicket.

        This bridges the MarketContext callback to the OrderTicket's price data.
        """
        if self._order_ticket and symbol == self._selected_symbol:
            self._order_ticket.set_price_data(symbol, price, timestamp)

    # =========================================================================
    # Initialization
    # =========================================================================

    async def initialize(self) -> None:
        """Initialize all components and subscriptions.

        FAIL-CLOSED: If any critical subscription or safety fetch fails, the order
        ticket is explicitly disabled with a clear reason.

        Raises:
            RuntimeError: If called on a disposed context.
        """
        if self._disposed:
            raise RuntimeError("Cannot initialize disposed OrderEntryContext")

        try:
            # Initialize child components with timer tracker for lifecycle management
            if self._watchlist:
                await self._watchlist.initialize(timer_tracker=self._track_timer)
            if self._order_ticket:
                await self._order_ticket.initialize(timer_tracker=self._track_timer)
            if self._market_context:
                await self._market_context.initialize(timer_tracker=self._track_timer)
            if self._price_chart:
                await self._price_chart.initialize(timer_tracker=self._track_timer)

            # Subscribe to GLOBAL channels
            await self._subscribe_to_kill_switch_channel()
            await self._subscribe_to_circuit_breaker_channel()
            await self._subscribe_to_connection_channel()
            await self._subscribe_to_positions_channel()

            # CRITICAL: Fetch initial state for safety mechanisms (fail-closed)
            await self._fetch_initial_safety_state()

        except Exception as exc:
            # FAIL-CLOSED: Explicitly disable order ticket on init failure
            logger.error(f"OrderEntryContext initialization failed: {exc}")

            # CLEANUP: Cancel any timers registered before failure
            for timer in self._timers:
                try:
                    timer.cancel()
                except Exception as timer_exc:
                    logger.debug(f"Timer cleanup during init failure: {timer_exc}")
            self._timers.clear()

            # CLEANUP: Release any subscriptions acquired before failure
            async with self._subscription_lock:
                channels_to_cleanup = list(self._subscriptions)
                self._channel_owners.clear()
                self._channel_callbacks.clear()
                self._pending_subscribes.clear()
                self._subscriptions.clear()

            for channel in channels_to_cleanup:
                try:
                    await self._realtime.unsubscribe(channel)
                except Exception as unsub_exc:
                    logger.debug(f"Unsubscribe cleanup during init failure: {unsub_exc}")

            if self._order_ticket:
                self._order_ticket.set_circuit_breaker_state(
                    True, "Initialization failed - please refresh"
                )
                self._order_ticket.set_kill_switch_state(
                    True, "Initialization failed - please refresh"
                )
            raise

    # =========================================================================
    # Safety State Management
    # =========================================================================

    async def _fetch_initial_safety_state(self) -> None:
        """Fetch initial state for kill switch and circuit breaker.

        CRITICAL: Pub/sub only delivers updates AFTER subscription. If the
        circuit breaker is already TRIPPED when we subscribe, we'd never know.
        This fetch ensures we start in the correct state.

        FAIL-CLOSED: If fetch fails or times out, we default to disabled (safe mode).
        """
        try:
            # Fetch circuit breaker state from Redis (authoritative source)
            cb_raw = await asyncio.wait_for(
                self._redis.get("circuit_breaker:state"),
                timeout=2.0,
            )

            cb_tripped = True  # Fail-closed default
            cb_reason = "Initial state: Unknown/missing"
            if cb_raw:
                try:
                    cb_data = json.loads(cb_raw)
                    cb_state = str(cb_data.get("state", "")).upper()

                    reset_at = cb_data.get("reset_at")
                    tripped_at = cb_data.get("tripped_at")
                    timestamp_valid = False

                    if cb_state == "OPEN":
                        if reset_at:
                            try:
                                parse_iso_timestamp(str(reset_at))
                                timestamp_valid = True
                            except (ValueError, TypeError):
                                logger.warning(f"Invalid circuit breaker reset_at: {reset_at!r}")
                        elif tripped_at is None:
                            timestamp_valid = True
                        else:
                            logger.warning("Circuit breaker has tripped_at but no reset_at")

                        if timestamp_valid:
                            cb_tripped = False
                            cb_reason = None  # type: ignore[assignment]
                        else:
                            cb_reason = "Initial state: OPEN but missing/invalid timestamp"
                    elif cb_state == "TRIPPED":
                        cb_reason = cb_data.get("trip_reason", "Initial state: TRIPPED")
                    elif cb_state == "QUIET_PERIOD":
                        cb_reason = "Initial state: QUIET_PERIOD (transitional)"
                    else:
                        cb_reason = f"Initial state: Unknown ({cb_state!r})"
                except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                    logger.warning(f"Invalid circuit breaker JSON: {exc}")
                    cb_reason = "Initial state: Invalid data"

            if self._order_ticket:
                self._order_ticket.set_circuit_breaker_state(cb_tripped, cb_reason)

            # Fetch kill switch state from Redis
            ks_raw = await asyncio.wait_for(
                self._redis.get("kill_switch:state"),
                timeout=2.0,
            )

            ks_engaged = True  # Fail-closed default
            ks_reason = "Initial state: Unknown/missing"
            if ks_raw:
                try:
                    ks_data = json.loads(ks_raw)
                    ks_state = str(ks_data.get("state", "")).upper()

                    disengaged_at = ks_data.get("disengaged_at")
                    engaged_at = ks_data.get("engaged_at")
                    timestamp_valid = False

                    if ks_state == "ACTIVE":
                        if disengaged_at:
                            try:
                                parse_iso_timestamp(str(disengaged_at))
                                timestamp_valid = True
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Invalid kill switch disengaged_at: {disengaged_at!r}"
                                )
                        elif engaged_at is None:
                            timestamp_valid = True
                        else:
                            logger.warning("Kill switch has engaged_at but no disengaged_at")

                        if timestamp_valid:
                            ks_engaged = False
                            ks_reason = None  # type: ignore[assignment]
                        else:
                            ks_reason = "Initial state: ACTIVE but missing/invalid timestamp"
                    elif ks_state == "ENGAGED":
                        ks_reason = ks_data.get("engagement_reason", "Initial state: ENGAGED")
                    else:
                        ks_reason = f"Initial state: Unknown ({ks_state!r})"
                except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                    logger.warning(f"Invalid kill switch JSON: {exc}")
                    ks_reason = "Initial state: Invalid data"

            if self._order_ticket:
                self._order_ticket.set_kill_switch_state(ks_engaged, ks_reason)

        except asyncio.TimeoutError:
            logger.warning("Timeout fetching initial safety state from Redis")
            if self._order_ticket:
                self._order_ticket.set_circuit_breaker_state(
                    True, "Safety state fetch timed out"
                )
                self._order_ticket.set_kill_switch_state(True, "Safety state fetch timed out")
        except Exception as exc:
            logger.warning(f"Failed to fetch initial safety state: {exc}")
            if self._order_ticket:
                self._order_ticket.set_circuit_breaker_state(
                    True, "Unable to verify safety state"
                )
                self._order_ticket.set_kill_switch_state(True, "Unable to verify safety state")

    async def _verify_circuit_breaker_safe(self) -> bool:
        """Authoritative check if circuit breaker allows trading.

        Called by OrderTicket at confirm-time to verify circuit breaker state.
        Fetches directly from Redis (not cached pub/sub state) for authoritative check.

        Returns:
            True if circuit breaker is OPEN (trading allowed).
            False if TRIPPED, QUIET_PERIOD, unknown, timeout, or error (fail-closed).
        """
        try:
            cb_raw = await asyncio.wait_for(
                self._redis.get("circuit_breaker:state"),
                timeout=0.5,
            )
            if not cb_raw:
                return False  # Missing = fail-closed
            try:
                cb_data = json.loads(cb_raw)
                cb_state = str(cb_data.get("state", "")).upper()

                tripped_at = cb_data.get("tripped_at")
                reset_at = cb_data.get("reset_at")

                if cb_state == "OPEN":
                    if reset_at:
                        parse_iso_timestamp(str(reset_at))  # Raises on invalid
                    elif tripped_at is not None:
                        logger.warning(
                            "Circuit breaker verify: OPEN with tripped_at but no reset_at"
                        )
                        return False
                    return True
                else:
                    return False
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
                logger.warning(f"Invalid circuit breaker JSON at verify: {exc}")
                return False
        except asyncio.TimeoutError:
            logger.warning("Circuit breaker verification timed out")
            return False
        except Exception as exc:
            logger.warning(f"Circuit breaker verification failed: {exc}")
            return False

    async def _verify_kill_switch_safe(self) -> bool:
        """Authoritative check if kill switch allows trading.

        Called by OrderTicket at confirm-time to verify kill switch state.
        Fetches directly from Redis (not cached pub/sub state) for authoritative check.

        Returns:
            True if kill switch is ACTIVE (trading allowed).
            False if ENGAGED, unknown, timeout, or error (fail-closed).
        """
        try:
            ks_raw = await asyncio.wait_for(
                self._redis.get("kill_switch:state"),
                timeout=0.5,
            )
            if not ks_raw:
                return False  # Missing = fail-closed
            try:
                ks_data = json.loads(ks_raw)
                ks_state = str(ks_data.get("state", "")).upper()

                engaged_at = ks_data.get("engaged_at")
                disengaged_at = ks_data.get("disengaged_at")

                if ks_state == "ACTIVE":
                    if disengaged_at:
                        parse_iso_timestamp(str(disengaged_at))  # Raises on invalid
                    elif engaged_at is not None:
                        logger.warning(
                            "Kill switch verify: ACTIVE with engaged_at but no disengaged_at"
                        )
                        return False
                    return True
                else:
                    return False
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
                logger.warning(f"Invalid kill switch JSON at verify: {exc}")
                return False
        except asyncio.TimeoutError:
            logger.warning("Kill switch verification timed out")
            return False
        except Exception as exc:
            logger.warning(f"Kill switch verification failed: {exc}")
            return False

    # =========================================================================
    # Channel Subscriptions
    # =========================================================================

    async def _subscribe_to_kill_switch_channel(self) -> None:
        """Subscribe to kill switch state - OrderEntryContext owns this."""
        channel = "kill_switch:state"
        await self._acquire_channel(channel, self.OWNER_KILL_SWITCH, self._on_kill_switch_update)

    async def _on_kill_switch_update(self, data: dict[str, Any]) -> None:
        """Handle kill switch update and dispatch to components."""
        if self._disposed:
            return

        try:
            if not isinstance(data, dict):
                raise TypeError(f"Expected dict, got {type(data).__name__}")
            state = str(data.get("state", "")).upper()

            engaged_at = data.get("engaged_at")
            disengaged_at = data.get("disengaged_at")

            if state == "ACTIVE":
                if disengaged_at:
                    parse_iso_timestamp(str(disengaged_at))
                elif engaged_at is not None:
                    raise ValueError("ACTIVE state has engaged_at but no disengaged_at")

        except Exception as exc:
            logger.warning(f"Invalid kill switch payload: {exc}, treating as engaged")
            if self._order_ticket:
                self._order_ticket.set_kill_switch_state(True, "Invalid kill switch payload")
            return

        reason: str | None
        if state not in ("ACTIVE", "ENGAGED"):
            logger.warning(f"Malformed kill switch state: {state!r}, treating as engaged")
            engaged = True
            reason = "Malformed kill switch state"
        elif state == "ENGAGED":
            engaged = True
            raw_reason = data.get("engagement_reason")
            reason = str(raw_reason) if raw_reason is not None else None
        else:
            # ACTIVE state - safe, clear reason to prevent stale halt message
            engaged = False
            reason = None

        if self._order_ticket:
            self._order_ticket.set_kill_switch_state(engaged, reason)

    async def _subscribe_to_circuit_breaker_channel(self) -> None:
        """Subscribe to circuit breaker state - OrderEntryContext owns this."""
        channel = "circuit_breaker:state"
        await self._acquire_channel(
            channel, self.OWNER_CIRCUIT_BREAKER, self._on_circuit_breaker_update
        )

    async def _on_circuit_breaker_update(self, data: dict[str, Any]) -> None:
        """Handle circuit breaker update and dispatch to components."""
        if self._disposed:
            return

        try:
            if not isinstance(data, dict):
                raise TypeError(f"Expected dict, got {type(data).__name__}")
            state = str(data.get("state", "")).upper()

            tripped_at = data.get("tripped_at")
            reset_at = data.get("reset_at")

            if state == "OPEN":
                if reset_at:
                    parse_iso_timestamp(str(reset_at))
                elif tripped_at is not None:
                    raise ValueError("OPEN state has tripped_at but no reset_at")

        except Exception as exc:
            logger.warning(f"Invalid circuit breaker payload: {exc}, treating as tripped")
            if self._order_ticket:
                self._order_ticket.set_circuit_breaker_state(
                    True, "Invalid circuit breaker payload"
                )
            return

        reason: str | None
        if state not in ("OPEN", "TRIPPED", "QUIET_PERIOD"):
            logger.warning(f"Malformed circuit breaker state: {state!r}, treating as tripped")
            tripped = True
            reason = "Malformed circuit breaker state"
        elif state == "QUIET_PERIOD":
            tripped = True
            reason = "Circuit breaker in quiet period"
        elif state == "TRIPPED":
            tripped = True
            raw_reason = data.get("trip_reason")
            reason = str(raw_reason) if raw_reason is not None else None
        else:
            # OPEN state - safe, clear reason to prevent stale trip message
            tripped = False
            reason = None

        if self._order_ticket:
            self._order_ticket.set_circuit_breaker_state(tripped, reason)

    async def _subscribe_to_connection_channel(self) -> None:
        """Subscribe to connection state - OrderEntryContext owns this."""
        channel = "connection:state"
        await self._acquire_channel(channel, self.OWNER_CONNECTION, self._on_connection_update)

    async def _on_connection_update(self, data: dict[str, Any]) -> None:
        """Handle connection update and dispatch to components."""
        if self._disposed:
            return

        try:
            if not isinstance(data, dict):
                raise TypeError(f"Expected dict, got {type(data).__name__}")
            state = str(data.get("state", "")).upper()
        except Exception as exc:
            logger.warning(f"Invalid connection payload: {exc}, treating as read-only")
            self._last_connection_state = "UNKNOWN"
            if self._order_ticket:
                self._order_ticket.set_connection_state("UNKNOWN", True)
            return

        # Track previous state for reconnect detection
        # Only DISCONNECTED/RECONNECTING states indicate true connection loss
        # DEGRADED is read-only but connection is still alive (high latency)
        was_truly_disconnected = (
            self._last_connection_state is not None
            and self._last_connection_state in DISCONNECTED_STATES
        )

        if state not in READ_WRITE_CONNECTION_STATES and state not in READ_ONLY_CONNECTION_STATES:
            logger.warning(f"Malformed connection state: {state!r}, treating as read-only")
            is_read_only = True
        else:
            is_read_only = state not in READ_WRITE_CONNECTION_STATES

        self._last_connection_state = state

        # RECONNECT HANDLING: Re-fetch safety state and re-subscribe channels
        # Only resubscribe after true disconnect (DISCONNECTED/RECONNECTING), not DEGRADED
        # DEGRADED->CONNECTED doesn't need resubscribe since pubsub connection was never lost
        if was_truly_disconnected and state == "CONNECTED":
            logger.info(
                "Connection restored from disconnect - re-fetching safety state and revalidating subscriptions"
            )
            try:
                await self._fetch_initial_safety_state()
            except Exception as exc:
                logger.warning(f"Failed to re-fetch safety state on reconnect: {exc}")

            await self._resubscribe_all_channels()
            await self._retry_failed_subscriptions()

        if self._order_ticket:
            self._order_ticket.set_connection_state(state, is_read_only)

    async def _subscribe_to_positions_channel(self) -> None:
        """Subscribe to position updates - OrderEntryContext owns this."""
        channel = f"positions:{self._user_id}"
        await self._acquire_channel(channel, self.OWNER_POSITIONS, self._on_position_update)

    async def _on_position_update(self, data: dict[str, Any]) -> None:
        """Handle position update and dispatch to components."""
        if self._disposed:
            return

        if not isinstance(data, dict):
            logger.warning(f"Invalid position update payload type: {type(data).__name__}")
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_position_data(self._selected_symbol, 0, None)
            return

        positions = data.get("positions", [])

        if not isinstance(positions, list):
            logger.warning(f"Invalid positions field type: {type(positions).__name__}")
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_position_data(self._selected_symbol, 0, None)
            return

        timestamp_str = data.get("timestamp")
        timestamp: datetime | None = None
        if timestamp_str:
            try:
                timestamp = parse_iso_timestamp(timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in position update, leaving data stale")
        else:
            logger.warning("Position update missing timestamp, leaving data stale")

        if self._order_ticket and self._selected_symbol:
            found = False
            for pos in positions:
                if pos.get("symbol") == self._selected_symbol:
                    try:
                        qty = int(pos.get("qty", 0))
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            f"Invalid qty in position update for {self._selected_symbol}: "
                            f"{pos.get('qty')!r} - {exc}"
                        )
                        qty = 0
                        timestamp = None
                    self._order_ticket.set_position_data(self._selected_symbol, qty, timestamp)
                    found = True
                    break

            if not found:
                self._order_ticket.set_position_data(self._selected_symbol, 0, timestamp)

    # =========================================================================
    # Price Channel Subscription (Selected Symbol)
    # =========================================================================

    async def _subscribe_to_price_channel(self, symbol: str) -> None:
        """Subscribe to price updates for selected symbol."""
        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError as exc:
            logger.warning(f"Invalid symbol for subscription: {symbol!r} - {exc}")
            return

        channel = f"price.updated.{normalized}"
        await self._acquire_channel(channel, self.OWNER_SELECTED_SYMBOL, self._on_price_update)
        self._current_selected_channel = channel

    async def _unsubscribe_from_price_channel(self) -> None:
        """Release selected symbol subscription, keeping watchlist subscription intact."""
        if self._current_selected_channel:
            await self._release_channel(self._current_selected_channel, self.OWNER_SELECTED_SYMBOL)
            self._current_selected_channel = None

    async def _on_price_update(self, data: dict[str, Any]) -> None:
        """Handle price update and dispatch to components."""
        if self._disposed:
            return

        if not isinstance(data, dict):
            logger.warning(f"Invalid price update payload type: {type(data).__name__}")
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_price_data(self._selected_symbol, None, None)
            return

        symbol = data.get("symbol")
        raw_price = data.get("price")
        raw_timestamp = data.get("timestamp")

        if not isinstance(symbol, str) or not symbol:
            logger.warning(f"Price update missing/invalid symbol: {symbol!r}")
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_price_data(self._selected_symbol, None, None)
            return

        timestamp: datetime | None = None
        if raw_timestamp:
            try:
                timestamp = parse_iso_timestamp(raw_timestamp)
            except (ValueError, TypeError) as exc:
                logger.warning(f"Invalid timestamp in price update for {symbol}: {exc}")

        # Dispatch to OrderTicket for selected symbol
        if self._order_ticket and self._selected_symbol and symbol == self._selected_symbol:
            price: Decimal | None = None
            if raw_price is not None:
                try:
                    parsed_price = Decimal(str(raw_price))
                    if parsed_price.is_finite() and parsed_price > 0:
                        price = parsed_price
                    else:
                        logger.warning(
                            f"Invalid/non-finite price {parsed_price} for {symbol}, "
                            "clearing price timestamp"
                        )
                except (InvalidOperation, ValueError, TypeError) as exc:
                    logger.warning(
                        f"Invalid price conversion for {symbol}: {raw_price!r} - {exc}"
                    )

            effective_timestamp = timestamp if price is not None else None
            self._order_ticket.set_price_data(symbol, price, effective_timestamp)

        # Dispatch to MarketContext for display (ONLY if selected symbol)
        if self._market_context and symbol == self._selected_symbol:
            self._market_context.set_price_data(data)

        # Dispatch to PriceChart for real-time tick updates (ONLY if selected symbol)
        if self._price_chart and symbol == self._selected_symbol:
            self._price_chart.set_price_data(data)

        # Dispatch to Watchlist for ALL symbols (NO filtering)
        if self._watchlist:
            self._watchlist.set_symbol_price_data(symbol, data)

    # =========================================================================
    # Symbol Selection
    # =========================================================================

    async def on_symbol_selected(self, symbol: str | None) -> None:
        """Handle symbol selection - manages price subscription lifecycle.

        Called when user selects symbol from Watchlist or MarketContext.

        Args:
            symbol: The selected symbol, or None to clear selection.
        """
        if self._disposed:
            return

        if symbol is not None:
            try:
                symbol = validate_and_normalize_symbol(symbol)
            except ValueError as exc:
                logger.warning(f"Invalid symbol selection rejected: {symbol!r} - {exc}")
                return

        if symbol == self._selected_symbol:
            return

        # Increment selection version to invalidate any in-flight operations
        self._selection_version += 1
        current_version = self._selection_version

        # Unsubscribe from previous symbol's price channel
        await self._unsubscribe_from_price_channel()

        if self._selection_version != current_version:
            return  # Stale - newer selection in progress

        self._selected_symbol = symbol

        # Subscribe to new symbol's price channel (only if symbol provided)
        if symbol:
            try:
                await self._subscribe_to_price_channel(symbol)
            except Exception as exc:
                logger.error(f"Failed to subscribe to price channel for {symbol}: {exc}")
                self._selected_symbol = None
                ui.notify(f"Unable to load data for {symbol}", type="warning")
                if self._order_ticket:
                    await self._order_ticket.on_symbol_changed(None)
                if self._market_context:
                    await self._market_context.on_symbol_changed(None)
                if self._price_chart:
                    await self._price_chart.on_symbol_changed(None)
                return

        if self._selection_version != current_version:
            return  # Stale - newer selection in progress

        # Notify all child components
        if self._order_ticket:
            await self._order_ticket.on_symbol_changed(symbol)
            if self._selection_version != current_version:
                return

        if self._market_context:
            await self._market_context.on_symbol_changed(symbol)
            if self._selection_version != current_version:
                return

        if self._price_chart:
            await self._price_chart.on_symbol_changed(symbol)

    # =========================================================================
    # Watchlist Subscription Management
    # =========================================================================

    async def on_watchlist_subscribe_request(self, symbol: str) -> None:
        """Handle watchlist request to subscribe to a symbol's price channel."""
        if self._disposed:
            return

        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError as exc:
            logger.warning(f"Invalid watchlist symbol for subscription: {symbol!r} - {exc}")
            return

        channel = f"price.updated.{normalized}"
        await self._acquire_channel(channel, self.OWNER_WATCHLIST, self._on_price_update)

    async def on_watchlist_unsubscribe_request(self, symbol: str) -> None:
        """Handle watchlist request to release a symbol's price channel."""
        if self._disposed:
            return

        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError as exc:
            logger.warning(f"Invalid watchlist symbol for unsubscribe: {symbol!r} - {exc}")
            return

        channel = f"price.updated.{normalized}"
        await self._release_channel(channel, self.OWNER_WATCHLIST)

    # =========================================================================
    # Subscription Ownership Management
    # =========================================================================

    async def _acquire_channel(
        self, channel: str, owner: str, callback: Callable[[dict[str, Any]], Any]
    ) -> None:
        """Acquire ownership of a channel, subscribing if first owner."""
        if self._disposed:
            return

        need_subscribe = False
        pending_future: asyncio.Future[None] | None = None

        async with self._subscription_lock:
            if channel in self._pending_subscribes:
                pending_future = self._pending_subscribes[channel]
            elif channel not in self._channel_owners:
                self._channel_owners[channel] = set()
                need_subscribe = True
                pending_future = asyncio.get_running_loop().create_future()
                self._pending_subscribes[channel] = pending_future
            self._channel_owners[channel].add(owner)
            if channel not in self._channel_callbacks:
                self._channel_callbacks[channel] = callback
            elif self._channel_callbacks[channel] is not callback:
                raise ValueError(
                    f"Callback mismatch for channel {channel}: new owner '{owner}' "
                    f"provided different callback than existing."
                )

        if not need_subscribe and pending_future is not None:
            try:
                await pending_future
            except Exception:
                raise
            return

        if need_subscribe:
            try:
                await self._realtime.subscribe(channel, callback)

                if self._disposed:
                    logger.info(f"Disposed during subscribe - unsubscribing {channel}")
                    try:
                        await self._realtime.unsubscribe(channel)
                    except Exception:
                        pass
                    if pending_future and not pending_future.done():
                        pending_future.set_result(None)
                    return

                need_immediate_unsubscribe = False
                async with self._subscription_lock:
                    self._pending_subscribes.pop(channel, None)
                    if channel in self._channel_owners and self._channel_owners[channel]:
                        self._subscriptions.append(channel)
                    else:
                        need_immediate_unsubscribe = True
                        self._channel_callbacks.pop(channel, None)

                if need_immediate_unsubscribe:
                    logger.info(
                        f"All owners released during subscribe - unsubscribing orphan {channel}"
                    )
                    try:
                        await self._realtime.unsubscribe(channel)
                    except Exception as exc:
                        logger.warning(f"Failed to unsubscribe orphan {channel}: {exc}")

                if pending_future and not pending_future.done():
                    pending_future.set_result(None)

            except Exception as exc:
                logger.warning(f"Failed to subscribe to {channel}: {exc}")
                async with self._subscription_lock:
                    self._pending_subscribes.pop(channel, None)
                    owners_snapshot = set()
                    if channel in self._channel_owners:
                        owners_snapshot = self._channel_owners[channel].copy()
                    self._failed_subscriptions[channel] = (owners_snapshot, callback)
                    self._channel_callbacks.pop(channel, None)
                    if channel in self._channel_owners:
                        del self._channel_owners[channel]
                if pending_future and not pending_future.done():
                    pending_future.set_exception(exc)
                raise

    async def _release_channel(self, channel: str, owner: str) -> None:
        """Release ownership of a channel, unsubscribing if last owner."""
        if self._disposed:
            return

        need_unsubscribe = False

        async with self._subscription_lock:
            if channel in self._failed_subscriptions:
                failed_owners, _ = self._failed_subscriptions[channel]
                failed_owners.discard(owner)
                if not failed_owners:
                    del self._failed_subscriptions[channel]

            if channel not in self._channel_owners:
                return

            self._channel_owners[channel].discard(owner)

            if not self._channel_owners[channel]:
                need_unsubscribe = True
                del self._channel_owners[channel]
                self._channel_callbacks.pop(channel, None)
                self._failed_subscriptions.pop(channel, None)
                try:
                    self._subscriptions.remove(channel)
                except ValueError:
                    pass

        if need_unsubscribe:
            try:
                await self._realtime.unsubscribe(channel)
            except Exception as exc:
                logger.warning(f"Error releasing channel {channel}: {exc}")

    async def _resubscribe_all_channels(self) -> None:
        """Re-subscribe all owned channels after reconnect."""
        if self._disposed:
            return

        async with self._subscription_lock:
            channels_with_callbacks = [
                (channel, self._channel_callbacks.get(channel))
                for channel in self._subscriptions
            ]

        if not channels_with_callbacks:
            return

        logger.info(f"Re-subscribing {len(channels_with_callbacks)} channels after reconnect")

        for channel, callback in channels_with_callbacks:
            if self._disposed:
                return

            async with self._subscription_lock:
                if channel not in self._channel_owners or not self._channel_owners[channel]:
                    logger.debug(f"Skipping resubscribe for released channel: {channel}")
                    continue

            if callback is None:
                logger.warning(
                    f"No callback registered for channel {channel} - skipping resubscribe"
                )
                continue
            try:
                await self._realtime.subscribe(channel, callback)
            except Exception as exc:
                logger.warning(f"Failed to resubscribe channel {channel}: {exc}")
                async with self._subscription_lock:
                    if channel in self._channel_owners:
                        owners_snapshot = self._channel_owners[channel].copy()
                        self._failed_subscriptions[channel] = (owners_snapshot, callback)

    async def _retry_failed_subscriptions(self) -> None:
        """Retry subscriptions that previously failed."""
        if self._disposed:
            return

        async with self._subscription_lock:
            failed_channels = {
                ch: (owners.copy(), cb)
                for ch, (owners, cb) in self._failed_subscriptions.items()
            }

        if not failed_channels:
            return

        logger.info(f"Retrying {len(failed_channels)} failed subscriptions after reconnect")

        for channel, (owners, callback) in failed_channels.items():
            if self._disposed:
                return
            if not owners:
                async with self._subscription_lock:
                    self._failed_subscriptions.pop(channel, None)
                continue
            try:
                for owner in owners:
                    await self._acquire_channel(channel, owner, callback)
                async with self._subscription_lock:
                    self._failed_subscriptions.pop(channel, None)
                logger.info(
                    f"Successfully retried subscription for {channel} with {len(owners)} owner(s)"
                )
            except Exception as exc:
                logger.warning(f"Retry failed for channel {channel}: {exc}")

    # =========================================================================
    # Timer Management
    # =========================================================================

    def _track_timer(self, timer: ui.timer) -> None:
        """Track a timer for cleanup."""
        self._timers.append(timer)

    # =========================================================================
    # Getters for Components
    # =========================================================================

    def get_verify_circuit_breaker(self) -> Callable[[], Awaitable[bool]]:
        """Get the circuit breaker verification callback for OrderTicket."""
        return self._verify_circuit_breaker_safe

    def get_verify_kill_switch(self) -> Callable[[], Awaitable[bool]]:
        """Get the kill switch verification callback for OrderTicket."""
        return self._verify_kill_switch_safe

    def get_selected_symbol(self) -> str | None:
        """Get the currently selected symbol."""
        return self._selected_symbol

    # =========================================================================
    # Cleanup
    # =========================================================================

    async def dispose(self) -> None:
        """Clean up all subscriptions and timers on page unload."""
        if self._disposed:
            return
        self._disposed = True

        # Cancel all timers
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()

        # Unsubscribe from all channels
        async with self._subscription_lock:
            channels_to_unsubscribe = list(self._subscriptions)

            for channel, future in self._pending_subscribes.items():
                if future and not future.done():
                    future.set_exception(asyncio.CancelledError("OrderEntryContext disposed"))

            self._subscriptions.clear()
            self._channel_owners.clear()
            self._pending_subscribes.clear()
            self._channel_callbacks.clear()
            self._failed_subscriptions.clear()

        for channel in channels_to_unsubscribe:
            try:
                await self._realtime.unsubscribe(channel)
            except Exception as exc:
                logger.warning(f"Error unsubscribing from {channel}: {exc}")

        # Dispose child components
        if self._watchlist:
            await self._watchlist.dispose()
        if self._order_ticket:
            await self._order_ticket.dispose()
        if self._market_context:
            await self._market_context.dispose()
        if self._price_chart:
            await self._price_chart.dispose()

        logger.info(f"OrderEntryContext disposed for user {self._user_id}")


__all__ = ["OrderEntryContext"]
