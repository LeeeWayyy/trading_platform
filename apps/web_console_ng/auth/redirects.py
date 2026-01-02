from __future__ import annotations

ALLOWED_REDIRECT_PATHS = {
    "/",
    "/manual",
    "/kill-switch",
    "/risk",
    "/backtest",
    "/admin",
    "/mfa-verify",
}


def sanitize_redirect_path(path: str | None) -> str:
    """Normalize redirect targets to internal allowlisted paths only."""
    if not path:
        return "/"
    if path.startswith("//"):
        return "/"
    if "://" in path:
        return "/"
    if not path.startswith("/"):
        return "/"
    if path not in ALLOWED_REDIRECT_PATHS:
        return "/"
    return path


__all__ = ["ALLOWED_REDIRECT_PATHS", "sanitize_redirect_path"]
