"""Unit tests for /research workspace helpers."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from apps.web_console_ng.pages import research as research_module
from libs.web_console_services.research_workspace_service import (
    LIFECYCLE_CANDIDATE,
    LIFECYCLE_LIVE,
    LifecycleRow,
)


def test_get_requested_research_tab_valid(monkeypatch) -> None:
    """Valid tab query should be preserved."""
    fake_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"query_string": b"tab=promote"})
            )
        )
    )
    monkeypatch.setattr(research_module, "ui", fake_ui)

    assert research_module._get_requested_research_tab() == research_module.TAB_PROMOTE


def test_get_requested_research_tab_invalid_defaults(monkeypatch) -> None:
    """Invalid tab query should default to Discover."""
    fake_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"query_string": b"tab=unknown"})
            )
        )
    )
    monkeypatch.setattr(research_module, "ui", fake_ui)

    assert research_module._get_requested_research_tab() == research_module.TAB_DISCOVER


def test_get_requested_research_tab_missing_request_defaults(monkeypatch) -> None:
    """Missing request context should fail open to Discover."""
    fake_ui = SimpleNamespace(
        context=SimpleNamespace(client=SimpleNamespace(request=None))
    )
    monkeypatch.setattr(research_module, "ui", fake_ui)

    assert research_module._get_requested_research_tab() == research_module.TAB_DISCOVER


def test_get_requested_validate_backtest_tab_valid(monkeypatch) -> None:
    """Valid backtest sub-tab query should be preserved."""
    fake_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"query_string": b"backtest_tab=running"})
            )
        )
    )
    monkeypatch.setattr(research_module, "ui", fake_ui)

    assert research_module._get_requested_validate_backtest_tab() == "running"


def test_get_requested_validate_backtest_tab_invalid_defaults(monkeypatch) -> None:
    """Invalid backtest sub-tab query should default to new."""
    fake_ui = SimpleNamespace(
        context=SimpleNamespace(
            client=SimpleNamespace(
                request=SimpleNamespace(scope={"query_string": b"backtest_tab=invalid"})
            )
        )
    )
    monkeypatch.setattr(research_module, "ui", fake_ui)

    assert research_module._get_requested_validate_backtest_tab() == "new"


def test_build_validate_backtest_link() -> None:
    """Discover links should route to consolidated Research Validate tab."""
    result = research_module._build_validate_backtest_link(signal_id="sig-123")

    assert result == (
        "/research?tab=validate&backtest_tab=new&signal_id=sig-123&source=alpha_explorer"
    )


def test_get_research_workspace_service_process_cache(monkeypatch) -> None:
    """Workspace service should cache per process and refresh on dir change."""
    monkeypatch.setattr(research_module, "_research_workspace_service_cache", None)
    monkeypatch.setattr(research_module, "_research_workspace_service_registry_dir", None)
    monkeypatch.setattr(research_module, "_research_workspace_service_import_error", None)
    monkeypatch.setattr(research_module.config, "MODEL_REGISTRY_DIR", "/tmp/research-cache-a")

    first = research_module._get_research_workspace_service()
    second = research_module._get_research_workspace_service()

    assert first is second

    monkeypatch.setattr(research_module.config, "MODEL_REGISTRY_DIR", "/tmp/research-cache-b")
    third = research_module._get_research_workspace_service()

    assert third is not first


def test_get_research_workspace_service_skips_after_import_error(monkeypatch) -> None:
    """Missing optional dependency should short-circuit service initialization."""
    monkeypatch.setattr(research_module, "_research_workspace_service_import_error", "duckdb")
    assert research_module._get_research_workspace_service() is None


def _row(
    *,
    strategy_name: str,
    version: str,
    lifecycle_label: str,
    linked: bool,
    ops_status: str | None = None,
) -> LifecycleRow:
    return LifecycleRow(
        strategy_name=strategy_name,
        version=version,
        ops_status=ops_status,
        research_status=None,
        lifecycle_label=lifecycle_label,
        linkage_key="k",
        linked=linked,
        signal_id=None,
        backtest_job_id=None,
        snapshot_id=None,
        dataset_version_ids={},
        config_hash=None,
    )


def test_discover_candidate_rows_filters_and_sorts() -> None:
    rows = [
        _row(
            strategy_name="zeta",
            version="v2",
            lifecycle_label=LIFECYCLE_CANDIDATE,
            linked=True,
        ),
        _row(
            strategy_name="alpha",
            version="v1",
            lifecycle_label=LIFECYCLE_CANDIDATE,
            linked=True,
        ),
        _row(
            strategy_name="beta",
            version="v3",
            lifecycle_label=LIFECYCLE_LIVE,
            linked=True,
        ),
        _row(
            strategy_name="omega",
            version="v1",
            lifecycle_label=LIFECYCLE_CANDIDATE,
            linked=False,
        ),
    ]

    result = research_module._discover_candidate_rows(rows)

    assert [(r.strategy_name, r.version) for r in result] == [("alpha", "v1"), ("zeta", "v2")]


def test_resolve_promote_action_by_role_and_status() -> None:
    inactive_row = _row(
        strategy_name="alpha",
        version="v1",
        lifecycle_label=LIFECYCLE_CANDIDATE,
        linked=True,
        ops_status="inactive",
    )
    active_row = _row(
        strategy_name="alpha",
        version="v2",
        lifecycle_label=LIFECYCLE_CANDIDATE,
        linked=True,
        ops_status="active",
    )
    unlinked_row = _row(
        strategy_name="alpha",
        version="v3",
        lifecycle_label=LIFECYCLE_CANDIDATE,
        linked=False,
        ops_status="inactive",
    )

    assert research_module._resolve_promote_action(inactive_row, can_manage=True) == "ACTIVATE"
    assert research_module._resolve_promote_action(active_row, can_manage=True) == "DEACTIVATE"
    assert research_module._resolve_promote_action(unlinked_row, can_manage=True) is None
    assert research_module._resolve_promote_action(inactive_row, can_manage=False) is None


def test_resolve_accessible_tabs_orders_enabled_tabs() -> None:
    tabs = research_module._resolve_accessible_tabs(
        can_view_discover=True,
        can_view_validate=False,
        can_view_promote=True,
    )

    assert tabs == [research_module.TAB_DISCOVER, research_module.TAB_PROMOTE]


def test_resolve_selected_tab_falls_back_to_first_accessible() -> None:
    selected = research_module._resolve_selected_tab(
        requested_tab=research_module.TAB_VALIDATE,
        accessible_tabs=[research_module.TAB_DISCOVER, research_module.TAB_PROMOTE],
    )

    assert selected == research_module.TAB_DISCOVER


def test_should_load_lifecycle_rows_only_for_selected_promote() -> None:
    assert (
        research_module._should_load_lifecycle_rows(
            can_view_promote=True,
            selected_tab_id=research_module.TAB_PROMOTE,
        )
        is True
    )
    assert (
        research_module._should_load_lifecycle_rows(
            can_view_promote=True,
            selected_tab_id=research_module.TAB_DISCOVER,
        )
        is True
    )
    assert (
        research_module._should_load_lifecycle_rows(
            can_view_promote=True,
            selected_tab_id=research_module.TAB_VALIDATE,
        )
        is False
    )
    assert (
        research_module._should_load_lifecycle_rows(
            can_view_promote=False,
            selected_tab_id=research_module.TAB_PROMOTE,
        )
        is False
    )


def test_should_render_validate_panel_only_when_selected() -> None:
    assert (
        research_module._should_render_validate_panel(
            selected_tab_id=research_module.TAB_VALIDATE
        )
        is True
    )
    assert (
        research_module._should_render_validate_panel(
            selected_tab_id=research_module.TAB_DISCOVER
        )
        is False
    )


def test_validate_tab_embeds_backtest_sections() -> None:
    """Validate tab should render embedded backtest workflows."""
    source = inspect.getsource(research_module._render_validate_tab)

    assert "get_backtest_prefill_from_request" in source
    assert "render_new_backtest_form" in source
    assert "render_running_jobs" in source
    assert "render_backtest_results" in source


def test_validate_tab_checks_feature_flag_before_backtest_import() -> None:
    """Validate should fail open on disabled feature before importing backtest module."""
    source = inspect.getsource(research_module._render_validate_tab)
    assert source.index("if not config.FEATURE_BACKTEST_MANAGER") < source.index(
        "from apps.web_console_ng.pages import backtest as backtest_page"
    )


def test_optional_backtest_dependency_detection() -> None:
    """Optional dependency matcher should only allow known backtest extras."""
    assert research_module._is_optional_backtest_dependency("plotly") is True
    assert research_module._is_optional_backtest_dependency("plotly.graph_objects") is True
    assert research_module._is_optional_backtest_dependency("apps.web_console_ng.pages") is False


def test_optional_research_workspace_dependency_detection() -> None:
    """Research workspace import fallback should apply only to optional deps."""
    assert research_module._is_optional_research_workspace_dependency("duckdb") is True
    assert research_module._is_optional_research_workspace_dependency("duckdb.core") is True
    assert (
        research_module._is_optional_research_workspace_dependency("apps.web_console_ng")
        is False
    )


def test_discover_access_requires_alpha_feature_flag() -> None:
    """Discover tab gating must honor FEATURE_ALPHA_EXPLORER kill switch."""
    source = inspect.getsource(research_module.research_workspace_page)

    assert "FEATURE_ALPHA_EXPLORER" in source


def test_lifecycle_row_load_catches_specific_exceptions() -> None:
    """Lifecycle fetch should use targeted exception handling, not blanket catch."""
    source = inspect.getsource(research_module.research_workspace_page)
    assert "except (RuntimeError, ValueError, TypeError, LookupError):" in source
