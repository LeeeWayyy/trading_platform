---
id: P6T7
title: "Professional Trading Terminal - Order Actions"
phase: P6
task: T7
priority: P0
owner: "@development-team"
state: PLANNING
created: 2026-01-13
dependencies: [P6T2, P6T6]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T7.1-T7.4]
---

# P6T7: Order Actions

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLANNING
**Priority:** P0 (Core Trading)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 7 of 18
**Dependency:** P6T2 (kill switch gating), P6T6 (fat finger validation)

---

## Objective

Implement critical order actions: flatten controls, cancel all, order replay, and one-click trading with comprehensive safety controls.

**Success looks like:**
- Quick emergency flatten from dashboard
- One-click cancel all with filtering
- One-click trading for rapid execution (with safety controls)
- Order replay for quick re-entry

---

## Architecture Overview

### Existing Infrastructure (Reuse)

| Component | Location | Purpose |
|-----------|----------|---------|
| `AsyncTradingClient` | `core/client.py` | Has `close_position()`, `cancel_all_orders()`, `flatten_all_positions()`, `submit_manual_order()` |
| `ActionButton` | `components/action_button.py` | State machine for async operations (DEFAULT→SENDING→SUCCESS/FAILED) |
| `FatFingerValidator` | `components/fat_finger_validator.py` | Pre-submission validation with `FatFingerThresholds` |
| `on_close_position()` | `components/positions_grid.py` | Pattern for kill switch checking + dialog confirmation |
| `on_cancel_order()` | `components/orders_table.py` | Pattern for order cancellation (no safety gate - risk reducing) |
| `OrderEntryContext` | `components/order_entry_context.py` | Central orchestrator with state callbacks |
| `position_management.py` | `pages/position_management.py` | MFA id_token retrieval pattern |

### Safety Gate Classification

| Action Type | Risk Level | Safety Pattern |
|-------------|------------|----------------|
| Cancel (single/all) | Risk-Reducing | **Fail-Open**: No safety gates, always allowed |
| Close Position | Risk-Reducing | **Fail-Open**: Warn on CB tripped, allow; block only on explicit kill switch |
| Flatten (single/all) | Risk-Reducing | **Fail-Open**: Same as close, require MFA for flatten-all |
| **Reverse Position** | **Risk-Increasing** | **Fail-Closed**: Block on ANY safety uncertainty (KS, CB, connection, fat finger) |
| **One-Click Orders** | **Risk-Increasing** | **Fail-Closed**: Block on ANY safety uncertainty, require fresh price |

### Reusable Safety Gate Helper (NEW)

**Rationale:** Gemini review identified code duplication risk. Extract the complex safety-check logic from `positions_grid.py/on_close_position()` into a reusable module.

```python
# New file: apps/web_console_ng/components/safety_gate.py

import httpx
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)

class SafetyPolicy(Enum):
    """Safety gate behavior policy."""
    FAIL_OPEN = "fail_open"    # Risk-reducing: allow on uncertainty
    FAIL_CLOSED = "fail_closed"  # Risk-increasing: block on uncertainty

@dataclass(frozen=True)
class SafetyCheckResult:
    """Result of safety gate check."""
    allowed: bool
    reason: str | None
    warnings: list[str]

class SafetyGate:
    """Reusable safety gate for order actions.

    Centralizes the complex logic for:
    - Cached state pre-check (instant UI response)
    - API validation with 5xx vs 4xx handling

    IMPLEMENTATION NOTE: Startup Race Condition
    - Fail-closed actions require cached states to be available
    - Ensure OrderEntryContext._fetch_initial_safety_state() completes before
      enabling risk-increasing actions (Reverse, One-Click)
    - Consider adding an `is_ready` property that returns False until initial
      state fetch completes, and block fail-closed actions when not ready
    - Policy-based fail-open/fail-closed behavior
    """

    def __init__(
        self,
        client: AsyncTradingClient,
        user_id: str,
        user_role: str,
        strategies: list[str] | None = None,
    ):
        self._client = client
        self._user_id = user_id
        self._user_role = user_role
        self._strategies = strategies

    async def check(
        self,
        *,
        policy: SafetyPolicy,
        cached_kill_switch: bool | None = None,
        cached_connection_state: str | None = None,
        cached_circuit_breaker: bool | None = None,
        require_connected: bool = True,
    ) -> SafetyCheckResult:
        """Check all safety gates with specified policy.

        CRITICAL: For FAIL_CLOSED policy, unknown/None state = BLOCKED.
        This ensures risk-increasing actions truly fail closed on ANY uncertainty.

        Args:
            policy: FAIL_OPEN for risk-reducing, FAIL_CLOSED for risk-increasing
            cached_*: Cached state from OrderEntryContext for instant response
            require_connected: Whether to check connection state

        Returns:
            SafetyCheckResult with allowed status and any warnings
        """
        warnings: list[str] = []

        # 1. Connection state check
        if require_connected:
            if cached_connection_state is None or cached_connection_state == "UNKNOWN":
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(False, "Connection state unknown - cannot proceed", [])
                warnings.append("Connection state unknown - proceeding with caution")
            elif cached_connection_state in {"DISCONNECTED", "RECONNECTING", "DEGRADED"}:
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(False, f"Connection {cached_connection_state}", [])
                warnings.append(f"Connection {cached_connection_state} - proceeding with caution")

        # 2. Kill switch check (FAIL_CLOSED blocks on unknown)
        if cached_kill_switch is None:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Kill switch state unknown - cannot proceed", [])
            warnings.append("Kill switch state unknown - proceeding with caution")
        elif cached_kill_switch is True:
            return SafetyCheckResult(False, "Kill Switch is ENGAGED", [])

        # 3. Circuit breaker check (FAIL_CLOSED blocks on unknown)
        if cached_circuit_breaker is None:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Circuit breaker state unknown - cannot proceed", [])
            warnings.append("Circuit breaker state unknown - proceeding with caution")
        elif cached_circuit_breaker is True:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Circuit breaker is TRIPPED", [])
            warnings.append("Circuit breaker TRIPPED - allowed for risk reduction")

        return SafetyCheckResult(True, None, warnings)

    async def check_with_api_verification(
        self,
        *,
        policy: SafetyPolicy,
        cached_connection_state: str | None = None,  # Pass current connection state
    ) -> SafetyCheckResult:
        """Check safety gates with fresh API verification at confirm time.

        Used in confirmation dialogs to ensure state hasn't changed.

        For FAIL_CLOSED actions: Verifies kill switch, circuit breaker, AND connection state.
        For FAIL_OPEN actions: Verifies BOTH but only warns on circuit breaker (doesn't block).
        """
        warnings: list[str] = []

        # 0. Connection state check (for FAIL_CLOSED actions)
        # CRITICAL: Must re-check connection at confirm time for risk-increasing actions
        # FAIL-CLOSED: Block on unknown/None state (uncertainty = block)
        READ_ONLY_STATES = {"DISCONNECTED", "RECONNECTING", "DEGRADED"}
        KNOWN_GOOD_STATES = {"CONNECTED"}  # Only CONNECTED allows trading (per order_entry_context.py)

        if policy == SafetyPolicy.FAIL_CLOSED:
            # Block on None/unknown (fail-closed means uncertainty = block)
            if cached_connection_state is None:
                return SafetyCheckResult(
                    False, "Connection state unknown (FAIL-CLOSED)", []
                )
            state_upper = cached_connection_state.upper()
            if state_upper in READ_ONLY_STATES:
                return SafetyCheckResult(
                    False, f"Connection is {cached_connection_state} (FAIL-CLOSED)", []
                )
            if state_upper not in KNOWN_GOOD_STATES:
                return SafetyCheckResult(
                    False, f"Connection state '{cached_connection_state}' unknown (FAIL-CLOSED)", []
                )
        else:
            # FAIL_OPEN: Warn but proceed for risk-reducing actions
            # CRITICAL: Also warn on None/UNKNOWN to maintain visibility (matches check() behavior)
            if cached_connection_state is None:
                warnings.append("Connection state unknown - proceeding (risk-reducing)")
            elif cached_connection_state.upper() in READ_ONLY_STATES:
                warnings.append(f"Connection is {cached_connection_state} - proceeding (risk-reducing)")
            elif cached_connection_state.upper() not in KNOWN_GOOD_STATES:
                warnings.append(f"Connection state '{cached_connection_state}' unknown - proceeding (risk-reducing)")

        # 1. Fresh kill switch check via API
        # Error handling mirrors positions_grid.py logic:
        # - FAIL_OPEN (risk-reducing): 5xx = warn & proceed, 4xx = block (invalid request)
        # - FAIL_CLOSED (risk-increasing): any error = block
        try:
            ks = await self._client.fetch_kill_switch_status(
                self._user_id, role=self._user_role, strategies=self._strategies
            )
            if ks.get("state") == "ENGAGED":
                return SafetyCheckResult(False, "Kill Switch engaged", [])
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status >= 500:
                # 5xx = Server error (transient)
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(False, f"Kill switch check failed (HTTP {status})", [])
                # FAIL_OPEN: Allow risk-reducing action when safety service has transient issues
                warnings.append(f"Kill switch service error ({status}) - proceeding (risk-reducing)")
            else:
                # 4xx = Client error (invalid/unauthorized) - always block
                return SafetyCheckResult(False, f"Kill switch check failed (HTTP {status} - invalid request)", [])
        except httpx.RequestError:
            if policy == SafetyPolicy.FAIL_CLOSED:
                return SafetyCheckResult(False, "Kill switch service unreachable", [])
            # FAIL_OPEN: Allow risk-reducing action when safety service is unreachable
            warnings.append("Kill switch service unreachable - proceeding (risk-reducing)")

        # 2. Fresh circuit breaker check via API
        # FAIL_CLOSED: Block on tripped or error
        # FAIL_OPEN: Warn on tripped (don't block risk-reducing actions), ignore errors
        try:
            cb = await self._client.fetch_circuit_breaker_status(
                self._user_id, role=self._user_role, strategies=self._strategies
            )
            cb_state = str(cb.get("state", "")).upper()
            if cb_state in {"TRIPPED", "ENGAGED", "ON", "QUIET_PERIOD"}:
                if policy == SafetyPolicy.FAIL_CLOSED:
                    return SafetyCheckResult(False, f"Circuit breaker is {cb_state}", [])
                # FAIL_OPEN: Warn but allow risk-reducing action (matches on_close_position behavior)
                warnings.append(f"Warning: Circuit breaker is {cb_state}")
        except httpx.HTTPStatusError as exc:
            if policy == SafetyPolicy.FAIL_CLOSED:
                # FAIL_CLOSED: Block on any CB verification failure
                return SafetyCheckResult(False, f"Circuit breaker check failed (HTTP {exc.response.status_code})", [])
            # FAIL_OPEN: Allow risk-reducing action on CB service error
            warnings.append(f"Circuit breaker service error ({exc.response.status_code}) - proceeding")
        except httpx.RequestError:
            if policy == SafetyPolicy.FAIL_CLOSED:
                # FAIL_CLOSED: Block if CB service unreachable
                return SafetyCheckResult(False, "Circuit breaker service unreachable", [])
            # FAIL_OPEN: Allow risk-reducing action if CB service unreachable
            warnings.append("Circuit breaker service unreachable - proceeding")

        return SafetyCheckResult(True, None, warnings)
```

### OrderEntryContext Safety State - Fields AND Getters (NEW)

**Rationale:** Codex review identified that plan adds getters but no backing fields or update logic.

**Add backing fields and update logic to `OrderEntryContext`:**

