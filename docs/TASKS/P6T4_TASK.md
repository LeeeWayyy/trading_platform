---
id: P6T4
title: "Professional Trading Terminal - Order Entry Context"
phase: P6
task: T4
priority: P0
owner: "@development-team"
state: PLAN
created: 2026-01-13
dependencies: [P6T1, P6T2, P6T3]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T4.1-T4.4]
---

# P6T4: Order Entry Context

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLAN (Planning Complete)
**Priority:** P0 (Core Trading)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 4 of 18
**Dependency:** P6T1 (Core), P6T2 (Header - connection/kill switch gating), P6T3 (Hotkeys)

---

## Objective

Build the order entry context with always-visible order ticket, real-time market data, price charts, and watchlist.

**Success looks like:**
- Order ticket visible on dashboard without navigation
- Level 1 market data displayed when entering orders
- TradingView chart integrated for visual context
- Watchlist with quick action capability

---

## Architecture Overview

### Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Dashboard Page                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌──────────────────────┐  ┌────────────────────────┐ │
│  │   Watchlist     │  │   Market Context     │  │   Price Chart          │ │
│  │   (T4.4)        │  │   (T4.2)             │  │   (T4.3)               │ │
│  │                 │  │                      │  │                        │ │
│  │  [AAPL] ▲1.2%   │  │  AAPL                │  │  ┌──────────────────┐  │ │
│  │  [MSFT] ▼0.5%   │→ │  Bid: 150.25 x 100  │→ │  │ ░░▓▓█░░▓▓░░▓▓█  │  │ │
│  │  [TSLA] ▲2.1%   │  │  Ask: 150.50 x 200  │  │  │ Candlestick      │  │ │
│  │  [GOOGL]▲0.8%   │  │  Spread: 0.17%      │  │  │ + Exec Markers   │  │ │
│  │  + Add Symbol   │  │  Last: 150.37 ▲0.5% │  │  └──────────────────┘  │ │
│  └────────┬────────┘  │  ⏱ Updated: 2s ago  │  │                        │ │
│           │           └──────────┬───────────┘  └────────────────────────┘ │
│           │                      │                                          │
│           ▼                      ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                        Order Ticket (T4.1)                             │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │ │
│  │  │  Symbol: [AAPL    ▼]  Current Pos: +100 shares                  │  │ │
│  │  │  Side:   [BUY] [SELL]  Buying Power: $125,432.00                │  │ │
│  │  │  Qty:    [_____]  [100] [500] [1000] [MAX]   ← Presets          │  │ │
│  │  │  Type:   [Market ▼]  Limit: [______]                            │  │ │
│  │  │  Impact: Uses $15,050.00 (12% of buying power)                  │  │ │
│  │  │  ───────────────────────────────────────────                    │  │ │
│  │  │  [ Preview Order ]  [ Clear ]                                   │  │ │
│  │  └─────────────────────────────────────────────────────────────────┘  │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Real-Time Data Sources                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐     │
│  │ Alpaca WebSocket│      │ Redis Pub/Sub   │      │ Redis Cache     │     │
│  │ Market Data     │ ───▶ │ price.updated.* │ ───▶ │ price:{symbol}  │     │
│  │ (Quote Stream)  │      │ fills:{user}    │      │ TTL: 300s       │     │
│  └─────────────────┘      └────────┬────────┘      └────────┬────────┘     │
│                                    │                        │               │
│                                    ▼                        ▼               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    RealtimeUpdater (P6T1.3)                          │   │
│  │  • Throttled updates (100ms min interval)                           │   │
│  │  • Bounded queue (100 items, drop oldest)                           │   │
│  │  • Per-channel subscriptions                                        │   │
│  └───────────────────────────────────┬─────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      UI Components                                   │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │   │
│  │  │ MarketCtx   │  │ OrderTicket │  │ Watchlist   │  │ PriceChart │  │   │
│  │  │ (bid/ask)   │  │ (position,  │  │ (prices,    │  │ (candles,  │  │   │
│  │  │             │  │  bp impact) │  │  sparklines)│  │  markers)  │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### State Management Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          State Management                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  app.storage.client (Per-Tab, Browser-Session)                       │   │
│  │  ├── order_entry_context: OrderEntryContext                          │   │
│  │  │   ├── selected_symbol: str | None                                 │   │
│  │  │   ├── side: "buy" | "sell"                                        │   │
│  │  │   ├── quantity: int | None                                        │   │
│  │  │   ├── order_type: "market" | "limit" | "stop" | "stop_limit"      │   │
│  │  │   └── limit_price: Decimal | None                                 │   │
│  │  ├── market_data_cache: dict[symbol, MarketDataSnapshot]             │   │
│  │  └── watchlist_state: WatchlistState                                 │   │
│  │      ├── symbols: list[str]                                          │   │
│  │      ├── expanded: bool                                              │   │
│  │      └── sort_by: "name" | "change" | "volume"                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  UserStateManager (Redis-Backed, 24hr TTL)                           │   │
│  │  ├── pending_form: {"order_entry:{tab_id}": {...}}  # Per-tab form   │   │
│  │  └── preferences: {"watchlist_symbols": [...]}  # Persistent         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  WorkspacePersistenceService (PostgreSQL, Per-User)                  │   │
│  │  └── workspace_key: "watchlist.main"                                 │   │
│  │      └── state: {"symbols": [...], "order": [...]}                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tasks (4 total)

### T4.1: Always-Visible Order Ticket - HIGH PRIORITY

**Goal:** Embed order entry directly on dashboard for rapid execution.

**Acceptance Criteria:**
- [ ] Order ticket visible on dashboard without navigation
- [ ] Position for selected symbol shown in ticket
- [ ] One-click quantity presets functional
- [ ] Buying power impact calculated before submit
- [ ] Order ticket respects connection state (disabled when disconnected)
- [ ] Order ticket respects kill switch state (disabled when halted)

**Files:**
- Create: `apps/web_console_ng/components/order_ticket.py`
- Create: `apps/web_console_ng/components/quantity_presets.py`
- Modify: `apps/web_console_ng/pages/dashboard.py`

#### Implementation Details

##### 4.1.1 OrderTicket Component Architecture

```python
# SHARED MODULE: apps/web_console_ng/utils/time.py
# This utility is imported by all components (OrderTicket, MarketContext, PriceChart,
# Watchlist, OrderEntryContext) to ensure consistent timestamp parsing.
# DO NOT duplicate this function - always import from the shared module.

def parse_iso_timestamp(timestamp_str: str) -> datetime:
    """Parse ISO 8601 timestamp string, handling 'Z' suffix and converting to UTC.

    Python 3.11's datetime.fromisoformat() doesn't accept 'Z' suffix,
    which is common in JSON payloads and API responses. This helper
    normalizes the timezone suffix before parsing.

    SAFETY: Always returns a timezone-aware datetime NORMALIZED TO UTC.
    - If source has timezone offset (e.g., +05:00), converts to UTC equivalent
    - If source is naive (no timezone), assumes UTC
    This ensures consistent staleness calculations (aware - aware works correctly).

    Args:
        timestamp_str: ISO 8601 timestamp (e.g., "2024-01-15T10:30:00Z" or "2024-01-15T10:30:00+05:00")

    Returns:
        Timezone-aware datetime object normalized to UTC.
    """
    # Replace 'Z' with '+00:00' for Python compatibility
    normalized = timestamp_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)

    # SAFETY: Normalize to UTC for consistent staleness calculations
    if dt.tzinfo is None:
        # Naive timestamp: assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Aware timestamp: convert to UTC (e.g., +05:00 -> UTC equivalent)
        dt = dt.astimezone(timezone.utc)

    return dt


# Symbol validation pattern: uppercase letters, digits, dots (for BRK.B style), hyphens
# Max 10 chars to prevent abuse
VALID_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9\.\-]{1,10}$")


def validate_and_normalize_symbol(symbol: str) -> str:
    """Validate and normalize a stock symbol for channel subscription.

    SECURITY: Symbols come from user input and are used to construct Redis channel names.
    We must validate to prevent malformed/malicious channel names.

    Normalization:
    - Strip whitespace
    - Convert to uppercase

    Validation:
    - Must be 1-10 characters
    - Only alphanumeric, dots, and hyphens allowed
    - Dots allow symbols like BRK.B, hyphens for special cases

    Args:
        symbol: Raw symbol string from user input

    Returns:
        Normalized symbol (uppercase, stripped)

    Raises:
        ValueError: If symbol is invalid (empty, too long, or contains invalid chars)
    """
    if not symbol:
        raise ValueError("Symbol cannot be empty")

    normalized = symbol.strip().upper()

    if not VALID_SYMBOL_PATTERN.match(normalized):
        raise ValueError(f"Invalid symbol format: {symbol!r}")

    return normalized


@dataclass
class OrderTicketState:
    """Order ticket form state."""
    symbol: str | None = None
    side: Literal["buy", "sell"] = "buy"
    quantity: int | None = None
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"

class OrderTicketComponent:
    """Always-visible order entry widget for dashboard."""

    # Configuration
    QUANTITY_PRESETS = [100, 500, 1000]  # Configurable via env
    STALE_POSITION_THRESHOLD_S = 30

    def __init__(
        self,
        trading_client: AsyncTradingClient,
        state_manager: UserStateManager,
        connection_monitor: ConnectionMonitor,
        user_id: str,  # Required for API calls (positions, account info)
        role: str,  # User role for authorization
        strategies: list[str],  # Strategies for position filtering
        on_symbol_selected: Callable[[str | None], Awaitable[None]] | None = None,  # Async callback (None clears selection)
        verify_circuit_breaker: Callable[[], Awaitable[bool]] | None = None,  # Returns True if safe to trade
        verify_kill_switch: Callable[[], Awaitable[bool]] | None = None,  # Returns True if safe to trade
    ):
        self._client = trading_client
        self._state_manager = state_manager
        self._connection_monitor = connection_monitor
        self._user_id = user_id
        self._role = role
        self._strategies = strategies
        self._on_symbol_selected = on_symbol_selected
        # CRITICAL: Callbacks to OrderEntryContext for authoritative safety checks
        # OrderTicket does NOT have direct Redis access - this is by design.
        # Safety verification is delegated to OrderEntryContext.
        self._verify_circuit_breaker = verify_circuit_breaker
        self._verify_kill_switch = verify_kill_switch

        # UI elements (bound after create())
        self._symbol_input: ui.select | None = None
        self._side_toggle: ui.toggle | None = None
        self._quantity_input: ui.number | None = None
        self._order_type_select: ui.select | None = None
        self._limit_price_input: ui.number | None = None
        self._stop_price_input: ui.number | None = None  # For stop and stop_limit orders
        self._submit_button: ActionButton | None = None

        # State
        self._state = OrderTicketState()
        self._current_position: int = 0
        self._buying_power: Decimal | None = None
        self._last_price: Decimal | None = None

        # Safety state (FAIL-CLOSED defaults)
        # CRITICAL: Default to UNSAFE state until initial safety fetch confirms otherwise.
        # This prevents a fail-open window between component creation and safety state load.
        self._kill_switch_engaged: bool = True  # Default: engaged (unsafe)
        self._circuit_breaker_tripped: bool = True  # Default: tripped (unsafe)
        self._safety_state_loaded: bool = False  # Track if initial safety state has been loaded
        self._timer_tracker: Callable[[ui.timer], None] | None = None

        # Tab session ID for cross-tab isolation of pending forms
        # Each tab/component instance gets a unique ID to prevent intent collision
        import uuid
        self._tab_session_id: str = uuid.uuid4().hex[:16]

        # Timestamp tracking for staleness checks (fail-closed)
        self._position_last_updated: datetime | None = None
        self._price_last_updated: datetime | None = None
        self._buying_power_last_updated: datetime | None = None

        # Position/risk limits (cached from risk manager, fail-closed defaults)
        # CRITICAL: These limits provide client-side safety checks before server-side validation.
        # Triple defense: cached limits → confirm-time fresh limits → server-side validation.
        self._max_position_per_symbol: int | None = None  # Max shares per symbol
        self._max_notional_per_order: Decimal | None = None  # Max $ per order
        self._max_total_exposure: Decimal | None = None  # Max total portfolio exposure $
        self._current_total_exposure: Decimal | None = None  # Current portfolio exposure $ (sum of position notionals)
        self._limits_last_updated: datetime | None = None
        self._limits_loaded: bool = False  # Track if initial limits have been fetched (fail-closed)

        # Timer references for cleanup
        self._position_timer: ui.timer | None = None
        self._buying_power_timer: ui.timer | None = None

        # Task tracking for periodic refresh (prevents overlap and allows cancellation)
        self._position_refresh_task: asyncio.Task | None = None
        self._buying_power_refresh_task: asyncio.Task | None = None
        self._disposed: bool = False  # Guard against updates after disposal

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Initialize the order ticket component with timer tracking.

        Args:
            timer_tracker: Callback to register timers with OrderEntryContext for lifecycle management.
        """
        self._timer_tracker = timer_tracker

        # Start data refresh timers (tracked via timer_tracker)
        self._start_data_refresh_timers(timer_tracker)

        # Restore any pending form from previous session
        await self._restore_pending_form()

###### Order Type Dependent Price Fields

The order ticket must show/hide price input fields based on order type:

**Visibility Rules:**
- `market`: No price fields shown
- `limit`: Show `_limit_price_input` only
- `stop`: Show `_stop_price_input` only
- `stop_limit`: Show both `_limit_price_input` and `_stop_price_input`

def _on_order_type_changed(self, order_type: str) -> None:
    """Handle order type selection change.

    Updates price field visibility and clears values for hidden fields.
    This prevents stale price values from previous order type being submitted.
    """
    self._state.order_type = order_type

    # Determine which price fields to show
    show_limit = order_type in ("limit", "stop_limit")
    show_stop = order_type in ("stop", "stop_limit")

    # Update visibility
    if self._limit_price_input:
        if show_limit:
            self._limit_price_input.classes(remove="hidden")
        else:
            self._limit_price_input.classes(add="hidden")
            # Clear hidden field value to prevent stale data
            self._limit_price_input.set_value(None)
            self._state.limit_price = None

    if self._stop_price_input:
        if show_stop:
            self._stop_price_input.classes(remove="hidden")
        else:
            self._stop_price_input.classes(add="hidden")
            # Clear hidden field value to prevent stale data
            self._stop_price_input.set_value(None)
            self._state.stop_price = None

    # Update buying power impact with new effective price
    self._update_buying_power_impact()

def _create_price_inputs(self, container: ui.element) -> None:
    """Create price input fields for limit and stop orders.

    Both fields are created but visibility is controlled by _on_order_type_changed.
    """
    with container:
        # Limit price input (for limit and stop_limit orders)
        self._limit_price_input = ui.number(
            label="Limit Price",
            format="%.2f",
            min=0.01,
            step=0.01,
            on_change=lambda e: self._on_limit_price_changed(e.value),
        ).classes("w-32 hidden")  # Hidden by default (market order)

        # Stop price input (for stop and stop_limit orders)
        self._stop_price_input = ui.number(
            label="Stop Price",
            format="%.2f",
            min=0.01,
            step=0.01,
            on_change=lambda e: self._on_stop_price_changed(e.value),
        ).classes("w-32 hidden")  # Hidden by default (market order)

def _on_limit_price_changed(self, value: float | None) -> None:
    """Handle limit price input change.

    SAFETY: Validates input is finite and positive to prevent NaN/Infinity
    from bypassing downstream limit checks.
    """
    if value is not None:
        try:
            dec_value = Decimal(str(value))
            # CRITICAL: Reject non-finite values (NaN/Infinity can bypass comparisons)
            if dec_value.is_finite() and dec_value > 0:
                self._state.limit_price = dec_value
            else:
                self._state.limit_price = None
        except (InvalidOperation, ValueError, TypeError):
            self._state.limit_price = None
    else:
        self._state.limit_price = None
    self._update_buying_power_impact()

def _on_stop_price_changed(self, value: float | None) -> None:
    """Handle stop price input change.

    SAFETY: Validates input is finite and positive to prevent NaN/Infinity
    from bypassing downstream limit checks.
    """
    if value is not None:
        try:
            dec_value = Decimal(str(value))
            # CRITICAL: Reject non-finite values (NaN/Infinity can bypass comparisons)
            if dec_value.is_finite() and dec_value > 0:
                self._state.stop_price = dec_value
            else:
                self._state.stop_price = None
        except (InvalidOperation, ValueError, TypeError):
            self._state.stop_price = None
    else:
        self._state.stop_price = None
    self._update_buying_power_impact()
```

##### 4.1.2 Safety Integration (P6T2 Dependencies)

