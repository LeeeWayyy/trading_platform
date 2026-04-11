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
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from psycopg import sql
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

    export_type: Literal["csv", "excel", "clipboard"] = Field(
        ..., description="Type of export"
    )
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

    # Generate Excel content with error handling
    try:
        excel_content, row_count = await _generate_excel_content(
            ctx=ctx,
            grid_name=grid_name,
            strategy_ids=strategy_ids,
            filter_params=filter_params,
            visible_columns=visible_columns,
            sort_model=sort_model,
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
    # (e.g. ``is_bps``, ``fill_rate_pct``) that are derived client-side.
    # The export provides the raw underlying trade/order data so users
    # can compute their own analytics.  When the grid sends computed
    # column names via ``visible_columns``, ``_validate_columns`` drops
    # them and falls back to this full raw-data allowlist.
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
        "execution_date": "executed_at",
    },
}


def _validate_columns(
    grid_name: str, visible_columns: list[str] | None
) -> list[str]:
    """Return the export column list, validated against the server allowlist.

    Frontend column aliases are resolved first (e.g. ``"time"`` →
    ``"executed_at"``).  Only columns that exist in the allowlist are
    kept (in the requested order).  Unknown columns are silently dropped
    to prevent SQL injection or data leakage.

    When the majority of requested columns are not in the allowlist (i.e.
    fewer than half survive validation), the full allowlist is returned
    instead of a misleadingly small subset.  This handles grids like TCA
    whose frontend sends computed display columns (``fill_rate_pct``,
    ``is_bps``, etc.) alongside a few raw DB columns -- returning only
    ``symbol`` and ``side`` would be incomplete and confusing.
    """
    allowed = _GRID_COLUMNS[grid_name]
    if not visible_columns:
        return allowed
    aliases = _COLUMN_ALIASES.get(grid_name, {})
    resolved = [aliases.get(c, c) for c in visible_columns]
    valid = [c for c in resolved if c in allowed]
    if not valid:
        return allowed
    # Fall back to full schema when most requested columns were
    # computed/frontend-only (fewer than half survived validation).
    if len(valid) < len(resolved) / 2:
        return allowed
    return valid