```python
# Add to apps/web_console_ng/components/order_entry_context.py

class OrderEntryContext:
    def __init__(self, ...):
        # ... existing init ...

        # NEW: Cached safety state fields (backing storage)
        self._cached_kill_switch_engaged: bool | None = None
        self._cached_circuit_breaker_tripped: bool | None = None
        self._cached_connection_state: str = "UNKNOWN"
        self._cached_prices: dict[str, tuple[Decimal, datetime]] = {}

    # UPDATE existing callbacks to populate cached state:

    def _on_kill_switch_update(self, data: dict) -> None:
        """Handle kill switch pub/sub update."""
        # ... existing logic ...
        # NEW: Update cached state
        self._cached_kill_switch_engaged = data.get("state") == "ENGAGED"
        # ... propagate to components ...

    def _on_circuit_breaker_update(self, data: dict) -> None:
        """Handle circuit breaker pub/sub update."""
        # ... existing logic ...
        # NEW: Update cached state
        state = str(data.get("state", "")).upper()
        self._cached_circuit_breaker_tripped = state in {"TRIPPED", "ENGAGED", "ON", "QUIET_PERIOD"}
        # ... propagate to components ...

    async def _on_connection_update(self, data: dict[str, Any]) -> None:
        """Handle connection update (actual signature from order_entry_context.py)."""
        # ... existing validation logic ...
        state = str(data.get("state", "")).upper()
        # NEW: Update cached state
        self._cached_connection_state = state
        # ... existing logic dispatching to components ...

    async def _on_price_update(self, data: dict[str, Any]) -> None:
        """Handle price update (actual signature from order_entry_context.py)."""
        # ... existing validation logic ...
        symbol = data.get("symbol")
        price = data.get("price")
        raw_timestamp = data.get("timestamp")

        # CRITICAL: Validate price is positive and finite (prevents invalid cached prices)
        if price is not None:
            try:
                price_decimal = Decimal(str(price))
                if price_decimal <= 0 or not price_decimal.is_finite():
                    logger.warning("invalid_price_update", extra={"symbol": symbol, "price": price})
                    return  # Don't cache invalid prices
            except (InvalidOperation, ValueError):
                logger.warning("unparseable_price_update", extra={"symbol": symbol, "price": price})
                return  # Don't cache unparseable prices

        # Parse timestamp (consistent with existing code pattern)
        # CRITICAL: Missing timestamps are treated as STALE (not now) for FAIL-CLOSED safety
        timestamp: datetime | None = None
        if raw_timestamp:
            if isinstance(raw_timestamp, datetime):
                timestamp = raw_timestamp
            else:
                # Parse ISO timestamp string
                from apps.web_console_ng.utils.time import parse_iso_timestamp
                timestamp = parse_iso_timestamp(str(raw_timestamp))
        # If no timestamp or failed to parse -> timestamp remains None (won't cache)

        if symbol and price is not None and timestamp is not None:
            # NEW: Update cached prices with parsed timestamp (only valid data)
            self._cached_prices[symbol] = (Decimal(str(price)), timestamp)
        # ... existing logic dispatching to components ...

    # NEW: Read-only getters for SafetyGate and OneClickHandler

    @property
    def cached_kill_switch_engaged(self) -> bool | None:
        """Get cached kill switch state (None if unknown)."""
        return self._cached_kill_switch_engaged

    @property
    def cached_connection_state(self) -> str:
        """Get cached connection state."""
        return self._cached_connection_state

    @property
    def cached_circuit_breaker_tripped(self) -> bool | None:
        """Get cached circuit breaker state (None if unknown)."""
        return self._cached_circuit_breaker_tripped

    @property
    def cached_prices(self) -> dict[str, tuple[Decimal, datetime]]:
        """Get cached prices by symbol with timestamps (copy for safety)."""
        return self._cached_prices.copy()

    # CRITICAL: Update _fetch_initial_safety_state to populate cached fields immediately
    # This ensures SafetyGate has valid data from startup (not just after first pub/sub update)
    #
    # IMPORTANT: This uses the existing Redis-authoritative approach (already implemented
    # in order_entry_context.py). The cached fields are populated from the same Redis
    # data that the existing code already fetches - NOT from additional API calls.

    async def _fetch_initial_safety_state(self) -> None:
        """Fetch initial safety state on startup.

        CRITICAL: Must populate cached_* fields immediately so FAIL-CLOSED actions
        (Reverse, One-Click) work from first interaction, not just after first pub/sub update.

        NOTE: This method already exists and fetches from Redis (authoritative source).
        We simply add cached field population from the same Redis data that is already parsed.
        """
        # Existing logic fetches from Redis and updates UI components...
        # After parsing Redis data for kill switch:
        # ks_engaged = redis_ks_data.get("state") == "ENGAGED"  (existing parse)
        # self._order_ticket.set_kill_switch_state(ks_engaged)  (existing)
        # NEW: Also populate cached field for SafetyGate
        # self._cached_kill_switch_engaged = ks_engaged

        # After parsing Redis data for circuit breaker:
        # cb_state = str(redis_cb_data.get("state", "")).upper()  (existing parse)
        # cb_tripped = cb_state in {"TRIPPED", "ENGAGED", "ON", "QUIET_PERIOD"}  (existing)
        # self._order_ticket.set_circuit_breaker_state(cb_tripped)  (existing)
        # NEW: Also populate cached field for SafetyGate
        # self._cached_circuit_breaker_tripped = cb_tripped

        # The key change is: add one line per safety state to also set the cached field
        # from the same Redis data that is already being parsed and used.
        pass  # Implementation detail - add cached field assignment after existing parse

    # CRITICAL: Handler for one-click events - sources working_orders for Alt-Click

    async def handle_one_click(self, args: dict) -> None:
        """Handle one-click events from DOM ladder/chart.

        Routes to appropriate OneClickHandler method based on mode.
        CRITICAL: For alt_cancel mode, fetches fresh working orders.
        """
        mode = args.get("mode")
        symbol = args.get("symbol")

        # CRITICAL: Validate symbol is present (required for all one-click actions)
        # Fallback: Try to derive from containerId if DOM ladder set data-symbol attribute
        if not symbol:
            container_id = args.get("containerId")
            if container_id and hasattr(self, "_container_symbol_map"):
                symbol = self._container_symbol_map.get(container_id)
            if not symbol:
                ui.notify("Cannot execute: symbol not specified", type="negative")
                logger.warning("one_click_missing_symbol", extra={"args": args})
                return

        # Validate side for shift/ctrl modes (required for order submission)
        if mode in {"shift_limit", "ctrl_market"}:
            side = args.get("side")
            if side not in {"buy", "sell"}:
                ui.notify(f"Cannot execute: invalid side '{side}'", type="negative")
                logger.warning("one_click_invalid_side", extra={"side": side, "mode": mode})
                return

        if mode == "shift_limit":
            # Defensive price validation (parseability, positivity, finiteness)
            price_raw = args.get("price")
            if price_raw is None:
                ui.notify("Cannot place order: price not specified", type="negative")
                return
            try:
                price = Decimal(str(price_raw))
                if not price.is_finite() or price <= 0:
                    ui.notify(f"Cannot place order: price must be positive ({price_raw})", type="negative")
                    return
            except (InvalidOperation, ValueError):
                ui.notify(f"Cannot place order: invalid price '{price_raw}'", type="negative")
                return
            await self._one_click_handler.on_shift_click(
                symbol=symbol,
                price=price,
                side=args["side"],
            )
        elif mode == "ctrl_market":
            await self._one_click_handler.on_ctrl_click(
                symbol=symbol,
                side=args["side"],
            )
        elif mode == "alt_cancel":
            # CRITICAL: Fetch fresh orders for accurate cancellation
            # FAIL-OPEN: On fetch error, warn and exit cleanly (risk-reducing action)
            try:
                response = await self._client.fetch_open_orders(
                    self._user_id, role=self._role, strategies=self._strategies
                )
                working_orders = response.get("orders", [])
            except Exception as fetch_exc:
                ui.notify(f"Cannot fetch orders for cancel: {fetch_exc}", type="warning")
                logger.warning("alt_click_fetch_failed", extra={"error": str(fetch_exc)})
                return
            # Defensive price validation (parseability, positivity, finiteness)
            price_raw = args.get("price")
            if price_raw is None:
                ui.notify("Cannot cancel: price not specified", type="negative")
                return
            try:
                price = Decimal(str(price_raw))
                if not price.is_finite() or price <= 0:
                    ui.notify(f"Cannot cancel: price must be positive ({price_raw})", type="negative")
                    return
            except (InvalidOperation, ValueError):
                ui.notify(f"Cannot cancel: invalid price '{price_raw}'", type="negative")
                return
            # Derive is_read_only from connection state (matches READ_ONLY_CONNECTION_STATES)
            # CRITICAL: Include UNKNOWN and None as read-only to ensure warnings are shown
            is_read_only = (
                self._cached_connection_state is None
                or self._cached_connection_state in {"DISCONNECTED", "RECONNECTING", "DEGRADED", "UNKNOWN"}
            )
            await self._one_click_handler.on_alt_click(
                symbol=symbol,
                price=price,
                working_orders=working_orders,
                is_read_only=is_read_only,
            )
```

### Client API Extension Required

**CRITICAL: `cancel_order` needs to send CancelOrderRequest payload**

The backend requires `reason`, `requested_by`, `requested_at` for cancel orders (schema: `CancelOrderRequest`). Update `AsyncTradingClient.cancel_order()`:

```python
# apps/web_console_ng/core/client.py - Update cancel_order signature

async def cancel_order(
    self,
    order_id: str,
    user_id: str,
    role: str | None = None,
    strategies: list[str] | None = None,  # Required for auth header scope
    *,
    reason: str,  # min 10 chars
    requested_by: str,
    requested_at: str,  # ISO format
) -> dict[str, Any]:
    """Cancel an order by client_order_id (POST with CancelOrderRequest body)."""
    headers = self._get_auth_headers(user_id, role, strategies)  # Include strategies for auth scope
    payload = {
        "reason": reason,
        "requested_by": requested_by,
        "requested_at": requested_at,
    }
    resp = await self._client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers=headers,
        json=payload,
    )
    resp.raise_for_status()
    return self._json_dict(resp)
```

**All cancel_order call sites must be updated to pass audit fields AND strategies.**

**Client API Extensions Required (strategies propagation)**

Update the following client method signatures to accept `strategies` and pass through to `_get_auth_headers(...)`:

```python
# apps/web_console_ng/core/client.py - Update signatures to accept strategies

async def submit_manual_order(
    self,
    order_data: dict[str, Any],
    user_id: str,
    role: str | None = None,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    headers = self._get_auth_headers(user_id, role, strategies)
    ...

async def close_position(
    self,
    symbol: str,
    reason: str,
    requested_by: str,
    requested_at: str,
    user_id: str,
    role: str | None = None,
    qty: int | None = None,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    headers = self._get_auth_headers(user_id, role, strategies)
    ...

async def cancel_all_orders(
    self,
    symbol: str,
    reason: str,
    requested_by: str,
    requested_at: str,
    user_id: str,
    role: str | None = None,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    headers = self._get_auth_headers(user_id, role, strategies)
    ...

async def flatten_all_positions(
    self,
    reason: str,
    requested_by: str,
    requested_at: str,
    id_token: str,
    user_id: str,
    role: str | None = None,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    headers = self._get_auth_headers(user_id, role, strategies)
    ...
```

**All call sites must pass `strategies` for authorization scope parity.**

### Backend API Endpoints (Already Implemented)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/positions/{symbol}/close` | POST | Close single position |
| `/api/v1/orders/cancel-all` | POST | Cancel all orders for symbol (no side filter) |
| `/api/v1/positions/flatten-all` | POST | Flatten all (requires MFA id_token) |
| `/api/v1/orders/{client_order_id}/cancel` | POST | Cancel single order (requires CancelOrderRequest body) |
| `/api/v1/manual/orders` | POST | Submit manual order with audit trail |

### MFA Token Retrieval Pattern

**Rationale:** Codex review (HIGH-6) identified need for explicit id_token retrieval.

```python
# Existing pattern from pages/position_management.py
id_token = app.storage.user.get("id_token")
if not id_token:
    # Trigger re-authentication flow
    ui.notify("MFA required - please re-authenticate", type="warning")
    # ... redirect to auth
    return
```

---

## Tasks (4 total)

### T7.1: Flatten Strategy/Symbol Button - HIGH PRIORITY

**Goal:** Quick emergency flatten from dashboard.

**Prerequisite:** P6T2.4 (Kill Switch) must be complete - flatten is a safety-critical operation.

#### Implementation Plan

**Component 1: `flatten_controls.py` - Position Row Actions**

