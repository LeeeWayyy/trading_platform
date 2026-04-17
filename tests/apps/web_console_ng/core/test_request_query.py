"""Tests for request query-parameter helpers."""

from __future__ import annotations

from types import SimpleNamespace

from apps.web_console_ng.core.request_query import (
    get_query_param_from_raw_query,
    get_request_query_param,
)


def test_get_query_param_from_raw_query_bytes() -> None:
    """Raw query-string bytes should parse into first-value selection."""
    assert (
        get_query_param_from_raw_query(
            raw_query=b"tab=validate&tab=running",
            key="tab",
        )
        == "validate"
    )


def test_get_request_query_param_prefers_query_params_mapping() -> None:
    """Request query_params mapping should be used when available."""
    request = SimpleNamespace(query_params={"signal_id": "sig-123"}, scope={})
    assert (
        get_request_query_param(
            request=request,
            key="signal_id",
        )
        == "sig-123"
    )


def test_get_request_query_param_falls_back_to_scope_query_string() -> None:
    """Scope query_string fallback should support request stubs in tests."""
    request = SimpleNamespace(scope={"query_string": b"source=alpha_explorer"})
    assert (
        get_request_query_param(
            request=request,
            key="source",
        )
        == "alpha_explorer"
    )


def test_get_request_query_param_defaults_on_missing_request() -> None:
    """None request should return explicit default without raising."""
    assert (
        get_request_query_param(
            request=None,
            key="missing",
            default="fallback",
        )
        == "fallback"
    )
