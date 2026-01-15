"""Admin Dashboard page for NiceGUI web console (P5T7).

Provides admin-only management of:
- API Keys (create and list; revoke/rotate TODO for future implementation)
- System Configuration (trading hours, position limits, system defaults)
- Audit Log viewing (with filters, pagination, export)

PARITY: Mirrors apps/web_console/pages/admin.py functionality

Note: Admin components are ASYNC - use async db pool (see Note #39).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from io import StringIO
from typing import TYPE_CHECKING, Any, TypeVar

import psycopg
from nicegui import ui
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator

from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.core.common.log_sanitizer import sanitize_dict
from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# Admin permissions required for page access
ADMIN_PERMISSIONS = {
    Permission.MANAGE_API_KEYS,
    Permission.MANAGE_SYSTEM_CONFIG,
    Permission.VIEW_AUDIT,
}

# Pagination and limits
PAGE_SIZE = 50
MAX_EXPORT_RECORDS = 10000

# API Key validation
MIN_REVOCATION_REASON_LENGTH = 20

# Config cache TTL
CONFIG_CACHE_TTL_SECONDS = 300


def _get_user_identifier(user: dict[str, Any]) -> str:
    """Extract user identifier for audit logging.

    Returns user_id if present, falls back to username, or 'unknown'.
    """
    return str(user.get("user_id") or user.get("username", "unknown"))


# === Config Models (from config_editor.py) ===


class TradingHoursConfig(BaseModel):
    """Trading hours and session flags."""

    market_open: time = time(9, 30)
    market_close: time = time(16, 0)
    pre_market_enabled: bool = False
    after_hours_enabled: bool = False

    @field_validator("market_close")
    @classmethod
    def close_after_open(cls, v: time, info: ValidationInfo) -> time:
        if "market_open" in info.data and v <= info.data["market_open"]:
            raise ValueError("market_close must be after market_open")
        return v


class PositionLimitsConfig(BaseModel):
    """Per-symbol and aggregate position limits."""

    max_position_per_symbol: int = Field(default=1000, ge=1, le=100000)
    max_notional_total: Decimal = Field(
        default=Decimal("100000"), ge=Decimal("1000"), le=Decimal("10000000")
    )
    max_open_orders: int = Field(default=10, ge=1, le=1000)


class SystemDefaultsConfig(BaseModel):
    """Global system safety defaults."""

    dry_run: bool = True
    circuit_breaker_enabled: bool = True
    drawdown_threshold: Decimal = Field(
        default=Decimal("0.05"), ge=Decimal("0.01"), le=Decimal("0.50")
    )


# === Audit Filters ===


@dataclass
class AuditFilters:
    """Filters applied to audit log queries."""

    user_id: str | None
    action: str | None
    event_type: str | None
    outcome: str | None
    start_at: datetime | None
    end_at: datetime | None


ACTION_CHOICES = [
    "All",
    "config_saved",
    "api_key_created",
    "api_key_revoked",
    "role_changed",
    "login",
    "logout",
]
EVENT_TYPES = ["All", "admin", "auth", "action"]
OUTCOMES = ["All", "success", "failure"]


# === Page Entry Point ===


@ui.page("/admin")
@requires_auth
@main_layout
async def admin_page() -> None:
    """Admin Dashboard page."""
    user = get_current_user()

    # Check for any admin permission
    if not any(has_permission(user, p) for p in ADMIN_PERMISSIONS):
        perm_names = ", ".join(p.value for p in ADMIN_PERMISSIONS)
        ui.label(f"Access denied: Requires one of: {perm_names}").classes("text-red-500 text-lg")
        return

    # Get async db pool for admin operations
    async_pool = get_db_pool()
    if async_pool is None:
        ui.label("Database not configured. Contact administrator.").classes("text-red-500")
        return

    # Page title
    ui.label("Admin Dashboard").classes("text-2xl font-bold mb-4")

    # Tabs
    with ui.tabs().classes("w-full") as tabs:
        tab_api = ui.tab("API Keys")
        tab_config = ui.tab("System Config")
        tab_recon = ui.tab("Reconciliation")
        tab_audit = ui.tab("Audit Logs")

    with ui.tab_panels(tabs, value=tab_api).classes("w-full"):
        with ui.tab_panel(tab_api):
            await _render_api_key_manager(user, async_pool)

        with ui.tab_panel(tab_config):
            await _render_config_editor(user, async_pool)

        with ui.tab_panel(tab_recon):
            await _render_reconciliation_tools(user)

        with ui.tab_panel(tab_audit):
            await _render_audit_log_viewer(user, async_pool)


# === API Key Manager ===


async def _render_api_key_manager(user: dict[str, Any], db_pool: AsyncConnectionPool) -> None:
    """Render API key manager UI."""
    if not has_permission(user, Permission.MANAGE_API_KEYS):
        ui.label("Permission denied: MANAGE_API_KEYS required").classes("text-red-500")
        return

    user_id = _get_user_identifier(user)

    ui.label("API Key Manager").classes("text-xl font-bold mb-2")
    ui.label("Create and manage API keys for programmatic access.").classes(
        "text-gray-500 text-sm mb-4"
    )

    # Create form
    with ui.card().classes("w-full p-4 mb-4"):
        ui.label("Create New API Key").classes("text-lg font-bold mb-2")

        name_input = ui.input(label="Key Name", placeholder="3-50 characters").classes("w-full")

        ui.label("Scopes").classes("text-sm font-medium mt-2")
        with ui.row().classes("gap-4"):
            scope_read_positions = ui.checkbox("Read positions")
            scope_read_orders = ui.checkbox("Read orders")
            scope_write_orders = ui.checkbox("Write orders")
            scope_read_strategies = ui.checkbox("Read strategies")

        with ui.row().classes("items-center gap-2 mt-2"):
            set_expiry = ui.checkbox("Set expiry date")
            expiry_date = ui.date().classes("w-48")
            expiry_date.visible = False

        def toggle_expiry() -> None:
            expiry_date.visible = set_expiry.value

        set_expiry.on_value_change(toggle_expiry)

        async def create_key() -> None:
            name = name_input.value
            if not name or len(name.strip()) < 3:
                ui.notify("Name must be at least 3 characters", type="negative")
                return
            if len(name.strip()) > 50:
                ui.notify("Name must be 50 characters or fewer", type="negative")
                return

            scopes = {
                "read_positions": scope_read_positions.value,
                "read_orders": scope_read_orders.value,
                "write_orders": scope_write_orders.value,
                "read_strategies": scope_read_strategies.value,
            }
            if not any(scopes.values()):
                ui.notify("Select at least one scope", type="negative")
                return

            expires_at = None
            if set_expiry.value and expiry_date.value:
                expires_at = datetime.combine(
                    date.fromisoformat(expiry_date.value),
                    time(23, 59, 59, tzinfo=UTC),
                )
                if expires_at <= datetime.now(UTC):
                    ui.notify("Expiry date must be in the future", type="negative")
                    return

            try:
                result = await _create_api_key(db_pool, user_id, name.strip(), scopes, expires_at)
                if result:
                    ui.notify("API key created! Copy it now - it won't be shown again.", type="positive")
                    # Show the key in a dialog
                    with ui.dialog() as dialog, ui.card():
                        ui.label("Your new API key:").classes("font-bold")
                        ui.code(result["full_key"]).classes("w-full")
                        ui.label("Copy this key now. It will not be shown again.").classes(
                            "text-yellow-600 text-sm"
                        )
                        ui.button("Close", on_click=dialog.close)
                    dialog.open()
                    # Refresh keys list data before updating UI
                    await fetch_keys()
                    keys_list.refresh()
            except ValueError as e:
                logger.exception(
                    "api_key_create_validation_error",
                    extra={"error": str(e), "user_id": user_id, "name": name},
                )
                ui.notify(f"Invalid input: {e}", type="negative")
            except RuntimeError as e:
                logger.exception(
                    "api_key_create_service_error",
                    extra={"error": str(e), "user_id": user_id, "name": name},
                )
                ui.notify("Service error. Please try again.", type="negative")

        ui.button("Create Key", on_click=create_key, color="primary").classes("mt-4")

    # Existing keys list
    keys_data: list[dict[str, Any]] = []

    async def fetch_keys() -> None:
        nonlocal keys_data
        keys_data = await _list_api_keys(db_pool, user_id)

    await fetch_keys()

    @ui.refreshable
    def keys_list() -> None:
        ui.label("Existing Keys").classes("text-lg font-bold mb-2")

        if not keys_data:
            ui.label("No API keys yet. Create one to get started.").classes("text-gray-500")
            return

        # Keys table
        columns: list[dict[str, Any]] = [
            {"name": "name", "label": "Name", "field": "name"},
            {"name": "prefix", "label": "Prefix", "field": "key_prefix"},
            {"name": "scopes", "label": "Scopes", "field": "scopes"},
            {"name": "created", "label": "Created", "field": "created_at"},
            {"name": "status", "label": "Status", "field": "status"},
        ]

        rows: list[dict[str, Any]] = []
        for key in keys_data:
            status = "Active"
            if key.get("revoked_at"):
                status = "Revoked"
            elif key.get("expires_at") and key["expires_at"] < datetime.now(UTC):
                status = "Expired"

            scopes = key.get("scopes", [])
            if isinstance(scopes, list):
                scopes_str = ", ".join(scopes[:3])
                if len(scopes) > 3:
                    scopes_str += "..."
            else:
                scopes_str = str(scopes)

            rows.append({
                "name": key["name"],
                "key_prefix": key["key_prefix"],
                "scopes": scopes_str,
                "created_at": key["created_at"].isoformat() if key.get("created_at") else "-",
                "status": status,
            })

        ui.table(columns=columns, rows=rows).classes("w-full")

    keys_list()


async def _create_api_key(
    db_pool: AsyncConnectionPool,
    user_id: str,
    name: str,
    scopes: dict[str, bool],
    expires_at: datetime | None,
) -> dict[str, Any] | None:
    """Create a new API key."""
    from libs.platform.admin.api_keys import generate_api_key, hash_api_key

    full_key, prefix, salt = generate_api_key()
    key_hash = hash_api_key(full_key, salt)
    scope_list = [s for s, enabled in scopes.items() if enabled]

    async with db_pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO api_keys (user_id, name, key_hash, key_salt, key_prefix, scopes, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, name, key_hash, salt, prefix, scope_list, expires_at),
        )

    return {"full_key": full_key, "prefix": prefix}


async def _list_api_keys(db_pool: AsyncConnectionPool, user_id: str) -> list[dict[str, Any]]:
    """List API keys for a user."""
    from psycopg.rows import dict_row

    async with db_pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT id, name, key_prefix, scopes, expires_at, last_used_at, created_at, revoked_at
                FROM api_keys WHERE user_id = %s ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()

    return list(rows) if rows else []


# === Config Editor ===

# TypeVar for config model
_ConfigT = TypeVar("_ConfigT", bound=BaseModel)


async def _render_config_editor(user: dict[str, Any], db_pool: AsyncConnectionPool) -> None:
    """Render system configuration editor UI."""
    if not has_permission(user, Permission.MANAGE_SYSTEM_CONFIG):
        ui.label("Permission denied: MANAGE_SYSTEM_CONFIG required").classes("text-red-500")
        return

    ui.label("System Configuration").classes("text-xl font-bold mb-2")
    ui.label("Admin-only settings for trading windows, limits, and defaults.").classes(
        "text-gray-500 text-sm mb-4"
    )

    # Sub-tabs for config sections
    with ui.tabs().classes("w-full") as config_tabs:
        tab_hours = ui.tab("Trading Hours")
        tab_limits = ui.tab("Position Limits")
        tab_defaults = ui.tab("System Defaults")

    with ui.tab_panels(config_tabs, value=tab_hours).classes("w-full"):
        with ui.tab_panel(tab_hours):
            await _render_trading_hours_form(user, db_pool)

        with ui.tab_panel(tab_limits):
            await _render_position_limits_form(user, db_pool)

        with ui.tab_panel(tab_defaults):
            await _render_system_defaults_form(user, db_pool)


# === Reconciliation Tools ===


async def _render_reconciliation_tools(user: dict[str, Any]) -> None:
    """Render reconciliation tools (fills backfill)."""
    if not has_permission(user, Permission.MANAGE_RECONCILIATION):
        ui.label("Permission denied: MANAGE_RECONCILIATION required").classes("text-red-500")
        return

    ui.label("Reconciliation Tools").classes("text-xl font-bold mb-2")
    ui.label("Manual controls for fills backfill and trade P&L reconstruction.").classes(
        "text-gray-500 text-sm mb-4"
    )

    user_id = _get_user_identifier(user)
    user_role = str(user.get("role") or "viewer")
    strategies = user.get("strategies") or []
    if isinstance(strategies, str):
        user_strategies = [strategies]
    else:
        user_strategies = [str(s) for s in strategies if s]

    client = AsyncTradingClient.get()

    with ui.card().classes("w-full p-4"):
        ui.label("Alpaca Fills Backfill").classes("text-lg font-bold mb-2")

        lookback_input = ui.number(
            label="Lookback Hours (optional)",
            value=None,
            min=1,
            max=720,
            step=1,
        ).classes("w-48")
        recalc_all = ui.checkbox("Recalculate realized P&L for all trades").classes("mt-2")

        result_box = ui.label("").classes("text-xs text-gray-500 mt-2")

        async def run_backfill() -> None:
            lookback_hours = None
            if lookback_input.value:
                try:
                    lookback_hours = int(lookback_input.value)
                except (TypeError, ValueError):
                    ui.notify("Lookback hours must be a number", type="negative")
                    return
            try:
                result = await client.run_fills_backfill(
                    user_id=user_id,
                    role=user_role,
                    strategies=user_strategies,
                    lookback_hours=lookback_hours,
                    recalc_all_trades=bool(recalc_all.value),
                )
                result_box.text = json.dumps(result, indent=2)
                ui.notify("Fills backfill completed", type="positive")
            except Exception as exc:
                logger.error(
                    "fills_backfill_failed",
                    extra={"user_id": user_id, "error": str(exc)},
                )
                ui.notify("Fills backfill failed", type="negative")

        ui.button("Run Fills Backfill", on_click=run_backfill, color="primary").classes("mt-4")


async def _get_config(
    db_pool: AsyncConnectionPool,
    config_key: str,
    config_class: type[_ConfigT],
) -> _ConfigT:
    """Load config from database with defaults."""
    from psycopg.rows import dict_row

    try:
        async with db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT config_value FROM system_config WHERE config_key = %s",
                    (config_key,),
                )
                row = await cursor.fetchone()

        if row:
            raw_value = row.get("config_value")
            if raw_value:
                return config_class.model_validate(raw_value)
    except psycopg.OperationalError as e:
        logger.warning(
            "config_load_db_error",
            extra={"config_key": config_key, "error": str(e), "operation": "get_config"},
        )
    except ValidationError as e:
        logger.warning(
            "config_load_validation_error",
            extra={"config_key": config_key, "error": str(e), "operation": "get_config"},
        )
    except ValueError as e:
        logger.warning(
            "config_load_value_error",
            extra={"config_key": config_key, "error": str(e), "operation": "get_config"},
        )

    return config_class()


async def _save_config(
    db_pool: AsyncConnectionPool,
    config_key: str,
    config_value: BaseModel,
    user_id: str,
) -> bool:
    """Save config to database."""
    payload = config_value.model_dump(mode="json")
    try:
        async with db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO system_config (config_key, config_value, config_type, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (config_key) DO UPDATE SET
                    config_value = EXCLUDED.config_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (config_key, payload, config_key, user_id),
            )
        return True
    except psycopg.OperationalError as e:
        logger.exception(
            "config_save_db_error",
            extra={"config_key": config_key, "error": str(e), "operation": "save_config"},
        )
        return False
    except ValueError as e:
        logger.exception(
            "config_save_value_error",
            extra={"config_key": config_key, "error": str(e), "operation": "save_config"},
        )
        return False


async def _render_trading_hours_form(user: dict[str, Any], db_pool: AsyncConnectionPool) -> None:
    """Render trading hours config form."""
    config = await _get_config(db_pool, "trading_hours", TradingHoursConfig)

    with ui.card().classes("w-full p-4"):
        # Time inputs using string format HH:MM
        market_open_str = config.market_open.strftime("%H:%M")
        market_close_str = config.market_close.strftime("%H:%M")

        open_input = ui.input(label="Market Open (HH:MM)", value=market_open_str).classes("w-48")
        close_input = ui.input(label="Market Close (HH:MM)", value=market_close_str).classes("w-48")
        pre_market = ui.checkbox("Enable pre-market", value=config.pre_market_enabled)
        after_hours = ui.checkbox("Enable after-hours", value=config.after_hours_enabled)

        async def save() -> None:
            try:
                open_time = datetime.strptime(open_input.value, "%H:%M").time()
                close_time = datetime.strptime(close_input.value, "%H:%M").time()
                updated = TradingHoursConfig(
                    market_open=open_time,
                    market_close=close_time,
                    pre_market_enabled=pre_market.value,
                    after_hours_enabled=after_hours.value,
                )
                user_id = _get_user_identifier(user)
                if await _save_config(db_pool, "trading_hours", updated, user_id):
                    ui.notify("Trading hours saved", type="positive")
                else:
                    ui.notify("Failed to save", type="negative")
            except (ValueError, ValidationError) as e:
                ui.notify(f"Invalid input: {e}", type="negative")

        ui.button("Save Trading Hours", on_click=save, color="primary").classes("mt-4")


async def _render_position_limits_form(user: dict[str, Any], db_pool: AsyncConnectionPool) -> None:
    """Render position limits config form."""
    config = await _get_config(db_pool, "position_limits", PositionLimitsConfig)

    with ui.card().classes("w-full p-4"):
        max_pos = ui.number(
            label="Max position per symbol",
            value=config.max_position_per_symbol,
            min=1,
            max=100000,
        ).classes("w-48")
        max_notional = ui.number(
            label="Max total notional ($)",
            value=float(config.max_notional_total),
            min=1000,
            max=10000000,
        ).classes("w-48")
        max_orders = ui.number(
            label="Max open orders",
            value=config.max_open_orders,
            min=1,
            max=1000,
        ).classes("w-48")

        async def save() -> None:
            try:
                updated = PositionLimitsConfig(
                    max_position_per_symbol=int(max_pos.value),
                    max_notional_total=Decimal(str(max_notional.value)),
                    max_open_orders=int(max_orders.value),
                )
                user_id = _get_user_identifier(user)
                if await _save_config(db_pool, "position_limits", updated, user_id):
                    ui.notify("Position limits saved", type="positive")
                else:
                    ui.notify("Failed to save", type="negative")
            except (ValueError, ValidationError) as e:
                ui.notify(f"Invalid input: {e}", type="negative")

        ui.button("Save Limits", on_click=save, color="primary").classes("mt-4")


async def _render_system_defaults_form(user: dict[str, Any], db_pool: AsyncConnectionPool) -> None:
    """Render system defaults config form."""
    config = await _get_config(db_pool, "system_defaults", SystemDefaultsConfig)

    with ui.card().classes("w-full p-4"):
        dry_run = ui.checkbox("Dry run mode", value=config.dry_run)
        cb_enabled = ui.checkbox("Circuit breaker enabled", value=config.circuit_breaker_enabled)
        drawdown = ui.number(
            label="Drawdown threshold",
            value=float(config.drawdown_threshold),
            min=0.01,
            max=0.50,
            step=0.01,
            format="%.2f",
        ).classes("w-48")

        async def save() -> None:
            try:
                updated = SystemDefaultsConfig(
                    dry_run=dry_run.value,
                    circuit_breaker_enabled=cb_enabled.value,
                    drawdown_threshold=Decimal(str(drawdown.value)),
                )
                user_id = _get_user_identifier(user)
                if await _save_config(db_pool, "system_defaults", updated, user_id):
                    ui.notify("System defaults saved", type="positive")
                else:
                    ui.notify("Failed to save", type="negative")
            except (ValueError, ValidationError) as e:
                ui.notify(f"Invalid input: {e}", type="negative")

        ui.button("Save Defaults", on_click=save, color="primary").classes("mt-4")


# === Audit Log Viewer ===


async def _render_audit_log_viewer(user: dict[str, Any], db_pool: AsyncConnectionPool) -> None:
    """Render audit log viewer with filters, pagination, and export."""
    if not has_permission(user, Permission.VIEW_AUDIT):
        ui.label("Permission denied: VIEW_AUDIT required").classes("text-red-500")
        return

    ui.label("Audit Log").classes("text-xl font-bold mb-2")
    ui.label("Query audit events with masking applied to sensitive fields.").classes(
        "text-gray-500 text-sm mb-4"
    )

    # Filter controls
    with ui.card().classes("w-full p-4 mb-4"):
        ui.label("Filters").classes("font-bold mb-2")

        with ui.row().classes("gap-4 flex-wrap"):
            user_filter = ui.input(label="User ID").classes("w-40")
            action_filter = ui.select(label="Action", options=ACTION_CHOICES, value="All").classes("w-40")
            event_filter = ui.select(label="Event Type", options=EVENT_TYPES, value="All").classes("w-40")
            outcome_filter = ui.select(label="Outcome", options=OUTCOMES, value="All").classes("w-40")

        with ui.row().classes("gap-4 mt-2"):
            use_date = ui.checkbox("Filter by date range")
            start_date = ui.date().classes("w-40")
            end_date = ui.date().classes("w-40")
            start_date.visible = False
            end_date.visible = False

        def toggle_dates() -> None:
            start_date.visible = use_date.value
            end_date.visible = use_date.value

        use_date.on_value_change(toggle_dates)

    # State
    current_page = 0
    logs_data: list[dict[str, Any]] = []
    total_count = 0

    def get_filters() -> AuditFilters:
        user_id = user_filter.value.strip() if user_filter.value else None
        action = None if action_filter.value == "All" else action_filter.value
        event_type = None if event_filter.value == "All" else event_filter.value
        outcome = None if outcome_filter.value == "All" else outcome_filter.value

        start_at = None
        end_at = None
        if use_date.value:
            if start_date.value:
                start_at = datetime.combine(
                    date.fromisoformat(start_date.value), time.min, tzinfo=UTC
                )
            if end_date.value:
                end_at = datetime.combine(
                    date.fromisoformat(end_date.value), time.max, tzinfo=UTC
                )

        return AuditFilters(
            user_id=user_id,
            action=action,
            event_type=event_type,
            outcome=outcome,
            start_at=start_at,
            end_at=end_at,
        )

    async def fetch_logs() -> None:
        nonlocal logs_data, total_count
        filters = get_filters()
        logs_data, total_count = await _fetch_audit_logs(
            db_pool, filters, PAGE_SIZE, current_page * PAGE_SIZE
        )

    await fetch_logs()

    @ui.refreshable
    def logs_display() -> None:
        ui.label(f"Showing {len(logs_data)} of {total_count} records (page {current_page + 1})").classes(
            "text-sm text-gray-500 mb-2"
        )

        if not logs_data:
            ui.label("No audit events found for the selected filters.").classes("text-gray-500")
            return

        # Logs table
        columns: list[dict[str, Any]] = [
            {"name": "timestamp", "label": "Timestamp", "field": "timestamp", "sortable": True},
            {"name": "user_id", "label": "User ID", "field": "user_id"},
            {"name": "action", "label": "Action", "field": "action"},
            {"name": "event_type", "label": "Event Type", "field": "event_type"},
            {"name": "resource", "label": "Resource", "field": "resource"},
            {"name": "outcome", "label": "Outcome", "field": "outcome"},
        ]

        rows: list[dict[str, Any]] = []
        for log in logs_data:
            rows.append({
                "timestamp": log["timestamp"].isoformat() if log.get("timestamp") else "-",
                "user_id": log.get("user_id", "-"),
                "action": log.get("action", "-"),
                "event_type": log.get("event_type", "-"),
                "resource": f"{log.get('resource_type', '-')}/{log.get('resource_id', '-')}",
                "outcome": log.get("outcome", "-"),
            })

        ui.table(columns=columns, rows=rows).classes("w-full")

        # Details expanders
        for log in logs_data:
            with ui.expansion(f"Details: {log.get('action', '-')} @ {log.get('timestamp', '-')}"):
                details = log.get("details") or {}
                ui.json_editor({"content": {"json": details}}, on_change=lambda e: None).classes(
                    "w-full"
                )

    logs_display()

    # Pagination
    with ui.row().classes("gap-4 mt-4"):
        async def prev_page() -> None:
            nonlocal current_page
            if current_page > 0:
                current_page -= 1
                await fetch_logs()
                logs_display.refresh()

        async def next_page() -> None:
            nonlocal current_page
            max_page = (total_count - 1) // PAGE_SIZE if total_count > 0 else 0
            if current_page < max_page:
                current_page += 1
                await fetch_logs()
                logs_display.refresh()

        ui.button("Previous", on_click=prev_page).props("flat")
        ui.button("Next", on_click=next_page).props("flat")

        async def apply_filters() -> None:
            nonlocal current_page
            current_page = 0
            await fetch_logs()
            logs_display.refresh()

        ui.button("Apply Filters", on_click=apply_filters, color="primary")

    # Export button
    async def export_csv() -> None:
        filters = get_filters()
        all_logs, _ = await _fetch_audit_logs(db_pool, filters, MAX_EXPORT_RECORDS, 0)
        csv_data = _build_audit_csv(all_logs)
        ui.download(csv_data, filename="audit_logs.csv")

    ui.button("Export CSV", on_click=export_csv).classes("mt-4")


async def _fetch_audit_logs(
    db_pool: AsyncConnectionPool,
    filters: AuditFilters,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch audit logs with filters."""
    from psycopg.rows import dict_row

    query = """
    SELECT
        id, timestamp, user_id, action, event_type,
        resource_type, resource_id, outcome, details
    FROM audit_log
    WHERE (%s::text IS NULL OR user_id = %s::text)
      AND (%s::text IS NULL OR action = %s::text)
      AND (%s::text IS NULL OR event_type = %s::text)
      AND (%s::text IS NULL OR outcome = %s::text)
      AND (%s::timestamptz IS NULL OR timestamp >= %s::timestamptz)
      AND (%s::timestamptz IS NULL OR timestamp <= %s::timestamptz)
    ORDER BY timestamp DESC
    LIMIT %s::int OFFSET %s::int
    """

    count_query = """
    SELECT COUNT(*) as count FROM audit_log
    WHERE (%s::text IS NULL OR user_id = %s::text)
      AND (%s::text IS NULL OR action = %s::text)
      AND (%s::text IS NULL OR event_type = %s::text)
      AND (%s::text IS NULL OR outcome = %s::text)
      AND (%s::timestamptz IS NULL OR timestamp >= %s::timestamptz)
      AND (%s::timestamptz IS NULL OR timestamp <= %s::timestamptz)
    """

    params = (
        filters.user_id, filters.user_id,
        filters.action, filters.action,
        filters.event_type, filters.event_type,
        filters.outcome, filters.outcome,
        filters.start_at, filters.start_at,
        filters.end_at, filters.end_at,
        limit, offset,
    )
    count_params = params[:-2]

    async with db_pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()

            await cursor.execute(count_query, count_params)
            count_row = await cursor.fetchone()

    total = count_row.get("count", 0) if count_row else 0

    parsed = []
    for entry in rows or []:
        # Sanitize details
        details = entry.get("details")
        if details:
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except json.JSONDecodeError:
                    details = {"raw": details}
            if isinstance(details, dict):
                details = sanitize_dict(details)
            entry["details"] = details

        parsed.append(dict(entry))

    return parsed, total


def _build_audit_csv(logs: list[dict[str, Any]]) -> bytes:
    """Build CSV bytes from audit logs."""
    fieldnames = [
        "timestamp", "user_id", "action", "event_type",
        "resource_type", "resource_id", "outcome", "details",
    ]

    output_rows = []
    for log in logs:
        ts_val = log.get("timestamp")
        ts_str = ""
        if ts_val is not None and hasattr(ts_val, "isoformat"):
            ts_str = ts_val.isoformat()
        output_rows.append({
            "timestamp": ts_str,
            "user_id": str(log.get("user_id") or ""),
            "action": str(log.get("action") or ""),
            "event_type": str(log.get("event_type") or ""),
            "resource_type": str(log.get("resource_type") or ""),
            "resource_id": str(log.get("resource_id") or ""),
            "outcome": str(log.get("outcome") or ""),
            "details": json.dumps(log.get("details") or {}, default=str),
        })

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(output_rows)
    return buffer.getvalue().encode()


__all__ = ["admin_page"]
