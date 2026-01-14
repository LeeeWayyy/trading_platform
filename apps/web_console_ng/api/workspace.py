"""API endpoints for workspace persistence.

SECURITY: User identity is derived from authenticated session cookie,
NOT from client-provided headers. This prevents spoofing attacks.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel

from apps.web_console_ng.auth.csrf import verify_csrf_token
from apps.web_console_ng.core.workspace_persistence import (
    MAX_STATE_SIZE,
    DatabaseUnavailableError,
    get_workspace_service,
)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# Allowlist of valid grid IDs to prevent storage fan-out attacks
#
# MAINTENANCE: When adding a new grid that needs workspace persistence:
# 1. Add the grid_id to this set
# 2. Update the browser-side GridThrottle.recordUpdate() call in the new grid's initialization
# 3. Add the grid_id to the test fixtures in test_workspace_api_security.py
# 4. Run tests: pytest tests/apps/web_console_ng/test_workspace_api_security.py -v
#
# Valid grid IDs should match the pattern used in create_*_grid() functions.
VALID_GRID_IDS = frozenset(
    {
        "positions_grid",
        "orders_grid",
        "backtest_results_grid",
        "risk_metrics_grid",
    }
)
MAX_GRID_ID_LENGTH = 64


def validate_grid_id(grid_id: str) -> None:
    """Validate grid_id against allowlist to prevent storage fan-out.

    SECURITY: Unvalidated grid_id allows arbitrary key creation, leading to storage bloat.
    """
    if len(grid_id) > MAX_GRID_ID_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"grid_id exceeds max length ({MAX_GRID_ID_LENGTH})",
        )
    if grid_id not in VALID_GRID_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid grid_id. Must be one of: {', '.join(sorted(VALID_GRID_IDS))}",
        )


async def enforce_max_state_size(request: Request) -> None:
    """Reject requests with body size above MAX_STATE_SIZE (streaming-safe)."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header",
            ) from exc
        if length > MAX_STATE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="State too large",
            )

    # If body already cached, validate size and return.
    cached_body = getattr(request, "_body", None)
    if cached_body is not None:
        if len(cached_body) > MAX_STATE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="State too large",
            )
        return

    # Stream the body with a hard cap to prevent chunked/misreported sizes.
    received = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        received.extend(chunk)
        if len(received) > MAX_STATE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="State too large",
            )
    request._body = bytes(received)
    request._stream_consumed = True


class GridState(BaseModel):
    """Grid state model."""

    columns: list[dict[str, Any]] | None = None
    filters: dict[str, Any] | None = None
    sort: list[dict[str, Any]] | None = None


async def require_authenticated_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency to get authenticated user from session cookie.

    SECURITY: Derives user from session cookie via request.state.user (set by SessionMiddleware).
    Falls back to direct session validation if middleware hasn't run.
    Raises 401 if not authenticated.
    """
    # FIRST: Check request.state.user (set by SessionMiddleware)
    user: dict[str, Any] | None = getattr(request.state, "user", None)
    if user and user.get("user_id"):
        return user

    # FALLBACK: Direct session validation (if SessionMiddleware didn't run)
    from apps.web_console_ng.auth.middleware import _validate_session_and_get_user

    user_data, _ = await _validate_session_and_get_user(request)
    if not user_data or not user_data.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user_data


@router.post("/grid/{grid_id}")
async def save_grid_state(
    grid_id: str,
    request: Request,
    state: GridState = Body(...),
    _size_guard: None = Depends(enforce_max_state_size),
    user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, bool]:
    """Save grid state.

    SECURITY: User derived from session, CSRF validated, grid_id validated against allowlist.
    """
    validate_grid_id(grid_id)
    await verify_csrf_token(request)

    service = get_workspace_service()
    try:
        success = await service.save_grid_state(
            user_id=user["user_id"],
            grid_id=grid_id,
            state=state.model_dump(exclude_none=True),
        )
    except DatabaseUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        ) from None
    if not success:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="State too large",
        )
    return {"success": True}


@router.get("/grid/{grid_id}")
async def load_grid_state(
    grid_id: str,
    user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, Any] | None:
    """Load grid state.

    SECURITY: User derived from session, grid_id validated against allowlist.
    """
    validate_grid_id(grid_id)
    service = get_workspace_service()
    try:
        return await service.load_grid_state(user_id=user["user_id"], grid_id=grid_id)
    except DatabaseUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        ) from None


@router.delete("/grid/{grid_id}")
async def reset_grid_state(
    grid_id: str,
    request: Request,
    user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, bool]:
    """Reset grid state to defaults.

    SECURITY: User derived from session, CSRF validated, grid_id validated against allowlist.
    """
    validate_grid_id(grid_id)
    await verify_csrf_token(request)

    service = get_workspace_service()
    try:
        await service.reset_workspace(user["user_id"], f"grid.{grid_id}")
    except DatabaseUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        ) from None
    return {"success": True}
