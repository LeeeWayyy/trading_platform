"""SQL Explorer handoff prefill tests."""

from __future__ import annotations

from apps.web_console_ng.pages import sql_explorer as module


def test_resolve_sql_explorer_prefill_warns_when_query_has_no_dataset() -> None:
    initial_dataset, prefill_query, notifications = module._resolve_sql_explorer_prefill(
        allowed_datasets=["crsp"],
        requested_dataset=None,
        requested_query="SELECT 1",
    )

    assert initial_dataset == "crsp"
    assert prefill_query == ""
    assert notifications == [
        ("Linked query was not loaded because no dataset was provided", "warning")
    ]


def test_resolve_sql_explorer_prefill_warns_when_query_dataset_not_authorized() -> None:
    initial_dataset, prefill_query, notifications = module._resolve_sql_explorer_prefill(
        allowed_datasets=["crsp"],
        requested_dataset="taq",
        requested_query="SELECT * FROM taq_trades",
    )

    assert initial_dataset == "crsp"
    assert prefill_query == ""
    assert notifications == [
        (
            "Requested dataset is unavailable or not authorized; linked query was not loaded",
            "warning",
        )
    ]


def test_resolve_sql_explorer_prefill_loads_authorized_query() -> None:
    initial_dataset, prefill_query, notifications = module._resolve_sql_explorer_prefill(
        allowed_datasets=["crsp"],
        requested_dataset="crsp",
        requested_query="SELECT * FROM crsp_daily",
    )

    assert initial_dataset == "crsp"
    assert prefill_query == "SELECT * FROM crsp_daily"
    assert notifications == [
        ("Query editor prefilled from link; review before running", "info")
    ]


def test_resolve_sql_explorer_prefill_warns_when_query_truncated() -> None:
    long_query = "SELECT '" + ("x" * module._MAX_PREFILL_QUERY_CHARS) + "'"

    initial_dataset, prefill_query, notifications = module._resolve_sql_explorer_prefill(
        allowed_datasets=["crsp"],
        requested_dataset="crsp",
        requested_query=long_query,
    )

    assert initial_dataset == "crsp"
    assert prefill_query == long_query[: module._MAX_PREFILL_QUERY_CHARS]
    assert notifications == [
        ("Query editor prefilled from link; review before running", "info"),
        ("Linked query was truncated to 8,192 characters", "warning"),
    ]
