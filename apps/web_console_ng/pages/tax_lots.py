"""Tax Lot Management page (P6T16.1).

Displays tax lots with wash sale warnings, harvesting suggestions,
cost basis method management, and Form 8949 export/preview.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from nicegui import ui
from psycopg.rows import dict_row

from apps.web_console_ng.auth.db_role import verify_db_role
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.components.tax_harvesting import render_harvesting_suggestions
from apps.web_console_ng.components.tax_lot_table import render_tax_lot_table
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.tax.tax_loss_harvesting import TaxLossHarvester
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.tax_lot_service import TaxLotService

logger = logging.getLogger(__name__)

_COST_BASIS_METHODS = ["fifo", "lifo", "specific_id"]


@ui.page("/tax-lots")
@requires_auth
@main_layout
async def tax_lots_page() -> None:
    """Tax lot management dashboard."""
    user = get_current_user()
    if not has_permission(user, Permission.VIEW_TAX_LOTS):
        ui.label("Permission denied: VIEW_TAX_LOTS required").classes("text-red-500")
        return

    db_pool = get_db_pool()
    if db_pool is None:
        ui.label("Database unavailable").classes("text-red-500")
        return

    service = TaxLotService(db_pool, user)
    audit = AuditLogger(db_pool)
    user_id = user.get("user_id", "unknown")
    is_admin = has_permission(user, Permission.MANAGE_TAX_LOTS)

    async def _check_tax_permission(
        permission: Permission, action: str, resource_type: str, resource_id: str,
    ) -> tuple[bool, str]:
        """Check tax permission with DB verification and audit on denial."""
        current = get_current_user()
        uid = current.get("user_id", "unknown")
        if has_permission(current, permission) and await verify_db_role(
            db_pool, uid, permission
        ):
            return True, uid
        try:
            await audit.log_action(
                user_id=uid,
                action=f"{action}_denied",
                resource_type=resource_type,
                resource_id=resource_id,
                outcome="denied",
                details={"role": current.get("role")},
            )
        except Exception:
            logger.debug("audit_log_%s_denied_failed", action)
        ui.notify("Permission denied", type="negative")
        return False, uid

    # State
    show_all_users = False

    async def _load_lots() -> list[Any]:
        if show_all_users and is_admin:
            return await service.list_lots(all_users=True, open_only=True)
        return await service.list_lots(open_only=True)

    lots = await _load_lots()

    # Fetch wash sale adjustments for displayed lots
    wash_sale_lot_ids = await _fetch_wash_sale_lot_ids(db_pool, lots)

    # Fetch current prices for harvesting (graceful degradation)
    current_prices = await _fetch_current_prices(lots, user)
    harvester = TaxLossHarvester(db_pool)  # type: ignore[arg-type]
    suggestions = None
    if current_prices and not show_all_users:
        try:
            suggestions = await harvester.find_opportunities(user_id, current_prices)
        except Exception:
            logger.warning("tax_loss_harvesting_failed", extra={"user_id": user_id})

    # Page title
    ui.label("Tax Lot Management").classes("text-2xl font-bold mb-4")

    # Summary metrics (refreshable for toggle)
    @ui.refreshable  # type: ignore[arg-type]
    async def summary_section() -> None:
        await _render_summary_metrics(lots, current_prices, wash_sale_lot_ids, db_pool)

    await summary_section()

    # Admin controls
    if is_admin:
        with ui.row().classes("w-full items-center gap-4 mb-4"):

            async def toggle_all_users(e: Any) -> None:
                allowed, _ = await _check_tax_permission(
                    Permission.MANAGE_TAX_LOTS, "toggle_all_users", "tax_lot", "",
                )
                if not allowed:
                    return
                nonlocal show_all_users, lots, wash_sale_lot_ids, current_prices, suggestions
                show_all_users = e.value
                lots = await _load_lots()
                wash_sale_lot_ids = await _fetch_wash_sale_lot_ids(db_pool, lots)
                current_prices = await _fetch_current_prices(lots, get_current_user())
                suggestions = None
                if current_prices and not show_all_users:
                    try:
                        suggestions = await harvester.find_opportunities(user_id, current_prices)
                    except Exception:
                        logger.warning("tax_loss_harvesting_failed", extra={"user_id": user_id})
                summary_section.refresh()
                lot_grid.refresh()
                harvesting_section.refresh()

            ui.switch("Show All Users", on_change=toggle_all_users)

    # Cost basis method selector
    if has_permission(user, Permission.MANAGE_TAX_SETTINGS):
        current_method = await service.get_cost_basis_method()
        with ui.row().classes("items-center gap-2 mb-4"):
            ui.label("Cost Basis Method:").classes("font-medium")

            async def _on_method_change(e: Any) -> None:
                allowed, current_uid = await _check_tax_permission(
                    Permission.MANAGE_TAX_SETTINGS, "cost_basis_method_change",
                    "tax_settings", "",
                )
                if not allowed:
                    return
                current = get_current_user()
                fresh_service = TaxLotService(db_pool, current)
                try:
                    await fresh_service.set_cost_basis_method(e.value)
                    await audit.log_admin_change(
                        admin_user_id=current_uid,
                        action="cost_basis_method_changed",
                        target_user_id=current_uid,
                        details={"method": e.value},
                    )
                    ui.notify(f"Cost basis method set to {e.value.upper()}", type="positive")
                except ValueError as ve:
                    ui.notify(f"Invalid method: {ve}", type="negative")
                except PermissionError:
                    await audit.log_action(
                        user_id=current_uid,
                        action="cost_basis_method_change_denied",
                        resource_type="tax_settings",
                        resource_id=current_uid,
                        outcome="denied",
                        details={"attempted_method": e.value},
                    )
                    ui.notify("Permission denied", type="negative")
                except Exception:
                    logger.exception(
                        "cost_basis_method_change_failed",
                        extra={"user_id": current_uid, "method": e.value},
                    )
                    ui.notify("Failed to update cost basis method", type="negative")

            ui.select(
                options=_COST_BASIS_METHODS,
                value=current_method,
                on_change=_on_method_change,
            ).classes("w-48")

    # Main content layout
    with ui.row().classes("w-full gap-4"):
        with ui.column().classes("flex-1"):
            # Close lot handler
            async def _on_close_lot(lot_id: str) -> None:
                allowed, current_uid = await _check_tax_permission(
                    Permission.MANAGE_TAX_LOTS, "lot_close", "tax_lot", lot_id,
                )
                if not allowed:
                    return
                current = get_current_user()
                fresh_service = TaxLotService(db_pool, current)
                # Look up lot owner before closing for accurate audit attribution
                lot_owner_id = current_uid
                if show_all_users:
                    try:
                        async with db_pool.connection() as conn:
                            cur = await conn.execute(
                                "SELECT user_id FROM tax_lots WHERE id = %s",
                                (lot_id,),
                            )
                            row = await cur.fetchone()
                            if row:
                                lot_owner_id = row[0]
                    except Exception:
                        lot_owner_id = "unknown"
                try:
                    result = await fresh_service.close_lot(lot_id, all_users=show_all_users)
                except Exception:
                    logger.exception(
                        "lot_close_failed",
                        extra={"user_id": current_uid, "lot_id": lot_id},
                    )
                    ui.notify("Failed to close lot — please try again", type="negative")
                    return
                if result:
                    try:
                        await audit.log_admin_change(
                            admin_user_id=current_uid,
                            action="lot_closed",
                            target_user_id=lot_owner_id,
                            details={"lot_id": lot_id, "all_users_mode": show_all_users},
                        )
                    except Exception:
                        logger.debug("audit_log_lot_close_failed")
                    ui.notify(f"Lot {lot_id[:8]}... closed", type="positive")
                    # Refresh all sections
                    nonlocal lots, wash_sale_lot_ids, current_prices, suggestions
                    lots = await _load_lots()
                    wash_sale_lot_ids = await _fetch_wash_sale_lot_ids(db_pool, lots)
                    current_prices = await _fetch_current_prices(lots, get_current_user())
                    suggestions = None
                    if current_prices and not show_all_users:
                        try:
                            suggestions = await harvester.find_opportunities(
                                user_id, current_prices
                            )
                        except Exception:
                            logger.warning(
                                "tax_loss_harvesting_failed",
                                extra={"user_id": user_id},
                            )
                    summary_section.refresh()
                    lot_grid.refresh()
                    harvesting_section.refresh()
                else:
                    ui.notify("Lot not found", type="warning")

            @ui.refreshable
            def lot_grid() -> None:
                render_tax_lot_table(
                    lots,
                    wash_sale_lot_ids=wash_sale_lot_ids,
                    on_close=_on_close_lot if is_admin else None,
                    can_close=is_admin,
                )
                # Wash sale limitation note
                with ui.row().classes("mt-2"):
                    ui.icon("info").classes("text-gray-400")
                    ui.label(
                        "Wash sale detection is scoped to open replacement lots only."
                    ).classes("text-gray-400 text-xs")

            lot_grid()

        # Right sidebar: harvesting + export
        with ui.column().classes("w-80"):

            @ui.refreshable
            def harvesting_section() -> None:
                render_harvesting_suggestions(suggestions)

            harvesting_section()

            # Export controls
            ui.separator().classes("my-4")
            ui.label("Reports").classes("text-lg font-bold mb-2")

            if has_permission(user, Permission.EXPORT_DATA):

                async def _export_form_8949() -> None:
                    await _handle_form_8949_export(db_pool, user)

                ui.button(
                    "Download Form 8949 (CSV)",
                    on_click=_export_form_8949,
                    icon="download",
                ).classes("w-full")

            if has_permission(user, Permission.VIEW_TAX_REPORTS):

                async def _preview_form_8949() -> None:
                    await _handle_form_8949_preview(db_pool, user)

                ui.button(
                    "Preview Form 8949",
                    on_click=_preview_form_8949,
                    icon="preview",
                ).props("flat").classes("w-full mt-2")

            if not has_permission(user, Permission.EXPORT_DATA) and not has_permission(
                user, Permission.VIEW_TAX_REPORTS
            ):
                ui.label("No export/preview permissions").classes("text-gray-400 text-sm")


async def _fetch_wash_sale_lot_ids(db_pool: Any, lots: list[Any]) -> set[str]:
    """Query wash sale adjustments for displayed lot IDs."""
    if not lots:
        return set()
    lot_ids = [lot.lot_id for lot in lots]
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT DISTINCT replacement_lot_id::text "
                    "FROM tax_wash_sale_adjustments "
                    "WHERE replacement_lot_id = ANY(%s::uuid[])",
                    (lot_ids,),
                )
                rows = await cur.fetchall()
        return {row["replacement_lot_id"] for row in rows}
    except Exception:
        logger.warning("wash_sale_query_failed", extra={"lot_count": len(lot_ids)})
        return set()


async def _fetch_current_prices(lots: list[Any], user: dict[str, Any]) -> dict[str, Decimal]:
    """Fetch current prices via AsyncTradingClient.

    Graceful degradation: returns empty dict on 403 or any error.
    """
    if not lots:
        return {}
    symbols_needed = {lot.symbol for lot in lots}
    client = AsyncTradingClient.get()
    try:
        raw = await client.fetch_market_prices(
            user.get("user_id", "unknown"),
            role=user.get("role"),
            strategies=user.get("strategies"),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            logger.info("market_prices_permission_denied", extra={"role": user.get("role")})
            return {}
        logger.warning("market_prices_http_error", extra={"status": exc.response.status_code})
        return {}
    except httpx.HTTPError as exc:
        logger.warning("market_prices_fetch_failed", extra={"error": str(exc)})
        return {}
    except ValueError as exc:
        logger.warning("market_prices_invalid_payload", extra={"error": str(exc)})
        return {}
    prices: dict[str, Decimal] = {}
    for item in raw:
        try:
            sym = str(item["symbol"])
            if sym in symbols_needed and item.get("mid") is not None:
                prices[sym] = Decimal(str(item["mid"]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return prices


async def _render_summary_metrics(
    lots: list[Any],
    current_prices: dict[str, Decimal],
    wash_sale_lot_ids: set[str],
    db_pool: Any,
) -> None:
    """Render summary header cards."""
    total_cost = sum(lot.cost_basis for lot in lots)
    total_value = Decimal("0")
    priced_cost = Decimal("0")
    has_prices = bool(current_prices)

    if has_prices:
        for lot in lots:
            price = current_prices.get(lot.symbol)
            if price is not None:
                total_value += price * lot.remaining_quantity
                priced_cost += lot.cost_basis

    all_priced = has_prices and priced_cost == total_cost

    now = datetime.now(UTC)
    short_term = [lot for lot in lots if (now - lot.acquisition_date).days <= 365]
    long_term = [lot for lot in lots if (now - lot.acquisition_date).days > 365]

    with ui.row().classes("w-full gap-4 mb-4"):
        with ui.card().classes("flex-1 p-3"):
            ui.label("Total Cost Basis").classes("text-gray-500 text-sm")
            ui.label(f"${float(total_cost):,.2f}").classes("text-xl font-bold")
        with ui.card().classes("flex-1 p-3"):
            ui.label("Unrealized Gain/Loss").classes("text-gray-500 text-sm")
            if has_prices and priced_cost > 0:
                gain = total_value - priced_cost
                color = "text-green-500" if gain >= 0 else "text-red-500"
                label = f"${float(gain):+,.2f}"
                if not all_priced:
                    label += " (partial)"
                ui.label(label).classes(f"text-xl font-bold {color}")
            else:
                ui.label("N/A").classes("text-xl font-bold text-gray-400")
        with ui.card().classes("flex-1 p-3"):
            ui.label("Short / Long Term").classes("text-gray-500 text-sm")
            ui.label(f"{len(short_term)} / {len(long_term)}").classes("text-xl font-bold")
        with ui.card().classes("flex-1 p-3"):
            ui.label("Wash Sale Lots").classes("text-gray-500 text-sm")
            count = len(wash_sale_lot_ids)
            color = "text-red-500" if count > 0 else ""
            ui.label(str(count)).classes(f"text-xl font-bold {color}")


async def _get_disposition_rows(db_pool: Any, user_id: str) -> list[dict[str, Any]]:
    """Query tax_lot_dispositions joined with tax_lots for Form 8949."""
    async with db_pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT
                    tl.symbol,
                    tl.acquired_at,
                    d.disposed_at,
                    d.quantity,
                    d.cost_basis,
                    d.total_proceeds,
                    d.realized_gain_loss,
                    d.holding_period,
                    d.wash_sale_disallowed
                FROM tax_lot_dispositions d
                JOIN tax_lots tl ON d.lot_id = tl.id
                WHERE tl.user_id = %s
                ORDER BY d.disposed_at DESC
                """,
                (user_id,),
            )
            return list(await cur.fetchall())


