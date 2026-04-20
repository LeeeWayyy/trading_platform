import pytest

from apps.web_console_ng.auth.redirects import (
    ALLOWED_REDIRECT_PATHS,
    TRADE_REDIRECT_QUERY_KEYS,
    sanitize_redirect_path,
)


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "//evil.example.com",
        "http://evil.example.com",
        "https://evil.example.com",
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "vbscript:alert(1)",
        "relative",
        "/not-allowed",
    ],
)
def test_sanitize_redirect_path_rejects_invalid(path):
    assert sanitize_redirect_path(path) == "/"


@pytest.mark.parametrize("path", sorted(ALLOWED_REDIRECT_PATHS))
def test_sanitize_redirect_path_allows_allowlist(path):
    assert sanitize_redirect_path(path) == path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/trade/", "/trade"),
        ("/admin/", "/admin"),
    ],
)
def test_sanitize_redirect_path_normalizes_trailing_slash(path, expected):
    assert sanitize_redirect_path(path) == expected


def test_sanitize_redirect_path_trade_query_preserves_allowlisted_keys():
    allowlisted_key = next(iter(TRADE_REDIRECT_QUERY_KEYS))
    path = f"/trade?{allowlisted_key}=x&drop=1"
    assert sanitize_redirect_path(path) == f"/trade?{allowlisted_key}=x"


def test_sanitize_redirect_path_non_trade_query_is_filtered_by_allowlist():
    assert sanitize_redirect_path("/admin?user_id=123&foo=bar") == "/admin?user_id=123"


def test_sanitize_redirect_path_mfa_query_preserves_pending_and_next():
    path = "/mfa-verify?pending=mfa&next=/trade&drop=1"
    assert sanitize_redirect_path(path) == "/mfa-verify?pending=mfa&next=%2Ftrade"


def test_sanitize_redirect_path_mfa_query_with_trailing_slash_preserves_context() -> None:
    path = "/mfa-verify/?pending=mfa&next=/trade&drop=1"
    assert sanitize_redirect_path(path) == "/mfa-verify?pending=mfa&next=%2Ftrade"


def test_sanitize_redirect_path_with_root_path_allows_unprefixed_path():
    path = "/trade?symbol=SPY&drop=1"
    assert sanitize_redirect_path(path, root_path="/console") == "/trade?symbol=SPY"


def test_sanitize_redirect_path_with_root_path_sanitizes_research_query() -> None:
    path = "/console/research?tab=validate&backtest_tab=compare&drop=1"
    assert (
        sanitize_redirect_path(path, root_path="/console")
        == "/research?tab=validate&backtest_tab=results"
    )


@pytest.mark.parametrize(
    "legacy_path",
    [
        "/manual-order",
        "/position-management",
        "/alpha-explorer",
        "/alpha-explorer?signal_id=sig-1",
        "/backtest",
        "/models",
    ],
)
def test_sanitize_redirect_path_rejects_retired_legacy_routes(legacy_path: str) -> None:
    assert sanitize_redirect_path(legacy_path) == "/"


def test_allowlist_excludes_retired_legacy_routes() -> None:
    assert "/manual-order" not in ALLOWED_REDIRECT_PATHS
    assert "/position-management" not in ALLOWED_REDIRECT_PATHS
    assert "/alpha-explorer" not in ALLOWED_REDIRECT_PATHS
    assert "/backtest" not in ALLOWED_REDIRECT_PATHS
    assert "/models" not in ALLOWED_REDIRECT_PATHS


def test_sanitize_redirect_path_research_query_keeps_backtest_context() -> None:
    path = "/research?tab=validate&backtest_tab=running&backtest_job_id=job-1&id=abc&view=summary&drop=1"
    assert (
        sanitize_redirect_path(path)
        == "/research?tab=validate&backtest_tab=running&backtest_job_id=job-1&id=abc&view=summary"
    )


def test_sanitize_redirect_path_research_drops_backtest_tab_without_validate() -> None:
    path = "/research?backtest_tab=running&signal_id=sig-1&source=alpha_explorer"
    assert sanitize_redirect_path(path) == "/research?signal_id=sig-1&source=alpha_explorer"


def test_sanitize_redirect_path_research_drops_backtest_tab_for_discover_tab() -> None:
    path = "/research?tab=discover&backtest_tab=running&signal_id=sig-1"
    assert sanitize_redirect_path(path) == "/research?tab=discover&signal_id=sig-1"


def test_sanitize_redirect_path_research_drops_validate_only_params_for_discover_tab() -> None:
    path = (
        "/research?tab=discover&backtest_tab=running&backtest_job_id=job-1&id=res-1&signal_id=sig-1"
    )
    assert sanitize_redirect_path(path) == "/research?tab=discover&signal_id=sig-1"


def test_sanitize_redirect_path_research_drops_validate_only_params_without_tab() -> None:
    path = "/research?backtest_job_id=job-1&id=res-1&signal_id=sig-1"
    assert sanitize_redirect_path(path) == "/research?signal_id=sig-1"


def test_sanitize_redirect_path_research_normalizes_compare_alias_for_validate() -> None:
    path = "/research?tab=validate&backtest_tab=compare&signal_id=sig-1"
    assert sanitize_redirect_path(path) == "/research?tab=validate&backtest_tab=results&signal_id=sig-1"


def test_sanitize_redirect_path_research_invalid_backtest_tab_is_dropped() -> None:
    path = "/research?tab=validate&backtest_tab=unknown&signal_id=sig-1"
    assert sanitize_redirect_path(path) == "/research?tab=validate&signal_id=sig-1"


def test_sanitize_redirect_path_research_uses_first_valid_tab_when_multiple_present() -> None:
    path = "/research?tab=unknown&tab=validate&backtest_tab=running&signal_id=sig-1"
    assert sanitize_redirect_path(path) == "/research?tab=validate&backtest_tab=running&signal_id=sig-1"


def test_sanitize_redirect_path_research_drops_invalid_tab_values() -> None:
    path = "/research?tab=unknown&signal_id=sig-1"
    assert sanitize_redirect_path(path) == "/research?signal_id=sig-1"
