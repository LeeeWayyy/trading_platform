from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

from starlette.responses import Response

logger = logging.getLogger(__name__)

ALLOWED_REDIRECT_PATHS = {
    "/",
    "/trade",
    "/research",
    "/alpha-explorer",
    "/backtest",
    "/models",
    "/circuit-breaker",
    "/risk",
    "/admin",
    "/tax-lots",
    "/mfa-verify",
}

LEGACY_REDIRECT_REMAP = {
    "/manual-order": "/trade",
    "/position-management": "/trade",
}

LEGACY_TRADE_REDIRECT_QUERY_KEYS = frozenset(
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
    "/trade": LEGACY_TRADE_REDIRECT_QUERY_KEYS,
    "/admin": frozenset({"user_id", "tab", "view"}),
    "/alpha-explorer": frozenset({"signal_id", "source", "strategy_id", "tab", "view"}),
    "/backtest": frozenset({"id", "signal_id", "source", "backtest_job_id", "tab", "view"}),
    "/models": frozenset({"id", "model_id", "strategy_id", "tab", "view"}),
    "/research": frozenset({"tab", "signal_id", "source", "model_id", "strategy_id"}),
    "/risk": frozenset({"symbol", "tab", "view"}),
    "/tax-lots": frozenset({"symbol", "tab", "view"}),
    "/circuit-breaker": frozenset({"tab", "view"}),
    # The nested ``next`` value is only a transport parameter for MFA handoff.
    # It must be re-sanitized by the MFA page before any redirect action.
    "/mfa-verify": frozenset({"pending", "next"}),
}

LEGACY_TRADE_MARKER_COOKIE_NAME = "legacy_trade_from"
LEGACY_TRADE_MARKER_MAX_AGE_SECONDS = 600
LEGACY_TRADE_ALLOWED_MARKERS = frozenset(path.lstrip("/") for path in LEGACY_REDIRECT_REMAP)

CookieSameSite = Literal["lax", "strict", "none"]


def legacy_cookie_security_attrs(cookie_flags: Mapping[str, object]) -> tuple[bool, CookieSameSite]:
    """Normalize cookie security fields used by legacy marker cookies."""
    secure = bool(cookie_flags.get("secure", False))
    samesite_raw = str(cookie_flags.get("samesite", "lax")).lower()
    if samesite_raw not in {"lax", "strict", "none"}:
        logger.warning("invalid_legacy_cookie_samesite_coerced_to_lax: %s", samesite_raw)
        samesite_raw = "lax"
    return secure, cast(CookieSameSite, samesite_raw)


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


def legacy_trade_marker_cookie_path(*, root_path: str | None) -> str:
    """Scope legacy marker cookie to app root so '/' and '/trade' aliases both receive it."""
    return normalize_root_path(root_path) or "/"


def normalize_legacy_trade_marker(marker: str | None) -> str | None:
    """Return allowlisted legacy marker value or None."""
    normalized = str(marker or "").strip().lower().lstrip("/")
    return normalized if normalized in LEGACY_TRADE_ALLOWED_MARKERS else None


def legacy_trade_marker_from_redirect_path(
    path: str | None, *, root_path: str | None = None
) -> str | None:
    """Extract legacy marker from a redirect path when it remaps to /trade."""
    if not path:
        return None
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith("/"):
        return None
    raw_path = strip_root_path(parsed.path, root_path=root_path)
    normalized_path = raw_path.rstrip("/") or "/"
    if normalized_path not in LEGACY_REDIRECT_REMAP:
        return None
    return normalize_legacy_trade_marker(normalized_path.lstrip("/"))


def set_legacy_trade_marker_cookie(
    response: Response,
    *,
    marker: str | None,
    root_path: str | None,
    secure: bool,
    samesite: CookieSameSite,
) -> None:
    """Set legacy trade marker cookie when marker is allowlisted."""
    normalized_marker = normalize_legacy_trade_marker(marker)
    if normalized_marker is None:
        return
    response.set_cookie(
        key=LEGACY_TRADE_MARKER_COOKIE_NAME,
        value=normalized_marker,
        path=legacy_trade_marker_cookie_path(root_path=root_path),
        max_age=LEGACY_TRADE_MARKER_MAX_AGE_SECONDS,
        secure=secure,
        httponly=True,
        samesite=samesite,
    )


def clear_legacy_trade_marker_cookie(
    response: Response,
    *,
    root_path: str | None,
    secure: bool,
    samesite: CookieSameSite,
) -> None:
    """Expire legacy trade marker cookie scoped to the trade workspace."""
    response.delete_cookie(
        key=LEGACY_TRADE_MARKER_COOKIE_NAME,
        path=legacy_trade_marker_cookie_path(root_path=root_path),
        secure=secure,
        httponly=True,
        samesite=samesite,
    )


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
    if normalized_path in LEGACY_REDIRECT_REMAP:
        target_path = LEGACY_REDIRECT_REMAP[normalized_path]
    else:
        target_path = normalized_path

    if target_path not in ALLOWED_REDIRECT_PATHS:
        return "/"
    allowed_query_keys = ALLOWED_REDIRECT_QUERY_KEYS_BY_PATH.get(target_path)
    parsed_items = parse_qsl(parsed.query, keep_blank_values=False)
    if not parsed.query or not parsed_items:
        return target_path
    if not allowed_query_keys:
        return target_path

    query_items: list[tuple[str, str]] = [
        (key, value) for key, value in parsed_items if key in allowed_query_keys
    ]
    if not query_items:
        return target_path
    return target_path + "?" + urlencode(query_items, doseq=True)


__all__ = [
    "ALLOWED_REDIRECT_PATHS",
    "LEGACY_TRADE_MARKER_COOKIE_NAME",
    "LEGACY_TRADE_MARKER_MAX_AGE_SECONDS",
    "ALLOWED_REDIRECT_QUERY_KEYS_BY_PATH",
    "LEGACY_REDIRECT_REMAP",
    "LEGACY_TRADE_ALLOWED_MARKERS",
    "LEGACY_TRADE_REDIRECT_QUERY_KEYS",
    "clear_legacy_trade_marker_cookie",
    "legacy_trade_marker_from_redirect_path",
    "legacy_trade_marker_cookie_path",
    "normalize_legacy_trade_marker",
    "normalize_root_path",
    "sanitize_redirect_path",
    "set_legacy_trade_marker_cookie",
    "strip_root_path",
    "trade_workspace_path",
    "with_root_path_once",
    "with_root_path",
]