```python
# Integration with ConnectionMonitor (P6T2.3) and KillSwitch (P6T2.4)

# Data freshness thresholds (trading safety)
POSITION_STALE_THRESHOLD_S = 30  # Block if position data > 30s old
PRICE_STALE_THRESHOLD_S = 30    # Block if market price > 30s old
BUYING_POWER_STALE_THRESHOLD_S = 60  # Block if buying power > 60s old
LIMITS_STALE_THRESHOLD_S = 300  # Block if risk limits > 5min old (limits change infrequently)

def _should_disable_submission(self) -> tuple[bool, str]:
    """Check if order submission should be disabled.

    FAIL-CLOSED: Returns (True, reason) if ANY safety condition fails.
    Order of checks matters for user feedback priority.
    """

    # 0. Safety state loaded check (FAIL-CLOSED)
    # CRITICAL: Block submission until initial safety state has been fetched.
    # This prevents a fail-open window between component creation and safety state load.
    if not self._safety_state_loaded:
        return (True, "Safety state loading...")

    # 1. Connection state check
    if self._connection_monitor.is_read_only():
        return (True, "Connection unavailable")

    # 2. Kill switch check (cached, real-time updated)
    if self._kill_switch_engaged:
        return (True, "Kill switch engaged")

    # 3. Circuit breaker check (cached, real-time updated)
    # CRITICAL: Must block all orders when circuit breaker is tripped
    if self._circuit_breaker_tripped:
        return (True, "Circuit breaker tripped - trading halted")

    # 4. Symbol validation
    if not self._state.symbol:
        return (True, "Select a symbol")

    # 5. Quantity validation
    if not self._state.quantity or self._state.quantity <= 0:
        return (True, "Enter quantity")

    # 6. Data staleness checks (fail-closed for safety)
    if self._is_position_data_stale():
        return (True, "Position data stale - refresh required")

    if self._is_price_data_stale():
        return (True, "Market price stale - refresh required")

    # 7. Buying power staleness check (CRITICAL for order impact calculation)
    if self._is_buying_power_stale():
        return (True, "Buying power data stale - refresh required")

    # 8. Order type price validation
    # CRITICAL: Ensure required prices are provided based on order type
    price_error = self._validate_order_type_prices()
    if price_error:
        return (True, price_error)

    # 9. Position limits loaded check (fail-closed)
    # CRITICAL: Block submission until risk limits have been fetched.
    if not self._limits_loaded:
        return (True, "Risk limits loading...")

    # 10. Position limits staleness check
    if self._is_limits_stale():
        return (True, "Risk limits stale - refresh required")

    # 11. Position limit validation (cached check - will re-verify at confirm time)
    limit_violation = self._check_position_limits()
    if limit_violation:
        return (True, limit_violation)

    return (False, "")

def _is_position_data_stale(self) -> bool:
    """Check if position data is too old for safe trading."""
    if self._position_last_updated is None:
        return True  # No data = stale (fail-closed)
    age_s = (datetime.now(timezone.utc) - self._position_last_updated).total_seconds()
    return age_s > POSITION_STALE_THRESHOLD_S

def _is_price_data_stale(self) -> bool:
    """Check if market price is too old for safe trading."""
    if self._price_last_updated is None:
        return True  # No data = stale (fail-closed)
    age_s = (datetime.now(timezone.utc) - self._price_last_updated).total_seconds()
    return age_s > PRICE_STALE_THRESHOLD_S

def _is_buying_power_stale(self) -> bool:
    """Check if buying power data is too old for safe trading.

    CRITICAL: Stale buying power can lead to orders that exceed account limits.
    Buying power changes with market movements and fills, so we must ensure
    fresh data before calculating order impact.
    """
    if self._buying_power_last_updated is None:
        return True  # No data = stale (fail-closed)
    age_s = (datetime.now(timezone.utc) - self._buying_power_last_updated).total_seconds()
    return age_s > BUYING_POWER_STALE_THRESHOLD_S

def _is_limits_stale(self) -> bool:
    """Check if position/risk limits are too old for safe trading.

    CRITICAL: Stale limits could allow orders that breach risk constraints.
    """
    if self._limits_last_updated is None:
        return True  # No data = stale (fail-closed)
    age_s = (datetime.now(timezone.utc) - self._limits_last_updated).total_seconds()
    return age_s > LIMITS_STALE_THRESHOLD_S

def _check_position_limits(self) -> str | None:
    """Check if proposed order violates position limits.

    Returns:
        Error message if limit violated, None if within limits.

    CRITICAL: Triple defense - this is the cached check layer.
    Confirm-time and server-side validation provide additional protection.
    """
    if not self._state.symbol or not self._state.quantity:
        return None  # Validation handled elsewhere

    # Calculate proposed position after order
    proposed_qty = self._state.quantity
    if self._state.side == "sell":
        proposed_position = self._current_position - proposed_qty
    else:
        proposed_position = self._current_position + proposed_qty

    # Check per-symbol position limit
    if self._max_position_per_symbol is not None:
        if abs(proposed_position) > self._max_position_per_symbol:
            return f"Order exceeds position limit ({self._max_position_per_symbol} shares)"

    # Check per-order notional limit
    # CRITICAL: Use effective order price, not just last market price
    # - limit/stop_limit orders use limit_price
    # - stop orders (BOTH buy and sell) use max(stop_price, last_price) for conservative
    #   limit checking. This ensures we check against the highest possible notional.
    # - market orders use last_price
    effective_price = self._get_effective_order_price()
    order_notional: Decimal | None = None
    # Use `is not None` to handle price=0 correctly (though 0 should be invalid earlier)
    if effective_price is not None:
        order_notional = Decimal(proposed_qty) * effective_price

    # Use `is not None` for consistency with safety rules (0 is a valid notional for 0-qty)
    if self._max_notional_per_order is not None and order_notional is not None:
        if order_notional > self._max_notional_per_order:
            return f"Order exceeds max notional (${self._max_notional_per_order:,.0f})"

    # Check total portfolio exposure limit
    # CRITICAL: Prevents total portfolio risk from exceeding configured limits
    # Use `is not None` for consistency with safety rules
    if self._max_total_exposure is not None and order_notional is not None:
        # FAIL-CLOSED: If we don't know current exposure, block the order
        # This prevents submitting orders when we can't verify risk limits
        if self._current_total_exposure is None:
            return "Cannot verify exposure limit - position data unavailable"

        # Calculate per-symbol exposure change
        # Current symbol position is tracked as _current_position (shares)
        current_symbol_notional = abs(
            Decimal(self._current_position) * (self._last_price or Decimal(0))
        )

        # Calculate proposed symbol position and its notional
        if self._state.side == "buy":
            proposed_symbol_pos = self._current_position + self._state.quantity
        else:
            proposed_symbol_pos = self._current_position - self._state.quantity
        proposed_symbol_notional = abs(
            Decimal(proposed_symbol_pos) * (effective_price or self._last_price or Decimal(0))
        )

        # Projected total exposure = current total - current symbol + proposed symbol
        # This correctly handles: long closing, short opening, flip from long to short, etc.
        projected_exposure = (
            self._current_total_exposure
            - current_symbol_notional
            + proposed_symbol_notional
        )

        if projected_exposure > self._max_total_exposure:
            return f"Order exceeds total exposure limit (${self._max_total_exposure:,.0f})"

    return None

def _get_effective_order_price(self) -> Decimal | None:
    """Get the effective price for order calculations (notional, MAX, limits).

    Returns a CONSERVATIVE price estimate for fail-closed safety:
    - limit/stop_limit: limit_price (the max price we'll pay/min we'll receive)
    - stop (BOTH buy and sell): max(stop_price, last_price) - conservative for limits
    - market: last_price (best estimate of execution price)

    CRITICAL: For stop orders, we use max(stop, last) for BOTH sides because:
    - Stop buy: gap UP means paying more than stop = higher notional
    - Stop sell: using max ensures we check against higher notional threshold,
      which is more conservative for limit checks even though execution might
      be at a lower price on gap down

    This conservative approach ensures we don't exceed limits by underestimating
    notional exposure. Returns None if price is unavailable.
    """
    order_type = self._state.order_type
    side = self._state.side

    if order_type in ("limit", "stop_limit"):
        # Limit orders execute at limit price or better
        return self._state.limit_price or self._last_price

    elif order_type == "stop":
        # Stop orders trigger at stop price, then execute at market
        # Use CONSERVATIVE estimate (max) for limit/notional calculations
        # This ensures we check against the highest possible notional
        stop_price = self._state.stop_price
        if stop_price and self._last_price:
            # BOTH buy and sell use max(stop, last) for conservative limit checking:
            # - Stop buy: gap UP means paying more than stop = higher notional
            # - Stop sell: using max ensures we check against higher notional threshold,
            #   which is more conservative for limit checks even though execution
            #   might be at a lower price on gap down
            return max(stop_price, self._last_price)
        return stop_price or self._last_price

    else:
        # Market orders - use last traded price as best estimate
        return self._last_price

def _validate_order_type_prices(self) -> str | None:
    """Validate required prices based on order type.

    Returns:
        Error message if validation fails, None if valid.

    Order type requirements:
    - market: No price required
    - limit: limit_price required and > 0
    - stop: stop_price required and > 0
    - stop_limit: Both limit_price and stop_price required and > 0
    """
    order_type = self._state.order_type
    limit_price = self._state.limit_price
    stop_price = self._state.stop_price

    if order_type == "market":
        # Market orders don't require price fields
        return None

    elif order_type == "limit":
        if limit_price is None:
            return "Limit orders require a limit price"
        if limit_price <= 0:
            return "Limit price must be positive"

    elif order_type == "stop":
        if stop_price is None:
            return "Stop orders require a stop price"
        if stop_price <= 0:
            return "Stop price must be positive"

    elif order_type == "stop_limit":
        if limit_price is None:
            return "Stop-limit orders require a limit price"
        if limit_price <= 0:
            return "Limit price must be positive"
        if stop_price is None:
            return "Stop-limit orders require a stop price"
        if stop_price <= 0:
            return "Stop price must be positive"

        # Validate stop-limit price relationship based on order side
        # Buy stop-limit: stop triggers above current price, limit sets max fill price
        #   - limit_price <= stop_price (willing to pay up to limit after trigger)
        # Sell stop-limit: stop triggers below current price, limit sets min fill price
        #   - limit_price >= stop_price (willing to sell down to limit after trigger)
        side = self._state.side
        if side == "buy" and limit_price > stop_price:
            return (
                f"Buy stop-limit: limit price (${limit_price:.2f}) must be at or below "
                f"stop price (${stop_price:.2f})"
            )
        elif side == "sell" and limit_price < stop_price:
            return (
                f"Sell stop-limit: limit price (${limit_price:.2f}) must be at or above "
                f"stop price (${stop_price:.2f})"
            )

    return None

def _validate_order_data_prices(self, order_data: dict) -> str | None:
    """Final validation of built order_data before submission.

    Args:
        order_data: The built order dictionary ready for submission.

    Returns:
        Error message if validation fails, None if valid.

    This is the final line of defense for price validation (triple defense).
    Validates the actual data being submitted, not the source state.
    """
    order_type = order_data.get("order_type", "market")
    limit_price = order_data.get("limit_price")
    stop_price = order_data.get("stop_price")

    # Helper for safe Decimal conversion
    # Accepts str, Decimal, int, float - normalizes to Decimal
    # Rejects NaN/Infinity to prevent invalid order data
    def safe_decimal(value: str | Decimal | int | float | None) -> Decimal | None:
        if value is None:
            return None
        try:
            # Normalize to str first to handle all numeric types consistently
            dec = Decimal(str(value))
            # Reject NaN/Infinity - invalid for order prices
            if not dec.is_finite():
                return None
            return dec
        except (InvalidOperation, ValueError, TypeError):
            return None

    if order_type == "market":
        return None

    elif order_type == "limit":
        # Explicit None or empty string check (not falsy, to handle "0" correctly)
        if limit_price is None or limit_price == "":
            return "Limit order missing limit_price"
        limit_dec = safe_decimal(limit_price)
        if limit_dec is None:
            return f"Invalid limit price format: {limit_price!r}"
        if limit_dec <= 0:
            return "Limit price must be positive"

    elif order_type == "stop":
        if stop_price is None or stop_price == "":
            return "Stop order missing stop_price"
        stop_dec = safe_decimal(stop_price)
        if stop_dec is None:
            return f"Invalid stop price format: {stop_price!r}"
        if stop_dec <= 0:
            return "Stop price must be positive"

    elif order_type == "stop_limit":
        if limit_price is None or limit_price == "":
            return "Stop-limit order missing limit_price"
        limit_dec = safe_decimal(limit_price)
        if limit_dec is None:
            return f"Invalid limit price format: {limit_price!r}"
        if limit_dec <= 0:
            return "Limit price must be positive"
        if stop_price is None or stop_price == "":
            return "Stop-limit order missing stop_price"
        stop_dec = safe_decimal(stop_price)
        if stop_dec is None:
            return f"Invalid stop price format: {stop_price!r}"
        if stop_dec <= 0:
            return "Stop price must be positive"

        # Validate stop-limit price relationship based on order side
        side = order_data.get("side", "buy")
        if side == "buy" and limit_dec > stop_dec:
            return (
                f"Buy stop-limit: limit price (${limit_dec:.2f}) must be at or below "
                f"stop price (${stop_dec:.2f})"
            )
        elif side == "sell" and limit_dec < stop_dec:
            return (
                f"Sell stop-limit: limit price (${limit_dec:.2f}) must be at or above "
                f"stop price (${stop_dec:.2f})"
            )

    return None

async def _handle_submit(self) -> bool | None:
    """Handle order submission with two-phase confirmation."""

    # Phase 1: Cached safety check (instant feedback)
    disabled, reason = self._should_disable_submission()
    if disabled:
        ui.notify(f"Cannot submit: {reason}", type="negative")
        return False

    # Get or create intent-based client_order_id BEFORE preview
    # Intent model: new intent on form change, reuse on retry of same form state
    # This allows same order params to be submitted multiple times intentionally
    self._pending_client_order_id = await self._get_or_create_client_order_id()
    # NOTE: _get_or_create_client_order_id already saves pending form with client_order_id

    # Show preview dialog (user must confirm)
    await self._show_preview_dialog()
    return None  # Manual lifecycle continues in dialog

async def _confirm_and_submit(self) -> bool:
    """Phase 2: Fresh API check and submit with idempotency.

    CRITICAL: All safety checks are re-performed at confirm time with fresh data.
    This prevents race conditions where state changes between preview and confirm.

    Refresh strategy at confirm time:
    1. Position - fetch fresh to detect fills between preview and confirm
    2. Price - fetch fresh for accurate impact calculation
    3. Buying power - fetch fresh to ensure sufficient funds
    4. Kill switch - authoritative check (not cached)
    """
    # Re-check connection state at confirm time (not just cached)
    if self._connection_monitor.is_read_only():
        ui.notify("Cannot submit: Connection lost", type="negative")
        return False

    # AUTHORITATIVE kill switch check at confirm time (CRITICAL: safety mechanism)
    # Don't rely on cached state - delegate to OrderEntryContext for authoritative check.
    # OrderTicket does NOT have direct Redis access (by design - single subscription owner).
    if self._verify_kill_switch:
        try:
            is_safe = await self._verify_kill_switch()
            if not is_safe:
                ui.notify("Cannot submit: Kill switch verification failed", type="negative")
                return False
        except Exception as exc:
            # FAIL-CLOSED: If we can't verify kill switch state, block the order
            logger.warning(f"Failed to verify kill switch at confirm: {exc}")
            ui.notify("Cannot submit: Unable to verify kill switch state", type="negative")
            return False
    else:
        # FAIL-CLOSED: No verification callback = assume unsafe
        # This should never happen if OrderEntryContext is properly initialized
        logger.error("Kill switch verification callback not configured - blocking order")
        ui.notify("Cannot submit: Kill switch verification not available", type="negative")
        return False

    # AUTHORITATIVE circuit breaker check at confirm time (CRITICAL: safety mechanism)
    # Don't rely on cached state - delegate to OrderEntryContext for authoritative check.
    # OrderTicket does NOT have direct Redis access (by design - single subscription owner).
    if self._verify_circuit_breaker:
        try:
            is_safe = await self._verify_circuit_breaker()
            if not is_safe:
                ui.notify("Cannot submit: Circuit breaker verification failed", type="negative")
                return False
        except Exception as exc:
            # FAIL-CLOSED: If we can't verify circuit breaker state, block the order
            logger.warning(f"Failed to verify circuit breaker at confirm: {exc}")
            ui.notify("Cannot submit: Unable to verify circuit breaker state", type="negative")
            return False
    else:
        # FAIL-CLOSED: No verification callback = assume unsafe
        logger.warning("No circuit breaker verification callback configured")
        ui.notify("Cannot submit: Circuit breaker verification unavailable", type="negative")
        return False

    # REFRESH AND RE-CHECK POSITION DATA AT CONFIRM TIME
    # Position may have changed due to fills between preview and confirm
    try:
        ui.notify("Verifying position...", type="info", timeout=1000)
        positions_resp = await self._client.fetch_positions(
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
        )
        positions = positions_resp.get("positions", [])

        # CRITICAL: Require server timestamp for fail-closed staleness tracking
        # DO NOT use datetime.now() - that masks stale cached data as fresh
        #
        # API DEPENDENCY: Requires /api/v1/positions to include top-level "timestamp" field
        # FALLBACK: If top-level timestamp absent, use newest position "updated_at"
        # FAIL-CLOSED: If no valid timestamp found, leave timestamp as None (blocks submission)
        # See: API Contract Extension section in Implementation Pre-requisites
        position_timestamp_str = positions_resp.get("timestamp")
        if not position_timestamp_str:
            # FALLBACK: Use newest position updated_at if available
            newest_updated_at = None
            for pos in positions:
                pos_updated = pos.get("updated_at")
                if pos_updated:
                    try:
                        pos_ts = parse_iso_timestamp(str(pos_updated))
                        if newest_updated_at is None or pos_ts > newest_updated_at:
                            newest_updated_at = pos_ts
                    except (ValueError, TypeError):
                        pass  # Skip invalid timestamps

            if newest_updated_at:
                logger.info("Using newest position updated_at as response timestamp (API contract extension pending)")
                position_server_timestamp = newest_updated_at
            else:
                # No timestamp available - fail-closed
                logger.warning("Position response missing timestamp at confirm time")
                self._position_last_updated = None  # Clear staleness on failure
                self._current_total_exposure = None
                ui.notify("Cannot submit: Position timestamp unavailable", type="negative")
                return False
        else:
            # Position response has timestamp - parse it
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                position_server_timestamp = parse_iso_timestamp(position_timestamp_str)
            except (ValueError, TypeError) as exc:
                logger.warning(f"Invalid position timestamp format: {exc}")
                self._position_last_updated = None  # Clear staleness on failure
                self._current_total_exposure = None
                ui.notify("Cannot submit: Invalid position timestamp", type="negative")
                return False

        # Update position for selected symbol
        for pos in positions:
            if pos.get("symbol") == self._state.symbol:
                self._current_position = int(pos.get("qty", 0))
                break
        else:
            self._current_position = 0

        # CRITICAL: Recompute total exposure from fresh positions (confirm-time triple defense)
        # This ensures exposure limit checks use fresh data, not stale cached values
        # FAIL-CLOSED: If ANY position has invalid/missing price, block submission
        total_exposure = Decimal(0)
        exposure_valid = True
        for pos in positions:
            try:
                qty = abs(int(pos.get("qty", 0)))
                if qty == 0:
                    continue  # Zero quantity contributes nothing

                price_str = pos.get("current_price")
                if price_str is None:
                    logger.warning(f"Missing price for position {pos.get('symbol')}")
                    exposure_valid = False
                    break

                price = Decimal(str(price_str))
                # CRITICAL: Reject non-finite values (NaN/Infinity)
                # NaN comparisons return False, so "price <= 0" would pass for NaN
                if not price.is_finite() or price <= 0:
                    logger.warning(f"Invalid/non-finite price {price} for position {pos.get('symbol')}")
                    exposure_valid = False
                    break

                total_exposure += qty * price
            except (ValueError, TypeError, InvalidOperation) as parse_err:
                logger.warning(f"Bad position data for {pos.get('symbol')}: {parse_err}")
                exposure_valid = False
                break

        if not exposure_valid:
            # FAIL-CLOSED: Invalid position data means we can't trust exposure
            self._position_last_updated = None  # Clear staleness on failure
            self._current_total_exposure = None
            ui.notify("Cannot submit: Position data invalid", type="negative")
            return False

        self._current_total_exposure = total_exposure
        # Use server timestamp, NOT datetime.now() - fail-closed staleness tracking
        self._position_last_updated = position_server_timestamp

    except Exception as exc:
        logger.warning(f"Failed to refresh position at confirm: {exc}")
        # FAIL-CLOSED: Clear staleness and set exposure to None so limit checks block
        self._position_last_updated = None
        self._current_total_exposure = None
        ui.notify("Cannot submit: Unable to verify position", type="negative")
        return False

    # Still check staleness after refresh (should pass, but safety check)
    if self._is_position_data_stale():
        ui.notify("Cannot submit: Position data stale", type="negative")
        return False

    # REFRESH AND RE-CHECK PRICE DATA AT CONFIRM TIME
    # Price may have moved significantly between preview and confirm
    # CRITICAL: Must verify symbol found AND price > 0 AND timestamp present/fresh
    try:
        ui.notify("Verifying price...", type="info", timeout=1000)
        prices_resp = await self._client.fetch_market_prices(
            user_id=self._user_id,
            role=self._role,
        )
        price_found = False
        for price_data in prices_resp:
            if price_data.get("symbol") == self._state.symbol:
                # API CONTRACT: MarketPricePoint uses "mid" field (bid/ask midpoint)
                # Fallback to "price" ONLY if "mid" key is absent (not just falsy)
                # Using explicit None check to avoid treating 0 as missing
                mid_price = price_data.get("mid")
                if mid_price is not None:
                    raw_price = mid_price
                else:
                    # "mid" key absent - try "price" as fallback
                    raw_price = price_data.get("price")

                if raw_price is None:
                    logger.warning(f"Missing price field (mid/price) for {self._state.symbol}")
                    self._price_last_updated = None  # Clear staleness on failure
                    ui.notify("Cannot submit: Market price unavailable", type="negative")
                    return False

                fresh_price = Decimal(str(raw_price))
                # SAFETY: Reject zero/negative/non-finite price (fail-closed)
                # CRITICAL: NaN comparisons return False, so explicit is_finite() check required
                if not fresh_price.is_finite() or fresh_price <= 0:
                    logger.warning(f"Invalid/non-finite price {fresh_price} for {self._state.symbol}")
                    self._price_last_updated = None  # Clear staleness on failure
                    ui.notify("Cannot submit: Invalid market price", type="negative")
                    return False

                # SAFETY: Require timestamp from response (fail-closed if missing)
                # DO NOT use datetime.now() - that masks stale cached prices as fresh
                raw_timestamp = price_data.get("timestamp")
                if not raw_timestamp:
                    logger.warning(f"Missing timestamp for {self._state.symbol}")
                    self._price_last_updated = None  # Clear staleness on failure
                    ui.notify("Cannot submit: Price timestamp unavailable", type="negative")
                    return False

                try:
                    price_timestamp = parse_iso_timestamp(raw_timestamp)
                except (ValueError, TypeError) as exc:
                    logger.warning(f"Invalid timestamp format for {self._state.symbol}: {exc}")
                    self._price_last_updated = None  # Clear staleness on failure
                    ui.notify("Cannot submit: Invalid price timestamp", type="negative")
                    return False

                self._last_price = fresh_price
                self._price_last_updated = price_timestamp  # Use response timestamp, NOT now()
                price_found = True
                break

        # SAFETY: Fail-closed if symbol not found in price response
        if not price_found:
            logger.warning(f"Symbol {self._state.symbol} not found in price response")
            self._price_last_updated = None  # Clear staleness on failure
            ui.notify("Cannot submit: Price data unavailable", type="negative")
            return False

    except Exception as exc:
        logger.warning(f"Failed to refresh price at confirm: {exc}")
        self._price_last_updated = None  # Clear staleness on failure
        ui.notify("Cannot submit: Unable to verify market price", type="negative")
        return False

    if self._is_price_data_stale():
        ui.notify("Cannot submit: Price data stale", type="negative")
        return False

    # REFRESH AND RE-CHECK BUYING POWER AT CONFIRM TIME
    try:
        ui.notify("Verifying buying power...", type="info", timeout=1000)
        account_resp = await self._client.fetch_account_info(
            user_id=self._user_id,
            role=self._role,
        )

        # CRITICAL: Require server timestamp for fail-closed staleness tracking
        # NEVER use datetime.now() - that would mask stale data as fresh (FAIL-OPEN)
        #
        # API DEPENDENCY: Requires /api/v1/account to include "timestamp" field
        # Fallback: Try alternate timestamp fields before failing
        # See: API Contract Extension section in Implementation Pre-requisites
        account_timestamp_str = account_resp.get("timestamp")
        account_server_timestamp: datetime | None = None

        if account_timestamp_str:
            try:
                account_server_timestamp = parse_iso_timestamp(account_timestamp_str)
            except (ValueError, TypeError) as exc:
                logger.warning(f"Invalid account timestamp format: {exc}")
        else:
            # FALLBACK: Try alternate timestamp fields (NOT datetime.now())
            for alt_field in ("last_equity_change", "updated_at", "as_of"):
                alt_timestamp = account_resp.get(alt_field)
                if alt_timestamp:
                    try:
                        account_server_timestamp = parse_iso_timestamp(str(alt_timestamp))
                        logger.info(f"Using alternate account timestamp field '{alt_field}' (API contract extension pending)")
                        break
                    except (ValueError, TypeError):
                        continue

        # FAIL-CLOSED: No valid timestamp = block submission
        # NEVER use datetime.now() as fallback - this is a trading safety requirement
        if account_server_timestamp is None:
            logger.warning("Account response missing valid timestamp - blocking submission (FAIL-CLOSED)")
            self._buying_power_last_updated = None  # Clear staleness on failure
            ui.notify("Cannot submit: Account timestamp unavailable", type="negative")
            return False

        # FAIL-CLOSED: Check raw value for None before parsing
        # Distinguish "unavailable" (field missing) from "zero" (insufficient)
        raw_buying_power = account_resp.get("buying_power")
        if raw_buying_power is None:
            self._buying_power = None
            self._buying_power_last_updated = None  # Force staleness
            ui.notify("Cannot submit: Buying power unavailable", type="negative")
            return False

        # Parse buying power with guard for invalid values
        try:
            fresh_buying_power = Decimal(str(raw_buying_power))
            # CRITICAL: Reject non-finite values (NaN/Infinity)
            # NaN comparisons return False, so explicit is_finite() check required
            if not fresh_buying_power.is_finite():
                raise ValueError(f"Non-finite buying power: {fresh_buying_power}")
        except (InvalidOperation, ValueError, TypeError) as exc:
            logger.warning(f"Invalid buying power value: {raw_buying_power!r} - {exc}")
            self._buying_power = None
            self._buying_power_last_updated = None  # Force staleness
            ui.notify("Cannot submit: Invalid buying power data", type="negative")
            return False

        self._buying_power = fresh_buying_power
        # Use server timestamp, NOT datetime.now() - fail-closed staleness tracking
        self._buying_power_last_updated = account_server_timestamp

        # FAIL-CLOSED: Block submission if buying power is zero or negative
        if self._buying_power <= 0:
            ui.notify("Cannot submit: Insufficient buying power (0)", type="negative")
            return False

        # Re-validate order impact with fresh buying power and fresh price
        impact = self._calculate_buying_power_impact()
        # Use `is not None` for percentage comparison (truthiness would skip None)
        if impact["percentage"] is not None and impact["percentage"] > 100:
            ui.notify("Cannot submit: Order exceeds buying power", type="negative")
            return False
    except Exception as exc:
        logger.warning(f"Failed to refresh buying power at confirm: {exc}")
        self._buying_power_last_updated = None  # Clear staleness on failure
        ui.notify("Cannot submit: Unable to verify buying power", type="negative")
        return False

    if self._is_buying_power_stale():
        ui.notify("Cannot submit: Buying power data stale", type="negative")
        return False

    # Re-validate order type prices at confirm time FIRST (triple defense)
    # CRITICAL: Validate prices BEFORE position limits check because limit calculations
    # use _get_effective_order_price() which falls back to last_price when prices are missing.
    # Validating prices first ensures notional calculations use the correct order prices.
    price_error = self._validate_order_type_prices()
    if price_error:
        ui.notify(f"Cannot submit: {price_error}", type="negative")
        return False

    # REFRESH AND RE-CHECK POSITION LIMITS AT CONFIRM TIME
    # Triple defense: fresh limits ensure we catch any limit changes since cached check
    # Uses same atomic commit pattern as _fetch_initial_limits for consistency
    try:
        ui.notify("Verifying position limits...", type="info", timeout=1000)
        limits_resp = await self._client.fetch_risk_limits(
            user_id=self._user_id,
            role=self._role,
            symbol=self._state.symbol,
        )

        # CRITICAL: Require server timestamp for fail-closed staleness tracking
        # DO NOT use datetime.now() - that masks stale cached data as fresh
        limits_timestamp_str = limits_resp.get("timestamp")
        if not limits_timestamp_str:
            logger.warning("Risk limits response missing timestamp at confirm time")
            self._clear_cached_limits()  # Clear stale limits for consistency
            ui.notify("Cannot submit: Limits timestamp unavailable", type="negative")
            return False

        try:
            # Use shared parse_iso_timestamp for consistent tz-aware parsing
            limits_server_timestamp = parse_iso_timestamp(limits_timestamp_str)
        except (ValueError, TypeError) as exc:
            logger.warning(f"Invalid risk limits timestamp format: {exc}")
            self._clear_cached_limits()  # Clear stale limits for consistency
            ui.notify("Cannot submit: Invalid limits timestamp", type="negative")
            return False

        # IMPORTANT: Use `is not None` to preserve 0 as a valid limit (block all trading)
        # Parse into temp vars for atomic commit (same pattern as _fetch_initial_limits)
        try:
            raw_position_limit = limits_resp.get("max_position_per_symbol")
            temp_max_position = (
                int(raw_position_limit)
                if raw_position_limit is not None
                else None
            )
        except (ValueError, TypeError) as exc:
            logger.warning(f"Invalid max_position_per_symbol: {raw_position_limit!r} - {exc}")
            self._clear_cached_limits()  # Clear stale limits for consistency
            ui.notify("Cannot submit: Invalid position limit data", type="negative")
            return False

        try:
            raw_notional_limit = limits_resp.get("max_notional_per_order")
            if raw_notional_limit is not None:
                temp_max_notional = Decimal(str(raw_notional_limit))
                # CRITICAL: Reject non-finite values (NaN/Infinity)
                if not temp_max_notional.is_finite():
                    raise ValueError(f"Non-finite notional limit: {temp_max_notional}")
            else:
                temp_max_notional = None
        except (ValueError, TypeError, InvalidOperation) as exc:
            logger.warning(f"Invalid max_notional_per_order: {limits_resp.get('max_notional_per_order')!r} - {exc}")
            self._clear_cached_limits()  # Clear stale limits for consistency
            ui.notify("Cannot submit: Invalid notional limit data", type="negative")
            return False

        try:
            raw_exposure_limit = limits_resp.get("max_total_exposure")
            if raw_exposure_limit is not None:
                temp_max_exposure = Decimal(str(raw_exposure_limit))
                # CRITICAL: Reject non-finite values (NaN/Infinity)
                if not temp_max_exposure.is_finite():
                    raise ValueError(f"Non-finite exposure limit: {temp_max_exposure}")
            else:
                temp_max_exposure = None
        except (ValueError, TypeError, InvalidOperation) as exc:
            logger.warning(f"Invalid max_total_exposure: {limits_resp.get('max_total_exposure')!r} - {exc}")
            self._clear_cached_limits()  # Clear stale limits for consistency
            ui.notify("Cannot submit: Invalid exposure limit data", type="negative")
            return False

        # All limits parsed successfully - commit atomically
        self._max_position_per_symbol = temp_max_position
        self._max_notional_per_order = temp_max_notional
        self._max_total_exposure = temp_max_exposure
        self._limits_last_updated = limits_server_timestamp
        self._limits_loaded = True

        # Re-validate with fresh limits (prices already validated above)
        limit_violation = self._check_position_limits()
        if limit_violation:
            ui.notify(f"Cannot submit: {limit_violation}", type="negative")
            return False
    except Exception as exc:
        logger.warning(f"Failed to refresh position limits at confirm: {exc}")
        self._clear_cached_limits()  # Clear stale limits for consistency
        ui.notify("Cannot submit: Unable to verify position limits", type="negative")
        return False

    if self._is_limits_stale():
        ui.notify("Cannot submit: Position limits data stale", type="negative")
        return False

    # Fresh kill switch check (authoritative, not cached)
    # FAIL-CLOSED: Require explicit ACTIVE, not just "not ENGAGED"
    # Any missing/unknown/malformed state blocks submission
    # STATE VALUES (matches libs/trading/risk_management/kill_switch.py):
    # - ACTIVE = trading allowed (normal state)
    # - ENGAGED = trading blocked (kill switch engaged)
    try:
        ks_status = await self._client.fetch_kill_switch_status(
            self._user_id, self._role
        )

        # TYPE GUARD: Validate response structure
        if not isinstance(ks_status, dict):
            logger.warning(f"Kill switch response not a dict: {type(ks_status).__name__}")
            ui.notify("Cannot submit: Invalid kill switch response", type="negative")
            return False

        # FAIL-CLOSED: Require explicit ACTIVE, block on any other state
        ks_state = str(ks_status.get("state", "")).upper()
        if ks_state != "ACTIVE":
            # Log the actual state for debugging
            if ks_state == "ENGAGED":
                ui.notify("Cannot submit: Kill switch is ENGAGED", type="negative")
            elif not ks_state:
                ui.notify("Cannot submit: Kill switch state unavailable", type="negative")
            else:
                logger.warning(f"Unknown kill switch state: {ks_state!r}")
                ui.notify("Cannot submit: Kill switch state unknown", type="negative")
            return False
    except Exception as exc:
        logger.warning(f"Failed to verify kill switch at confirm: {exc}")
        ui.notify("Cannot submit: Unable to verify kill switch state", type="negative")
        return False  # Fail-closed on error

    # Validate and normalize symbol before submission
    # Prevents malformed symbols from reaching API (defense in depth with server-side validation)
    try:
        normalized_symbol = validate_and_normalize_symbol(self._state.symbol)
    except ValueError as exc:
        logger.warning(f"Invalid symbol at submit: {self._state.symbol!r} - {exc}")
        ui.notify("Cannot submit: Invalid symbol format", type="negative")
        return False

    # Build order_data dict matching AsyncTradingClient.submit_manual_order signature
    order_data: dict[str, Any] = {
        "symbol": normalized_symbol,
        "side": self._state.side,
        "qty": self._state.quantity,
        "order_type": self._state.order_type,
        "time_in_force": self._state.time_in_force,
        # CRITICAL: Include client_order_id for idempotency
        "client_order_id": self._pending_client_order_id,
    }
    if self._state.limit_price is not None:
        order_data["limit_price"] = str(self._state.limit_price)
    if self._state.stop_price is not None:
        order_data["stop_price"] = str(self._state.stop_price)

    # Final order data validation (triple defense - validate built order before submit)
    # This ensures the order_data dict itself is valid (not just the source state)
    final_price_error = self._validate_order_data_prices(order_data)
    if final_price_error:
        logger.warning(f"Final order data validation failed: {final_price_error}")
        ui.notify(f"Cannot submit: {final_price_error}", type="negative")
        return False

    # FINAL SAFETY CHECKS: Recheck both CB and KS immediately before submit
    # Safety state could have changed during confirm flow processing (race window)
    # This is the last line of defense before the order goes out

    # Final circuit breaker check
    if self._verify_circuit_breaker:
        try:
            final_cb_check = await self._verify_circuit_breaker()
            if not final_cb_check:
                ui.notify("Cannot submit: Circuit breaker tripped during confirmation", type="negative")
                return False
        except Exception as exc:
            logger.warning(f"Final circuit breaker check failed: {exc}")
            ui.notify("Cannot submit: Unable to verify circuit breaker", type="negative")
            return False  # Fail-closed

    # Final kill switch check (mirrors CB pattern for consistency)
    if self._verify_kill_switch:
        try:
            final_ks_check = await self._verify_kill_switch()
            if not final_ks_check:
                ui.notify("Cannot submit: Kill switch engaged during confirmation", type="negative")
                return False
        except Exception as exc:
            logger.warning(f"Final kill switch check failed: {exc}")
            ui.notify("Cannot submit: Unable to verify kill switch", type="negative")
            return False  # Fail-closed

    # FINAL CONNECTION CHECK (right before submit)
    # Connection could transition to read-only after initial check but before submit
    # This is the last gate before irreversible order submission
    if self._connection_monitor.is_read_only():
        logger.warning("Connection went read-only during confirm flow")
        ui.notify("Cannot submit: Connection lost during order preparation", type="negative")
        return False

    # Submit order via execution gateway (uses dict signature)
    response = await self._client.submit_manual_order(
        order_data=order_data,
        user_id=self._user_id,
        role=self._role,
    )

    if response.get("status") in ("pending_new", "new", "accepted"):
        ui.notify(f"Order submitted: {response.get('client_order_id')}", type="positive")
        await self._clear_form()
        # Use tab-scoped key to match _get_or_create_client_order_id
        form_key = f"order_entry:{self._tab_session_id}"
        await self._state_manager.clear_pending_form(form_key)
        return True
    else:
        ui.notify(f"Order failed: {response.get('message', 'Unknown error')}", type="negative")
        return False

def _generate_intent_id(self) -> str:
    """Generate a new unique intent ID for a new order attempt.

    Intent IDs are generated at PREVIEW time, not submit time. This allows:
    - Same order params to be submitted multiple times (different intents)
    - Retries of the same intent to reuse the same client_order_id (idempotent)

    The intent ID is stored in the pending form and reused if the user
    retries after a failed submission without modifying the form.

    Returns:
        32-character UUIDv4 hex string (no dashes).
        UUIDv4 provides sufficient randomness (122 bits) to prevent collisions.
        Alpaca accepts up to 48 characters for client_order_id.
    """
    import uuid
    return uuid.uuid4().hex  # 32-char hex string (no dashes)

async def _get_or_create_client_order_id(self) -> str:
    """Get existing intent ID or create new one for idempotent submission.

    INTENT MODEL:
    - On first preview: generate new intent ID, store in pending form
    - On re-preview with unchanged form: reuse stored intent ID (idempotent retry)
    - On form change: clear stored intent ID, will generate new on next preview

    CROSS-TAB SAFETY:
    - Pending form is scoped to tab/session ID (not just user ID)
    - This prevents two tabs with matching form state from sharing intent IDs
    - Each tab gets its own pending form entry, allowing parallel order workflows

    This allows:
    - User to submit identical orders intentionally (each tab gets new ID)
    - Retries of same intent within same tab to be idempotent
    """
    # Scope pending form to this tab/session to prevent cross-tab intent sharing
    # _tab_session_id is generated once per OrderTicketComponent instance
    form_key = f"order_entry:{self._tab_session_id}"

    # Check if we have a pending intent for the current form state in THIS TAB
    # SCHEMA: pending_forms[form_key]["form_data"] = {client_order_id, symbol, side, ...}
    # This matches the schema used by _save_pending_form and _restore_pending_form
    pending = await self._state_manager.restore_state()
    pending_entry = pending.get("pending_forms", {}).get(form_key, {})
    pending_form = pending_entry.get("form_data", {})

    # If we have a stored intent and form params match, reuse it
    stored_intent = pending_form.get("client_order_id")
    if stored_intent:
        # Verify form state matches (prevents using old intent for new params)
        if (pending_form.get("symbol") == self._state.symbol
            and pending_form.get("side") == self._state.side
            and pending_form.get("quantity") == self._state.quantity
            and pending_form.get("order_type") == self._state.order_type
            and pending_form.get("limit_price") == str(self._state.limit_price or "")
            and pending_form.get("stop_price") == str(self._state.stop_price or "")):
            return stored_intent

    # Generate new intent ID for this order attempt
    new_intent = self._generate_intent_id()

    # Store in pending form for potential retry (scoped to this tab)
    # Uses form_id and form_data to match the schema expected by _restore_pending_form
    await self._state_manager.save_pending_form(
        form_id=form_key,
        form_data={
            "client_order_id": new_intent,
            "symbol": self._state.symbol,
            "side": self._state.side,
            "quantity": self._state.quantity,
            "order_type": self._state.order_type,
            "limit_price": str(self._state.limit_price or ""),
            "stop_price": str(self._state.stop_price or ""),
        },
    )

    return new_intent
```

##### 4.1.2.1 UI Disable Behavior (Connection/Kill Switch/Circuit Breaker)

```python
# UI-level disabling when trading is not allowed
# Driven by real-time subscription updates via CALLBACKS from OrderEntryContext.
#
# SUBSCRIPTION OWNERSHIP: OrderEntryContext owns ALL subscriptions.
# OrderTicket does NOT subscribe directly to any Redis channels.
# Instead, it receives state updates via set_* callback methods:
#   - set_connection_state() - called when connection:state changes
#   - set_kill_switch_state() - called when kill_switch:state changes
#   - set_circuit_breaker_state() - called when circuit_breaker:state changes
#
# This pattern:
#   - Avoids duplicate subscriptions across components
#   - Centralizes lifecycle management in OrderEntryContext
#   - Ensures proper cleanup on dispose

def set_connection_state(self, state: str, is_read_only: bool) -> None:
    """Called by OrderEntryContext when connection state changes.

    This is the ONLY way OrderTicket receives connection state updates.
    OrderTicket does NOT subscribe directly to connection:state channel.

    SECURITY: State strings come from pub/sub (untrusted) and must be sanitized.
    """
    if is_read_only:
        # SECURITY: Sanitize state from pub/sub payload (untrusted input)
        # Normalize to uppercase first, then map to known safe states
        # (consistent uppercase casing for all states)
        normalized_state = str(state).upper()
        KNOWN_STATES = {"CONNECTED", "DISCONNECTED", "RECONNECTING", "DEGRADED", "UNKNOWN"}
        safe_state = normalized_state if normalized_state in KNOWN_STATES else "UNKNOWN"
        self._set_ui_disabled(True, f"Connection: {safe_state}")
    else:
        # Only re-enable if ALL safety conditions pass (fail-closed)
        if not self._kill_switch_engaged and not self._circuit_breaker_tripped:
            self._set_ui_disabled(False, "")

def set_kill_switch_state(self, engaged: bool, reason: str | None) -> None:
    """Called by OrderEntryContext when kill switch state changes.

    This is the ONLY way OrderTicket receives kill switch state updates.
    OrderTicket does NOT subscribe directly to kill_switch:state channel.

    SECURITY: Reason strings come from Redis (untrusted) and must be sanitized
    before display. We use html.escape() for defense-in-depth even though
    NiceGUI's .text property should render as plain text.
    """
    self._kill_switch_engaged = engaged
    self._safety_state_loaded = True  # Mark safety state as loaded
    if engaged:
        # SECURITY: Sanitize reason from Redis (untrusted input)
        safe_reason = html.escape(reason or "Trading halted")
        self._set_ui_disabled(True, f"Kill switch: {safe_reason}")
    else:
        if not self._connection_monitor.is_read_only() and not self._circuit_breaker_tripped:
            self._set_ui_disabled(False, "")

def set_circuit_breaker_state(self, tripped: bool, reason: str | None) -> None:
    """Called by OrderEntryContext when circuit breaker state changes.

    This is the ONLY way OrderTicket receives circuit breaker state updates.
    OrderTicket does NOT subscribe directly to circuit_breaker:state channel.

    CRITICAL: Circuit breaker is a global safety mechanism that halts ALL trading
    when market conditions are dangerous (drawdown breach, broker errors, data staleness).
    Unlike kill switch (manual), circuit breaker is automatic.

    SECURITY: Reason strings come from Redis (untrusted) and must be sanitized.
    """
    self._circuit_breaker_tripped = tripped
    if tripped:
        # SECURITY: Sanitize reason from Redis (untrusted input)
        safe_reason = html.escape(reason or "Trading halted")
        self._set_ui_disabled(True, f"Circuit breaker: {safe_reason}")
    else:
        # Only re-enable if no other blocking conditions
        if not self._connection_monitor.is_read_only() and not self._kill_switch_engaged:
            self._set_ui_disabled(False, "")

def set_price_data(self, symbol: str, price: Decimal | None, timestamp: datetime | None) -> None:
    """Called by OrderEntryContext when price data updates.

    This is the ONLY way OrderTicket receives real-time price updates.
    OrderTicket does NOT subscribe directly to price.updated.{symbol} channel.

    CRITICAL: Price updates drive:
    - Buying power impact calculation
    - MAX quantity preset calculation
    - Order preview validation

    Data Flow:
        Redis price.updated.{symbol} → RealtimeUpdater → OrderEntryContext
        → OrderTicket.set_price_data() → _update_buying_power_impact()

    Args:
        symbol: The symbol for this price update.
        price: The new price, or None to indicate invalid price data.
               When None, _last_price is CLEARED to show staleness in UI.
        timestamp: Server timestamp, or None if timestamp unavailable.
                   If None, _price_last_updated is cleared to force staleness.

    FAIL-CLOSED: When price or timestamp is None, we CLEAR both _last_price and
    _price_last_updated. This ensures:
    - UI shows stale indicator (not old price)
    - Impact calculations don't use stale values
    - Staleness checks fail, preventing submission on unverifiable data.
    """
    # Only update if symbol matches selected symbol
    if symbol != self._state.symbol:
        return

    # FAIL-CLOSED: Update price - clear to None when price is invalid/missing
    # This ensures UI shows stale indicator and impact calculations don't use old values
    self._last_price = price  # Always update (valid Decimal or None)

    # FAIL-CLOSED: Update timestamp (valid or None)
    # If timestamp is None, this clears it and forces staleness check to fail
    self._price_last_updated = timestamp

    # Recalculate buying power impact with fresh price
    self._update_buying_power_impact()

    # Update quantity presets MAX calculation
    # CRITICAL: Preserve full context (limits, position, side) to avoid losing limit-aware MAX
    if self._quantity_presets:
        self._quantity_presets.update_context(
            buying_power=self._buying_power,
            current_price=price,
            current_position=self._current_position,
            max_position_per_symbol=self._max_position_per_symbol,
            max_notional_per_order=self._max_notional_per_order,
            side=self._state.side,
            effective_price=self._state.limit_price or self._state.stop_price,
        )

async def on_symbol_changed(self, symbol: str | None) -> None:
    """Called by OrderEntryContext when selected symbol changes.

    This updates the order ticket's current symbol and triggers
    fresh data fetch for the new symbol.

    CRITICAL: Must reset ALL symbol-scoped state (price, position, timestamps)
    to prevent stale data from previous symbol being used for new symbol.

    RACE PREVENTION: After async operations, validate symbol is still current
    before updating state/UI to prevent stale fetches from overwriting newer data.
    """
    self._state.symbol = symbol

    # CRITICAL: Reset ALL symbol-scoped state to prevent stale data from previous symbol
    # Without this, staleness checks would pass with old timestamps for new symbol
    self._last_price = None
    self._price_last_updated = None
    self._current_position = 0
    self._position_last_updated = None

    # CRITICAL: Reset ALL limits state BEFORE fetching new symbol limits
    # Prevents stale limits from previous symbol from being used if fetch fails
    self._limits_loaded = False
    self._limits_last_updated = None
    self._max_position_per_symbol = None
    self._max_notional_per_order = None
    self._max_total_exposure = None

    if symbol:
        # Fetch fresh position and buying power for the new symbol
        await self._fetch_position_and_buying_power()

        # RACE CHECK: Ensure symbol hasn't changed during async fetch
        # This prevents stale fetch results from overwriting newer selections
        if symbol != self._state.symbol:
            return  # Stale - symbol changed during fetch

        # Fetch initial risk limits for the new symbol (required for UI gating)
        # CRITICAL: Without this, _limits_loaded remains False and submission is blocked
        # The confirm-time fetch is authoritative; this is the cached layer of triple-defense
        await self._fetch_initial_limits(symbol)

        # RACE CHECK: Symbol may have changed during limits fetch
        if symbol != self._state.symbol:
            return  # Stale - symbol changed during fetch

        self._update_ui_from_state()
    else:
        # Clear form if no symbol selected
        # Limits already reset above - just update UI
        self._update_ui_from_state()

def set_position_data(self, symbol: str, qty: int, timestamp: datetime | None) -> None:
    """Called by OrderEntryContext when position data updates.

    This is the ONLY way OrderTicket receives real-time position updates.
    OrderTicket does NOT subscribe directly to positions:{user_id} channel.

    Data Flow:
        Redis positions:{user_id} → RealtimeUpdater → OrderEntryContext
        → OrderTicket.set_position_data() → UI update

    Args:
        symbol: The symbol for this position update.
        qty: The position quantity.
        timestamp: Server timestamp, or None if timestamp unavailable.
                   If None, _position_last_updated is cleared to force staleness.

    FAIL-CLOSED: When timestamp is None, we CLEAR _position_last_updated to None.
    This forces staleness checks to fail, preventing submission on unverifiable data.
    We still update the position value for display purposes.
    """
    # Only update if symbol matches selected symbol
    if symbol != self._state.symbol:
        return

    # Always update position value (data is valid even if timestamp unknown)
    self._current_position = qty

    # FAIL-CLOSED: Update timestamp (valid or None)
    # If timestamp is None, this clears it and forces staleness check to fail
    self._position_last_updated = timestamp

    # Update position display
    self._update_position_display()

# NOTE: _on_kill_switch_change() method has been REMOVED.
# OrderTicket does NOT subscribe to Redis channels.
# Kill switch state is received via set_kill_switch_state() callback from OrderEntryContext.
# See set_kill_switch_state() above for the callback-based implementation.

def _set_ui_disabled(self, disabled: bool, reason: str) -> None:
    """Set UI elements to disabled state with reason display.

    When disabled:
    - Submit button disabled + grayed out
    - Quantity input disabled
    - Side toggle disabled
    - Reason banner displayed at top of order ticket
    - Hotkeys for submit disabled (B/S keys still work for side selection)
    """
    if self._submit_button:
        self._submit_button.set_enabled(not disabled)

    if self._quantity_input:
        self._quantity_input.set_enabled(not disabled)

    if self._side_toggle:
        self._side_toggle.set_enabled(not disabled)

    # Show/hide reason banner
    if disabled and reason:
        self._show_disabled_banner(reason)
    else:
        self._hide_disabled_banner()

def _show_disabled_banner(self, reason: str) -> None:
    """Display prominent banner explaining why trading is disabled."""
    if self._disabled_banner:
        self._disabled_banner.text = reason
        self._disabled_banner.classes(remove="hidden")
    # CSS: bg-red-900 text-white p-2 rounded text-center font-bold

def _hide_disabled_banner(self) -> None:
    """Hide the disabled reason banner."""
    if self._disabled_banner:
        self._disabled_banner.classes(add="hidden")
```

