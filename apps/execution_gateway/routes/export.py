"""
Export audit routes for Execution Gateway (P6T8).

Provides endpoints for export audit tracking with compliance logging:
- POST /api/v1/export/audit - Create export audit record (returns audit_id)
- PATCH /api/v1/export/audit/{audit_id} - Complete export with actual row count
- GET /api/v1/export/excel/{audit_id} - Download Excel file (single-use token)

Security features:
- Export permission required (EXPORT_DATA)
- IP address and session tracking
- Single-use Excel download links

Design Pattern:
    - Router defined at module level
    - Dependencies injected via Depends()
    - Audit records created before export, completed after
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from psycopg.sql import SQL, Composable, Identifier
from pydantic import BaseModel, Field

from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.api.utils import get_client_ip, get_user_agent
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import get_context
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import APIAuthConfig, AuthContext, api_auth
from libs.data.sql.strategy_mapping_sql import SYMBOL_STRATEGY_CTE
from libs.platform.security import sanitize_for_export
from libs.platform.web_console_auth.permissions import Permission, get_authorized_strategies

logger = logging.getLogger(__name__)

router = APIRouter()

# Export auth dependency - requires EXPORT_DATA permission
export_auth = api_auth(
    APIAuthConfig(
        action="export",
        require_role=None,
        require_permission=Permission.EXPORT_DATA,
    ),
    authenticator_getter=build_gateway_authenticator,
)


# =============================================================================
# Request/Response Models
# =============================================================================


class ExportAuditCreateRequest(BaseModel):
    """Request to create an export audit record."""

    export_type: Literal["csv", "excel", "clipboard"] = Field(..., description="Type of export")
    grid_name: str = Field(
        ..., description="Name of grid being exported (positions, orders, fills, audit, tca)"
    )
    filter_params: dict[str, Any] | None = Field(
        default=None, description="AG Grid filter model at time of export"
    )
    visible_columns: list[str] | None = Field(
        default=None, description="List of columns included in export"
    )
    sort_model: list[dict[str, Any]] | None = Field(
        default=None, description="AG Grid sort model at time of export"
    )
    export_scope: Literal["visible", "full"] = Field(
        default="visible", description="Export scope: visible rows or all filtered"
    )
    estimated_row_count: int | None = Field(default=None, description="Client-estimated row count")


class ExportAuditCreateResponse(BaseModel):
    """Response from creating an export audit record."""

    audit_id: UUID = Field(..., description="Unique audit record ID")
    status: str = Field(default="pending", description="Export status")
    created_at: datetime = Field(..., description="When audit record was created")


class ExportAuditCompleteRequest(BaseModel):
    """Request to complete/update an export audit record."""

    actual_row_count: int = Field(..., description="Actual rows exported", ge=0)
    status: Literal["completed", "failed"] = Field(
        default="completed", description="Final export status"
    )
    error_message: str | None = Field(default=None, description="Error details if status=failed")


class ExportAuditResponse(BaseModel):
    """Full export audit record response."""

    audit_id: UUID
    user_id: str
    export_type: str
    grid_name: str
    filter_params: dict[str, Any] | None
    visible_columns: list[str] | None
    sort_model: list[dict[str, Any]] | None
    strategy_ids: list[str] | None
    export_scope: str
    estimated_row_count: int | None
    actual_row_count: int | None
    reported_by: str | None
    status: str
    error_message: str | None
    ip_address: str | None
    session_id: str | None
    user_agent: str | None
    created_at: datetime
    completed_at: datetime | None


# =============================================================================
# Helper Functions
# =============================================================================


# Helper functions moved to apps/execution_gateway/api/utils.py
# Imported as: get_client_ip, get_user_agent


async def _create_export_audit(
    ctx: AppContext,
    user_id: str,
    request_data: ExportAuditCreateRequest,
    strategy_ids: list[str],
    ip_address: str | None,
    session_id: str | None,
    user_agent: str | None,
) -> tuple[UUID, datetime]:
    """Create export audit record in database.

    Returns:
        Tuple of (audit_id, created_at)
    """
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO export_audit (
                    user_id, export_type, grid_name, filter_params,
                    visible_columns, sort_model, strategy_ids, export_scope,
                    estimated_row_count, status, ip_address, session_id, user_agent
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    user_id,
                    request_data.export_type,
                    request_data.grid_name,
                    (
                        json.dumps(request_data.filter_params)
                        if request_data.filter_params
                        else None
                    ),
                    (
                        json.dumps(request_data.visible_columns)
                        if request_data.visible_columns
                        else None
                    ),
                    (json.dumps(request_data.sort_model) if request_data.sort_model else None),
                    json.dumps(strategy_ids) if strategy_ids else None,
                    request_data.export_scope,
                    request_data.estimated_row_count,
                    ip_address,
                    session_id,
                    user_agent,
                ),
            )
            row = cur.fetchone()
            return row[0], row[1]


async def _complete_export_audit(
    ctx: AppContext,
    audit_id: UUID,
    actual_row_count: int,
    status: str,
    reported_by: str,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Complete export audit record with actual results.

    Returns:
        Updated record or None if not found
    """
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_audit
                SET actual_row_count = %s,
                    reported_by = %s,
                    status = %s,
                    error_message = %s,
                    completed_at = NOW()
                WHERE id = %s AND status = 'pending'
                RETURNING id
                """,
                (actual_row_count, reported_by, status, error_message, audit_id),
            )
            row = cur.fetchone()
            return {"id": row[0]} if row else None


def _parse_jsonb(value: Any) -> Any:
    """Parse JSONB column value from database.

    JSONB columns may be returned as raw strings depending on the database
    driver configuration. This helper ensures they are properly deserialized
    to Python dicts/lists for Pydantic model validation.
    """
    if value is None:
        return None
    if isinstance(value, dict | list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


async def _get_export_audit(ctx: AppContext, audit_id: UUID) -> dict[str, Any] | None:
    """Get export audit record by ID."""
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, export_type, grid_name, filter_params,
                       visible_columns, sort_model, strategy_ids, export_scope,
                       estimated_row_count, actual_row_count, reported_by,
                       status, error_message, ip_address, session_id, user_agent,
                       created_at, completed_at
                FROM export_audit
                WHERE id = %s
                """,
                (audit_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            # Parse JSONB columns to ensure proper Python types for Pydantic
            return {
                "audit_id": row[0],
                "user_id": row[1],
                "export_type": row[2],
                "grid_name": row[3],
                "filter_params": _parse_jsonb(row[4]),
                "visible_columns": _parse_jsonb(row[5]),
                "sort_model": _parse_jsonb(row[6]),
                "strategy_ids": _parse_jsonb(row[7]),
                "export_scope": row[8],
                "estimated_row_count": row[9],
                "actual_row_count": row[10],
                "reported_by": row[11],
                "status": row[12],
                "error_message": row[13],
                "ip_address": row[14],
                "session_id": row[15],
                "user_agent": row[16],
                "created_at": row[17],
                "completed_at": row[18],
            }


async def _mark_audit_as_expired(ctx: AppContext, audit_id: UUID) -> None:
    """Mark export audit as expired (for single-use download links)."""
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_audit
                SET status = 'expired',
                    completed_at = NOW()
                WHERE id = %s AND status = 'completed'
                """,
                (audit_id,),
            )


async def _claim_export_audit(ctx: AppContext, audit_id: UUID) -> bool:
    """Atomically claim an export audit for download (prevents race conditions).

    Returns True if successfully claimed, False if already claimed by another request.
    """
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_audit
                SET status = 'downloading'
                WHERE id = %s AND status = 'pending'
                RETURNING id
                """,
                (audit_id,),
            )
            row = cur.fetchone()
            return row is not None


