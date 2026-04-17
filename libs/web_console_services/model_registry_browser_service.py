"""Model registry browser service with RBAC enforcement and audit logging.

Reads from the Postgres ``model_registry`` table (created by
``db/legacy/migrations_pre_alembic/001_create_model_registry.sql``), NOT the
file-based registry in ``libs/models/``. The file-based registry uses
staged/production/archived/failed statuses, while the Postgres table uses
active/inactive/testing/failed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from libs.core.common.db import acquire_connection
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
    is_admin,
)

logger = logging.getLogger(__name__)


class ModelRegistryBrowserService:
    """Service for browsing and managing the model registry.

    RBAC scoping: model list is filtered by the user's authorized strategies
    via ``get_authorized_strategies(user)`` + ``VIEW_ALL_STRATEGIES`` fallback.
    """

    def __init__(
        self,
        db_pool: Any,
        audit_logger: AuditLogger,
        *,
        model_registry_url: str | None = None,
        validate_token: str | None = None,
    ) -> None:
        self.db_pool = db_pool
        self.audit_logger = audit_logger
        self._model_registry_url = model_registry_url
        # Private, scoped by naming convention — must only be read by
        # validate_model(), never passed to any other method or used for
        # any other API call.
        self._validate_token = validate_token

    @staticmethod
    def _serialize_model_row(row: Any) -> dict[str, Any]:
        """Convert a model_registry DB row tuple to API payload."""
        return {
            "id": row[0],
            "strategy_name": row[1],
            "version": row[2],
            "model_path": row[3],
            "status": row[4],
            "performance_metrics": row[5],
            "config": row[6],
            "created_at": row[7],
            "activated_at": row[8],
            "deactivated_at": row[9],
            "created_by": row[10],
            "notes": row[11],
        }

    async def list_strategies_with_models(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        """List strategies that have models in the registry, scoped by RBAC."""
        if not has_permission(user, Permission.VIEW_MODELS):
            raise PermissionError("Permission VIEW_MODELS required")

        authorized = get_authorized_strategies(user)
        view_all = has_permission(user, Permission.VIEW_ALL_STRATEGIES)

        async with acquire_connection(self.db_pool) as conn:
            if view_all:
                cursor = await conn.execute(
                    """
                    SELECT DISTINCT strategy_name
                    FROM model_registry
                    ORDER BY strategy_name
                    """
                )
            elif authorized:
                cursor = await conn.execute(
                    """
                    SELECT DISTINCT strategy_name
                    FROM model_registry
                    WHERE strategy_name = ANY(%s)
                    ORDER BY strategy_name
                    """,
                    (authorized,),
                )
            else:
                return []

            rows = await cursor.fetchall()
            return [{"strategy_name": row[0]} for row in rows]

    async def list_models_for_strategies(
        self,
        user: dict[str, Any],
        *,
        strategy_names: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch model rows across strategies in a single query, scoped by RBAC."""
        if not has_permission(user, Permission.VIEW_MODELS):
            raise PermissionError("Permission VIEW_MODELS required")

        requested: list[str] = []
        seen: set[str] = set()
        for raw_name in strategy_names or []:
            name = str(raw_name).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            requested.append(name)
        requested_names = requested or None

        authorized = get_authorized_strategies(user)
        view_all = has_permission(user, Permission.VIEW_ALL_STRATEGIES)

        scoped_names: list[str] | None = None
        if view_all:
            scoped_names = requested_names
        elif requested_names is not None:
            authorized_set = set(authorized)
            scoped_names = [name for name in requested_names if name in authorized_set]
            if not scoped_names:
                return {}
        elif authorized:
            scoped_names = list(dict.fromkeys(authorized))
        else:
            return {}

        async with acquire_connection(self.db_pool) as conn:
            if scoped_names is None:
                cursor = await conn.execute(
                    """
                    SELECT id, strategy_name, version, model_path, status,
                           performance_metrics, config, created_at, activated_at,
                           deactivated_at, created_by, notes
                    FROM model_registry
                    ORDER BY
                        strategy_name,
                        CASE
                            WHEN status = 'active' THEN 1
                            WHEN activated_at IS NOT NULL THEN 2
                            ELSE 3
                        END,
                        activated_at DESC NULLS LAST,
                        created_at DESC
                    """
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT id, strategy_name, version, model_path, status,
                           performance_metrics, config, created_at, activated_at,
                           deactivated_at, created_by, notes
                    FROM model_registry
                    WHERE strategy_name = ANY(%s)
                    ORDER BY
                        strategy_name,
                        CASE
                            WHEN status = 'active' THEN 1
                            WHEN activated_at IS NOT NULL THEN 2
                            ELSE 3
                        END,
                        activated_at DESC NULLS LAST,
                        created_at DESC
                    """,
                    (scoped_names,),
                )

            rows = await cursor.fetchall()
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                payload = self._serialize_model_row(row)
                strategy_name = str(payload["strategy_name"])
                grouped.setdefault(strategy_name, []).append(payload)
            return grouped

    async def get_models_for_strategy(
        self, strategy_name: str, user: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Get all model versions for a strategy (most recent first)."""
        if not has_permission(user, Permission.VIEW_MODELS):
            raise PermissionError("Permission VIEW_MODELS required")

        # RBAC check: user must have access to this strategy
        authorized = get_authorized_strategies(user)
        view_all = has_permission(user, Permission.VIEW_ALL_STRATEGIES)
        if not view_all and strategy_name not in authorized:
            raise PermissionError(f"Access denied to strategy '{strategy_name}'")

        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                """
                SELECT id, strategy_name, version, model_path, status,
                       performance_metrics, config, created_at, activated_at,
                       deactivated_at, created_by, notes
                FROM model_registry
                WHERE strategy_name = %s
                ORDER BY
                    CASE
                        WHEN status = 'active' THEN 1
                        WHEN activated_at IS NOT NULL THEN 2
                        ELSE 3
                    END,
                    activated_at DESC NULLS LAST,
                    created_at DESC
                """,
                (strategy_name,),
            )
            rows = await cursor.fetchall()
            return [self._serialize_model_row(row) for row in rows]

    async def activate_model(
        self,
        strategy_name: str,
        version: str,
        user: dict[str, Any],
    ) -> None:
        """Activate a model version (admin-only).

        Uses the Postgres ``activate_model()`` function which atomically
        deactivates all other versions for the strategy.

        Service-layer precheck: raises ValueError when activating an
        already-active model. The DB function is idempotent and would
        silently re-activate, but we enforce this constraint explicitly.
        """
        if not is_admin(user):
            raise PermissionError("Admin role required for model activation")

        user_id = user.get("user_id", "unknown")

        async with acquire_connection(self.db_pool) as conn:
            # Lock ALL rows for this strategy to serialize concurrent activations.
            # Without this, two admins activating different versions simultaneously
            # could both succeed, leaving two active rows.
            await conn.execute(
                "SELECT 1 FROM model_registry WHERE strategy_name = %s FOR UPDATE",
                (strategy_name,),
            )

            # Precheck: ensure target model exists and is not already active
            cursor = await conn.execute(
                "SELECT status FROM model_registry WHERE strategy_name = %s AND version = %s",
                (strategy_name, version),
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Model not found: strategy={strategy_name}, version={version}")
            if row[0] == "active":
                raise ValueError(
                    f"Model already active: strategy={strategy_name}, version={version}"
                )

            # Call the DB function for atomic activation
            await conn.execute(
                "SELECT activate_model(%s, %s)",
                (strategy_name, version),
            )

        await self.audit_logger.log_action(
            user_id=user_id,
            action="MODEL_ACTIVATED",
            resource_type="model",
            resource_id=f"{strategy_name}/{version}",
            outcome="success",
            details={
                "strategy_name": strategy_name,
                "version": version,
                "previous_status": row[0],
            },
        )

    async def deactivate_model(
        self,
        strategy_name: str,
        version: str,
        user: dict[str, Any],
    ) -> None:
        """Deactivate a model version (admin-only).

        NOTE: This sets the DB status to 'inactive' for administrative tracking.
        It does NOT stop a running signal service from using an already-loaded
        model — the signal service loads models from files, not DB status.
        To stop signal generation, use the strategy toggle (active=False).
        """
        if not is_admin(user):
            raise PermissionError("Admin role required for model deactivation")

        user_id = user.get("user_id", "unknown")

        async with acquire_connection(self.db_pool) as conn:
            cursor = await conn.execute(
                """
                SELECT status FROM model_registry
                WHERE strategy_name = %s AND version = %s
                FOR UPDATE
                """,
                (strategy_name, version),
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Model not found: strategy={strategy_name}, version={version}")

            await conn.execute(
                """
                UPDATE model_registry
                SET status = 'inactive', deactivated_at = NOW()
                WHERE strategy_name = %s AND version = %s
                """,
                (strategy_name, version),
            )

        await self.audit_logger.log_action(
            user_id=user_id,
            action="MODEL_DEACTIVATED",
            resource_type="model",
            resource_id=f"{strategy_name}/{version}",
            outcome="success",
            details={
                "strategy_name": strategy_name,
                "version": version,
                "previous_status": row[0],
            },
        )

    async def validate_model(
        self,
        model_type: str,
        version: str,
        user: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Optional: validate a model via the model_registry API.

        ``model_type`` must be a ``ModelType`` enum value (e.g.
        ``"alpha_weights"``, ``"risk_model"``), NOT a strategy name.

        Returns validation result or None if API unavailable (graceful
        degradation). Uses the ``_validate_token`` stored at init time.
        Admin-only: model validation uses a privileged service token.
        """
        if not is_admin(user):
            raise PermissionError("Admin role required for model validation")

        if not self._model_registry_url or not self._validate_token:
            return None

        try:
            async with httpx.AsyncClient(base_url=self._model_registry_url, timeout=30.0) as client:
                resp = await client.post(
                    f"/api/v1/models/{model_type}/{version}/validate",
                    headers={"Authorization": f"Bearer {self._validate_token}"},
                )
                if resp.is_success:
                    try:
                        result: dict[str, Any] = resp.json()
                        return result
                    except ValueError:
                        logger.warning(
                            "model_validate_json_decode_error",
                            extra={"model_type": model_type, "version": version},
                        )
                        return None
                logger.warning(
                    "model_validate_api_error",
                    extra={
                        "model_type": model_type,
                        "version": version,
                        "status": resp.status_code,
                    },
                )
                return None
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning(
                "model_validate_api_unavailable",
                extra={
                    "model_type": model_type,
                    "version": version,
                    "error": str(exc),
                },
            )
            return None


__all__ = ["ModelRegistryBrowserService"]