##### 4.1.3 Hotkey Integration (P6T3.2 Dependency)

```python
# Hotkey handler registration for B/S keys

def _register_hotkeys(self, hotkey_manager: HotkeyManager) -> None:
    """Register order ticket hotkey handlers."""

    hotkey_manager.register_handler("focus_buy", self._set_side_buy)
    hotkey_manager.register_handler("focus_sell", self._set_side_sell)
    hotkey_manager.register_handler("submit_order", self._trigger_preview)
    hotkey_manager.register_handler("cancel_form", self._clear_form)

def _set_side_buy(self) -> None:
    """Set side to BUY (triggered by 'B' key)."""
    self._state.side = "buy"
    if self._side_toggle:
        self._side_toggle.value = "buy"
    self._update_buying_power_impact()

def _set_side_sell(self) -> None:
    """Set side to SELL (triggered by 'S' key)."""
    self._state.side = "sell"
    if self._side_toggle:
        self._side_toggle.value = "sell"
    self._update_buying_power_impact()
```

##### 4.1.4 Quantity Presets Component

```python
# apps/web_console_ng/components/quantity_presets.py

class QuantityPresetsComponent:
    """One-click quantity preset buttons with MAX calculation."""

    DEFAULT_PRESETS = [100, 500, 1000]

    def __init__(
        self,
        on_preset_selected: Callable[[int], None],
        presets: list[int] | None = None,
    ):
        self._on_preset_selected = on_preset_selected
        self._presets = presets or self.DEFAULT_PRESETS
        self._max_button: ui.button | None = None
        self._buying_power: Decimal | None = None
        self._current_price: Decimal | None = None

        # Position limit context for MAX calculation
        self._current_position: int = 0
        self._max_position_per_symbol: int | None = None
        self._max_notional_per_order: Decimal | None = None
        self._side: str = "buy"  # Order side affects position limit calculation
        self._effective_price: Decimal | None = None  # Limit/stop price for non-market orders

    def create(self) -> ui.row:
        """Create preset buttons row."""
        with ui.row().classes("gap-2 items-center") as row:
            for preset in self._presets:
                ui.button(
                    str(preset),
                    on_click=lambda p=preset: self._on_preset_selected(p),
                ).classes("w-16 h-8 text-sm")

            # MAX button with dynamic calculation
            self._max_button = ui.button(
                "MAX",
                on_click=self._calculate_and_select_max,
            ).classes("w-16 h-8 text-sm bg-blue-600")

        return row

    def _calculate_and_select_max(self) -> None:
        """Calculate max affordable quantity based on buying power AND position limits.

        CRITICAL: MAX must respect both buying power and risk limits.
        Uses the most restrictive of all applicable limits.

        NOTE: For limit/stop orders, uses effective_price (the order price) instead
        of current_price for more accurate calculations. This prevents showing MAX
        values that would exceed limits when the order price differs from market price.
        """
        # Use effective price (limit/stop) if available, otherwise market price
        calc_price = self._effective_price or self._current_price

        # Use `is None` checks to handle 0 correctly
        # 0 buying power is a valid state (no trading allowed), not "unavailable"
        if calc_price is None:
            ui.notify("Cannot calculate MAX: price unavailable", type="warning")
            return

        if self._buying_power is None:
            ui.notify("Cannot calculate MAX: buying power unavailable", type="warning")
            return

        if self._buying_power <= 0:
            ui.notify("Insufficient buying power (0)", type="warning")
            return

        if calc_price <= 0:
            return

        # Calculate max by buying power (using effective price)
        max_by_buying_power = int(self._buying_power // calc_price)

        # Calculate max by per-symbol position limit (if configured)
        max_by_position_limit: int | None = None
        if self._max_position_per_symbol is not None:
            if self._side == "buy":
                # Buying: max = limit - current_position
                max_by_position_limit = self._max_position_per_symbol - self._current_position
            else:
                # Selling: max = limit + current_position (can go short up to limit)
                max_by_position_limit = self._max_position_per_symbol + self._current_position
            max_by_position_limit = max(0, max_by_position_limit)  # Can't be negative

        # Calculate max by per-order notional limit (using effective price)
        max_by_notional: int | None = None
        if self._max_notional_per_order is not None:
            max_by_notional = int(self._max_notional_per_order // calc_price)

        # Use the most restrictive limit (minimum of all applicable limits)
        applicable_limits = [max_by_buying_power]
        if max_by_position_limit is not None:
            applicable_limits.append(max_by_position_limit)
        if max_by_notional is not None:
            applicable_limits.append(max_by_notional)

        max_qty = min(applicable_limits)

        # Apply safety margin (95% to avoid edge cases)
        safe_max = int(max_qty * Decimal("0.95"))

        if safe_max > 0:
            self._on_preset_selected(safe_max)
        else:
            # Provide specific feedback on which limit is the constraint
            if max_by_position_limit is not None and max_by_position_limit == 0:
                ui.notify("Position limit reached", type="warning")
            elif max_by_notional is not None and max_by_notional == 0:
                ui.notify("Order notional limit reached", type="warning")
            else:
                ui.notify("Insufficient buying power", type="warning")

    def update_context(
        self,
        buying_power: Decimal | None,
        current_price: Decimal | None,
        current_position: int = 0,
        max_position_per_symbol: int | None = None,
        max_notional_per_order: Decimal | None = None,
        side: str = "buy",
        effective_price: Decimal | None = None,
    ) -> None:
        """Update context for MAX calculation.

        Args:
            buying_power: Available buying power in dollars.
            current_price: Current market price of selected symbol.
            current_position: Current position in selected symbol (shares).
            max_position_per_symbol: Maximum allowed position per symbol (shares).
            max_notional_per_order: Maximum allowed notional per order ($).
            side: Order side ('buy' or 'sell') - affects position limit calc.
            effective_price: Limit/stop price for non-market orders. If provided,
                used for buying power and notional calculations instead of current_price.
        """
        self._buying_power = buying_power
        self._current_price = current_price
        self._current_position = current_position
        self._effective_price = effective_price
        self._max_position_per_symbol = max_position_per_symbol
        self._max_notional_per_order = max_notional_per_order
        self._side = side
```

##### 4.1.5 Buying Power Impact Calculation

```python
def _calculate_buying_power_impact(self) -> dict[str, Any]:
    """Calculate order's impact on buying power.

    CRITICAL: Uses _get_effective_order_price() for consistent price calculation
    across all order types (limit, stop, stop_limit, market). This ensures stop
    orders are properly accounted for with conservative gap-up estimates.
    """
    if not self._state.quantity:
        return {
            "notional": None,
            "percentage": None,
            "remaining": None,
            "warning": False,
        }

    # Use effective price (accounts for stop orders with conservative estimates)
    effective_price = self._get_effective_order_price()
    # Use `is None` for consistency; price <= 0 is validated elsewhere
    if effective_price is None:
        return {
            "notional": None,
            "percentage": None,
            "remaining": None,
            "warning": False,
        }

    # Calculate notional value using effective price
    notional = effective_price * Decimal(self._state.quantity)

    # Use `is None` to distinguish "unavailable" from "zero" buying power
    # Both mean we can't trade, but messages should be clearer
    if self._buying_power is None or self._buying_power <= 0:
        return {
            "notional": notional,
            "percentage": None,
            "remaining": None,
            "warning": True,  # Either unavailable or insufficient
        }

    # Calculate percentage of buying power
    percentage = (notional / self._buying_power) * 100
    remaining = self._buying_power - notional

    return {
        "notional": notional,
        "percentage": percentage,
        "remaining": remaining,
        "warning": percentage > 50,  # Warn if >50% of buying power
    }
```

##### 4.1.6 Form Recovery Pattern (Idempotency)

```python
async def _save_pending_form(self, client_order_id: str) -> None:
    """Save form state for recovery with pre-generated client_order_id.

    CRITICAL: client_order_id is generated BEFORE saving, enabling idempotent
    resubmission on browser crash/reconnect. The same order won't be submitted
    twice even if user clicks submit and browser crashes before confirmation.

    NOTE: Form key is scoped to tab/session ID to match _get_or_create_client_order_id.
    This ensures cross-tab isolation of pending forms.
    """
    # Use tab-scoped key to match _get_or_create_client_order_id
    form_key = f"order_entry:{self._tab_session_id}"
    await self._state_manager.save_pending_form(
        form_id=form_key,
        form_data={
            "symbol": self._state.symbol,
            "side": self._state.side,
            "quantity": self._state.quantity,
            "order_type": self._state.order_type,
            "limit_price": str(self._state.limit_price) if self._state.limit_price else None,
            "stop_price": str(self._state.stop_price) if self._state.stop_price else None,
            "time_in_force": self._state.time_in_force,
            "client_order_id": client_order_id,  # Include for recovery
        },
    )

async def _restore_pending_form(self) -> None:
    """Restore form state after reconnection.

    NOTE: Uses tab-scoped key to match _get_or_create_client_order_id.
    Only restores form for THIS tab's session, not other tabs.
    """
    # Use tab-scoped key to match _get_or_create_client_order_id
    form_key = f"order_entry:{self._tab_session_id}"
    state = await self._state_manager.restore_state()
    pending = state.get("pending_forms", {}).get(form_key)

    if pending:
        form_data = pending.get("form_data", {})

        # SAFE DECIMAL PARSING: Corrupted Redis data should not crash restore
        # On any parse failure, clear the pending form and start fresh
        def safe_parse_decimal(key: str) -> Decimal | None:
            raw = form_data.get(key)
            if raw is None:
                return None
            try:
                dec = Decimal(str(raw))
                if not dec.is_finite():
                    return None
                return dec
            except (InvalidOperation, ValueError, TypeError):
                return None

        # SAFE FIELD PARSING: Validate all fields to prevent corrupted data from causing errors
        def safe_parse_int(key: str) -> int | None:
            raw = form_data.get(key)
            if raw is None:
                return None
            try:
                val = int(raw)
                return val if val > 0 else None
            except (ValueError, TypeError):
                return None

        def safe_parse_enum(key: str, allowed: set[str], default: str) -> str:
            raw = form_data.get(key, default)
            return raw if raw in allowed else default

        def safe_parse_symbol(key: str) -> str | None:
            raw = form_data.get(key)
            if not raw or not isinstance(raw, str):
                return None
            # Use validate_and_normalize_symbol for consistent validation
            # (allows dots/hyphens like BRK.B, matches rest of codebase)
            try:
                return validate_and_normalize_symbol(raw)
            except ValueError:
                return None

        try:
            limit_price = safe_parse_decimal("limit_price")
            stop_price = safe_parse_decimal("stop_price")

            # Validate all form fields (fail-closed: invalid = clear)
            validated_symbol = safe_parse_symbol("symbol")
            validated_side = safe_parse_enum("side", {"buy", "sell"}, "buy")
            validated_quantity = safe_parse_int("quantity")
            validated_order_type = safe_parse_enum("order_type", {"market", "limit", "stop", "stop_limit"}, "market")
            validated_tif = safe_parse_enum("time_in_force", {"day", "gtc", "ioc", "fok"}, "day")

            self._state = OrderTicketState(
                symbol=validated_symbol,
                side=validated_side,
                quantity=validated_quantity,
                order_type=validated_order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=validated_tif,
            )
            # Restore client_order_id for idempotent resubmission
            self._pending_client_order_id = form_data.get("client_order_id")
            self._update_ui_from_state()
            ui.notify("Order form restored from previous session", type="info")
        except Exception as exc:
            # Corrupted form data - clear and start fresh
            logger.warning(f"Failed to restore pending form: {exc}")
            await self._clear_pending_form()
            # Reset to clean state
            self._state = OrderTicketState()
            self._pending_client_order_id = None
```

##### 4.1.7 Position and Buying Power Data Sources

```python
# Data Sources and Update Strategy
#
# Position Data:
#   - Source: fetch_positions() -> /api/v1/positions endpoint
#   - Update: Real-time via RealtimeUpdater (positions:{user_id} channel)
#   - Fallback: Periodic polling every 5s when WebSocket unavailable
#   - Staleness: Block submission if > 30s old
#
# Buying Power:
#   - Source: fetch_account_info() -> /api/v1/account endpoint
#   - Update: Periodic polling every 10s (no WebSocket channel)
#   - Staleness: Block submission if > 60s old
#
# Market Price:
#   - Source: RealtimeUpdater (price.updated.{symbol} channel)
#   - Fallback: fetch_positions() current_price field
#   - Staleness: Block submission if > 30s old

async def _fetch_position_and_buying_power(self) -> None:
    """Fetch fresh position and buying power data.

    FAIL-CLOSED TIMESTAMP POLICY: Requires server timestamps from responses
    for accurate staleness tracking. If timestamp is missing or invalid,
    the _last_updated field is NOT updated - data remains marked as stale.
    This ensures staleness checks block order submission when timestamp
    validation fails.
    """

    # Fetch positions (includes current position for selected symbol)
    try:
        positions_resp = await self._client.fetch_positions(
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
        )
        positions = positions_resp.get("positions", [])

        # Parse server timestamp for staleness tracking
        # FAIL-CLOSED: If timestamp missing/invalid, do NOT update last_updated
        # This keeps data marked as stale rather than falsely marking it fresh
        #
        # API DEPENDENCY: Requires /api/v1/positions to include top-level "timestamp" field
        # Fallback: Try newest position updated_at if top-level timestamp absent
        position_timestamp_str = positions_resp.get("timestamp")
        position_server_timestamp: datetime | None = None
        if position_timestamp_str:
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                position_server_timestamp = parse_iso_timestamp(position_timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in position response, trying fallback")

        if position_server_timestamp is None:
            # FALLBACK: Use newest position updated_at if available (NOT datetime.now())
            newest_updated_at = None
            for pos in positions:
                updated_str = pos.get("updated_at")
                if updated_str:
                    try:
                        pos_timestamp = parse_iso_timestamp(str(updated_str))
                        if newest_updated_at is None or pos_timestamp > newest_updated_at:
                            newest_updated_at = pos_timestamp
                    except (ValueError, TypeError):
                        continue
            if newest_updated_at:
                position_server_timestamp = newest_updated_at
                logger.info("Using newest position updated_at as timestamp fallback")
            else:
                logger.warning("Position response missing timestamp, data remains stale")

        # Extract position for selected symbol
        if self._state.symbol:
            for pos in positions:
                if pos.get("symbol") == self._state.symbol:
                    self._current_position = int(pos.get("qty", 0))
                    break
            else:
                self._current_position = 0  # No position

        # Compute total portfolio exposure (sum of absolute notional values)
        # This is used for max_total_exposure limit checks
        # FAIL-CLOSED: If ANY position has invalid/missing price, we cannot trust the total
        total_exposure = Decimal(0)
        exposure_valid = True
        for pos in positions:
            try:
                qty = abs(int(pos.get("qty", 0)))
                if qty == 0:
                    continue  # Zero quantity contributes nothing to exposure

                price_str = pos.get("current_price")
                if price_str is None:
                    # Missing price for non-zero position = can't calculate exposure
                    logger.warning(f"Missing price for position {pos.get('symbol')}")
                    exposure_valid = False
                    break

                price = Decimal(str(price_str))
                if not price.is_finite() or price <= 0:
                    # Invalid/non-finite price for non-zero position = can't trust exposure
                    # is_finite() rejects NaN/Infinity which would contaminate total_exposure
                    logger.warning(f"Invalid price {price} for position {pos.get('symbol')}")
                    exposure_valid = False
                    break

                total_exposure += qty * price
            except (ValueError, TypeError, InvalidOperation) as parse_err:
                logger.warning(f"Bad position data for {pos.get('symbol')}: {parse_err}")
                exposure_valid = False
                break  # FAIL-CLOSED: Don't continue with invalid data

        if exposure_valid:
            self._current_total_exposure = total_exposure
            # FAIL-CLOSED: Update timestamp (valid or None)
            # If timestamp is None, this clears it and forces staleness check to fail
            self._position_last_updated = position_server_timestamp
        else:
            # FAIL-CLOSED: Invalid position data means we can't trust exposure
            self._current_total_exposure = None
            # Clear timestamp to force staleness check to fail
            self._position_last_updated = None
            # Don't update timestamp - data is invalid
    except Exception as exc:
        logger.warning(f"Failed to fetch positions: {exc}")
        # FAIL-CLOSED: Set exposure to None so limit checks block
        self._current_total_exposure = None
        # FAIL-CLOSED: Clear timestamp to force staleness check to fail
        # (violates "missing/invalid clears _last_updated" if we leave it as-is)
        self._position_last_updated = None

    # Fetch buying power (separate API call)
    try:
        account_resp = await self._client.fetch_account_info(
            user_id=self._user_id,
            role=self._role,
        )

        # Parse server timestamp for staleness tracking
        # FAIL-CLOSED: If timestamp missing/invalid, do NOT update last_updated
        #
        # API DEPENDENCY: Requires /api/v1/account to include "timestamp" field
        # Fallback: Try alternate timestamp fields before leaving stale
        account_timestamp_str = account_resp.get("timestamp")
        account_server_timestamp: datetime | None = None
        if account_timestamp_str:
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                account_server_timestamp = parse_iso_timestamp(account_timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in account response, trying fallback")

        if account_server_timestamp is None:
            # FALLBACK: Try alternate timestamp fields (NOT datetime.now())
            for alt_field in ("last_equity_change", "updated_at", "as_of"):
                alt_timestamp = account_resp.get(alt_field)
                if alt_timestamp:
                    try:
                        account_server_timestamp = parse_iso_timestamp(str(alt_timestamp))
                        logger.info(f"Using alternate account timestamp field '{alt_field}'")
                        break
                    except (ValueError, TypeError):
                        continue

            if account_server_timestamp is None:
                logger.warning("Account response missing timestamp, data remains stale")

        # FAIL-CLOSED: Check raw value for None before parsing (matches confirm-time pattern)
        raw_buying_power = account_resp.get("buying_power")
        if raw_buying_power is None:
            # Missing = unavailable, different from 0 (insufficient)
            self._buying_power = None
            self._buying_power_last_updated = None  # Force staleness
            logger.warning("Buying power field missing in account response")
        else:
            try:
                parsed_buying_power = Decimal(str(raw_buying_power))
                if not parsed_buying_power.is_finite():
                    # NaN/Infinity cannot be used for buying power comparisons
                    raise ValueError(f"Non-finite buying power: {parsed_buying_power}")
                self._buying_power = parsed_buying_power
            except (InvalidOperation, ValueError, TypeError) as exc:
                logger.warning(f"Invalid buying power value: {raw_buying_power!r} - {exc}")
                self._buying_power = None  # Treat as unavailable on parse error
                self._buying_power_last_updated = None  # Force staleness
                return
            # FAIL-CLOSED: Update timestamp (valid or None)
            # If timestamp is None, this clears it and forces staleness check to fail
            self._buying_power_last_updated = account_server_timestamp
    except Exception as exc:
        logger.warning(f"Failed to fetch account info: {exc}")
        # FAIL-CLOSED: Clear both value and timestamp to force staleness
        self._buying_power = None
        self._buying_power_last_updated = None

def _clear_cached_limits(self) -> None:
    """Clear all cached limit fields.

    Called on any failure during limits fetch to ensure no stale/partial limits remain.
    This is CRITICAL for fail-closed safety - stale limits from previous symbol
    must never be used for the current symbol.
    """
    self._limits_loaded = False
    self._limits_last_updated = None
    self._max_position_per_symbol = None
    self._max_notional_per_order = None
    self._max_total_exposure = None

async def _fetch_initial_limits(self, symbol: str) -> None:
    """Fetch initial risk limits for a symbol.

    Called on symbol selection to populate cached limits layer.
    The confirm-time fetch is authoritative; this enables the UI gating layer.

    FAIL-CLOSED: If fetch fails or timestamp is invalid, _limits_loaded remains False
    and submission is blocked. This is the intended fail-closed behavior.

    CRITICAL: On any failure (timestamp, parsing, network), ALL cached limits are cleared
    to prevent partial state where some limits are set and others are None/stale.

    Args:
        symbol: The selected symbol for symbol-specific limits.
    """
    try:
        limits_resp = await self._client.fetch_risk_limits(
            user_id=self._user_id,
            role=self._role,
            symbol=symbol,
        )

        # Parse server timestamp for staleness tracking (fail-closed if missing/invalid)
        limits_timestamp_str = limits_resp.get("timestamp")
        limits_server_timestamp: datetime | None = None
        if limits_timestamp_str:
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                limits_server_timestamp = parse_iso_timestamp(limits_timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in limits response, leaving limits unloaded")
                self._clear_cached_limits()  # Ensure clean state
                return
        else:
            logger.warning("Limits response missing timestamp, leaving limits unloaded")
            self._clear_cached_limits()  # Ensure clean state
            return

        # Parse limits with `is not None` to preserve 0 as valid limit
        # Use temporary variables to ensure atomic commit (all or nothing)
        try:
            raw_position_limit = limits_resp.get("max_position_per_symbol")
            temp_max_position = (
                int(raw_position_limit)
                if raw_position_limit is not None
                else None
            )
        except (ValueError, TypeError) as exc:
            logger.warning(f"Invalid max_position_per_symbol: {raw_position_limit!r} - {exc}")
            self._clear_cached_limits()  # Ensure clean state
            return

        try:
            raw_notional_limit = limits_resp.get("max_notional_per_order")
            if raw_notional_limit is not None:
                temp_max_notional = Decimal(str(raw_notional_limit))
                if not temp_max_notional.is_finite():
                    # NaN/Infinity cannot be used for limit comparisons
                    raise ValueError(f"Non-finite max_notional_per_order: {temp_max_notional}")
            else:
                temp_max_notional = None
        except (ValueError, TypeError, InvalidOperation) as exc:
            logger.warning(f"Invalid max_notional_per_order: {raw_notional_limit!r} - {exc}")
            self._clear_cached_limits()  # Ensure clean state
            return

        try:
            raw_exposure_limit = limits_resp.get("max_total_exposure")
            if raw_exposure_limit is not None:
                temp_max_exposure = Decimal(str(raw_exposure_limit))
                if not temp_max_exposure.is_finite():
                    # NaN/Infinity cannot be used for limit comparisons
                    raise ValueError(f"Non-finite max_total_exposure: {temp_max_exposure}")
            else:
                temp_max_exposure = None
        except (ValueError, TypeError, InvalidOperation) as exc:
            logger.warning(f"Invalid max_total_exposure: {raw_exposure_limit!r} - {exc}")
            self._clear_cached_limits()  # Ensure clean state
            return

        # All limits parsed successfully - commit atomically
        self._max_position_per_symbol = temp_max_position
        self._max_notional_per_order = temp_max_notional
        self._max_total_exposure = temp_max_exposure
        self._limits_last_updated = limits_server_timestamp
        self._limits_loaded = True

    except Exception as exc:
        logger.warning(f"Failed to fetch initial limits: {exc}")
        self._clear_cached_limits()  # Ensure clean state on network/API failure

def _start_data_refresh_timers(self, tracker: Callable[[ui.timer], None]) -> None:
    """Start periodic data refresh timers.

    Args:
        tracker: Callback to register timers with OrderEntryContext for lifecycle management.
                 This ensures timers are cancelled on page exit, preventing memory leaks.

    CRITICAL: All timers MUST be registered with the tracker to prevent leaks.
    OrderEntryContext.dispose() will cancel all tracked timers on page exit.

    TASK TRACKING: Tasks are tracked to:
    - Prevent overlapping refreshes (skip if previous refresh still in-flight)
    - Allow cancellation on dispose (clean shutdown)
    """

    def _spawn_position_refresh() -> None:
        """Spawn position refresh task, skipping if one is already in-flight."""
        if self._disposed:
            return  # Don't start new tasks after disposal
        if self._position_refresh_task and not self._position_refresh_task.done():
            return  # Skip - previous refresh still running (prevent overlap)
        self._position_refresh_task = asyncio.create_task(self._refresh_position_data())

    def _spawn_buying_power_refresh() -> None:
        """Spawn buying power refresh task, skipping if one is already in-flight."""
        if self._disposed:
            return  # Don't start new tasks after disposal
        if self._buying_power_refresh_task and not self._buying_power_refresh_task.done():
            return  # Skip - previous refresh still running (prevent overlap)
        self._buying_power_refresh_task = asyncio.create_task(self._refresh_buying_power())

    # Position refresh every 5s (backup for missed WebSocket updates)
    self._position_timer = ui.timer(5.0, _spawn_position_refresh)
    tracker(self._position_timer)  # Register for cleanup

    # Buying power refresh every 10s (no WebSocket channel available)
    self._buying_power_timer = ui.timer(10.0, _spawn_buying_power_refresh)
    tracker(self._buying_power_timer)  # Register for cleanup

async def _refresh_position_data(self) -> None:
    """Periodic position refresh (timer callback).

    Called every 5s as backup for missed WebSocket position updates.
    Updates position and timestamp for staleness tracking.

    FAIL-CLOSED: If server timestamp missing/invalid, do NOT update
    _position_last_updated - data remains marked as stale.

    DISPOSAL GUARD: Returns early if component disposed during task execution.
    """
    if self._disposed:
        return  # Component disposed - don't update state

    if not self._state.symbol:
        return

    try:
        positions_resp = await self._client.fetch_positions(
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
        )
        positions = positions_resp.get("positions", [])

        # Parse server timestamp (fail-closed if missing/invalid)
        # API DEPENDENCY: Requires /api/v1/positions to include top-level "timestamp" field
        # Fallback: Try newest position updated_at if top-level timestamp absent
        server_timestamp_str = positions_resp.get("timestamp")
        server_timestamp: datetime | None = None
        if server_timestamp_str:
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                server_timestamp = parse_iso_timestamp(server_timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in position response, trying fallback")

        if server_timestamp is None:
            # FALLBACK: Use newest position updated_at if available (NOT datetime.now())
            newest_updated_at = None
            for pos in positions:
                updated_str = pos.get("updated_at")
                if updated_str:
                    try:
                        pos_timestamp = parse_iso_timestamp(str(updated_str))
                        if newest_updated_at is None or pos_timestamp > newest_updated_at:
                            newest_updated_at = pos_timestamp
                    except (ValueError, TypeError):
                        continue
            if newest_updated_at:
                server_timestamp = newest_updated_at
                logger.info("Using newest position updated_at as timestamp fallback")
            else:
                logger.warning("Position response missing timestamp, data remains stale")

        # Always update position value (we got fresh data, even if timestamp unknown)
        for pos in positions:
            if pos.get("symbol") == self._state.symbol:
                self._current_position = int(pos.get("qty", 0))
                break
        else:
            self._current_position = 0

        # FAIL-CLOSED: Update timestamp (valid or None)
        # If timestamp is None, this clears it and forces staleness check to fail
        self._position_last_updated = server_timestamp

        self._update_ui_from_state()

    except Exception as exc:
        logger.debug(f"Position refresh failed (will retry): {exc}")
        # FAIL-CLOSED: Clear timestamp to force staleness check to fail
        self._position_last_updated = None

async def _refresh_buying_power(self) -> None:
    """Periodic buying power refresh (timer callback).

    Called every 10s since there's no WebSocket channel for buying power.
    Updates buying power and timestamp for staleness tracking.

    FAIL-CLOSED: If server timestamp missing/invalid, do NOT update
    _buying_power_last_updated - data remains marked as stale.

    DISPOSAL GUARD: Returns early if component disposed during task execution.
    """
    if self._disposed:
        return  # Component disposed - don't update state

    try:
        account_resp = await self._client.fetch_account_info(
            user_id=self._user_id,
            role=self._role,
        )

        # Parse server timestamp (fail-closed if missing/invalid)
        # API DEPENDENCY: Requires /api/v1/account to include "timestamp" field
        # Fallback: Try alternate timestamp fields before leaving stale
        server_timestamp_str = account_resp.get("timestamp")
        server_timestamp: datetime | None = None
        if server_timestamp_str:
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                server_timestamp = parse_iso_timestamp(server_timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in account response, trying fallback")

        if server_timestamp is None:
            # FALLBACK: Try alternate timestamp fields (NOT datetime.now())
            for alt_field in ("last_equity_change", "updated_at", "as_of"):
                alt_timestamp = account_resp.get(alt_field)
                if alt_timestamp:
                    try:
                        server_timestamp = parse_iso_timestamp(str(alt_timestamp))
                        logger.info(f"Using alternate account timestamp field '{alt_field}'")
                        break
                    except (ValueError, TypeError):
                        continue

            if server_timestamp is None:
                logger.warning("Account response missing timestamp, data remains stale")

        # FAIL-CLOSED: Check raw value for None before parsing (matches confirm-time pattern)
        raw_buying_power = account_resp.get("buying_power")
        if raw_buying_power is None:
            # Missing = unavailable, different from 0 (insufficient)
            self._buying_power = None
            self._buying_power_last_updated = None  # Force staleness
            logger.warning("Buying power field missing in refresh response")
            self._update_ui_from_state()
            return

        try:
            parsed_buying_power = Decimal(str(raw_buying_power))
            if not parsed_buying_power.is_finite():
                # NaN/Infinity cannot be used for buying power comparisons
                raise ValueError(f"Non-finite buying power: {parsed_buying_power}")
            self._buying_power = parsed_buying_power
        except (InvalidOperation, ValueError, TypeError) as exc:
            logger.warning(f"Invalid buying power in refresh: {raw_buying_power!r} - {exc}")
            self._buying_power = None  # Treat as unavailable on parse error
            self._buying_power_last_updated = None  # Force staleness
            self._update_ui_from_state()
            return

        # FAIL-CLOSED: Update timestamp (valid or None)
        # If timestamp is None, this clears it and forces staleness check to fail
        self._buying_power_last_updated = server_timestamp

        self._update_ui_from_state()

    except Exception as exc:
        logger.debug(f"Buying power refresh failed (will retry): {exc}")
        # FAIL-CLOSED: Clear both value and timestamp to force staleness
        self._buying_power = None
        self._buying_power_last_updated = None

async def dispose(self) -> None:
    """Clean up component-local resources.

    Called by OrderEntryContext during page exit.
    Timers are tracked centrally, but component may have local cleanup.

    TASK CANCELLATION: Cancels any in-flight refresh tasks to prevent:
    - State updates after disposal
    - Resource usage from orphaned tasks
    """
    # Mark as disposed FIRST to prevent new task spawning
    self._disposed = True

    # Cancel local timer references (redundant since tracked centrally, but defensive)
    if self._position_timer:
        self._position_timer.cancel()
    if self._buying_power_timer:
        self._buying_power_timer.cancel()

    # Cancel in-flight refresh tasks (prevents state updates after disposal)
    for task in [self._position_refresh_task, self._buying_power_refresh_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected - task cancelled successfully
```

---

### T4.2: Real-Time Market Context - MEDIUM PRIORITY

**Goal:** Display Level 1 market data when entering orders.

**Acceptance Criteria:**
- [ ] Bid/Ask spread visible for selected symbol
- [ ] Last price with change displayed
- [ ] Position context shown
- [ ] Real-time updates via WebSocket
- [ ] Fallback behavior when data feed unavailable (show "N/A", not error)
- [ ] Data staleness indicator (>30s = warning)

**Files:**
- Create: `apps/web_console_ng/components/market_context.py`
- Create: `apps/web_console_ng/components/level1_display.py`

