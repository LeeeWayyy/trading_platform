"""Shared helpers for data-management UI components."""

from __future__ import annotations

from typing import Any

TREND_DATASETS: tuple[str, ...] = (
    "crsp",
    "compustat",
    "taq",
    "fama_french",
    "alpaca_sip",
)


def format_datetime(dt: Any) -> str:
    """Format a datetime for display, handling None and non-datetime values."""
    if dt is not None and hasattr(dt, "isoformat"):
        return str(dt.isoformat())
    return "-"


def get_user_id_safe(user: Any) -> str | None:
    """Extract user_id from user dict or object safely."""
    if isinstance(user, dict):
        val = user.get("id")
        return str(val) if val is not None else None
    val = getattr(user, "id", None)
    return str(val) if val is not None else None


__all__ = ["TREND_DATASETS", "format_datetime", "get_user_id_safe"]
