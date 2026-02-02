"""Hierarchical orders transformer and UI helpers for TWAP parent/child orders."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.synthetic_id import FALLBACK_ID_PREFIX, SYNTHETIC_ID_PREFIX
from apps.web_console_ng.core.workspace_persistence import (
    DatabaseUnavailableError,
    get_workspace_service,
)

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "failed",
    "rejected",
    "replaced",
    "done_for_day",
    "blocked_kill_switch",
    "blocked_circuit_breaker",
}


@dataclass
class HierarchicalOrdersState:
    """State for hierarchical orders expansion persistence."""

    user_id: str | None
    panel_id: str = "hierarchical_orders"
    expanded_parent_ids: set[str] = field(default_factory=set)

    async def load(self, *, service: Any | None = None) -> None:
        if not self.user_id:
            return
        if service is None:
            service = get_workspace_service()
        try:
            state = await service.load_panel_state(self.user_id, self.panel_id)
        except DatabaseUnavailableError:
            logger.warning(
                "hierarchical_orders_state_load_db_unavailable",
                extra={"panel_id": self.panel_id},
            )
            return
        if not state:
            return
        raw_ids = state.get("expanded_parent_ids")
        if isinstance(raw_ids, list):
            self.expanded_parent_ids = {str(value) for value in raw_ids if value}

    async def save(self, *, service: Any | None = None) -> None:
        if not self.user_id:
            return
        if service is None:
            service = get_workspace_service()
        try:
            await service.save_panel_state(
                user_id=self.user_id,
                panel_id=self.panel_id,
                state={"expanded_parent_ids": sorted(self.expanded_parent_ids)},
            )
        except DatabaseUnavailableError:
            logger.warning(
                "hierarchical_orders_state_save_db_unavailable",
                extra={"panel_id": self.panel_id},
            )

    def update_expanded(self, expanded_ids: list[str]) -> None:
        self.expanded_parent_ids = {str(value) for value in expanded_ids if value}

    def prune(self, current_parent_ids: set[str]) -> bool:
        """Remove expansion entries for parents no longer present."""
        before = set(self.expanded_parent_ids)
        self.expanded_parent_ids.intersection_update(current_parent_ids)
        return before != self.expanded_parent_ids


def _coerce_number(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (TypeError, ValueError, InvalidOperation):
            return default
    return default


def _format_qty(value: Decimal) -> str:
    if value == value.to_integral():
        return f"{int(value)}"
    return f"{value.normalize()}"


def is_terminal_status(status: str | None) -> bool:
    return str(status or "").lower() in TERMINAL_STATUSES


def compute_parent_aggregates(
    parent: dict[str, Any], children: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute aggregate filled/total quantities for a parent order."""
    parent_qty = _coerce_number(parent.get("qty"))
    total_qty = parent_qty
    if total_qty == 0 and children:
        total_qty = sum((_coerce_number(child.get("qty")) for child in children), Decimal("0"))

    filled_qty = sum((_coerce_number(child.get("filled_qty")) for child in children), Decimal("0"))
    if filled_qty == 0:
        filled_qty = _coerce_number(parent.get("filled_qty"))

    parent["filled_qty_agg"] = filled_qty
    parent["total_qty_agg"] = total_qty
    if total_qty > 0:
        parent["progress"] = f"{_format_qty(filled_qty)}/{_format_qty(total_qty)} filled"
    else:
        parent["progress"] = "--"
    return parent