#### Implementation Details

##### 4.2.1 MarketContext Component Architecture

```python
# apps/web_console_ng/components/market_context.py

@dataclass
class MarketDataSnapshot:
    """Point-in-time market data snapshot."""
    symbol: str
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    last_price: Decimal | None = None
    prev_close: Decimal | None = None
    volume: int | None = None
    timestamp: datetime | None = None

    @property
    def mid_price(self) -> Decimal | None:
        if self.bid_price and self.ask_price:
            return (self.bid_price + self.ask_price) / 2
        return None

    @property
    def spread_bps(self) -> Decimal | None:
        if self.bid_price and self.ask_price and self.mid_price:
            spread = self.ask_price - self.bid_price
            return (spread / self.mid_price) * 10000
        return None

    @property
    def change(self) -> Decimal | None:
        if self.last_price and self.prev_close and self.prev_close > 0:
            return self.last_price - self.prev_close
        return None

    @property
    def change_pct(self) -> Decimal | None:
        if self.last_price and self.prev_close and self.prev_close > 0:
            return ((self.last_price - self.prev_close) / self.prev_close) * 100
        return None


class MarketContextComponent:
    """Real-time Level 1 market data display."""

    STALE_THRESHOLD_S = 30  # Configurable via env
    UPDATE_THROTTLE_MS = 100  # Minimum update interval

    def __init__(
        self,
        realtime_updater: RealtimeUpdater,
        trading_client: AsyncTradingClient,
    ):
        self._realtime = realtime_updater
        self._client = trading_client
        self._current_symbol: str | None = None
        self._data: MarketDataSnapshot | None = None
        self._last_ui_update: float = 0
        self._timer_tracker: Callable[[ui.timer], None] | None = None

        # UI elements
        self._bid_label: ui.label | None = None
        self._ask_label: ui.label | None = None
        self._spread_label: ui.label | None = None
        self._last_price_label: ui.label | None = None
        self._change_label: ui.label | None = None
        self._staleness_badge: ui.badge | None = None

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Initialize the market context component with timer tracking.

        Args:
            timer_tracker: Callback to register timers with OrderEntryContext for lifecycle management.
        """
        self._timer_tracker = timer_tracker

        # Start periodic staleness badge update timer
        # This ensures the badge ages even without new price updates
        staleness_timer = ui.timer(1.0, self._update_staleness_from_timer)
        timer_tracker(staleness_timer)

    def _update_staleness_from_timer(self) -> None:
        """Periodic callback to update staleness badge based on elapsed time.

        Called by timer every 1 second to ensure the staleness badge
        continues to age even when no new price updates arrive.
        Without this, badge would only update on new price data.
        """
        if self._data and self._data.timestamp:
            self._update_staleness_badge(self._data.timestamp)

    def _update_staleness_badge(self, timestamp: datetime | None) -> None:
        """Update staleness badge based on data age.

        Badge states:
        - Live (green): <5s old
        - Xms ago (yellow): 5-30s old
        - Stale (red): >30s old
        - No data (gray): No timestamp
        """
        if not self._staleness_badge:
            return

        if not timestamp:
            self._staleness_badge.text = "No data"
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-red-500")
            self._staleness_badge.classes(add="bg-gray-500 text-white")
            return

        age_s = (datetime.now(timezone.utc) - timestamp).total_seconds()

        if age_s < 5:
            self._staleness_badge.text = "Live"
            self._staleness_badge.classes(remove="bg-yellow-500 bg-red-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-green-500 text-white")
        elif age_s < self.STALE_THRESHOLD_S:
            self._staleness_badge.text = f"{int(age_s)}s ago"
            self._staleness_badge.classes(remove="bg-green-500 bg-red-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-yellow-500 text-black")
        else:
            self._staleness_badge.text = f"Stale ({int(age_s)}s)"
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-red-500 text-white")
```

##### 4.2.2 Data Updates via Callbacks (NO Direct Subscriptions)

```python
"""
SUBSCRIPTION OWNERSHIP: MarketContext does NOT subscribe to Redis.
OrderEntryContext owns all subscriptions and dispatches updates via callbacks.

Data Flow: Redis → RealtimeUpdater → OrderEntryContext → MarketContext.set_price_data()
"""

async def on_symbol_changed(self, symbol: str | None) -> None:
    """Called by OrderEntryContext when selected symbol changes.

    NOTE: MarketContext does NOT subscribe to Redis directly.
    It receives price updates via set_price_data() callback.

    RACE PREVENTION: After async operations, validate symbol is still current
    before updating state/UI to prevent stale fetches from overwriting newer data.
    """
    self._current_symbol = symbol

    if not symbol:
        self._data = None
        self._update_ui_no_data()
        return

    # Fetch initial data from API (fallback if Redis cache empty)
    await self._fetch_initial_data(symbol)

    # RACE CHECK: Ensure symbol hasn't changed during async fetch
    # This prevents stale fetch results from overwriting newer selections
    if symbol != self._current_symbol:
        return  # Stale - symbol changed during fetch

def set_price_data(self, data: dict) -> None:
    """Called by OrderEntryContext when price data updates.

    This is the ONLY way MarketContext receives real-time price updates.
    MarketContext does NOT subscribe directly to price.updated.{symbol} channel.
    """
    # TYPE GUARD: Validate payload structure
    if not isinstance(data, dict):
        logger.warning(f"MarketContext received invalid data type: {type(data).__name__}")
        return  # Silently ignore malformed data (display-only component)

    # Throttle UI updates
    now = time.time()
    if now - self._last_ui_update < self.UPDATE_THROTTLE_MS / 1000:
        return
    self._last_ui_update = now

    # Update internal state via existing _on_price_update logic
    self._handle_price_update(data)

def _handle_price_update(self, data: dict) -> None:
    """Process price update data and update UI.

    Called by set_price_data() callback (via OrderEntryContext).

    SAFE PARSING: All Decimal/timestamp conversions wrapped in try/except.
    On malformed data, set field to None and continue (display-only component).
    This ensures a single bad tick doesn't crash the callback.
    """
    # Safe parsing helpers
    def safe_decimal(key: str) -> Decimal | None:
        raw = data.get(key)
        if raw is None:
            return None
        try:
            dec = Decimal(str(raw))
            # Reject NaN/Infinity to prevent UI artifacts and downstream formatting issues
            if not dec.is_finite():
                logger.warning(f"MarketContext: Non-finite {key}: {raw!r}")
                return None
            return dec
        except (InvalidOperation, ValueError, TypeError):
            logger.warning(f"MarketContext: Invalid {key}: {raw!r}")
            return None

    def safe_timestamp(key: str) -> datetime | None:
        raw = data.get(key)
        if raw is None:
            return None
        try:
            return parse_iso_timestamp(raw)
        except (ValueError, TypeError):
            logger.warning(f"MarketContext: Invalid {key}: {raw!r}")
            return None

    # Update snapshot with safe parsing (None on any parse failure)
    self._data = MarketDataSnapshot(
        symbol=data.get("symbol", self._current_symbol),
        bid_price=safe_decimal("bid"),
        ask_price=safe_decimal("ask"),
        bid_size=data.get("bid_size"),
        ask_size=data.get("ask_size"),
        last_price=safe_decimal("price"),
        prev_close=safe_decimal("prev_close"),
        timestamp=safe_timestamp("timestamp"),
    )

    self._update_ui()
```

##### 4.2.3 Level1 Display Component

```python
# apps/web_console_ng/components/level1_display.py

class Level1DisplayComponent:
    """Compact bid/ask display with visual indicators."""

    def create(self) -> ui.card:
        """Create the Level 1 display card."""
        with ui.card().classes("p-3 w-full") as card:
            # Symbol header
            with ui.row().classes("justify-between items-center mb-2"):
                self._symbol_label = ui.label().classes("text-lg font-bold")
                self._staleness_badge = ui.badge().classes("text-xs")

            # Bid/Ask grid
            with ui.grid(columns=2).classes("gap-2 w-full"):
                # Bid column
                with ui.column().classes("items-start"):
                    ui.label("BID").classes("text-xs text-gray-500")
                    self._bid_price = ui.label().classes("text-xl font-mono text-green-500")
                    self._bid_size = ui.label().classes("text-xs text-gray-400")

                # Ask column
                with ui.column().classes("items-end"):
                    ui.label("ASK").classes("text-xs text-gray-500")
                    self._ask_price = ui.label().classes("text-xl font-mono text-red-500")
                    self._ask_size = ui.label().classes("text-xs text-gray-400")

            # Spread row
            with ui.row().classes("justify-center mt-2"):
                ui.label("Spread:").classes("text-xs text-gray-500")
                self._spread = ui.label().classes("text-xs font-mono ml-1")

            # Last price & change
            ui.separator().classes("my-2")
            with ui.row().classes("justify-between items-center"):
                with ui.column().classes("items-start"):
                    ui.label("Last").classes("text-xs text-gray-500")
                    self._last_price = ui.label().classes("text-lg font-mono")
                self._change_badge = ui.badge().classes("text-sm")

        return card

    def update(self, data: MarketDataSnapshot | None) -> None:
        """Update display with new data."""

        if not data:
            self._show_no_data()
            return

        # Update symbol
        self._symbol_label.text = data.symbol

        # Update bid/ask
        if data.bid_price is not None:
            self._bid_price.text = f"${data.bid_price:.2f}"
            self._bid_size.text = f"x {data.bid_size or '?'}"
        else:
            self._bid_price.text = "N/A"
            self._bid_size.text = ""

        if data.ask_price is not None:
            self._ask_price.text = f"${data.ask_price:.2f}"
            self._ask_size.text = f"x {data.ask_size or '?'}"
        else:
            self._ask_price.text = "N/A"
            self._ask_size.text = ""

        # Update spread
        if data.spread_bps is not None:
            self._spread.text = f"{data.spread_bps:.2f} bps"
        else:
            self._spread.text = "N/A"

        # Update last price
        if data.last_price is not None:
            self._last_price.text = f"${data.last_price:.2f}"
        else:
            self._last_price.text = "N/A"

        # Update change badge
        if data.change_pct is not None:
            sign = "+" if data.change_pct >= 0 else ""
            self._change_badge.text = f"{sign}{data.change_pct:.2f}%"
            color = "green" if data.change_pct >= 0 else "red"
            self._change_badge.classes(remove="bg-green-500 bg-red-500 bg-gray-500")
            self._change_badge.classes(add=f"bg-{color}-500 text-white")
        else:
            self._change_badge.text = "N/A"
            self._change_badge.classes(remove="bg-green-500 bg-red-500")
            self._change_badge.classes(add="bg-gray-500 text-white")

        # Update staleness indicator
        self._update_staleness(data.timestamp)

    def _update_staleness(self, timestamp: datetime | None) -> None:
        """Update staleness badge based on data age."""

        if not timestamp:
            self._staleness_badge.text = "No data"
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-red-500")
            self._staleness_badge.classes(add="bg-gray-500 text-white")
            return

        age_s = (datetime.now(timezone.utc) - timestamp).total_seconds()

        if age_s < 5:
            self._staleness_badge.text = "Live"
            self._staleness_badge.classes(remove="bg-yellow-500 bg-red-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-green-500 text-white")
        elif age_s < 30:
            self._staleness_badge.text = f"{int(age_s)}s ago"
            self._staleness_badge.classes(remove="bg-green-500 bg-red-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-yellow-500 text-black")
        else:
            self._staleness_badge.text = f"Stale ({int(age_s)}s)"
            self._staleness_badge.classes(remove="bg-green-500 bg-yellow-500 bg-gray-500")
            self._staleness_badge.classes(add="bg-red-500 text-white")

    async def dispose(self) -> None:
        """Clean up component resources.

        Called by OrderEntryContext on page unload.
        MarketContext doesn't own subscriptions (handled by OrderEntryContext),
        but needs to clear any pending state.
        """
        # Clear references
        self._data = None
        # Note: This component doesn't have timers or subscriptions to clean up
        # Subscriptions are managed by OrderEntryContext
```

##### 4.2.4 Fallback Behavior

```python
async def _fetch_initial_data(self, symbol: str) -> None:
    """Fetch initial data from API as fallback.

    Cache Schema (Redis key: price:{symbol} via RedisKeys.price(symbol)):
    {
        "symbol": str,
        "bid": str (Decimal as string),
        "ask": str (Decimal as string),
        "mid": str (Decimal as string),
        "bid_size": int,
        "ask_size": int,
        "timestamp": str (ISO format),
        "exchange": str | None
    }
    """
    try:
        # Try market prices API first (returns cached Redis data)
        # NOTE: Requires adding fetch_cached_price to AsyncTradingClient
        # Implementation: GET /api/v1/market-data/{symbol}/quote
        cached = await self._client.fetch_cached_price(
            user_id=self._user_id,
            role=self._role,
            symbol=symbol,
        )
        if cached:
            # Use safe_decimal pattern with is_finite() to reject NaN/Infinity
            def parse_price(key: str) -> Decimal | None:
                raw = cached.get(key)
                if raw is None:
                    return None
                try:
                    dec = Decimal(str(raw))
                    return dec if dec.is_finite() else None
                except (InvalidOperation, ValueError, TypeError):
                    return None

            # Parse timestamp with try/except to avoid aborting fallback chain
            parsed_timestamp = None
            raw_timestamp = cached.get("timestamp")
            if raw_timestamp:
                try:
                    parsed_timestamp = parse_iso_timestamp(str(raw_timestamp))
                except (ValueError, TypeError):
                    pass  # Leave as None, continue with rest of fallback chain

            self._data = MarketDataSnapshot(
                symbol=symbol,
                bid_price=parse_price("bid"),
                ask_price=parse_price("ask"),
                last_price=parse_price("mid"),
                timestamp=parsed_timestamp,
            )
            self._update_ui()
            return

        # Fallback: fetch from positions API (last known price for held symbols)
        # NOTE: This only works for symbols we have a position in
        positions_resp = await self._client.fetch_positions(
            user_id=self._user_id,
            role=self._role,
            strategies=self._strategies,
        )
        positions = positions_resp.get("positions", [])
        for pos in positions:
            if pos.get("symbol") == symbol:
                # Safe parsing with is_finite() check
                last_price = None
                raw_price = pos.get("current_price")
                if raw_price is not None:
                    try:
                        dec = Decimal(str(raw_price))
                        last_price = dec if dec.is_finite() else None
                    except (InvalidOperation, ValueError, TypeError):
                        pass  # Leave as None

                self._data = MarketDataSnapshot(
                    symbol=symbol,
                    last_price=last_price,
                )
                self._update_ui()
                return

        # Fallback for symbols without position: try market prices endpoint
        # This endpoint returns prices for all watched symbols
        try:
            market_prices = await self._client.fetch_market_prices(
                user_id=self._user_id,
                role=self._role,
            )
            for price_data in market_prices:
                if price_data.get("symbol") == symbol:
                    # Safe parsing with is_finite() check
                    # API CONTRACT: Use "mid" field (bid/ask midpoint), fallback to "price" if absent
                    last_price = None
                    mid_price = price_data.get("mid")
                    raw_price = mid_price if mid_price is not None else price_data.get("price")
                    if raw_price is not None:
                        try:
                            dec = Decimal(str(raw_price))
                            last_price = dec if dec.is_finite() else None
                        except (InvalidOperation, ValueError, TypeError):
                            pass  # Leave as None

                    self._data = MarketDataSnapshot(
                        symbol=symbol,
                        last_price=last_price,
                    )
                    self._update_ui()
                    return
        except Exception:
            pass  # Continue to N/A fallback

        # Ultimate fallback: show N/A
        self._data = MarketDataSnapshot(symbol=symbol)
        self._update_ui_no_data()

    except Exception as exc:
        logger.warning(f"Failed to fetch initial data for {symbol}: {exc}")
        self._data = MarketDataSnapshot(symbol=symbol)
        self._update_ui_no_data()
```

---

### T4.3: TradingView Chart Integration - MEDIUM PRIORITY

**Goal:** Embed price charts for visual context during trading.

**Acceptance Criteria:**
- [ ] Lightweight Charts integrated via CDN
- [ ] Candlestick chart renders for selected symbol
- [ ] Execution prices marked on chart
- [ ] VWAP/TWAP overlays for algo orders
- [ ] Fallback to static chart if real-time feed unavailable
- [ ] Chart data source documented (licensing considerations noted)

**Files:**
- Create: `apps/web_console_ng/components/price_chart.py`
- Create: `apps/web_console_ng/ui/lightweight_charts.py`

#### Implementation Details

##### 4.3.1 Lightweight Charts Integration

```python
# apps/web_console_ng/ui/lightweight_charts.py

"""
Lightweight Charts Integration for NiceGUI

Library: TradingView Lightweight Charts (Apache 2.0 License)
Version: 4.1.0 (pinned for stability)

Licensing Notes:
- Apache 2.0 License allows commercial use
- Attribution required (included in chart footer)
- Data source: Alpaca Market Data API

Security Notes:
- CDN assets loaded with SRI (Subresource Integrity) hash
- CSP allowlist entry required: script-src unpkg.com
- Alternative: Host locally in /static/vendor/ for airgapped deployments
"""

# CDN with SRI hash for supply-chain security
# Hash generated via: curl -s "$CDN_URL" | openssl dgst -sha384 -binary | openssl base64 -A
LIGHTWEIGHT_CHARTS_CDN = "https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"
LIGHTWEIGHT_CHARTS_SRI = "sha384-2PoRwGg4nLjjsqHMzWaFrNj9FH5kGXsMTxDQXnRrKvJpEfBKqGqSqGfxQR8hG2tM"

# Local fallback path (for airgapped/high-security deployments)
# CRITICAL: Download and verify hash before deployment:
#   curl -o static/vendor/lightweight-charts.4.1.0.production.js "$LIGHTWEIGHT_CHARTS_CDN"
#   openssl dgst -sha384 static/vendor/lightweight-charts.4.1.0.production.js
LIGHTWEIGHT_CHARTS_LOCAL = "/static/vendor/lightweight-charts.4.1.0.production.js"

# Build verification (Makefile target):
# .PHONY: verify-vendor-assets
# verify-vendor-assets:
#     @echo "Verifying lightweight-charts SRI hash..."
#     @ACTUAL=$$(openssl dgst -sha384 -binary static/vendor/lightweight-charts.4.1.0.production.js | openssl base64 -A) && \
#     EXPECTED="2PoRwGg4nLjjsqHMzWaFrNj9FH5kGXsMTxDQXnRrKvJpEfBKqGqSqGfxQR8hG2tM" && \
#     if [ "$$ACTUAL" != "$$EXPECTED" ]; then echo "SRI MISMATCH!"; exit 1; fi && \
#     echo "SRI hash verified ✓"

CHART_INIT_JS = """
(function() {
    const container = document.getElementById('{container_id}');
    if (!container) return;

    // Create chart
    const chart = LightweightCharts.createChart(container, {{
        width: {width},
        height: {height},
        layout: {{
            background: {{ type: 'solid', color: '#1e1e1e' }},
            textColor: '#d1d4dc',
        }},
        grid: {{
            vertLines: {{ color: '#2B2B43' }},
            horzLines: {{ color: '#363C4E' }},
        }},
        crosshair: {{
            mode: LightweightCharts.CrosshairMode.Normal,
        }},
        timeScale: {{
            timeVisible: true,
            secondsVisible: false,
        }},
    }});

    // Create candlestick series
    const candlestickSeries = chart.addCandlestickSeries({{
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderVisible: false,
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
    }});

    // Store references
    window.__charts = window.__charts || {{}};
    window.__charts['{chart_id}'] = {{
        chart: chart,
        candlestickSeries: candlestickSeries,
        markers: [],
        vwapSeries: null,
        twapSeries: null,
    }};

    // Add attribution footer (required by Apache 2.0 license)
    const attribution = document.createElement('div');
    attribution.style.cssText = 'position:absolute;bottom:2px;right:4px;font-size:9px;color:#666;';
    attribution.innerHTML = 'Chart: <a href="https://tradingview.github.io/lightweight-charts/" target="_blank" rel="noopener noreferrer" style="color:#888;">Lightweight Charts</a> | Data: Alpaca';
    container.style.position = 'relative';
    container.appendChild(attribution);

    // Resize handler
    const resizeObserver = new ResizeObserver(entries => {{
        chart.applyOptions({{ width: container.clientWidth }});
    }});
    resizeObserver.observe(container);
}})();
"""


class LightweightChartsLoader:
    """Load Lightweight Charts library via CDN with SRI and fallback."""

    _loaded: bool = False
    _ready: bool = False  # Track if chart API is ready

    @classmethod
    async def ensure_loaded(cls) -> None:
        """Ensure the library is loaded exactly once with SRI verification."""
        if cls._loaded:
            # Wait for ready state if already loading
            while not cls._ready:
                await asyncio.sleep(0.05)
            return

        cls._loaded = True

        # Load with SRI hash and crossorigin for supply-chain security
        # Falls back to local copy if CDN fails
        await ui.run_javascript(f"""
            (async function() {{
                if (typeof LightweightCharts !== 'undefined') {{
                    window.__lwc_ready = true;
                    return;
                }}

                try {{
                    const script = document.createElement('script');
                    script.src = '{LIGHTWEIGHT_CHARTS_CDN}';
                    script.integrity = '{LIGHTWEIGHT_CHARTS_SRI}';
                    script.crossOrigin = 'anonymous';

                    await new Promise((resolve, reject) => {{
                        script.onload = resolve;
                        script.onerror = reject;
                        document.head.appendChild(script);
                    }});
                    console.log('Lightweight Charts loaded from CDN');
                }} catch (e) {{
                    console.warn('CDN load failed, using local fallback:', e);
                    const fallback = document.createElement('script');
                    fallback.src = '{LIGHTWEIGHT_CHARTS_LOCAL}';
                    await new Promise((resolve, reject) => {{
                        fallback.onload = resolve;
                        fallback.onerror = reject;
                        document.head.appendChild(fallback);
                    }});
                    console.log('Lightweight Charts loaded from local fallback');
                }}
                window.__lwc_ready = true;
            }})();
        """)

        # Wait for library to be ready
        for _ in range(100):  # Max 5 seconds
            ready = await ui.run_javascript("window.__lwc_ready === true")
            if ready:
                cls._ready = True
                return
            await asyncio.sleep(0.05)

        raise RuntimeError("Failed to load Lightweight Charts library")
```

##### 4.3.2 PriceChart Component

```python
# apps/web_console_ng/components/price_chart.py

@dataclass
class CandleData:
    """Single candle data point."""
    time: int  # Unix timestamp
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


@dataclass
class ExecutionMarker:
    """Execution marker for chart overlay."""
    time: int  # Unix timestamp
    price: float
    side: Literal["buy", "sell"]
    quantity: int
    order_id: str


class PriceChartComponent:
    """Interactive price chart with execution markers."""

    DEFAULT_TIMEFRAME = "1D"  # 1 day of data
    CANDLE_INTERVAL = "5m"   # 5-minute candles

    def __init__(
        self,
        trading_client: AsyncTradingClient,
        realtime_updater: RealtimeUpdater,
    ):
        self._client = trading_client
        self._realtime = realtime_updater
        self._current_symbol: str | None = None
        self._chart_id: str = f"chart_{id(self)}"
        self._container_id: str = f"container_{id(self)}"
        self._candles: list[CandleData] = []
        self._markers: list[ExecutionMarker] = []
        self._timer_tracker: Callable[[ui.timer], None] | None = None
        self._last_realtime_update: datetime | None = None
        self._width: int = 600  # Default, overridden by create()
        self._height: int = 300  # Default, overridden by create()
        # Track pending update tasks for cleanup on dispose (prevent task leaks)
        self._pending_update_tasks: set[asyncio.Task] = set()

    def create(self, width: int = 600, height: int = 300) -> ui.html:
        """Create the chart container.

        NOTE: Does NOT start timers. Timer for chart initialization is started
        in initialize() to ensure timer_tracker is available.
        """
        # Store dimensions for initialization
        self._width = width
        self._height = height

        # Container div
        container = ui.html(f'<div id="{self._container_id}" style="width:100%;height:{height}px;"></div>')

        return container

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Initialize the chart component with timer tracking.

        Args:
            timer_tracker: Callback to register timers with OrderEntryContext for lifecycle management.

        CRITICAL: Timers are created HERE (not in create()) to ensure timer_tracker
        is available for registration. create() only builds UI elements.
        """
        self._timer_tracker = timer_tracker

        # Initialize chart via one-shot timer (tracked)
        async def init_chart():
            await LightweightChartsLoader.ensure_loaded()
            await ui.run_javascript(
                CHART_INIT_JS.format(
                    container_id=self._container_id,
                    chart_id=self._chart_id,
                    width=self._width,
                    height=self._height,
                )
            )

        init_timer = ui.timer(0.1, init_chart, once=True)
        timer_tracker(init_timer)

        # Start realtime staleness monitor (tracked via timer_tracker)
        self._start_realtime_staleness_monitor(timer_tracker)

    async def on_symbol_changed(self, symbol: str | None) -> None:
        """Called by OrderEntryContext when selected symbol changes.

        NOTE: PriceChart does NOT subscribe to Redis directly.
        It receives price updates via set_price_data() callback from OrderEntryContext.

        SUBSCRIPTION OWNERSHIP: OrderEntryContext owns all subscriptions.

        CRITICAL: Must reset realtime update timestamp to prevent stale
        badge state from previous symbol affecting new symbol display.

        RACE PREVENTION: After each async operation, validate symbol is still current
        before updating state/UI to prevent stale fetches from overwriting newer data.
        """
        self._current_symbol = symbol

        # CRITICAL: Reset realtime update timestamp for staleness tracking
        # Without this, staleness badge would show wrong age for new symbol
        self._last_realtime_update = None

        if not symbol:
            await self._clear_chart()
            return

        # Fetch historical data
        candles = await self._fetch_candle_data(symbol)

        # RACE CHECK: Ensure symbol hasn't changed during candle fetch
        if symbol != self._current_symbol:
            return  # Stale - symbol changed during fetch

        self._candles = candles

        # Fetch execution history for markers
        markers = await self._fetch_execution_markers(symbol)

        # RACE CHECK: Ensure symbol hasn't changed during marker fetch
        if symbol != self._current_symbol:
            return  # Stale - symbol changed during fetch

        self._markers = markers

        # Update chart (only if symbol still current)
        await self._update_chart_data()

        # NOTE: No direct Redis subscription here!
        # Real-time updates come via set_price_data() callback from OrderEntryContext

    def set_price_data(self, data: dict) -> None:
        """Called by OrderEntryContext when price data updates.

        This is the ONLY way PriceChart receives real-time price updates.
        PriceChart does NOT subscribe directly to price.updated.{symbol} channel.

        Used to update the chart with the latest price tick.

        STALENESS TRACKING: Only update _last_realtime_update AFTER validating
        that price data is present and parseable. Invalid updates should NOT
        suppress stale/fallback overlays.
        """
        # TYPE GUARD: Validate payload structure
        if not isinstance(data, dict):
            logger.warning(f"PriceChart received invalid data type: {type(data).__name__}")
            return  # Silently ignore malformed data (display-only component)

        # VALIDATE price before updating staleness timestamp
        # This ensures invalid ticks don't mask stale data
        raw_price = data.get("price")
        if raw_price is None:
            logger.debug("PriceChart: tick missing price, not updating staleness")
            return

        try:
            parsed_price = Decimal(str(raw_price))
            if not parsed_price.is_finite() or parsed_price <= 0:
                # is_finite() rejects NaN/Infinity that would bypass <= 0 check
                logger.warning(f"PriceChart: invalid/non-finite price {parsed_price}, not updating staleness")
                return
        except (InvalidOperation, ValueError, TypeError):
            logger.warning(f"PriceChart: unparseable price {raw_price!r}, not updating staleness")
            return

        # Only update staleness timestamp AFTER validating price is valid
        # FAIL-CLOSED: REQUIRE SERVER TIMESTAMP - do NOT use datetime.now() as fallback.
        # If timestamp is missing/invalid, set to None and keep/show stale overlay.
        # This prevents masking stale data as fresh (consistent with trading safety policy).
        raw_timestamp = data.get("timestamp")
        if raw_timestamp:
            try:
                self._last_realtime_update = parse_iso_timestamp(str(raw_timestamp))
            except (ValueError, TypeError):
                # Invalid timestamp format - FAIL-CLOSED: treat as missing
                logger.debug(f"PriceChart: unparseable timestamp {raw_timestamp!r}, keeping stale")
                self._last_realtime_update = None  # Will show stale overlay
        else:
            # No timestamp in payload - FAIL-CLOSED: treat as stale
            logger.debug("PriceChart: missing timestamp, keeping stale overlay")
            self._last_realtime_update = None

        # Process the price update
        # Track task for cleanup on dispose (avoid task leaks)
        task = asyncio.create_task(self._handle_price_update(data))
        self._pending_update_tasks.add(task)
        task.add_done_callback(self._pending_update_tasks.discard)

    async def _handle_price_update(self, data: dict) -> None:
        """Process incoming price update and update chart."""
        # Existing _on_price_update logic goes here

    async def _fetch_candle_data(self, symbol: str) -> list[CandleData]:
        """Fetch historical candle data with market session and calendar awareness.

        NOTE: This requires adding fetch_historical_bars() to AsyncTradingClient.
        The execution gateway must expose an endpoint or proxy to Alpaca bars API.

        Market Session Handling:
        - Uses trading calendar to handle weekends/holidays correctly
        - Pre-market (4:00-9:30 ET): Fetch previous trading day + current pre-market
        - Regular session (9:30-16:00 ET): Fetch today's bars only
        - After-hours (16:00-20:00 ET): Fetch today's full session + after-hours
        - Closed market: Fetch last trading day's full session
        """
        from zoneinfo import ZoneInfo

        try:
            now_et = datetime.now(ZoneInfo("America/New_York"))

            # Get trading calendar to handle weekends/holidays
            # NOTE: Requires caching trading calendar or using Alpaca calendar API
            last_trading_day, next_trading_day = await self._get_trading_days(now_et)

            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

            # Determine if market is open today
            is_trading_day = last_trading_day.date() == now_et.date()

            if not is_trading_day:
                # Weekend/holiday: fetch last trading day's full session
                start_time = last_trading_day.replace(hour=4, minute=0).isoformat()
                end_time = last_trading_day.replace(hour=20, minute=0).isoformat()
            elif now_et < market_open:
                # Pre-market: fetch previous trading day + current pre-market
                prev_day = await self._get_previous_trading_day(now_et)
                start_time = prev_day.replace(hour=9, minute=30).isoformat()
                end_time = now_et.isoformat()
            elif now_et > market_close:
                # After-hours: fetch from today's pre-market open
                start_time = now_et.replace(hour=4, minute=0).isoformat()
                end_time = now_et.isoformat()
            else:
                # Regular session: fetch from today's pre-market
                start_time = now_et.replace(hour=4, minute=0).isoformat()
                end_time = now_et.isoformat()

            # NOTE: Requires adding this method to AsyncTradingClient
            # The endpoint could proxy to Alpaca /v2/stocks/{symbol}/bars
            bars = await self._client.fetch_historical_bars(
                user_id=self._user_id,
                role=self._role,
                symbol=symbol,
                timeframe="5Min",
                start=start_time,
                end=end_time,
                limit=200,  # Max bars, filtered by time range
            )

            return [
                CandleData(
                    time=int(parse_iso_timestamp(bar["timestamp"]).timestamp()),
                    open=float(bar["open"]),
                    high=float(bar["high"]),
                    low=float(bar["low"]),
                    close=float(bar["close"]),
                    volume=bar.get("volume"),
                )
                for bar in bars
            ]
        except Exception as exc:
            logger.warning(f"Failed to fetch candle data for {symbol}: {exc}")
            # Trigger fallback UI instead of empty chart
            await self._show_fallback_chart(symbol)
            return []

    async def _get_trading_days(self, reference: datetime) -> tuple[datetime, datetime]:
        """Get last and next trading days relative to reference date.

        Uses cached trading calendar to handle weekends and US market holidays.
        Calendar is cached for 24 hours to minimize API calls.

        Implementation Strategy:
        1. Check module-level cache for calendar data
        2. If stale/missing, fetch from Alpaca Calendar API
        3. Parse and store in sorted list for binary search lookup
        4. Return last trading day <= reference and next trading day >= reference
        """
        calendar = await self._get_cached_trading_calendar()

        ref_date = reference.date()
        last_trading = None
        next_trading = None

        for day in calendar:
            if day <= ref_date:
                last_trading = day
            if day >= ref_date and next_trading is None:
                next_trading = day
            if last_trading and next_trading:
                break

        # Convert to datetime with market times
        last_dt = datetime.combine(
            last_trading or ref_date,
            datetime.min.time(),
            tzinfo=reference.tzinfo,
        )
        next_dt = datetime.combine(
            next_trading or ref_date,
            datetime.min.time(),
            tzinfo=reference.tzinfo,
        )

        return (last_dt, next_dt)

    async def _get_cached_trading_calendar(self) -> list[date]:
        """Get trading calendar with caching.

        Cache Strategy:
        - Module-level cache: _TRADING_CALENDAR_CACHE
        - TTL: 24 hours
        - Scope: Current year + next 30 days
        - Source: Alpaca GET /v1/calendar or local holiday list fallback
        """
        global _TRADING_CALENDAR_CACHE, _TRADING_CALENDAR_CACHE_TIME

        # Check cache validity
        if (
            _TRADING_CALENDAR_CACHE is not None
            and _TRADING_CALENDAR_CACHE_TIME is not None
            and (datetime.now(timezone.utc) - _TRADING_CALENDAR_CACHE_TIME).total_seconds() < 86400
        ):
            return _TRADING_CALENDAR_CACHE

        try:
            # Fetch from Alpaca calendar API (requires adding to AsyncTradingClient)
            start = date.today() - timedelta(days=30)
            end = date.today() + timedelta(days=60)
            calendar_data = await self._client.get_trading_calendar(
                user_id=self._user_id,
                role=self._role,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
            trading_days = [
                date.fromisoformat(d["date"])
                for d in calendar_data
                if d.get("open")  # Only include open market days
            ]
        except Exception as exc:
            logger.warning(f"Failed to fetch trading calendar: {exc}")
            # Fallback: use simple weekday filter (excludes weekends, not holidays)
            trading_days = [
                date.today() - timedelta(days=i)
                for i in range(90)
                if (date.today() - timedelta(days=i)).weekday() < 5
            ]

        _TRADING_CALENDAR_CACHE = sorted(trading_days)
        _TRADING_CALENDAR_CACHE_TIME = datetime.now(timezone.utc)
        return _TRADING_CALENDAR_CACHE

    async def _get_previous_trading_day(self, reference: datetime) -> datetime:
        """Get the previous trading day before reference date."""
        last, _ = await self._get_trading_days(reference - timedelta(days=1))
        return last

# Module-level calendar cache
_TRADING_CALENDAR_CACHE: list[date] | None = None
_TRADING_CALENDAR_CACHE_TIME: datetime | None = None

    async def _fetch_execution_markers(self, symbol: str) -> list[ExecutionMarker]:
        """Fetch today's executions for chart markers.

        NOTE: Uses fetch_recent_fills which returns all recent fills.
        We filter client-side by symbol since the API doesn't support symbol filter.
        """
        try:
            # Use existing fetch_recent_fills (GET /api/v1/orders/recent-fills)
            # API returns all fills; we filter by symbol client-side
            fills_resp = await self._client.fetch_recent_fills(
                user_id=self._user_id,
                role=self._role,
                strategies=self._strategies,
                limit=100,  # Fetch more to ensure we get enough for this symbol
            )
            fills = fills_resp.get("fills", [])

            return [
                ExecutionMarker(
                    time=int(parse_iso_timestamp(fill["filled_at"]).timestamp()),
                    price=float(fill["price"]),
                    side=fill["side"],
                    quantity=fill["qty"],
                    order_id=fill["client_order_id"],
                )
                for fill in fills
                if fill.get("symbol") == symbol
            ]
        except Exception as exc:
            logger.warning(f"Failed to fetch execution markers for {symbol}: {exc}")
            return []

    async def _update_chart_data(self) -> None:
        """Update chart with candles and markers."""

        if not self._candles:
            return

        # Format candle data for JS
        candle_data = [
            {
                "time": c.time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
            }
            for c in self._candles
        ]

        # Format markers for JS
        marker_data = [
            {
                "time": m.time,
                "position": "aboveBar" if m.side == "sell" else "belowBar",
                "color": "#ef5350" if m.side == "sell" else "#26a69a",
                "shape": "arrowDown" if m.side == "sell" else "arrowUp",
                "text": f"{m.side.upper()} {m.quantity}",
            }
            for m in self._markers
        ]

        await ui.run_javascript(f"""
            const chartRef = window.__charts['{self._chart_id}'];
            if (chartRef) {{
                chartRef.candlestickSeries.setData({json.dumps(candle_data)});
                chartRef.candlestickSeries.setMarkers({json.dumps(marker_data)});
            }}
        """)

    async def _on_price_update(self, data: dict) -> None:
        """Handle real-time price update - update last candle.

        NOTE: This is an INTERNAL method called by _handle_price_update() which is
        invoked from set_price_data(). The type guard is in set_price_data().
        DO NOT call this method directly from external code.

        DEPRECATED consideration: If only set_price_data() is the supported entrypoint,
        this method should be consolidated into _handle_price_update() for clarity.
        """
        # TYPE GUARD (defensive, in case called directly)
        if not isinstance(data, dict):
            logger.warning(f"PriceChart._on_price_update: invalid data type: {type(data).__name__}")
            return

        price_raw = data.get("price")
        if price_raw is None:
            return
        try:
            price = float(price_raw)
            # float() doesn't reject inf/nan by default, so check explicitly
            if not math.isfinite(price) or price <= 0:
                return
        except (ValueError, TypeError):
            return

        if not self._candles:
            return

        # Track last update time for staleness detection
        # FAIL-CLOSED: REQUIRE SERVER TIMESTAMP - do NOT use datetime.now() as fallback.
        # This aligns with the fail-closed timestamp policy used in set_price_data().
        raw_timestamp = data.get("timestamp")
        if raw_timestamp:
            try:
                self._last_realtime_update = parse_iso_timestamp(str(raw_timestamp))
                await self._hide_stale_overlay()  # Only hide if valid timestamp
            except (ValueError, TypeError):
                logger.debug(f"PriceChart._on_price_update: unparseable timestamp {raw_timestamp!r}")
                self._last_realtime_update = None  # Keep stale overlay
        else:
            logger.debug("PriceChart._on_price_update: missing timestamp, keeping stale overlay")
            self._last_realtime_update = None  # Keep stale overlay

        # Update the last candle's close (simplified real-time update)
        last_candle = self._candles[-1]
        updated_candle = {
            "time": last_candle.time,
            "open": last_candle.open,
            "high": max(last_candle.high, price),
            "low": min(last_candle.low, price),
            "close": price,
        }

        await ui.run_javascript(f"""
            const chartRef = window.__charts['{self._chart_id}'];
            if (chartRef) {{
                chartRef.candlestickSeries.update({json.dumps(updated_candle)});
            }}
        """)

    # ===== Realtime Feed Staleness Detection =====

    REALTIME_STALE_THRESHOLD_S = 60  # Show warning if no updates for 60s
    REALTIME_FALLBACK_THRESHOLD_S = 180  # Show fallback chart after 3 minutes

    def _start_realtime_staleness_monitor(self, tracker: Callable[[ui.timer], None]) -> None:
        """Start timer to check for realtime feed staleness.

        Args:
            tracker: Callback to register timer with OrderEntryContext for lifecycle management.
        """
        def check_staleness_tracked() -> None:
            """Create tracked task for staleness check (prevent task leaks)."""
            task = asyncio.create_task(self._check_realtime_staleness())
            self._pending_update_tasks.add(task)  # Reuse existing tracked set
            task.add_done_callback(self._pending_update_tasks.discard)

        self._staleness_timer = ui.timer(
            10.0,  # Check every 10 seconds
            check_staleness_tracked,
        )
        tracker(self._staleness_timer)  # Register for cleanup

    async def _check_realtime_staleness(self) -> None:
        """Check if realtime feed is stale and update UI accordingly."""
        if not self._current_symbol or not self._last_realtime_update:
            return

        age_s = (datetime.now(timezone.utc) - self._last_realtime_update).total_seconds()

        if age_s > self.REALTIME_FALLBACK_THRESHOLD_S:
            # Feed is dead - show fallback
            await self._show_stale_fallback_chart()
        elif age_s > self.REALTIME_STALE_THRESHOLD_S:
            # Feed is stale - show warning overlay
            await self._show_stale_overlay(int(age_s))

    async def _show_stale_overlay(self, age_s: int) -> None:
        """Show stale data warning overlay on chart."""
        await ui.run_javascript(f"""
            const container = document.getElementById('{self._container_id}');
            let overlay = container.querySelector('.stale-overlay');
            if (!overlay) {{
                overlay = document.createElement('div');
                overlay.className = 'stale-overlay';
                overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;background:rgba(255,165,0,0.9);color:#000;padding:4px;text-align:center;font-size:12px;z-index:100;';
                container.appendChild(overlay);
            }}
            overlay.textContent = 'Real-time feed stale ({age_s}s) - Data may be outdated';
            overlay.style.display = 'block';
        """)

    async def _hide_stale_overlay(self) -> None:
        """Hide stale data warning overlay."""
        await ui.run_javascript(f"""
            const container = document.getElementById('{self._container_id}');
            const overlay = container?.querySelector('.stale-overlay');
            if (overlay) overlay.style.display = 'none';
        """)

    async def _show_stale_fallback_chart(self) -> None:
        """Show fallback UI when realtime feed is dead."""
        await ui.run_javascript(f"""
            const container = document.getElementById('{self._container_id}');
            let overlay = container.querySelector('.fallback-overlay');
            if (!overlay) {{
                overlay = document.createElement('div');
                overlay.className = 'fallback-overlay';
                overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;background:rgba(30,30,30,0.95);display:flex;align-items:center;justify-content:center;z-index:200;';
                overlay.innerHTML = `
                    <div style="text-align:center;color:#888;">
                        <div style="font-size:32px;margin-bottom:8px;">📊</div>
                        <div style="font-size:14px;">Real-time feed unavailable</div>
                        <div style="font-size:12px;color:#666;margin-top:4px;">Chart data may be outdated</div>
                        <button onclick="this.parentElement.parentElement.style.display='none'" style="margin-top:12px;padding:4px 12px;cursor:pointer;">Dismiss</button>
                    </div>
                `;
                container.appendChild(overlay);
            }}
            overlay.style.display = 'flex';
        """)
```

