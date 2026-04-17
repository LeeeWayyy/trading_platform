"""Helpers for resilient request query-parameter extraction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs


def _normalize_raw_query_string(raw_query: bytes | str | None) -> str:
    """Return UTF-8 query-string text from raw scope/query payload."""
    if raw_query is None:
        return ""
    if isinstance(raw_query, bytes):
        return raw_query.decode("utf-8")
    return str(raw_query)


def get_query_param_from_raw_query(
    *,
    raw_query: bytes | str | None,
    key: str,
    default: str | None = None,
) -> str | None:
    """Return first value for `key` from raw query-string content."""
    params = parse_qs(_normalize_raw_query_string(raw_query))
    values = params.get(key)
    if not values:
        return default
    return values[0]


def get_request_query_param(
    *,
    request: Any | None,
    key: str,
    default: str | None = None,
) -> str | None:
    """Return query parameter from Starlette/NiceGUI request-like objects."""
    if request is None:
        return default

    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        value = query_params.get(key, default)
        if value is None:
            return default
        return str(value)

    scope = getattr(request, "scope", None)
    raw_query: bytes | str | None = None
    if isinstance(scope, Mapping):
        raw_query = scope.get("query_string", b"")
    return get_query_param_from_raw_query(raw_query=raw_query, key=key, default=default)