def _resolve_sort_aliases(
    grid_name: str,
    sort_model: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Resolve frontend column aliases in sort_model items.

    The web console grids may emit display-friendly column IDs in the
    sort model (e.g. ``"type"`` instead of ``"order_type"``).  This
    translates each ``colId`` through ``_COLUMN_ALIASES`` so that the
    downstream ``_build_order_clause`` can match them against the
    server-side allowlist.
    """
    if not sort_model:
        return sort_model
    aliases = _COLUMN_ALIASES.get(grid_name, {})
    if not aliases:
        return sort_model
    return [
        {**item, "colId": aliases.get(item.get("colId", ""), item.get("colId", ""))}
        for item in sort_model
    ]


def _quote_identifier(name: str) -> str:
    """Quote a possibly table-qualified identifier via psycopg.sql.

    ``psycopg.sql.Identifier`` accepts multiple string parts for qualified
    names (e.g. ``Identifier("p", "symbol")`` produces ``"p"."symbol"``).
    This helper splits on ``.`` and delegates to ``Identifier`` so that
    both simple and qualified names are safely quoted.
    """
    parts = name.split(".")
    return sql.Identifier(*parts).as_string(None)


def _build_order_clause(
    sort_model: list[dict[str, Any]] | None,
    allowed_columns: list[str],
    default_order: str,
) -> str:
    """Build a safe ORDER BY clause from an AG Grid sort model.

    Only column names that appear in *allowed_columns* are accepted.
    Items are sorted by their ``sortIndex`` (if present) so that
    multi-column sort precedence matches the user's grid configuration.

    All identifiers are quoted via ``psycopg.sql.Identifier`` to prevent
    any possibility of SQL injection, even though ``allowed_columns``
    originates from hardcoded allowlists.
    """
    if not sort_model:
        return default_order
    # Sort by sortIndex to preserve multi-column sort precedence.
    # Items without sortIndex are appended in their original order.
    indexed = sorted(
        sort_model,
        key=lambda item: (item.get("sortIndex") is None, item.get("sortIndex", 0)),
    )
    parts: list[str] = []
    for item in indexed:
        col = item.get("colId", "")
        direction = "DESC" if item.get("sort") == "desc" else "ASC"
        if col in allowed_columns:
            parts.append(f"{_quote_identifier(col)} {direction}")
    return ", ".join(parts) if parts else default_order


# Columns that are JSONB in the database and need an explicit ::text
# cast when used in text filter operations (ILIKE, =, !=).
_JSONB_COLUMNS: frozenset[str] = frozenset({"details"})


def _build_single_condition(
    col: str,
    qcol: str,
    spec: dict[str, Any],
    clauses: list[str],
    params: list[Any],
) -> None:
    """Append SQL for a single AG Grid filter condition.

    This handles one atomic condition (``type`` + ``filter``).  Compound
    filters (``operator`` + ``conditions``) are decomposed by
    ``_build_filter_clause`` before calling this helper.
    """
    filter_type = spec.get("filterType", "text")
    ftype = spec.get("type", "")

    if filter_type == "text":
        value = spec.get("filter")
        if value is None:
            return
        value = str(value)
        # JSONB columns need an explicit ::text cast for text
        # operators; for text/varchar columns the cast is omitted
        # to allow B-tree index usage.
        text_col = f"{qcol}::text" if col in _JSONB_COLUMNS else qcol
        if ftype == "contains":
            clauses.append(f"{text_col} ILIKE %s")
            params.append(f"%{value}%")
        elif ftype == "equals":
            clauses.append(f"{text_col} = %s")
            params.append(value)
        elif ftype == "startsWith":
            clauses.append(f"{text_col} ILIKE %s")
            params.append(f"{value}%")
        elif ftype == "endsWith":
            clauses.append(f"{text_col} ILIKE %s")
            params.append(f"%{value}")
        elif ftype == "notEqual":
            clauses.append(f"{text_col} != %s")
            params.append(value)

    elif filter_type == "number":
        value = spec.get("filter")
        if ftype == "equals" and value is not None:
            clauses.append(f"{qcol} = %s")
            params.append(value)
        elif ftype == "greaterThan" and value is not None:
            clauses.append(f"{qcol} > %s")
            params.append(value)
        elif ftype == "lessThan" and value is not None:
            clauses.append(f"{qcol} < %s")
            params.append(value)
        elif ftype == "greaterThanOrEqual" and value is not None:
            clauses.append(f"{qcol} >= %s")
            params.append(value)
        elif ftype == "lessThanOrEqual" and value is not None:
            clauses.append(f"{qcol} <= %s")
            params.append(value)
        elif ftype == "inRange":
            lo = spec.get("filter")
            hi = spec.get("filterTo")
            if lo is not None and hi is not None:
                clauses.append(f"{qcol} >= %s AND {qcol} <= %s")
                params.extend([lo, hi])

    elif filter_type == "date":
        date_from = spec.get("dateFrom")
        date_to = spec.get("dateTo")
        # Use range comparisons on the raw timestamp column instead of
        # ::date casts, which prevent B-tree index usage on large tables.
        # AG Grid sends dates as 'YYYY-MM-DD' strings; we compare them
        # as timestamps to enable index scans.
        if ftype == "equals" and date_from is not None:
            # "equals day" means >= start of day AND < next day
            clauses.append(f"{qcol} >= %s AND {qcol} < %s::date + interval '1 day'")
            params.extend([date_from, date_from])
        elif ftype == "greaterThan" and date_from is not None:
            # After the end of the given day
            clauses.append(f"{qcol} >= %s::date + interval '1 day'")
            params.append(date_from)
        elif ftype == "lessThan" and date_from is not None:
            clauses.append(f"{qcol} < %s::date")
            params.append(date_from)
        elif ftype == "greaterThanOrEqual" and date_from is not None:
            clauses.append(f"{qcol} >= %s::date")
            params.append(date_from)
        elif ftype == "lessThanOrEqual" and date_from is not None:
            # Include all of the given day
            clauses.append(f"{qcol} < %s::date + interval '1 day'")
            params.append(date_from)
        elif ftype == "inRange" and date_from is not None and date_to is not None:
            clauses.append(f"{qcol} >= %s AND {qcol} < %s::date + interval '1 day'")
            params.extend([date_from, date_to])

    elif filter_type == "set":
        values = spec.get("values")
        if isinstance(values, list) and values:
            # Use ANY(%s) with a list parameter for set membership
            text_col = f"{qcol}::text" if col in _JSONB_COLUMNS else qcol
            clauses.append(f"{text_col} = ANY(%s)")
            params.append(values)


def _build_filter_clause(
    filter_params: dict[str, Any] | None,
    allowed_columns: list[str],
    *,
    col_prefix: str = "",
    col_prefix_map: dict[str, str] | None = None,
) -> tuple[str, list[Any]]:
    """Build a parameterized WHERE fragment from AG Grid filter model.

    Supports the common AG Grid filter types:
    - ``text`` filters (contains, equals, startsWith, endsWith, notEqual)
    - ``number`` filters (equals, greaterThan, lessThan, inRange, etc.)
    - ``date`` filters (equals, greaterThan, lessThan, inRange)
    - ``set`` filters (values list for IN-clause matching)

    Also handles **compound** filters where AG Grid sends an
    ``operator`` (``"AND"`` / ``"OR"``) with a ``conditions`` list
    instead of a single ``type``/``filter`` pair.

    Only columns present in *allowed_columns* are accepted; unknown
    columns are silently dropped.  All values are passed as query
    parameters (never interpolated) to prevent SQL injection.

    Args:
        filter_params: AG Grid filter model dict.
        allowed_columns: Server-side column allowlist.
        col_prefix: Optional table alias prefix (e.g. ``"p."``).
            Applied to every column reference in the generated SQL
            unless overridden by *col_prefix_map*.
        col_prefix_map: Optional per-column qualified name mapping
            (e.g. ``{"order_qty": "o.qty"}``).  When a column appears
            in this map, the mapped value is used instead of
            ``col_prefix + col``.

    Returns:
        A tuple of (sql_fragment, param_list).  *sql_fragment* is a
        string like ``"AND col1 ILIKE %s AND col2 >= %s"`` (empty
        string when there are no applicable filters).  *param_list*
        contains the corresponding bind values.
    """
    if not filter_params:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    for col, spec in filter_params.items():
        if col not in allowed_columns:
            continue
        if not isinstance(spec, dict):
            continue

        # Use per-column mapping when available, otherwise fall back
        # to the blanket prefix.  All identifiers are quoted via
        # _quote_identifier to prevent any injection possibility.
        if col_prefix_map and col in col_prefix_map:
            qcol = _quote_identifier(col_prefix_map[col])
        else:
            qcol = _quote_identifier(f"{col_prefix}{col}")

        # AG Grid compound filter: ``operator`` + ``conditions`` list.
        # Example: {"operator": "AND", "conditions": [{...}, {...}]}
        conditions = spec.get("conditions")
        if isinstance(conditions, list) and conditions:
            operator = spec.get("operator", "AND").upper()
            sql_op = " OR " if operator == "OR" else " AND "
            sub_clauses: list[str] = []
            sub_params: list[Any] = []
            for cond in conditions:
                if isinstance(cond, dict):
                    _build_single_condition(col, qcol, cond, sub_clauses, sub_params)
            if sub_clauses:
                # Wrap in parentheses to preserve operator precedence
                clauses.append(f"({sql_op.join(sub_clauses)})")
                params.extend(sub_params)
        else:
            # Simple (non-compound) filter
            _build_single_condition(col, qcol, spec, clauses, params)

    result_sql = " AND ".join(clauses)
    if result_sql:
        result_sql = f"AND {result_sql} "
    return result_sql, params


# ---------------------------------------------------------------------------
# Per-grid data fetchers
# ---------------------------------------------------------------------------


def _fetch_positions_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_clause: str = "",
    filter_params_list: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch positions data scoped to authorized strategies.

    The ``positions`` table is symbol-scoped (no ``strategy_id`` column),
    so ownership is inferred via the shared fail-closed mapping from
    ``libs.data.sql.strategy_mapping_sql``: a symbol is attributed to a
    strategy only when exactly ONE strategy has ever traded it.  Symbols
    traded by multiple strategies are excluded to prevent cross-strategy
    data leakage.
    """
    # Qualify sort columns with the "p." alias to avoid ambiguity when
    # joining positions with the symbol_strategy CTE.
    qualified_sort: list[dict[str, Any]] | None = None
    if sort_model:
        qualified_sort = [
            {**item, "colId": f"p.{item.get('colId')}"} if item.get("colId") in columns else item
            for item in sort_model
        ]
    qualified_columns = [f"p.{c}" for c in columns]
    order_clause = _build_order_clause(
        qualified_sort, qualified_columns, "p.symbol ASC",
    )
    # Build SELECT list using psycopg.sql.Identifier for column names.
    select_cols = sql.SQL(", ").join(
        sql.SQL("{tbl}.{col} AS {alias}").format(
            tbl=sql.Identifier("p"),
            col=sql.Identifier(c),
            alias=sql.Identifier(c),
        )
        for c in columns
    )
    query_params: list[Any] = [strategy_ids]
    if filter_params_list:
        query_params.extend(filter_params_list)
    query_params.append(_EXPORT_ROW_LIMIT)
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = sql.SQL(
                "WITH {cte} "
                "SELECT {cols} FROM positions p "
                "JOIN symbol_strategy ss ON p.symbol = ss.symbol "
                "WHERE p.qty != 0 AND ss.strategy = ANY(%s) "
                "{filter_clause}"
                "ORDER BY {order_clause} "
                "LIMIT %s"
            ).format(
                cte=sql.SQL(SYMBOL_STRATEGY_CTE),
                cols=select_cols,
                filter_clause=sql.SQL(filter_clause),
                order_clause=sql.SQL(order_clause),
            )
            cur.execute(query, query_params)
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_orders_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_clause: str = "",
    filter_params_list: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch orders scoped to authorized strategies.

    This fetcher returns ALL orders matching strategy scope and the
    caller-provided filter clause.  It intentionally does NOT inject
    implicit status predicates because the server cannot know which UI
    tab originated the export request.

    **Frontend contract:** When the user exports from a status-scoped
    tab (e.g. the "Working Orders" tab, which pre-filters to
    pending/open/partially_filled statuses before calling
    ``setRowData``), the UI component MUST inject the corresponding
    ``status`` filter into the AG Grid ``filterModel`` before sending
    the export audit request.  ``GridExportToolbar`` is responsible for
    this; see ``apps/web_console_ng/components/grid_export_toolbar.py``.

    If the frontend omits the status filter, the export will correctly
    return all strategy-scoped orders -- which is the expected behavior
    for the "All Orders" / "History" views.
    """
    order_clause = _build_order_clause(sort_model, columns, "created_at DESC")
    select_cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    query_params: list[Any] = [strategy_ids]
    if filter_params_list:
        query_params.extend(filter_params_list)
    query_params.append(_EXPORT_ROW_LIMIT)
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = sql.SQL(
                "SELECT {cols} FROM orders "
                "WHERE strategy_id = ANY(%s) "
                "{filter_clause}"
                "ORDER BY {order_clause} "
                "LIMIT %s"
            ).format(
                cols=select_cols,
                filter_clause=sql.SQL(filter_clause),
                order_clause=sql.SQL(order_clause),
            )
            cur.execute(query, query_params)
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_fills_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_clause: str = "",
    filter_params_list: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch fills (trades) scoped to authorized strategies."""
    order_clause = _build_order_clause(sort_model, columns, "executed_at DESC")
    select_cols = sql.SQL(", ").join(
        sql.SQL("{tbl}.{col}").format(
            tbl=sql.Identifier("t"),
            col=sql.Identifier(c),
        )
        for c in columns
    )
    query_params: list[Any] = [strategy_ids]
    if filter_params_list:
        query_params.extend(filter_params_list)
    query_params.append(_EXPORT_ROW_LIMIT)
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = sql.SQL(
                "SELECT {cols} FROM trades t "
                "WHERE COALESCE(t.superseded, FALSE) = FALSE "
                "AND t.strategy_id = ANY(%s) "
                "{filter_clause}"
                "ORDER BY {order_clause} "
                "LIMIT %s"
            ).format(
                cols=select_cols,
                filter_clause=sql.SQL(filter_clause),
                order_clause=sql.SQL(order_clause),
            )
            cur.execute(query, query_params)
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


def _fetch_audit_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_clause: str = "",
    filter_params_list: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch audit log entries scoped to authorized strategies.

    Audit entries are scoped by matching the ``strategy_id`` key inside
    the ``details`` JSONB column against the authorized *strategy_ids*.
    System-level entries (those without a ``strategy_id`` in their
    details) are excluded to enforce least-privilege -- exporting only
    strategy-related audit entries prevents leaking sensitive system
    operations to users who may only be authorized for specific
    strategies.

    Note: ``details->>'strategy_id'`` assumes strategy_id is stored as a
    top-level key in the JSONB ``details`` column.  This matches the
    current audit_log schema.  If the schema evolves to nest strategy_id
    differently, update the WHERE clause below and the corresponding
    GIN index on ``audit_log.details``.
    """
    order_clause = _build_order_clause(sort_model, columns, "timestamp DESC, id DESC")
    select_cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    query_params: list[Any] = [strategy_ids]
    if filter_params_list:
        query_params.extend(filter_params_list)
    query_params.append(_EXPORT_ROW_LIMIT)
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = sql.SQL(
                "SELECT {cols} FROM audit_log "
                "WHERE details->>'strategy_id' = ANY(%s) "
                "{filter_clause}"
                "ORDER BY {order_clause} "
                "LIMIT %s"
            ).format(
                cols=select_cols,
                filter_clause=sql.SQL(filter_clause),
                order_clause=sql.SQL(order_clause),
            )
            cur.execute(query, query_params)
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


# Map TCA column names to their qualified table references.
# Every column in _GRID_COLUMNS["tca"] MUST have an entry here so
# that SELECT / ORDER BY / filter references are unambiguous across
# the trades (t) and orders (o) tables.  Columns missing from this
# map are silently skipped to prevent incorrect table attribution.
# Defined at module level so both _fetch_tca_data and
# _generate_excel_content can use it for filter qualification.
_TCA_COL_MAP: dict[str, str] = {
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


def _fetch_tca_data(
    ctx: AppContext,
    strategy_ids: list[str],
    columns: list[str],
    sort_model: list[dict[str, Any]] | None,
    filter_clause: str = "",
    filter_params_list: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch TCA trade data with order context, scoped to authorized strategies."""
    # Only include columns that have an explicit mapping to prevent
    # incorrect table attribution for unmapped columns.
    mapped_columns = [c for c in columns if c in _TCA_COL_MAP]
    # Build SELECT list using psycopg.sql for the alias identifiers.
    # The qualified source reference (e.g. "t.trade_id") comes from the
    # hardcoded _TCA_COL_MAP so it's safe to wrap with sql.SQL().
    select_cols = sql.SQL(", ").join(
        sql.SQL("{src} AS {alias}").format(
            src=sql.SQL(_TCA_COL_MAP[c]),
            alias=sql.Identifier(c),
        )
        for c in mapped_columns
    )
    # Qualify sort columns with table aliases via _TCA_COL_MAP to
    # avoid ambiguity when trades and orders share column names
    # (e.g. symbol, qty, client_order_id).  Only mapped columns
    # are accepted; unmapped sort entries are dropped.
    qualified_sort: list[dict[str, Any]] | None = None
    if sort_model:
        qualified_sort = [
            {**item, "colId": _TCA_COL_MAP[item.get("colId", "")]}
            for item in sort_model
            if item.get("colId", "") in _TCA_COL_MAP
        ]
    qualified_tca_columns = [_TCA_COL_MAP[c] for c in mapped_columns]
    order_clause = _build_order_clause(
        qualified_sort, qualified_tca_columns, "t.executed_at DESC",
    )
    query_params: list[Any] = [strategy_ids]
    if filter_params_list:
        query_params.extend(filter_params_list)
    query_params.append(_EXPORT_ROW_LIMIT)
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            query = sql.SQL(
                "SELECT {cols} "
                "FROM trades t "
                "LEFT JOIN orders o ON t.client_order_id = o.client_order_id "
                "WHERE COALESCE(t.superseded, FALSE) = FALSE "
                "AND t.strategy_id = ANY(%s) "
                "{filter_clause}"
                "ORDER BY {order_clause} "
                "LIMIT %s"
            ).format(
                cols=select_cols,
                filter_clause=sql.SQL(filter_clause),
                order_clause=sql.SQL(order_clause),
            )
            cur.execute(query, query_params)
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row, strict=True)) for row in cur.fetchall()]


