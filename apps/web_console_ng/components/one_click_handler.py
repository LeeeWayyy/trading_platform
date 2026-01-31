"""One-click trading handler for DOM ladder and price chart.

This module provides instant order placement via modifier key clicks:
- Shift+Click: Limit order at clicked price
- Ctrl+Click: Market order
- Alt+Click: Cancel order(s) at price level

Example:
    handler = OneClickHandler(
        trading_client=client,
        fat_finger_validator=validator,
        safety_gate=gate,
        state_manager=state_manager,
        user_id=user_id,
        user_role=user_role,
        strategies=["alpha_baseline"],
    )

    # Shift+Click: Instant limit order
    await handler.on_shift_click("AAPL", Decimal("150.00"), "buy")

    # Ctrl+Click: Instant market order
    await handler.on_ctrl_click("AAPL", "sell")

    # Alt+Click: Cancel at price level
    await handler.on_alt_click("AAPL", Decimal("150.00"), working_orders)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal

from nicegui import ui

from apps.web_console_ng.components.safety_gate import SafetyGate, SafetyPolicy
from apps.web_console_ng.utils.orders import (
    is_cancellable_order_id,
    validate_symbol,
)

if TYPE_CHECKING:
    from apps.web_console_ng.components.fat_finger_validator import FatFingerValidator
    from apps.web_console_ng.core.client import AsyncTradingClient
    from apps.web_console_ng.core.state_manager import UserStateManager

logger = logging.getLogger(__name__)

# Safety thresholds
PRICE_STALENESS_THRESHOLD_S = 30  # Block if price older than 30s
PRICE_MATCH_TOLERANCE = Decimal("0.01")  # $0.01 tolerance for order matching


@dataclass
class OneClickConfig:
    """One-click trading configuration."""

    enabled: bool = False  # Opt-in required
    daily_notional_cap: Decimal = field(default_factory=lambda: Decimal("500000"))
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
        state_manager: UserStateManager,
        user_id: str,
        user_role: str,
        strategies: list[str] | None = None,
    ):
        """Initialize one-click handler.

        Args:
            trading_client: Client for API calls
            fat_finger_validator: Validator for position size checks
            safety_gate: Safety gate for policy-based checks
            state_manager: Manager for persisting preferences
            user_id: User ID for API calls
            user_role: User role for authorization
            strategies: Strategy scope for multi-strategy users
        """
        self._client = trading_client
        self._validator = fat_finger_validator
        self._safety = safety_gate
        self._state_manager = state_manager
        self._user_id = user_id
        self._user_role = user_role
        self._strategies = strategies
        self._config = OneClickConfig()
        self._last_click_time: float = 0.0

        # Cached prices dict - to be set by OrderEntryContext
        self._cached_prices: dict[str, tuple[Decimal, datetime]] = {}

        # Cached safety state - to be set by OrderEntryContext
        self._cached_kill_switch: bool | None = None
        self._cached_connection_state: str | None = None
        self._cached_circuit_breaker: bool | None = None

    def set_cached_prices(self, prices: dict[str, tuple[Decimal, datetime]]) -> None:
        """Update cached prices from OrderEntryContext."""
        self._cached_prices = prices

    def set_cached_safety_state(
        self,
        kill_switch: bool | None,
        connection_state: str | None,
        circuit_breaker: bool | None,
    ) -> None:
        """Update cached safety state from OrderEntryContext."""
        self._cached_kill_switch = kill_switch
        self._cached_connection_state = connection_state
        self._cached_circuit_breaker = circuit_breaker

    def get_config(self) -> OneClickConfig:
        """Get current one-click configuration."""
        return self._config

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable one-click trading."""
        self._config.enabled = enabled

    def set_daily_notional_cap(self, cap: Decimal) -> None:
        """Set daily notional cap."""
        self._config.daily_notional_cap = cap

    def set_default_qty(self, qty: int) -> None:
        """Set default quantity for one-click orders."""
        self._config.default_qty = qty

    async def _get_adv(self, symbol: str) -> int | None:
        """Get Average Daily Volume for fat finger validation.

        Returns None if unavailable (validator handles None gracefully).
        """
        try:
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
        """Get fresh price from cached prices with staleness check.

        Returns:
            Tuple of (price, error_message) - price is None if stale/missing
        """
        if not self._cached_prices or symbol not in self._cached_prices:
            return None, "No price data available"

        try:
            price, timestamp = self._cached_prices[symbol]
            # Validate timestamp is a datetime (guards against malformed cache entries)
            if not isinstance(timestamp, datetime):
                return None, "Price timestamp invalid (not datetime)"
            # Validate price is positive and finite
            if not isinstance(price, Decimal) or price <= 0 or not price.is_finite():
                return None, f"Invalid price value: {price}"
            age = (datetime.now(UTC) - timestamp).total_seconds()
            if age > PRICE_STALENESS_THRESHOLD_S:
                return None, f"Price data stale ({age:.0f}s old)"
            return price, ""
        except (TypeError, ValueError) as exc:
            return None, f"Price cache malformed: {exc}"

    async def _get_daily_notional(self) -> Decimal:
        """Get today's accumulated notional from persistent state."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        notional_key = f"one_click_notional_{today}"
        state = await self._state_manager.restore_state()
        preferences = state.get("preferences", {}) if state else {}
        value = preferences.get(notional_key)
        return Decimal(str(value)) if value else Decimal("0")

    async def _update_daily_notional(self, new_total: Decimal) -> None:
        """Update today's accumulated notional in persistent state.

        CRITICAL: Only call AFTER successful order submission to prevent drift.
        Server-side is the AUTHORITATIVE cap enforcer with atomic Redis validation.
        UI-side tracking is best-effort UX optimization.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        notional_key = f"one_click_notional_{today}"
        await self._state_manager.save_preferences(notional_key, str(new_total))

    async def _show_first_use_confirmation(self) -> bool:
        """Show first-use confirmation dialog.

        Returns:
            True if user confirmed, False otherwise
        """
        confirmed = False

        with ui.dialog() as dialog, ui.card().classes("p-4 w-96"):
            ui.label("One-Click Trading").classes("text-lg font-bold")
            ui.label(
                "One-click trading allows instant order placement without confirmation. "
                "Orders will be submitted immediately when you use modifier keys."
            ).classes("text-sm")
            ui.label("• Shift+Click: Limit order at price").classes("text-sm")
            ui.label("• Ctrl+Click: Market order").classes("text-sm")
            ui.label("• Alt+Click: Cancel orders at price").classes("text-sm")

            ui.label(
                "This feature is intended for experienced traders. "
                "Fat finger limits and daily notional caps still apply."
            ).classes("text-sm text-warning mt-2")

            with ui.row().classes("gap-4 mt-4"):

                def on_confirm() -> None:
                    nonlocal confirmed
                    confirmed = True
                    dialog.close()

                def on_cancel() -> None:
                    dialog.close()

                ui.button("I Understand", on_click=on_confirm).classes(
                    "bg-blue-600 text-white"
                )
                ui.button("Cancel", on_click=on_cancel)

        dialog.open()
        await dialog  # Wait for dialog to close
        return confirmed

    async def on_shift_click(
        self,
        symbol: str,
        price: Decimal,
        side: Literal["buy", "sell"],
    ) -> None:
        """Shift+Click: Instant limit order at clicked price.

        Args:
            symbol: Trading symbol
            price: Limit price
            side: Order side
        """
        # Enforce role/enablement at entry point
        if not self.is_enabled():
            ui.notify("One-click trading is not enabled", type="warning")
            return
        await self._execute_one_click(
            symbol,
            price,
            side,
            "limit",
            mode="shift_click_limit",
        )

    async def on_ctrl_click(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
    ) -> None:
        """Ctrl+Click: Instant market order.

        Note: Requires fresh price for fat finger validation and notional cap.

        Args:
            symbol: Trading symbol
            side: Order side
        """
        # Enforce role/enablement at entry point
        if not self.is_enabled():
            ui.notify("One-click trading is not enabled", type="warning")
            return

        # Get fresh price (required for market orders)
        price, error = self._get_fresh_price(symbol)
        if price is None:
            ui.notify(f"Cannot place market order: {error}", type="negative")
            return

        await self._execute_one_click(
            symbol,
            price,
            side,
            "market",
            mode="ctrl_click_market",
        )

    async def on_alt_click(
        self,
        symbol: str,
        price: Decimal,
        working_orders: list[dict[str, Any]],
        is_read_only: bool = False,
    ) -> None:
        """Alt+Click: Cancel order(s) at price level.

        Matches working orders by symbol + limit_price with tolerance.
        Note: Alt+Click cancel doesn't require one-click to be enabled (always allowed),
        but respects viewer role. Read-only mode allows cancel with warning (risk-reducing).

        Args:
            symbol: Trading symbol
            price: Price level to cancel at
            working_orders: List of working orders to search
            is_read_only: Whether connection is in read-only mode
        """
        # Permission checks
        if self._user_role == "viewer":
            ui.notify("Viewers cannot cancel orders", type="warning")
            return

        # CRITICAL: Cancel is risk-reducing (fail-open) - allow in read-only with warning
        # Also warn on unknown/None/"UNKNOWN" connection state to maintain visibility
        conn_unknown = (
            self._cached_connection_state is None
            or (self._cached_connection_state or "").upper() == "UNKNOWN"
        )
        if is_read_only or conn_unknown:
            state_desc = (
                self._cached_connection_state
                if self._cached_connection_state and self._cached_connection_state.upper() != "UNKNOWN"
                else "unknown"
            )
            ui.notify(
                f"Warning: connection {state_desc} - cancel may be delayed",
                type="warning",
            )
            # Proceed with cancel (don't block risk-reducing action)

        # Find orders at this price level (limit orders only)
        def _price_matches(order: dict[str, Any], target_price: Decimal) -> bool:
            """Check if order's limit_price matches target within tolerance."""
            limit_price_raw = order.get("limit_price")
            if limit_price_raw is None:
                return False
            try:
                order_price = Decimal(str(limit_price_raw))
                return abs(order_price - target_price) <= PRICE_MATCH_TOLERANCE
            except (InvalidOperation, ValueError):
                logger.warning(
                    "alt_click_invalid_limit_price",
                    extra={
                        "order_id": order.get("client_order_id"),
                        "limit_price": limit_price_raw,
                    },
                )
                return False

        orders_at_level = [
            o
            for o in working_orders
            if o.get("symbol") == symbol and _price_matches(o, price)
        ]

        if not orders_at_level:
            ui.notify(f"No orders at ${price}", type="info")
            return

        # Separate cancellable from uncancellable orders
        cancellable_orders = [
            o for o in orders_at_level if is_cancellable_order_id(o.get("client_order_id"))
        ]
        skipped = len(orders_at_level) - len(cancellable_orders)

        # Cancel orders concurrently with bounded concurrency
        cancelled = 0
        failed = 0
        if cancellable_orders:
            semaphore = asyncio.Semaphore(5)

            async def _cancel_one(order: dict[str, Any]) -> bool:
                order_id = order.get("client_order_id")
                async with semaphore:
                    try:
                        await self._client.cancel_order(
                            order_id,
                            self._user_id,
                            role=self._user_role,
                            strategies=self._strategies,
                            reason="Alt-click cancel at price level",
                            requested_by=self._user_id,
                            requested_at=datetime.now(UTC).isoformat(),
                        )
                        return True
                    except Exception as exc:
                        logger.warning(
                            "alt_click_cancel_failed",
                            extra={"order_id": order_id, "error": str(exc)},
                        )
                        return False

            results = await asyncio.gather(*[_cancel_one(o) for o in cancellable_orders])
            cancelled = sum(1 for r in results if r)
            failed = len(results) - cancelled

        # Report results with accurate status
        if failed > 0:
            ui.notify(
                f"Cancelled {cancelled}, failed {failed} at ${price}",
                type="warning" if cancelled > 0 else "negative",
            )
        elif skipped > 0:
            ui.notify(
                f"Cancelled {cancelled}, skipped {skipped} (invalid ID) at ${price}",
                type="info",
            )
        else:
            ui.notify(f"Cancelled {cancelled} order(s) at ${price}", type="info")

    async def handle_one_click(self, args: dict[str, Any]) -> None:
        """Handle one-click event from JavaScript CustomEvent.

        Args:
            args: Event detail dict with mode, symbol, price, side
        """
        mode = args.get("mode")
        symbol_raw = args.get("symbol")
        price_raw = args.get("price")
        side_raw = args.get("side")

        if not symbol_raw:
            logger.warning("handle_one_click_missing_symbol", extra={"args": args})
            return

        # Validate and normalize symbol to prevent path traversal attacks
        symbol, symbol_error = validate_symbol(symbol_raw)
        if symbol is None:
            logger.warning(
                "handle_one_click_invalid_symbol",
                extra={"args": args, "error": symbol_error},
            )
            ui.notify(f"Invalid symbol: {symbol_error}", type="negative")
            return

        if mode == "shift_limit":
            if price_raw is None or side_raw is None:
                logger.warning(
                    "handle_one_click_shift_missing_params", extra={"args": args}
                )
                return
            try:
                price = Decimal(str(price_raw))
            except (InvalidOperation, ValueError):
                ui.notify("Invalid price", type="negative")
                return
            if side_raw not in ("buy", "sell"):
                ui.notify("Invalid side", type="negative")
                return
            await self.on_shift_click(symbol, price, side_raw)

        elif mode == "ctrl_market":
            if side_raw is None:
                logger.warning(
                    "handle_one_click_ctrl_missing_side", extra={"args": args}
                )
                return
            if side_raw not in ("buy", "sell"):
                ui.notify("Invalid side", type="negative")
                return
            await self.on_ctrl_click(symbol, side_raw)

        elif mode == "alt_cancel":
            if price_raw is None:
                logger.warning(
                    "handle_one_click_alt_missing_price", extra={"args": args}
                )
                return
            try:
                price = Decimal(str(price_raw))
            except (InvalidOperation, ValueError):
                ui.notify("Invalid price", type="negative")
                return
            # Fetch working orders for the symbol
            try:
                response = await self._client.fetch_open_orders(
                    self._user_id,
                    role=self._user_role,
                    strategies=self._strategies,
                )
                working_orders = response.get("orders", [])
            except Exception as exc:
                ui.notify(f"Failed to fetch orders: {exc}", type="negative")
                return

            # Determine read-only state (case-insensitive)
            conn_state_upper = (self._cached_connection_state or "").upper()
            is_read_only = conn_state_upper in {
                "DISCONNECTED",
                "RECONNECTING",
                "DEGRADED",
            }
            await self.on_alt_click(symbol, price, working_orders, is_read_only)

        else:
            logger.warning("handle_one_click_unknown_mode", extra={"mode": mode})

    async def _execute_one_click(
        self,
        symbol: str,
        price: Decimal,
        side: Literal["buy", "sell"],
        order_type: str,
        *,
        mode: str,
    ) -> None:
        """Execute one-click order with all safety checks.

        Safety: FAIL-CLOSED policy (risk-increasing action)

        Args:
            symbol: Trading symbol
            price: Order price
            side: Order side
            order_type: "market" or "limit"
            mode: Audit mode identifier
        """
        # 1. Check cooldown
        if not self._check_cooldown():
            ui.notify("Too fast - please wait", type="warning")
            return

        # 2. Check safety gates with FAIL-CLOSED policy (cached first for instant feedback)
        result = await self._safety.check(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_kill_switch=self._cached_kill_switch,
            cached_connection_state=self._cached_connection_state,
            cached_circuit_breaker=self._cached_circuit_breaker,
            require_connected=True,
        )
        if not result.allowed:
            ui.notify(f"Order blocked: {result.reason}", type="negative")
            return

        # 2b. Fresh API verification for FAIL-CLOSED actions
        fresh_result = await self._safety.check_with_api_verification(
            policy=SafetyPolicy.FAIL_CLOSED,
            cached_connection_state=self._cached_connection_state,
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
                type="negative",
            )
            return

        # 5. Fat finger validation (with ADV for % of volume check)
        adv = await self._get_adv(symbol)
        validation = self._validator.validate(symbol=symbol, qty=qty, price=price, adv=adv)
        if validation.blocked:
            ui.notify(
                f"Fat finger blocked: {validation.warnings[0].message}", type="negative"
            )
            return

        # 6. Submit order via manual order endpoint (consistent audit trail)
        order_data: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "order_origin": "one_click",  # Server-side notional cap enforcement key
            "reason": f"One-click {mode}",
            "requested_by": self._user_id,
            "requested_at": datetime.now(UTC).isoformat(),
        }
        if order_type == "limit":
            order_data["limit_price"] = str(price)

        try:
            submit_result = await self._client.submit_manual_order(
                order_data=order_data,
                user_id=self._user_id,
                role=self._user_role,
                strategies=self._strategies,
            )
        except Exception as exc:
            logger.error("one_click_order_failed", extra={"error": str(exc)})
            ui.notify(f"Order failed: {exc}", type="negative")
            return

        # Order succeeded - update notional tracking (warn on failure, don't label order failed)
        new_total = current_notional + notional
        try:
            await self._update_daily_notional(new_total)
        except Exception as notional_exc:
            logger.warning(
                "one_click_notional_save_failed",
                extra={"error": str(notional_exc), "notional": str(new_total)},
            )
            ui.notify("Warning: Order succeeded but notional tracking failed", type="warning")

        # Brief toast confirmation
        order_id = str(submit_result.get("client_order_id", ""))[-6:]
        price_display = "MKT" if order_type == "market" else f"${price}"
        ui.notify(
            f"✓ {side.upper()} {qty} {symbol} @ {price_display} (#{order_id})",
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
                "client_order_id": submit_result.get("client_order_id"),
                "daily_notional": str(new_total),
                "strategy_id": "manual_controls_one_click",
            },
        )


__all__ = ["OneClickHandler", "OneClickConfig"]
