from __future__ import annotations

from apps.web_console_ng.research_query import sanitize_research_query_items


def test_sanitize_research_query_items_deduplicates_backtest_tab() -> None:
    query_items = [
        ("tab", "validate"),
        ("backtest_tab", "compare"),
        ("backtest_tab", "running"),
    ]

    assert sanitize_research_query_items(
        query_items,
        selected_tab="validate",
        include_tab=True,
    ) == [
        ("tab", "validate"),
        ("backtest_tab", "results"),
    ]


def test_sanitize_research_query_items_excludes_tab_when_include_tab_false() -> None:
    query_items = [
        ("tab", "promote"),
        ("model_id", "m-1"),
    ]

    assert sanitize_research_query_items(
        query_items,
        selected_tab="promote",
        include_tab=False,
    ) == [
        ("model_id", "m-1"),
    ]


def test_sanitize_research_query_items_drops_validate_only_keys_outside_validate() -> None:
    query_items = [
        ("backtest_tab", "running"),
        ("backtest_job_id", "job-1"),
        ("id", "compare-1"),
        ("signal_id", "sig-1"),
    ]

    assert sanitize_research_query_items(
        query_items,
        selected_tab="discover",
        include_tab=False,
    ) == [
        ("signal_id", "sig-1"),
    ]


def test_sanitize_research_query_items_handles_empty_input() -> None:
    assert sanitize_research_query_items(
        [],
        selected_tab="validate",
        include_tab=True,
    ) == []

