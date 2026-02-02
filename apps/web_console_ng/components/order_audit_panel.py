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

from apps.web_console_ng.config import EXECUTION_GATEWAY_URL
from libs.platform.web_console_auth.permissions import is_admin

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


def format_action(action: str | None) -> str:
    """Format action type for display."""
    if not action:
        return "Unknown"
    return ACTION_LABELS.get(action.lower(), action.replace("_", " ").title())


def get_outcome_class(outcome: str | None) -> str:
    """Get CSS class for outcome."""
    if not outcome:
        return "text-gray-400"
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
        user: dict[str, Any] | None = None,
    ) -> None:
        """Initialize audit panel.

        Args:
            user_id: Current user ID for API auth
            role: User role for API auth
            strategies: Authorized strategies for API auth
            user: User object for permission checks (PII visibility)
        """
        self.user_id = user_id
        self.role = role
        self.strategies = strategies
        self.user = user
        self._is_admin = is_admin(user) if user else False
        self._dialog: ui.dialog | None = None
        self._content_container: ui.column | None = None
        self._current_order_id: str | None = None

    async def show(self, client_order_id: str) -> None:
        """Show audit trail for an order.

        Args:
            client_order_id: Order to show audit trail for
        """
        logger.info(
            "Opening order audit panel",
            extra={
                "client_order_id": client_order_id,
                "user_id": self.user_id,
                "role": self.role,
            },
        )
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

        # Strip PII at data level for non-admins (not just display level)
        # This prevents PII from residing in memory for non-privileged users
        if data and not self._is_admin:
            entries = data.get("entries", [])
            for entry in entries:
                entry.pop("ip_address", None)
                entry.pop("user_id", None)

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

            # NOTE: Export toolbar removed - GridExportToolbar requires AG Grid,
            # but this panel uses ui.table. Future: implement ui.table export path.

            # Audit entries table - hide PII for non-admin users
            _render_audit_table(entries, show_pii=self._is_admin)


def _render_audit_table(entries: list[dict[str, Any]], show_pii: bool = False) -> None:
    """Render audit entries as a table.

    Args:
        entries: List of audit entry dicts
        show_pii: Whether to show PII columns (IP, user_id) - admin only
    """
    # Base columns - always visible
    columns: list[dict[str, str]] = [
        {"name": "time", "label": "Time", "field": "timestamp"},
        {"name": "action", "label": "Action", "field": "action"},
        {"name": "outcome", "label": "Outcome", "field": "outcome"},
    ]

    # PII columns - only for admin users
    if show_pii:
        columns.extend([
            {"name": "user", "label": "User", "field": "user_id"},
            {"name": "ip", "label": "IP", "field": "ip_address"},
        ])

    columns.append({"name": "details", "label": "Details", "field": "details_str"})

    rows = []
    for idx, entry in enumerate(entries):
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

        # Create unique row key: combine index + timestamp + action to avoid collisions
        timestamp_str = format_timestamp(entry.get("timestamp", ""))
        action_str = format_action(entry.get("action", ""))
        row_id = f"{idx}-{timestamp_str}-{action_str}"

        row: dict[str, Any] = {
            "row_id": row_id,
            "timestamp": timestamp_str,
            "action": action_str,
            "outcome": entry.get("outcome", ""),
            "details_str": details_str or "-",
        }

        # Only include PII fields for admins
        if show_pii:
            row["user_id"] = entry.get("user_id") or "-"
            row["ip_address"] = entry.get("ip_address") or "-"

        rows.append(row)

    ui.table(
        columns=columns,
        rows=rows,
        row_key="row_id",  # Use unique composite key to avoid collisions
    ).classes("w-full").props("id=audit-entries-grid dense")


async def show_order_audit_dialog(
    client_order_id: str,
    user_id: str,
    role: str,
    strategies: list[str],
    user: dict[str, Any] | None = None,
) -> None:
    """Convenience function to show audit trail dialog.

    Args:
        client_order_id: Order to show audit trail for
        user_id: Current user ID
        role: User role
        strategies: Authorized strategies
        user: User object for permission checks (PII visibility)
    """
    panel = OrderAuditPanel(user_id, role, strategies, user=user)
    await panel.show(client_order_id)


__all__ = [
    "OrderAuditPanel",
    "fetch_audit_trail",
    "show_order_audit_dialog",
]
