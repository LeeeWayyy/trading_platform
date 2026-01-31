"""Order Audit Trail Panel component for P6T8.

Provides a dialog/panel to display order audit trail with:
- Chronological list of all actions on an order
- IP address and session ID for compliance
- Export functionality

Uses execution gateway API: GET /api/v1/orders/{client_order_id}/audit
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
from nicegui import ui

from apps.web_console_ng.components.grid_export_toolbar import GridExportToolbar
from apps.web_console_ng.config import EXECUTION_GATEWAY_URL

logger = logging.getLogger(__name__)


# Action type to display mapping
ACTION_LABELS = {
    "submit": "Order Submitted",
    "submitted": "Order Submitted",
    "cancel": "Cancellation Requested",
    "canceled": "Order Canceled",
    "modify": "Order Modified",
    "fill": "Fill Received",
    "partial_fill": "Partial Fill",
    "reject": "Order Rejected",
    "rejected": "Order Rejected",
    "expire": "Order Expired",
    "expired": "Order Expired",
    "replace": "Order Replaced",
    "replaced": "Order Replaced",
    "pending_new": "Pending Acceptance",
    "accepted": "Order Accepted",
    "new": "Order New",
}

# Outcome to color mapping
OUTCOME_COLORS = {
    "success": "text-green-400",
    "failure": "text-red-400",
    "pending": "text-yellow-400",
    "error": "text-red-500",
}


async def fetch_audit_trail(
    client_order_id: str,
    user_id: str,
    role: str,
    strategies: list[str],
) -> dict[str, Any] | None:
    """Fetch order audit trail from API.

    Returns None on error.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {
                "X-User-ID": user_id,
                "X-User-Role": role,
                "X-User-Strategies": ",".join(strategies),
            }
            response = await client.get(
                f"{EXECUTION_GATEWAY_URL}/api/v1/orders/{client_order_id}/audit",
                headers=headers,
            )
            if response.status_code == 200:
                result: dict[str, Any] = response.json()
                return result
            logger.warning(
                "Audit trail API returned non-200",
                extra={
                    "status": response.status_code,
                    "client_order_id": client_order_id,
                },
            )
    except httpx.RequestError as e:
        logger.warning(
            "Audit trail API unavailable",
            extra={"error": str(e), "client_order_id": client_order_id},
        )
    return None


def format_timestamp(ts: str | datetime) -> str:
    """Format timestamp for display."""
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return ts
    else:
        dt = ts
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_action(action: str) -> str:
    """Format action type for display."""
    return ACTION_LABELS.get(action.lower(), action.replace("_", " ").title())


def get_outcome_class(outcome: str) -> str:
    """Get CSS class for outcome."""
    return OUTCOME_COLORS.get(outcome.lower(), "text-gray-400")


