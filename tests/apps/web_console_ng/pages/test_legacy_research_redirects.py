"""Unit tests for legacy research-route compatibility redirects."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from apps.web_console_ng.pages import legacy_research_redirects as legacy_module


def test_build_backtest_redirect_target_maps_legacy_query() -> None:
    """Legacy /backtest query params should map to Research Validate URL."""
    result = legacy_module._build_backtest_redirect_target(
        b"tab=running&signal_id=sig-123&source=alpha_explorer"
    )

    assert (
        result
        == "/research?tab=validate&backtest_tab=running&signal_id=sig-123&source=alpha_explorer"
    )


def test_build_backtest_redirect_target_defaults_on_invalid_tab() -> None:
    """Unknown legacy tab should fail open to Research Validate root."""
    result = legacy_module._build_backtest_redirect_target(b"tab=unknown")

    assert result == "/research?tab=validate"


def test_build_backtest_redirect_target_ignores_blank_context() -> None:
    """Blank signal/source params should not leak into redirect URL."""
    result = legacy_module._build_backtest_redirect_target(
        b"tab=results&signal_id=%20%20&source=%20"
    )

    assert result == "/research?tab=validate&backtest_tab=results"


def test_legacy_route_handlers_accept_no_positional_args() -> None:
    """Compatibility route handlers must be callable by NiceGUI without args."""
    source = inspect.getsource(legacy_module)

    assert "async def legacy_alpha_explorer_redirect() -> None:" in source
    assert "async def legacy_backtest_redirect() -> None:" in source
    assert "async def legacy_models_redirect() -> None:" in source


def test_legacy_route_handlers_target_expected_research_tabs() -> None:
    """Legacy route handlers should redirect to canonical Research tabs."""
    source = inspect.getsource(legacy_module)

    assert 'ui.navigate.to("/research?tab=discover")' in source
    assert 'ui.navigate.to("/research?tab=promote")' in source


def test_get_current_request_raw_query_reads_scope_value(monkeypatch) -> None:
    """Raw query extractor should read query_string from request scope mapping."""
    stub_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"query_string": b"tab=running&source=legacy"})
            )
        )
    )
    monkeypatch.setattr(legacy_module, "ui", stub_ui)

    assert legacy_module._get_current_request_raw_query() == b"tab=running&source=legacy"


def test_get_current_request_raw_query_returns_none_without_scope(monkeypatch) -> None:
    """Raw query extractor should fail open when request has no scope mapping."""
    stub_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope=None)
            )
        )
    )
    monkeypatch.setattr(legacy_module, "ui", stub_ui)

    assert legacy_module._get_current_request_raw_query() is None


def test_get_current_request_raw_query_handles_context_exception(monkeypatch) -> None:
    """Raw query extractor should fail open when context access raises."""

    class _BrokenUI:
        @property
        def context(self):  # pragma: no cover - property invoked by helper
            raise RuntimeError("boom")

    monkeypatch.setattr(legacy_module, "ui", _BrokenUI())

    assert legacy_module._get_current_request_raw_query() is None