```python
# New file: apps/web_console_ng/components/flatten_controls.py

import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

PRICE_STALENESS_THRESHOLD_S = 30  # Block if price older than 30s

class FlattenControls:
    """Flatten/Reverse buttons for position rows."""

    def __init__(
        self,
        safety_gate: SafetyGate,
        trading_client: AsyncTradingClient,
        fat_finger_validator: FatFingerValidator,
        order_entry_context: OrderEntryContext,
        strategies: list[str] | None = None,
    ):
        self._safety = safety_gate
        self._client = trading_client
        self._validator = fat_finger_validator
        self._context = order_entry_context
        self._strategies = strategies

    async def _get_fresh_price_with_fallback(
        self, symbol: str, user_id: str, user_role: str
    ) -> tuple[Decimal | None, str]:
        """Get fresh price from cache, with on-demand quote fallback for non-subscribed symbols.

        Returns: (price, error_message) - price is None if unavailable/stale
        """
        # First try cached price (from OrderEntryContext)
        cached_prices = self._context.cached_prices
        if cached_prices and symbol in cached_prices:
            price, timestamp = cached_prices[symbol]
            age = (datetime.now(UTC) - timestamp).total_seconds()
            if age <= PRICE_STALENESS_THRESHOLD_S:
                return price, ""

        # Fallback: On-demand price fetch for non-subscribed symbols
        # NOTE: fetch_quote doesn't exist - use fetch_market_prices and filter
        # IMPORTANT: Use 'mid' field (consistent with existing manual_order.py usage)
        try:
            prices = await self._client.fetch_market_prices(
                user_id=user_id,
                role=user_role,
                strategies=self._strategies,
            )
            # Find the symbol in the prices list
            symbol_price = next(
                (p for p in prices if p.get("symbol", "").upper() == symbol.upper()),
                None
            )
            # Use 'mid' field (consistent with existing code in manual_order.py)
            if symbol_price and symbol_price.get("mid"):
                # CRITICAL: Validate price is positive and finite (FAIL-CLOSED safety)
                try:
                    price_val = Decimal(str(symbol_price["mid"]))
                    if price_val <= 0 or not price_val.is_finite():
                        return None, f"Invalid price value: {symbol_price['mid']}"
                except (InvalidOperation, ValueError):
                    return None, f"Unparseable price: {symbol_price['mid']}"

                # CRITICAL: Require valid timestamp for FAIL-CLOSED actions (no timestamp = stale)
                # NOTE: Verify fetch_market_prices API includes 'timestamp' field per-symbol.
                #       If missing from API schema:
                #       Option A: Add 'timestamp' field to API response (recommended)
                #       Option B: Use server-provided response timestamp header if available
                #       Option C: Use 'updated_at' or similar field if present
                #       For now, FAIL-CLOSED blocks if timestamp unavailable (safest default)
                raw_ts = symbol_price.get("timestamp") or symbol_price.get("updated_at")
                if not raw_ts:
                    # IMPLEMENTATION NOTE: If API doesn't provide per-symbol timestamps,
                    # either extend API to include them OR use a request-level staleness approach:
                    # - Add response_timestamp header to fetch_market_prices
                    # - Check if (now - response_timestamp) < STALENESS_THRESHOLD
                    return None, "Price data missing timestamp (cannot verify freshness - API may need schema update)"

                from apps.web_console_ng.utils.time import parse_iso_timestamp
                ts = parse_iso_timestamp(str(raw_ts)) if isinstance(raw_ts, str) else raw_ts
                if not ts or not isinstance(ts, datetime):
                    return None, "Price timestamp invalid (cannot verify freshness)"

                age = (datetime.now(UTC) - ts).total_seconds()
                if age > PRICE_STALENESS_THRESHOLD_S:
                    return None, f"Price data stale ({age:.0f}s old)"

                return price_val, ""
            return None, "No price data available for symbol"
        except Exception as exc:
            logger.warning("reverse_price_fetch_failed", extra={"symbol": symbol, "error": str(exc)})
            return None, f"Price fetch failed: {exc}"

    async def _get_adv(self, symbol: str, user_id: str, user_role: str) -> int | None:
        """Get Average Daily Volume for fat finger validation.

        Returns None if unavailable (validator handles None gracefully).
        """
        try:
            # CRITICAL: fetch_adv requires user_id, role, strategies
            adv_data = await self._client.fetch_adv(
                symbol=symbol,
                user_id=user_id,
                role=user_role,
                strategies=self._strategies,
            )
            return adv_data.get("adv") if adv_data else None
        except Exception:
            return None  # ADV is optional, fat finger still validates qty/notional

    async def on_flatten_symbol(
        self, symbol: str, qty: int, user_id: str, user_role: str,
    ) -> None:
        """Flatten single symbol position (close + cancel all pending orders).

        Safety: FAIL-OPEN (risk-reducing action)

        IMPORTANT: This MUST do BOTH:
        1. Close position (exit current exposure)
        2. Cancel all orders for symbol (wipe the book to prevent re-entry)
        """
        # 0. Role check (viewers cannot trade - consistent with on_close_position)
        if user_role == "viewer":
            ui.notify("Viewers cannot flatten positions", type="warning")
            return

        # 0b. Qty validation (same safeguards as positions_grid.on_close_position)
        # NOTE: qty can be negative for short positions - use abs() for actual operations
        # CRITICAL: Reject fractional quantities (backend requires integers)
        try:
            qty_float = float(qty)
            if not math.isfinite(qty_float) or qty_float == 0:
                ui.notify("Invalid position quantity", type="negative")
                return
            if qty_float != int(qty_float):
                ui.notify("Position quantity must be an integer", type="negative")
                return
            qty = int(qty_float)  # Normalize to int for downstream use
        except (ValueError, TypeError):
            ui.notify("Invalid position quantity", type="negative")
            return

        # 1. Pre-check with cached state (instant response)
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=self._context.cached_kill_switch_engaged,
            cached_connection_state=self._context.cached_connection_state,
            cached_circuit_breaker=self._context.cached_circuit_breaker_tripped,
        )
        if not result.allowed:
            ui.notify(f"Cannot flatten: {result.reason}", type="negative")
            return

        # Show warnings if any
        for warning in result.warnings:
            ui.notify(warning, type="warning")

        # 2. Show confirmation dialog
        # 3. At confirm: Fresh API check, then execute two-step flatten:

        async def execute_flatten() -> None:
            """Execute flatten: close position + cancel all orders."""
            # Fresh safety check at confirm time
            confirm_result = await self._safety.check_with_api_verification(
                policy=SafetyPolicy.FAIL_OPEN
            )
            if not confirm_result.allowed:
                ui.notify(f"Flatten blocked: {confirm_result.reason}", type="negative")
                return

            # Step 1: Cancel working orders for symbol (strategy-scoped to avoid cross-strategy cancels)
            # Use per-order cancel on fetched list (same as global cancel-all rationale)
            # NOTE: Detect and warn about uncancellable orders (can re-enter after flatten)
            try:
                response = await self._client.fetch_open_orders(
                    user_id, role=user_role, strategies=self._strategies
                )
                all_symbol_orders = [
                    o for o in response.get("orders", [])
                    if o.get("symbol", "").upper() == symbol.upper()
                ]

                # Detect uncancellable orders (missing/synthetic/fallback IDs)
                uncancellable = [
                    o for o in all_symbol_orders
                    if not o.get("client_order_id")
                    or o.get("client_order_id", "").startswith(SYNTHETIC_ID_PREFIX)
                    or o.get("client_order_id", "").startswith(FALLBACK_ID_PREFIX)
                ]
                if uncancellable:
                    # Warn user about residual orders (fail-open, but alert)
                    ui.notify(
                        f"Warning: {len(uncancellable)} order(s) cannot be cancelled (invalid IDs) - may re-open after flatten",
                        type="warning"
                    )
                    logger.warning(
                        "flatten_uncancellable_orders",
                        extra={"symbol": symbol, "count": len(uncancellable)}
                    )

                # Cancel orders with valid IDs
                symbol_orders = [
                    o for o in all_symbol_orders
                    if o.get("client_order_id")
                    and not o.get("client_order_id", "").startswith(SYNTHETIC_ID_PREFIX)
                    and not o.get("client_order_id", "").startswith(FALLBACK_ID_PREFIX)
                ]
                cancelled = 0
                failed = 0
                for order in symbol_orders:
                    try:
                        await self._client.cancel_order(
                            order["client_order_id"], user_id, role=user_role,
                            strategies=self._strategies,  # Required for auth scope
                            reason="Flatten symbol - pre-close cancel",
                            requested_by=user_id,
                            requested_at=datetime.now(UTC).isoformat(),
                        )
                        cancelled += 1
                    except Exception:
                        failed += 1  # Track failures but continue (fail-open)
                # Notify user of results (including failures so they know to check)
                if failed > 0:
                    ui.notify(
                        f"Cancelled {cancelled}, failed {failed} order(s) for {symbol} - verify no residual orders",
                        type="warning"
                    )
                elif cancelled > 0:
                    ui.notify(f"Cancelled {cancelled} order(s) for {symbol}", type="info")
            except Exception as exc:
                logger.warning("flatten_cancel_orders_failed", extra={"symbol": symbol, "error": str(exc)})
                # Continue with close - this is fail-open for risk reduction

            # Step 2: Close position (exit exposure)
            try:
                await self._client.close_position(
                    symbol=symbol,
                    qty=abs(qty),
                    reason="Flatten symbol",
                    requested_by=user_id,
                    requested_at=datetime.now(UTC).isoformat(),
                    user_id=user_id,
                    role=user_role,
                    strategies=self._strategies,
                )
                ui.notify(f"Closed {symbol} position", type="positive")
            except Exception as exc:
                ui.notify(f"Close failed: {exc}", type="negative")

    async def on_reverse_position(
        self, symbol: str, qty: int, current_side: str, user_id: str, user_role: str,
    ) -> None:
        """Reverse position (close + open opposite).

        Safety: FAIL-CLOSED (risk-increasing action - opens new exposure)

        Args:
            qty: Position quantity (always positive integer from positions grid)
            current_side: Derived from position qty sign in caller:
                - Positive position qty (long) -> current_side="buy"
                - Negative position qty (short) -> current_side="sell"

        Strategy: Two-step execution
        0. Cancel all existing working orders for symbol (prevent re-entry during reverse)
        1. Close current position (await fill or timeout)
        2. Submit opposite order only after close succeeds

        This prevents double exposure from simultaneous orders.
        """
        # 0. Role check (viewers cannot trade)
        if user_role == "viewer":
            ui.notify("Viewers cannot reverse positions", type="warning")
            return

        # 0b. Qty validation (same as positions_grid.on_close_position)
        # NOTE: qty can be negative for short positions - use abs() for actual operations
        # CRITICAL: Reject fractional quantities (backend requires integers)
        try:
            qty_float = float(qty)
            if not math.isfinite(qty_float) or qty_float == 0:
                ui.notify("Invalid position quantity", type="negative")
                return
            if qty_float != int(qty_float):
                ui.notify("Position quantity must be an integer", type="negative")
                return
            qty = int(qty_float)  # Normalize to int for downstream use
        except (ValueError, TypeError):
            ui.notify("Invalid position quantity", type="negative")
            return

        # 1. Pre-check with FAIL-CLOSED policy (risk-increasing!)
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_CLOSED,  # Critical: fail-closed
            cached_kill_switch=self._context.cached_kill_switch_engaged,
            cached_connection_state=self._context.cached_connection_state,
            cached_circuit_breaker=self._context.cached_circuit_breaker_tripped,
            require_connected=True,
        )
        if not result.allowed:
            ui.notify(f"Cannot reverse: {result.reason}", type="negative")
            return

        # 1b. PRE-CHECK: Count open orders for dialog info (don't cancel yet - wait for confirm)
        # Actual cancellation happens in execute_reverse() after user confirms
        open_order_count = 0
        has_uncancellable = False
        try:
            response = await self._client.fetch_open_orders(
                user_id, role=user_role, strategies=self._strategies
            )
            all_symbol_orders = [
                o for o in response.get("orders", [])
                if o.get("symbol", "").upper() == symbol.upper()
            ]

            # Check for uncancellable orders (missing/synthetic/fallback IDs)
            uncancellable_orders = [
                o for o in all_symbol_orders
                if not o.get("client_order_id")
                or o.get("client_order_id", "").startswith(SYNTHETIC_ID_PREFIX)
                or o.get("client_order_id", "").startswith(FALLBACK_ID_PREFIX)
            ]
            if uncancellable_orders:
                has_uncancellable = True
                ui.notify(
                    f"Cannot reverse: {len(uncancellable_orders)} open order(s) have uncancellable IDs",
                    type="negative"
                )
                return

            # Count cancellable orders for dialog message
            cancellable_orders = [
                o for o in all_symbol_orders
                if o.get("client_order_id")
                and not o.get("client_order_id", "").startswith(SYNTHETIC_ID_PREFIX)
                and not o.get("client_order_id", "").startswith(FALLBACK_ID_PREFIX)
            ]
            open_order_count = len(cancellable_orders)
        except Exception as fetch_exc:
            # FAIL-CLOSED: Block if we can't verify open orders
            ui.notify(f"Cannot reverse: failed to check open orders ({fetch_exc})", type="negative")
            return

        # 2. Fetch fresh price (may need on-demand quote for non-subscribed symbols)
        opposite_side = "sell" if current_side == "buy" else "buy"
        price, error = await self._get_fresh_price_with_fallback(symbol, user_id, user_role)
        if price is None:
            ui.notify(f"Cannot reverse: {error}", type="negative")
            return

        # 3. Get ADV for fat finger validation (required for % of ADV check)
        adv = await self._get_adv(symbol, user_id, user_role)

        # 4. Fat finger validation for new opposite position (with ADV for full safety)
        validation = self._validator.validate(symbol=symbol, qty=abs(qty), price=price, adv=adv)
        if validation.blocked:
            ui.notify(f"Reverse blocked: {validation.warnings[0].message}", type="negative")
            return

        # 3. Show dialog with explicit two-step warning (include open order count if any):
        # "Reverse {symbol}? Will cancel {open_order_count} open orders, close {qty} {current_side} THEN open {qty} {opposite_side}"
        # "Note: Opposite order will only submit after close is confirmed"
        # If open_order_count > 0, include warning about order cancellation

        # 4. At confirm: Fresh API check with FAIL-CLOSED, then execute two-step reverse:
        # NOTE: Dialog implementation MUST include double-submit protection (submitting flag + button disable)

        # Double-submit protection state (shared with dialog)
        reverse_submitting = False

        async def execute_reverse() -> None:
            """Execute two-step reverse with close-confirmation strategy.

            Close-Confirmation Strategy:
            1. Submit close order and await API response (order accepted)
            2. Poll position endpoint until position is zero OR timeout (30s)
            3. Only submit opposite order after confirmed flat
            4. Abort and notify user if timeout or error
            """
            nonlocal reverse_submitting
            if reverse_submitting:
                return  # Prevent double-submit
            reverse_submitting = True

            # CRITICAL: Entire function body wrapped in try/finally to ensure reset on ANY exit path
            try:
                # Constants for polling
                CLOSE_CONFIRMATION_TIMEOUT_S = 30
                POLL_INTERVAL_S = 1.0

                # Fresh safety check at confirm time
                confirm_result = await self._safety.check_with_api_verification(
                    policy=SafetyPolicy.FAIL_CLOSED,
                    cached_connection_state=self._context.cached_connection_state,
                )
                if not confirm_result.allowed:
                    ui.notify(f"Reverse blocked: {confirm_result.reason}", type="negative")
                    return

            # Step 0: ALWAYS check/cancel open orders (not just when pre-check found some)
            # CRITICAL: Orders may appear after pre-check dialog; must re-verify at confirm time
            # FAIL-CLOSED: Block on any uncancellable orders (missing/synthetic/fallback IDs)
            try:
                response = await self._client.fetch_open_orders(
                    user_id, role=user_role, strategies=self._strategies
                )
                all_symbol_orders = [
                    o for o in response.get("orders", [])
                    if o.get("symbol", "").upper() == symbol.upper()
                ]

                # FAIL-CLOSED: Block if any uncancellable orders exist
                uncancellable = [
                    o for o in all_symbol_orders
                    if not o.get("client_order_id")
                    or o.get("client_order_id", "").startswith(SYNTHETIC_ID_PREFIX)
                    or o.get("client_order_id", "").startswith(FALLBACK_ID_PREFIX)
                ]
                if uncancellable:
                    ui.notify(
                        f"Reverse blocked: {len(uncancellable)} order(s) have uncancellable IDs",
                        type="negative"
                    )
                    return

                # Get cancellable orders
                symbol_orders = [
                    o for o in all_symbol_orders
                    if o.get("client_order_id")
                    and not o.get("client_order_id", "").startswith(SYNTHETIC_ID_PREFIX)
                    and not o.get("client_order_id", "").startswith(FALLBACK_ID_PREFIX)
                ]

                # Cancel all open orders for symbol
                if symbol_orders:
                    cancel_failures = []
                    for order in symbol_orders:
                        try:
                            await self._client.cancel_order(
                                order["client_order_id"], user_id, role=user_role,
                                strategies=self._strategies,  # Required for auth scope
                                reason="Reverse position - pre-cancel",
                                requested_by=user_id,
                                requested_at=datetime.now(UTC).isoformat(),
                            )
                        except Exception as cancel_exc:
                            cancel_failures.append(order.get("client_order_id"))
                            logger.warning(
                                "reverse_cancel_failed",
                                extra={"symbol": symbol, "order_id": order.get("client_order_id"), "error": str(cancel_exc)}
                            )

                    # FAIL-CLOSED: Block if any cancel failed
                    if cancel_failures:
                        ui.notify(
                            f"Reverse blocked: failed to cancel {len(cancel_failures)} order(s)",
                            type="negative"
                        )
                        return

                    # Re-verify zero open orders (including uncancellable)
                    recheck = await self._client.fetch_open_orders(
                        user_id, role=user_role, strategies=self._strategies
                    )
                    remaining = [
                        o for o in recheck.get("orders", [])
                        if o.get("symbol", "").upper() == symbol.upper()
                    ]
                    if remaining:
                        ui.notify(
                            f"Reverse blocked: {len(remaining)} order(s) still pending",
                            type="negative"
                        )
                        return

                    ui.notify(f"Cancelled {len(symbol_orders)} order(s) for {symbol}", type="info")
            except Exception as fetch_exc:
                ui.notify(f"Reverse blocked: failed to check orders ({fetch_exc})", type="negative")
                return

            # Step 1: Submit close order
            try:
                close_result = await self._client.close_position(
                    symbol=symbol,
                    qty=abs(qty),
                    reason="Reverse position - close leg",
                    requested_by=user_id,
                    requested_at=datetime.now(UTC).isoformat(),
                    user_id=user_id,
                    role=user_role,
                    strategies=self._strategies,
                )
                ui.notify(f"Close submitted for {symbol}, awaiting fill...", type="info")
            except Exception as exc:
                ui.notify(f"Close failed: {exc}", type="negative")
                return

            # Step 2: Poll position until flat or timeout
            start_time = time.time()
            is_flat = False
            consecutive_errors = 0
            MAX_CONSECUTIVE_ERRORS = 3  # Abort if 3+ consecutive poll failures
            while time.time() - start_time < CLOSE_CONFIRMATION_TIMEOUT_S:
                await asyncio.sleep(POLL_INTERVAL_S)
                try:
                    # IMPORTANT: Pass strategies for proper scoping (matches auth header)
                    positions = await self._client.fetch_positions(
                        user_id, role=user_role, strategies=self._strategies
                    )
                    pos_list = positions.get("positions", [])
                    symbol_pos = next((p for p in pos_list if p.get("symbol") == symbol), None)
                    # Use float() for robust parsing (handles "0.0", negative shorts, etc.)
                    qty_val = float(symbol_pos.get("qty", 0)) if symbol_pos else 0.0
                    if symbol_pos is None or qty_val == 0.0:
                        is_flat = True
                        break
                    consecutive_errors = 0  # Reset on successful poll
                except Exception as poll_exc:
                    consecutive_errors += 1
                    logger.warning(
                        "reverse_poll_error",
                        extra={"symbol": symbol, "error": str(poll_exc), "consecutive": consecutive_errors}
                    )
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        ui.notify(f"Reverse aborted: position polling failed ({poll_exc})", type="negative")
                        return

            if not is_flat:
                ui.notify(f"Reverse aborted: {symbol} position not closed within {CLOSE_CONFIRMATION_TIMEOUT_S}s", type="negative")
                logger.warning(
                    "reverse_close_timeout",
                    extra={"symbol": symbol, "user_id": user_id, "timeout_s": CLOSE_CONFIRMATION_TIMEOUT_S}
                )
                return

            # Step 3: Final safety re-check before opening opposite position
            # CRITICAL: State could have changed during close/polling - must re-verify
            final_check = await self._safety.check_with_api_verification(
                policy=SafetyPolicy.FAIL_CLOSED,
                cached_connection_state=self._context.cached_connection_state,
            )
            if not final_check.allowed:
                ui.notify(
                    f"Reverse aborted before open: {final_check.reason} (position is now flat)",
                    type="negative"
                )
                logger.warning(
                    "reverse_open_blocked_after_close",
                    extra={"symbol": symbol, "reason": final_check.reason}
                )
                return

            # Step 3b: CRITICAL - Re-validate price freshness before open leg
            # Price used in pre-check may have gone stale during close/poll (up to 30s)
            # FAIL-CLOSED: Block if fresh price unavailable or exceeds fat-finger limits
            try:
                # Reuse existing _get_fresh_price_with_fallback (includes staleness check)
                fresh_price, price_error = await self._get_fresh_price_with_fallback(
                    symbol, user_id, user_role
                )
                if fresh_price is None:
                    ui.notify(
                        f"Reverse aborted: {price_error} (position is now flat)",
                        type="negative"
                    )
                    return

                # Re-run fat-finger validation with fresh price
                notional = Decimal(abs(qty)) * fresh_price
                # CRITICAL: _get_adv requires user_id and user_role (see definition at line ~648)
                adv = await self._get_adv(symbol, user_id, user_role) if self._validator else None
                validation_result = self._validator.validate(
                    symbol=symbol,
                    qty=abs(qty),
                    price=fresh_price,
                    notional=notional,
                    adv=adv,
                ) if self._validator else None

                if validation_result and not validation_result.allowed:
                    ui.notify(
                        f"Reverse aborted: fat-finger check failed ({validation_result.reason}) - position is now flat",
                        type="negative"
                    )
                    logger.warning("reverse_open_fat_finger_failed", extra={
                        "symbol": symbol,
                        "qty": abs(qty),
                        "fresh_price": str(fresh_price),
                        "reason": validation_result.reason,
                    })
                    return
            except Exception as price_exc:
                ui.notify(
                    f"Reverse aborted: price re-validation failed ({price_exc}) - position is now flat",
                    type="negative"
                )
                return

            # Step 4: Submit opposite order only after confirmed flat, safety re-verified, AND price re-validated
            # CRITICAL: Use abs(qty) - ManualOrderRequest.qty must be positive
            # CRITICAL: submit_manual_order takes order_data dict, NOT kwargs
            try:
                order_data = {
                    "symbol": symbol,
                    "side": opposite_side,
                    "qty": abs(qty),  # Must be positive - side determines direction
                    "order_type": "market",
                    "reason": "Reverse position - open leg",
                    "requested_by": user_id,
                    "requested_at": datetime.now(UTC).isoformat(),
                }
                await self._client.submit_manual_order(
                    order_data=order_data,
                    user_id=user_id,
                    role=user_role,
                    strategies=self._strategies,
                )
                ui.notify(f"Reversed {symbol}: now {opposite_side} {abs(qty)}", type="positive")
            except Exception as exc:
                ui.notify(f"Opposite order failed: {exc} (position is now flat)", type="negative")
            # NOTE: This inner finally only covers the submit_manual_order call
            # The OUTER finally (below) covers the entire function body
            finally:
                # CRITICAL: Reset double-submit guard on ALL exit paths
                # This finally belongs to the outer try block started after reverse_submitting = True
                reverse_submitting = False
```

