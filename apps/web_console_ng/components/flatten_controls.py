"""Flatten and reverse position controls.

This module provides position row actions for flattening (close + cancel orders)
and reversing (close + open opposite) positions.

Example:
    controls = FlattenControls(
        safety_gate=gate,
        trading_client=client,
        fat_finger_validator=validator,
        strategies=["alpha_baseline"],
    )

    # Flatten single symbol (FAIL-OPEN: cached state is optional)
    await controls.on_flatten_symbol(
        "AAPL", 100, user_id, user_role,
        cached_kill_switch=True,  # Optional, enables instant UI response
        cached_connection_state="CONNECTED",
        cached_circuit_breaker=False,
    )

    # Reverse position (FAIL-CLOSED: cached state is required)
    await controls.on_reverse_position(
        "AAPL", 100, "buy", user_id, user_role,
        cached_kill_switch=False,
        cached_connection_state="CONNECTED",
        cached_circuit_breaker=False,
    )
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from nicegui import ui

from apps.web_console_ng.components.safety_gate import SafetyGate, SafetyPolicy
from apps.web_console_ng.utils.orders import is_cancellable_order_id
from apps.web_console_ng.utils.time import parse_iso_timestamp

if TYPE_CHECKING:
    from apps.web_console_ng.components.fat_finger_validator import FatFingerValidator
    from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)

# Price staleness threshold for FAIL-CLOSED operations
PRICE_STALENESS_THRESHOLD_S = 30


class FlattenControls:
    """Flatten/Reverse buttons for position rows.

    Provides two main operations:
    - on_flatten_symbol: Close position + cancel all pending orders (FAIL-OPEN)
    - on_reverse_position: Close position + open opposite (FAIL-CLOSED)
    """

    def __init__(
        self,
        safety_gate: SafetyGate,
        trading_client: AsyncTradingClient,
        fat_finger_validator: FatFingerValidator,
        strategies: list[str] | None = None,
    ):
        """Initialize flatten controls.

        Args:
            safety_gate: Safety gate for policy-based checks
            trading_client: Client for API calls
            fat_finger_validator: Validator for position size checks
            strategies: Strategy scope for multi-strategy users
        """
        self._safety = safety_gate
        self._client = trading_client
        self._validator = fat_finger_validator
        self._strategies = strategies

    async def _get_fresh_price_with_fallback(
        self, symbol: str, user_id: str, user_role: str, *, strict_timestamp: bool = False
    ) -> tuple[Decimal | None, str]:
        """Get fresh price via API with staleness validation.

        Args:
            symbol: Symbol to get price for
            user_id: User ID for API calls
            user_role: User role for API calls
            strict_timestamp: If True, require valid timestamp (FAIL_CLOSED for reverse)

        Returns:
            Tuple of (price, error_message) - price is None if unavailable/stale
        """
        # Fetch price directly from API (no cached price access)
        try:
            prices = await self._client.fetch_market_prices(
                user_id=user_id,
                role=user_role,
                strategies=self._strategies,
            )
            # Find the symbol in the prices list
            symbol_price = next(
                (p for p in prices if p.get("symbol", "").upper() == symbol.upper()),
                None,
            )
            if symbol_price and symbol_price.get("mid"):
                try:
                    price_val = Decimal(str(symbol_price["mid"]))
                    if price_val <= 0 or not price_val.is_finite():
                        return None, f"Invalid price value: {symbol_price['mid']}"
                except (InvalidOperation, ValueError):
                    return None, f"Unparseable price: {symbol_price['mid']}"

                # Check timestamp - required for FAIL_CLOSED (strict_timestamp=True)
                raw_ts = symbol_price.get("timestamp") or symbol_price.get("updated_at")
                if raw_ts:
                    try:
                        if isinstance(raw_ts, str):
                            ts = parse_iso_timestamp(raw_ts)
                        elif isinstance(raw_ts, datetime):
                            ts = raw_ts
                        else:
                            # Non-string, non-datetime (e.g., int/float timestamp)
                            # FAIL_CLOSED: Block on unrecognized format
                            if strict_timestamp:
                                return None, f"Price timestamp unrecognized type: {type(raw_ts).__name__} (FAIL-CLOSED)"
                            ts = None
                        if ts and isinstance(ts, datetime):
                            age = (datetime.now(UTC) - ts).total_seconds()
                            if age > PRICE_STALENESS_THRESHOLD_S:
                                return None, f"Price data stale ({age:.0f}s old)"
                        elif strict_timestamp:
                            # ts is None or not datetime after parsing - FAIL_CLOSED blocks
                            return None, "Price timestamp failed to parse (FAIL-CLOSED)"
                    except Exception:
                        # FAIL_CLOSED: Block on unparseable timestamp
                        if strict_timestamp:
                            return None, "Price timestamp unparseable (FAIL-CLOSED)"
                        # FAIL_OPEN: Proceed with price despite timestamp issue
                elif strict_timestamp:
                    # FAIL_CLOSED: Block on missing timestamp
                    return None, "Price timestamp missing (FAIL-CLOSED)"

                return price_val, ""
            return None, "No price data available for symbol"
        except Exception as exc:
            logger.warning(
                "price_fetch_failed", extra={"symbol": symbol, "error": str(exc)}
            )
            return None, f"Price fetch failed: {exc}"

    async def _get_adv(self, symbol: str, user_id: str, user_role: str) -> int | None:
        """Get Average Daily Volume for fat finger validation.

        Args:
            symbol: Symbol to get ADV for
            user_id: User ID for API calls
            user_role: User role for API calls

        Returns:
            ADV value or None if unavailable
        """
        try:
            adv_data = await self._client.fetch_adv(
                symbol=symbol,
                user_id=user_id,
                role=user_role,
                strategies=self._strategies,
            )
            return adv_data.get("adv") if adv_data else None
        except Exception:
            return None  # ADV is optional

    def _validate_qty(self, qty: int | float) -> tuple[int | None, str]:
        """Validate position quantity.

        Args:
            qty: Quantity to validate

        Returns:
            Tuple of (normalized_qty, error_message) - qty is None if invalid
        """
        try:
            qty_float = float(qty)
            if not math.isfinite(qty_float) or qty_float == 0:
                return None, "Invalid position quantity"
            if qty_float != int(qty_float):
                return None, "Position quantity must be an integer"
            return int(qty_float), ""
        except (ValueError, TypeError):
            return None, "Invalid position quantity"

    async def _cancel_symbol_orders(
        self,
        symbol: str,
        user_id: str,
        user_role: str,
        reason: str,
    ) -> tuple[int, int, int, bool]:
        """Cancel all orders for a symbol.

        Args:
            symbol: Symbol to cancel orders for
            user_id: User ID for API calls
            user_role: User role for API calls
            reason: Reason for cancellation

        Returns:
            Tuple of (cancelled_count, failed_count, uncancellable_count, had_fetch_error)
            had_fetch_error is True if we couldn't fetch orders (FAIL_CLOSED should block)
        """
        try:
            response = await self._client.fetch_open_orders(
                user_id, role=user_role, strategies=self._strategies
            )
            all_symbol_orders = [
                o
                for o in response.get("orders", [])
                if o.get("symbol", "").upper() == symbol.upper()
            ]

            # Detect uncancellable orders using shared utility
            uncancellable = [
                o
                for o in all_symbol_orders
                if not is_cancellable_order_id(o.get("client_order_id"))
            ]

            # Cancel orders with valid IDs
            cancellable = [
                o
                for o in all_symbol_orders
                if is_cancellable_order_id(o.get("client_order_id"))
            ]

            cancelled = 0
            failed = 0
            for order in cancellable:
                try:
                    await self._client.cancel_order(
                        order["client_order_id"],
                        user_id,
                        role=user_role,
                        strategies=self._strategies,
                        reason=reason,
                        requested_by=user_id,
                        requested_at=datetime.now(UTC).isoformat(),
                    )
                    cancelled += 1
                except Exception:
                    failed += 1

            return cancelled, failed, len(uncancellable), False
        except Exception as exc:
            logger.warning(
                "cancel_orders_failed", extra={"symbol": symbol, "error": str(exc)}
            )
            return 0, 0, 0, True  # had_fetch_error = True

    async def _verify_orders_cleared(
        self,
        symbol: str,
        user_id: str,
        user_role: str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.5,
    ) -> tuple[bool, str]:
        """Verify no open orders remain for symbol (for FAIL_CLOSED operations).

        Args:
            symbol: Symbol to check
            user_id: User ID for API calls
            user_role: User role for API calls
            timeout_s: Maximum time to wait for orders to clear
            poll_interval_s: Time between polls

        Returns:
            Tuple of (cleared, error_message) - cleared is True if no orders remain
        """
        start_time = datetime.now(UTC)
        while (datetime.now(UTC) - start_time).total_seconds() < timeout_s:
            try:
                response = await self._client.fetch_open_orders(
                    user_id, role=user_role, strategies=self._strategies
                )
                symbol_orders = [
                    o
                    for o in response.get("orders", [])
                    if o.get("symbol", "").upper() == symbol.upper()
                ]
                if len(symbol_orders) == 0:
                    return True, ""
            except Exception as exc:
                # FAIL_CLOSED: Continue polling on fetch error
                logger.warning(
                    "verify_orders_cleared_fetch_error",
                    extra={"symbol": symbol, "error": str(exc)},
                )
            await asyncio.sleep(poll_interval_s)

        return False, f"Orders not cleared after {timeout_s}s"

    async def on_flatten_symbol(
        self,
        symbol: str,
        qty: int,
        user_id: str,
        user_role: str,
        *,
        cached_kill_switch: bool | None = None,
        cached_connection_state: str | None = None,
        cached_circuit_breaker: bool | None = None,
    ) -> None:
        """Flatten single symbol position (close + cancel all pending orders).

        Safety: FAIL-OPEN (risk-reducing action)

        Args:
            symbol: Symbol to flatten
            qty: Position quantity (can be negative for shorts)
            user_id: User ID for API calls
            user_role: User role for API calls
            cached_kill_switch: Cached kill switch state (True=engaged, False=safe, None=unknown)
            cached_connection_state: Cached connection state ("CONNECTED", "DISCONNECTED", etc.)
            cached_circuit_breaker: Cached circuit breaker state (True=tripped, False=open, None=unknown)
        """
        # 0. Role check
        if user_role == "viewer":
            ui.notify("Viewers cannot flatten positions", type="warning")
            return

        # 0b. Qty validation
        validated_qty, error = self._validate_qty(qty)
        if validated_qty is None:
            ui.notify(error, type="negative")
            return

        # 1. Pre-check with cached state
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_OPEN,
            cached_kill_switch=cached_kill_switch,
            cached_connection_state=cached_connection_state,
            cached_circuit_breaker=cached_circuit_breaker,
        )
        if not result.allowed:
            ui.notify(f"Cannot flatten: {result.reason}", type="negative")
            return

        # Show warnings
        for warning in result.warnings:
            ui.notify(warning, type="warning")

        # Execute flatten
        async def execute_flatten() -> None:
            """Execute flatten: cancel orders + close position."""
            # Fresh safety check (FAIL-OPEN: pass cached state for warnings but proceed on uncertainty)
            confirm_result = await self._safety.check_with_api_verification(
                policy=SafetyPolicy.FAIL_OPEN,
                cached_connection_state=cached_connection_state,
            )
            if not confirm_result.allowed:
                ui.notify(f"Flatten blocked: {confirm_result.reason}", type="negative")
                return

            # Step 1: Cancel working orders (FAIL_OPEN: warn on errors but proceed)
            cancelled, failed, uncancellable, had_fetch_error = await self._cancel_symbol_orders(
                symbol, user_id, user_role, "Flatten symbol - pre-close cancel"
            )

            if had_fetch_error:
                ui.notify(
                    "Warning: Failed to fetch orders - proceeding with close (risk-reducing)",
                    type="warning",
                )

            if uncancellable > 0:
                ui.notify(
                    f"Warning: {uncancellable} order(s) cannot be cancelled (invalid IDs)",
                    type="warning",
                )

            if failed > 0:
                ui.notify(
                    f"Cancelled {cancelled}, failed {failed} order(s) for {symbol}",
                    type="warning",
                )
            elif cancelled > 0:
                ui.notify(f"Cancelled {cancelled} order(s) for {symbol}", type="info")

            # Step 1b: Fetch authoritative position qty (FAIL_OPEN: warn + fallback to UI qty)
            close_qty = abs(validated_qty)  # Default to UI qty
            try:
                positions_resp = await self._client.fetch_positions(
                    user_id, role=user_role, strategies=self._strategies
                )
                positions_list = positions_resp.get("positions", [])
                current_pos = next(
                    (p for p in positions_list if p.get("symbol", "").upper() == symbol.upper()),
                    None,
                )
                if current_pos:
                    # Parse qty robustly (handles float strings like "100.0" from API)
                    authoritative_qty = abs(int(float(current_pos.get("qty", 0))))
                    if authoritative_qty == 0:
                        ui.notify(f"{symbol} position already flat", type="info")
                        return
                    close_qty = authoritative_qty
                else:
                    # FAIL_OPEN: Symbol missing from positions API (may be filtered/stale)
                    # Proceed with UI qty and warning (risk-reducing action should still work)
                    ui.notify(
                        f"Warning: {symbol} not in positions list - using UI qty",
                        type="warning",
                    )
            except Exception as pos_exc:
                # FAIL_OPEN: Warn but proceed with UI qty (risk-reducing)
                ui.notify(
                    f"Warning: Failed to verify position ({pos_exc}) - using UI qty",
                    type="warning",
                )

            # Step 2: Close position using authoritative qty
            try:
                await self._client.close_position(
                    symbol=symbol,
                    qty=close_qty,
                    reason="Flatten symbol",
                    requested_by=user_id,
                    requested_at=datetime.now(UTC).isoformat(),
                    user_id=user_id,
                    role=user_role,
                    strategies=self._strategies,
                )
                ui.notify(f"Closed {symbol} position ({close_qty} shares)", type="positive")
            except Exception as exc:
                ui.notify(f"Close failed: {exc}", type="negative")

        # Show confirmation dialog
        with ui.dialog() as dialog, ui.card().classes("p-4"):
            ui.label(f"Flatten {symbol}?").classes("text-lg font-bold")
            ui.label(f"This will cancel all orders and close {abs(validated_qty)} shares.")

            with ui.row().classes("gap-4 mt-4"):

                async def on_confirm() -> None:
                    dialog.close()
                    await execute_flatten()

                ui.button("Flatten", on_click=on_confirm).classes("bg-red-600 text-white")
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()

    async def on_reverse_position(
        self,
        symbol: str,
        qty: int,
        current_side: str,
        user_id: str,
        user_role: str,
        *,
        cached_kill_switch: bool | None = None,
        cached_connection_state: str | None = None,
        cached_circuit_breaker: bool | None = None,
    ) -> None:
        """Reverse position (close + open opposite).

        Safety: FAIL-CLOSED (risk-increasing action)

        IMPORTANT: Cached state should be provided from the caller's real-time
        subscriptions. For FAIL-CLOSED operations, None values will block the action.

        Args:
            symbol: Symbol to reverse
            qty: Position quantity (positive integer)
            current_side: Current side ("buy" for long, "sell" for short)
            user_id: User ID for API calls
            user_role: User role for API calls
            cached_kill_switch: Cached kill switch state (True=engaged, False=safe, None=unknown)
            cached_connection_state: Cached connection state ("CONNECTED", "DISCONNECTED", etc.)
            cached_circuit_breaker: Cached circuit breaker state (True=tripped, False=open, None=unknown)
        """
        # 0. Role check
        if user_role == "viewer":
            ui.notify("Viewers cannot reverse positions", type="warning")
            return

        # 0b. Qty validation
        validated_qty, error = self._validate_qty(qty)
        if validated_qty is None:
            ui.notify(error, type="negative")
            return

        # 1. Pre-check with FAIL-CLOSED policy
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=cached_kill_switch,
            cached_connection_state=cached_connection_state,
            cached_circuit_breaker=cached_circuit_breaker,
            require_connected=True,
        )
        if not result.allowed:
            ui.notify(f"Cannot reverse: {result.reason}", type="negative")
            return

        # 1b. Check for uncancellable orders
        try:
            response = await self._client.fetch_open_orders(
                user_id, role=user_role, strategies=self._strategies
            )
            all_symbol_orders = [
                o
                for o in response.get("orders", [])
                if o.get("symbol", "").upper() == symbol.upper()
            ]

            uncancellable_orders = [
                o
                for o in all_symbol_orders
                if not is_cancellable_order_id(o.get("client_order_id"))
            ]
            if uncancellable_orders:
                ui.notify(
                    f"Cannot reverse: {len(uncancellable_orders)} open order(s) have uncancellable IDs",
                    type="negative",
                )
                return

            open_order_count = len(
                [
                    o
                    for o in all_symbol_orders
                    if is_cancellable_order_id(o.get("client_order_id"))
                ]
            )
        except Exception as fetch_exc:
            ui.notify(
                f"Cannot reverse: failed to check open orders ({fetch_exc})",
                type="negative",
            )
            return

        # 2. Fetch fresh price (strict_timestamp for FAIL_CLOSED reverse)
        opposite_side = "sell" if current_side == "buy" else "buy"
        price, error = await self._get_fresh_price_with_fallback(
            symbol, user_id, user_role, strict_timestamp=True
        )
        if price is None:
            ui.notify(f"Cannot reverse: {error}", type="negative")
            return

        # 3. Get ADV and validate
        adv = await self._get_adv(symbol, user_id, user_role)

        # 4. Fat finger validation
        validation = self._validator.validate(
            symbol=symbol, qty=abs(validated_qty), price=price, adv=adv
        )
        if validation.blocked:
            ui.notify(
                f"Reverse blocked: {validation.warnings[0].message}", type="negative"
            )
            return

        # Double-submit protection
        reverse_submitting = False

        async def execute_reverse() -> None:
            """Execute two-step reverse."""
            nonlocal reverse_submitting
            if reverse_submitting:
                return
            reverse_submitting = True

            try:
                CLOSE_CONFIRMATION_TIMEOUT_S = 30
                POLL_INTERVAL_S = 1.0

                # Fresh safety check
                confirm_result = await self._safety.check_with_api_verification(
                    policy=SafetyPolicy.FAIL_CLOSED,
                    cached_connection_state=cached_connection_state,
                )
                if not confirm_result.allowed:
                    ui.notify(
                        f"Reverse blocked: {confirm_result.reason}", type="negative"
                    )
                    return

                # Step 0: Cancel open orders (FAIL_CLOSED: block on any error or uncertainty)
                cancelled, failed, uncancellable, had_fetch_error = await self._cancel_symbol_orders(
                    symbol, user_id, user_role, "Reverse position - pre-cancel"
                )

                # FAIL_CLOSED: Block on fetch error - can't verify no working orders
                if had_fetch_error:
                    ui.notify(
                        "Reverse blocked: failed to fetch open orders (FAIL-CLOSED)",
                        type="negative",
                    )
                    return

                if failed > 0:
                    ui.notify(
                        f"Reverse blocked: failed to cancel {failed} order(s)",
                        type="negative",
                    )
                    return

                # FAIL_CLOSED: Block on uncancellable orders (working orders = uncertainty)
                if uncancellable > 0:
                    ui.notify(
                        f"Reverse blocked: {uncancellable} uncancellable order(s) (FAIL-CLOSED)",
                        type="negative",
                    )
                    return

                if cancelled > 0:
                    ui.notify(
                        f"Cancelled {cancelled} order(s) for {symbol}", type="info"
                    )

                # Step 0a: Verify all orders cleared (FAIL_CLOSED: must confirm no working orders)
                # Cancel is async - orders can still fill during close/poll window if we don't verify
                if cancelled > 0 or open_order_count > 0:
                    orders_cleared, clear_error = await self._verify_orders_cleared(
                        symbol, user_id, user_role, timeout_s=10.0
                    )
                    if not orders_cleared:
                        ui.notify(
                            f"Reverse blocked: {clear_error} (FAIL-CLOSED)",
                            type="negative",
                        )
                        return

                # Step 0b: Fetch current position (authoritative size AND side)
                # This ensures the open leg matches actual closed qty and correct direction
                try:
                    positions_resp = await self._client.fetch_positions(
                        user_id, role=user_role, strategies=self._strategies
                    )
                    positions_list = positions_resp.get("positions", [])
                    current_pos = next(
                        (p for p in positions_list if p.get("symbol", "").upper() == symbol.upper()),
                        None,
                    )
                    if current_pos:
                        # Parse qty robustly (handles float strings like "100.0" from API)
                        raw_qty = int(float(current_pos.get("qty", 0)))
                        authoritative_qty = abs(raw_qty)
                    if current_pos and authoritative_qty > 0:
                        # Derive side from position qty sign (positive = long, negative = short)
                        authoritative_side = "buy" if raw_qty > 0 else "sell"
                        authoritative_opposite_side = "sell" if raw_qty > 0 else "buy"
                        # Warn if UI side disagrees with authoritative side
                        if authoritative_side != current_side:
                            ui.notify(
                                f"Warning: Position side ({authoritative_side}) differs from UI ({current_side})",
                                type="warning",
                            )
                    else:
                        # Position already flat or not found - can't reverse
                        ui.notify(
                            f"Reverse blocked: no position found for {symbol}",
                            type="negative",
                        )
                        return
                except Exception as pos_exc:
                    ui.notify(
                        f"Reverse blocked: failed to fetch position ({pos_exc})",
                        type="negative",
                    )
                    return

                # Step 1: Close position using authoritative qty
                # Capture response to detect if backend clamped the qty
                actual_closed_qty = authoritative_qty  # Default to requested
                try:
                    close_response = await self._client.close_position(
                        symbol=symbol,
                        qty=authoritative_qty,
                        reason="Reverse position - close leg",
                        requested_by=user_id,
                        requested_at=datetime.now(UTC).isoformat(),
                        user_id=user_id,
                        role=user_role,
                        strategies=self._strategies,
                    )
                    # Check if backend returned a different qty (clamped)
                    if close_response and isinstance(close_response, dict):
                        response_qty = close_response.get("qty") or close_response.get("filled_qty")
                        if response_qty is not None:
                            try:
                                # Convert via float first (handles "100.0" from API)
                                response_qty_int = abs(int(float(response_qty)))
                                if response_qty_int != authoritative_qty:
                                    ui.notify(
                                        f"Warning: Close qty adjusted {authoritative_qty} → {response_qty_int}",
                                        type="warning",
                                    )
                                    actual_closed_qty = response_qty_int
                            except (ValueError, TypeError) as exc:
                                logger.warning(
                                    "unparseable_close_response_qty",
                                    extra={
                                        "symbol": symbol,
                                        "response_qty": response_qty,
                                        "error": str(exc),
                                    },
                                )
                                # Keep authoritative_qty as fallback
                except Exception as close_exc:
                    ui.notify(f"Close failed: {close_exc}", type="negative")
                    return

                # Step 2: Poll until flat with FAIL_CLOSED strictness
                # Flat confirmation: position missing OR qty=0 (APIs may filter WHERE qty != 0)
                # Require 2 consecutive confirmations to guard against transient states
                start_time = datetime.now(UTC)
                confirmed_flat = False
                consecutive_flat_polls = 0  # Require 2 consecutive confirmations for safety
                while (datetime.now(UTC) - start_time).total_seconds() < CLOSE_CONFIRMATION_TIMEOUT_S:
                    try:
                        positions_resp = await self._client.fetch_positions(
                            user_id, role=user_role, strategies=self._strategies
                        )
                        positions_list = positions_resp.get("positions", [])
                        symbol_pos = next(
                            (
                                p
                                for p in positions_list
                                if p.get("symbol", "").upper() == symbol.upper()
                            ),
                            None,
                        )
                        if symbol_pos is None:
                            # Missing symbol = flat (API filters out zero-qty positions)
                            # Increment counter to confirm across consecutive polls
                            consecutive_flat_polls += 1
                            if consecutive_flat_polls >= 2:
                                confirmed_flat = True
                                break
                        else:
                            # Parse qty robustly (handles float strings like "100.0" from API)
                            poll_qty = abs(int(float(symbol_pos.get("qty", 0))))
                            if poll_qty == 0:
                                # Explicit qty=0 - position confirmed flat
                                consecutive_flat_polls += 1
                                if consecutive_flat_polls >= 2:
                                    confirmed_flat = True
                                    break
                            else:
                                # Position still open, reset consecutive counter
                                consecutive_flat_polls = 0
                    except Exception:
                        # Fetch error - reset consecutive counter, continue polling
                        consecutive_flat_polls = 0
                    await asyncio.sleep(POLL_INTERVAL_S)

                if not confirmed_flat:
                    ui.notify(
                        f"Reverse timeout: position not confirmed flat after {CLOSE_CONFIRMATION_TIMEOUT_S}s",
                        type="negative",
                    )
                    return

                # Step 3: Re-verify safety before open leg
                final_check = await self._safety.check_with_api_verification(
                    policy=SafetyPolicy.FAIL_CLOSED,
                    cached_connection_state=cached_connection_state,
                )
                if not final_check.allowed:
                    ui.notify(
                        f"Reverse aborted before open: {final_check.reason}",
                        type="negative",
                    )
                    return

                # Step 3b: Re-validate price freshness (strict_timestamp for FAIL_CLOSED reverse)
                fresh_price, price_error = await self._get_fresh_price_with_fallback(
                    symbol, user_id, user_role, strict_timestamp=True
                )
                if fresh_price is None:
                    ui.notify(
                        f"Reverse aborted: {price_error} (position is now flat)",
                        type="negative",
                    )
                    return

                # Step 3c: Re-run fat-finger validation with fresh price and actual closed qty
                # Use actual_closed_qty (from Step 1 response), which accounts for backend clamping
                # This ensures open leg matches what was actually closed, not what we requested.
                fresh_adv = await self._get_adv(symbol, user_id, user_role)
                fresh_validation = self._validator.validate(
                    symbol=symbol, qty=actual_closed_qty, price=fresh_price, adv=fresh_adv
                )
                if fresh_validation.blocked:
                    ui.notify(
                        f"Reverse aborted: {fresh_validation.warnings[0].message} (position is now flat)",
                        type="negative",
                    )
                    return

                # Step 4: Submit opposite order using authoritative side and actual closed qty
                # CRITICAL: Use actual_closed_qty from close_position response to ensure symmetric reverse
                try:
                    order_data = {
                        "symbol": symbol,
                        "side": authoritative_opposite_side,  # Use authoritative side, not UI
                        "qty": actual_closed_qty,  # Use actual closed qty, not requested
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
                    ui.notify(
                        f"Reversed {symbol}: now {authoritative_opposite_side} {actual_closed_qty}",
                        type="positive",
                    )
                except Exception as exc:
                    ui.notify(
                        f"Opposite order failed: {exc} (position is now flat)",
                        type="negative",
                    )
            finally:
                reverse_submitting = False

        # Show confirmation dialog
        with ui.dialog() as dialog, ui.card().classes("p-4"):
            ui.label(f"Reverse {symbol}?").classes("text-lg font-bold")
            msg = f"Close {abs(validated_qty)} {current_side} THEN open {abs(validated_qty)} {opposite_side}"
            if open_order_count > 0:
                msg = f"Cancel {open_order_count} orders, " + msg
            ui.label(msg)
            ui.label("Note: Opposite order submits only after close confirms.").classes(
                "text-sm text-gray-500"
            )

            with ui.row().classes("gap-4 mt-4"):

                async def on_confirm() -> None:
                    dialog.close()
                    await execute_reverse()

                ui.button("Reverse", on_click=on_confirm).classes(
                    "bg-orange-600 text-white"
                )
                ui.button("Cancel", on_click=dialog.close)

        dialog.open()


__all__ = ["FlattenControls"]