def _sort_children(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(child: dict[str, Any]) -> tuple[int, str]:
        slice_num = child.get("slice_num")
        if slice_num is None:
            return (1, str(child.get("client_order_id") or ""))
        try:
            return (0, f"{int(slice_num):06d}")
        except (TypeError, ValueError):
            return (1, str(child.get("client_order_id") or ""))

    return sorted(children, key=sort_key)


def transform_to_hierarchy(
    orders: list[dict[str, Any]],
    *,
    all_orders: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Transform flat orders into AG Grid tree data structure.

    Adds hierarchy_path and is_parent/is_child flags. Orphan children are
    returned as flat rows (single-element hierarchy_path).
    """
    orders = [dict(order) for order in orders]
    parent_map: dict[str, dict[str, Any]] = {}
    children_map: dict[str, list[dict[str, Any]]] = {}

    for order in orders:
        parent_id = order.get("parent_order_id")
        order_id = order.get("client_order_id")
        if parent_id:
            children_map.setdefault(str(parent_id), []).append(order)
        elif order_id:
            parent_map[str(order_id)] = order

    # Optionally pull in parent rows from the full snapshot.
    if all_orders is not None:
        for order in all_orders:
            order_id = order.get("client_order_id")
            if not order_id:
                continue
            if str(order_id) in parent_map:
                continue
            if order.get("parent_order_id"):
                continue
            parent_map[str(order_id)] = dict(order)

    results: list[dict[str, Any]] = []

    for parent_id, parent in parent_map.items():
        parent["is_parent"] = True
        parent["is_child"] = False
        parent["hierarchy_path"] = [parent_id]
        children = _sort_children(children_map.get(parent_id, []))
        parent["child_count"] = len(children)
        compute_parent_aggregates(parent, children)
        results.append(parent)
        for child in children:
            child_id = str(child.get("client_order_id") or "")
            child["is_parent"] = False
            child["is_child"] = True
            child["hierarchy_path"] = [parent_id, child_id]
            results.append(child)

    # Add orphan children that have no parent in snapshot
    for parent_id, children in children_map.items():
        if parent_id in parent_map:
            continue
        for child in _sort_children(children):
            child_id = str(child.get("client_order_id") or "")
            child["is_parent"] = False
            child["is_child"] = True
            child["is_orphan"] = True
            child["hierarchy_path"] = [child_id]
            results.append(child)

    return results


def _filter_cancelable_children(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cancelable: list[dict[str, Any]] = []
    for child in children:
        order_id = str(child.get("client_order_id") or "")
        status = str(child.get("status") or "").lower()
        if not order_id:
            continue
        if order_id.startswith(SYNTHETIC_ID_PREFIX) or order_id.startswith(FALLBACK_ID_PREFIX):
            continue
        if is_terminal_status(status):
            continue
        cancelable.append(child)
    return cancelable


def _render_children_summary(children: list[dict[str, Any]]) -> None:
    for child in children:
        slice_num = child.get("slice_num")
        status = child.get("status") or "unknown"
        qty = child.get("qty") or "?"
        if slice_num is not None:
            label = f"Slice {slice_num}: {qty} shares ({status})"
        else:
            label = f"Slice: {qty} shares ({status})"
        ui.label(label).classes("text-sm text-text-secondary")


async def on_cancel_parent_order(
    parent_order_id: str | None,
    symbol: str,
    children: list[dict[str, Any]],
    user_id: str,
    user_role: str,
    strategies: list[str] | None = None,
) -> None:
    """Handle cancel-all for a TWAP parent order.

    Args:
        parent_order_id: The parent TWAP order ID
        symbol: Symbol for notifications
        children: List of child slice orders
        user_id: User ID for auth
        user_role: User role for auth
        strategies: Strategy scope for multi-strategy auth
    """
    if user_role == "viewer":
        ui.notify("Viewers cannot cancel orders", type="warning")
        return

    cancelable = _filter_cancelable_children(children)
    if not cancelable:
        ui.notify("No pending child slices to cancel", type="warning")
        return

    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Cancel {len(cancelable)} child slices for {symbol}?").classes(
            "text-lg font-bold"
        )
        ui.label("The following child slices will be cancelled:").classes("text-sm")
        _render_children_summary(cancelable)

        submitting = False
        confirm_button: ui.button | None = None

        with ui.row().classes("gap-4 mt-4"):

            async def confirm() -> None:
                nonlocal submitting
                if submitting:
                    return
                submitting = True
                if confirm_button:
                    confirm_button.disable()

                client = AsyncTradingClient.get()
                failures: list[str] = []

                # Cancel all children in parallel for better UX with large TWAP orders
                async def cancel_child(child: dict[str, Any]) -> str | None:
                    child_id = str(child.get("client_order_id") or "")
                    if not child_id:
                        return None
                    try:
                        await client.cancel_order(
                            child_id,
                            user_id,
                            role=user_role,
                            strategies=strategies,
                            reason="Cancel parent TWAP order - child slice",
                            requested_by=user_id,
                            requested_at=datetime.now(UTC).isoformat(),
                        )
                        logger.info(
                            "cancel_child_slice_submitted",
                            extra={
                                "user_id": user_id,
                                "parent_order_id": parent_order_id,
                                "child_order_id": child_id,
                                "symbol": symbol,
                                "strategy_id": "manual",
                            },
                        )
                        return None
                    except httpx.HTTPStatusError as exc:
                        return f"{child_id} (HTTP {exc.response.status_code})"
                    except httpx.RequestError as exc:
                        return f"{child_id} ({type(exc).__name__})"

                results = await asyncio.gather(
                    *[cancel_child(child) for child in cancelable],
                    return_exceptions=False,
                )
                failures = [r for r in results if r is not None]

                if failures:
                    ui.notify(
                        f"Cancel failed for {len(failures)} child slice(s)",
                        type="negative",
                    )
                else:
                    ui.notify(
                        f"Cancel requested for {len(cancelable)} child slice(s)",
                        type="positive",
                    )
                dialog.close()

            confirm_button = ui.button("Confirm", on_click=confirm).classes("bg-red-600 text-white")
            ui.button("Cancel", on_click=dialog.close)

    dialog.open()


__all__ = [
    "HierarchicalOrdersState",
    "compute_parent_aggregates",
    "transform_to_hierarchy",
    "is_terminal_status",
    "on_cancel_parent_order",
]