_HOLDING_PERIOD_MAP = {"short": "short_term", "long": "long_term"}


def _normalize_holding_period(raw: str | None) -> str:
    """Normalize DB holding_period values to Form 8949 expected values."""
    if not raw:
        return "short_term"
    return _HOLDING_PERIOD_MAP.get(raw, raw)


def _rows_to_report_rows(rows: list[dict[str, Any]]) -> list[Any]:
    """Map DB rows to TaxReportRow dataclass instances."""
    from libs.platform.tax.export import TaxReportRow

    result = []
    for row in rows:
        acquired_at = row["acquired_at"]
        disposed_at = row["disposed_at"]
        result.append(
            TaxReportRow(
                symbol=row["symbol"],
                acquired_date=acquired_at.date() if acquired_at else None,  # type: ignore[arg-type]
                disposed_date=disposed_at.date() if disposed_at else None,  # type: ignore[arg-type]
                quantity=row["quantity"],
                cost_basis=row["cost_basis"],
                proceeds=row["total_proceeds"],
                gain_loss=row["realized_gain_loss"],
                holding_period=_normalize_holding_period(row.get("holding_period", "short")),
                wash_sale_adjustment=row.get("wash_sale_disallowed"),
            )
        )
    return result


async def _handle_form_8949_export(db_pool: Any, user: dict[str, Any]) -> None:
    """Export Form 8949 as CSV download. Requires EXPORT_DATA."""
    current = get_current_user()
    current_uid = current.get("user_id", "unknown")
    if not has_permission(current, Permission.EXPORT_DATA) or not await verify_db_role(
        db_pool, current_uid, Permission.EXPORT_DATA
    ):
        try:
            audit = AuditLogger(db_pool)
            await audit.log_action(
                user_id=current.get("user_id", "unknown"),
                action="form_8949_export_denied",
                resource_type="tax_report",
                resource_id="",
                outcome="denied",
                details={"reason": "permission_denied"},
            )
        except Exception:
            logger.debug("audit_log_export_denied_failed")
        ui.notify("Permission denied", type="negative")
        return

    from libs.platform.tax.form_8949 import Form8949Exporter

    user_id = current.get("user_id", "unknown")
    try:
        rows = await _get_disposition_rows(db_pool, user_id)
    except Exception:
        logger.warning("form_8949_export_query_failed", extra={"user_id": user_id})
        ui.notify("Failed to load disposition data", type="negative")
        return
    if not rows:
        ui.notify("No dispositions to export", type="info")
        return

    try:
        report_rows = _rows_to_report_rows(rows)
        exporter = Form8949Exporter()
        formatted = exporter.format_rows(report_rows)
        csv_data = exporter.to_csv(formatted)
    except Exception:
        logger.warning("form_8949_export_format_failed", extra={"user_id": user_id})
        ui.notify("Failed to generate export", type="negative")
        return
    ui.download(csv_data.encode() if isinstance(csv_data, str) else csv_data, "form_8949.csv")

    # Audit trail for sensitive data export
    try:
        audit = AuditLogger(db_pool)
        await audit.log_action(
            user_id=user_id,
            action="form_8949_export",
            resource_type="tax_report",
            resource_id=user_id,
            outcome="success",
            details={"rows_exported": len(rows)},
        )
    except Exception:
        logger.debug("form_8949_export_audit_failed", extra={"user_id": user_id})


