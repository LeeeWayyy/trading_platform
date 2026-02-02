"""Grid export toolbar component for P6T8.

Provides CSV, Excel, and Clipboard export functionality with:
- Formula injection sanitization
- Server-side audit logging
- PII column exclusion for non-admin users
- Strict audit mode support
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import app, ui

from apps.web_console_ng.core.client import AsyncTradingClient
from libs.platform.security import sanitize_for_export
from libs.platform.web_console_auth.permissions import get_authorized_strategies

logger = logging.getLogger(__name__)

# Feature flag for strict audit mode
EXPORT_STRICT_AUDIT_MODE = os.getenv("EXPORT_STRICT_AUDIT_MODE", "false").lower() == "true"


# sanitize_for_export is imported from libs.platform.security
# This ensures a SINGLE SOURCE OF TRUTH for formula injection protection.
# The JavaScript version in static/js/grid_export.js MUST be kept in sync.
# See libs/platform/security/sanitization.py for the canonical implementation.


class GridExportToolbar:
    """Export toolbar component for AG Grids.

    Provides CSV, Excel, and Clipboard export with audit logging.
    """

    def __init__(
        self,
        grid_id: str,
        grid_name: str,
        filename_prefix: str,
        pii_columns: list[str] | None = None,
        exclude_columns: list[str] | None = None,
        on_export_start: Callable[[str], None] | None = None,
        on_export_complete: Callable[[str, int], None] | None = None,
        api_base_url: str = "/api/v1",
    ) -> None:
        """Initialize export toolbar.

        Args:
            grid_id: HTML ID of the AG Grid element
            grid_name: Logical name for audit (e.g., "positions", "orders")
            filename_prefix: Prefix for exported files
            pii_columns: Columns to exclude for non-admin users
            exclude_columns: Columns to always exclude from export
            on_export_start: Callback when export starts
            on_export_complete: Callback when export completes (type, row_count)
            api_base_url: Base URL for export API endpoints
        """
        self.grid_id = grid_id
        self.grid_name = grid_name
        self.filename_prefix = filename_prefix
        self.pii_columns = pii_columns or []
        self.exclude_columns = exclude_columns or []
        self.on_export_start = on_export_start
        self.on_export_complete = on_export_complete
        self.api_base_url = api_base_url

        self._csv_button: ui.button | None = None
        self._excel_button: ui.button | None = None
        self._clipboard_button: ui.button | None = None

    def _get_filename(self) -> str:
        """Generate filename with timestamp."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M")
        return f"{self.filename_prefix}_{timestamp}"

    def _get_exclude_columns(self) -> list[str]:
        """Get columns to exclude from export.

        Combines always-excluded columns with PII columns for non-admin users.
        Default-deny: PII excluded when user context unavailable (safety first).
        """
        from libs.platform.web_console_auth.permissions import is_admin

        exclude = list(self.exclude_columns)

        # Check if current user is admin - default to excluding PII if user unknown
        user = app.storage.user.get("user") if hasattr(app.storage, "user") else None
        if not user or not is_admin(user):
            # Default-deny: exclude PII when user is unknown or not admin
            exclude.extend(self.pii_columns)

        return exclude

    async def _get_grid_state(self) -> dict[str, Any]:
        """Get current grid state (filter, sort, columns, row count).

        Respects PII exclusion rules so audit records accurately reflect exported scope.
        """
        # Use same exclude list as export to ensure audit accuracy
        exclude_cols = self._get_exclude_columns()
        exclude_js = json.dumps(exclude_cols)

        js_code = f"""
        (function() {{
            const obj = window['{self.grid_id}'];
            const gridApi = obj?.gridOptions?.api || obj?.api || obj;
            if (!gridApi) {{
                return {{ success: false }};
            }}
            return {{
                success: true,
                filterModel: window.GridExport.getFilterModel(gridApi),
                sortModel: window.GridExport.getSortModel(gridApi),
                columns: window.GridExport.getExportableColumns(gridApi, {exclude_js}),
                rowCount: window.GridExport.getVisibleRowCount(gridApi)
            }};
        }})();
        """
        return await ui.run_javascript(js_code) or {"success": False}

    def _get_auth_headers(self) -> dict[str, str]:
        """Get auth headers from NiceGUI session for API calls.

        Uses AsyncTradingClient._get_auth_headers() for proper authentication
        including HMAC signatures when INTERNAL_TOKEN_SECRET is set.
        This ensures export API calls work in production with api_auth enforce mode.

        Uses get_authorized_strategies() to derive permissions-filtered strategies,
        not raw session strategies, for consistent security posture.
        """
        try:
            user = app.storage.user.get("user") if hasattr(app.storage, "user") else None
            if user:
                user_id = str(user.get("user_id") or user.get("username", "unknown"))
                role = str(user.get("role", "viewer"))
                # Use permission-filtered strategies, not raw session strategies
                strategies = get_authorized_strategies(user)

                # Use AsyncTradingClient for proper HMAC-signed headers
                client = AsyncTradingClient.get()
                return client._get_auth_headers(user_id, role, strategies)
        except Exception:
            pass  # Return empty headers if unavailable
        return {}

    async def _create_audit_record(
        self, export_type: str, grid_state: dict[str, Any]
    ) -> str | None:
        """Create export audit record via API.

        Returns audit_id or None if failed.
        """
        try:
            import httpx

            headers = self._get_auth_headers()
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base_url}/export/audit",
                    headers=headers,
                    json={
                        "export_type": export_type,
                        "grid_name": self.grid_name,
                        "filter_params": grid_state.get("filterModel"),
                        "visible_columns": grid_state.get("columns"),
                        "sort_model": grid_state.get("sortModel"),
                        "export_scope": "visible",
                        "estimated_row_count": grid_state.get("rowCount"),
                    },
                    timeout=10.0,
                )
                if response.status_code == 201:
                    data: dict[str, Any] = response.json()
                    audit_id: str | None = data.get("audit_id")
                    return audit_id
                logger.warning(
                    "Failed to create export audit",
                    extra={"status": response.status_code, "body": response.text},
                )
        except Exception as exc:
            logger.warning("Export audit API unavailable", extra={"error": str(exc)})
        return None

    async def _complete_audit_record(
        self, audit_id: str, row_count: int, status: str = "completed"
    ) -> None:
        """Complete export audit record via API."""
        try:
            import httpx

            headers = self._get_auth_headers()
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"{self.api_base_url}/export/audit/{audit_id}",
                    headers=headers,
                    json={
                        "actual_row_count": row_count,
                        "status": status,
                    },
                    timeout=10.0,
                )
        except Exception as exc:
            logger.warning(
                "Failed to complete export audit",
                extra={"audit_id": audit_id, "error": str(exc)},
            )

    async def _handle_csv_export(self) -> None:
        """Handle CSV export button click."""
        if self.on_export_start:
            self.on_export_start("csv")

        # Get grid state for audit
        grid_state = await self._get_grid_state()

        # Create audit record
        audit_id = await self._create_audit_record("csv", grid_state)

        # In strict audit mode, block export if audit creation fails
        if EXPORT_STRICT_AUDIT_MODE and audit_id is None:
            ui.notify("Export blocked: audit record creation failed", type="negative")
            logger.warning(
                "CSV export blocked due to audit failure in strict mode",
                extra={"grid_name": self.grid_name},
            )
            return

        exclude_cols = self._get_exclude_columns()
        # Use json.dumps for safe JS array serialization (prevents injection)
        exclude_js = json.dumps(exclude_cols)
        filename = self._get_filename()
        # Escape filename for JS string literal (prevents injection)
        filename_js = json.dumps(filename)

        js_code = f"""
        (async function() {{
            const obj = window['{self.grid_id}'];
            const gridApi = obj?.gridOptions?.api || obj?.api || obj;
            if (!gridApi) {{
                console.error('Grid API not found for {self.grid_id}');
                return {{ success: false, rowCount: 0 }};
            }}

            try {{
                window.GridExport.exportToCsv(gridApi, {filename_js}, {exclude_js});
                const rowCount = window.GridExport.getVisibleRowCount(gridApi);
                return {{ success: true, rowCount: rowCount }};
            }} catch (e) {{
                console.error('CSV export failed:', e);
                return {{ success: false, rowCount: 0 }};
            }}
        }})();
        """

        result = await ui.run_javascript(js_code)

        if result and result.get("success"):
            row_count = result.get("rowCount", 0)
            # Complete audit record
            if audit_id:
                await self._complete_audit_record(audit_id, row_count)
            if self.on_export_complete:
                self.on_export_complete("csv", row_count)
        elif audit_id:
            # Mark audit as failed
            await self._complete_audit_record(audit_id, 0, "failed")

    async def _handle_clipboard_export(self) -> None:
        """Handle clipboard export button click."""
        if self.on_export_start:
            self.on_export_start("clipboard")

        # Get grid state for audit
        grid_state = await self._get_grid_state()

        # Create audit record
        audit_id = await self._create_audit_record("clipboard", grid_state)

        # In strict audit mode, block export if audit creation fails
        if EXPORT_STRICT_AUDIT_MODE and audit_id is None:
            ui.notify("Copy blocked: audit record creation failed", type="negative")
            logger.warning(
                "Clipboard copy blocked due to audit failure in strict mode",
                extra={"grid_name": self.grid_name},
            )
            return

        exclude_cols = self._get_exclude_columns()
        # Use json.dumps for safe JS array serialization (prevents injection)
        exclude_js = json.dumps(exclude_cols)

        # Call JavaScript clipboard function
        js_code = f"""
        (async function() {{
            const obj = window['{self.grid_id}'];
            const gridApi = obj?.gridOptions?.api || obj?.api || obj;
            if (!gridApi) {{
                console.error('Grid API not found for {self.grid_id}');
                return {{ success: false, rowCount: 0 }};
            }}

            try {{
                const rowCount = await window.GridExport.copyToClipboard(gridApi, {exclude_js});
                return {{ success: true, rowCount: rowCount }};
            }} catch (e) {{
                console.error('Clipboard copy failed:', e);
                return {{ success: false, rowCount: 0 }};
            }}
        }})();
        """

        result = await ui.run_javascript(js_code)

        if result and result.get("success"):
            row_count = result.get("rowCount", 0)
            ui.notify(f"Copied {row_count} rows to clipboard", type="positive")
            # Complete audit record
            if audit_id:
                await self._complete_audit_record(audit_id, row_count)
            if self.on_export_complete:
                self.on_export_complete("clipboard", row_count)
        else:
            ui.notify("Failed to copy to clipboard", type="negative")
            if audit_id:
                await self._complete_audit_record(audit_id, 0, "failed")

    async def _handle_excel_export(self) -> None:
        """Handle Excel export button click.

        Excel export works differently from CSV/Clipboard:
        1. Create audit record via POST /api/v1/export/audit
        2. Redirect to GET /api/v1/export/excel/{audit_id} for download
        The server generates the Excel file and marks the audit as used (single-use link).
        """
        if self.on_export_start:
            self.on_export_start("excel")

        # Get grid state for audit
        grid_state = await self._get_grid_state()
        if not grid_state.get("success"):
            ui.notify("Failed to get grid state", type="negative")
            return

        # Create audit record for Excel export
        audit_id = await self._create_audit_record("excel", grid_state)
        if not audit_id:
            ui.notify("Failed to create export audit record", type="negative")
            return

        # Trigger download via JavaScript (opens new tab/downloads file)
        download_url = f"{self.api_base_url}/export/excel/{audit_id}"
        await ui.run_javascript(f"window.open('{download_url}', '_blank');")

        ui.notify("Excel export started", type="positive")
        if self.on_export_complete:
            self.on_export_complete("excel", grid_state.get("rowCount", 0))

    def create(self) -> ui.row:
        """Create and return the export toolbar element."""
        with ui.row().classes("gap-2 items-center") as toolbar:
            # CSV button (hidden in strict mode)
            if not EXPORT_STRICT_AUDIT_MODE:
                self._csv_button = ui.button(
                    "CSV",
                    icon="download",
                    on_click=self._handle_csv_export,
                ).props("size=sm flat").classes("text-xs")

            # Excel button (always visible)
            self._excel_button = ui.button(
                "Excel",
                icon="table_view",
                on_click=self._handle_excel_export,
            ).props("size=sm flat").classes("text-xs")

            # Clipboard button (hidden in strict mode)
            if not EXPORT_STRICT_AUDIT_MODE:
                self._clipboard_button = ui.button(
                    "Copy",
                    icon="content_copy",
                    on_click=self._handle_clipboard_export,
                ).props("size=sm flat").classes("text-xs")

        return toolbar

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all export buttons."""
        if self._csv_button:
            if enabled:
                self._csv_button.enable()
            else:
                self._csv_button.disable()
        if self._excel_button:
            if enabled:
                self._excel_button.enable()
            else:
                self._excel_button.disable()
        if self._clipboard_button:
            if enabled:
                self._clipboard_button.enable()
            else:
                self._clipboard_button.disable()


__all__ = [
    "GridExportToolbar",
    "sanitize_for_export",
    "EXPORT_STRICT_AUDIT_MODE",
]