class OrderAuditPanel:
    """Order audit trail panel component.

    Displays chronological audit trail for a specific order.
    """

    def __init__(
        self,
        user_id: str,
        role: str,
        strategies: list[str],
    ) -> None:
        """Initialize audit panel.

        Args:
            user_id: Current user ID for API auth
            role: User role for API auth
            strategies: Authorized strategies for API auth
        """
        self.user_id = user_id
        self.role = role
        self.strategies = strategies
        self._dialog: ui.dialog | None = None
        self._content_container: ui.column | None = None
        self._current_order_id: str | None = None

    async def show(self, client_order_id: str) -> None:
        """Show audit trail for an order.

        Args:
            client_order_id: Order to show audit trail for
        """
        self._current_order_id = client_order_id

        if self._dialog is None:
            self._create_dialog()

        if self._content_container:
            await self._load_content(client_order_id)

        if self._dialog:
            self._dialog.open()

    def _create_dialog(self) -> None:
        """Create the dialog structure."""
        with ui.dialog().classes("w-[700px] max-w-[90vw]") as dialog:
            self._dialog = dialog
            with ui.card().classes("w-full p-0"):
                # Header
                with ui.row().classes(
                    "w-full px-4 py-3 bg-surface-2 items-center justify-between"
                ):
                    ui.label("Order Audit Trail").classes("text-lg font-bold text-white")
                    ui.button(icon="close", on_click=dialog.close).props(
                        "flat round size=sm"
                    )

                # Content container
                self._content_container = ui.column().classes(
                    "w-full p-4 max-h-[60vh] overflow-y-auto"
                )

    async def _load_content(self, client_order_id: str) -> None:
        """Load and render audit trail content."""
        if not self._content_container:
            return

        self._content_container.clear()

        with self._content_container:
            # Loading state
            with ui.row().classes("w-full justify-center p-4"):
                ui.spinner(size="lg")
                ui.label("Loading audit trail...").classes("ml-2 text-gray-400")

        # Fetch data
        data = await fetch_audit_trail(
            client_order_id, self.user_id, self.role, self.strategies
        )

        self._content_container.clear()

        with self._content_container:
            if data is None:
                # Error state
                with ui.row().classes("w-full justify-center p-4"):
                    ui.icon("error", size="lg").classes("text-red-500")
                    ui.label("Failed to load audit trail").classes("ml-2 text-red-400")
                return

            entries = data.get("entries", [])
            total_count = data.get("total_count", 0)

            # Order ID header
            ui.label(f"Order: {client_order_id}").classes(
                "text-sm text-gray-400 mb-4 font-mono"
            )

            if not entries:
                # Empty state
                with ui.row().classes("w-full justify-center p-4"):
                    ui.icon("info", size="lg").classes("text-gray-500")
                    ui.label("No audit entries found").classes("ml-2 text-gray-400")
                return

            # Summary
            ui.label(f"{total_count} audit entries").classes(
                "text-xs text-gray-500 mb-3"
            )

            # Export toolbar
            with ui.row().classes("w-full justify-end mb-3"):
                export_toolbar = GridExportToolbar(
                    grid_id="audit-entries-grid",
                    grid_name="audit",
                    filename_prefix=f"order_audit_{client_order_id[:12]}",
                )
                export_toolbar.create()

            # Audit entries table
            _render_audit_table(entries)


def _render_audit_table(entries: list[dict[str, Any]]) -> None:
    """Render audit entries as a table."""
    columns = [
        {"name": "time", "label": "Time", "field": "timestamp"},
        {"name": "action", "label": "Action", "field": "action"},
        {"name": "outcome", "label": "Outcome", "field": "outcome"},
        {"name": "user", "label": "User", "field": "user_id"},
        {"name": "ip", "label": "IP", "field": "ip_address"},
        {"name": "details", "label": "Details", "field": "details_str"},
    ]

    rows = []
    for entry in entries:
        details = entry.get("details", {})
        details_str = ""
        if details:
            # Format key details only
            key_fields = ["symbol", "side", "qty", "price", "reason", "status"]
            parts = []
            for k in key_fields:
                if k in details and details[k]:
                    parts.append(f"{k}={details[k]}")
            if parts:
                details_str = ", ".join(parts[:3])
                if len(parts) > 3:
                    details_str += "..."

        rows.append(
            {
                "timestamp": format_timestamp(entry.get("timestamp", "")),
                "action": format_action(entry.get("action", "")),
                "outcome": entry.get("outcome", ""),
                "user_id": entry.get("user_id") or "-",
                "ip_address": entry.get("ip_address") or "-",
                "details_str": details_str or "-",
            }
        )

    ui.table(
        columns=columns,
        rows=rows,
        row_key="timestamp",
    ).classes("w-full").props("id=audit-entries-grid dense")


async def show_order_audit_dialog(
    client_order_id: str,
    user_id: str,
    role: str,
    strategies: list[str],
) -> None:
    """Convenience function to show audit trail dialog.

    Args:
        client_order_id: Order to show audit trail for
        user_id: Current user ID
        role: User role
        strategies: Authorized strategies
    """
    panel = OrderAuditPanel(user_id, role, strategies)
    await panel.show(client_order_id)


__all__ = [
    "OrderAuditPanel",
    "fetch_audit_trail",
    "show_order_audit_dialog",
]