##### 4.3.3 VWAP/TWAP Overlay

```python
async def add_vwap_overlay(self, vwap_data: list[dict]) -> None:
    """Add VWAP line overlay to chart.

    VWAP = Cumulative(Price * Volume) / Cumulative(Volume)
    Data source: Calculated from candle data or fetched from algo order context.
    """
    if not vwap_data:
        return

    # Format for line series
    line_data = [
        {"time": d["time"], "value": d["vwap"]}
        for d in vwap_data
    ]

    await ui.run_javascript(f"""
        const chartRef = window.__charts['{self._chart_id}'];
        if (chartRef) {{
            // Remove existing VWAP if present
            if (chartRef.vwapSeries) {{
                chartRef.chart.removeSeries(chartRef.vwapSeries);
            }}

            // Add VWAP line series
            chartRef.vwapSeries = chartRef.chart.addLineSeries({{
                color: '#2196F3',
                lineWidth: 2,
                title: 'VWAP',
            }});
            chartRef.vwapSeries.setData({json.dumps(line_data)});
        }}
    """)

async def add_twap_overlay(self, twap_data: list[dict]) -> None:
    """Add TWAP line overlay to chart.

    TWAP = Simple average of prices over time intervals
    Data source: Calculated from candle close prices or fetched from algo order context.

    For algo orders using TWAP execution:
    - twap_data comes from the algo order's execution plan
    - Shows target execution price vs actual fills
    """
    if not twap_data:
        return

    # Format for line series
    line_data = [
        {"time": d["time"], "value": d["twap"]}
        for d in twap_data
    ]

    await ui.run_javascript(f"""
        const chartRef = window.__charts['{self._chart_id}'];
        if (chartRef) {{
            // Remove existing TWAP if present
            if (chartRef.twapSeries) {{
                chartRef.chart.removeSeries(chartRef.twapSeries);
            }}

            // Add TWAP line series (orange to distinguish from VWAP)
            chartRef.twapSeries = chartRef.chart.addLineSeries({{
                color: '#FF9800',
                lineWidth: 2,
                lineStyle: 1,  // Dashed
                title: 'TWAP',
            }});
            chartRef.twapSeries.setData({json.dumps(line_data)});
        }}
    """)

def calculate_vwap_from_candles(self, candles: list[CandleData]) -> list[dict]:
    """Calculate VWAP from candle data."""
    if not candles:
        return []

    vwap_data = []
    cumulative_volume = 0
    cumulative_pv = 0.0  # Price * Volume

    for candle in candles:
        typical_price = (candle.high + candle.low + candle.close) / 3
        volume = candle.volume or 0

        cumulative_volume += volume
        cumulative_pv += typical_price * volume

        if cumulative_volume > 0:
            vwap = cumulative_pv / cumulative_volume
            vwap_data.append({"time": candle.time, "vwap": vwap})

    return vwap_data

def calculate_twap_from_candles(self, candles: list[CandleData]) -> list[dict]:
    """Calculate TWAP from candle data (simple time-weighted average)."""
    if not candles:
        return []

    twap_data = []
    cumulative_price = 0.0
    count = 0

    for candle in candles:
        count += 1
        cumulative_price += candle.close
        twap = cumulative_price / count
        twap_data.append({"time": candle.time, "twap": twap})

    return twap_data
```

##### 4.3.4 Fallback to Static Chart

```python
def _escape_html(text: str) -> str:
    """Escape HTML special characters to prevent XSS."""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;"))

async def _show_fallback_chart(self, symbol: str) -> None:
    """Show static chart when real-time feed unavailable.

    SECURITY: Symbol is:
    1. Validated via validate_and_normalize_symbol at entry points
    2. JSON-encoded for safe JS interpolation (prevents template literal injection)
    3. HTML-escaped before DOM insertion (prevents XSS via innerHTML)
    """
    import json

    # JSON-encode for safe JS interpolation (escapes backticks, ${}, etc.)
    # This is the primary defense against template literal injection
    # NOTE: json.dumps() also properly escapes any special characters in the symbol
    js_safe_symbol = json.dumps(symbol)

    # Generate simple line chart from last known prices
    # NOTE: js_safe_symbol is already quoted by json.dumps, so no extra quotes needed
    # Symbol is inserted via textContent (not innerHTML) to prevent XSS
    await ui.run_javascript(f"""
        const container = document.getElementById('{self._container_id}');
        const symbolForDisplay = {js_safe_symbol};
        if (container) {{
            // Use textContent for the symbol to avoid innerHTML XSS
            const messageDiv = document.createElement('div');
            messageDiv.style.cssText = `
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100%;
                background: #1e1e1e;
                color: #888;
                font-size: 14px;
            `;
            const contentDiv = document.createElement('div');
            contentDiv.style.textAlign = 'center';

            const iconDiv = document.createElement('div');
            iconDiv.style.cssText = 'font-size: 24px; margin-bottom: 8px;';
            iconDiv.textContent = '📊';

            const symbolDiv = document.createElement('div');
            symbolDiv.textContent = 'Chart unavailable for ' + symbolForDisplay;

            const feedDiv = document.createElement('div');
            feedDiv.style.cssText = 'font-size: 12px; color: #666;';
            feedDiv.textContent = 'Real-time feed not connected';

            contentDiv.appendChild(iconDiv);
            contentDiv.appendChild(symbolDiv);
            contentDiv.appendChild(feedDiv);
            messageDiv.appendChild(contentDiv);

            container.innerHTML = '';
            container.appendChild(messageDiv);
        }}
    """)

async def dispose(self) -> None:
    """Clean up chart component resources.

    Called by OrderEntryContext on page unload.
    Clears chart rendering, cancels pending tasks, and clears state.
    """
    # Cancel any pending update tasks to prevent state updates after dispose
    for task in list(self._pending_update_tasks):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    self._pending_update_tasks.clear()

    # Clear chart by removing from DOM if needed
    if self._container_id:
        try:
            await ui.run_javascript(f"""
                const container = document.getElementById('{self._container_id}');
                if (container) {{ container.innerHTML = ''; }}
            """)
        except Exception:
            pass  # Best effort cleanup

    # Clear internal state
    self._current_symbol = None
    self._chart_instance = None
    # Note: Subscriptions are managed by OrderEntryContext
```

---

### T4.4: Watchlist Component - MEDIUM PRIORITY

**Goal:** Compact symbol list with quick action capability.

**Acceptance Criteria:**
- [ ] Watchlist displays last price, change, sparkline
- [ ] Click to select updates order ticket
- [ ] Drag to reorder
- [ ] Add/remove symbols
- [ ] Watchlist persists via workspace persistence (P6T1.4)

**Files:**
- Create: `apps/web_console_ng/components/watchlist.py`
- Create: `apps/web_console_ng/components/sparkline.py`

#### Implementation Details

##### 4.4.1 Watchlist Component Architecture

```python
# apps/web_console_ng/components/watchlist.py
import html  # For escaping symbols in HTML attributes (security)
import math  # For isfinite() sparkline validation

@dataclass
class WatchlistItem:
    """Single watchlist item with price data."""
    symbol: str
    last_price: Decimal | None = None
    prev_close: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    sparkline_data: list[float] = field(default_factory=list)
    timestamp: datetime | None = None


class WatchlistComponent:
    """Persistent watchlist with real-time updates and quick actions."""

    WORKSPACE_KEY = "watchlist.main"
    MAX_SYMBOLS = 20  # Limit to prevent excessive subscriptions
    SPARKLINE_POINTS = 20  # Number of historical points for sparkline

    def __init__(
        self,
        workspace_service: WorkspacePersistenceService,
        realtime_updater: RealtimeUpdater,
        trading_client: AsyncTradingClient,
        user_id: str,
        on_symbol_selected: Callable[[str | None], Awaitable[None]],  # Async callback (None clears selection)
    ):
        self._workspace = workspace_service
        self._realtime = realtime_updater
        self._client = trading_client
        self._user_id = user_id
        self._on_symbol_selected = on_symbol_selected

        # State
        self._items: dict[str, WatchlistItem] = {}
        self._symbol_order: list[str] = []
        self._selected_symbol: str | None = None

        # Pending selection task (for race condition prevention)
        # We cancel the previous task before starting a new one to ensure
        # the last click wins deterministically on fast repeated clicks.
        self._pending_selection_task: asyncio.Task | None = None

        # THROTTLING STATE: Per-row updates throttled to 2Hz (500ms) to prevent UI jank
        self._row_render_interval: float = 0.5  # 500ms = 2Hz max per symbol
        self._last_row_render: dict[str, float] = {}  # symbol -> monotonic timestamp of last render
        self._pending_row_renders: set[str] = set()  # symbols waiting for throttle window
        # NOTE: call_later() returns TimerHandle, not Task - use correct type for mypy strict
        self._render_batch_handle: asyncio.TimerHandle | None = None  # batched render handle

        # UI elements
        self._list_container: ui.column | None = None
        self._add_input: ui.input | None = None

    def _log_task_exception(self, task: asyncio.Task) -> None:
        """Done callback to log exceptions from async tasks.

        Prevents "Task exception was never retrieved" warnings by consuming
        the exception and logging it. Errors in selection callbacks should not
        crash the component but should be visible for debugging.
        """
        try:
            exc = task.exception()
            if exc is not None:
                logger.error(f"Watchlist selection task failed: {exc}", exc_info=exc)
        except asyncio.CancelledError:
            pass  # Task was cancelled - expected, not an error

    async def initialize(self, timer_tracker: Callable[[ui.timer], None]) -> None:
        """Load saved watchlist from workspace persistence and start timers.

        Args:
            timer_tracker: Callback to register timers with OrderEntryContext for lifecycle management.

        CRITICAL: Timers are created HERE (not in create()) to ensure timer_tracker
        is available for registration. create() only builds UI elements.
        """
        self._timer_tracker = timer_tracker

        # Initialize drag-to-reorder (one-shot timer, tracked for early disposal)
        init_sortable_timer = ui.timer(0.1, self._init_sortable, once=True)
        timer_tracker(init_sortable_timer)

        # Load persisted watchlist
        state = await self._workspace.load_grid_state(
            user_id=self._user_id,
            grid_id=self.WORKSPACE_KEY,
        )

        if state:
            raw_symbols = state.get("symbols", [])
            # SECURITY: Validate persisted symbols before using
            # Malformed symbols could create invalid channel names or XSS via DOM attributes
            # DE-DUPLICATE: Prevent duplicate symbols which could cause ownership issues
            # (removing one duplicate would unsubscribe the channel while other needs it)
            validated_symbols = []
            seen_symbols: set[str] = set()
            for symbol in raw_symbols[:self.MAX_SYMBOLS]:
                try:
                    validated = validate_and_normalize_symbol(symbol)
                    # Skip duplicates
                    if validated in seen_symbols:
                        logger.warning(f"Duplicate watchlist symbol skipped: {validated}")
                        continue
                    seen_symbols.add(validated)
                    validated_symbols.append(validated)
                except ValueError as exc:
                    # Skip invalid symbols from persistence - log and continue
                    logger.warning(f"Invalid persisted watchlist symbol: {symbol!r} - {exc}")
                    continue

            self._symbol_order = validated_symbols

            # Request OrderEntryContext to subscribe for all valid symbols
            for symbol in self._symbol_order:
                self._items[symbol] = WatchlistItem(symbol=symbol)
                await self._request_subscribe(symbol)

            # Fetch initial prices
            await self._fetch_all_prices()
        else:
            # Default watchlist
            self._symbol_order = ["SPY", "QQQ", "AAPL", "MSFT", "TSLA"]
            for symbol in self._symbol_order:
                self._items[symbol] = WatchlistItem(symbol=symbol)
                await self._request_subscribe(symbol)
            await self._fetch_all_prices()
            await self._save_watchlist()
```

##### 4.4.2 Watchlist UI with Drag-to-Reorder

```python
def create(self) -> ui.card:
    """Create the watchlist UI."""

    with ui.card().classes("p-2 w-64") as card:
        # Header
        with ui.row().classes("justify-between items-center mb-2"):
            ui.label("Watchlist").classes("text-sm font-bold")
            ui.button(
                icon="settings",
                on_click=self._show_settings,
            ).classes("w-8 h-8").props("flat dense")

        # Add symbol input
        with ui.row().classes("gap-1 mb-2"):
            self._add_input = ui.input(
                placeholder="Add symbol",
            ).classes("flex-grow").props("dense outlined")
            ui.button(
                icon="add",
                on_click=self._add_symbol_from_input,
            ).classes("w-8 h-8").props("flat dense")

        # Symbol list (sortable via JS)
        self._list_container = ui.column().classes("gap-1 w-full")
        self._list_container.props('id="watchlist-items"')

        self._render_items()

        # NOTE: Drag-to-reorder timer is started in initialize(), not here,
        # to ensure timer_tracker is available for registration.

    return card

def _render_items(self) -> None:
    """Render all watchlist items."""

    if self._list_container:
        self._list_container.clear()

    with self._list_container:
        for symbol in self._symbol_order:
            item = self._items.get(symbol)
            if item:
                self._render_item(item)

def _render_item(self, item: WatchlistItem) -> None:
    """Render single watchlist item.

    SECURITY: Symbol is escaped when used in HTML attributes (props) to prevent
    XSS/attribute injection. While symbols are validated at ingestion via
    validate_and_normalize_symbol(), we apply defense-in-depth escaping here.
    """
    is_selected = item.symbol == self._selected_symbol

    # SECURITY: Escape symbol for use in HTML attribute (defense-in-depth)
    # Even though symbols are validated at ingestion, we escape to prevent
    # any potential XSS if validation is ever bypassed or malformed data persisted
    escaped_symbol = html.escape(item.symbol, quote=True)

    # Row ID for per-row JavaScript updates (used by _render_single_item)
    row_id = f"watchlist-row-{escaped_symbol}"

    with ui.row().classes(
        f"w-full p-2 rounded cursor-pointer hover:bg-gray-700 "
        f"{'bg-blue-900' if is_selected else ''}"
    ).props(f'id="{row_id}" data-symbol="{escaped_symbol}"') as row:

        row.on("click", lambda s=item.symbol: self._select_symbol(s))

        # Symbol and price
        with ui.column().classes("flex-grow"):
            with ui.row().classes("justify-between"):
                # NOTE: ui.label auto-escapes text content, but we use
                # validated symbol here (safe from XSS in text context)
                ui.label(item.symbol).classes("font-bold text-sm")
                # IMPORTANT: "price" class used by _render_single_item for DOM updates
                if item.last_price:
                    ui.label(f"${item.last_price:.2f}").classes("price text-sm font-mono")
                else:
                    ui.label("N/A").classes("price text-sm text-gray-500")

            with ui.row().classes("justify-between items-center"):
                # Change badge
                # IMPORTANT: "change" class used by _render_single_item for DOM updates
                if item.change_pct is not None:
                    sign = "+" if item.change_pct >= 0 else ""
                    color = "text-green-400" if item.change_pct >= 0 else "text-red-400"
                    ui.label(f"{sign}{item.change_pct:.2f}%").classes(f"change text-xs {color}")
                else:
                    ui.label("--").classes("change text-xs text-gray-500")

                # Sparkline
                if item.sparkline_data:
                    self._render_sparkline(item.sparkline_data, item.change_pct)

        # Remove button (on hover)
        ui.button(
            icon="close",
            on_click=lambda s=item.symbol: self._remove_symbol(s),
        ).classes("w-6 h-6 opacity-0 hover:opacity-100").props("flat dense")

def _render_sparkline(self, data: list[float], change_pct: Decimal | None) -> None:
    """Render inline sparkline SVG.

    SECURITY: Validates and sanitizes data points before rendering.
    Non-numeric values are dropped to prevent SVG injection/breakage.
    """
    # Validate and filter data - only keep finite numeric values
    validated_data: list[float] = []
    for val in data:
        try:
            # Coerce to float and check for finite value
            float_val = float(val)
            if math.isfinite(float_val):
                validated_data.append(float_val)
            else:
                logger.debug(f"Dropped non-finite sparkline value: {val}")
        except (TypeError, ValueError) as exc:
            logger.debug(f"Dropped invalid sparkline value: {val!r} - {exc}")
            continue

    if len(validated_data) < 2:
        return

    width = 50
    height = 20
    color = "#4ade80" if (change_pct or 0) >= 0 else "#f87171"

    # Normalize data to fit in height
    min_val = min(validated_data)
    max_val = max(validated_data)
    range_val = max_val - min_val or 1

    points = []
    for i, val in enumerate(validated_data):
        x = i * width / (len(validated_data) - 1)
        y = height - ((val - min_val) / range_val * height)
        points.append(f"{x:.1f},{y:.1f}")

    polyline = " ".join(points)

    ui.html(f"""
        <svg width="{width}" height="{height}" class="ml-2">
            <polyline
                points="{polyline}"
                fill="none"
                stroke="{color}"
                stroke-width="1.5"
            />
        </svg>
    """)
```

##### 4.4.3 Drag-to-Reorder with SortableJS

```python
async def _init_sortable(self) -> None:
    """Initialize SortableJS for drag-to-reorder.

    SECURITY: Uses load_cdn_asset_with_fallback() to load SortableJS with SRI hash.
    This prevents CDN compromise from injecting malicious code.
    See "CDN Asset Security" section for implementation details.
    """
    # Load SortableJS with SRI protection (uses load_cdn_asset_with_fallback)
    await load_cdn_asset_with_fallback("sortablejs")

    # Initialize Sortable on the watchlist container
    await ui.run_javascript("""
        const el = document.getElementById('watchlist-items');
        if (el && !el._sortable) {
            el._sortable = new Sortable(el, {
                animation: 150,
                ghostClass: 'bg-gray-800',
                onEnd: function(evt) {
                    // Dispatch custom event with new order
                    const symbols = Array.from(el.children)
                        .map(row => row.dataset.symbol)
                        .filter(Boolean);
                    window.dispatchEvent(new CustomEvent('watchlist_reorder', {
                        detail: { symbols: symbols }
                    }));
                }
            });
        }
    """)

    # Handle reorder events
    ui.on("watchlist_reorder", self._on_reorder, args=["detail"])

async def _on_reorder(self, detail: dict) -> None:
    """Handle watchlist reorder from drag operation.

    SECURITY: Validates payload before accepting new order.
    - Must be a list of strings
    - Must contain exactly the same symbols as current watchlist
    - No duplicates allowed
    """
    if not isinstance(detail, dict):
        logger.warning(f"Invalid reorder detail type: {type(detail)}")
        return

    new_order = detail.get("symbols")

    # Validate new_order is a non-empty list of strings
    if not isinstance(new_order, list) or not new_order:
        logger.warning(f"Invalid reorder symbols type: {type(new_order)}")
        return

    if not all(isinstance(s, str) for s in new_order):
        logger.warning("Reorder symbols contains non-string elements")
        return

    # Check for duplicates
    if len(new_order) != len(set(new_order)):
        logger.warning("Reorder symbols contains duplicates")
        return

    # Validate exactly same symbols as current watchlist (no additions/removals)
    if set(new_order) != set(self._symbol_order):
        logger.warning(
            f"Reorder symbols mismatch: got {set(new_order)}, expected {set(self._symbol_order)}"
        )
        return

    self._symbol_order = new_order
    await self._save_watchlist()
```

##### 4.4.4 Symbol Management

```python
async def _add_symbol_from_input(self) -> None:
    """Add symbol from input field.

    SECURITY: Symbol is validated against allowlist before adding.
    This prevents XSS via malformed symbols in watchlist display.
    """
    if not self._add_input:
        return

    raw_symbol = self._add_input.value.strip()
    self._add_input.value = ""

    if not raw_symbol:
        return

    # SECURITY: Validate and normalize symbol format FIRST (before any display)
    try:
        symbol = validate_and_normalize_symbol(raw_symbol)
    except ValueError as exc:
        ui.notify(f"Invalid symbol format: {raw_symbol}", type="negative")
        return

    if symbol in self._items:
        ui.notify(f"{symbol} already in watchlist", type="warning")
        return

    if len(self._items) >= self.MAX_SYMBOLS:
        ui.notify(f"Maximum {self.MAX_SYMBOLS} symbols allowed", type="warning")
        return

    # Validate symbol exists in broker's asset list (optional UX improvement)
    try:
        is_valid = await self._client.validate_symbol(symbol)
        if not is_valid:
            ui.notify(f"Unknown symbol: {symbol}", type="negative")
            return
    except Exception:
        # SECURITY: Do NOT allow on validation exception - symbol format is valid
        # but we can't confirm it exists. Block to prevent noisy subscriptions.
        ui.notify(f"Could not validate symbol: {symbol}. Try again.", type="warning")
        return

    # Add to watchlist
    self._items[symbol] = WatchlistItem(symbol=symbol)
    self._symbol_order.append(symbol)

    # Subscribe to updates
    await self._request_subscribe(symbol)

    # Fetch initial price
    await self._fetch_price(symbol)

    # Re-render and save
    self._render_items()
    await self._save_watchlist()

    ui.notify(f"Added {symbol} to watchlist", type="positive")

async def _remove_symbol(self, symbol: str) -> None:
    """Remove symbol from watchlist.

    CRITICAL: If removing the selected symbol, must notify OrderEntryContext
    to clear its selection and release the price subscription. Without this,
    OrderEntryContext would continue holding stale selection state.
    """

    if symbol not in self._items:
        return

    # Unsubscribe from updates
    await self._request_unsubscribe(symbol)

    # Remove from state
    del self._items[symbol]
    self._symbol_order.remove(symbol)

    # Clear selection if removed AND notify OrderEntryContext
    if self._selected_symbol == symbol:
        self._selected_symbol = None

        # Cancel pending selection task to prevent race condition
        if self._pending_selection_task and not self._pending_selection_task.done():
            self._pending_selection_task.cancel()

        # CRITICAL: Notify OrderEntryContext to clear selection and release subscription
        if self._on_symbol_selected:
            self._pending_selection_task = asyncio.create_task(
                self._on_symbol_selected(None)
            )
            # Add done callback to log exceptions (prevent "Task exception was never retrieved")
            self._pending_selection_task.add_done_callback(self._log_task_exception)

    # Re-render and save
    self._render_items()
    await self._save_watchlist()

    ui.notify(f"Removed {symbol} from watchlist", type="info")

def _select_symbol(self, symbol: str) -> None:
    """Select symbol and notify OrderEntryContext.

    NOTE: _on_symbol_selected is an async callback (OrderEntryContext.on_symbol_selected).
    We use asyncio.create_task to avoid blocking the UI thread.

    RACE PREVENTION: On fast repeated clicks, we cancel any pending selection
    task before starting a new one. This ensures the last click wins
    deterministically, preventing OrderEntryContext from subscribing to the
    wrong symbol if tasks complete out of order.
    """
    self._selected_symbol = symbol
    self._render_items()  # Re-render to show selection

    # Cancel pending selection task to prevent race condition
    if self._pending_selection_task and not self._pending_selection_task.done():
        self._pending_selection_task.cancel()

    # Notify OrderEntryContext (async callback - schedule as task)
    if self._on_symbol_selected:
        self._pending_selection_task = asyncio.create_task(
            self._on_symbol_selected(symbol)
        )
        # Add done callback to log exceptions (prevent "Task exception was never retrieved")
        self._pending_selection_task.add_done_callback(self._log_task_exception)
```

##### 4.4.5 Persistence Integration (P6T1.4)