**IMPLEMENTATION NOTE: Double-Submit Protection Structure**

The `execute_reverse()` function above shows the pattern but the markdown indentation may not perfectly reflect Python indentation. The critical requirement is:

```python
reverse_submitting = True
try:
    # ALL code paths (safety checks, order fetches, cancels, close, poll, open)
    # must be inside this try block
    ...
finally:
    reverse_submitting = False  # Reset on ANY exit (return, exception, or normal completion)
```

All early `return` statements must be inside the outer try block so the finally always executes.

**Component 2: `emergency_actions.py` - Header Emergency Controls**

```python
# New file: apps/web_console_ng/components/emergency_actions.py

class EmergencyActionsHeader:
    """FLATTEN ALL and CANCEL ALL ORDERS header buttons."""

    SNAPSHOT_STALENESS_THRESHOLD_S = 60  # Warn if positions snapshot older than 60s

    def __init__(
        self,
        safety_gate: SafetyGate,
        trading_client: AsyncTradingClient,
        order_entry_context: OrderEntryContext,
        strategies: list[str] | None = None,  # Required for fetch_open_orders scoping
        positions_snapshot: list[dict] | None = None,  # Pass from dashboard for confirmation counts
    ):
        self._safety = safety_gate
        self._client = trading_client
        self._context = order_entry_context
        self._strategies = strategies  # Used in fetch_open_orders for consistent scoping
        self._positions_snapshot = positions_snapshot or []
        self._snapshot_timestamp: datetime | None = None

    def update_positions_snapshot(self, positions: list[dict]) -> None:
        """Update positions snapshot (called from dashboard on position updates)."""
        self._positions_snapshot = positions
        self._snapshot_timestamp = datetime.now(UTC)

    def _is_snapshot_stale(self) -> bool:
        """Check if positions snapshot is too old for reliable counts."""
        if self._snapshot_timestamp is None:
            return True
        age = (datetime.now(UTC) - self._snapshot_timestamp).total_seconds()
        return age > self.SNAPSHOT_STALENESS_THRESHOLD_S

    def _get_positions_summary(self) -> tuple[int, Decimal]:
        """Get position count and total market value from snapshot."""
        count = len(self._positions_snapshot)
        total_value = sum(
            abs(Decimal(str(p.get("market_value", 0))))
            for p in self._positions_snapshot
        )
        return count, total_value

    async def on_flatten_all(self, user_id: str, user_role: str) -> None:
        """Flatten all positions (requires MFA).

        Safety: FAIL-OPEN (risk-reducing action)
        MFA: Required via id_token from app.storage.user
        """
        # 0. Role check (viewers cannot trade)
        if user_role == "viewer":
            ui.notify("Viewers cannot flatten positions", type="warning")
            return

        # 1. Get MFA token (existing pattern from position_management.py)
        id_token = app.storage.user.get("id_token")
        if not id_token:
            ui.notify("MFA required - please re-authenticate", type="warning")
            # Trigger re-auth flow
            return

        # 2. Pre-check with cached state (including circuit breaker for operator awareness)
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=self._context.cached_kill_switch_engaged,
            cached_connection_state=self._context.cached_connection_state,
            cached_circuit_breaker=self._context.cached_circuit_breaker_tripped,  # Include CB for warning
        )
        if not result.allowed:
            ui.notify(f"Cannot flatten: {result.reason}", type="negative")
            return

        # 3. Get positions summary from snapshot (passed from dashboard)
        pos_count, pos_value = self._get_positions_summary()
        if pos_count == 0:
            ui.notify("No positions to flatten", type="info")
            return

        # 3b. Show safety warnings (always, not just when stale)
        for warning in result.warnings:
            ui.notify(warning, type="warning")

        # 3c. Additional stale snapshot warning
        if self._is_snapshot_stale():
            ui.notify("Position counts may be outdated - verify before confirming", type="warning")

        # 4. Two-step confirmation dialog:
        #    Step 1: "Flatten ALL positions? This will close {pos_count} positions worth ${pos_value:,.0f}"
        #    Step 2: Type "FLATTEN" to confirm

        # 5. At confirm: Fresh API check, then call flatten_all_positions with ALL required args
        # NOTE: Dialog implementation MUST include double-submit protection (submitting flag + button disable)
        # Pattern: submitting = False; if submitting: return; submitting = True; btn.disable(); try: ... finally: submitting = False; btn.enable()

        # Double-submit protection state (shared with dialog)
        flatten_all_submitting = False

        async def execute_flatten_all() -> None:
            """Execute flatten all positions.

            CRITICAL SAFETY: Cancel all open orders FIRST, then close positions.
            This prevents outstanding orders from re-opening exposure after flatten.
            """
            nonlocal flatten_all_submitting
            if flatten_all_submitting:
                return  # Prevent double-submit
            flatten_all_submitting = True

            try:
                # Fresh safety check at confirm time
            confirm_result = await self._safety.check_with_api_verification(
                policy=SafetyPolicy.FAIL_OPEN
            )
            if not confirm_result.allowed:
                ui.notify(f"Flatten blocked: {confirm_result.reason}", type="negative")
                return

            # PHASE 1: Cancel all open orders to prevent re-entry
            # Fetch orders, group by symbol, cancel all (same logic as on_cancel_all_orders_global)
            cancelled_orders = 0
            cancel_errors = 0
            try:
                # CRITICAL: Use self._strategies for consistent scope (not None)
                # Using None can 403 for non-admins or return out-of-scope orders
                response = await self._client.fetch_open_orders(
                    user_id, role=user_role, strategies=self._strategies
                )
                open_orders = response.get("orders", [])

                # Group by symbol for efficient bulk cancel
                orders_by_symbol: dict[str, list[dict]] = {}
                for order in open_orders:
                    sym = order.get("symbol")
                    if sym:
                        orders_by_symbol.setdefault(sym, []).append(order)

                # Cancel per-symbol (uses bulk cancel_all_orders endpoint)
                for sym, orders in orders_by_symbol.items():
                    try:
                        cancel_result = await self._client.cancel_all_orders(
                            symbol=sym,
                            reason="Pre-flatten cancel: emergency flatten all",
                            requested_by=user_id,
                            requested_at=datetime.now(UTC).isoformat(),
                            user_id=user_id,
                            role=user_role,
                            strategies=self._strategies,
                        )
                        cancelled_orders += cancel_result.get("cancelled_count", 0)
                    except Exception as cancel_exc:
                        cancel_errors += 1
                        logger.warning("flatten_all_cancel_failed", extra={
                            "symbol": sym,
                            "error": str(cancel_exc),
                        })

                if cancelled_orders > 0:
                    ui.notify(f"Cancelled {cancelled_orders} orders before flatten", type="info")
                if cancel_errors > 0:
                    ui.notify(f"Warning: {cancel_errors} symbol(s) had cancel errors", type="warning")

            except Exception as fetch_exc:
                # FAIL-OPEN: Warn but proceed with flatten (risk-reducing)
                ui.notify(f"Warning: Could not cancel orders first: {fetch_exc}", type="warning")
                logger.warning("flatten_all_order_fetch_failed", extra={"error": str(fetch_exc)})

            # PHASE 2: Close all positions
            try:
                # CRITICAL: flatten_all_positions requires ALL these args
                result = await self._client.flatten_all_positions(
                    reason="Emergency flatten all positions",  # min 20 chars
                    requested_by=user_id,
                    requested_at=datetime.now(UTC).isoformat(),
                    id_token=id_token,
                    user_id=user_id,
                    role=user_role,
                    strategies=self._strategies,
                )
                positions_closed = result.get("positions_closed", 0)
                # NOTE: orders_created is a list of order IDs, not an int (per FlattenAllResponse schema)
                orders_created_list = result.get("orders_created", [])
                orders_created_count = len(orders_created_list) if isinstance(orders_created_list, list) else 0
                ui.notify(
                    f"Flattened {positions_closed} positions ({orders_created_count} orders created)",
                    type="positive"
                )
            except Exception as exc:
                ui.notify(f"Flatten all failed: {exc}", type="negative")
            # NOTE: This inner finally only covers the flatten_all_positions call
            # The OUTER finally (below) covers the entire function body
            finally:
                # CRITICAL: Reset double-submit guard on ALL exit paths
                # This finally belongs to the outer try block started after flatten_all_submitting = True
                flatten_all_submitting = False

**IMPLEMENTATION NOTE: Double-Submit Protection Structure for execute_flatten_all**

The `execute_flatten_all()` function above shows the pattern but the markdown indentation may not perfectly reflect Python indentation. The critical requirement is:

```python
flatten_all_submitting = True
try:
    # ALL code paths (safety check, order cancels, flatten_all_positions)
    # must be inside this try block
    ...
