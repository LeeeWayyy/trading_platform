"""Workspace persistence for grid/panel state."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from apps.web_console_ng.core.database import get_db_pool

logger = logging.getLogger(__name__)

# Current schema versions for each workspace type
SCHEMA_VERSIONS = {
    "grid": 1,
    "panel": 1,
}

MAX_STATE_SIZE = 65536  # 64KB


class DatabaseUnavailableError(Exception):
    """Raised when database pool is not configured."""

    pass


@dataclass
class WorkspaceState:
    """Workspace state container."""

    user_id: str
    workspace_key: str
    state: dict[str, Any]
    schema_version: int


def _require_db_pool() -> Any:
    """Get DB pool or raise DatabaseUnavailableError.

    IMPORTANT: get_db_pool() returns None when DATABASE_URL is unset.
    All callers must handle this case to avoid AttributeError on None.

    Returns AsyncConnectionPool but typed as Any for simplicity.
    """
    pool = get_db_pool()
    if pool is None:
        raise DatabaseUnavailableError("Database pool not configured (DATABASE_URL unset)")
    return pool


class WorkspacePersistenceService:
    """Service for saving/loading workspace state.

    Note: Uses psycopg AsyncConnectionPool with async context managers.
    The project's DB pool is AsyncConnectionPool, so we must use async APIs.
    """

    async def save_grid_state(
        self,
        user_id: str,
        grid_id: str,
        state: dict[str, Any],
    ) -> bool:
        """Save grid column state (order, width, sort, filter).

        Args:
            user_id: User identifier
            grid_id: Grid identifier (e.g., 'positions_grid')
            state: AG Grid state object from getColumnState()

        Returns:
            True if saved successfully

        Raises:
            DatabaseUnavailableError: If database pool is not configured
        """
        workspace_key = f"grid.{grid_id}"
        state_json = json.dumps(state)

        # Use byte length for parity with DB constraint (octet_length)
        state_bytes = len(state_json.encode("utf-8"))
        if state_bytes > MAX_STATE_SIZE:
            logger.warning(
                "workspace_state_too_large",
                extra={
                    "user_id": user_id,
                    "workspace_key": workspace_key,
                    "size_bytes": state_bytes,
                    "limit": MAX_STATE_SIZE,
                },
            )
            return False

        pool = _require_db_pool()
        # Use async context managers for AsyncConnectionPool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO workspace_state (user_id, workspace_key, state_json, schema_version)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, workspace_key)
                    DO UPDATE SET state_json = EXCLUDED.state_json, schema_version = EXCLUDED.schema_version
                    """,
                    (user_id, workspace_key, state_json, SCHEMA_VERSIONS["grid"]),
                )
            await conn.commit()

        logger.info(
            "workspace_state_saved",
            extra={
                "user_id": user_id,
                "workspace_key": workspace_key,
                "size_bytes": state_bytes,
            },
        )
        return True

    async def load_grid_state(
        self,
        user_id: str,
        grid_id: str,
    ) -> dict[str, Any] | None:
        """Load grid column state.

        Returns None if no state saved or schema version mismatch.

        Raises:
            DatabaseUnavailableError: If database pool is not configured
        """
        workspace_key = f"grid.{grid_id}"

        pool = _require_db_pool()
        # Use async context managers for AsyncConnectionPool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT state_json, schema_version
                    FROM workspace_state
                    WHERE user_id = %s AND workspace_key = %s
                    """,
                    (user_id, workspace_key),
                )
                row = await cur.fetchone()

        if not row:
            return None

        state_json, saved_version = row
        current_version = SCHEMA_VERSIONS["grid"]

        if saved_version != current_version:
            logger.warning(
                "workspace_state_schema_mismatch",
                extra={
                    "user_id": user_id,
                    "workspace_key": workspace_key,
                    "saved_version": saved_version,
                    "current_version": current_version,
                },
            )
            # Return None to use defaults (don't apply stale state)
            return None

        try:
            result: dict[str, Any] = json.loads(state_json)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "workspace_state_corrupt_json",
                extra={
                    "user_id": user_id,
                    "workspace_key": workspace_key,
                    "error": type(exc).__name__,
                },
            )
            return None
        if not isinstance(result, dict):
            logger.warning(
                "workspace_state_invalid_type",
                extra={
                    "user_id": user_id,
                    "workspace_key": workspace_key,
                    "type": type(result).__name__,
                },
            )
            return None
        return result

    async def reset_workspace(self, user_id: str, workspace_key: str | None = None) -> None:
        """Reset workspace state to defaults.

        Args:
            user_id: User identifier
            workspace_key: Optional specific key to reset (None = reset all)

        Raises:
            DatabaseUnavailableError: If database pool is not configured
        """
        pool = _require_db_pool()
        # Use async context managers for AsyncConnectionPool
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                if workspace_key:
                    await cur.execute(
                        "DELETE FROM workspace_state WHERE user_id = %s AND workspace_key = %s",
                        (user_id, workspace_key),
                    )
                else:
                    await cur.execute(
                        "DELETE FROM workspace_state WHERE user_id = %s",
                        (user_id,),
                    )
            await conn.commit()

        logger.info(
            "workspace_state_reset",
            extra={"user_id": user_id, "workspace_key": workspace_key or "all"},
        )


# Singleton instance
_workspace_service: WorkspacePersistenceService | None = None


def get_workspace_service() -> WorkspacePersistenceService:
    """Get workspace persistence service singleton."""
    global _workspace_service
    if _workspace_service is None:
        _workspace_service = WorkspacePersistenceService()
    return _workspace_service


__all__ = [
    "WorkspaceState",
    "WorkspacePersistenceService",
    "DatabaseUnavailableError",
    "get_workspace_service",
    "SCHEMA_VERSIONS",
]