```python
async def _save_watchlist(self) -> None:
    """Save watchlist to workspace persistence."""

    state = {
        "symbols": self._symbol_order,
        "version": 1,
    }

    await self._workspace.save_grid_state(
        user_id=self._user_id,
        grid_id=self.WORKSPACE_KEY,
        state=state,
    )

"""
SUBSCRIPTION OWNERSHIP MODEL FOR WATCHLIST:

Watchlist does NOT subscribe directly to Redis channels.
Instead, it requests OrderEntryContext to manage subscriptions on its behalf.

Data Flow:
1. Watchlist calls on_symbols_changed() callback when symbols are added/removed
2. OrderEntryContext subscribes/unsubscribes to price channels
3. OrderEntryContext dispatches price updates to Watchlist via set_symbol_price_data()

This maintains the single-owner subscription model while supporting
multiple symbol tracking in the watchlist.
"""

def __init__(
    self,
    ...,
    on_subscribe_symbol: Callable[[str], Awaitable[None]] | None = None,
    on_unsubscribe_symbol: Callable[[str], Awaitable[None]] | None = None,
):
    ...
    # Callbacks to request OrderEntryContext to manage subscriptions
    self._on_subscribe_symbol = on_subscribe_symbol
    self._on_unsubscribe_symbol = on_unsubscribe_symbol

async def _request_subscribe(self, symbol: str) -> None:
    """Request OrderEntryContext to subscribe to a symbol's price channel.

    Watchlist does NOT subscribe directly to Redis.
    """
    if self._on_subscribe_symbol:
        await self._on_subscribe_symbol(symbol)

async def _request_unsubscribe(self, symbol: str) -> None:
    """Request OrderEntryContext to unsubscribe from a symbol's price channel.

    Watchlist does NOT unsubscribe directly from Redis.
    """
    if self._on_unsubscribe_symbol:
        await self._on_unsubscribe_symbol(symbol)

def set_symbol_price_data(self, symbol: str, data: dict) -> None:
    """Called by OrderEntryContext when price data updates for a watchlist symbol.

    This is the ONLY way Watchlist receives real-time price updates.
    Watchlist does NOT subscribe directly to price.updated.{symbol} channels.

    PERFORMANCE: Uses throttled per-row updates to prevent UI jank:
    1. Updates are throttled to max 2Hz (500ms) per symbol
    2. Per-row update via _render_single_item() instead of full list
    3. Pending updates are batched when multiple arrive in same throttle window
    """
    # TYPE GUARD: Validate payload structure
    if not isinstance(data, dict):
        logger.warning(f"Watchlist received invalid data type for {symbol}: {type(data).__name__}")
        return  # Silently ignore malformed data (display-only component)

    item = self._items.get(symbol)
    if not item:
        return

    # Update price data
    # GUARD: Wrap parsing in try/except to handle malformed pub/sub data
    # Use `is not None` to handle 0/empty correctly, and clear on invalid/missing data
    # FAIL-CLOSED: Missing field = clear to None (not keep stale value)
    raw_price = data.get("price")
    if raw_price is not None:
        try:
            parsed_price = Decimal(str(raw_price))
            if parsed_price.is_finite() and parsed_price > 0:
                # is_finite() rejects NaN/Infinity that would bypass > 0 check
                # (Decimal('Infinity') > 0 returns True!)
                item.last_price = parsed_price
            else:
                # Invalid/non-finite price - clear to show staleness
                item.last_price = None
        except (InvalidOperation, ValueError, TypeError) as exc:
            logger.warning(f"Invalid price in watchlist update for {item.symbol}: {raw_price!r} - {exc}")
            # Clear on invalid data to show staleness (not keep stale value)
            item.last_price = None
    else:
        # FAIL-CLOSED: Missing price field = clear to show staleness
        item.last_price = None

    raw_timestamp = data.get("timestamp")
    if raw_timestamp is not None:
        try:
            item.timestamp = parse_iso_timestamp(raw_timestamp)
        except (ValueError, TypeError) as exc:
            logger.warning(f"Invalid timestamp in watchlist update for {item.symbol}: {raw_timestamp!r} - {exc}")
            item.timestamp = None  # Mark as stale
    else:
        # FAIL-CLOSED: Missing timestamp field = clear to show staleness
        item.timestamp = None

    # Clear change/change_pct when price is missing/invalid
    if item.last_price is None:
        item.change = None
        item.change_pct = None

    # Calculate change if we have valid prev_close
    # GUARD: prev_close must be positive and finite to avoid DivisionByZero/InvalidOperation
    if (
        item.last_price is not None
        and item.prev_close is not None
        and item.prev_close.is_finite()
        and item.prev_close > 0
    ):
        item.change = item.last_price - item.prev_close
        item.change_pct = (item.change / item.prev_close) * 100
    elif item.last_price is not None:
        # Have price but invalid prev_close - show price without change
        item.change = None
        item.change_pct = None

    # Update sparkline (append latest price)
    # SECURITY: Validate price is a valid finite number before adding to sparkline
    if item.last_price:
        try:
            price_float = float(item.last_price)
            if math.isfinite(price_float) and price_float > 0:
                item.sparkline_data.append(price_float)
                if len(item.sparkline_data) > self.SPARKLINE_POINTS:
                    item.sparkline_data = item.sparkline_data[-self.SPARKLINE_POINTS:]
        except (TypeError, ValueError) as exc:
            logger.warning(f"Invalid sparkline price for {item.symbol}: {item.last_price!r} - {exc}")

    # THROTTLED PER-ROW UPDATE: Only update this specific row, throttled to 2Hz
    self._schedule_row_render(symbol)

def _schedule_row_render(self, symbol: str) -> None:
    """Schedule a throttled per-row render for the given symbol.

    THROTTLING STRATEGY:
    - Max 2Hz (500ms interval) per symbol to prevent UI jank
    - Multiple updates within throttle window are batched
    - Uses asyncio.call_later for efficient scheduling

    This prevents excessive renders when receiving rapid tick updates
    while ensuring the UI stays reasonably current.
    """
    import time

    now = time.monotonic()
    last_render = self._last_row_render.get(symbol, 0.0)
    time_since_last = now - last_render

    if time_since_last >= self._row_render_interval:
        # Outside throttle window - render immediately
        self._last_row_render[symbol] = now
        self._render_single_item(symbol)
    else:
        # Inside throttle window - schedule for later
        self._pending_row_renders.add(symbol)
        # TimerHandle has .cancelled() not .done() - check if we need to schedule
        if self._render_batch_handle is None or self._render_batch_handle.cancelled():
            # Schedule batch render after throttle window expires
            delay = self._row_render_interval - time_since_last
            # Use get_running_loop() (Python 3.11+) instead of deprecated get_event_loop()
            loop = asyncio.get_running_loop()
            self._render_batch_handle = loop.call_later(
                delay, self._flush_pending_renders
            )

def _flush_pending_renders(self) -> None:
    """Render all pending row updates.

    Called after throttle window expires. Renders each pending symbol
    as a batch to minimize DOM operations.
    """
    import time

    # Clear the task handle so new schedules can be created
    self._render_batch_handle = None

    if not self._pending_row_renders:
        return

    now = time.monotonic()
    symbols_to_render = list(self._pending_row_renders)
    self._pending_row_renders.clear()

    for symbol in symbols_to_render:
        self._last_row_render[symbol] = now
        self._render_single_item(symbol)

def _render_single_item(self, symbol: str) -> None:
    """Render a single watchlist row without re-rendering the entire list.

    PER-ROW UPDATE: Updates only the affected row's DOM elements by ID.
    This is much more efficient than _render_items() for real-time updates.

    Row elements are identified by data attribute: data-symbol="{symbol}"
    """
    item = self._items.get(symbol)
    if not item or not self._list_container:
        return

    # Find the row element by symbol (NiceGUI/Quasar uses refs or data attributes)
    # Implementation uses JavaScript interop to update only the specific row
    row_id = f"watchlist-row-{symbol}"

    # Format display values
    price_display = f"${item.last_price:.2f}" if item.last_price else "—"
    change_display = f"{item.change:+.2f}" if item.change is not None else "—"
    change_pct_display = f"({item.change_pct:+.1f}%)" if item.change_pct is not None else ""
    change_color = "green" if (item.change or 0) >= 0 else "red"

    # Update row via JavaScript (efficient single-row DOM update)
    ui.run_javascript(f'''
        const row = document.getElementById("{row_id}");
        if (row) {{
            row.querySelector(".price").textContent = "{price_display}";
            row.querySelector(".change").textContent = "{change_display} {change_pct_display}";
            row.querySelector(".change").style.color = "{change_color}";
        }}
    ''')

async def dispose(self) -> None:
    """Clean up watchlist component resources.

    Called by OrderEntryContext on page unload.
    Cancels pending tasks and clears state.
    """
    # Cancel any pending selection task
    if self._pending_selection_task and not self._pending_selection_task.done():
        self._pending_selection_task.cancel()
        try:
            await self._pending_selection_task
        except asyncio.CancelledError:
            pass

    # Cancel pending render batch (throttle cleanup)
    if self._render_batch_handle is not None:
        # call_later returns a TimerHandle, cancel it
        self._render_batch_handle.cancel()
        self._render_batch_handle = None
    self._pending_row_renders.clear()
    self._last_row_render.clear()

    # Clear state
    self._items.clear()
    self._symbol_order.clear()
    self._selected_symbol = None
    self._pending_selection_task = None
    # Note: Subscriptions are managed by OrderEntryContext, not Watchlist
```

##### 4.4.6 Sparkline Component

```python
# apps/web_console_ng/components/sparkline.py

class SparklineComponent:
    """Standalone sparkline SVG component."""

    def __init__(
        self,
        width: int = 50,
        height: int = 20,
        stroke_width: float = 1.5,
    ):
        self._width = width
        self._height = height
        self._stroke_width = stroke_width

    def render(
        self,
        data: list[float],
        positive_color: str = "#4ade80",
        negative_color: str = "#f87171",
    ) -> str:
        """Render sparkline as SVG string."""

        if len(data) < 2:
            return ""

        # Determine color based on overall trend
        color = positive_color if data[-1] >= data[0] else negative_color

        # Normalize data to fit in height
        min_val = min(data)
        max_val = max(data)
        range_val = max_val - min_val or 1

        points = []
        for i, val in enumerate(data):
            x = i * self._width / (len(data) - 1)
            y = self._height - ((val - min_val) / range_val * self._height)
            points.append(f"{x:.1f},{y:.1f}")

        polyline = " ".join(points)

        return f"""
            <svg width="{self._width}" height="{self._height}">
                <polyline
                    points="{polyline}"
                    fill="none"
                    stroke="{color}"
                    stroke-width="{self._stroke_width}"
                />
            </svg>
        """

    def create(self, data: list[float]) -> ui.html:
        """Create NiceGUI element with sparkline."""
        svg = self.render(data)
        return ui.html(svg)
```

---

## Dashboard Integration

### Modified Dashboard Layout

```python
# apps/web_console_ng/pages/dashboard.py

@main_layout
async def dashboard_page():
    """Main trading dashboard with embedded order entry context."""

    # Get shared services
    trading_client = get_trading_client()
    state_manager = app.storage.client.get("state_manager")
    realtime_updater = app.storage.client.get("realtime_updater")
    connection_monitor = app.storage.client.get("connection_monitor")
    redis_client = app.storage.client.get("redis")  # For authoritative safety checks
    user_role = app.storage.client.get("user_role")  # User role for authorization
    user_strategies = app.storage.client.get("user_strategies", [])  # User's strategies

    # Initialize order entry context
    order_context = OrderEntryContext(
        realtime_updater=realtime_updater,
        trading_client=trading_client,
        state_manager=state_manager,
        connection_monitor=connection_monitor,
        redis=redis_client,
        user_id=user_id,
        role=user_role,
        strategies=user_strategies,
    )

    # Store in per-tab storage
    app.storage.client["order_context"] = order_context

    # Register cleanup on page unload (subscription lifecycle management)
    # LIFECYCLE PATTERN: Use app.on_disconnect which is the established pattern
    # in apps/web_console_ng/core/connection_events.py:100-124
    # This ensures async cleanup runs correctly when client disconnects
    async def cleanup_on_unload():
        try:
            await order_context.dispose()
        except Exception as exc:
            logger.warning(f"Error during order context cleanup: {exc}")

    # IMPORTANT: Use app.on_disconnect (not context.client.on_disconnect)
    # The app.on_disconnect properly awaits async callbacks and matches
    # the existing lifecycle pattern in the codebase
    from nicegui import app
    app.on_disconnect(cleanup_on_unload)

    # Responsive grid layout:
    # - Desktop (>=1024px): 3-column layout
    # - Tablet (768-1023px): 2-column layout (watchlist + main)
    # - Mobile (<768px): Single column (stacked)
    with ui.element("div").classes(
        "grid gap-4 w-full h-full "
        "grid-cols-1 md:grid-cols-2 lg:grid-cols-[250px_1fr_350px]"
    ):
        # Left column: Watchlist (hidden on mobile, visible on tablet+)
        with ui.column().classes("h-full hidden md:flex"):
            watchlist = order_context.create_watchlist()

        # Middle column: Chart + Positions Grid
        with ui.column().classes("h-full gap-4"):
            # Price chart (upper)
            with ui.card().classes("flex-grow min-h-[200px]"):
                chart = order_context.create_price_chart()

            # Positions grid (lower)
            with ui.card().classes("h-64 min-h-[200px]"):
                await create_positions_grid(user_id, role, strategies)

        # Right column: Market Context + Order Ticket
        with ui.column().classes("h-full gap-4"):
            # Market context (upper)
            market_ctx = order_context.create_market_context()

            # Order ticket (lower)
            order_ticket = order_context.create_order_ticket()

    # Mobile-only: Floating watchlist button
    with ui.element("div").classes("fixed bottom-4 right-4 md:hidden"):
        ui.button(
            icon="list",
            on_click=lambda: order_context.toggle_watchlist_drawer(),
        ).classes("rounded-full w-14 h-14")

    # Initialize all components AFTER UI creation
    # CRITICAL: initialize() must be called AFTER create_*() methods return
    # because create_*() builds UI elements that initialize() configures and
    # starts timers for. The flow is:
    # 1. create_*() - Build UI elements, set up event handlers
    # 2. initialize() - Start timers, load data, establish subscriptions
    #
    # Timers are started in initialize(), not in create(), to ensure:
    # - timer_tracker is available for registration
    # - All UI elements exist before timers fire
    await order_context.initialize()
```

### OrderEntryContext Lifecycle Management