finally:
    flatten_all_submitting = False  # Reset on ANY exit (return, exception, or normal completion)
```

All early `return` statements must be inside the outer try block so the finally always executes.

    async def on_cancel_all_orders_global(self, user_id: str, user_role: str) -> None:
        """Cancel ALL open orders (no safety gate - always allowed for authorized users).

        Note: Viewers are blocked (consistent with on_cancel_order in orders_table.py).
        Read-only mode does NOT block cancel (cancel is risk-reducing, fail-open).
        """
        # Permission check (consistent with on_cancel_order)
        if user_role == "viewer":
            ui.notify("Viewers cannot cancel orders", type="warning")
            return

        # 1. Show dialog: "Cancel all X open orders?"
        # 2. At confirm: Fetch unique symbols from open orders, call cancel_all_orders() for each

        async def execute_cancel_all_global() -> None:
            """Execute global cancel all orders.

            CRITICAL: Uses per-order cancel on the fetched list (not cancel_all_orders per symbol)
            because per-order cancel ensures we only cancel orders within the
            UI's strategy scope (and skip invalid IDs) for multi-strategy users.
            """
            # Fetch open orders (scoped by strategies)
            # CRITICAL: Include strategies for consistent scoping
            response = await self._client.fetch_open_orders(
                user_id, role=user_role, strategies=self._strategies
            )
            orders = response.get("orders", [])

            # Separate valid from skipped orders (missing/synthetic/fallback IDs)
            valid_orders = []
            skipped_orders = []
            for o in orders:
                order_id = o.get("client_order_id")
                if not order_id:
                    skipped_orders.append(o)
                elif order_id.startswith(SYNTHETIC_ID_PREFIX) or order_id.startswith(FALLBACK_ID_PREFIX):
                    skipped_orders.append(o)
                else:
                    valid_orders.append(o)

            # Warn about skipped orders (residual may remain)
            if skipped_orders:
                ui.notify(
                    f"Warning: {len(skipped_orders)} order(s) cannot be cancelled (invalid IDs)",
                    type="warning"
                )

            if not valid_orders:
                ui.notify("No cancellable orders found", type="info")
                return

            # Cancel per order with bounded concurrency (same as T7.2 pattern)
            # CRITICAL: Using per-order cancel ensures we only cancel orders that were
            # fetched with strategies filter, not all orders on the symbol
            MAX_CONCURRENT_CANCELS = 5
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_CANCELS)
            results: list[bool] = []

            async def cancel_one(order: dict) -> bool:
                async with semaphore:
                    try:
                        await self._client.cancel_order(
                            order["client_order_id"], user_id, role=user_role,
                            strategies=self._strategies,  # Required for auth scope
                            reason="Emergency cancel all orders",
                            requested_by=user_id,
                            requested_at=datetime.now(UTC).isoformat(),
                        )
                        return True
                    except Exception:
                        return False

            results = await asyncio.gather(*[cancel_one(o) for o in valid_orders])
            cancelled_count = sum(results)
            failed_count = len(results) - cancelled_count

            if failed_count > 0:
                ui.notify(f"Cancelled {cancelled_count}, failed {failed_count} orders", type="warning")
            else:
                ui.notify(f"Cancelled {cancelled_count} orders", type="positive")

            # Audit log entry for bulk cancellation (acceptance criteria requirement)
            logger.info(
                "cancel_all_global_executed",
                extra={
                    "reason": "Emergency cancel all orders",
                    "requested_by": user_id,
                    "requested_at": datetime.now(UTC).isoformat(),
                    "cancelled_count": cancelled_count,
                    "failed_count": failed_count,
                    "strategy_id": "manual_controls_cancel_all",
                }
            )
```

**UI Integration:**
- Add buttons to `positions_grid.py` actions column: [Close] [Flatten] [Reverse]
- Add header row above positions grid with: [FLATTEN ALL] [CANCEL ALL ORDERS]

**Files to Create:**
- `apps/web_console_ng/components/safety_gate.py`
- `apps/web_console_ng/components/flatten_controls.py`
- `apps/web_console_ng/components/emergency_actions.py`

**Files to Modify:**
- `apps/web_console_ng/components/order_entry_context.py` (add safety state fields + getters + update callbacks)
- `apps/web_console_ng/components/positions_grid.py` (add action buttons)
- `apps/web_console_ng/ui/trading_layout.py` (add emergency header)
- `apps/web_console_ng/static/js/aggrid_renderers.js` (add Flatten/Reverse button renderers + CustomEvent dispatch)
- `apps/web_console_ng/pages/dashboard.py` (bind new CustomEvents, pass positions snapshot to EmergencyActionsHeader)

**Acceptance Criteria:**
- [ ] Flatten buttons on position rows (Close, Flatten Symbol, Reverse)
- [ ] **Reverse uses FAIL-CLOSED policy** (blocks on any safety uncertainty)
- [ ] **Reverse uses two-step execution** (close then opposite, not simultaneous)
- [ ] **Reverse checks price staleness** (blocks if price > 30s old)
- [ ] **Reverse handles poll errors** (aborts after 3 consecutive failures)
- [ ] Header flatten all with two-step confirmation (type "FLATTEN")
- [ ] **MFA required for flatten-all via id_token from app.storage.user**
- [ ] **Flatten-all warns on stale positions snapshot** (> 60s old)
- [ ] Audit log entry for each flatten action (via backend API - reason/requested_by/requested_at)
- [ ] **Gated by:** Kill switch state (block if halted)
- [ ] **Gated by:** Connection state (block if disconnected for Reverse)
- [ ] **Respects fat finger limits for Reverse** (new exposure is risk-increasing)

---

### T7.2: Cancel All Orders Button - MEDIUM PRIORITY

**Goal:** One-click cancel all with filtering.

#### Implementation Plan

**Note:** Backend `/api/v1/orders/cancel-all` is per-symbol only and does not support side filtering. Side filtering requires per-order cancellation.

**Component: `cancel_all_dialog.py`**

