"""Shared authentication helper utilities."""

from __future__ import annotations

from typing import Any


def get_user_id(user: Any) -> str:
    """Extract user_id from an auth payload."""

    value = getattr(user, "user_id", None)
    if value:
        return str(value)
    if isinstance(user, dict):
        return str(user.get("user_id", "unknown"))
    return "unknown"


__all__ = ["get_user_id"]