async def _fail_export_audit(ctx: AppContext, audit_id: UUID, error_message: str) -> None:
    """Mark export audit as failed with error message."""
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_audit
                SET status = 'failed',
                    error_message = %s,
                    completed_at = NOW()
                WHERE id = %s
                """,
                (error_message, audit_id),
            )


async def _complete_and_expire_export_audit(
    ctx: AppContext,
    audit_id: UUID,
    actual_row_count: int,
) -> None:
    """Complete export and mark as expired in one operation (single-use link)."""
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE export_audit
                SET actual_row_count = %s,
                    reported_by = 'server',
                    status = 'expired',
                    completed_at = NOW()
                WHERE id = %s
                """,
                (actual_row_count, audit_id),
            )


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/api/v1/export/audit",
    response_model=ExportAuditCreateResponse,
    tags=["Export"],
    status_code=status.HTTP_201_CREATED,
)
async def create_export_audit(
    request: Request,
    payload: ExportAuditCreateRequest,
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth_context: AuthContext = Depends(export_auth),
) -> ExportAuditCreateResponse:
    """
    Create an export audit record before performing an export.

    This endpoint is called before CSV/Excel/Clipboard exports to create
    an audit trail. The returned audit_id should be used to:
    1. Complete the audit with actual row count (PATCH /export/audit/{id})
    2. Download Excel files (GET /export/excel/{id})

    Strategy IDs are automatically injected from user authorization scope.

    Args:
        request: HTTP request for IP/User-Agent extraction
        payload: Export audit details
        ctx: Application context
        user: Authenticated user context
        _auth_context: Auth context for export permission

    Returns:
        ExportAuditCreateResponse with audit_id for subsequent operations

    Raises:
        HTTPException 403: User not authorized to export
    """
    # Get user's authorized strategies (server-side injection for compliance)
    user_obj = user.get("user")
    user_id = user.get("user_id", "unknown")
    session_id = user.get("session_id")
    strategy_ids = get_authorized_strategies(user_obj)

    if not strategy_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No strategy access - cannot export",
        )

    # Extract request metadata
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    # Create audit record
    audit_id, created_at = await _create_export_audit(
        ctx=ctx,
        user_id=user_id,
        request_data=payload,
        strategy_ids=strategy_ids,
        ip_address=ip_address,
        session_id=session_id,
        user_agent=user_agent,
    )

    logger.info(
        "Export audit created",
        extra={
            "audit_id": str(audit_id),
            "user_id": user_id,
            "export_type": payload.export_type,
            "grid_name": payload.grid_name,
            "ip_address": ip_address,
        },
    )

    return ExportAuditCreateResponse(
        audit_id=audit_id,
        status="pending",
        created_at=created_at,
    )


@router.patch(
    "/api/v1/export/audit/{audit_id}",
    response_model=ExportAuditResponse,
    tags=["Export"],
)
async def complete_export_audit(
    audit_id: UUID,
    payload: ExportAuditCompleteRequest,
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth_context: AuthContext = Depends(export_auth),
) -> ExportAuditResponse:
    """
    Complete an export audit record with actual results.

    Called after client-side export (CSV/Clipboard) completes to record
    the actual row count. This endpoint validates ownership - users can
    only complete their own audit records.

    Args:
        audit_id: The audit record ID from create_export_audit
        payload: Completion details with actual row count
        ctx: Application context
        user: Authenticated user context
        _auth_context: Auth context for export permission

    Returns:
        ExportAuditResponse with complete audit record

    Raises:
        HTTPException 404: Audit record not found
        HTTPException 403: Not owner of audit record or already completed
    """
    user_id = user.get("user_id", "unknown")

    # Get existing audit record
    audit_record = await _get_export_audit(ctx, audit_id)
    if not audit_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export audit {audit_id} not found",
        )

    # Verify ownership
    if audit_record["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot complete export audit owned by another user",
        )

    # Verify not already completed
    if audit_record["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Export audit already has status: {audit_record['status']}",
        )

    # Complete the audit (client-reported for CSV/clipboard)
    result = await _complete_export_audit(
        ctx=ctx,
        audit_id=audit_id,
        actual_row_count=payload.actual_row_count,
        status=payload.status,
        reported_by="client",
        error_message=payload.error_message,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export audit {audit_id} not found or already completed",
        )

    logger.info(
        "Export audit completed",
        extra={
            "audit_id": str(audit_id),
            "user_id": user_id,
            "actual_row_count": payload.actual_row_count,
            "status": payload.status,
        },
    )

    # Fetch and return updated record
    updated_record = await _get_export_audit(ctx, audit_id)
    if not updated_record:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch updated audit record",
        )

    return ExportAuditResponse(**updated_record)