```python
# New file: apps/web_console_ng/components/cancel_all_dialog.py

from apps.web_console_ng.core.synthetic_id import SYNTHETIC_ID_PREFIX, FALLBACK_ID_PREFIX

class CancelAllDialog:
    """Cancel all orders dialog with filtering.

    Note: Side filtering uses per-order cancel (backend cancel-all is per-symbol only).

    Safety:
    - Viewer role blocked (consistent with on_cancel_order)
    - Uses bounded concurrency for large order counts
    """

    # Bounded concurrency to avoid overwhelming backend/network
    MAX_CONCURRENT_CANCELS = 5

    def __init__(
        self,
        orders: list[dict],
        trading_client: AsyncTradingClient,
        user_id: str,
        user_role: str,
        is_read_only: bool = False,  # From connection state
        strategies: list[str] | None = None,  # Required for confirm-time refresh scoping
    ):
        self._orders = orders
        self._client = trading_client
        self._user_id = user_id
        self._user_role = user_role
        self._is_read_only = is_read_only
        self._strategies = strategies  # For fetch_open_orders scoping at confirm time

    def _check_permissions(self) -> tuple[bool, str]:
        """Check if user can cancel orders. Returns (allowed, reason).

        Note: Read-only mode does NOT block cancel operations.
        Cancel is FAIL-OPEN (risk-reducing) - allowed even during degraded connections.
        Only viewer role is blocked (consistent with on_cancel_order policy).
        """
        if self._user_role == "viewer":
            return False, "Viewers cannot cancel orders"
        # NOTE: is_read_only intentionally NOT checked here
        # Cancel is risk-reducing and should be allowed during connection issues
        return True, ""

    def _filter_orders(
        self, symbol_filter: str, side_filter: str
    ) -> tuple[list[dict], list[dict]]:
        """Filter orders and separate valid from invalid IDs.

        Returns: (valid_orders, skipped_orders)
        """
        filtered = self._orders

        if symbol_filter != "All Symbols":
            filtered = [o for o in filtered if o.get("symbol") == symbol_filter]

        if side_filter == "Buy Only":
            filtered = [o for o in filtered if o.get("side") == "buy"]
        elif side_filter == "Sell Only":
            filtered = [o for o in filtered if o.get("side") == "sell"]

        # Separate valid IDs from uncancellable orders (missing/synthetic/fallback IDs)
        valid = []
        skipped = []
        for order in filtered:
            order_id = order.get("client_order_id")
            # Skip falsy IDs (missing/empty) same as on_cancel_order policy
            if not order_id:
                skipped.append(order)
            elif order_id.startswith(SYNTHETIC_ID_PREFIX) or order_id.startswith(FALLBACK_ID_PREFIX):
                skipped.append(order)
            else:
                valid.append(order)

        return valid, skipped

    def _unique_symbols(self) -> list[str]:
        """Get unique symbols from current orders list."""
        return sorted(set(o.get("symbol", "") for o in self._orders if o.get("symbol")))

    async def _execute_cancel_all(self, orders: list[dict]) -> tuple[int, int]:
        """Execute cancellation with partial failure reporting and bounded concurrency.

        Uses semaphore to limit concurrent cancels (avoids overwhelming backend).
        Returns: (success_count, failure_count)
        """
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CANCELS)
        results: list[bool] = []

        async def cancel_one(order: dict) -> bool:
            async with semaphore:
                order_id = order.get("client_order_id")
                if not order_id:
                    # Should not happen (filtered out earlier), but handle gracefully
                    return False
                try:
                    await self._client.cancel_order(
                        order_id,
                        self._user_id,
                        role=self._user_role,
                        strategies=self._strategies,  # Required for auth scope
                        reason="Cancel All Orders dialog",
                        requested_by=self._user_id,
                        requested_at=datetime.now(UTC).isoformat(),
                    )
                    return True
                except Exception as exc:
                    logger.warning(
                        "cancel_all_single_order_failed",
                        extra={
                            "order_id": order_id,
                            "symbol": order.get("symbol"),
                            "error": str(exc),
                        }
                    )
                    return False

        # Execute all cancels with bounded concurrency
        results = await asyncio.gather(*[cancel_one(o) for o in orders])
        success_count = sum(1 for r in results if r)
        failure_count = len(results) - success_count

        return success_count, failure_count

    async def show(self) -> None:
        """Show cancel all dialog with filter options."""
        # Check permissions first (consistent with on_cancel_order)
        allowed, reason = self._check_permissions()
        if not allowed:
            ui.notify(reason, type="warning")
            return

        with ui.dialog() as dialog, ui.card().classes("p-4 w-96"):
            ui.label("Cancel Orders").classes("text-lg font-bold")

            # Filter controls
            with ui.row().classes("gap-4"):
                symbol_select = ui.select(
                    options=["All Symbols"] + self._unique_symbols(),
                    value="All Symbols",
                    label="Symbol"
                )
                side_select = ui.select(
                    options=["All Sides", "Buy Only", "Sell Only"],
                    value="All Sides",
                    label="Side"
                )

            # Preview with skipped count
            count_label = ui.label()
            skipped_label = ui.label().classes("text-warning text-xs")

            async def update_preview():
                valid, skipped = self._filter_orders(symbol_select.value, side_select.value)
                count_label.text = f"Will cancel {len(valid)} order(s)"
                if skipped:
                    skipped_label.text = f"⚠️ {len(skipped)} order(s) cannot be cancelled (missing ID)"
                else:
                    skipped_label.text = ""

            # NOTE: NiceGUI on_change needs sync wrapper for async handlers
            def on_change_handler():
                asyncio.create_task(update_preview())

            symbol_select.on_change(on_change_handler)
            side_select.on_change(on_change_handler)
            await update_preview()  # Initial update

            # Action buttons with double-submit protection
            submitting = False

            with ui.row().classes("gap-4 mt-4"):
                async def confirm():
                    nonlocal submitting
                    if submitting:
                        return  # Prevent double-submit
                    submitting = True
                    confirm_btn.disable()

                    try:
                        # CRITICAL: Refetch orders at confirm time (snapshot may be stale)
                        # Orders may have filled/cancelled since dialog opened
                        try:
                            fresh_response = await self._client.fetch_open_orders(
                                self._user_id, role=self._user_role, strategies=self._strategies
                            )
                            fresh_orders = fresh_response.get("orders", [])
                            # Re-apply filters to fresh data
                            self._orders = fresh_orders  # Update internal state
                        except Exception as fetch_exc:
                            ui.notify(f"Failed to refresh orders: {fetch_exc}", type="warning")
                            # Proceed with stale data but warn user
                            logger.warning("cancel_all_dialog_fetch_failed", extra={"error": str(fetch_exc)})

                        valid, skipped = self._filter_orders(symbol_select.value, side_select.value)
                        if not valid:
                            ui.notify("No orders to cancel", type="warning")
                            return

                        success, failed = await self._execute_cancel_all(valid)
                    finally:
                        submitting = False
                        confirm_btn.enable()

                    if failed == 0:
                        ui.notify(f"Cancelled {success} order(s)", type="positive")
                    else:
                        ui.notify(f"Cancelled {success}, failed {failed} order(s)", type="warning")

                    # Audit log entry for bulk cancellation (acceptance criteria requirement)
                    logger.info(
                        "cancel_all_dialog_executed",
                        extra={
                            "reason": "Cancel All Orders dialog",
                            "requested_by": self._user_id,
                            "requested_at": datetime.now(UTC).isoformat(),
                            "symbol_filter": symbol_select.value,
                            "side_filter": side_select.value,
                            "success_count": success,
                            "failed_count": failed,
                            "skipped_count": len(skipped),
                            "strategy_id": "manual_controls_cancel_all_dialog",
                        }
                    )

                    dialog.close()

                confirm_btn = ui.button("Cancel Orders", on_click=confirm).classes("bg-red-600 text-white")
                ui.button("Close", on_click=dialog.close)

        dialog.open()
```

**Files to Create:**
- `apps/web_console_ng/components/cancel_all_dialog.py`

**Files to Modify:**
- `apps/web_console_ng/components/orders_table.py` (add header button)
- `apps/web_console_ng/ui/trading_layout.py` (wire up dialog)
- `apps/web_console_ng/pages/dashboard.py` (pass is_read_only to CancelAllDialog)

