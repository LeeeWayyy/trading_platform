import pytest

from apps.web_console_ng.auth.redirects import (
    ALLOWED_REDIRECT_PATHS,
    LEGACY_REDIRECT_REMAP,
    LEGACY_TRADE_REDIRECT_QUERY_KEYS,
    legacy_trade_marker_from_redirect_path,
    normalize_legacy_trade_marker,
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


@pytest.mark.parametrize("path", ["/alpha-explorer", "/backtest", "/models"])
def test_legacy_compat_paths_remain_allowlisted(path):
    assert path in ALLOWED_REDIRECT_PATHS
    assert sanitize_redirect_path(path) == path


@pytest.mark.parametrize("path", sorted(LEGACY_REDIRECT_REMAP))
def test_legacy_trade_paths_remap_to_trade(path):
    assert path not in ALLOWED_REDIRECT_PATHS
    assert sanitize_redirect_path(path) == "/trade"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/manual-order/", "/trade"),
        ("/position-management/", "/trade"),
        ("/trade/", "/trade"),
        ("/admin/", "/admin"),
    ],
)
def test_sanitize_redirect_path_normalizes_trailing_slash(path, expected):
    assert sanitize_redirect_path(path) == expected


def test_sanitize_redirect_path_trade_query_preserves_allowlisted_keys():
    allowlisted_key = next(iter(LEGACY_TRADE_REDIRECT_QUERY_KEYS))
    path = f"/trade?{allowlisted_key}=x&drop=1"
    assert sanitize_redirect_path(path) == f"/trade?{allowlisted_key}=x"


def test_sanitize_redirect_path_legacy_query_is_remapped_and_filtered():
    path = "/manual-order?symbol=SPY&qty=5&drop=1"
    assert sanitize_redirect_path(path) == "/trade?symbol=SPY&qty=5"


def test_sanitize_redirect_path_non_trade_query_is_filtered_by_allowlist():
    assert sanitize_redirect_path("/admin?user_id=123&foo=bar") == "/admin?user_id=123"


def test_sanitize_redirect_path_backtest_query_preserves_known_context_keys():
    path = "/backtest?signal_id=s1&source=alpha_explorer&drop=1"
    assert sanitize_redirect_path(path) == "/backtest?signal_id=s1&source=alpha_explorer"


def test_sanitize_redirect_path_mfa_query_preserves_pending_and_next():
    path = "/mfa-verify?pending=mfa&next=/trade&drop=1"
    assert sanitize_redirect_path(path) == "/mfa-verify?pending=mfa&next=%2Ftrade"


def test_sanitize_redirect_path_mfa_query_with_trailing_slash_preserves_context() -> None:
    path = "/mfa-verify/?pending=mfa&next=/trade&drop=1"
    assert sanitize_redirect_path(path) == "/mfa-verify?pending=mfa&next=%2Ftrade"


def test_sanitize_redirect_path_with_root_path_allows_unprefixed_path():
    path = "/trade?symbol=SPY&drop=1"
    assert sanitize_redirect_path(path, root_path="/console") == "/trade?symbol=SPY"


@pytest.mark.parametrize(
    ("path", "root_path", "expected"),
    [
        ("/manual-order", None, "manual-order"),
        ("/manual-order?symbol=SPY", None, "manual-order"),
        ("/console/manual-order", "/console", "manual-order"),
        ("/position-management", None, "position-management"),
        ("/trade", None, None),
        ("/admin", None, None),
        ("https://evil.test/manual-order", None, None),
    ],
)
def test_legacy_trade_marker_from_redirect_path(path, root_path, expected):
    assert legacy_trade_marker_from_redirect_path(path, root_path=root_path) == expected


def test_normalize_legacy_trade_marker_accepts_leading_slash():
    assert normalize_legacy_trade_marker("/manual-order") == "manual-order"
