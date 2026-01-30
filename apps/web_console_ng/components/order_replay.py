"""Order replay/duplicate functionality.

This module provides the ability to replay previous orders by pre-filling
the order ticket with values from filled/cancelled orders.

Example:
    handler = OrderReplayHandler()

    if handler.can_replay(order):
        await handler.on_replay(
            order=order,
            user_id=user_id,
            user_role=user_role,
            on_prefill_order_ticket=order_ticket.prefill_order,
        )
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

from nicegui import ui

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayableOrder:
    """Order data for replay (immutable).

    Attributes:
        symbol: Trading symbol
        side: Order side ("buy" or "sell")
        qty: Order quantity as Decimal (Order Ticket truncates to int)
        order_type: Order type
        limit_price: Limit price for limit/stop_limit orders
        stop_price: Stop price for stop/stop_limit orders
        time_in_force: Time in force
        original_order_id: Original client_order_id for audit trail
    """

    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: Literal["day", "gtc", "ioc", "fok"]
    original_order_id: str


class OrderReplayHandler:
    """Handle order replay/duplicate functionality.

    Allows users to quickly replay previous orders by pre-filling the
    order ticket with values from filled/cancelled orders.
    """

    # Terminal statuses that allow replay
    REPLAYABLE_STATUSES = {"filled", "canceled", "cancelled", "expired", "rejected"}

    def can_replay(self, order: dict[str, object]) -> bool:
        """Check if order can be replayed.

        Args:
            order: Order data dict

        Returns:
            True if order has a terminal status that allows replay
        """
        status = str(order.get("status", "")).lower()
        return status in self.REPLAYABLE_STATUSES

    def extract_replay_data(self, order: dict[str, object]) -> ReplayableOrder | None:
        """Extract replay data from filled/cancelled order.

        Args:
            order: Order data dict

        Returns:
            ReplayableOrder with extracted data, or None if required fields missing
        """
        try:
            # Required fields
            symbol = order.get("symbol")
            side = order.get("side")
            original_order_id = order.get("client_order_id")

            if not symbol or not isinstance(symbol, str):
                logger.warning(
                    "replay_extract_missing_symbol", extra={"order": str(order)[:200]}
                )
                return None
            if not side or side not in ("buy", "sell"):
                logger.warning(
                    "replay_extract_invalid_side",
                    extra={"side": side, "order": str(order)[:200]},
                )
                return None
            if not original_order_id or not isinstance(original_order_id, str):
                logger.warning(
                    "replay_extract_missing_order_id", extra={"order": str(order)[:200]}
                )
                return None

            # Quantity - use qty or fall back to filled_qty
            raw_qty = order.get("qty") or order.get("filled_qty") or 0
            try:
                qty = Decimal(str(raw_qty))
            except (InvalidOperation, ValueError):
                logger.warning(
                    "replay_extract_invalid_qty",
                    extra={"qty": raw_qty, "order": str(order)[:200]},
                )
                return None

            if qty <= 0:
                logger.warning(
                    "replay_extract_zero_qty",
                    extra={"qty": str(qty), "order": str(order)[:200]},
                )
                return None

            # Order type with default
            order_type_raw = order.get("type") or order.get("order_type") or "market"
            if order_type_raw not in ("market", "limit", "stop", "stop_limit"):
                order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
            else:
                order_type = order_type_raw  # type: ignore[assignment]

            # Optional prices
            limit_price: Decimal | None = None
            stop_price: Decimal | None = None

            if order.get("limit_price"):
                try:
                    limit_price = Decimal(str(order["limit_price"]))
                except (InvalidOperation, ValueError):
                    pass  # Leave as None

            if order.get("stop_price"):
                try:
                    stop_price = Decimal(str(order["stop_price"]))
                except (InvalidOperation, ValueError):
                    pass  # Leave as None

            # Time in force with default
            tif_raw = order.get("time_in_force") or "day"
            if tif_raw not in ("day", "gtc", "ioc", "fok"):
                tif: Literal["day", "gtc", "ioc", "fok"] = "day"
            else:
                tif = tif_raw  # type: ignore[assignment]

            return ReplayableOrder(
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=tif,
                original_order_id=original_order_id,
            )
        except Exception as exc:
            logger.warning(
                "replay_extract_failed",
                extra={"error": str(exc), "order": str(order)[:200]},
            )
            return None

    async def on_replay(
        self,
        order: dict[str, object],
        user_id: str,
        user_role: str,
        on_prefill_order_ticket: Callable[[ReplayableOrder], None],
    ) -> None:
        """Handle replay button click - pre-fill order ticket.

        Args:
            order: Order data to replay
            user_id: User ID for audit
            user_role: User role for authorization
            on_prefill_order_ticket: Callback to prefill order ticket
        """
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
        original_qty = replay_data.qty
        rounded_qty = int(original_qty.to_integral_value(rounding=ROUND_DOWN))

        if rounded_qty <= 0:
            ui.notify("Cannot replay: quantity rounds to zero", type="warning")
            return

        # Check if rounding occurred and notify user
        qty_was_rounded = original_qty != Decimal(rounded_qty)
        if qty_was_rounded:
            ui.notify(
                f"Note: quantity adjusted from {original_qty} to {rounded_qty} "
                "(Order Ticket is integer-only)",
                type="info",
            )
            logger.info(
                "replay_qty_truncated",
                extra={
                    "original_qty": str(original_qty),
                    "rounded_qty": rounded_qty,
                    "symbol": replay_data.symbol,
                },
            )

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
        ui.notify(
            f"Order form pre-filled with {replay_data.symbol} {replay_data.side}",
            type="info",
        )

        # Audit log (field name matches acceptance criteria)
        logger.info(
            "order_replay_prefilled",
            extra={
                "user_id": user_id,
                "symbol": replay_data.symbol,
                "replayed_from": replay_data.original_order_id,
                "strategy_id": "manual",
            },
        )


__all__ = ["OrderReplayHandler", "ReplayableOrder"]