# Dispatch table for grid data fetchers
_GRID_FETCHERS = {
    "positions": _fetch_positions_data,
    "orders": _fetch_orders_data,
    "fills": _fetch_fills_data,
    "audit": _fetch_audit_data,
    "tca": _fetch_tca_data,
}


def _sanitize_cell_value(raw_value: Any) -> Any:
    """Sanitise a single cell value for Excel export.

    Strings are sanitised against formula injection.  Temporal types
    are normalised to UTC and stripped of timezone info (openpyxl
    does not support tz-aware datetimes).  JSONB dicts/lists are
    serialised to JSON strings.

    Returns the sanitised value ready for ``ws.append()``.
    """
    if isinstance(raw_value, dict | list):
        raw_value = json.dumps(raw_value, default=str)
    if isinstance(raw_value, str):
        return sanitize_for_export(raw_value)
    if raw_value is None:
        return ""
    if isinstance(raw_value, datetime):
        # openpyxl does not support timezone-aware datetimes.
        # Normalize all datetimes to UTC before stripping tzinfo.
        if raw_value.tzinfo is None:
            raw_value = raw_value.replace(tzinfo=UTC)
        return raw_value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(raw_value, date):
        # PostgreSQL DATE columns are returned as datetime.date
        # objects by psycopg.  openpyxl handles date natively.
        return raw_value
    return raw_value