async def _handle_form_8949_preview(db_pool: Any, user: dict[str, Any]) -> None:
    """Preview Form 8949 data on-screen. Requires VIEW_TAX_REPORTS."""
    current = get_current_user()
    current_uid = current.get("user_id", "unknown")
    if not has_permission(current, Permission.VIEW_TAX_REPORTS) or not await verify_db_role(
        db_pool, current_uid, Permission.VIEW_TAX_REPORTS
    ):
        try:
            audit = AuditLogger(db_pool)
            await audit.log_action(
                user_id=current.get("user_id", "unknown"),
                action="form_8949_preview_denied",
                resource_type="tax_report",
                resource_id="",
                outcome="denied",
                details={"reason": "permission_denied"},
            )
        except Exception:
            logger.debug("audit_log_preview_denied_failed")
        ui.notify("Permission denied", type="negative")
        return

    user_id = current.get("user_id", "unknown")
    try:
        rows = await _get_disposition_rows(db_pool, user_id)
    except Exception:
        logger.warning("form_8949_preview_query_failed", extra={"user_id": user_id})
        ui.notify("Failed to load disposition data", type="negative")
        return
    if not rows:
        ui.notify("No dispositions to preview", type="info")
        return

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl"):
        ui.label("Form 8949 Preview").classes("text-lg font-bold mb-2")

        columns = [
            {"name": "symbol", "label": "Symbol", "field": "symbol"},
            {"name": "acquired", "label": "Acquired", "field": "acquired"},
            {"name": "disposed", "label": "Disposed", "field": "disposed"},
            {"name": "quantity", "label": "Qty", "field": "quantity"},
            {"name": "cost_basis", "label": "Cost Basis", "field": "cost_basis"},
            {"name": "proceeds", "label": "Proceeds", "field": "proceeds"},
            {"name": "gain_loss", "label": "Gain/Loss", "field": "gain_loss"},
            {"name": "holding", "label": "Holding", "field": "holding"},
            {"name": "wash_sale", "label": "Wash Sale Adj.", "field": "wash_sale"},
        ]

        table_rows = []
        for row in rows:
            acq = row["acquired_at"]
            disp = row["disposed_at"]
            table_rows.append(
                {
                    "symbol": row["symbol"],
                    "acquired": acq.strftime("%Y-%m-%d") if acq else "-",
                    "disposed": disp.strftime("%Y-%m-%d") if disp else "-",
                    "quantity": float(row["quantity"]) if row["quantity"] else 0,
                    "cost_basis": f"${float(row['cost_basis']):,.2f}"
                    if row["cost_basis"] is not None
                    else "-",
                    "proceeds": f"${float(row['total_proceeds']):,.2f}"
                    if row["total_proceeds"] is not None
                    else "-",
                    "gain_loss": f"${float(row['realized_gain_loss']):+,.2f}"
                    if row["realized_gain_loss"] is not None
                    else "-",
                    "holding": row.get("holding_period", "-"),
                    "wash_sale": f"${float(row['wash_sale_disallowed']):,.2f}"
                    if row.get("wash_sale_disallowed") is not None
                    else "-",
                }
            )

        ui.table(columns=columns, rows=table_rows).classes("w-full")
        ui.button("Close", on_click=dialog.close)
    dialog.open()

    # Audit trail for sensitive data preview
    try:
        audit = AuditLogger(db_pool)
        await audit.log_action(
            user_id=user_id,
            action="form_8949_preview",
            resource_type="tax_report",
            resource_id=user_id,
            outcome="success",
            details={"rows_previewed": len(rows)},
        )
    except Exception:
        logger.debug("form_8949_preview_audit_failed", extra={"user_id": user_id})
