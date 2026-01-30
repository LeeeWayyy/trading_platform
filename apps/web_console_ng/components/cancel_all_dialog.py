"""Cancel all orders dialog with filtering.

This module provides a dialog for bulk order cancellation with symbol and side
filtering options.

Example:
    dialog = CancelAllDialog(
        orders=open_orders,
        trading_client=client,
        user_id=user_id,
        user_role=user_role,
        strategies=["alpha_baseline"],
    )
    await dialog.show()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from apps.web_console_ng.core.client import AsyncTradingClient

logger = logging.getLogger(__name__)

# ID prefixes that indicate uncancellable orders
SYNTHETIC_ID_PREFIX = "SYNTH-"
FALLBACK_ID_PREFIX = "FALLBACK-"


class CancelAllDialog:
    """Cancel all orders dialog with filtering.

    Note: Side filtering uses per-order cancel (backend cancel-all is per-symbol only).

    Safety:
    - Viewer role blocked (consistent with on_cancel_order)
    - Read-only mode NOT blocked (cancel is fail-open, risk-reducing)
    - Uses bounded concurrency for large order counts
    """

    # Bounded concurrency to avoid overwhelming backend/network
    MAX_CONCURRENT_CANCELS = 5

    def __init__(
        self,
        orders: list[dict[str, object]],
        trading_client: AsyncTradingClient,
        user_id: str,
        user_role: str,
        is_read_only: bool = False,
        strategies: list[str] | None = None,
    ):
        """Initialize cancel all dialog.

        Args:
            orders: List of open orders from fetch_open_orders response
            trading_client: Client for API calls
            user_id: User ID for API calls
            user_role: User role for authorization
            is_read_only: Connection read-only state (not used for cancel - fail-open)
            strategies: Strategy scope for multi-strategy users
        """
        self._orders = orders
        self._client = trading_client
        self._user_id = user_id
        self._user_role = user_role
        self._is_read_only = is_read_only  # Kept for documentation, not used
        self._strategies = strategies

    def _check_permissions(self) -> tuple[bool, str]:
        """Check if user can cancel orders.

        Returns:
            Tuple of (allowed, reason). Reason is empty string if allowed.

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
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        """Filter orders and separate valid from invalid IDs.

        Args:
            symbol_filter: "All Symbols" or specific symbol
            side_filter: "All Sides", "Buy Only", or "Sell Only"

        Returns:
            Tuple of (valid_orders, skipped_orders)
        """
        filtered = list(self._orders)

        if symbol_filter != "All Symbols":
            filtered = [o for o in filtered if o.get("symbol") == symbol_filter]

        if side_filter == "Buy Only":
            filtered = [o for o in filtered if o.get("side") == "buy"]
        elif side_filter == "Sell Only":
            filtered = [o for o in filtered if o.get("side") == "sell"]

        # Separate valid IDs from uncancellable orders (missing/synthetic/fallback IDs)
        valid: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for order in filtered:
            order_id = order.get("client_order_id")
            # Skip falsy IDs (missing/empty) same as on_cancel_order policy
            if not order_id:
                skipped.append(order)
            elif isinstance(order_id, str) and (
                order_id.startswith(SYNTHETIC_ID_PREFIX)
                or order_id.startswith(FALLBACK_ID_PREFIX)
            ):
                skipped.append(order)
            else:
                valid.append(order)

        return valid, skipped

    def _unique_symbols(self) -> list[str]:
        """Get unique symbols from current orders list."""
        symbols = set()
        for o in self._orders:
            symbol = o.get("symbol")
            if symbol and isinstance(symbol, str):
                symbols.add(symbol)
        return sorted(symbols)

    async def _execute_cancel_all(
        self, orders: list[dict[str, object]]
    ) -> tuple[int, int]:
        """Execute cancellation with partial failure reporting and bounded concurrency.

        Uses semaphore to limit concurrent cancels (avoids overwhelming backend).

        Args:
            orders: List of orders to cancel

        Returns:
            Tuple of (success_count, failure_count)
        """
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CANCELS)

        async def cancel_one(order: dict[str, object]) -> bool:
            async with semaphore:
                order_id = order.get("client_order_id")
                if not order_id or not isinstance(order_id, str):
                    # Should not happen (filtered out earlier), but handle gracefully
                    return False
                try:
                    await self._client.cancel_order(
                        order_id,
                        self._user_id,
                        role=self._user_role,
                        strategies=self._strategies,
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
                        },
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

        # Double-submit protection state
        submitting = False

        with ui.dialog() as dialog, ui.card().classes("p-4 w-96"):
            ui.label("Cancel Orders").classes("text-lg font-bold")

            # Filter controls
            with ui.row().classes("gap-4"):
                symbol_select = ui.select(
                    options=["All Symbols"] + self._unique_symbols(),
                    value="All Symbols",
                    label="Symbol",
                )
                side_select = ui.select(
                    options=["All Sides", "Buy Only", "Sell Only"],
                    value="All Sides",
                    label="Side",
                )

            # Preview with skipped count
            count_label = ui.label()
            skipped_label = ui.label().classes("text-warning text-xs")

            def update_preview() -> None:
                valid, skipped = self._filter_orders(
                    str(symbol_select.value), str(side_select.value)
                )
                count_label.text = f"Will cancel {len(valid)} order(s)"
                if skipped:
                    skipped_label.text = (
                        f"⚠️ {len(skipped)} order(s) cannot be cancelled (missing ID)"
                    )
                else:
                    skipped_label.text = ""

            symbol_select.on_value_change(update_preview)
            side_select.on_value_change(update_preview)
            update_preview()  # Initial update

            # Action buttons
            with ui.row().classes("gap-4 mt-4"):

                async def confirm() -> None:
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
                                self._user_id,
                                role=self._user_role,
                                strategies=self._strategies,
                            )
                            fresh_orders = fresh_response.get("orders", [])
                            if isinstance(fresh_orders, list):
                                # Re-apply filters to fresh data
                                self._orders = fresh_orders
                        except Exception as fetch_exc:
                            ui.notify(
                                f"Failed to refresh orders: {fetch_exc}", type="warning"
                            )
                            # Proceed with stale data but warn user
                            logger.warning(
                                "cancel_all_dialog_fetch_failed",
                                extra={"error": str(fetch_exc)},
                            )

                        valid, skipped = self._filter_orders(
                            str(symbol_select.value), str(side_select.value)
                        )
                        if not valid:
                            ui.notify("No orders to cancel", type="warning")
                            return

                        success, failed = await self._execute_cancel_all(valid)

                        if failed == 0:
                            ui.notify(f"Cancelled {success} order(s)", type="positive")
                        else:
                            ui.notify(
                                f"Cancelled {success}, failed {failed} order(s)",
                                type="warning",
                            )

                        # Audit log entry for bulk cancellation
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
                            },
                        )

                        dialog.close()
                    finally:
                        submitting = False
                        confirm_btn.enable()

                confirm_btn = ui.button("Cancel Orders", on_click=confirm).classes(
                    "bg-red-600 text-white"
                )
                ui.button("Close", on_click=dialog.close)

        dialog.open()


__all__ = ["CancelAllDialog"]