def _build_workbook(
    grid_name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> bytes:
    """Build an Excel workbook from grid data using streaming mode.

    Uses ``write_only=True`` to stream rows to disk without building
    the full XML tree in memory, keeping peak usage low even for the
    maximum ``_EXPORT_ROW_LIMIT`` (10 000 rows).

    Every cell value is sanitised via ``_sanitize_cell_value`` to
    prevent formula-injection attacks.

    Args:
        grid_name: Name of the grid (used as worksheet title).
        columns: Ordered list of column names to include.
        rows: Row data dicts keyed by column name.

    Returns:
        Raw bytes of the ``.xlsx`` file.
    """
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(grid_name.title())

    # Header row -- sanitize headers
    ws.append([sanitize_for_export(h) for h in columns])

    # Data rows -- sanitize values to prevent formula injection
    # while preserving native Excel types (numbers, dates).
    for row_data in rows:
        ws.append([
            _sanitize_cell_value(row_data.get(col_name))
            for col_name in columns
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


async def _generate_excel_content(
    ctx: AppContext,
    grid_name: str,
    strategy_ids: list[str],
    filter_params: dict[str, Any] | None,
    visible_columns: list[str] | None,
    sort_model: list[dict[str, Any]] | None,
) -> tuple[bytes, int]:
    """Generate Excel file content for a grid.

    Fetches real data from the database for the requested grid, applies
    column validation against a server-side allowlist, and sanitises every
    cell value to prevent formula-injection attacks.

    **Important:** This function uses ``filter_params`` (the AG Grid filter
    model) as the single source of truth for row filtering.  If the
    frontend applies additional filters outside the AG Grid filter model
    (e.g. a dashboard symbol dropdown that pre-filters ``setRowData``),
    those filters must be propagated into ``filter_params`` before the
    export audit is created, or the export will include rows not visible
    in the current grid view.

    Args:
        ctx: Application context with database access
        grid_name: Name of grid to export
        strategy_ids: Authorized strategy IDs for filtering
        filter_params: AG Grid filter model applied to export query.
            Must include any status/scope filters from the active tab
            (e.g. working-order status when exporting from "Working" tab).
        visible_columns: Columns to include (validated against allowlist)
        sort_model: AG Grid sort model

    Returns:
        Tuple of (excel_bytes, row_count)

    Raises:
        NotImplementedError: If grid type not supported or openpyxl missing
    """
    if grid_name not in _GRID_FETCHERS:
        raise NotImplementedError(f"Excel export not implemented for grid: {grid_name}")

    # Validate requested columns against server allowlist
    columns = _validate_columns(grid_name, visible_columns)

    # Resolve frontend aliases in sort_model before building ORDER BY
    resolved_sort = _resolve_sort_aliases(grid_name, sort_model)

    # Grids that use table aliases in their SQL queries need a prefix
    # so that filter column references match the aliased table.
    _GRID_FILTER_PREFIX: dict[str, str] = {
        "positions": "p.",
        "fills": "t.",
    }
    col_prefix = _GRID_FILTER_PREFIX.get(grid_name, "")

    # TCA spans two tables (trades t, orders o), so we use the
    # per-column mapping instead of a blanket prefix.
    tca_filter_map: dict[str, str] | None = None
    if grid_name == "tca":
        tca_filter_map = _TCA_COL_MAP

    # Also resolve aliases in filter_params keys before building the
    # filter clause (e.g. "type" -> "order_type" for orders grid).
    resolved_filter = filter_params
    if filter_params:
        aliases = _COLUMN_ALIASES.get(grid_name, {})
        if aliases:
            resolved_filter = {
                aliases.get(k, k): v for k, v in filter_params.items()
            }

    # Build filter WHERE clause from AG Grid filter model.
    # Use the full grid allowlist (not just the export projection) so
    # that filters on hidden columns are preserved.  For example, if
    # a user filters by "status" but only exports "symbol"/"qty", the
    # status predicate must still be applied.
    full_allowlist = _GRID_COLUMNS[grid_name]
    filter_clause, filter_params_list = _build_filter_clause(
        resolved_filter, full_allowlist, col_prefix=col_prefix,
        col_prefix_map=tca_filter_map,
    )

    # Offload synchronous DB fetch to a worker thread to avoid blocking
    # the FastAPI event loop.
    fetcher = _GRID_FETCHERS[grid_name]
    rows = await asyncio.to_thread(
        fetcher, ctx, strategy_ids, columns, resolved_sort,
        filter_clause, filter_params_list,
    )

    excel_bytes = await asyncio.to_thread(
        _build_workbook, grid_name, columns, rows,
    )
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
