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

import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.api.utils import get_client_ip, get_user_agent
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import get_context
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import APIAuthConfig, AuthContext, api_auth
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

    export_type: Literal["csv", "excel", "clipboard"] = Field(
        ..., description="Type of export"
    )
    grid_name: str = Field(
        ...,
        description="Name of grid being exported "
        "(positions, orders, working_orders, fills, audit, tca)",
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
    estimated_row_count: int | None = Field(
        default=None, description="Client-estimated row count"
    )


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
    error_message: str | None = Field(
        default=None, description="Error details if status=failed"
    )


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
                    (
                        json.dumps(request_data.sort_model)
                        if request_data.sort_model
                        else None
                    ),
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


async def _fail_export_audit(
    ctx: AppContext, audit_id: UUID, error_message: str
) -> None:
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
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
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

    # For visible-scope exports, cap rows to what the user sees
    export_scope = audit_record.get("export_scope", "visible")
    max_rows: int | None = None
    if export_scope == "visible":
        estimated = audit_record.get("estimated_row_count")
        if isinstance(estimated, int) and estimated >= 0:
            max_rows = estimated

    # Generate Excel content with error handling
    try:
        excel_content, row_count = await _generate_excel_content(
            ctx=ctx,
            grid_name=grid_name,
            strategy_ids=strategy_ids,
            filter_params=filter_params,
            visible_columns=visible_columns,
            sort_model=sort_model,
            max_rows=max_rows,
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


def _coerce_cell_value(value: Any) -> Any:
    """Coerce a database value to an Excel-safe type.

    Decimals are kept as-is (openpyxl handles them natively, preserving precision).
    Datetimes are kept as-is (openpyxl handles them natively).
    Everything else is stringified and sanitized.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value  # openpyxl supports Decimal natively; preserve precision
    if isinstance(value, datetime):
        # openpyxl requires timezone-naive datetimes; convert to UTC first
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, bool | int | float):
        return value
    # Dict/list → JSON string; all strings sanitized for formula injection
    if isinstance(value, dict | list):
        return sanitize_for_export(json.dumps(value, default=str))
    return sanitize_for_export(str(value))


# =============================================================================
# Per-Grid Data Fetchers
# =============================================================================


def _compute_unrealized_plpc(pos: Any) -> Decimal | None:
    """Compute unrealized P&L percentage for a position.

    Mirrors the UI computation in positions_grid.py so the exported
    ``unrealized_plpc`` column matches what the user sees.
    """
    unrealized_pl = getattr(pos, "unrealized_pl", None)
    avg_entry = getattr(pos, "avg_entry_price", None)
    qty = getattr(pos, "qty", None)
    if unrealized_pl is None or avg_entry is None or qty is None:
        return None
    if avg_entry == 0 or qty == 0:
        return None
    # Position fields are already Decimal from schemas.Position
    result: Decimal = unrealized_pl / (avg_entry * abs(qty))
    return result


def _fetch_positions_data(
    ctx: AppContext,
    strategy_ids: list[str],
    _filter_params: dict[str, Any] | None,
) -> tuple[list[str], list[list[Any]]]:
    """Fetch positions grid data scoped to authorized strategies.

    Uses ``get_positions_for_strategies`` to enforce strategy-level
    authorization.  Symbols traded by multiple strategies are excluded
    (fail-closed) to prevent cross-strategy data leakage — this is a
    known limitation until the positions table gains a strategy_id column.
    """
    db_columns = [
        "symbol", "qty", "avg_entry_price", "current_price",
        "unrealized_pl", "realized_pl", "updated_at", "last_trade_at",
    ]
    export_columns = db_columns + ["unrealized_plpc"]
    positions = ctx.db.get_positions_for_strategies(strategy_ids)
    rows = [
        [getattr(pos, col, None) for col in db_columns] + [_compute_unrealized_plpc(pos)]
        for pos in positions
    ]
    return export_columns, rows


def _extract_status_values(filter_params: dict[str, Any] | None) -> list[str] | None:
    """Extract status filter values from AG Grid filter model for SQL push-down.

    Returns a list of status strings if a status filter is present,
    or None to fetch all statuses.
    """
    if not filter_params or "status" not in filter_params:
        return None
    status_filter = filter_params["status"]
    if not isinstance(status_filter, dict):
        return None
    filter_val = status_filter.get("filter")
    op = status_filter.get("type", "")
    if op == "equals" and isinstance(filter_val, str):
        return [filter_val]
    # For set/in filters, collect all values
    values = status_filter.get("values")
    if isinstance(values, list):
        return [str(v) for v in values if v is not None]
    return None


# DB column → UI field aliases for orders grid.
# The UI grid uses field ids like "type" (not "order_type"), so exported
# columns must match the client's visible_columns / filter / sort model.
_ORDERS_DB_TO_UI: dict[str, str] = {
    "order_type": "type",
}

# Active order statuses that define the "Working" tab scope.
# Must stay in sync with WORKING_ORDER_STATUSES in tabbed_panel.py.
WORKING_ORDER_STATUSES: list[str] = [
    "new", "pending_new", "accepted", "partially_filled",
    "pending_cancel", "pending_replace",
]


def _fetch_orders_data(
    ctx: AppContext,
    strategy_ids: list[str],
    filter_params: dict[str, Any] | None,
    *,
    working_only: bool = False,
) -> tuple[list[str], list[list[Any]]]:
    """Fetch orders grid data scoped to authorized strategies.

    Pushes status filtering to SQL when a status filter is present
    in filter_params, ensuring correct row-count limits regardless
    of working/all-orders tab context.

    Args:
        working_only: When True, restricts to active order statuses
            even if filter_params has no explicit status filter.
            Used by the ``working_orders`` grid alias.
    """
    db_columns = [
        "client_order_id", "strategy_id", "symbol", "side", "qty",
        "order_type", "status", "filled_qty", "filled_avg_price",
        "limit_price", "stop_price",
        "created_at", "submitted_at", "filled_at",
    ]
    # Columns exposed to the client (with UI-friendly names)
    export_columns = [
        _ORDERS_DB_TO_UI.get(c, c) for c in db_columns
    ]

    statuses = _extract_status_values(filter_params)
    if working_only:
        # Always enforce working statuses; intersect with client filter if present
        allowed = set(WORKING_ORDER_STATUSES)
        if statuses is not None:
            statuses = [s for s in statuses if s in allowed]
        else:
            statuses = WORKING_ORDER_STATUSES

    order_dicts = ctx.db.get_orders_for_export(
        strategy_ids=strategy_ids,
        statuses=statuses,
    )
    rows = [[d.get(col) for col in db_columns] for d in order_dicts]
    return export_columns, rows


def _compute_progress(row: list[Any], columns: list[str]) -> Decimal | None:
    """Compute fill progress (filled_qty / qty) for working orders.

    Mirrors the UI's progress column shown for parent TWAP rows.
    Returns None if qty is zero or values are missing.
    """
    try:
        qty_idx = columns.index("qty")
        filled_idx = columns.index("filled_qty")
        qty = row[qty_idx]
        filled = row[filled_idx]
        if qty is None or filled is None or qty == 0:
            return None
        return Decimal(str(filled)) / Decimal(str(qty))
    except (ValueError, InvalidOperation):
        return None


def _fetch_working_orders_data(
    ctx: AppContext,
    strategy_ids: list[str],
    filter_params: dict[str, Any] | None,
) -> tuple[list[str], list[list[Any]]]:
    """Fetch working (active) orders for export.

    Delegates to ``_fetch_orders_data`` with ``working_only=True`` and
    appends a computed ``progress`` column (filled_qty / qty) matching
    the Working Orders grid's visible progress field.
    """
    columns, rows = _fetch_orders_data(
        ctx, strategy_ids, filter_params, working_only=True,
    )
    # Append computed progress column
    enriched_rows = [row + [_compute_progress(row, columns)] for row in rows]
    return columns + ["progress"], enriched_rows


def _fetch_fills_data(
    ctx: AppContext,
    strategy_ids: list[str],
    _filter_params: dict[str, Any] | None,
) -> tuple[list[str], list[list[Any]]]:
    """Fetch fills for export scoped to authorized strategies.

    The UI grid uses field name ``time`` for the fill timestamp, so we
    expose the column as ``time`` here (the DB method returns it as
    ``timestamp``).  This ensures visible_columns, filter, and sort
    models from the client match correctly.
    """
    # DB returns "timestamp"; UI grid field is "time"
    db_columns = [
        "client_order_id", "symbol", "side", "status",
        "qty", "price", "realized_pl", "timestamp",
    ]
    export_columns = [
        "client_order_id", "symbol", "side", "status",
        "qty", "price", "realized_pl", "time",
    ]
    fills = ctx.db.get_fills_for_export(
        strategy_ids=strategy_ids,
    )
    rows = [
        [fill.get(col) for col in db_columns]
        for fill in fills
    ]
    return export_columns, rows


def _fetch_audit_data(
    ctx: AppContext,
    strategy_ids: list[str],
    _filter_params: dict[str, Any] | None,
) -> tuple[list[str], list[list[Any]]]:
    """Fetch audit log entries scoped to authorized strategies.

    The audit_log table has no direct strategy_id column, so we join through
    the orders table for order-related entries to enforce strategy scoping.
    Non-order audit entries (e.g., login events) are included if they belong
    to the current user's session — but since we don't have user_id context
    here, we restrict to order-scoped entries only for safety.

    Raises:
        NotImplementedError: Audit export is disabled until full strategy
            scoping can be implemented without data leakage.
    """
    raise NotImplementedError(
        "Audit grid export is disabled: audit_log has no strategy_id column "
        "and cannot be reliably scoped to authorized strategies. "
        "Use the web console audit view which applies client-side filtering."
    )


def _fetch_tca_data(
    ctx: AppContext,
    strategy_ids: list[str],
    _filter_params: dict[str, Any] | None,
) -> tuple[list[str], list[list[Any]]]:
    """Fetch TCA data scoped to authorized strategies.

    Raises:
        NotImplementedError: TCA export is disabled because the UI grid uses
            computed columns (execution_date, fill_rate_pct, is_bps, vwap_bps,
            impact_bps, notional) derived from the TCA analysis pipeline. The
            server-side fetcher only has raw trade columns and cannot reproduce
            the UI's computed metrics. Filters/sorts on UI column names would
            silently fail.
    """
    raise NotImplementedError(
        "TCA grid export is disabled: the UI grid uses computed columns "
        "(execution_date, fill_rate_pct, is_bps, vwap_bps, impact_bps, notional) "
        "that require the TCA analysis pipeline. The server-side exporter cannot "
        "reproduce these metrics. Use CSV export from the browser instead."
    )


# Map grid names to their fetchers
_GridFetcher = Callable[
    [AppContext, list[str], dict[str, Any] | None],
    tuple[list[str], list[list[Any]]],
]

_GRID_FETCHERS: dict[str, _GridFetcher] = {
    "positions": _fetch_positions_data,
    "orders": _fetch_orders_data,
    "working_orders": _fetch_working_orders_data,
    "fills": _fetch_fills_data,
    "history": _fetch_fills_data,  # History grid uses same trades source as fills
    "audit": _fetch_audit_data,
    "tca": _fetch_tca_data,
}


def _to_decimal(v: Any) -> Decimal | None:
    """Convert a value to Decimal, returning None on failure."""
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _match_filter(value: Any, filter_def: dict[str, Any]) -> bool:
    """Check if a single cell value matches an AG Grid filter definition.

    Supports text, number, and date filter types with common operators.
    Returns True if the value passes the filter (should be kept).
    """
    filter_type = filter_def.get("filterType", "text")
    operator = filter_def.get("type", "")
    filter_value = filter_def.get("filter")

    if value is None:
        # Null values only match "blank" filters
        return bool(operator == "blank")

    if filter_type == "text":
        text_val = str(value).lower()
        filter_str = str(filter_value).lower() if filter_value is not None else ""
        if operator == "contains":
            return filter_str in text_val
        if operator == "notContains":
            return filter_str not in text_val
        if operator == "equals":
            return text_val == filter_str
        if operator == "notEqual":
            return text_val != filter_str
        if operator == "startsWith":
            return text_val.startswith(filter_str)
        if operator == "endsWith":
            return text_val.endswith(filter_str)
        if operator == "blank":
            return text_val.strip() == ""
        if operator == "notBlank":
            return text_val.strip() != ""
        return True  # Unknown text operator — don't filter out

    if filter_type == "number":
        if operator == "blank":
            return False  # value is not None (checked above), so not blank
        if operator == "notBlank":
            return True  # value is not None, so it's not blank
        num_val = _to_decimal(value)
        if num_val is None:
            return False  # Non-numeric value fails numeric filter
        if filter_value is None:
            return True  # No filter value provided — can't compare, keep row
        num_filter = _to_decimal(filter_value)
        if num_filter is None:
            return True  # Bad filter value — can't compare, keep row
        if operator == "equals":
            return num_val == num_filter
        if operator == "notEqual":
            return num_val != num_filter
        if operator == "greaterThan":
            return num_val > num_filter
        if operator == "greaterThanOrEqual":
            return num_val >= num_filter
        if operator == "lessThan":
            return num_val < num_filter
        if operator == "lessThanOrEqual":
            return num_val <= num_filter
        if operator == "inRange":
            num_to = _to_decimal(filter_def.get("filterTo"))
            if num_to is None:
                return True  # Bad/missing upper bound — can't compare, keep row
            return num_filter <= num_val <= num_to
        return True

    if filter_type == "date":
        # AG Grid sends dateFrom/dateTo as ISO-ish strings, e.g. "2026-03-28 00:00:00"
        date_from_str = filter_def.get("dateFrom")
        date_to_str = filter_def.get("dateTo")

        def _to_naive_utc(dt: datetime) -> datetime:
            """Convert to UTC then strip tzinfo for consistent comparison."""
            if dt.tzinfo is not None:
                dt = dt.astimezone(UTC).replace(tzinfo=None)
            return dt

        # Coerce cell value to a comparable datetime
        if isinstance(value, datetime):
            dt_val = _to_naive_utc(value)
        elif isinstance(value, date):
            dt_val = datetime(value.year, value.month, value.day)
        elif isinstance(value, str):
            try:
                dt_val = _to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                return False  # Unparseable — exclude (fail-closed, consistent with numeric)
        else:
            return False  # Non-date value — exclude (fail-closed, consistent with numeric)

        def _parse_ag_date(s: str | None) -> datetime | None:
            if not s:
                return None
            try:
                return _to_naive_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
            except ValueError:
                return None

        dt_from = _parse_ag_date(date_from_str)
        dt_to = _parse_ag_date(date_to_str)

        if operator == "blank":
            return False  # value is not None (checked above)
        if operator == "notBlank":
            return True
        if dt_from is None:
            return True  # No filter value — can't compare, keep row

        if operator == "equals":
            return dt_val.date() == dt_from.date()
        if operator == "notEqual":
            return dt_val.date() != dt_from.date()
        if operator == "greaterThan":
            return dt_val.date() > dt_from.date()
        if operator == "lessThan":
            return dt_val.date() < dt_from.date()
        if operator == "inRange":
            if dt_to is not None:
                return dt_from.date() <= dt_val.date() <= dt_to.date()
            # Missing dateTo — can't apply range, keep row (consistent with numeric)
            return True
        return True

    # Unsupported filter type — keep the row
    return True


def _match_compound_filter(value: Any, filter_def: dict[str, Any]) -> bool:
    """Handle AG Grid compound filters.

    AG Grid emits two compound formats:
    1. ``operator`` + ``condition1`` / ``condition2`` (legacy)
    2. ``operator`` + ``conditions: [...]`` (simple combined filters)

    If neither compound format is detected, delegate to _match_filter.
    """
    operator = filter_def.get("operator")
    condition1 = filter_def.get("condition1")
    condition2 = filter_def.get("condition2")
    conditions = filter_def.get("conditions")

    if operator and isinstance(conditions, list) and len(conditions) > 0:
        # AG Grid conditions array format — children inherit filterType from parent
        parent_filter_type = filter_def.get("filterType")
        enriched: list[dict[str, Any]] = []
        for c in conditions:
            if isinstance(c, dict):
                if "filterType" not in c and parent_filter_type:
                    enriched.append({**c, "filterType": parent_filter_type})
                else:
                    enriched.append(c)
        # Recurse via _match_compound_filter to handle nested compounds
        results = [_match_compound_filter(value, c) for c in enriched]
        if not results:
            return True  # No valid conditions — keep the row
        if operator == "AND":
            return all(results)
        if operator == "OR":
            return any(results)
        return results[0]

    if operator and isinstance(condition1, dict):
        # Legacy condition1/condition2 format — recurse for nested compounds
        result1 = _match_compound_filter(value, condition1)
        # Missing condition2: AND defaults to True (identity), OR to False (identity)
        if isinstance(condition2, dict):
            result2 = _match_compound_filter(value, condition2)
        else:
            result2 = operator != "OR"
        if operator == "AND":
            return result1 and result2
        if operator == "OR":
            return result1 or result2
        return result1  # Unknown operator — fall back to condition1 only

    # Simple (non-compound) filter
    return _match_filter(value, filter_def)


def _apply_filters(
    columns: list[str],
    rows: list[list[Any]],
    filter_params: dict[str, Any],
) -> list[list[Any]]:
    """Apply AG Grid filter model to rows (Python-side).

    Args:
        columns: Column names matching row indices.
        filter_params: AG Grid filter model, e.g.
            {"symbol": {"filterType": "text", "type": "contains", "filter": "AAPL"}}

    Returns:
        Filtered rows.
    """
    col_index = {c: i for i, c in enumerate(columns)}

    # Build list of (column_index, filter_def) for active filters
    active_filters: list[tuple[int, dict[str, Any]]] = []
    for col_name, filter_def in filter_params.items():
        if col_name in col_index and isinstance(filter_def, dict):
            active_filters.append((col_index[col_name], filter_def))

    if not active_filters:
        return rows

    def _row_matches(row: list[Any]) -> bool:
        for idx, fdef in active_filters:
            if not _match_compound_filter(row[idx], fdef):
                return False
        return True

    return [row for row in rows if _row_matches(row)]


def _apply_sort(
    columns: list[str],
    rows: list[list[Any]],
    sort_model: list[dict[str, Any]],
) -> list[list[Any]]:
    """Apply AG Grid sort model to rows (Python-side).

    Uses ``sortIndex`` (if present) to determine multi-sort precedence,
    matching AG Grid behaviour.  When ``sortIndex`` is absent the array
    position is used as a fallback so that simple sort models still work.

    Args:
        columns: Column names matching row indices.
        sort_model: AG Grid sort model, e.g.
            [{"colId": "symbol", "sort": "asc", "sortIndex": 0},
             {"colId": "qty", "sort": "desc", "sortIndex": 1}]

    Returns:
        Sorted rows (new list).
    """
    col_index = {c: i for i, c in enumerate(columns)}

    # Normalise sort order: honour sortIndex when present, fall back to
    # array position.  Higher sortIndex = lower priority (applied first in
    # the reversed iteration below).
    def _safe_sort_index(spec: dict[str, Any], fallback: int) -> int:
        raw = spec.get("sortIndex")
        if raw is None:
            return fallback
        try:
            return int(raw)
        except (ValueError, TypeError):
            return fallback

    indexed_model: list[tuple[int, dict[str, Any]]] = [
        (_safe_sort_index(spec, pos), spec)
        for pos, spec in enumerate(sort_model)
    ]
    indexed_model.sort(key=lambda t: t[0])
    ordered_specs = [spec for _, spec in indexed_model]

    def _sort_value(v: Any) -> tuple[int, Any]:
        """Return a comparable sort key safe for mixed-type columns.

        Numeric types (int/float/Decimal) are coerced to Decimal so they
        compare correctly with each other without precision loss.
        Non-numeric values are stringified and placed in a separate
        bucket to avoid TypeError.
        """
        if isinstance(v, bool):
            return (2, str(v))  # bool is subclass of int; sort as string
        if isinstance(v, Decimal):
            return (0, v)
        if isinstance(v, int | float):
            return (0, Decimal(str(v)))
        if isinstance(v, datetime):
            # Aware → convert to UTC then strip tz; naive → assumed UTC per project standard
            if v.tzinfo is not None:
                return (1, v.astimezone(UTC).replace(tzinfo=None))
            return (1, v)
        if isinstance(v, date):
            return (1, datetime(v.year, v.month, v.day))
        return (2, str(v))

    # Build sort keys in reverse priority order (last = primary in multi-sort)
    sorted_rows = list(rows)
    for sort_spec in reversed(ordered_specs):
        col_id = sort_spec.get("colId", "")
        if col_id not in col_index:
            continue
        idx = col_index[col_id]
        descending = sort_spec.get("sort", "asc") == "desc"

        # Partition nulls out so they always end up last regardless of direction
        null_rows: list[list[Any]] = []
        non_null_rows: list[list[Any]] = []
        for r in sorted_rows:
            (null_rows if r[idx] is None else non_null_rows).append(r)

        def _make_key(col_idx: int) -> Callable[[list[Any]], tuple[int, Any]]:
            def _key(row: list[Any]) -> tuple[int, Any]:
                return _sort_value(row[col_idx])
            return _key

        non_null_rows.sort(key=_make_key(idx), reverse=descending)
        sorted_rows = non_null_rows + null_rows

    return sorted_rows


def _build_excel_sync(
    ctx: AppContext,
    grid_name: str,
    strategy_ids: list[str],
    filter_params: dict[str, Any] | None,
    visible_columns: list[str] | None,
    sort_model: list[dict[str, Any]] | None,
    max_rows: int | None = None,
) -> tuple[bytes, int]:
    """Synchronous helper that does the heavy lifting for Excel export.

    Separated from the async wrapper so it can be offloaded to a worker
    thread via ``asyncio.to_thread``, preventing event-loop blocking on
    large datasets (up to 50 000 rows).

    Args:
        max_rows: When provided, truncate output to this many rows after
            filtering/sorting.  Used for visible-scope exports to match
            the number of rows the user sees in the grid.
    """
    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
    except ImportError as e:
        raise NotImplementedError(
            "Excel export requires openpyxl: pip install openpyxl"
        ) from e

    fetcher = _GRID_FETCHERS.get(grid_name)
    if fetcher is None:
        raise NotImplementedError(f"Excel export not implemented for grid: {grid_name}")

    # Fetch real data
    all_columns, data_rows = fetcher(ctx, strategy_ids, filter_params)

    # Apply filter_params (Python-side filtering for AG Grid filter model)
    if filter_params:
        data_rows = _apply_filters(all_columns, data_rows, filter_params)

    # Apply sort_model (Python-side sorting for AG Grid sort model)
    if sort_model:
        data_rows = _apply_sort(all_columns, data_rows, sort_model)

    # Truncate to visible-scope row count if requested
    if max_rows is not None and len(data_rows) > max_rows:
        data_rows = data_rows[:max_rows]

    # Filter to visible columns if specified, preserving CLIENT column order
    # Use `is not None` so an explicit empty list [] produces an empty export
    if visible_columns is not None:
        col_index_map = {c: i for i, c in enumerate(all_columns)}
        # Iterate visible_columns (client order), skip any not in server columns
        ordered_indices = [
            col_index_map[c] for c in visible_columns if c in col_index_map
        ]
        headers = [all_columns[i] for i in ordered_indices]
        rows = [[row[i] for i in ordered_indices] for row in data_rows]
    else:
        headers = all_columns
        rows = data_rows

    # Build workbook (write-only mode for memory efficiency on large exports)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=grid_name.title())

    # Header row (sanitized)
    ws.append([sanitize_for_export(h) for h in headers])

    # Data rows (sanitized + type-coerced)
    for row in rows:
        ws.append([_coerce_cell_value(v) for v in row])

    output = io.BytesIO()
    wb.save(output)
    wb.close()  # Required for write_only mode to finalize temp files
    output.seek(0)

    row_count = len(rows)
    return output.getvalue(), row_count


async def _generate_excel_content(
    ctx: AppContext,
    grid_name: str,
    strategy_ids: list[str],
    filter_params: dict[str, Any] | None,
    visible_columns: list[str] | None,
    sort_model: list[dict[str, Any]] | None,
    max_rows: int | None = None,
) -> tuple[bytes, int]:
    """Generate Excel file content for a grid.

    Offloads the blocking work (DB fetch, filtering, sorting, openpyxl
    workbook generation) to a worker thread so the FastAPI event loop
    is not blocked on large exports.
    """
    import asyncio

    return await asyncio.to_thread(
        _build_excel_sync,
        ctx, grid_name, strategy_ids,
        filter_params, visible_columns, sort_model,
        max_rows,
    )


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