```python
# apps/web_console_ng/components/order_entry_context.py

"""
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
  Redis Pub/Sub → RealtimeUpdater → OrderEntryContext → Component callbacks
"""

class OrderEntryContext:
    """Coordinates all order entry components with proper lifecycle management."""

    def __init__(
        self,
        realtime_updater: RealtimeUpdater,
        trading_client: AsyncTradingClient,
        state_manager: UserStateManager,
        connection_monitor: ConnectionMonitor,
        redis: aioredis.Redis,  # For authoritative safety state checks
        user_id: str,  # Required for API calls and subscriptions
        role: str,  # User role for authorization
        strategies: list[str],  # Strategies for position filtering
    ):
        self._realtime = realtime_updater
        self._client = trading_client
        self._state_manager = state_manager
        self._connection_monitor = connection_monitor
        self._redis = redis
        self._user_id = user_id
        self._role = role
        self._strategies = strategies

        self._subscriptions: list[str] = []  # Track active subscriptions
        self._timers: list[ui.timer] = []    # Track active timers
        self._disposed: bool = False

        # SUBSCRIPTION OWNERSHIP TRACKING (refcount per channel)
        # Each channel can be owned by multiple owners (watchlist, selected symbol)
        # Only unsubscribe when all owners release the channel
        self._channel_owners: dict[str, set[str]] = {}  # channel -> set of owners
        self._subscription_lock = asyncio.Lock()  # Protects _channel_owners, _subscriptions, _pending_subscribes
        # Pending subscribe futures (concurrent acquirers await the same future)
        # Created UNDER LOCK to prevent race conditions
        # MUST be instance-level to prevent cross-session leakage
        self._pending_subscribes: dict[str, asyncio.Future] = {}
        # Channel -> callback registry for reconnect resubscription
        # Different channels use different callbacks (price vs position)
        self._channel_callbacks: dict[str, Callable] = {}
        # Failed subscriptions to retry on reconnect
        # Dict[channel, (owners_set, callback)] - channels that failed to subscribe
        # Stores ALL owners so multi-owner channels (e.g., watchlist + selected) are fully restored
        self._failed_subscriptions: dict[str, tuple[set[str], Callable]] = {}
        # Owner constants
        self.OWNER_SELECTED_SYMBOL = "selected_symbol"
        self.OWNER_WATCHLIST = "watchlist"
        self.OWNER_POSITIONS = "positions"  # Global position subscription
        self.OWNER_KILL_SWITCH = "kill_switch"  # Global safety subscription
        self.OWNER_CIRCUIT_BREAKER = "circuit_breaker"  # Global safety subscription
        self.OWNER_CONNECTION = "connection"  # Global connection subscription
        # Track current selected channel for unsubscribe
        self._current_selected_channel: str | None = None

        # Child components (initialized in create methods)
        # NOTE: When creating OrderTicketComponent, pass all required dependencies:
        #   self._order_ticket = OrderTicketComponent(
        #       trading_client=self._client,
        #       state_manager=self._state_manager,
        #       connection_monitor=self._connection_monitor,
        #       user_id=self._user_id,           # <-- Required for API calls
        #       role=self._role,                 # <-- Required for API calls
        #       strategies=self._strategies,     # <-- Required for position filtering
        #       on_symbol_selected=self.on_symbol_selected,
        #       verify_circuit_breaker=self._verify_circuit_breaker_safe,  # <-- Authoritative check
        #       verify_kill_switch=self._verify_kill_switch_safe,          # <-- Authoritative check
        #   )
        self._order_ticket: OrderTicketComponent | None = None
        self._market_context: MarketContextComponent | None = None
        self._watchlist: WatchlistComponent | None = None
        self._price_chart: PriceChartComponent | None = None

        # Current selected symbol (shared state)
        self._selected_symbol: str | None = None

        # SYMBOL SELECTION VERSION (race prevention)
        # Monotonically increasing version number for symbol selection.
        # When child component updates complete, they report with their version.
        # Stale updates (version < current) are ignored.
        self._selection_version: int = 0

    async def initialize(self) -> None:
        """Initialize all components and subscriptions.

        FAIL-CLOSED: If any critical subscription or safety fetch fails, the order
        ticket is explicitly disabled with a clear reason. Partial init does NOT
        allow order entry.

        Raises:
            RuntimeError: If called on a disposed context
        """
        if self._disposed:
            raise RuntimeError("Cannot initialize disposed OrderEntryContext")

        try:
            # Initialize child components with timer tracker for lifecycle management
            # Components pass their timers to _track_timer for centralized cleanup on dispose
            await self._watchlist.initialize(timer_tracker=self._track_timer)
            await self._order_ticket.initialize(timer_tracker=self._track_timer)
            await self._market_context.initialize(timer_tracker=self._track_timer)
            await self._price_chart.initialize(timer_tracker=self._track_timer)

            # Subscribe to GLOBAL channels (owned by OrderEntryContext)
            await self._subscribe_to_kill_switch_channel()
            await self._subscribe_to_circuit_breaker_channel()  # CRITICAL: Safety mechanism
            await self._subscribe_to_connection_channel()
            await self._subscribe_to_positions_channel()

            # CRITICAL: Fetch initial state for safety mechanisms (fail-closed)
            # Without this, if circuit breaker is already tripped before subscribe,
            # we'd miss the state and allow orders (fail-open risk).
            # Default to disabled until we confirm state is safe.
            await self._fetch_initial_safety_state()

            # Note: Price channel subscription is PER-SYMBOL and managed in on_symbol_selected()
            # Initial symbol subscription happens when watchlist loads and fires selection event

        except Exception as exc:
            # FAIL-CLOSED: Explicitly disable order ticket on init failure
            logger.error(f"OrderEntryContext initialization failed: {exc}")

            # CLEANUP: Cancel any timers registered before failure to prevent leaks
            for timer in self._tracked_timers:
                try:
                    timer.cancel()
                except Exception as timer_exc:
                    logger.debug(f"Timer cleanup during init failure: {timer_exc}")
            self._tracked_timers.clear()

            # CLEANUP: Release any subscriptions acquired before failure
            # This prevents duplicate subscriptions on retry
            for channel, (owner, _) in list(self._subscription_owners.items()):
                try:
                    await self._realtime.unsubscribe(channel)
                except Exception as unsub_exc:
                    logger.debug(f"Unsubscribe cleanup during init failure: {unsub_exc}")
            self._subscription_owners.clear()
            self._subscription_callbacks.clear()
            self._subscription_refcounts.clear()
            self._pending_subscription_futures.clear()

            if self._order_ticket:
                # Keep safety state as fail-closed defaults (already True/True)
                # Set explicit reason so user understands why trading is disabled
                self._order_ticket.set_circuit_breaker_state(True, "Initialization failed - please refresh")
                self._order_ticket.set_kill_switch_state(True, "Initialization failed - please refresh")
            # Re-raise so caller can handle (e.g., show error to user)
            raise

    async def _fetch_initial_safety_state(self) -> None:
        """Fetch initial state for kill switch and circuit breaker.

        CRITICAL: Pub/sub only delivers updates AFTER subscription. If the
        circuit breaker is already TRIPPED when we subscribe, we'd never know.
        This fetch ensures we start in the correct state.

        FAIL-CLOSED: If fetch fails or times out, we default to disabled (safe mode).
        """
        try:
            # Fetch circuit breaker state from Redis (authoritative source)
            # Key: circuit_breaker:state
            #
            # STORAGE FORMAT CONTRACT (CANONICAL - matches libs/trading/risk_management/breaker.py):
            # - Redis KEY storage uses JSON:
            #   {
            #     "state": "OPEN"|"TRIPPED"|"QUIET_PERIOD",
            #     "tripped_at": str|null (ISO timestamp when tripped),
            #     "trip_reason": str|null,
            #     "trip_details": str|null,
            #     "reset_at": str|null (ISO timestamp when reset),
            #     "reset_by": str|null,
            #     "trip_count_today": int
            #   }
            # - OPEN = trading allowed, TRIPPED = trading blocked, QUIET_PERIOD = transitional (treat as tripped)
            #
            # FAIL-CLOSED: Only allow trading if explicit "OPEN" state is confirmed
            # Missing key, None, invalid JSON, or unknown value = assume TRIPPED (unsafe)
            # Timeout to prevent initialization stall on Redis issues
            cb_raw = await asyncio.wait_for(
                self._redis.get("circuit_breaker:state"),
                timeout=2.0,  # 2s for init (longer than confirm-time)
            )

            # Parse JSON from Redis key
            cb_tripped = True  # Fail-closed default
            cb_reason = "Initial state: Unknown/missing"
            if cb_raw:
                try:
                    cb_data = json.loads(cb_raw)
                    cb_state = str(cb_data.get("state", "")).upper()

                    # CRITICAL: For OPEN state, require valid reset_at timestamp (proves state is fresh)
                    # For initial OPEN (never tripped), reset_at may be None but tripped_at must also be None
                    # Stale/malformed data should not enable trading
                    reset_at = cb_data.get("reset_at")
                    tripped_at = cb_data.get("tripped_at")
                    timestamp_valid = False

                    if cb_state == "OPEN":
                        if reset_at:
                            # Was tripped, then reset - validate reset_at
                            try:
                                parse_iso_timestamp(str(reset_at))
                                timestamp_valid = True
                            except (ValueError, TypeError):
                                logger.warning(f"Invalid circuit breaker reset_at: {reset_at!r}")
                        elif tripped_at is None:
                            # Never tripped - this is valid OPEN state
                            timestamp_valid = True
                        else:
                            # Has tripped_at but no reset_at - inconsistent, fail-closed
                            logger.warning("Circuit breaker has tripped_at but no reset_at")

                        if timestamp_valid:
                            cb_tripped = False
                            cb_reason = None
                        else:
                            # FAIL-CLOSED: OPEN without valid timestamp = treat as tripped
                            cb_reason = "Initial state: OPEN but missing/invalid timestamp"
                    elif cb_state == "TRIPPED":
                        cb_reason = cb_data.get("trip_reason", "Initial state: TRIPPED")
                    elif cb_state == "QUIET_PERIOD":
                        # QUIET_PERIOD is transitional after reset - treat as tripped for safety
                        cb_reason = "Initial state: QUIET_PERIOD (transitional)"
                    else:
                        cb_reason = f"Initial state: Unknown ({cb_state!r})"
                except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                    logger.warning(f"Invalid circuit breaker JSON: {exc}")
                    cb_reason = "Initial state: Invalid data"

            if cb_tripped:
                self._order_ticket.set_circuit_breaker_state(True, cb_reason)
            else:
                self._order_ticket.set_circuit_breaker_state(False, None)

            # Fetch kill switch state from Redis
            # Key: kill_switch:state
            #
            # STORAGE FORMAT CONTRACT (CANONICAL - matches libs/trading/risk_management/kill_switch.py):
            # - Redis KEY storage uses JSON:
            #   {
            #     "state": "ACTIVE"|"ENGAGED",
            #     "engaged_at": str|null (ISO timestamp when engaged),
            #     "engaged_by": str|null,
            #     "engagement_reason": str|null,
            #     "disengaged_at": str|null (ISO timestamp when disengaged/returned to ACTIVE),
            #     "disengaged_by": str|null,
            #     "engagement_count_today": int
            #   }
            # - ACTIVE = trading allowed (normal state), ENGAGED = trading blocked (kill switch engaged)
            #
            # FAIL-CLOSED: Only allow trading if explicit "ACTIVE" state is confirmed
            # Timeout to prevent initialization stall on Redis issues
            ks_raw = await asyncio.wait_for(
                self._redis.get("kill_switch:state"),
                timeout=2.0,  # 2s for init (longer than confirm-time)
            )

            # Parse JSON from Redis key
            ks_engaged = True  # Fail-closed default
            ks_reason = "Initial state: Unknown/missing"
            if ks_raw:
                try:
                    ks_data = json.loads(ks_raw)
                    ks_state = str(ks_data.get("state", "")).upper()

                    # CRITICAL: For ACTIVE state, require valid disengaged_at timestamp (proves state is fresh)
                    # For initial ACTIVE (never engaged), disengaged_at may be None but engaged_at must also be None
                    # Stale/malformed data should not enable trading
                    disengaged_at = ks_data.get("disengaged_at")
                    engaged_at = ks_data.get("engaged_at")
                    timestamp_valid = False

                    if ks_state == "ACTIVE":
                        if disengaged_at:
                            # Was engaged, then disengaged - validate disengaged_at
                            try:
                                parse_iso_timestamp(str(disengaged_at))
                                timestamp_valid = True
                            except (ValueError, TypeError):
                                logger.warning(f"Invalid kill switch disengaged_at: {disengaged_at!r}")
                        elif engaged_at is None:
                            # Never engaged - this is valid ACTIVE state
                            timestamp_valid = True
                        else:
                            # Has engaged_at but no disengaged_at - inconsistent, fail-closed
                            logger.warning("Kill switch has engaged_at but no disengaged_at")

                        if timestamp_valid:
                            ks_engaged = False
                            ks_reason = None
                        else:
                            # FAIL-CLOSED: ACTIVE without valid timestamp = treat as engaged
                            ks_reason = "Initial state: ACTIVE but missing/invalid timestamp"
                    elif ks_state == "ENGAGED":
                        ks_reason = ks_data.get("engagement_reason", "Initial state: ENGAGED")
                    else:
                        ks_reason = f"Initial state: Unknown ({ks_state!r})"
                except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                    logger.warning(f"Invalid kill switch JSON: {exc}")
                    ks_reason = "Initial state: Invalid data"

            if ks_engaged:
                self._order_ticket.set_kill_switch_state(True, ks_reason)
            else:
                # Explicitly safe - kill switch is disengaged (normal operation)
                self._order_ticket.set_kill_switch_state(False, None)

        except asyncio.TimeoutError:
            # FAIL-CLOSED: Timeout fetching safety state = assume unsafe
            logger.warning("Timeout fetching initial safety state from Redis")
            self._order_ticket.set_circuit_breaker_state(True, "Safety state fetch timed out")
            self._order_ticket.set_kill_switch_state(True, "Safety state fetch timed out")
        except Exception as exc:
            # FAIL-CLOSED: On error, assume unsafe state and disable trading
            logger.warning(f"Failed to fetch initial safety state: {exc}")
            self._order_ticket.set_circuit_breaker_state(True, "Unable to verify safety state")
            self._order_ticket.set_kill_switch_state(True, "Unable to verify safety state")

    async def _subscribe_to_kill_switch_channel(self) -> None:
        """Subscribe to kill switch state - OrderEntryContext owns this.

        Uses ownership model for consistent tracking and reconnect resubscription.
        """
        channel = "kill_switch:state"
        await self._acquire_channel(channel, self.OWNER_KILL_SWITCH, self._on_kill_switch_update)

    async def _on_kill_switch_update(self, data: dict) -> None:
        """Handle kill switch update and dispatch to components.

        Channel payload (matches libs/trading/risk_management/kill_switch.py):
        {
            "state": "ACTIVE"|"ENGAGED",
            "engaged_at": str|null,
            "engaged_by": str|null,
            "engagement_reason": str|null,
            "disengaged_at": str|null,
            "disengaged_by": str|null,
            "engagement_count_today": int
        }

        FAIL-CLOSED: Missing/invalid state OR missing/invalid timestamp is treated as ENGAGED.
        Only explicit "ACTIVE" WITH valid timestamp allows trading.

        TYPE GUARDS: Validates data type to prevent exceptions on malformed payloads.
        """
        if self._disposed:
            return  # Ignore updates after dispose

        # TYPE GUARD: Validate payload structure
        # FAIL-CLOSED: Any type error = unsafe (engaged)
        try:
            if not isinstance(data, dict):
                raise TypeError(f"Expected dict, got {type(data).__name__}")
            state = str(data.get("state", "")).upper()

            # CRITICAL: For ACTIVE state, require valid disengaged_at timestamp
            # (proves kill switch was intentionally disengaged, not just missing data)
            # For initial ACTIVE (never engaged), both engaged_at and disengaged_at may be None
            engaged_at = data.get("engaged_at")
            disengaged_at = data.get("disengaged_at")

            if state == "ACTIVE":
                if disengaged_at:
                    # Was engaged, then disengaged - validate disengaged_at
                    parse_iso_timestamp(str(disengaged_at))  # Raises on invalid
                elif engaged_at is not None:
                    # Has engaged_at but no disengaged_at - inconsistent state
                    raise ValueError("ACTIVE state has engaged_at but no disengaged_at")
                # else: never engaged, valid initial state

        except Exception as exc:
            logger.warning(f"Invalid kill switch payload: {exc}, treating as engaged")
            if self._order_ticket:
                self._order_ticket.set_kill_switch_state(True, "Invalid kill switch payload")
            return

        # FAIL-CLOSED: Unknown/missing state = unsafe
        # Only explicit "ACTIVE" allows trading
        if state not in ("ACTIVE", "ENGAGED"):
            logger.warning(f"Malformed kill switch state: {state!r}, treating as engaged")
            engaged = True
            reason = "Malformed kill switch state"
        else:
            engaged = state != "ACTIVE"
            reason = data.get("engagement_reason")

        # Dispatch to all components that need kill switch state
        if self._order_ticket:
            self._order_ticket.set_kill_switch_state(engaged, reason)

    async def _subscribe_to_circuit_breaker_channel(self) -> None:
        """Subscribe to circuit breaker state - OrderEntryContext owns this.

        CRITICAL: Circuit breaker is an automatic safety mechanism that trips when:
        - Drawdown breach (account losing too much)
        - Broker errors (API failures)
        - Data staleness (market data feed dead >30min)

        Unlike kill switch (manual), circuit breaker is automatic and MUST
        block all order submissions when tripped.

        Uses ownership model for consistent tracking and reconnect resubscription.
        """
        channel = "circuit_breaker:state"
        await self._acquire_channel(channel, self.OWNER_CIRCUIT_BREAKER, self._on_circuit_breaker_update)

    async def _on_circuit_breaker_update(self, data: dict) -> None:
        """Handle circuit breaker update and dispatch to components.

        Channel payload (matches libs/trading/risk_management/breaker.py):
        {
            "state": "OPEN"|"TRIPPED"|"QUIET_PERIOD",
            "tripped_at": str|null,
            "trip_reason": str|null,
            "trip_details": str|null,
            "reset_at": str|null,
            "reset_by": str|null,
            "trip_count_today": int
        }

        - OPEN: Normal operation, trading allowed
        - TRIPPED: Emergency halt, block all orders
        - QUIET_PERIOD: Transitional after reset, treat as tripped for safety

        FAIL-CLOSED: Missing/invalid state OR missing/invalid timestamp is treated as TRIPPED.
        Only explicit "OPEN" WITH valid timestamp allows trading.

        TYPE GUARDS: Validates data type to prevent exceptions on malformed payloads.
        """
        if self._disposed:
            return  # Ignore updates after dispose

        # TYPE GUARD: Validate payload structure
        # FAIL-CLOSED: Any type error = unsafe (tripped)
        try:
            if not isinstance(data, dict):
                raise TypeError(f"Expected dict, got {type(data).__name__}")
            state = str(data.get("state", "")).upper()

            # CRITICAL: For OPEN state, require valid reset_at timestamp
            # (proves circuit breaker was intentionally reset, not just missing data)
            # For initial OPEN (never tripped), both tripped_at and reset_at may be None
            tripped_at = data.get("tripped_at")
            reset_at = data.get("reset_at")

            if state == "OPEN":
                if reset_at:
                    # Was tripped, then reset - validate reset_at
                    parse_iso_timestamp(str(reset_at))  # Raises on invalid
                elif tripped_at is not None:
                    # Has tripped_at but no reset_at - inconsistent state
                    raise ValueError("OPEN state has tripped_at but no reset_at")
                # else: never tripped, valid initial state

        except Exception as exc:
            logger.warning(f"Invalid circuit breaker payload: {exc}, treating as tripped")
            if self._order_ticket:
                self._order_ticket.set_circuit_breaker_state(True, "Invalid circuit breaker payload")
            return

        # FAIL-CLOSED: Unknown/missing state = unsafe
        # Only explicit "OPEN" allows trading
        # QUIET_PERIOD is transitional - treat as tripped
        if state not in ("OPEN", "TRIPPED", "QUIET_PERIOD"):
            logger.warning(f"Malformed circuit breaker state: {state!r}, treating as tripped")
            tripped = True
            reason = "Malformed circuit breaker state"
        elif state == "QUIET_PERIOD":
            tripped = True  # Transitional - don't allow trading
            reason = "Circuit breaker in quiet period"
        else:
            tripped = state != "OPEN"
            reason = data.get("trip_reason")

        # Dispatch to all components that need circuit breaker state
        if self._order_ticket:
            self._order_ticket.set_circuit_breaker_state(tripped, reason)

    async def _verify_circuit_breaker_safe(self) -> bool:
        """Authoritative check if circuit breaker allows trading.

        Called by OrderTicket at confirm-time to verify circuit breaker state.
        Fetches directly from Redis (not cached pub/sub state) for authoritative check.

        Schema (matches libs/trading/risk_management/breaker.py):
        {
            "state": "OPEN"|"TRIPPED"|"QUIET_PERIOD",
            "tripped_at": str|null,
            "reset_at": str|null,
            ...
        }

        Returns:
            True if circuit breaker is OPEN (trading allowed)
            False if TRIPPED, QUIET_PERIOD, unknown, timeout, or error (fail-closed)
        """
        try:
            # Short timeout to avoid stalling confirmation (fail-closed on timeout)
            cb_raw = await asyncio.wait_for(
                self._redis.get("circuit_breaker:state"),
                timeout=0.5,  # 500ms max
            )
            # Parse JSON (same format as pub/sub and initial fetch)
            if not cb_raw:
                return False  # Missing = fail-closed
            try:
                cb_data = json.loads(cb_raw)
                cb_state = str(cb_data.get("state", "")).upper()

                # CRITICAL: For OPEN state, require valid reset_at timestamp
                # (proves intentional reset, not just missing data)
                # For initial OPEN (never tripped), both tripped_at and reset_at may be None
                tripped_at = cb_data.get("tripped_at")
                reset_at = cb_data.get("reset_at")

                if cb_state == "OPEN":
                    if reset_at:
                        # Was tripped, then reset - validate reset_at
                        parse_iso_timestamp(str(reset_at))  # Raises on invalid
                    elif tripped_at is not None:
                        # Has tripped_at but no reset_at - inconsistent state
                        logger.warning("Circuit breaker verify: OPEN with tripped_at but no reset_at")
                        return False
                    # else: never tripped, valid initial state
                    return True
                else:
                    # TRIPPED or QUIET_PERIOD = not safe
                    return False
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
                logger.warning(f"Invalid circuit breaker JSON at verify: {exc}")
                return False  # Invalid JSON/timestamp = fail-closed
        except asyncio.TimeoutError:
            logger.warning("Circuit breaker verification timed out")
            return False  # Fail-closed on timeout
        except Exception as exc:
            logger.warning(f"Circuit breaker verification failed: {exc}")
            return False  # Fail-closed

    async def _verify_kill_switch_safe(self) -> bool:
        """Authoritative check if kill switch allows trading.

        Called by OrderTicket at confirm-time to verify kill switch state.
        Fetches directly from Redis (not cached pub/sub state) for authoritative check.

        Schema (matches libs/trading/risk_management/kill_switch.py):
        {
            "state": "ACTIVE"|"ENGAGED",
            "engaged_at": str|null,
            "disengaged_at": str|null,
            ...
        }

        Returns:
            True if kill switch is ACTIVE (trading allowed)
            False if ENGAGED, unknown, timeout, or error (fail-closed)
        """
        try:
            # Short timeout to avoid stalling confirmation (fail-closed on timeout)
            ks_raw = await asyncio.wait_for(
                self._redis.get("kill_switch:state"),
                timeout=0.5,  # 500ms max
            )
            # Parse JSON (same format as pub/sub and initial fetch)
            if not ks_raw:
                return False  # Missing = fail-closed
            try:
                ks_data = json.loads(ks_raw)
                ks_state = str(ks_data.get("state", "")).upper()

                # CRITICAL: For ACTIVE state, require valid disengaged_at timestamp
                # (proves intentional disengagement, not just missing data)
                # For initial ACTIVE (never engaged), both engaged_at and disengaged_at may be None
                engaged_at = ks_data.get("engaged_at")
                disengaged_at = ks_data.get("disengaged_at")

                if ks_state == "ACTIVE":
                    if disengaged_at:
                        # Was engaged, then disengaged - validate disengaged_at
                        parse_iso_timestamp(str(disengaged_at))  # Raises on invalid
                    elif engaged_at is not None:
                        # Has engaged_at but no disengaged_at - inconsistent state
                        logger.warning("Kill switch verify: ACTIVE with engaged_at but no disengaged_at")
                        return False
                    # else: never engaged, valid initial state
                    return True
                else:
                    # ENGAGED = not safe
                    return False
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
                logger.warning(f"Invalid kill switch JSON at verify: {exc}")
                return False  # Invalid JSON/timestamp = fail-closed
        except asyncio.TimeoutError:
            logger.warning("Kill switch verification timed out")
            return False  # Fail-closed on timeout
        except Exception as exc:
            logger.warning(f"Kill switch verification failed: {exc}")
            return False  # Fail-closed

    async def _subscribe_to_connection_channel(self) -> None:
        """Subscribe to connection state - OrderEntryContext owns this.

        Uses ownership model for consistent tracking and reconnect resubscription.
        """
        channel = "connection:state"
        await self._acquire_channel(channel, self.OWNER_CONNECTION, self._on_connection_update)

    # Connection states that disable trading
    READ_ONLY_CONNECTION_STATES = {"DISCONNECTED", "RECONNECTING", "DEGRADED"}

    # Only "CONNECTED" state allows trading; all others are read-only
    READ_WRITE_CONNECTION_STATES = {"CONNECTED"}

    async def _on_connection_update(self, data: dict) -> None:
        """Handle connection update and dispatch to components.

        On reconnect (CONNECTED after any read-only state), re-fetch safety state
        since pub/sub may have missed updates during the degraded/disconnect window.

        FAIL-CLOSED: Unknown/missing connection state is treated as read-only.
        Only explicit "CONNECTED" allows trading.

        TYPE GUARDS: Validates data type to prevent exceptions on malformed payloads.
        """
        if self._disposed:
            return  # Ignore updates after dispose

        # TYPE GUARD: Validate payload structure
        # FAIL-CLOSED: Any type error = read-only (no trading)
        try:
            if not isinstance(data, dict):
                raise TypeError(f"Expected dict, got {type(data).__name__}")
            state = str(data.get("state", "")).upper()
        except Exception as exc:
            logger.warning(f"Invalid connection payload: {exc}, treating as read-only")
            # CRITICAL: Set _last_connection_state so reconnect logic triggers on next valid CONNECTED
            # Without this, was_read_only check might fail and skip safety re-fetch
            self._last_connection_state = "UNKNOWN"
            if self._order_ticket:
                self._order_ticket.set_connection_state("UNKNOWN", True)
            return

        # Track previous state for reconnect detection
        was_read_only = hasattr(self, '_last_connection_state') and self._last_connection_state not in self.READ_WRITE_CONNECTION_STATES

        # FAIL-CLOSED: Unknown/missing state = read-only (no trading)
        # Only explicit "CONNECTED" allows trading
        if state not in self.READ_WRITE_CONNECTION_STATES and state not in self.READ_ONLY_CONNECTION_STATES:
            logger.warning(f"Malformed connection state: {state!r}, treating as read-only")
            is_read_only = True
        else:
            is_read_only = state not in self.READ_WRITE_CONNECTION_STATES

        # Track connection state for reconnect detection
        self._last_connection_state = state

        # RECONNECT HANDLING: Re-fetch safety state AND re-subscribe channels
        # Pub/sub may have missed updates during disconnect; we need authoritative state
        # Channels may need re-subscription if websocket was completely dropped
        if was_read_only and state == "CONNECTED":
            logger.info("Connection restored - re-fetching safety state and revalidating subscriptions")
            try:
                await self._fetch_initial_safety_state()
            except Exception as exc:
                logger.warning(f"Failed to re-fetch safety state on reconnect: {exc}")
                # Safety state remains fail-closed from disconnect

            # Re-subscribe all channels we own (websocket drop may have cleared them)
            await self._resubscribe_all_channels()

            # Retry any previously failed subscriptions
            await self._retry_failed_subscriptions()

        # Dispatch to all components that need connection state
        if self._order_ticket:
            self._order_ticket.set_connection_state(state, is_read_only)

    # =========================================================================
    # SUBSCRIPTION OWNERSHIP MANAGEMENT (refcount-based)
    # =========================================================================
    #
    # Multiple owners (watchlist, selected_symbol) can subscribe to the same channel.
    # We track owners per channel and only unsubscribe when all owners release it.
    # This prevents:
    # - Double subscriptions (we subscribe only once, track multiple owners)
    # - Premature unsubscribe (we only unsubscribe when no owners remain)

    # NOTE: _pending_subscribes is an INSTANCE attribute (see __init__)
    # - Dict[channel, asyncio.Future] - tracks pending subscribes so concurrent acquirers await the same future
    # - Future is created UNDER LOCK to prevent race where acquirer misses the pending state
    # - Cleared when subscribe completes (success or failure)
    # - MUST be instance-level to prevent cross-session leakage

    async def _acquire_channel(self, channel: str, owner: str, callback: Callable) -> None:
        """Acquire ownership of a channel, subscribing if first owner.

        RACE-FREE PENDING TASK PATTERN:
        1. Check/update ownership under lock, set pending Future if first owner
        2. If pending exists, await it (all concurrent acquirers await same Future)
        3. First owner performs subscribe, then resolves/rejects the Future
        4. On subscribe failure, clear ALL owners (fail-closed for concurrent acquirers)
        5. After subscribe, check if owners still exist (last owner may have released during await)

        DISPOSE SAFETY: Guards against acquiring after dispose to prevent
        orphaned subscriptions. Checks both before lock and after await.

        CONCURRENT SUBSCRIBE SAFETY: Uses Future set under the SAME lock as
        ownership creation, ensuring no concurrent acquirer slips through without
        awaiting. All concurrent acquirers either succeed or fail together.

        ORPHAN PREVENTION: If all owners release during subscribe, immediately
        unsubscribe to prevent orphaned subscriptions.

        Args:
            channel: Redis channel to subscribe to
            owner: Owner identifier (OWNER_SELECTED_SYMBOL or OWNER_WATCHLIST)
            callback: Callback function for channel updates (stored for reconnect resubscription)
        """
        # Early dispose check to prevent new subscriptions after dispose
        if self._disposed:
            return

        need_subscribe = False
        pending_future = None

        # Phase 1: Check if we need to subscribe or await pending (under lock)
        # CRITICAL: Register pending Future UNDER THE SAME LOCK as ownership creation
        # to prevent race where concurrent acquirer misses the pending task
        async with self._subscription_lock:
            # Check if subscribe already in progress - await the same future
            if channel in self._pending_subscribes:
                pending_future = self._pending_subscribes[channel]
            elif channel not in self._channel_owners:
                # First owner - will perform subscribe
                self._channel_owners[channel] = set()
                need_subscribe = True
                # Create Future UNDER LOCK so concurrent acquirers always see it
                # Use get_running_loop() (Py3.11+) instead of deprecated get_event_loop()
                pending_future = asyncio.get_running_loop().create_future()
                self._pending_subscribes[channel] = pending_future
            # Record ownership (even if awaiting pending - we're committed)
            self._channel_owners[channel].add(owner)
            # Store callback for reconnect resubscription (first caller's callback wins)
            # INVARIANT: Single callback per channel - all owners must use same callback
            if channel not in self._channel_callbacks:
                self._channel_callbacks[channel] = callback
            elif self._channel_callbacks[channel] is not callback:
                # Different callback for same channel - FAIL FAST
                # This is a programming error that would cause silent data misrouting
                raise ValueError(
                    f"Callback mismatch for channel {channel}: new owner '{owner}' "
                    f"provided different callback than existing. "
                    f"All owners must use the same callback for a channel."
                )

        # Phase 2a: If not first owner, await the pending future
        if not need_subscribe and pending_future is not None:
            try:
                await pending_future
            except Exception:
                # Subscribe failed - our ownership was already cleared by the task
                raise
            return

        # Phase 2b: First owner performs subscribe and resolves the future
        if need_subscribe:
            try:
                # Perform the actual subscription
                await self._realtime.subscribe(channel, callback)

                # Post-await dispose check: if disposed during subscribe, unsubscribe immediately
                if self._disposed:
                    logger.info(f"Disposed during subscribe - unsubscribing {channel}")
                    try:
                        await self._realtime.unsubscribe(channel)
                    except Exception:
                        pass  # Best effort cleanup
                    # Resolve future so waiters don't hang (they'll see disposed state)
                    if pending_future and not pending_future.done():
                        pending_future.set_result(None)
                    return

                # Phase 3: Update subscriptions list and check for orphan (under lock)
                need_immediate_unsubscribe = False
                async with self._subscription_lock:
                    # Clear pending future
                    self._pending_subscribes.pop(channel, None)
                    # Check if owners still exist (last owner may have released during await)
                    if channel in self._channel_owners and self._channel_owners[channel]:
                        self._subscriptions.append(channel)
                    else:
                        # ORPHAN PREVENTION: All owners released during subscribe
                        # Mark for unsubscribe (can't await inside lock)
                        need_immediate_unsubscribe = True
                        # Clean up callback registry
                        self._channel_callbacks.pop(channel, None)

                # Unsubscribe orphaned channel outside lock
                if need_immediate_unsubscribe:
                    logger.info(f"All owners released during subscribe - unsubscribing orphan {channel}")
                    try:
                        await self._realtime.unsubscribe(channel)
                    except Exception as exc:
                        logger.warning(f"Failed to unsubscribe orphan {channel}: {exc}")

                # Resolve future to signal success to concurrent waiters
                if pending_future and not pending_future.done():
                    pending_future.set_result(None)

            except Exception as exc:
                # ROLLBACK: Clear ALL owners on subscribe failure (fail-closed for all concurrent acquirers)
                logger.warning(f"Failed to subscribe to {channel}: {exc}")
                async with self._subscription_lock:
                    # Clear pending future
                    self._pending_subscribes.pop(channel, None)
                    # Track failed subscription for retry on reconnect
                    # Store ALL owners so multi-owner channels are fully restored
                    owners_snapshot = set()
                    if channel in self._channel_owners:
                        owners_snapshot = self._channel_owners[channel].copy()
                    self._failed_subscriptions[channel] = (owners_snapshot, callback)
                    # Clear callback registry (will be re-added on retry)
                    self._channel_callbacks.pop(channel, None)
                    # Clear ALL owners (not just this one) - they all must retry
                    if channel in self._channel_owners:
                        del self._channel_owners[channel]
                # Reject future so concurrent waiters see the failure
                if pending_future and not pending_future.done():
                    pending_future.set_exception(exc)
                # Re-raise to notify caller of failure
                raise

    async def _release_channel(self, channel: str, owner: str) -> None:
        """Release ownership of a channel, unsubscribing if last owner.

        Uses two-phase approach to avoid holding lock across await:
        1. Check/update ownership under lock, determine if unsubscribe needed
        2. Perform unsubscribe outside lock if needed

        DISPOSE SAFETY: Skips release if already disposed (cleanup handled by dispose).

        Args:
            channel: Redis channel to release
            owner: Owner identifier
        """
        # Skip if disposed - dispose() handles cleanup
        if self._disposed:
            return

        need_unsubscribe = False

        # Phase 1: Check if we need to unsubscribe (under lock)
        async with self._subscription_lock:
            # Also check and clean up from _failed_subscriptions
            # (channel may have failed to subscribe and is pending retry)
            if channel in self._failed_subscriptions:
                failed_owners, _ = self._failed_subscriptions[channel]
                failed_owners.discard(owner)
                if not failed_owners:
                    # No more owners want this channel - remove from retry list
                    del self._failed_subscriptions[channel]

            if channel not in self._channel_owners:
                return  # No active ownership recorded

            # Remove ownership
            self._channel_owners[channel].discard(owner)

            # Only unsubscribe if no owners remain
            if not self._channel_owners[channel]:
                need_unsubscribe = True
                del self._channel_owners[channel]
                # Clean up callback registry
                self._channel_callbacks.pop(channel, None)
                # Also clean up from failed subscriptions (if any)
                self._failed_subscriptions.pop(channel, None)
                try:
                    self._subscriptions.remove(channel)
                except ValueError:
                    pass  # Already removed

        # Phase 2: Unsubscribe outside lock (if needed)
        if need_unsubscribe:
            try:
                await self._realtime.unsubscribe(channel)
            except Exception as exc:
                logger.warning(f"Error releasing channel {channel}: {exc}")

    async def _resubscribe_all_channels(self) -> None:
        """Re-subscribe all owned channels after reconnect.

        Called when connection is restored from DEGRADED/DISCONNECTED to CONNECTED.
        Websocket drops may have cleared server-side subscriptions; we need to
        re-establish them.

        Uses stored callbacks from _channel_callbacks to ensure each channel
        gets its original callback (price channels use _on_price_update,
        position channels use _on_position_update, etc.).

        SAFETY: Iterates snapshot of channels to avoid lock contention.
        Re-checks ownership before each resubscribe to skip channels released during reconnect.
        Failures are logged but don't prevent other channels from resubscribing.
        """
        if self._disposed:
            return

        # Take snapshot of channels and their callbacks under lock
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

            # Re-check ownership before resubscribing (channel may have been released during reconnect)
            async with self._subscription_lock:
                if channel not in self._channel_owners or not self._channel_owners[channel]:
                    logger.debug(f"Skipping resubscribe for released channel: {channel}")
                    continue

            if callback is None:
                # Should not happen - log warning and skip
                logger.warning(f"No callback registered for channel {channel} - skipping resubscribe")
                continue
            try:
                # RealtimeUpdater should handle idempotent subscribe (no-op if already subscribed)
                await self._realtime.subscribe(channel, callback)
            except Exception as exc:
                # Log but continue - don't let one channel failure block others
                logger.warning(f"Failed to resubscribe channel {channel}: {exc}")
                # Add to failed subscriptions for retry on next reconnect
                async with self._subscription_lock:
                    if channel in self._channel_owners:
                        owners_snapshot = self._channel_owners[channel].copy()
                        self._failed_subscriptions[channel] = (owners_snapshot, callback)

    async def _retry_failed_subscriptions(self) -> None:
        """Retry subscriptions that previously failed.

        Called on reconnect to recover from transient subscribe failures.
        This ensures watchlist/selected symbol updates are restored after
        a temporary network issue caused subscribe failures.

        Uses stored (owners_set, callback) to re-acquire with ALL original owners.
        Multi-owner channels (e.g., both watchlist and selected symbol) are fully restored.
        """
        if self._disposed:
            return

        # Take snapshot of failed subscriptions under lock
        async with self._subscription_lock:
            # Deep copy owners to avoid mutation during iteration
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
                # No owners left (released during disconnect) - skip
                async with self._subscription_lock:
                    self._failed_subscriptions.pop(channel, None)
                continue
            try:
                # Re-acquire for ALL owners (first owner subscribes, rest just add ownership)
                for owner in owners:
                    await self._acquire_channel(channel, owner, callback)
                # Clear from failed list on success
                async with self._subscription_lock:
                    self._failed_subscriptions.pop(channel, None)
                logger.info(f"Successfully retried subscription for {channel} with {len(owners)} owner(s)")
            except Exception as exc:
                # Still failed - will retry on next reconnect
                logger.warning(f"Retry failed for channel {channel}: {exc}")

    # =========================================================================
    # SELECTED SYMBOL SUBSCRIPTION (using ownership model)
    # =========================================================================

    async def _subscribe_to_price_channel(self, symbol: str) -> None:
        """Subscribe to price updates for selected symbol.

        Uses refcount ownership - if watchlist already subscribed, we just add owner.
        Validates symbol to prevent malformed channel names.
        """
        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError as exc:
            logger.warning(f"Invalid symbol for subscription: {symbol!r} - {exc}")
            return  # Don't subscribe to invalid symbol

        channel = f"price.updated.{normalized}"
        await self._acquire_channel(channel, self.OWNER_SELECTED_SYMBOL, self._on_price_update)
        self._current_selected_channel = channel  # Track for unsubscribe

    async def _unsubscribe_from_price_channel(self) -> None:
        """Release selected symbol subscription, keeping watchlist subscription intact."""
        if hasattr(self, '_current_selected_channel') and self._current_selected_channel:
            await self._release_channel(self._current_selected_channel, self.OWNER_SELECTED_SYMBOL)
            self._current_selected_channel = None

    async def _on_price_update(self, data: dict) -> None:
        """Handle price update and dispatch to components.

        Data Flow: Redis → RealtimeUpdater → OrderEntryContext → Components

        FILTERING: MarketContext and PriceChart only receive updates for the
        currently selected symbol. Watchlist receives updates for all symbols.
        This prevents components from displaying data for wrong symbols when
        watchlist and selected symbol subscriptions overlap.

        VALIDATION: Missing or invalid (<=0) prices OR missing timestamps are NOT
        dispatched to OrderTicket to prevent marking data as "fresh" when it's not.
        Staleness checks would then pass but data would be unusable or undated.

        TYPE GUARDS: Validates data type to prevent exceptions on malformed payloads.
        """
        if self._disposed:
            return  # Ignore updates after dispose

        # TYPE GUARD: Validate payload structure
        # FAIL-CLOSED: On malformed data, force staleness for selected symbol
        if not isinstance(data, dict):
            logger.warning(f"Invalid price update payload type: {type(data).__name__}")
            # Force staleness for selected symbol if one is selected
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_price_data(self._selected_symbol, None, None)
            return

        symbol = data.get("symbol")
        raw_price = data.get("price")
        raw_timestamp = data.get("timestamp")

        # GUARD: Require valid symbol string - reject malformed/missing symbol
        # This prevents None == None match when selected symbol is also None
        if not isinstance(symbol, str) or not symbol:
            logger.warning(f"Price update missing/invalid symbol: {symbol!r}")
            # FAIL-CLOSED: Malformed payload on symbol channel clears staleness for selected symbol
            # This ensures missing/invalid symbol doesn't leave stale data marked as fresh
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_price_data(self._selected_symbol, None, None)
            return  # Skip malformed payload

        # Parse timestamp only if present
        timestamp: datetime | None = None
        if raw_timestamp:
            try:
                timestamp = parse_iso_timestamp(raw_timestamp)
            except (ValueError, TypeError) as exc:
                logger.warning(f"Invalid timestamp in price update for {symbol}: {exc}")
                # Continue with timestamp=None (treated as stale for OrderTicket)

        # Dispatch to OrderTicket for impact calculations
        # FAIL-CLOSED: ALWAYS call set_price_data for selected symbol updates.
        # If timestamp is missing/invalid, pass None to clear _price_last_updated,
        # which forces staleness checks to fail (preventing trading on unverifiable data).
        # GUARD: Also require _selected_symbol to be a non-empty string
        if self._order_ticket and self._selected_symbol and symbol == self._selected_symbol:
            # Parse price (may be None for invalid data)
            price: Decimal | None = None
            if raw_price is not None:
                try:
                    parsed_price = Decimal(str(raw_price))
                    if parsed_price.is_finite() and parsed_price > 0:
                        # is_finite() rejects NaN/Infinity that would bypass > 0 check
                        # (Decimal('Infinity') > 0 returns True!)
                        price = parsed_price
                    else:
                        logger.warning(f"Invalid/non-finite price {parsed_price} for {symbol}, clearing price timestamp")
                except (InvalidOperation, ValueError, TypeError) as exc:
                    logger.warning(f"Invalid price conversion for {symbol}: {raw_price!r} - {exc}")

            # FAIL-CLOSED: If price is invalid/missing, also clear timestamp to force staleness
            # Don't let a fresh timestamp make invalid price data appear usable
            effective_timestamp = timestamp if price is not None else None

            # ALWAYS call set_price_data - pass None for timestamp/price to clear and force staleness
            # This ensures missing timestamp OR invalid price doesn't leave data marked as fresh
            self._order_ticket.set_price_data(symbol, price, effective_timestamp)

        # Dispatch to MarketContext for display (ONLY if selected symbol)
        # MarketContext handles its own validation for display purposes (can show "stale" badge)
        if self._market_context and symbol == self._selected_symbol:
            self._market_context.set_price_data(data)

        # Dispatch to PriceChart for real-time tick updates (ONLY if selected symbol)
        if self._price_chart and symbol == self._selected_symbol:
            self._price_chart.set_price_data(data)

        # Dispatch to Watchlist for ALL symbols (NO filtering - watchlist shows all)
        # Watchlist is display-only so missing timestamp is acceptable
        if self._watchlist:
            self._watchlist.set_symbol_price_data(symbol, data)

    # =========================================================================
    # POSITION UPDATE SUBSCRIPTION (user-specific, global subscription)
    # =========================================================================

    async def _subscribe_to_positions_channel(self) -> None:
        """Subscribe to position updates - OrderEntryContext owns this.

        Uses ownership model for consistent tracking and reconnect resubscription.
        Position channel is a singleton (one per user), but using the ownership
        model ensures the callback is stored for reconnect.
        """
        channel = f"positions:{self._user_id}"
        await self._acquire_channel(channel, self.OWNER_POSITIONS, self._on_position_update)

    async def _on_position_update(self, data: dict) -> None:
        """Handle position update and dispatch to components.

        Data Flow: Redis → RealtimeUpdater → OrderEntryContext → OrderTicket

        CRITICAL: If selected symbol is NOT in positions payload, the position
        was closed/liquidated - we MUST set qty=0 to avoid stale position display.

        FAIL-CLOSED: If server timestamp missing/invalid, pass None to child
        components so they do NOT update their _position_last_updated field.
        This keeps data marked as stale rather than falsely marking it fresh.

        TYPE GUARDS: Validates data type to prevent exceptions on malformed payloads.
        """
        if self._disposed:
            return  # Ignore updates after dispose

        # TYPE GUARD: Validate payload structure
        # FAIL-CLOSED: On malformed data, force staleness for selected symbol
        if not isinstance(data, dict):
            logger.warning(f"Invalid position update payload type: {type(data).__name__}")
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_position_data(self._selected_symbol, 0, None)
            return

        positions = data.get("positions", [])

        # TYPE GUARD: Validate positions is a list
        if not isinstance(positions, list):
            logger.warning(f"Invalid positions field type: {type(positions).__name__}")
            if self._order_ticket and self._selected_symbol:
                self._order_ticket.set_position_data(self._selected_symbol, 0, None)
            return

        # Parse server timestamp (fail-closed if missing/invalid)
        # Pass None to child components if timestamp unavailable
        timestamp_str = data.get("timestamp")
        timestamp: datetime | None = None
        if timestamp_str:
            try:
                # Use shared parse_iso_timestamp for consistent tz-aware parsing
                timestamp = parse_iso_timestamp(timestamp_str)
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp in position update, leaving data stale")
        else:
            logger.warning("Position update missing timestamp, leaving data stale")

        # Dispatch to OrderTicket for selected symbol
        if self._order_ticket and self._selected_symbol:
            found = False
            for pos in positions:
                if pos.get("symbol") == self._selected_symbol:
                    # GUARD: Wrap qty parsing in try/except to handle malformed data
                    # FAIL-CLOSED: On parse error, set qty=0 and timestamp=None to force staleness
                    try:
                        qty = int(pos.get("qty", 0))
                    except (ValueError, TypeError) as exc:
                        logger.warning(f"Invalid qty in position update for {self._selected_symbol}: {pos.get('qty')!r} - {exc}")
                        qty = 0
                        timestamp = None  # Force staleness on malformed data
                    self._order_ticket.set_position_data(self._selected_symbol, qty, timestamp)
                    found = True
                    break

            # SAFETY: Symbol not in positions means position was closed
            # Must explicitly set to 0 to avoid stale position display
            if not found:
                self._order_ticket.set_position_data(self._selected_symbol, 0, timestamp)

    # =========================================================================
    # SYMBOL SELECTION (manages subscription lifecycle)
    # =========================================================================

    async def on_symbol_selected(self, symbol: str | None) -> None:
        """Handle symbol selection - manages price subscription lifecycle.

        Called when user selects symbol from Watchlist or MarketContext.
        Unsubscribes from previous symbol, subscribes to new symbol.

        Args:
            symbol: The selected symbol, or None to clear selection.

        SECURITY: Symbol is validated before being used in channel names or UI.
        Invalid symbols are rejected silently (logged) to prevent XSS or
        malformed channel subscriptions.

        RACE PREVENTION: Uses version token to ignore stale updates from
        child component async operations that complete out of order.
        On fast repeated clicks, only the latest selection takes effect.
        """
        if self._disposed:
            return  # Ignore selections after dispose

        # SECURITY: Validate symbol format before processing (None is allowed for clear)
        if symbol is not None:
            try:
                symbol = validate_and_normalize_symbol(symbol)
            except ValueError as exc:
                logger.warning(f"Invalid symbol selection rejected: {symbol!r} - {exc}")
                return  # Silently reject invalid symbols

        if symbol == self._selected_symbol:
            return

        # Increment selection version FIRST to invalidate any in-flight operations
        self._selection_version += 1
        current_version = self._selection_version

        # Unsubscribe from previous symbol's price channel
        await self._unsubscribe_from_price_channel()

        # Check if selection changed during unsubscribe (race check)
        if self._selection_version != current_version:
            return  # Stale - newer selection in progress

        # Update selected symbol
        self._selected_symbol = symbol

        # Subscribe to new symbol's price channel (only if symbol provided)
        if symbol:
            try:
                await self._subscribe_to_price_channel(symbol)
            except Exception as exc:
                logger.error(f"Failed to subscribe to price channel for {symbol}: {exc}")
                # RECOVERY: Clear selection to avoid partial state
                # User sees symbol cleared and can retry selection
                self._selected_symbol = None
                ui.notify(f"Unable to load data for {symbol}", type="warning")
                # CRITICAL: Clear ALL components to avoid inconsistent UI
                # (e.g., MarketContext showing old symbol while OrderTicket is cleared)
                if self._order_ticket:
                    await self._order_ticket.on_symbol_changed(None)
                if self._market_context:
                    await self._market_context.on_symbol_changed(None)
                if self._price_chart:
                    await self._price_chart.on_symbol_changed(None)
                # Optionally clear watchlist selection (user can re-select)
                if self._watchlist:
                    self._watchlist.clear_selection()
                return

        # Check if selection changed during subscribe (race check)
        if self._selection_version != current_version:
            return  # Stale - newer selection in progress

        # Notify all child components (always notify, even for None)
        # CRITICAL: After each await, re-check version to catch race conditions
        # where a newer selection arrives while children are processing

        if self._order_ticket:
            await self._order_ticket.on_symbol_changed(symbol)
            # RACE CHECK: Version may have changed during child's async operations
            if self._selection_version != current_version:
                return  # Stale - newer selection in progress

        if self._market_context:
            await self._market_context.on_symbol_changed(symbol)
            # RACE CHECK: Version may have changed during child's async operations
            if self._selection_version != current_version:
                return  # Stale - newer selection in progress

        if self._price_chart:
            await self._price_chart.on_symbol_changed(symbol)
            # RACE CHECK: Final child - no need to check but included for consistency
            if self._selection_version != current_version:
                return  # Stale - newer selection in progress

    # =========================================================================
    # WATCHLIST SUBSCRIPTION MANAGEMENT (using ownership model)
    # =========================================================================
    #
    # Watchlist needs price updates for ALL its symbols, not just the selected one.
    # Uses the same refcount ownership model to avoid conflicts with selected symbol.

    async def _on_watchlist_subscribe_request(self, symbol: str) -> None:
        """Handle watchlist request to subscribe to a symbol's price channel.

        Uses refcount ownership - if selected symbol already subscribed, we just add owner.
        Validates symbol to prevent malformed channel names.
        """
        if self._disposed:
            return  # Ignore requests after dispose

        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError as exc:
            logger.warning(f"Invalid watchlist symbol for subscription: {symbol!r} - {exc}")
            return  # Don't subscribe to invalid symbol

        channel = f"price.updated.{normalized}"
        await self._acquire_channel(channel, self.OWNER_WATCHLIST, self._on_price_update)

    async def _on_watchlist_unsubscribe_request(self, symbol: str) -> None:
        """Handle watchlist request to release a symbol's price channel.

        Uses refcount ownership - only unsubscribes if no other owner (selected symbol).
        Validates symbol to prevent malformed channel names.
        """
        if self._disposed:
            return  # Ignore requests after dispose

        try:
            normalized = validate_and_normalize_symbol(symbol)
        except ValueError as exc:
            logger.warning(f"Invalid watchlist symbol for unsubscribe: {symbol!r} - {exc}")
            return  # Can't release invalid symbol

        channel = f"price.updated.{normalized}"
        await self._release_channel(channel, self.OWNER_WATCHLIST)

    async def dispose(self) -> None:
        """Clean up all subscriptions and timers on page unload.

        CRITICAL: Must be called on page exit to prevent:
        - Memory leaks from orphaned subscriptions
        - Duplicate updates on page re-entry
        - Redis pub/sub connection accumulation
        """
        if self._disposed:
            return
        self._disposed = True

        # Cancel all timers
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()

        # Unsubscribe from all channels (take snapshot under lock)
        async with self._subscription_lock:
            channels_to_unsubscribe = list(self._subscriptions)

            # CRITICAL: Cancel/resolve pending subscribe futures before clearing
            # This prevents awaiters from hanging indefinitely on dispose
            for channel, future in self._pending_subscribes.items():
                if future and not future.done():
                    future.set_exception(asyncio.CancelledError("OrderEntryContext disposed"))

            # Clear all subscription state under lock
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

        # Dispose child components (guard against None for partial initialization)
        # If initialization failed before component creation, these may be None
        if self._watchlist:
            await self._watchlist.dispose()
        if self._order_ticket:
            await self._order_ticket.dispose()
        if self._market_context:
            await self._market_context.dispose()
        if self._price_chart:
            await self._price_chart.dispose()

        logger.info(f"OrderEntryContext disposed for user {self._user_id}")

    # NOTE: _track_subscription() has been REMOVED.
    # Use _acquire_channel() instead, which handles tracking automatically
    # with proper locking, callback registration, and duplicate prevention.

    def _track_timer(self, timer: ui.timer) -> None:
        """Track a timer for cleanup."""
        self._timers.append(timer)
```

