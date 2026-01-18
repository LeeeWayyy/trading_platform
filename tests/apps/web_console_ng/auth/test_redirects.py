import pytest

from apps.web_console_ng.auth.redirects import ALLOWED_REDIRECT_PATHS, sanitize_redirect_path


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "//evil.example.com",
        "http://evil.example.com",
        "https://evil.example.com",
        "relative",
        "/not-allowed",
    ],
)
def test_sanitize_redirect_path_rejects_invalid(path):
    assert sanitize_redirect_path(path) == "/"


@pytest.mark.parametrize("path", sorted(ALLOWED_REDIRECT_PATHS))
def test_sanitize_redirect_path_allows_allowlist(path):
    assert sanitize_redirect_path(path) == path
