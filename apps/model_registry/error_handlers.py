"""
Error handlers for Model Registry API.

Installs a custom ``HTTPException`` handler that flattens the nested
``{"detail": {"detail": ..., "code": ...}}`` shape produced by the default
FastAPI handler (when a dict is passed to ``HTTPException.detail``) into the
flat ``{"detail": str, "code": str}`` shape declared by
:class:`apps.model_registry.schemas.ErrorResponse`.

Fixes issue #166: callers declared ``ErrorResponse`` as their 4xx/5xx schema
but routes raised ``HTTPException(detail={"detail": ..., "code": ...})``,
which FastAPI would wrap a second time — producing a doubly-nested payload
that did not match the OpenAPI contract and broke client deserialization.
"""

from __future__ import annotations

from typing import Any, TypeGuard

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.model_registry.schemas import ErrorResponse


def _is_detail_code_mapping(detail: Any) -> TypeGuard[dict[str, Any]]:
    """Return True iff ``detail`` is a mapping containing ``detail`` and ``code`` string keys."""
    if not isinstance(detail, dict):
        return False
    if "detail" not in detail or "code" not in detail:
        return False
    # Require the inner "detail" to be a string and "code" to be a string so we
    # only flatten the well-known shape produced by routes.py; anything else is
    # left to FastAPI's default handler.
    return isinstance(detail.get("detail"), str) and isinstance(detail.get("code"), str)


async def flatten_http_exception_handler(request: Request, exc: Exception) -> Response:
    """Flatten ``HTTPException(detail={"detail": str, "code": str})`` payloads.

    Any other ``HTTPException`` is delegated to FastAPI's default handler so
    that headers, status codes, and string-detail responses continue to work
    exactly as they did before this handler was installed.

    The signature accepts ``Exception`` to match Starlette's exception-handler
    protocol; callers must only register this for (Starlette)``HTTPException``
    subclasses, which :func:`install_error_handlers` does. For extra safety
    against misregistration (and against Python running with ``-O`` where
    ``assert`` statements are stripped), we verify the type explicitly and
    fall back to the default handler for anything unexpected.
    """
    if not isinstance(exc, StarletteHTTPException):
        return await default_http_exception_handler(request, exc)  # type: ignore[arg-type]

    if _is_detail_code_mapping(exc.detail):
        # Build the payload via ``ErrorResponse`` so serialization is driven
        # by the Pydantic model — any future schema changes (e.g. added
        # fields with defaults) stay in sync automatically.
        payload = ErrorResponse(
            detail=exc.detail["detail"],
            code=exc.detail["code"],
        ).model_dump()
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=getattr(exc, "headers", None),
        )
    return await default_http_exception_handler(request, exc)


def install_error_handlers(app: FastAPI) -> None:
    """Register the flattening ``HTTPException`` handler on ``app``.

    Registered for both FastAPI's ``HTTPException`` and the underlying
    Starlette ``HTTPException`` so sub-apps and middleware-level raises are
    also flattened.
    """
    app.add_exception_handler(HTTPException, flatten_http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, flatten_http_exception_handler)