@router.get(
    "/api/v1/export/excel/{audit_id}",
    tags=["Export"],
    responses={
        200: {
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}},
            "description": "Excel file download",
        },
        404: {"description": "Audit record not found or expired"},
        403: {"description": "Not authorized to download this export"},
    },
)
async def download_excel_export(
    audit_id: UUID,
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth_context: AuthContext = Depends(export_auth),
) -> StreamingResponse:
    """
    Download Excel export file (single-use link).

    This endpoint generates and returns an Excel file based on the
    audit record's grid_name and filter parameters. The link is single-use:
    after successful download, the audit status changes to 'expired'.

    Security:
    - Validates user ownership of audit record
    - Single-use: prevents link sharing/replay
    - Strategy scope enforced from audit record (not current user)

    Args:
        audit_id: The audit record ID
        ctx: Application context
        user: Authenticated user context
        _auth_context: Auth context for export permission

    Returns:
        StreamingResponse with Excel file

    Raises:
        HTTPException 404: Audit not found, wrong type, or already used
        HTTPException 403: Not owner of audit record
        HTTPException 501: Grid export not implemented
    """
    user_id = user.get("user_id", "unknown")

    # Get audit record
    audit_record = await _get_export_audit(ctx, audit_id)
    if not audit_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export audit {audit_id} not found",
        )

    # Verify ownership
    if audit_record["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot download export owned by another user",
        )

    # Verify export type
    if audit_record["export_type"] != "excel":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Audit {audit_id} is not an Excel export",
        )

    # Verify status - must be pending (single-use check via atomic claim)
    # We use atomic UPDATE to claim the record and prevent race conditions
    if audit_record["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Export link has already been used or expired",
        )

    # Atomically claim the audit record to prevent concurrent downloads
    # This prevents race conditions where two requests pass the status check
    claim_result = await _claim_export_audit(ctx, audit_id)
    if not claim_result:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Export link has already been used (concurrent request)",
        )

    # Generate Excel file based on grid_name
    grid_name = audit_record["grid_name"]
    strategy_ids = audit_record["strategy_ids"] or []
    filter_params = audit_record["filter_params"]
    visible_columns = audit_record["visible_columns"]
    sort_model = audit_record["sort_model"]

    # Honour ``export_scope="visible"`` — when the grid displays a
    # limited subset of rows (e.g. TCA page trims to 50), cap the
    # server-side query so the Excel file does not contain rows that
    # the user never saw.
    row_limit: int | None = None
    if (
        audit_record.get("export_scope") == "visible"
        and audit_record.get("estimated_row_count") is not None
    ):
        row_limit = audit_record["estimated_row_count"]

    # Generate Excel content with error handling
    try:
        excel_content, row_count = await _generate_excel_content(
            ctx=ctx,
            grid_name=grid_name,
            strategy_ids=strategy_ids,
            filter_params=filter_params,
            visible_columns=visible_columns,
            sort_model=sort_model,
            row_limit=row_limit,
        )
    except NotImplementedError as e:
        # Mark as failed before raising
        await _fail_export_audit(ctx, audit_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        ) from e
    except Exception as e:
        # Catch any other error and mark audit as failed
        error_msg = f"{type(e).__name__}: {e}"
        await _fail_export_audit(ctx, audit_id, error_msg)
        logger.exception(
            "Excel export generation failed",
            extra={"audit_id": str(audit_id), "error": error_msg},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Excel generation failed",
        ) from e

    # Complete the audit (server-reported for Excel) and mark as expired
    await _complete_and_expire_export_audit(
        ctx=ctx,
        audit_id=audit_id,
        actual_row_count=row_count,
    )

    # Generate filename
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M")
    filename = f"{grid_name}_{timestamp}.xlsx"

    logger.info(
        "Excel export downloaded",
        extra={
            "audit_id": str(audit_id),
            "user_id": user_id,
            "grid_name": grid_name,
            "row_count": row_count,
        },
    )

    return StreamingResponse(
        io.BytesIO(excel_content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Sanitization function moved to libs/platform/security/sanitization.py
# Imported as: sanitize_for_export
# This ensures a single source of truth for formula injection protection.


# ---------------------------------------------------------------------------
# Allowed columns per grid — server-side allowlist for export security.
# Only columns in these sets may appear in exported files.
# ---------------------------------------------------------------------------
_GRID_COLUMNS: dict[str, list[str]] = {
    "positions": [
        "symbol",
        "qty",
        "avg_entry_price",
        "current_price",
        "unrealized_pl",
        "realized_pl",
        "updated_at",
    ],
    "orders": [
        "client_order_id",
        "strategy_id",
        "symbol",
        "side",
        "qty",
        "order_type",
        "limit_price",
        "stop_price",
        "time_in_force",
        "status",
        "filled_qty",
        "filled_avg_price",
        "created_at",
        "filled_at",
    ],
    "fills": [
        "trade_id",
        "client_order_id",
        "strategy_id",
        "symbol",
        "side",
        "qty",
        "price",
        "executed_at",
        # ``status`` is synthesized as a literal 'filled' in the query
        # because all records in the trades table are completed fills.
        # The dashboard grid exposes this as a column, so we include it
        # in the allowlist to avoid silently dropping user-selected columns.
        "status",
    ],
    "audit": [
        "id",
        "timestamp",
        "user_id",
        "action",
        "details",
        "reason",
    ],
    # NOTE: The TCA grid in the web console displays computed metrics
    # (e.g. ``is_bps``, ``fill_rate_pct``, ``vwap_bps``) that are
    # derived client-side.  The export provides the raw underlying
    # trade/order data so users can compute their own analytics.
    # Frontend display names are mapped via ``_COLUMN_ALIASES["tca"]``
    # (e.g. ``execution_date`` → ``executed_at``, ``filled_qty`` →
    # ``qty``).  Purely computed columns like ``is_bps`` have no DB
    # backing and are dropped by ``_validate_columns``, which falls
    # back to this full raw-data allowlist when all columns are
    # unrecognised.
    "tca": [
        "trade_id",
        "client_order_id",
        "strategy_id",
        "symbol",
        "side",
        "qty",
        "price",
        "executed_at",
        "order_submitted_at",
        "order_qty",
        "filled_avg_price",
    ],
}

# Maximum rows per export to prevent excessive memory / query time
_EXPORT_ROW_LIMIT = 10_000

# Statuses shown in the dashboard's Working-orders grid.  Must match
# ``WORKING_ORDER_STATUSES`` in
# ``apps.web_console_ng.components.tabbed_panel`` to ensure export
# parity with the on-screen grid.  ``PENDING_STATUSES`` from the
# database module is broader (includes ``submitted`` and
# ``submitted_unconfirmed``) and would return rows not visible in
# the grid.
_WORKING_ORDER_STATUSES: tuple[str, ...] = (
    "new",
    "pending_new",
    "partially_filled",
    "accepted",
    "pending_cancel",
    "pending_replace",
)

# Columns stored as JSONB in the database.  Text filters on these columns
# must cast to ``::text`` to avoid PostgreSQL operator errors.
_JSONB_COLUMNS: dict[str, set[str]] = {
    "audit": {"details"},
}


# ---------------------------------------------------------------------------
# Frontend → DB column alias mapping.
# The web console grids may use display-friendly field names (e.g. "time"
# instead of "executed_at").  This mapping translates them before the
# allowlist check so that valid columns are not silently dropped.
# ---------------------------------------------------------------------------
_COLUMN_ALIASES: dict[str, dict[str, str]] = {
    "orders": {
        "type": "order_type",
    },
    "fills": {
        "time": "executed_at",
    },
    "tca": {
        # The TCA grid displays computed metrics (fill_rate_pct, is_bps,
        # vwap_bps) derived client-side.  Map the grid's display column
        # names to the raw DB columns so _validate_columns does not drop
        # them.
        "execution_date": "executed_at",
        "filled_qty": "qty",
    },
}


def _validate_columns(grid_name: str, visible_columns: list[str] | None) -> list[str]:
    """Return the export column list, validated against the server allowlist.

    Frontend column aliases are resolved first (e.g. ``"time"`` →
    ``"executed_at"``).  Only columns that exist in the allowlist are
    kept (in the requested order).  Unknown columns are silently dropped
    to prevent SQL injection or data leakage.
    """
    allowed = _GRID_COLUMNS[grid_name]
    if not visible_columns:
        return allowed
    aliases = _COLUMN_ALIASES.get(grid_name, {})
    resolved = [aliases.get(c, c) for c in visible_columns]
    return [c for c in resolved if c in allowed] or allowed


def _build_order_clause(
    sort_model: list[dict[str, Any]] | None,
    allowed_columns: list[str],
    default_order: str,
) -> str:
    """Build a safe ORDER BY clause from an AG Grid sort model.

    Only column names that appear in *allowed_columns* are accepted.
    Items are ordered by ``sortIndex`` (if present) so that multi-column
    sorts honour the precedence chosen by the user in the grid.
    """
    if not sort_model:
        return default_order
    # Sort by sortIndex when present to preserve AG Grid precedence.
    # Items without sortIndex retain their original list position.
    ordered = sorted(
        sort_model,
        key=lambda item: (item.get("sortIndex") is None, item.get("sortIndex", 0)),
    )
    parts: list[str] = []
    for item in ordered:
        col = item.get("colId", "")
        direction = "DESC" if item.get("sort") == "desc" else "ASC"
        if col in allowed_columns:
            parts.append(f"{col} {direction}")
    return ", ".join(parts) if parts else default_order


def _build_filter_clauses(
    filter_params: dict[str, Any] | None,
    allowed_columns: list[str],
    *,
    col_prefix: str = "",
    jsonb_columns: set[str] | None = None,
) -> tuple[str, list[Any]]:
    """Translate an AG Grid filter model into SQL WHERE fragments.

    Returns a tuple of ``(where_fragment, params)`` where
    *where_fragment* is a string of ``AND ...`` clauses (empty string
    if no filters) and *params* is a list of bind values.

    Only columns present in *allowed_columns* are accepted; unknown
    columns are silently ignored to prevent SQL injection.

    JSONB columns listed in *jsonb_columns* are automatically cast to
    ``::text`` before text-type filters to avoid PostgreSQL operator
    errors (JSONB does not support ``ILIKE`` directly).

    Supported AG Grid filter types:

    * ``text``   -- ``contains``, ``equals``, ``startsWith``, ``endsWith``
    * ``number`` -- ``equals``, ``greaterThan``, ``lessThan``
    * ``date``   -- ``equals``, ``greaterThan``, ``lessThan``, ``inRange``
    * ``set``    -- membership filter (``values`` list)
    """
    if not filter_params:
        return "", []

    fragments: list[str] = []
    params: list[Any] = []
    _jsonb = jsonb_columns or set()

    for col, spec in filter_params.items():
        if col not in allowed_columns:
            continue
        # Guard against malformed filter entries (e.g. bare strings
        # instead of dict specs) to avoid AttributeError on .get().
        if not isinstance(spec, dict):
            continue
        qualified = f"{col_prefix}{col}" if col_prefix else col

        # AG Grid compound filters use a ``conditions`` array with an
        # ``operator`` (AND/OR).  Flatten them into individual clauses.
        conditions = spec.get("conditions")
        if isinstance(conditions, list) and conditions:
            operator = spec.get("operator", "AND").upper()
            sql_op = "OR" if operator == "OR" else "AND"
            sub_fragments: list[str] = []
            sub_params: list[Any] = []
            parent_type = spec.get("filterType", "")
            for cond in conditions:
                if not isinstance(cond, dict):
                    continue
                _apply_single_filter(
                    col,
                    cond,
                    qualified,
                    _jsonb,
                    sub_fragments,
                    sub_params,
                    parent_filter_type=parent_type,
                )
            if sub_fragments:
                combined = f" {sql_op} ".join(sub_fragments)
                fragments.append(f"({combined})")
                params.extend(sub_params)
        else:
            _apply_single_filter(col, spec, qualified, _jsonb, fragments, params)

    where = " ".join(f"AND {f}" for f in fragments)
    return where, params


def _apply_single_filter(
    col: str,
    spec: dict[str, Any],
    qualified: str,
    jsonb_columns: set[str],
    fragments: list[str],
    params: list[Any],
    *,
    parent_filter_type: str = "",
) -> None:
    """Apply a single AG Grid filter condition to fragments/params.

    *parent_filter_type* is used as a fallback when the condition
    itself lacks a ``filterType`` key (common in compound filters
    where the type lives on the outer spec).

    Resolution order for ``filter_type``:
      1. The child's own ``filterType`` (if present)
      2. The *parent_filter_type* inherited from the compound wrapper
      3. The child's ``type`` field (operation name like ``equals``)

    Using ``parent_filter_type`` before ``type`` is important because
    ``type`` stores the *operation* (e.g. ``"equals"``), which overlaps
    with the text-filter check and would cause number/date ``equals``
    conditions inside compound filters to be misrouted as text filters.
    """
    filter_type = spec.get("filterType") or parent_filter_type or spec.get("type", "")

    if filter_type in ("text", "contains", "equals", "startsWith", "endsWith"):
        # Cast JSONB columns to text so ILIKE / = operators work.
        text_col = f"{qualified}::text" if col in jsonb_columns else qualified
        _apply_text_filter(text_col, spec, fragments, params)
    elif filter_type in ("number", "greaterThan", "lessThan"):
        _apply_number_filter(qualified, spec, fragments, params)
    elif filter_type in ("date", "inRange"):
        _apply_date_filter(qualified, spec, fragments, params)
    elif filter_type == "set":
        values = spec.get("values")
        if isinstance(values, list) and values:
            fragments.append(f"{qualified} = ANY(%s)")
            params.append(values)


def _apply_text_filter(
    col: str,
    spec: dict[str, Any],
    fragments: list[str],
    params: list[Any],
) -> None:
    """Add a text-type AG Grid filter clause."""
    op = spec.get("type", "contains")
    val = spec.get("filter", "")
    if not isinstance(val, str) or not val:
        return
    if op == "contains":
        fragments.append(f"{col} ILIKE %s")
        params.append(f"%{val}%")
    elif op == "equals":
        fragments.append(f"{col} = %s")
        params.append(val)
    elif op == "startsWith":
        fragments.append(f"{col} ILIKE %s")
        params.append(f"{val}%")
    elif op == "endsWith":
        fragments.append(f"{col} ILIKE %s")
        params.append(f"%{val}")


def _apply_number_filter(
    col: str,
    spec: dict[str, Any],
    fragments: list[str],
    params: list[Any],
) -> None:
    """Add a number-type AG Grid filter clause."""
    op = spec.get("type", "equals")
    val = spec.get("filter")
    if val is None:
        return
    if op == "equals":
        fragments.append(f"{col} = %s")
        params.append(val)
    elif op == "greaterThan":
        fragments.append(f"{col} > %s")
        params.append(val)
    elif op == "lessThan":
        fragments.append(f"{col} < %s")
        params.append(val)


def _apply_date_filter(
    col: str,
    spec: dict[str, Any],
    fragments: list[str],
    params: list[Any],
) -> None:
    """Add a date-type AG Grid filter clause."""
    op = spec.get("type", "equals")
    date_from = spec.get("dateFrom")
    date_to = spec.get("dateTo")
    if op == "equals" and date_from:
        fragments.append(f"{col}::date = %s::date")
        params.append(date_from)
    elif op == "greaterThan" and date_from:
        # AG Grid date filters are day-based: "greaterThan 2026-01-01"
        # means "after that entire day", so we use >= dateFrom + 1 day
        # to exclude all rows on the selected day.
        fragments.append(f"{col} >= (%s::date + interval '1 day')")
        params.append(date_from)
    elif op == "lessThan" and date_from:
        # AG Grid "lessThan" is also day-based: exclude the selected day
        # entirely by using < dateFrom (midnight of that day).
        fragments.append(f"{col} < %s::date")
        params.append(date_from)
    elif op == "inRange" and date_from and date_to:
        # AG Grid sends dateTo as the selected calendar day (midnight).
        # Use ``dateTo::date + interval '1 day'`` so the entire end date
        # is included and single-day ranges (same dateFrom/dateTo) return
        # rows from that day.
        fragments.append(f"{col} >= %s::timestamp AND {col} < (%s::date + interval '1 day')")
        params.append(date_from)
        params.append(date_to)


# ---------------------------------------------------------------------------
# Per-grid data fetchers
# ---------------------------------------------------------------------------


def _fetch_positions_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_params: dict[str, Any] | None = None,
    filterable_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch positions data scoped to authorized strategies.

    The ``positions`` table is symbol-scoped (no ``strategy_id`` column),
    so ownership is inferred via the shared fail-closed mapping from
    ``libs.data.sql.strategy_mapping_sql``: a symbol is attributed to a
    strategy only when exactly ONE strategy has ever traded it.  Symbols
    traded by multiple strategies are excluded to prevent cross-strategy
    data leakage.

    This is the same scoping used by
    ``apps.execution_gateway.database.get_positions_for_strategies``
    and the ``/api/v1/positions`` endpoint, so the export rows match
    exactly what the dashboard grid displays.  A future schema migration
    adding ``strategy_id`` to the positions table will remove this
    limitation.
    """
    filter_cols = filterable_columns or columns
    # Qualify sort columns with the "p." alias to avoid ambiguity when
    # joining positions with the symbol_strategy CTE.  Use filter_cols
    # (the full allowlist) rather than columns (projected) so sorts on
    # hidden-but-allowed columns are still honoured.
    qualified_sort: list[dict[str, Any]] | None = None
    if sort_model:
        qualified_sort = [
            {**item, "colId": f"p.{item['colId']}"} if item.get("colId") in filter_cols else item
            for item in sort_model
        ]
    qualified_columns = [f"p.{c}" for c in filter_cols]
    order_clause = _build_order_clause(
        qualified_sort,
        qualified_columns,
        "p.symbol ASC",
    )
    col_list = SQL(", ").join(SQL("p.{col} AS {col}").format(col=Identifier(c)) for c in columns)
    filter_clause, filter_params_list = _build_filter_clauses(
        filter_params,
        filter_cols,
        col_prefix="p.",
    )
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = SQL(
                "WITH {cte} "
                "SELECT {cols} FROM positions p "
                "JOIN symbol_strategy ss ON p.symbol = ss.symbol "
                "WHERE p.qty != 0 AND ss.strategy = ANY(%s) "
                "{filter} "
                "ORDER BY {order} "
                "LIMIT %s"
            ).format(
                cte=SQL(SYMBOL_STRATEGY_CTE),
                cols=col_list,
                filter=SQL(filter_clause),
                order=SQL(order_clause),
            )
            cur.execute(query, (strategy_ids, *filter_params_list, _EXPORT_ROW_LIMIT))
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_orders_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_params: dict[str, Any] | None = None,
    filterable_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch orders scoped to authorized strategies.

    Only working/pending orders are returned to match the dashboard's
    Working-orders grid.  The status filter uses
    ``_WORKING_ORDER_STATUSES`` which mirrors the client-side
    ``WORKING_ORDER_STATUSES`` set from the web console, ensuring
    export parity with what the user sees on screen.
    """
    filter_cols = filterable_columns or columns
    order_clause = _build_order_clause(sort_model, filter_cols, "created_at DESC")
    col_list = SQL(", ").join(Identifier(c) for c in columns)
    filter_clause, filter_params_list = _build_filter_clauses(
        filter_params,
        filter_cols,
    )
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = SQL(
                "SELECT {cols} FROM orders "
                "WHERE strategy_id = ANY(%s) "
                "AND status = ANY(%s) "
                "{filter} "
                "ORDER BY {order} "
                "LIMIT %s"
            ).format(
                cols=col_list,
                filter=SQL(filter_clause),
                order=SQL(order_clause),
            )
            cur.execute(
                query,
                (
                    strategy_ids,
                    list(_WORKING_ORDER_STATUSES),
                    *filter_params_list,
                    _EXPORT_ROW_LIMIT,
                ),
            )
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_fills_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_params: dict[str, Any] | None = None,
    filterable_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch fills (trades) scoped to authorized strategies.

    The ``status`` column is synthesized as the literal ``'filled'``
    because every record in the trades table represents a completed fill.
    """
    filter_cols = filterable_columns or columns
    order_clause = _build_order_clause(sort_model, filter_cols, "executed_at DESC")
    # Synthesize 'status' as a literal since it doesn't exist in the
    # trades table -- all trade records are completed fills.
    col_parts: list[Composable] = []
    for c in columns:
        if c == "status":
            col_parts.append(SQL("'filled' AS status"))
        else:
            col_parts.append(SQL("t.{col}").format(col=Identifier(c)))
    col_list = SQL(", ").join(col_parts)
    # Exclude the synthetic 'status' column from filter processing
    # since it has no DB backing and cannot be filtered.
    db_columns = [c for c in filter_cols if c != "status"]
    filter_clause, filter_params_list = _build_filter_clauses(
        filter_params,
        db_columns,
        col_prefix="t.",
    )
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = SQL(
                "SELECT {cols} FROM trades t "
                "WHERE COALESCE(t.superseded, FALSE) = FALSE "
                "AND t.strategy_id = ANY(%s) "
                "{filter} "
                "ORDER BY {order} "
                "LIMIT %s"
            ).format(
                cols=col_list,
                filter=SQL(filter_clause),
                order=SQL(order_clause),
            )
            cur.execute(query, (strategy_ids, *filter_params_list, _EXPORT_ROW_LIMIT))
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_audit_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_params: dict[str, Any] | None = None,
    filterable_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch audit log entries scoped to authorized strategies.

    Audit entries are scoped by matching the ``strategy_id`` key inside
    the ``details`` JSONB column against the authorized *strategy_ids*.
    System-level entries (those without a ``strategy_id`` in their
    details) are excluded to enforce least-privilege -- exporting only
    strategy-related audit entries prevents leaking sensitive system
    operations to users who may only be authorized for specific
    strategies.
    """
    filter_cols = filterable_columns or columns
    order_clause = _build_order_clause(sort_model, filter_cols, "timestamp DESC, id DESC")
    col_list = SQL(", ").join(Identifier(c) for c in columns)
    filter_clause, filter_params_list = _build_filter_clauses(
        filter_params,
        filter_cols,
        jsonb_columns=_JSONB_COLUMNS.get("audit"),
    )
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = SQL(
                "SELECT {cols} FROM audit_log "
                "WHERE details->>'strategy_id' = ANY(%s) "
                "{filter} "
                "ORDER BY {order} "
                "LIMIT %s"
            ).format(
                cols=col_list,
                filter=SQL(filter_clause),
                order=SQL(order_clause),
            )
            cur.execute(query, (strategy_ids, *filter_params_list, _EXPORT_ROW_LIMIT))
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_tca_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_params: dict[str, Any] | None = None,
    filterable_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch TCA trade data with order context, scoped to authorized strategies.

    The TCA grid in the web console displays computed metrics
    (``fill_rate_pct``, ``is_bps``, ``vwap_bps``) derived client-side.
    The export provides the raw underlying trade/order data so users
    can compute their own analytics.  Frontend column aliases are
    resolved via ``_COLUMN_ALIASES["tca"]`` before reaching this
    fetcher, so mapped columns are included correctly.

    .. note::

       The Execution Quality page uses page-level date/strategy
       selectors (``start_date``, ``end_date``, ``strategy_id``) that
       are separate from the AG Grid filter model.  These constraints
       are injected by the frontend toolbar via
       ``GridExportToolbar.extra_filter_params`` so the server-side
       export query is scoped to the same date range the user
       selected.
    """
    filter_cols = filterable_columns or columns
    # Map column names to their qualified table references
    tca_col_map: dict[str, str] = {
        "trade_id": "t.trade_id",
        "client_order_id": "t.client_order_id",
        "strategy_id": "t.strategy_id",
        "symbol": "t.symbol",
        "side": "t.side",
        "qty": "t.qty",
        "price": "t.price",
        "executed_at": "t.executed_at",
        "order_submitted_at": "o.submitted_at",
        "order_qty": "o.qty",
        "filled_avg_price": "o.filled_avg_price",
    }
    # Build SELECT list using psycopg.sql -- column aliases from
    # tca_col_map are pre-qualified table.column references that are
    # safe because they come from a hardcoded mapping above, not user
    # input.  The output alias uses Identifier for proper quoting.
    select_cols = SQL(", ").join(
        SQL("{ref} AS {alias}").format(
            ref=SQL(tca_col_map.get(c, "t." + c)),
            alias=Identifier(c),
        )
        for c in columns
    )
    # Qualify sort columns with table aliases via tca_col_map to
    # avoid ambiguity when trades and orders share column names
    # (e.g. symbol, qty, client_order_id).
    qualified_sort: list[dict[str, Any]] | None = None
    if sort_model:
        qualified_sort = [
            (
                {
                    **item,
                    "colId": tca_col_map.get(item.get("colId", ""), f"t.{item.get('colId', '')}"),
                }
                if item.get("colId") in filter_cols
                else item
            )
            for item in sort_model
        ]
    qualified_tca_columns = [tca_col_map.get(c, f"t.{c}") for c in filter_cols]
    order_clause = _build_order_clause(
        qualified_sort,
        qualified_tca_columns,
        # Match the TCA API ordering (ASC) so visible-scope exports
        # return the same rows the user sees in the dashboard grid.
        "t.executed_at ASC",
    )
    # For TCA filters, pre-qualify column names via tca_col_map so the
    # WHERE clause references the correct table alias (e.g. "o.qty"
    # for order_qty, not "t.order_qty").
    #
    # NOTE: Unlike the other single-table fetchers (which use the
    # ``col_prefix`` parameter of ``_build_filter_clauses``), TCA
    # requires per-column qualification because the JOIN spans two
    # tables with different column owners.  Pre-qualifying here keeps
    # ``_build_filter_clauses`` simple (single-prefix mode) while
    # correctly routing each column to its owning table.
    tca_filter_params: dict[str, Any] | None = None
    if filter_params:
        tca_filter_params = {}
        for col, spec in filter_params.items():
            if col in filter_cols:
                qualified = tca_col_map.get(col, f"t.{col}")
                tca_filter_params[qualified] = spec
    qualified_filter_columns = [tca_col_map.get(c, f"t.{c}") for c in filter_cols]
    filter_clause, filter_params_list = _build_filter_clauses(
        tca_filter_params,
        qualified_filter_columns,
    )
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = SQL(
                "SELECT {cols} "
                "FROM trades t "
                "LEFT JOIN orders o ON t.client_order_id = o.client_order_id "
                "WHERE COALESCE(t.superseded, FALSE) = FALSE "
                "AND t.strategy_id = ANY(%s) "
                "{filter} "
                "ORDER BY {order} "
                "LIMIT %s"
            ).format(
                cols=select_cols,
                filter=SQL(filter_clause),
                order=SQL(order_clause),
            )
            cur.execute(query, (strategy_ids, *filter_params_list, _EXPORT_ROW_LIMIT))
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


# Type alias for grid data fetcher functions.
# Signature: (ctx, strategy_ids, columns, sort_model, filter_params, filterable_columns)
_GridFetcher = Callable[
    [
        AppContext,
        list[str],
        list[str],
        list[dict[str, Any]] | None,
        dict[str, Any] | None,
        list[str],
    ],
    list[dict[str, Any]],
]

# Dispatch table for grid data fetchers
_GRID_FETCHERS: dict[str, _GridFetcher] = {
    "positions": _fetch_positions_data,
    "orders": _fetch_orders_data,
    "fills": _fetch_fills_data,
    "audit": _fetch_audit_data,
    "tca": _fetch_tca_data,
}


async def _generate_excel_content(
    ctx: AppContext,
    grid_name: str,
    strategy_ids: list[str],
    filter_params: dict[str, Any] | None,
    visible_columns: list[str] | None,
    sort_model: list[dict[str, Any]] | None,
    *,
    row_limit: int | None = None,
) -> tuple[bytes, int]:
    """Generate Excel file content for a grid.

    Fetches real data from the database for the requested grid, applies
    column validation against a server-side allowlist, and sanitises every
    cell value to prevent formula-injection attacks.

    Args:
        ctx: Application context with database access
        grid_name: Name of grid to export
        strategy_ids: Authorized strategy IDs for filtering
        filter_params: AG Grid filter model (translated to SQL WHERE clauses)
        visible_columns: Columns to include (validated against allowlist)
        sort_model: AG Grid sort model
        row_limit: Optional cap on number of rows returned.  When
            ``export_scope="visible"``, this is set to the client's
            ``estimated_row_count`` so the Excel file only contains
            the rows the user actually saw in the grid.

    Returns:
        Tuple of (excel_bytes, row_count)

    Raises:
        NotImplementedError: If grid type not supported or openpyxl missing
    """
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise NotImplementedError("Excel export requires openpyxl: pip install openpyxl") from e

    if grid_name not in _GRID_FETCHERS:
        raise NotImplementedError(f"Excel export not implemented for grid: {grid_name}")

    # Validate requested columns against server allowlist
    columns = _validate_columns(grid_name, visible_columns)

    # Resolve column aliases in both filter_params and sort_model so
    # that frontend field names (e.g. "type" for orders, "time" for
    # fills) map to the DB column names expected by the fetchers.
    aliases = _COLUMN_ALIASES.get(grid_name, {})
    allowed = _GRID_COLUMNS[grid_name]

    resolved_filters: dict[str, Any] | None = None
    if filter_params:
        resolved_filters = {}
        for key, spec in filter_params.items():
            resolved_key = aliases.get(key, key)
            if resolved_key in allowed:
                resolved_filters[resolved_key] = spec

    resolved_sort: list[dict[str, Any]] | None = None
    if sort_model:
        resolved_sort = [
            {**item, "colId": aliases.get(item.get("colId", ""), item.get("colId", ""))}
            for item in sort_model
        ]

    # Offload synchronous DB fetch to a worker thread to avoid blocking
    # the FastAPI event loop.  Pass the full grid allowlist so filters on
    # hidden columns (columns not in the projected ``columns`` list) are
    # still applied — AG Grid allows filtering on columns the user has
    # subsequently hidden.
    fetcher = _GRID_FETCHERS[grid_name]
    rows = await asyncio.to_thread(
        fetcher,
        ctx,
        strategy_ids,
        columns,
        resolved_sort,
        resolved_filters,
        allowed,
    )

    # When export_scope="visible", cap rows at the client-reported count
    # so the Excel file only includes what the user saw on screen.
    #
    # NOTE: The TCA grid is excluded because it shows aggregated order-level
    # rows in the dashboard while the export provides raw trade-level rows.
    # Applying an order-based row_limit to trade rows would silently cut
    # off fills for multi-fill orders.
    if row_limit is not None and row_limit >= 0 and grid_name != "tca":
        rows = rows[:row_limit]

    # Build workbook (CPU-bound; done in worker thread below)
    def _build_workbook() -> bytes:
        wb = Workbook()
        ws = wb.active
        assert ws is not None  # Workbook() always creates an active sheet
        ws.title = grid_name.title()

        # Header row -- sanitize headers
        for col_idx, header in enumerate(columns, 1):
            ws.cell(row=1, column=col_idx, value=sanitize_for_export(header))

        # Data rows -- sanitize only string values to prevent formula
        # injection while preserving native Excel types (numbers, dates)
        # for proper formatting and calculations.
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, col_name in enumerate(columns, 1):
                raw_value = row_data.get(col_name)
                if isinstance(raw_value, dict | list):
                    raw_value = json.dumps(raw_value, default=str)

                # Only sanitize strings to prevent formula injection
                # while preserving numeric/date types that openpyxl
                # handles natively.
                if isinstance(raw_value, str):
                    value_to_set: Any = sanitize_for_export(raw_value)
                elif raw_value is None:
                    value_to_set = ""
                elif isinstance(raw_value, datetime):
                    # openpyxl does not support timezone-aware datetimes.
                    # If tz-aware, convert to UTC then strip; if naive,
                    # assume already UTC (per project coding standards)
                    # and use as-is.
                    if raw_value.tzinfo is not None:
                        value_to_set = raw_value.astimezone(UTC).replace(tzinfo=None)
                    else:
                        value_to_set = raw_value
                else:
                    value_to_set = raw_value

                ws.cell(
                    row=row_idx,
                    column=col_idx,
                    value=value_to_set,
                )

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    excel_bytes = await asyncio.to_thread(_build_workbook)
    row_count = len(rows)
    return excel_bytes, row_count


@router.get(
    "/api/v1/export/audit/{audit_id}",
    response_model=ExportAuditResponse,
    tags=["Export"],
)
async def get_export_audit_record(
    audit_id: UUID,
    ctx: AppContext = Depends(get_context),
    user: dict[str, Any] = Depends(build_user_context),
    _auth_context: AuthContext = Depends(export_auth),
) -> ExportAuditResponse:
    """
    Get an export audit record by ID.

    Args:
        audit_id: The audit record ID
        ctx: Application context
        user: Authenticated user context
        _auth_context: Auth context for export permission

    Returns:
        ExportAuditResponse with audit record details

    Raises:
        HTTPException 404: Audit record not found
        HTTPException 403: Not owner of audit record
    """
    user_id = user.get("user_id", "unknown")

    audit_record = await _get_export_audit(ctx, audit_id)
    if not audit_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export audit {audit_id} not found",
        )

    # Verify ownership
    if audit_record["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view export audit owned by another user",
        )

    return ExportAuditResponse(**audit_record)
