"""Service layer for tax lot tracking in the web console."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg.rows import dict_row

from libs.core.common.db import acquire_connection
from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaxLot:
    """Tax lot snapshot for UI and reporting."""

    lot_id: str
    symbol: str
    quantity: Decimal
    cost_basis: Decimal
    acquisition_date: datetime
    strategy_id: str | None
    status: str
    remaining_quantity: Decimal


class TaxLotService:
    """CRUD service for tax lots."""

    def __init__(self, db_pool: Any, user: dict[str, Any]) -> None:
        self._db_pool = db_pool
        self._user = user

    async def list_lots(
        self,
        user_id: str | None = None,
        *,
        all_users: bool = False,
        open_only: bool = False,
        limit: int = 500,
    ) -> list[TaxLot]:
        """List tax lots.

        Args:
            user_id: Filter by specific user. Defaults to current user if not provided.
            all_users: If True, list lots for all users (requires MANAGE_TAX_LOTS).
            open_only: If True, only return lots with remaining_quantity > 0 and closed_at IS NULL.
            limit: Maximum number of lots to return (default 500, max 500).

        Raises:
            ValueError: If limit is not between 1 and 500.
        """
        self._require_permission(Permission.VIEW_TAX_LOTS)

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        current_user_id = self._user.get("user_id")

        if all_users or (user_id and user_id != current_user_id):
            self._require_permission(Permission.MANAGE_TAX_LOTS)

        if not all_users and not current_user_id:
            logger.warning(
                "tax_lot_list_denied_no_user",
                extra={"all_users": all_users, "user_id": user_id},
            )
            raise PermissionError("User context required for listing tax lots")

        target_user_id = None if all_users else (user_id or current_user_id)

        # Build query with optional open_only filter
        where_clauses: list[str] = []
        params: list[Any] = []

        if target_user_id:
            where_clauses.append("user_id = %s")
            params.append(target_user_id)

        if open_only:
            where_clauses.append("remaining_quantity > 0")
            where_clauses.append("closed_at IS NULL")

        where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        params.append(limit)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"SELECT * FROM tax_lots{where_sql} ORDER BY acquired_at DESC LIMIT %s",
                    tuple(params),
                )
                rows = await cur.fetchall()

        return [self._row_to_lot(row) for row in rows]

    async def get_lot(
        self, lot_id: str, user_id: str | None = None, *, all_users: bool = False
    ) -> TaxLot | None:
        """Fetch a single tax lot by ID (user scoped)."""
        self._require_permission(Permission.VIEW_TAX_LOTS)

        current_user_id = self._user.get("user_id")

        if all_users or (user_id and user_id != current_user_id):
            self._require_permission(Permission.MANAGE_TAX_LOTS)

        if not all_users and not current_user_id:
            logger.warning(
                "tax_lot_get_denied_no_user",
                extra={"lot_id": lot_id, "user_id": user_id},
            )
            raise PermissionError("User context required for fetching tax lots")

        target_user_id = None if all_users else (user_id or current_user_id)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if target_user_id:
                    await cur.execute(
                        "SELECT * FROM tax_lots WHERE id = %s AND user_id = %s",
                        (lot_id, target_user_id),
                    )
                else:
                    await cur.execute(
                        "SELECT * FROM tax_lots WHERE id = %s",
                        (lot_id,),
                    )
                row = await cur.fetchone()

        if not row:
            return None

        return self._row_to_lot(row)

    async def create_lot(
        self,
        *,
        symbol: str,
        quantity: Decimal | float | str,
        cost_basis: Decimal | float | str,
        acquisition_date: datetime,
        strategy_id: str | None,
        status: str,
        user_id: str | None = None,
    ) -> TaxLot:
        """Create a new tax lot.

        Note: strategy_id is NOT persisted to DB (schema doesn't have this column).
        It's carried in the returned TaxLot for UI display purposes only.
        """
        self._require_permission(Permission.MANAGE_TAX_LOTS)

        # Validate acquisition_date type early for clear error message
        if not isinstance(acquisition_date, datetime):
            raise ValueError(f"acquisition_date must be datetime, got {type(acquisition_date)}")

        owner_id = user_id or self._user.get("user_id")
        if not owner_id:
            logger.warning(
                "tax_lot_create_denied_no_user",
                extra={"symbol": symbol},
            )
            raise PermissionError("User context required for creating tax lots")

        quantity_decimal = _to_decimal(quantity)
        cost_basis_decimal = _to_decimal(cost_basis)
        cost_per_share = cost_basis_decimal / quantity_decimal if quantity_decimal else Decimal("0")

        status_normalized = (status or "open").strip().lower()
        if status_normalized not in {"open", "closed"}:
            raise ValueError(f"status must be 'open' or 'closed', got: {status_normalized}")
        remaining_quantity = Decimal("0") if status_normalized == "closed" else quantity_decimal
        closed_at = datetime.now(UTC) if status_normalized == "closed" else None

        async with acquire_connection(self._db_pool) as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        INSERT INTO tax_lots (
                            user_id,
                            symbol,
                            quantity,
                            cost_per_share,
                            total_cost,
                            acquired_at,
                            acquisition_type,
                            remaining_quantity,
                            closed_at,
                            created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING *
                        """,
                        (
                            owner_id,
                            symbol,
                            quantity_decimal,
                            cost_per_share,
                            cost_basis_decimal,
                            acquisition_date,
                            "manual",
                            remaining_quantity,
                            closed_at,
                        ),
                    )
                    row = await cur.fetchone()

        if not row:
            raise RuntimeError("Tax lot creation failed")

        return self._row_to_lot(
            row,
            strategy_override=strategy_id,
            status_override=status_normalized,
        )

    async def update_lot(
        self,
        lot_id: str,
        updates: dict[str, Any],
        *,
        user_id: str | None = None,
        all_users: bool = False,
    ) -> TaxLot:
        """Update an existing tax lot (user-scoped).

        Args:
            lot_id: The lot ID to update.
            updates: Fields to update (symbol, quantity, cost_basis, acquisition_date, status).
            user_id: Target user. Defaults to current user.
            all_users: If True, allow updating any user's lot (requires MANAGE_TAX_LOTS).
        """
        self._require_permission(Permission.MANAGE_TAX_LOTS)

        current_user_id = self._user.get("user_id")

        # Cross-user access requires explicit all_users flag
        if all_users or (user_id and user_id != current_user_id):
            # Already have MANAGE_TAX_LOTS, but log for audit
            logger.info(
                "tax_lot_cross_user_update",
                extra={"lot_id": lot_id, "target_user": user_id, "all_users": all_users},
            )

        if not all_users and not current_user_id:
            raise PermissionError("User context required for updating tax lots")

        target_user_id = None if all_users else (user_id or current_user_id)

        # strategy_id is not persisted in DB schema, so exclude from updates
        allowed = {"symbol", "quantity", "cost_basis", "acquisition_date", "status"}
        update_keys = [key for key in updates.keys() if key in allowed]
        if not update_keys:
            raise ValueError("No valid fields provided for update")

        async with acquire_connection(self._db_pool) as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    # User-scoped query to prevent cross-user writes
                    # FOR UPDATE prevents concurrent updates from overwriting each other
                    if target_user_id:
                        await cur.execute(
                            "SELECT * FROM tax_lots WHERE id = %s AND user_id = %s FOR UPDATE",
                            (lot_id, target_user_id),
                        )
                    else:
                        await cur.execute(
                            "SELECT * FROM tax_lots WHERE id = %s FOR UPDATE", (lot_id,)
                        )
                    row = await cur.fetchone()
                    if not row:
                        raise ValueError(f"Tax lot {lot_id} not found")

                    current_quantity = _to_decimal(row.get("quantity"))
                    current_cost = _to_decimal(
                        row.get("total_cost")
                        if row.get("total_cost") is not None
                        else row.get("cost_basis")
                    )

                    new_quantity = _to_decimal(updates.get("quantity", current_quantity))
                    new_cost = _to_decimal(updates.get("cost_basis", current_cost))
                    new_acquired_at = updates.get(
                        "acquisition_date",
                        row.get("acquired_at") or row.get("acquisition_date"),
                    )
                    new_symbol = updates.get("symbol", row.get("symbol"))

                    status_override = updates.get("status")
                    if isinstance(status_override, str):
                        status_normalized = status_override.strip().lower()
                        if status_normalized not in {"open", "closed"}:
                            raise ValueError(
                                f"status must be 'open' or 'closed', got: {status_normalized}"
                            )
                    else:
                        status_normalized = self._derive_status(row)

                    remaining_quantity = row.get("remaining_quantity", new_quantity)
                    closed_at = row.get("closed_at")
                    if status_normalized == "closed":
                        remaining_quantity = Decimal("0")
                        closed_at = datetime.now(UTC)
                    elif status_normalized == "open" and isinstance(updates.get("status"), str):
                        # Only reset remaining_quantity when re-opening a closed lot;
                        # confirming an already-open lot must preserve partial-sale state.
                        current_status = self._derive_status(row)
                        if current_status == "closed":
                            remaining_quantity = new_quantity
                        closed_at = None

                    # Validate and cap remaining_quantity when quantity changes.
                    if "quantity" in updates:
                        disposed = current_quantity - _to_decimal(
                            row.get("remaining_quantity", current_quantity)
                        )
                        if disposed > Decimal("0") and new_quantity < disposed:
                            raise ValueError(
                                f"Cannot reduce quantity below disposed shares "
                                f"({disposed}); would erase disposition history"
                            )
                        remaining_quantity = new_quantity - disposed

                    set_clauses: list[str] = []
                    values: list[Any] = []

                    if "symbol" in updates:
                        set_clauses.append("symbol = %s")
                        values.append(new_symbol)
                    if "quantity" in updates:
                        set_clauses.append("quantity = %s")
                        values.append(new_quantity)
                    if "cost_basis" in updates:
                        set_clauses.append("total_cost = %s")
                        values.append(new_cost)
                    if "quantity" in updates or "cost_basis" in updates:
                        # When quantity changes without cost_basis, total_cost is preserved
                        # and cost_per_share is recalculated. This maintains the total_cost
                        # invariant but may not be the intended behavior.
                        if "quantity" in updates and "cost_basis" not in updates:
                            logger.warning(
                                "tax_lot_quantity_updated_without_cost_basis",
                                extra={
                                    "lot_id": str(lot_id),
                                    "old_quantity": str(current_quantity),
                                    "new_quantity": str(new_quantity),
                                    "total_cost_preserved": str(current_cost),
                                    "note": "cost_per_share recalculated; provide cost_basis to update total_cost",
                                },
                            )
                        cost_per_share = new_cost / new_quantity if new_quantity else Decimal("0")
                        set_clauses.append("cost_per_share = %s")
                        values.append(cost_per_share)
                    if "acquisition_date" in updates:
                        if not isinstance(new_acquired_at, datetime):
                            raise ValueError(
                                f"acquisition_date must be datetime, got {type(new_acquired_at)}"
                            )
                        set_clauses.append("acquired_at = %s")
                        values.append(new_acquired_at)
                    # Write remaining_quantity when quantity changes without an explicit status
                    status_is_explicit = "status" in updates and isinstance(status_override, str)
                    if "quantity" in updates and not status_is_explicit:
                        set_clauses.append("remaining_quantity = %s")
                        values.append(remaining_quantity)
                    # Only update status if a valid string was provided (ignore null/non-string values)
                    if status_is_explicit:
                        set_clauses.append("remaining_quantity = %s")
                        values.append(remaining_quantity)
                        set_clauses.append("closed_at = %s")
                        values.append(closed_at)

                    if not set_clauses:
                        raise ValueError("No valid fields provided for update")

                    # User-scoped UPDATE to prevent cross-user writes
                    if target_user_id:
                        query = (
                            "UPDATE tax_lots SET "
                            + ", ".join(set_clauses)
                            + " WHERE id = %s AND user_id = %s RETURNING *"
                        )
                        values.extend([lot_id, target_user_id])
                    else:
                        query = (
                            "UPDATE tax_lots SET "
                            + ", ".join(set_clauses)
                            + " WHERE id = %s RETURNING *"
                        )
                        values.append(lot_id)
                    await cur.execute(query, tuple(values))
                    updated = await cur.fetchone()

        if not updated:
            raise RuntimeError(f"Tax lot {lot_id} update failed")

        return self._row_to_lot(
            updated,
            status_override=updates.get("status"),
        )

    async def close_lot(
        self,
        lot_id: str,
        *,
        user_id: str | None = None,
        all_users: bool = False,
    ) -> TaxLot | None:
        """Close a tax lot by zeroing remaining quantity (user-scoped).

        Args:
            lot_id: The lot ID to close.
            user_id: Target user. Defaults to current user.
            all_users: If True, allow closing any user's lot (requires MANAGE_TAX_LOTS).
        """
        self._require_permission(Permission.MANAGE_TAX_LOTS)

        current_user_id = self._user.get("user_id")

        if not all_users and not current_user_id:
            raise PermissionError("User context required for closing tax lots")

        target_user_id = None if all_users else (user_id or current_user_id)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    # User-scoped UPDATE to prevent cross-user writes
                    if target_user_id:
                        await cur.execute(
                            """
                            UPDATE tax_lots
                            SET remaining_quantity = 0,
                                closed_at = NOW()
                            WHERE id = %s AND user_id = %s
                            RETURNING *
                            """,
                            (lot_id, target_user_id),
                        )
                    else:
                        await cur.execute(
                            """
                            UPDATE tax_lots
                            SET remaining_quantity = 0,
                                closed_at = NOW()
                            WHERE id = %s
                            RETURNING *
                            """,
                            (lot_id,),
                        )
                    row = await cur.fetchone()

        if not row:
            return None

        return self._row_to_lot(row, status_override="closed")

    _VALID_COST_BASIS_METHODS = {"fifo", "lifo", "specific_id"}

    async def get_cost_basis_method(self, user_id: str | None = None) -> str:
        """Get cost basis method for a user from tax_user_settings.

        Returns 'fifo' if no setting exists or the DB value is invalid.
        """
        self._require_permission(Permission.VIEW_TAX_LOTS)
        target = user_id or self._user.get("user_id")
        if not target:
            return "fifo"

        # Cross-user access requires elevated permission
        current_uid = self._user.get("user_id")
        if user_id and user_id != current_uid:
            self._require_permission(Permission.MANAGE_TAX_SETTINGS)

        async with acquire_connection(self._db_pool) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT cost_basis_method FROM tax_user_settings WHERE user_id = %s",
                    (target,),
                )
                row = await cur.fetchone()

        if row and row.get("cost_basis_method"):
            method = str(row["cost_basis_method"])
            if method in self._VALID_COST_BASIS_METHODS:
                return method
        return "fifo"

    async def set_cost_basis_method(self, method: str, user_id: str | None = None) -> None:
        """Set cost basis method for a user in tax_user_settings.

        Requires MANAGE_TAX_SETTINGS permission.
        """
        self._require_permission(Permission.MANAGE_TAX_SETTINGS)

        method_lower = method.strip().lower()
        if method_lower not in self._VALID_COST_BASIS_METHODS:
            raise ValueError(
                f"Invalid cost basis method: {method}. "
                f"Must be one of: {', '.join(sorted(self._VALID_COST_BASIS_METHODS))}"
            )

        target = user_id or self._user.get("user_id")
        if not target:
            raise PermissionError("User context required")

        async with acquire_connection(self._db_pool) as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO tax_user_settings (user_id, cost_basis_method)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id) DO UPDATE
                        SET cost_basis_method = EXCLUDED.cost_basis_method
                        """,
                        (target, method_lower),
                    )

    def _require_permission(self, permission: Permission) -> None:
        if not has_permission(self._user, permission):
            logger.warning(
                "tax_lot_permission_denied",
                extra={
                    "user_id": self._user.get("user_id"),
                    "permission": permission.value,
                },
            )
            raise PermissionError(f"Permission {permission.value} required")

    def _row_to_lot(
        self,
        row: Mapping[str, Any],
        *,
        strategy_override: str | None = None,
        status_override: str | None = None,
    ) -> TaxLot:
        lot_id = row.get("id") or row.get("lot_id")
        acquisition_date_raw = row.get("acquired_at") or row.get("acquisition_date")
        if not isinstance(acquisition_date_raw, datetime):
            raise ValueError(f"acquisition_date must be datetime, got {type(acquisition_date_raw)}")
        acquisition_date: datetime = acquisition_date_raw

        cost_basis_value = row.get("total_cost")
        if cost_basis_value is None:
            cost_basis_value = row.get("cost_basis")

        quantity_value = row.get("quantity")

        strategy_id_raw = (
            strategy_override if strategy_override is not None else row.get("strategy_id")
        )
        # Defensive: convert "None" string to actual None
        strategy_id = None if strategy_id_raw in (None, "None", "") else str(strategy_id_raw)
        status = (
            status_override.strip().lower()
            if isinstance(status_override, str)
            else row.get("status") or self._derive_status(row)
        )

        quantity_dec = _to_decimal(quantity_value)
        remaining_raw = row.get("remaining_quantity")
        remaining_dec = _to_decimal(remaining_raw) if remaining_raw is not None else quantity_dec

        return TaxLot(
            lot_id=str(lot_id),
            symbol=str(row.get("symbol", "")),
            quantity=quantity_dec,
            cost_basis=_to_decimal(cost_basis_value),
            acquisition_date=acquisition_date,
            strategy_id=strategy_id,
            status=status,
            remaining_quantity=remaining_dec,
        )

    @staticmethod
    def _derive_status(row: Mapping[str, Any]) -> str:
        closed_at = row.get("closed_at")
        remaining_quantity = row.get("remaining_quantity")
        if closed_at is not None:
            return "closed"
        if remaining_quantity is not None:
            try:
                remaining = _to_decimal(remaining_quantity)
            except (InvalidOperation, ValueError):
                remaining = Decimal("0")
            if remaining <= 0:
                return "closed"
        return "open"


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


__all__ = ["TaxLot", "TaxLotService"]