---

## Dependencies

```
P6T1.4 Workspace ──> T4.4 Watchlist (persistence)
P6T2.3 Connection ──> T4.1 Order Ticket (gating)
P6T2.4 Kill Switch ──> T4.1 Order Ticket (gating)
P6T3.2 Hotkeys ──> T4.1 Order Ticket (B/S keys)
```

### Dependency Verification Checklist

**Existing APIs (P6T1-T3):**
- [ ] `WorkspacePersistenceService` exists and has `save_grid_state`/`load_grid_state` methods
- [ ] `ConnectionMonitor` has `is_read_only()` method
- [ ] Kill switch state available via `trading_client.fetch_kill_switch_status(user_id, role)`
- [ ] `HotkeyManager` has `register_handler()` method for action callbacks
- [ ] `RealtimeUpdater` has `subscribe()`/`unsubscribe()` methods
- [ ] Workspace allowlist includes `"watchlist.main"` grid ID

**AsyncTradingClient Methods (existing, verify signatures):**
- [ ] `fetch_positions(user_id, role, strategies)` - Returns `{"positions": [{"symbol", "qty", "current_price", "updated_at", ...}], "timestamp"?}`
  - REQUIRED: Add top-level `timestamp` (ISO 8601) to response schema (see API Timestamp Requirements below)
  - FALLBACK: Client uses newest position `updated_at` if top-level timestamp absent
- [ ] `fetch_account_info(user_id, role)` - Returns `{"buying_power", "cash", "portfolio_value", "timestamp"?, "updated_at"?, ...}`
  - REQUIRED: Add `timestamp` (ISO 8601) to response schema (see API Timestamp Requirements below)
  - FALLBACK: Client tries `last_equity_change`, `updated_at`, or `as_of` fields
- [ ] `fetch_kill_switch_status(user_id, role)` - Returns full kill switch state (see Redis Pub/Sub schema below)
  - State values: `ACTIVE` (trading allowed) or `ENGAGED` (trading blocked)
  - Timestamp fields: `engaged_at`, `disengaged_at` (ISO 8601)
- [ ] `fetch_recent_fills(user_id, role, strategies, limit)` - Returns `{"fills": [{"symbol", "side", "qty", "price", "filled_at", "client_order_id"}]}`
- [ ] `submit_manual_order(order_data: dict, user_id, role)` - order_data must include `client_order_id`
  - REQUIRED: Server must validate symbol exists and is tradeable before submission (reject unknown symbols with 400)
  - REQUIRED: Server MUST enforce kill switch check (reject with 503 if ENGAGED) - defense in depth against UI bypass
  - REQUIRED: Server MUST enforce circuit breaker check (reject with 503 if TRIPPED) - defense in depth against UI bypass
  - NOTE: Client-side checks are for UX; server-side checks are authoritative safety gates
- [ ] `fetch_market_prices(user_id, role)` - Returns `[MarketPricePoint]` from `/api/v1/market_prices`
  - Endpoint: `GET /api/v1/market_prices` (with underscore)
  - Schema: `{"symbol": str, "mid": Decimal|null, "timestamp": datetime|null}` per `MarketPricePoint`
  - Field: Use `mid` field (bid/ask midpoint), NOT `price`
  - REQUIRED: Response MUST include `timestamp` (ISO 8601) for each price
  - Client MUST validate recency using response timestamp, NOT local time
  - Fail-closed: reject if timestamp missing or stale (>PRICE_STALE_THRESHOLD_S)

**AsyncTradingClient Methods (NEW - require implementation):**
- [ ] `fetch_historical_bars(user_id, role, symbol, timeframe, start, end, limit)` - For chart data
  - Implementation: Proxy to Alpaca `/v2/stocks/{symbol}/bars` endpoint
  - Add to execution gateway: `GET /api/v1/market-data/{symbol}/bars`
- [ ] `fetch_cached_price(user_id, role, symbol)` - For market context initial data
  - Implementation: GET Redis cache via `/api/v1/market-data/{symbol}/quote`
  - Returns: `{"symbol", "bid", "ask", "mid", "timestamp"}` or null if not cached
- [ ] `validate_symbol(symbol)` - Optional client-side for watchlist UX (fast feedback)
  - Implementation: HEAD request to `/api/v1/symbols/{symbol}` or use asset list
  - NOTE: Server-side validation in `submit_manual_order` is REQUIRED (rejects unknown symbols)
- [ ] `get_trading_calendar(start_date, end_date)` - For chart session handling
  - Implementation: Proxy to Alpaca `/v1/calendar` endpoint or cache locally

**UserStateManager Methods:**
- [ ] `save_pending_form(form_id, form_data)` - Persists form with recovery data
- [ ] `clear_pending_form(form_id)` - Clears after successful submission
- [ ] `restore_state()` - Returns dict with `pending_forms` key

**Redis Pub/Sub Channels (global state updates):**
- [ ] `connection:state` - Payload: `{"state": "CONNECTED"|"DEGRADED"|"DISCONNECTED"|"RECONNECTING"}`
- [ ] `kill_switch:state` - Payload (matches libs/trading/risk_management/kill_switch.py):
  ```json
  {
    "state": "ACTIVE"|"ENGAGED",
    "engaged_at": str|null,  // ISO timestamp when engaged
    "engaged_by": str|null,
    "engagement_reason": str|null,
    "disengaged_at": str|null,  // ISO timestamp when disengaged
    "disengaged_by": str|null,
    "engagement_count_today": int
  }
  ```
  - ACTIVE = trading allowed, ENGAGED = trading blocked
  - For ACTIVE state: require valid `disengaged_at` if `engaged_at` exists (fail-closed)
  - **PRE-REQUISITE**: Add pub/sub publisher to KillSwitch.engage()/disengage() methods
    - Current code only writes Redis key, does NOT publish updates
    - IMPLEMENTATION: Add `self.redis.publish("kill_switch:state", json.dumps(state))` on state changes
    - Alternative: Use Redis keyspace notifications (`NOTIFY_KEYSPACE_EVENTS = "KEA"`)
- [ ] `circuit_breaker:state` - Payload (matches libs/trading/risk_management/breaker.py):
  ```json
  {
    "state": "OPEN"|"TRIPPED"|"QUIET_PERIOD",
    "tripped_at": str|null,  // ISO timestamp when tripped
    "trip_reason": str|null,
    "trip_details": str|null,
    "reset_at": str|null,  // ISO timestamp when reset
    "reset_by": str|null,
    "trip_count_today": int
  }
  ```
  - OPEN = trading allowed, TRIPPED = trading blocked, QUIET_PERIOD = transitional (treat as tripped)
  - For OPEN state: require valid `reset_at` if `tripped_at` exists (fail-closed)
  - **PRE-REQUISITE**: Add pub/sub publisher to CircuitBreaker.trip()/reset()/_transition_to_open() methods
    - Current code only writes Redis key, does NOT publish updates
    - IMPLEMENTATION: Add `self.redis.publish("circuit_breaker:state", json.dumps(state))` on state changes
    - Alternative: Use Redis keyspace notifications (`NOTIFY_KEYSPACE_EVENTS = "KEA"`)
- [ ] `price.updated.{symbol}` - Payload (MINIMAL - may need extension):
  ```json
  {"symbol": str, "price": Decimal, "timestamp": datetime, "event_type": str}
  ```
  - Current publisher: Provides `symbol`, `price`, `timestamp`, `event_type` (NO bid/ask)
  - `event_type`: String indicating the type of update (e.g., "quote", "trade")
  - IMPLEMENTATION NOTE: For bid/ask data, components should fetch from cached quote endpoint
  - FUTURE OPTION: Extend publisher to include bid/ask if realtime spread needed
  - `price` field is the **mid price** (bid/ask midpoint) from `QuoteData.mid_price`, NOT last trade

**API Timestamp Requirements (Pre-requisite for fail-closed staleness tracking):**

The following API endpoints need server-side timestamps for fail-closed staleness tracking.
Without these timestamps, the client cannot reliably detect stale data, which is a trading safety risk.

1. **`/api/v1/positions` response:**
   - REQUIRED: Add top-level `timestamp` field (ISO 8601) indicating when positions were fetched
   - FALLBACK: If top-level `timestamp` absent, client uses newest position `updated_at`
   - If no valid timestamp found, client treats data as stale (blocks submission)

2. **`/api/v1/account` response:**
   - REQUIRED: Add `timestamp` field (ISO 8601) indicating when account data was fetched
   - FALLBACK: Client tries `last_equity_change`, `updated_at`, or `as_of` fields
   - If no valid timestamp found, client treats data as stale (blocks submission)

3. **Market prices (`/api/v1/market_prices`):**
   - Endpoint: `GET /api/v1/market_prices` (with underscore)
   - Schema: `[MarketPricePoint]` with fields `symbol`, `mid`, `timestamp`
   - REQUIRED: Each price entry must include `timestamp` (ISO 8601)
   - Client validates recency using response timestamp, NOT local time
   - Fail-closed: reject if timestamp missing or stale (>PRICE_STALE_THRESHOLD_S)

**CRITICAL: Client MUST NEVER use `datetime.now()` as fallback for these timestamps.**
Using local time as fallback would mask stale data as fresh, which is a FAIL-OPEN anti-pattern.

---

### Cache Schema Documentation

**Redis Price Cache (per-symbol quotes):**
```
Key: price:{symbol}  (e.g., price:AAPL)  // Note: singular "price", not "prices"
// Canonical key: RedisKeys.price(symbol) from libs/core/redis_client/keys.py
TTL: 300 seconds (5 minutes)
Value: JSON object
{
    "symbol": "AAPL",
    "bid": "150.25",           // Decimal as string for precision
    "ask": "150.50",           // Decimal as string
    "mid": "150.375",          // Pre-calculated mid price
    "bid_size": 100,           // Bid quantity
    "ask_size": 200,           // Ask quantity
    "timestamp": "2026-01-22T14:30:00Z",  // ISO 8601 UTC
    "exchange": "NASDAQ"       // Optional, may be null
}
```

**Trading Calendar Cache (module-level):**
```python
# Module-level cache (not Redis)
_TRADING_CALENDAR_CACHE: list[date]  # Sorted list of trading days
_TRADING_CALENDAR_CACHE_TIME: datetime  # Cache timestamp (UTC)
# TTL: 24 hours
# Scope: 30 days past to 60 days future
```

**Pending Order Form (UserStateManager / Redis):**
```
Key: user_state:{user_id}
TTL: 86400 seconds (24 hours)
Structure:
{
    "pending_forms": {
        // Tab-scoped key prevents cross-tab intent sharing
        // {tab_session_id} is a 16-char hex UUID generated per OrderTicketComponent instance
        "order_entry:{tab_session_id}": {
            "form_data": {
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 100,
                "order_type": "limit",
                "limit_price": "150.00",
                "stop_price": null,
                "time_in_force": "day",
                "client_order_id": "abc123..."  // Pre-generated for idempotency
            }
        }
    },
    "preferences": {...}
}
```

---

## Testing Strategy

### Unit Tests

```
tests/apps/web_console_ng/components/
├── test_order_ticket.py
│   ├── test_order_ticket_state_transitions
│   ├── test_quantity_presets_calculation
│   ├── test_buying_power_impact_calculation
│   ├── test_form_validation
│   ├── test_form_recovery_persistence
│   ├── test_safety_gate_checks
│   ├── test_position_limit_blocks_submission            # POSITION LIMITS
│   ├── test_notional_limit_blocks_submission            # POSITION LIMITS
│   ├── test_limits_not_loaded_blocks_submission         # POSITION LIMITS (fail-closed)
│   ├── test_stale_limits_blocks_submission              # POSITION LIMITS
│   ├── test_confirm_time_refreshes_limits               # POSITION LIMITS
│   ├── test_max_respects_position_limit                 # POSITION LIMITS + MAX
│   ├── test_max_respects_notional_limit                 # POSITION LIMITS + MAX
│   ├── test_max_uses_most_restrictive_limit             # POSITION LIMITS + MAX
│   ├── test_limit_order_requires_limit_price            # ORDER TYPE VALIDATION
│   ├── test_stop_order_requires_stop_price              # ORDER TYPE VALIDATION
│   ├── test_stop_limit_requires_both_prices             # ORDER TYPE VALIDATION
│   ├── test_zero_price_rejected                         # ORDER TYPE VALIDATION
│   ├── test_negative_price_rejected                     # ORDER TYPE VALIDATION
│   ├── test_market_order_no_price_required              # ORDER TYPE VALIDATION
│   ├── test_effective_price_limit_order                 # EFFECTIVE PRICE
│   ├── test_effective_price_stop_limit_order            # EFFECTIVE PRICE
│   ├── test_effective_price_stop_buy_uses_max           # EFFECTIVE PRICE (gap safety)
│   ├── test_effective_price_stop_sell                   # EFFECTIVE PRICE
│   ├── test_effective_price_market_fallback             # EFFECTIVE PRICE
│   ├── test_confirm_validates_prices_before_limits      # TRIPLE DEFENSE ORDER
│   ├── test_final_order_data_price_validation           # TRIPLE DEFENSE FINAL
│   ├── test_notional_uses_effective_price               # NOTIONAL CALC
│   ├── test_max_uses_effective_price                    # MAX CALC
│   ├── test_total_exposure_limit_blocks_order           # EXPOSURE LIMIT
│   ├── test_total_exposure_sell_reduces_exposure        # EXPOSURE LIMIT
│   ├── test_buying_power_uses_effective_price           # BUYING POWER
│   ├── test_stop_limit_buy_limit_above_stop_rejected    # STOP-LIMIT VALIDATION
│   ├── test_stop_limit_sell_limit_below_stop_rejected   # STOP-LIMIT VALIDATION
│   ├── test_stop_limit_valid_relationship_accepted      # STOP-LIMIT VALIDATION
│   ├── test_exposure_fail_closed_when_unknown           # EXPOSURE FAIL-CLOSED
│   ├── test_exposure_recomputed_on_position_refresh     # EXPOSURE RECOMPUTE
│   ├── test_sell_closing_long_decreases_exposure        # EXPOSURE SHORT
│   ├── test_sell_opening_short_increases_exposure       # EXPOSURE SHORT
│   ├── test_buy_closing_short_decreases_exposure        # EXPOSURE SHORT
│   ├── test_flip_long_to_short_exposure_calc            # EXPOSURE SHORT
│   ├── test_invalid_decimal_price_fails_gracefully      # DECIMAL ERROR HANDLING
│   ├── test_confirm_time_recomputes_total_exposure      # CONFIRM-TIME EXPOSURE
│   ├── test_position_fetch_error_sets_exposure_none     # FETCH ERROR FAIL-CLOSED
│   ├── test_safe_decimal_handles_zero_value             # ZERO VALUE HANDLING
│   ├── test_bad_position_price_continues_other_positions # PARSE ERROR HANDLING
│   ├── test_client_order_id_unique_per_intent           # INTENT-BASED
│   ├── test_client_order_id_reused_on_retry_same_form   # INTENT-BASED
│   ├── test_client_order_id_new_on_form_change          # INTENT-BASED
│   ├── test_client_order_id_isolated_across_tabs        # CROSS-TAB
│   ├── test_client_order_id_included_in_submission
│   ├── test_stale_position_data_blocks_submission    # SAFETY
│   ├── test_stale_price_data_blocks_submission       # SAFETY
│   ├── test_connection_lost_at_confirm_blocks_submit # SAFETY
│   ├── test_ui_disabled_banner_shows_on_connection_lost    # UI DISABLE
│   ├── test_ui_disabled_banner_shows_on_kill_switch        # UI DISABLE
│   ├── test_ui_disabled_banner_hides_on_recovery           # UI DISABLE
│   ├── test_ui_disabled_banner_shows_correct_reason        # UI DISABLE
│   ├── test_submit_button_disabled_when_banner_shown       # UI DISABLE
│   ├── test_set_price_data_updates_impact_calculation      # CALLBACK
│   ├── test_set_position_data_updates_display              # CALLBACK
│   └── test_confirm_time_refreshes_all_data                # CONFIRM SAFETY
├── test_market_context.py
│   ├── test_market_data_snapshot_calculations
│   ├── test_staleness_detection
│   ├── test_fallback_behavior
│   ├── test_price_update_throttling
│   └── test_staleness_badge_updates_correctly
├── test_price_chart.py
│   ├── test_candle_data_formatting
│   ├── test_execution_marker_creation
│   ├── test_vwap_overlay_data
│   ├── test_twap_overlay_data
│   ├── test_vwap_calculation_from_candles
│   ├── test_twap_calculation_from_candles
│   ├── test_market_session_time_range_calculation
│   └── test_fallback_triggered_on_fetch_failure     # FALLBACK
├── test_watchlist.py
│   ├── test_watchlist_persistence_save_load
│   ├── test_symbol_add_remove
│   ├── test_drag_reorder
│   ├── test_max_symbols_limit
│   └── test_sparkline_data_accumulation
├── test_sparkline.py
│   ├── test_sparkline_svg_generation
│   ├── test_color_selection_by_trend
│   └── test_normalization
└── test_order_entry_context.py
    ├── test_dispose_unsubscribes_all_channels       # CLEANUP
    ├── test_dispose_cancels_all_timers              # CLEANUP
    ├── test_subscription_tracking
    ├── test_double_dispose_is_safe
    ├── test_callbacks_ignored_after_dispose         # DISPOSE/CALLBACK RACE
    ├── test_symbol_selection_ignored_after_dispose  # DISPOSE/CALLBACK RACE
    ├── test_price_subscription_changes_on_symbol_select   # SUBSCRIPTION
    ├── test_price_update_dispatched_to_order_ticket       # DISPATCH
    ├── test_position_update_dispatched_to_order_ticket    # DISPATCH
    ├── test_position_update_sets_zero_when_absent         # POSITION CLOSED
    ├── test_kill_switch_dispatched_to_order_ticket        # DISPATCH
    ├── test_connection_state_dispatched_to_order_ticket   # DISPATCH
    ├── test_no_duplicate_price_subscriptions              # CLEANUP
    ├── test_child_timers_registered_via_tracker           # TIMER LIFECYCLE
    ├── test_staleness_timer_registered_via_tracker        # TIMER LIFECYCLE
    ├── test_selection_version_prevents_stale_updates      # RACE PREVENTION
    └── test_rapid_symbol_switches_only_latest_applied     # RACE PREVENTION
```

### Integration Tests

```
tests/apps/web_console_ng/integration/
├── test_order_ticket_with_market_context.py
│   ├── test_symbol_selection_updates_both_components
│   ├── test_price_update_reflects_in_buying_power
│   └── test_position_display_matches_market_context
├── test_watchlist_symbol_selection.py
│   ├── test_click_updates_order_ticket
│   ├── test_click_updates_chart
│   └── test_click_updates_market_context
├── test_order_submission_flow.py
│   ├── test_preview_dialog_shows_correct_data
│   ├── test_confirmation_checks_kill_switch
│   ├── test_successful_submission_clears_form
│   ├── test_idempotent_resubmit_with_same_client_order_id  # IDEMPOTENCY
│   ├── test_form_recovery_after_browser_crash              # RECOVERY
│   ├── test_final_cb_recheck_blocks_on_trip                # FINAL SAFETY
│   ├── test_price_timestamp_required_at_confirm            # PRICE RECENCY
│   ├── test_stale_price_timestamp_blocks_submit            # PRICE RECENCY
│   └── test_symbol_validation_at_submit                    # VALIDATION
├── test_reconnect_recovery.py
│   ├── test_safety_state_refetched_on_reconnect            # RECONNECT
│   ├── test_subscriptions_revalidated_on_reconnect         # RECONNECT
│   ├── test_safety_fetch_timeout_on_reconnect_stays_closed # RECONNECT TIMEOUT
│   └── test_safety_fetch_error_on_reconnect_stays_closed   # RECONNECT ERROR
└── test_subscription_lifecycle.py
    ├── test_subscriptions_cleaned_up_on_page_exit          # CLEANUP
    ├── test_no_duplicate_subscriptions_on_symbol_change    # CLEANUP
    ├── test_subscription_count_after_multiple_navigations  # CLEANUP
    ├── test_refcount_overlap_watchlist_unsubscribe_preserves_selected  # OVERLAP
    ├── test_refcount_overlap_selected_unsubscribe_preserves_watchlist  # OVERLAP
    ├── test_concurrent_subscribe_requests_single_subscription          # CONCURRENCY
    ├── test_concurrent_subscribe_failure_clears_all_owners            # CONCURRENCY FAILURE
    ├── test_concurrent_acquirers_await_same_pending_future            # PENDING FUTURE
    ├── test_pending_future_set_under_lock_prevents_race               # RACE PREVENTION
    ├── test_resubscribe_all_channels_on_reconnect                     # RECONNECT RESUBSCRIBE
    ├── test_resubscribe_uses_correct_callbacks_per_channel            # CALLBACK REGISTRY
    ├── test_resubscribe_position_channel_uses_position_callback       # CALLBACK REGISTRY
    ├── test_orphaned_subscribe_unsubscribes_when_owners_release       # ORPHAN PREVENTION
    ├── test_dispose_resolves_pending_futures                          # DISPOSE CLEANUP
    ├── test_dispose_clears_callback_registry                          # CLEANUP
    ├── test_subscribe_failure_clears_callback_registry                # FAILURE CLEANUP
    ├── test_resubscribe_skips_released_channel_during_reconnect       # RECONNECT RACE
    ├── test_invalid_persisted_watchlist_symbols_skipped               # PERSISTENCE VALIDATION
    ├── test_failed_subscription_retried_on_reconnect                  # RETRY MECHANISM
    ├── test_retry_clears_from_failed_on_success                       # RETRY CLEANUP
    ├── test_failed_subscription_removed_on_release                    # RELEASE AFTER FAILURE
    ├── test_failed_subscription_retry_restores_all_owners             # MULTI-OWNER RETRY
    ├── test_partial_release_keeps_remaining_owners_in_failed          # PARTIAL RELEASE
    ├── test_final_connection_check_blocks_submit                      # FINAL GATE
    ├── test_js_template_literal_injection_prevented                   # XSS PREVENTION
    └── test_subscribe_failure_rolls_back_ownership                     # ERROR HANDLING
```

### E2E Tests

```
tests/e2e/
├── test_order_entry_workflow.py
│   ├── test_full_order_entry_happy_path
│   ├── test_order_entry_with_kill_switch_engaged
│   ├── test_order_entry_with_connection_lost
│   ├── test_hotkey_driven_order_entry
│   ├── test_stale_data_blocking_prevents_submission        # SAFETY
│   └── test_retry_with_same_order_is_idempotent            # IDEMPOTENCY
├── test_chart_rendering.py
│   ├── test_chart_loads_for_symbol
│   ├── test_execution_markers_displayed
│   ├── test_chart_fallback_on_error
│   ├── test_chart_fallback_on_cdn_load_failure             # FALLBACK
│   └── test_vwap_twap_overlays_render_correctly
└── test_watchlist_persistence.py
    ├── test_watchlist_survives_page_reload
    ├── test_watchlist_order_persisted
    └── test_watchlist_removal_persisted
```

### Safety & Failure Mode Tests (Critical for Trading)

```
tests/apps/web_console_ng/safety/
├── test_stale_data_rejection.py
│   ├── test_submission_blocked_when_position_data_stale
│   ├── test_submission_blocked_when_price_data_stale
│   ├── test_submission_blocked_when_buying_power_stale
│   └── test_staleness_threshold_configurable
├── test_idempotency.py
│   ├── test_intent_id_reused_on_same_form_retry           # INTENT-BASED
│   ├── test_intent_id_changes_on_form_modification        # INTENT-BASED
│   ├── test_intent_id_differs_across_tabs                 # CROSS-TAB
│   ├── test_form_recovery_uses_stored_intent_id           # RECOVERY
│   ├── test_restore_uses_tab_scoped_key                   # TAB-SCOPED KEY
│   ├── test_save_and_restore_pending_form_key_match       # KEY CONSISTENCY
│   ├── test_clear_pending_form_uses_tab_scoped_key        # KEY CONSISTENCY
│   └── test_double_submit_with_same_intent_deduped        # BROKER DEDUPE
├── test_connection_safety.py
│   ├── test_connection_lost_during_preview_blocks_confirm
│   ├── test_connection_lost_after_submit_shows_pending_state
│   └── test_reconnect_restores_pending_order_form
├── test_redis_failure_safety.py
│   ├── test_circuit_breaker_verify_timeout_blocks_submission   # FAIL-CLOSED
│   ├── test_circuit_breaker_verify_error_blocks_submission     # FAIL-CLOSED
│   ├── test_kill_switch_verify_timeout_blocks_submission       # FAIL-CLOSED
│   ├── test_kill_switch_verify_error_blocks_submission         # FAIL-CLOSED
│   ├── test_kill_switch_api_failure_blocks_submission          # FAIL-CLOSED
│   ├── test_redis_unavailable_at_confirm_blocks_submission     # FAIL-CLOSED
│   ├── test_initial_safety_fetch_timeout_sets_fail_closed      # INIT SAFETY
│   └── test_initial_safety_fetch_error_sets_fail_closed        # INIT SAFETY
└── test_cleanup.py
    ├── test_page_exit_unsubscribes_all_channels
    ├── test_symbol_change_unsubscribes_previous
    └── test_no_memory_leak_after_multiple_navigations
```

### Test Coverage Targets

| Component | Target Coverage |
|-----------|-----------------|
| order_ticket.py | 90% |
| quantity_presets.py | 95% |
| market_context.py | 85% |
| level1_display.py | 80% |
| price_chart.py | 75% (JS integration limits) |
| watchlist.py | 90% |
| sparkline.py | 95% |

---

## Security Considerations

1. **CSRF Protection:** All workspace API calls include CSRF token
2. **User Identity:** User ID derived from session, never from client header
3. **Grid ID Allowlist:** Add `"watchlist.main"` to `VALID_GRID_IDS`
4. **Rate Limiting:** Order submission respects existing 40 req/min limit
5. **Input Validation:** Symbol validation before adding to watchlist
6. **XSS Prevention:** Sparkline SVG uses numeric data only, no user strings

### CDN Asset Security (Supply-Chain Protection)

```python
# CDN assets with SRI (Subresource Integrity) hashes
# Prevents CDN compromise from injecting malicious code

CDN_ASSETS = {
    "lightweight-charts": {
        "url": "https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js",
        "integrity": "sha384-<hash>",  # Generate: openssl dgst -sha384 -binary | openssl base64 -A
        "crossorigin": "anonymous",
    },
    "sortablejs": {
        "url": "https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js",
        "integrity": "sha384-<hash>",
        "crossorigin": "anonymous",
    },
}

# CSP headers (add to NiceGUI config or nginx)
# Content-Security-Policy: script-src 'self' unpkg.com cdn.jsdelivr.net;

# Local fallback for airgapped/high-security deployments
LOCAL_ASSETS = {
    "lightweight-charts": "/static/vendor/lightweight-charts.4.1.0.production.js",
    "sortablejs": "/static/vendor/sortable.1.15.0.min.js",
}

async def load_cdn_asset_with_fallback(asset_name: str) -> None:
    """Load CDN asset with SRI, fallback to local on failure."""
    asset = CDN_ASSETS.get(asset_name)
    local = LOCAL_ASSETS.get(asset_name)

    if not asset:
        raise ValueError(f"Unknown asset: {asset_name}")

    await ui.run_javascript(f"""
        (async function() {{
            try {{
                const script = document.createElement('script');
                script.src = '{asset["url"]}';
                script.integrity = '{asset["integrity"]}';
                script.crossOrigin = '{asset["crossorigin"]}';

                await new Promise((resolve, reject) => {{
                    script.onload = resolve;
                    script.onerror = reject;
                    document.head.appendChild(script);
                }});
            }} catch (e) {{
                console.warn('CDN load failed, using local fallback:', e);
                const fallback = document.createElement('script');
                fallback.src = '{local}';
                await new Promise(resolve => {{
                    fallback.onload = resolve;
                    document.head.appendChild(fallback);
                }});
            }}
        }})();
    """)
```

### Implementation Checklist for Security

- [ ] Generate SRI hashes for all CDN assets at build time
- [ ] Add CDN domains to CSP script-src allowlist
- [ ] Bundle local copies of all CDN assets in `/static/vendor/`
- [ ] Test fallback behavior when CDN is blocked
- [ ] Document asset provenance and update procedure

---

## Definition of Done

### Core Functionality
- [ ] All 4 tasks implemented (T4.1-T4.4)
- [ ] Order ticket always visible on dashboard
- [ ] Market context displayed for selected symbol
- [ ] Chart integrated with execution markers and VWAP/TWAP overlays
- [ ] Watchlist functional and persisted

### Safety & Reliability (Trading-Critical)
- [ ] Client_order_id passed to submit_manual_order for idempotency
- [ ] Stale data checks block submission (position, price, buying power)
- [ ] Connection state re-checked at confirm time
- [ ] Subscription cleanup on page exit (no memory leaks)
- [ ] Form recovery with idempotent resubmission works after browser crash

### API & Dependencies
- [ ] `fetch_historical_bars()` added to AsyncTradingClient
- [ ] `"watchlist.main"` added to workspace allowlist
- [ ] All dependency checklist items verified

### Security
- [ ] CDN assets loaded with SRI hashes
- [ ] Local fallback assets bundled
- [ ] CSP allowlist updated for CDN domains
- [ ] Chart licensing attribution included

### Testing
- [ ] Unit tests > 85% coverage
- [ ] Safety tests pass (stale data, idempotency, cleanup)
- [ ] E2E tests pass
- [ ] Code reviewed and approved

### UX
- [ ] Responsive layout works on tablet and desktop
- [ ] Mobile fallback (watchlist drawer) functional

---

## Implementation Order

1. **T4.1 Order Ticket** (HIGH) - Core functionality, enables testing
2. **T4.2 Market Context** (MEDIUM) - Required for order ticket context
3. **T4.4 Watchlist** (MEDIUM) - Symbol selection driver
4. **T4.3 Price Chart** (MEDIUM) - Visual enhancement, can be added last

---

**Last Updated:** 2026-01-22
**Status:** PLAN
