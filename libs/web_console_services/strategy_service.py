"""Strategy management service with RBAC enforcement and audit logging."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from libs.core.common.db import acquire_connection
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)

logger = logging.getLogger(__name__)


class StrategyService:
    """Service for strategy management with RBAC and audit logging.

    Strategies are listed from the ``strategies`` table (created in migration
    0006). The ``active`` column (added in migration 0028) controls whether
    the signal service generates signals for the strategy.

    RBAC scoping: strategy list is filtered by ``get_authorized_strategies(user)``
    plus ``VIEW_ALL_STRATEGIES`` fallback, matching the pattern used in
    ``compare.py``, ``exposure_service.py``, etc.
    """

    def __init__(self, db_pool: Any, audit_logger: AuditLogger) -> None:
        self.db_pool = db_pool
        self.audit_logger = audit_logger

    async def get_strategies(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch strategies scoped to user's authorized strategies.

        Returns list of dicts with strategy_id, name, description, active,
        updated_at, updated_by, and activity_status (derived from orders).
        """
        if not has_permission(user, Permission.MANAGE_STRATEGIES):
            raise PermissionError("Permission MANAGE_STRATEGIES required")

        authorized = get_authorized_strategies(user)
        view_all = has_permission(user, Permission.VIEW_ALL_STRATEGIES)

        # Single query with LEFT JOIN to avoid N+1 for activity status
        base_query = """
            SELECT s.strategy_id, s.name, s.description, s.active,
                   s.updated_at, s.updated_by,
                   oa.last_order_at
            FROM strategies s
            LEFT JOIN (
                SELECT strategy_id, MAX(created_at) AS last_order_at
                FROM orders
                GROUP BY strategy_id
            ) oa ON oa.strategy_id = s.strategy_id
        """

        async with acquire_connection(self.db_pool) as conn:
            if view_all:
                cursor = await conn.execute(base_query + " ORDER BY s.name")
            elif authorized:
                cursor = await conn.execute(
                    base_query + " WHERE s.strategy_id = ANY(%s) ORDER BY s.name",
                    (authorized,),
                )
            else:
                return []

            rows = await cursor.fetchall()

            strategies = []
            for row in rows:
                last_order_at = row[6]
                activity = self._derive_activity_status(last_order_at)
                strategies.append(
                    {
                        "strategy_id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "active": row[3],
                        "updated_at": row[4],
                        "updated_by": row[5],
                        "activity_status": activity,
                    }
                )

            return strategies

    def _derive_activity_status(self, last_order_at: Any) -> str:
        """Derive activity status from last order timestamp.

        Returns:
            "active" if last_order_at is within the last 24h
            "idle" if older
            "unknown" if last_order_at is None
        """
        if last_order_at is None:
            return "unknown"
        now = datetime.now(UTC)
        if hasattr(last_order_at, "tzinfo") and last_order_at.tzinfo is None:
            last_order_at = last_order_at.replace(tzinfo=UTC)
        delta = now - last_order_at
        if delta.total_seconds() <= 86400:  # 24 hours
            return "active"
        return "idle"

    async def _get_activity_status(self, conn: Any, strategy_id: str) -> str:
        """Derive activity status from orders table (single-strategy query).

        Used by callers that need status for a single strategy outside
        of the bulk get_strategies flow.

        Returns:
            "active" if MAX(created_at) across ALL orders is within the last 24h
            "idle" if older
            "unknown" if no orders exist or DB query fails
        """
        try:
            cursor = await conn.execute(
                """
                SELECT MAX(created_at) as last_order_at
                FROM orders
                WHERE strategy_id = %s
                """,
                (strategy_id,),
            )
            row = await cursor.fetchone()
            return self._derive_activity_status(row[0] if row else None)
        except Exception:
            logger.warning(
                "activity_status_query_failed",
                extra={"strategy_id": strategy_id},
            )
            return "unknown"

    async def get_open_exposure(self, strategy_id: str, user: dict[str, Any]) -> dict[str, Any]:
        """Check for open positions/orders for a strategy.

        Returns exposure data for the UI to display a confirmation dialog.
        The service does NOT block toggle — the confirm/cancel decision is
        a UI-layer concern.
        """
        if not has_permission(user, Permission.MANAGE_STRATEGIES):
            raise PermissionError("Permission MANAGE_STRATEGIES required")

        async with acquire_connection(self.db_pool) as conn:
            # Count positions for symbols this strategy has traded.
            # Includes ambiguous symbols (traded by multiple strategies) to
            # prefer over-warning rather than under-warning.
            cursor = await conn.execute(
                """
                SELECT
                    (SELECT COUNT(*)
                     FROM positions p
                     WHERE p.qty != 0
                       AND p.symbol IN (
                           SELECT DISTINCT symbol FROM orders WHERE strategy_id = %s
                       )) as positions_count,
                    (SELECT COUNT(*)
                     FROM orders
                     WHERE strategy_id = %s
                       AND is_terminal = FALSE) as open_orders_count
                """,
                (strategy_id, strategy_id),
            )
            row = await cursor.fetchone()

            if not row:
                return {"positions_count": 0, "open_orders_count": 0}

            return {
                "positions_count": row[0] or 0,
                "open_orders_count": row[1] or 0,
            }

    async def toggle_strategy(
        self,
        strategy_id: str,
        *,
        active: bool,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """Toggle strategy active status.

        Requires MANAGE_STRATEGIES permission. Returns updated strategy dict.
        The UI must have already checked open exposure and confirmed the action.
        """
        if not has_permission(user, Permission.MANAGE_STRATEGIES):
            raise PermissionError("Permission MANAGE_STRATEGIES required")

        user_id = user.get("user_id", "unknown")
        previous_active: bool | None = None

        async with acquire_connection(self.db_pool) as conn:
            # Fetch current state for audit trail (FOR UPDATE prevents concurrent toggle race)
            cursor = await conn.execute(
                "SELECT active FROM strategies WHERE strategy_id = %s FOR UPDATE",
                (strategy_id,),
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Strategy '{strategy_id}' not found")
            previous_active = row[0]

            # Update
            await conn.execute(
                """
                UPDATE strategies
                SET active = %s, updated_by = %s
                WHERE strategy_id = %s
                """,
                (active, user_id, strategy_id),
            )

            # Fetch updated row
            cursor = await conn.execute(
                """
                SELECT strategy_id, name, description, active,
                       updated_at, updated_by
                FROM strategies
                WHERE strategy_id = %s
                """,
                (strategy_id,),
            )
            updated = await cursor.fetchone()

        if not updated:
            raise RuntimeError(f"Strategy '{strategy_id}' not found after update")

        await self.audit_logger.log_action(
            user_id=user_id,
            action="STRATEGY_TOGGLED",
            resource_type="strategy",
            resource_id=strategy_id,
            outcome="success",
            details={
                "previous_active": previous_active,
                "new_active": active,
            },
        )

        return {
            "strategy_id": updated[0],
            "name": updated[1],
            "description": updated[2],
            "active": updated[3],
            "updated_at": updated[4],
            "updated_by": updated[5],
        }


__all__ = ["StrategyService"]