**Acceptance Criteria:**
- [ ] "Cancel All" button in order blotter header with count badge
- [ ] **Viewer role blocked** (consistent with on_cancel_order)
- [ ] **Read-only mode NOT blocked** (cancel is fail-open, risk-reducing action allowed during degraded connections)
- [ ] Filter options (by symbol, by side)
- [ ] **Side filtering uses per-order cancel** (backend limitation)
- [ ] **Skips synthetic/fallback IDs with warning** (cannot be cancelled)
- [ ] **Handles missing order IDs gracefully** (won't crash on malformed data)
- [ ] **Uses bounded concurrency** (MAX_CONCURRENT_CANCELS=5)
- [ ] **Reports partial failures** (success_count, failure_count)
- [ ] Confirmation dialog showing filtered order count
- [ ] Audit log entry for bulk cancellation (client-side structured log with reason/requested_by/requested_at; future: extend backend cancel endpoint to accept audit metadata)

---

### T7.3: Order Replay/Duplicate - LOW PRIORITY

**Goal:** Quick replay of previous orders.

**Prerequisite:** Order history data source must be available. Current history tab is placeholder - verify `/api/v1/orders/history` endpoint is wired before implementing.

#### Implementation Plan

**Data Source Requirement:**
- Verify `AsyncTradingClient.fetch_order_history()` exists and returns filled/cancelled orders
- If missing, add endpoint or defer this task until history is available

**OrderTicket Integration Requirement:**
- `OrderTicketComponent` needs a `prefill_order(order_state: OrderTicketState)` method
- This method should update `self._state` and call `self._sync_inputs_from_state()`
- The existing `apply_dom_price_click` is DOM-specific; replay needs a generic prefill method

**Component: `order_replay.py`**

```python
# New file: apps/web_console_ng/components/order_replay.py

@dataclass(frozen=True)
class ReplayableOrder:
    """Order data for replay (immutable)."""
    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal  # Use Decimal internally; Order Ticket truncates to int with warning (see on_replay)
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: Literal["day", "gtc", "ioc", "fok"]
    original_order_id: str  # For audit trail

class OrderReplayHandler:
    """Handle order replay/duplicate functionality."""

    # Terminal statuses that allow replay
    REPLAYABLE_STATUSES = {"filled", "canceled", "cancelled", "expired", "rejected"}

    def can_replay(self, order: dict) -> bool:
        """Check if order can be replayed."""
        status = order.get("status", "").lower()
        return status in self.REPLAYABLE_STATUSES

    def extract_replay_data(self, order: dict) -> ReplayableOrder | None:
        """Extract replay data from filled/cancelled order.

        Returns None if required fields are missing.
        """
        try:
            # Use Decimal to capture original qty (fractional fills); Order Ticket will truncate to int with warning
            raw_qty = order.get("qty") or order.get("filled_qty") or 0
            qty = Decimal(str(raw_qty))

            original_order_id = order.get("client_order_id")
            if not original_order_id:
                return None

            return ReplayableOrder(
                symbol=order["symbol"],
                side=order["side"],
                qty=qty,
                order_type=order.get("type", "market"),
                limit_price=Decimal(str(order["limit_price"])) if order.get("limit_price") else None,
                stop_price=Decimal(str(order["stop_price"])) if order.get("stop_price") else None,
                time_in_force=order.get("time_in_force", "day"),
                # Block replay without original client_order_id (audit trail requirement)
                original_order_id=original_order_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("replay_extract_failed", extra={"error": str(exc), "order": order})
            return None

    async def on_replay(
        self,
        order: dict,
        user_id: str,
        user_role: str,
        on_prefill_order_ticket: Callable[[ReplayableOrder], None],
    ) -> None:
        """Handle replay button click - pre-fill order ticket."""
        if not self.can_replay(order):
            ui.notify("Cannot replay active orders", type="warning")
            return

        replay_data = self.extract_replay_data(order)
        if replay_data is None:
            ui.notify("Cannot replay: missing order data", type="warning")
            return

        if replay_data.qty <= Decimal("0"):
            ui.notify("Cannot replay: order has no quantity", type="warning")
            return

        # CRITICAL: Order Ticket only supports integer quantities
        # Truncate fractional quantities DOWN (never round up to avoid increasing exposure)
        # Uses ROUND_DOWN explicitly to guarantee truncation toward zero
        from decimal import ROUND_DOWN
        original_qty = replay_data.qty
        rounded_qty = int(original_qty.to_integral_value(rounding=ROUND_DOWN))  # Explicit ROUND_DOWN

        if rounded_qty <= 0:
            ui.notify("Cannot replay: quantity rounds to zero", type="warning")
            return

        # Check if rounding occurred and notify user
        qty_was_rounded = original_qty != Decimal(rounded_qty)
        if qty_was_rounded:
            ui.notify(
                f"Note: quantity adjusted from {original_qty} to {rounded_qty} (Order Ticket is integer-only)",
                type="info"
            )
            logger.info("replay_qty_rounded", extra={
                "original_qty": str(original_qty),
                "rounded_qty": rounded_qty,
                "symbol": replay_data.symbol,
            })

        # Create adjusted replay data with integer quantity for Order Ticket
        adjusted_replay_data = ReplayableOrder(
            symbol=replay_data.symbol,
            side=replay_data.side,
            qty=Decimal(rounded_qty),  # Integer as Decimal for type consistency
            order_type=replay_data.order_type,
            limit_price=replay_data.limit_price,
            stop_price=replay_data.stop_price,
            time_in_force=replay_data.time_in_force,
            original_order_id=replay_data.original_order_id,
        )

        on_prefill_order_ticket(adjusted_replay_data)
        ui.notify(f"Order form pre-filled with {replay_data.symbol} {replay_data.side}", type="info")

        # Audit log (field name matches acceptance criteria)
        logger.info(
            "order_replay_prefilled",
            extra={
                "user_id": user_id,
                "symbol": replay_data.symbol,
                "replayed_from": replay_data.original_order_id,  # Matches acceptance criteria
                "strategy_id": "manual",
            }
        )
```

**Files to Create:**
- `apps/web_console_ng/components/order_replay.py`

**Files to Modify:**
- `apps/web_console_ng/components/orders_table.py` (add Replay button to terminal orders)
- `apps/web_console_ng/components/order_ticket.py` (add `prefill_from_replay()` method)
- `apps/web_console_ng/static/js/aggrid_renderers.js` (add Replay button renderer for terminal orders)
- `apps/web_console_ng/pages/dashboard.py` (bind replay CustomEvent, wire to order_ticket.prefill_from_replay)

**Acceptance Criteria:**
- [ ] **Verify order history data source exists** before implementation
- [ ] "Replay" button on filled/cancelled orders (not active orders)
- [ ] **Handles missing fields gracefully** (returns None, shows warning)
- [ ] Pre-fill order form with previous values (symbol, side, qty, type, prices)
- [ ] Generate new client_order_id (never reuse - handled by backend)
- [ ] Still subject to fat finger validation (existing flow)
- [ ] Audit log includes `replayed_from: original_order_id`

---

### T7.4: One-Click Trading (Shift+Click) - HIGH PRIORITY

**Goal:** Enable instant order placement without confirmation dialogs.

**Prerequisite:** P6T2.4 (Kill Switch) must be complete - one-click is a safety-critical feature.

#### Implementation Plan

**Critical Design Decisions (from review feedback):**

1. **JS Integration:** Use NiceGUI CustomEvent dispatch (existing pattern), NOT pywebview
2. **Market Orders:** Require fresh price from OrderEntryContext with staleness check
3. **Order Submission:** Use `submit_manual_order()` for consistent audit trail
4. **Daily Notional Cap:** Persist in `state_manager` (not session-only)
5. **Alt-Click Cancel:** Match working orders by symbol + price with tolerance

**Component: `one_click_handler.py`**

```python
# New file: apps/web_console_ng/components/one_click_handler.py

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

PRICE_STALENESS_THRESHOLD_S = 30  # Block if price older than 30s
PRICE_MATCH_TOLERANCE = Decimal("0.01")  # $0.01 tolerance for order matching

@dataclass
class OneClickConfig:
    """One-click trading configuration."""
    enabled: bool = False  # Opt-in required
    daily_notional_cap: Decimal = Decimal("500000")  # $500k default
    cooldown_ms: int = 500  # Prevent double-click
    default_qty: int = 100  # Default shares for one-click
    session_confirmed: bool = False  # First-use confirmation

class OneClickHandler:
    """Handle one-click trading from DOM ladder and price chart.

    Safety: FAIL-CLOSED (risk-increasing action)
    """

    COOLDOWN_MS = 500
    DAILY_NOTIONAL_CAP_DEFAULT = Decimal("500000")

    def __init__(
        self,
        trading_client: AsyncTradingClient,
        fat_finger_validator: FatFingerValidator,
        safety_gate: SafetyGate,
        order_entry_context: OrderEntryContext,
        state_manager: UserStateManager,
        user_id: str,
        user_role: str,
        strategies: list[str] | None = None,  # Required for fetch_adv, auth scoping
    ):
        self._client = trading_client
        self._validator = fat_finger_validator
        self._safety = safety_gate
        self._context = order_entry_context
        self._state_manager = state_manager
        self._user_id = user_id
        self._user_role = user_role
        self._config = OneClickConfig()
        self._last_click_time: float = 0.0
        self._strategies = strategies  # Used for fetch_adv and auth header consistency

    async def _get_adv(self, symbol: str) -> int | None:
        """Get Average Daily Volume for fat finger validation.

        Returns None if unavailable (validator handles None gracefully).
        """
        try:
            # CRITICAL: fetch_adv requires user_id, role, strategies
            adv_data = await self._client.fetch_adv(
                symbol=symbol,
                user_id=self._user_id,
                role=self._user_role,
                strategies=self._strategies,
            )
            return adv_data.get("adv") if adv_data else None
        except Exception:
            return None  # ADV is optional, fat finger still validates qty/notional

    def is_enabled(self) -> bool:
        """Check if one-click trading is enabled."""
        return self._config.enabled and self._user_role in {"trader", "admin"}

    def _check_cooldown(self) -> bool:
        """Check if cooldown has elapsed."""
        now = time.time()
        if (now - self._last_click_time) * 1000 < self.COOLDOWN_MS:
            return False
        self._last_click_time = now
        return True

    def _get_fresh_price(self, symbol: str) -> tuple[Decimal | None, str]:
        """Get fresh price from OrderEntryContext with staleness check.

        Returns: (price, error_message) - price is None if stale/missing
        """
        prices = self._context.cached_prices  # Note: plural, matches property name
        if not prices or symbol not in prices:
            return None, "No price data available"

        price, timestamp = prices[symbol]
        age = (datetime.now(UTC) - timestamp).total_seconds()
        if age > PRICE_STALENESS_THRESHOLD_S:
            return None, f"Price data stale ({age:.0f}s old)"

        return price, ""

    async def _get_daily_notional(self) -> Decimal:
        """Get today's accumulated notional from persistent state.

        Uses UserStateManager.restore_state() pattern.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        notional_key = f"one_click_notional_{today}"
        state = await self._state_manager.restore_state()
        preferences = state.get("preferences", {}) if state else {}
        value = preferences.get(notional_key)
        return Decimal(str(value)) if value else Decimal("0")

    async def _update_daily_notional(self, new_total: Decimal) -> None:
        """Update today's accumulated notional in persistent state.

        CRITICAL: Only call AFTER successful order submission to prevent drift.
        Server-side (apps/execution_gateway/api/manual_controls.py) is
        the AUTHORITATIVE cap enforcer with atomic Redis INCR validation.
        UI-side tracking is best-effort UX optimization.

        Uses UserStateManager.save_preferences(key, value) pattern.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        notional_key = f"one_click_notional_{today}"
        await self._state_manager.save_preferences(notional_key, str(new_total))

    async def on_shift_click(
        self, symbol: str, price: Decimal, side: Literal["buy", "sell"],
    ) -> None:
        """Shift+Click: Instant limit order at clicked price."""
        # Enforce role/enablement at entry point (not just in is_enabled check)
        if not self.is_enabled():
            ui.notify("One-click trading is not enabled", type="warning")
            return
        await self._execute_one_click(
            symbol, price, side, "limit",
            mode="shift_click_limit",
        )

    async def on_ctrl_click(
        self, symbol: str, side: Literal["buy", "sell"],
    ) -> None:
        """Ctrl+Click: Instant market order.

        Note: Requires fresh price for fat finger validation and notional cap.
        """
        # Enforce role/enablement at entry point (not just in is_enabled check)
        if not self.is_enabled():
            ui.notify("One-click trading is not enabled", type="warning")
            return

        # Get fresh price (required for market orders)
        price, error = self._get_fresh_price(symbol)
        if price is None:
            ui.notify(f"Cannot place market order: {error}", type="negative")
            return

        await self._execute_one_click(
            symbol, price, side, "market",
            mode="ctrl_click_market",
        )

    async def on_alt_click(
        self, symbol: str, price: Decimal, working_orders: list[dict],
        is_read_only: bool = False,
    ) -> None:
        """Alt+Click: Cancel order(s) at price level.

        Matches working orders by symbol + limit_price with tolerance.
        Note: Alt+Click cancel doesn't require one-click to be enabled (always allowed),
        but respects viewer role. Read-only mode allows cancel with warning (risk-reducing).
        """
        # Permission checks
        if self._user_role == "viewer":
            ui.notify("Viewers cannot cancel orders", type="warning")
            return
        # CRITICAL: Cancel is risk-reducing (fail-open) - allow in read-only with warning
        # Consistent with existing on_cancel_order policy in orders_table.py
        if is_read_only:
            ui.notify("Warning: connection degraded - cancel may be delayed", type="warning")
            # Proceed with cancel (don't block risk-reducing action)

        # Find orders at this price level (limit orders only)
        # Guard against malformed limit_price to avoid crashing on one bad order
        def _price_matches(order: dict, target_price: Decimal) -> bool:
            """Check if order's limit_price matches target within tolerance."""
            limit_price_raw = order.get("limit_price")
            if limit_price_raw is None:
                return False
            try:
                order_price = Decimal(str(limit_price_raw))
                return abs(order_price - target_price) <= PRICE_MATCH_TOLERANCE
            except (InvalidOperation, ValueError):
                logger.warning("alt_click_invalid_limit_price", extra={
                    "order_id": order.get("client_order_id"),
                    "limit_price": limit_price_raw,
                })
                return False

        orders_at_level = [
            o for o in working_orders
            if o.get("symbol") == symbol and _price_matches(o, price)
        ]

        if not orders_at_level:
            ui.notify(f"No orders at ${price}", type="info")
            return

        # Cancel all matched orders (skip invalid IDs - consistent with on_cancel_order)
        cancelled = 0
        skipped = 0
        for order in orders_at_level:
            order_id = order.get("client_order_id")
            # Skip synthetic AND fallback IDs (same as cancel_all_dialog and on_cancel_order)
            if not order_id or order_id.startswith(SYNTHETIC_ID_PREFIX) or order_id.startswith(FALLBACK_ID_PREFIX):
                skipped += 1
                continue
            try:
                await self._client.cancel_order(
                    order_id, self._user_id, role=self._user_role,
                    strategies=self._strategies,  # Required for auth scope
                    reason="Alt-click cancel at price level",
                    requested_by=self._user_id,
                    requested_at=datetime.now(UTC).isoformat(),
                )
                cancelled += 1
            except Exception:
                pass

        if skipped > 0:
            ui.notify(f"Cancelled {cancelled}, skipped {skipped} (invalid ID) at ${price}", type="info")
        else:
            ui.notify(f"Cancelled {cancelled} order(s) at ${price}", type="info")

    async def _execute_one_click(
        self, symbol: str, price: Decimal, side: Literal["buy", "sell"],
        order_type: str, *, mode: str,
    ) -> None:
        """Execute one-click order with all safety checks.

        Safety: FAIL-CLOSED policy (risk-increasing action)
        """
        # 1. Check cooldown
        if not self._check_cooldown():
            ui.notify("Too fast - please wait", type="warning")
            return

        # 2. Check safety gates with FAIL-CLOSED policy (cached first for instant feedback)
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=self._context.cached_kill_switch_engaged,
            cached_connection_state=self._context.cached_connection_state,
            cached_circuit_breaker=self._context.cached_circuit_breaker_tripped,
            require_connected=True,
        )
        if not result.allowed:
            ui.notify(f"Order blocked: {result.reason}", type="negative")
            return

        # 2b. CRITICAL: Fresh API verification for FAIL-CLOSED actions
        # Cached state may lag if KS/CB was tripped since last update
        # MUST pass cached_connection_state or FAIL-CLOSED will always block on None
        fresh_result = await self._safety.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state=self._context.cached_connection_state,
        )
        if not fresh_result.allowed:
            ui.notify(f"Order blocked: {fresh_result.reason}", type="negative")
            return

        # 3. First-use confirmation per session
        if not self._config.session_confirmed:
            confirmed = await self._show_first_use_confirmation()
            if not confirmed:
                return
            self._config.session_confirmed = True

        # 4. Daily notional cap check (pre-check only, update after success)
        qty = self._config.default_qty
        notional = Decimal(qty) * price
        current_notional = await self._get_daily_notional()
        if current_notional + notional > self._config.daily_notional_cap:
            ui.notify(
                f"Daily notional cap (${self._config.daily_notional_cap:,.0f}) would be exceeded",
                type="negative"
            )
            return
        # Note: Actual update happens AFTER successful submit to avoid drift on failure

        # 5. Fat finger validation (with ADV for % of volume check)
        adv = await self._get_adv(symbol)
        validation = self._validator.validate(symbol=symbol, qty=qty, price=price, adv=adv)
        if validation.blocked:
            ui.notify(f"Fat finger blocked: {validation.warnings[0].message}", type="negative")
            return

        # 6. Submit order via manual order endpoint (consistent audit trail)
        # CRITICAL: submit_manual_order takes order_data dict, NOT kwargs
        try:
            order_data = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "order_origin": "one_click",  # CRITICAL: Server-side notional cap enforcement key
                "reason": f"One-click {mode}",
                "requested_by": self._user_id,
                "requested_at": datetime.now(UTC).isoformat(),
            }
            if order_type == "limit":
                order_data["limit_price"] = str(price)
            result = await self._client.submit_manual_order(
                order_data=order_data,
                user_id=self._user_id,
                role=self._user_role,
                strategies=self._strategies,
            )

            # Update daily notional AFTER successful submit (prevents drift on failure)
            new_total = current_notional + notional
            await self._update_daily_notional(new_total)

            # Brief toast confirmation
            order_id = result.get("client_order_id", "")[-6:]
            ui.notify(
                f"✓ {side.upper()} {qty} {symbol} @ {'MKT' if order_type == 'market' else f'${price}'} (#{order_id})",
                type="positive",
                timeout=2000,
            )

            # Audit log
            logger.info(
                "one_click_order_submitted",
                extra={
                    "user_id": self._user_id,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "order_type": order_type,
                    "price": str(price),
                    "mode": mode,
                    "client_order_id": result.get("client_order_id"),
                    "daily_notional": str(new_total),  # From atomic check_and_update
                    "strategy_id": "manual_controls_one_click",
                },
            )
        except Exception as exc:
            logger.error("one_click_order_failed", extra={"error": str(exc)})
            ui.notify(f"Order failed: {exc}", type="negative")
```

**JavaScript Integration - CustomEvent Pattern (NiceGUI standard):**

```javascript
// Add to static/js/dom_ladder.js
// Uses CustomEvent dispatch (NiceGUI pattern), NOT pywebview

window.DOMLadder.handleClick = function(containerId, event, level) {
    const price = level.price;
    const side = level.type === 'bid' ? 'buy' : 'sell';

    // Get symbol from container data attribute (set during DOM ladder creation)
    const container = document.getElementById(containerId);
    const symbol = container ? container.dataset.symbol : null;

    // Dispatch CustomEvent for NiceGUI ui.on() handling
    // CRITICAL: Include symbol in payload so handlers don't receive None
    if (event.shiftKey) {
        // Shift+Click: Limit order at price
        window.dispatchEvent(new CustomEvent('dom_one_click', {
            detail: { containerId, symbol, price, side, mode: 'shift_limit' }
        }));
    } else if (event.ctrlKey || event.metaKey) {
        // Ctrl/Cmd+Click: Market order
        window.dispatchEvent(new CustomEvent('dom_one_click', {
            detail: { containerId, symbol, price: null, side, mode: 'ctrl_market' }
        }));
    } else if (event.altKey) {
        // Alt+Click: Cancel at level
        window.dispatchEvent(new CustomEvent('dom_one_click', {
            detail: { containerId, symbol, price, side: null, mode: 'alt_cancel' }
        }));
    } else {
        // Normal click: Pre-fill order form (existing behavior)
        window.dispatchEvent(new CustomEvent('dom_price_click', {
            detail: { containerId, symbol, price, side }
        }));
    }
};
```

**Dashboard.py Integration:**

```python
# In dashboard.py, add handler for new CustomEvent
# Use established pattern with _extract_event_detail and args=["detail"]

async def handle_dom_one_click(event: events.GenericEventArguments) -> None:
    detail = _extract_event_detail(event.args)
    await order_entry_context.handle_one_click(detail)

ui.on("dom_one_click", handle_dom_one_click, args=["detail"])
```

**Files to Create:**
- `apps/web_console_ng/components/one_click_handler.py`
- `apps/web_console_ng/components/one_click_settings.py` (settings UI)

**Files to Modify:**
- `apps/web_console_ng/components/dom_ladder.py` (dispatch CustomEvent)
- `apps/web_console_ng/components/price_chart.py` (dispatch CustomEvent)
- `apps/web_console_ng/static/js/dom_ladder.js` (JavaScript event handling)
- `apps/web_console_ng/core/state_manager.py` (persist daily notional)
- `apps/web_console_ng/pages/dashboard.py` (handle dom_one_click event)
- `apps/web_console_ng/components/order_entry_context.py` (add handle_one_click method)
- `apps/execution_gateway/api/manual_controls.py` (add daily notional cap enforcement - see backend task below)

**Backend Task: Daily Notional Cap Enforcement (REQUIRED)**

The daily notional cap MUST be enforced server-side to prevent bypass via multiple sessions or devtools. UI-side is best-effort UX only.

```python
# apps/execution_gateway/api/manual_controls.py - Add to submit_manual_order

from decimal import Decimal, ROUND_UP

NOTIONAL_SCALE = Decimal("100")  # Store cents to avoid float precision issues

async def _check_daily_notional_cap(
    redis: Redis,
    user_id: str,
    additional_notional_cents: int,  # Already scaled to cents
    cap_cents: int = 500000_00,  # $500,000 in cents
) -> bool:
    """Atomically check and increment daily notional. Returns True if allowed.

    Uses Redis INCRBY with scaled cents for precision-safe enforcement.
    Key: f"one_click_notional:{user_id}:{date}"
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    key = f"one_click_notional:{user_id}:{today}"

    # Lua script for atomic check+increment (uses integer cents for precision)
    lua_script = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local additional = tonumber(ARGV[1])
    local cap = tonumber(ARGV[2])
    if current + additional > cap then
        return 0  -- Blocked
    end
    redis.call('INCRBY', KEYS[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], 86400)  -- 24h TTL
    return 1  -- Allowed
    """
    result = await redis.eval(lua_script, 1, key, str(additional_notional_cents), str(cap_cents))
    return result == 1

async def _get_trusted_price(
    symbol: str,
    request_price: Decimal | None,
    ctx: FatFingerContext,  # From resolve_fat_finger_context dependency
) -> Decimal:
    """Get trusted price for notional calculation.

    For limit orders: use request price (user-specified).
    For market orders: fetch fresh price from ctx.liquidity_service (trusted source).
    Uses existing resolve_fat_finger_context plumbing for consistency.
    """
    if request_price is not None:
        return request_price
    # Market order: fetch from trusted source via existing liquidity service
    # This prevents clients from manipulating notional by omitting price
    # Uses ctx.liquidity_service.get_mid_price() (same as fat finger validation)
    market_price = await ctx.liquidity_service.get_mid_price(symbol)
    if market_price is None:
        raise HTTPException(status_code=400, detail="Cannot determine market price for notional cap")
    return Decimal(str(market_price))

# CRITICAL: Add order_origin field to ManualOrderRequest schema
# apps/execution_gateway/schemas_manual_controls.py:
#   class ManualOrderRequest(BaseModel):
#       ...existing fields...
#       order_origin: Literal["manual", "one_click"] = "manual"  # Server-validated field

# In POST /api/v1/manual/orders handler (apps/execution_gateway/api/manual_controls.py):
# CRITICAL: Use server-validated order_origin field, NOT free-text reason
# This prevents bypass via devtools/alternate clients
if request.order_origin == "one_click":
    # CRITICAL: Pass ctx from endpoint handler (resolve_fat_finger_context dependency)
    price = await _get_trusted_price(request.symbol, request.limit_price, ctx)
    notional = Decimal(request.qty) * price
    # CRITICAL: Round UP to enforce strict cap (prevent accumulated fractional cents bypass)
    notional_cents = int((notional * NOTIONAL_SCALE).to_integral_value(rounding=ROUND_UP))
    if not await _check_daily_notional_cap(redis, user_id, notional_cents):
        raise HTTPException(
            status_code=400,
            detail="Daily notional cap exceeded for one-click trading"
        )
```

**Acceptance Criteria:**
- [ ] Shift+Click places limit order instantly at clicked price
- [ ] Ctrl+Click places market order instantly
- [ ] **Market orders require fresh price** (staleness < 30s, block if missing)
- [ ] Alt+Click cancels order(s) at price level (with $0.01 tolerance)
- [ ] **Feature disabled by default** (opt-in in settings)
- [ ] **Entry points enforce enablement** (shift/ctrl clicks check is_enabled())
- [ ] **Alt+Click respects permissions** (viewer role blocked, read-only warns but proceeds - cancel is fail-open)
- [ ] Brief confirmation toast shown (not dialog)
- [ ] **Uses CustomEvent dispatch** (NiceGUI pattern, not pywebview)
- [ ] **Uses submit_manual_order()** for consistent audit trail
- [ ] Works on both DOM ladder and Chart
- [ ] **Gated by:** Kill switch state (FAIL-CLOSED)
- [ ] **Gated by:** Connection state (FAIL-CLOSED)
- [ ] **Gated by:** Circuit breaker (FAIL-CLOSED)
- [ ] **Safety: Fat finger thresholds still apply** (block if exceeded)
- [ ] **Safety: Daily notional cap** (BACKEND enforced atomically via Redis Lua script in manual_order.py; UI-side is best-effort UX only)
- [ ] **Safety: Cooldown** (prevent accidental double-click, 500ms)
- [ ] **Safety: First-use confirmation** per session (explain risks)
- [ ] **Role-gated:** Require TRADER role or higher
- [ ] **Audit trail:** Log all one-click orders with mode used

---

## Dependencies

```
P6T2.3 Connection ──> All order actions (gating)
P6T2.4 Kill Switch ──> T7.1 Flatten, T7.4 One-Click (gating)
P6T6.3 Fat Finger ──> T7.1 Reverse, T7.3 Replay, T7.4 One-Click (validation)
Order History API ──> T7.3 Replay (data source)
```

---

## Testing Strategy

### Unit Tests

**Safety Gate:**
- `test_safety_gate_fail_closed_blocks_on_disconnected()`
- `test_safety_gate_fail_open_warns_but_allows_on_disconnected()`
- `test_safety_gate_always_blocks_on_kill_switch_engaged()`
- `test_safety_gate_api_verification_handles_5xx()`

**T7.1 Flatten Controls:**
- `test_flatten_symbol_uses_fail_open_policy()`
- `test_flatten_symbol_cancels_orders_before_close()`
- `test_reverse_uses_fail_closed_policy()`
- `test_reverse_blocked_when_circuit_breaker_tripped()`
- `test_reverse_requires_fresh_price()`
- `test_reverse_blocked_on_stale_price()`
- `test_reverse_uses_on_demand_quote_for_non_subscribed()`
- `test_reverse_passes_adv_to_fat_finger()`
- `test_reverse_uses_abs_qty_for_open_leg()`
- `test_reverse_passes_strategies_to_poll()`
- `test_reverse_aborts_on_consecutive_poll_errors()`
- `test_flatten_all_requires_mfa_token()`
- `test_flatten_all_retrieves_id_token_from_storage()`
- `test_flatten_all_warns_on_stale_snapshot()`

**T7.2 Cancel All:**
- `test_filter_orders_by_symbol()`
- `test_filter_orders_by_side()`
- `test_cancel_all_skips_synthetic_ids()`
- `test_cancel_all_skips_fallback_ids()`
- `test_cancel_all_reports_partial_failures()`
- `test_cancel_all_handles_missing_order_id()`
- `test_unique_symbols_returns_sorted_list()`
- `test_cancel_all_uses_bounded_concurrency()`

**T7.3 Order Replay:**
- `test_can_replay_filled_orders()`
- `test_cannot_replay_active_orders()`
- `test_replay_handles_missing_fields()`
- `test_replay_extracts_correct_order_data()`
- `test_replay_truncates_fractional_qty_with_warning()` # Order Ticket is integer-only; fractional qty is truncated (ROUND_DOWN) with user notification

**T7.4 One-Click:**
- `test_cooldown_prevents_rapid_clicks()`
- `test_daily_notional_cap_persisted_across_refresh()`
- `test_daily_notional_reads_from_preferences_namespace()`
- `test_market_order_blocked_on_stale_price()`
- `test_market_order_blocked_on_missing_price()`
- `test_one_click_uses_fail_closed_policy()`
- `test_one_click_uses_submit_manual_order()`
- `test_one_click_passes_adv_to_fat_finger()`
- `test_alt_click_matches_orders_with_tolerance()`
- `test_first_use_confirmation_required()`
- `test_shift_click_blocked_when_not_enabled()`
- `test_ctrl_click_blocked_when_not_enabled()`
- `test_alt_click_blocked_for_viewer_role()`
- `test_alt_click_warns_but_proceeds_when_read_only()` # Cancel is fail-open, allows with warning

### Integration Tests

- `test_flatten_all_with_mfa_confirmation()`
- `test_one_click_with_fat_finger_rejection()`
- `test_cancel_all_with_filter_execution()`
- `test_replay_prefills_order_ticket()`
- `test_one_click_custom_event_dispatch()`

### E2E Tests

- `test_flatten_workflow_dialog_to_execution()`
- `test_one_click_trading_workflow_opt_in_execute_verify()`
- `test_cancel_all_with_filter_dialog()`

---

## Implementation Order

1. **T0: Safety Gate Helper** - Foundation for all actions
2. **T7.2 Cancel All Orders** (MEDIUM) - Simplest, no safety gates needed
3. **T7.3 Order Replay** (LOW) - Pre-fill only, uses existing validation (requires history API)
4. **T7.1 Flatten Controls** (HIGH) - Safety critical, uses SafetyGate
5. **T7.4 One-Click Trading** (HIGH) - Most complex, requires all infrastructure

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] **SafetyGate helper created and reused** across all actions
- [ ] **Reverse uses FAIL-CLOSED policy** (not fail-open like flatten)
- [ ] Flatten controls functional with safety gates
- [ ] Cancel all with filtering working
- [ ] One-click trading (opt-in, role-gated, with safety controls)
- [ ] **One-click uses CustomEvent** (not pywebview)
- [ ] **One-click market orders require fresh price**
- [ ] **Daily notional cap persisted** (survives page refresh)
- [ ] All actions respect kill switch state
- [ ] All actions respect connection state
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-29
**Status:** PLANNING
**Review Round:** 8
