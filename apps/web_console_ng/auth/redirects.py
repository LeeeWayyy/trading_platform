from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

from apps.web_console_ng.research_query import (
    RESEARCH_ALLOWED_QUERY_KEYS,
    sanitize_research_query_items,
    normalize_research_tab,
)

ALLOWED_REDIRECT_PATHS = {
    "/",
    "/trade",
    "/research",
    "/circuit-breaker",
    "/risk",
    "/admin",
    "/tax-lots",
    "/mfa-verify",
}
# Retired legacy routes intentionally fail closed to "/" rather than remapping.
# This prevents stale bookmarks from silently mutating state in the new IA.

TRADE_REDIRECT_QUERY_KEYS = frozenset(
    {
        "symbol",
        "side",
        "qty",
        "price",
        "order_type",
        "tif",
        "strategy_id",
    }
)
ALLOWED_REDIRECT_QUERY_KEYS_BY_PATH: dict[str, frozenset[str]] = {
    "/trade": TRADE_REDIRECT_QUERY_KEYS,
    "/admin": frozenset({"user_id", "tab", "view"}),
    "/research": RESEARCH_ALLOWED_QUERY_KEYS,
    "/risk": frozenset({"symbol", "tab", "view"}),
    "/tax-lots": frozenset({"symbol", "tab", "view"}),
    "/circuit-breaker": frozenset({"tab", "view"}),
    # The nested ``next`` value is only a transport parameter for MFA handoff.
    # It must be re-sanitized by the MFA page before any redirect action.
    "/mfa-verify": frozenset({"pending", "next"}),
}

def normalize_root_path(root_path: str | None) -> str:
    """Normalize optional ASGI root_path to '/prefix' form."""
    if not root_path:
        return ""
    normalized = str(root_path).strip()
    if not normalized or normalized == "/":
        return ""
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized.rstrip("/")


def with_root_path(path: str, *, root_path: str | None) -> str:
    """Prefix an app-relative path with root_path when present."""
    normalized_path = path if path.startswith("/") else f"/{path}"
    normalized_root = normalize_root_path(root_path)
    if not normalized_root:
        return normalized_path
    if normalized_path == "/":
        return normalized_root
    return f"{normalized_root}{normalized_path}"


def with_root_path_once(path: str, *, root_path: str | None) -> str:
    """Prefix root_path only when path is not already rooted."""
    normalized_path = path if path.startswith("/") else f"/{path}"
    normalized_root = normalize_root_path(root_path)
    if not normalized_root:
        return normalized_path
    if normalized_path == normalized_root or normalized_path.startswith(f"{normalized_root}/"):
        return normalized_path
    return with_root_path(normalized_path, root_path=root_path)


def trade_workspace_path(*, root_path: str | None) -> str:
    """Build canonical trade workspace URL with optional root_path prefix."""
    return with_root_path("/trade", root_path=root_path)


def strip_root_path(path: str, *, root_path: str | None) -> str:
    """Strip configured ASGI root_path from a request path."""
    normalized_root = normalize_root_path(root_path)
    if not normalized_root:
        return path
    if path == normalized_root:
        return "/"
    root_prefix = f"{normalized_root}/"
    if path.startswith(root_prefix):
        stripped = path[len(normalized_root) :]
        return stripped or "/"
    return path


def sanitize_redirect_path(path: str | None, *, root_path: str | None = None) -> str:
    """Normalize redirect targets to internal allowlisted paths only.

    Canonicalization rules:
    - strips configured root_path when present
    - normalizes trailing slash
    - discards fragments
    - filters query params by per-path allowlist
    """
    if not path:
        return "/"
    if path.startswith("//"):
        return "/"
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not parsed.path.startswith("/"):
        return "/"

    raw_path = strip_root_path(parsed.path, root_path=root_path)
    normalized_path = raw_path.rstrip("/") or "/"
    target_path = normalized_path
    if target_path not in ALLOWED_REDIRECT_PATHS:
        return "/"
    parsed_items = parse_qsl(parsed.query, keep_blank_values=False)
    allowed_query_keys = ALLOWED_REDIRECT_QUERY_KEYS_BY_PATH.get(target_path)
    if not parsed_items:
        return target_path
    if not allowed_query_keys:
        return target_path

    query_items: list[tuple[str, str]] = [
        (key, value) for key, value in parsed_items if key in allowed_query_keys
    ]
    if target_path == "/research":
        selected_tab: str | None = None
        for key, value in query_items:
            if key != "tab":
                continue
            normalized_tab = normalize_research_tab(value, default=None)
            if normalized_tab is not None:
                selected_tab = normalized_tab
                break
        query_items = sanitize_research_query_items(
            query_items,
            selected_tab=selected_tab,
            include_tab=True,
        )
    if not query_items:
        return target_path
    return target_path + "?" + urlencode(query_items, doseq=True)


__all__ = [
    "ALLOWED_REDIRECT_PATHS",
    "ALLOWED_REDIRECT_QUERY_KEYS_BY_PATH",
    "TRADE_REDIRECT_QUERY_KEYS",
    "normalize_root_path",
    "sanitize_redirect_path",
    "strip_root_path",
    "trade_workspace_path",
    "with_root_path_once",
    "with_root_path",
]
