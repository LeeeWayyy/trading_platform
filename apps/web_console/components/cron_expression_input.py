"""Cron expression input with preview rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st


def render_cron_expression_input(
    label: str,
    value: str,
    *,
    key: str,
    help_text: str | None = None,
    preview_count: int = 3,
) -> str:
    """Render a cron expression input with a preview of upcoming runs."""

    cron_expression = st.text_input(
        label,
        value=value,
        key=key,
        help=help_text or "Use standard 5-field cron format (min hour day month dow)",
    )

    preview = _preview_cron(cron_expression.strip(), preview_count)
    if preview is None:
        st.caption("Preview unavailable (croniter not installed).")
    elif isinstance(preview, str):
        st.caption(f"Preview error: {preview}")
    else:
        st.caption("Next runs (UTC): " + ", ".join(preview))

    return cron_expression.strip()


def _preview_cron(expression: str, count: int) -> list[str] | str | None:
    if not expression:
        return "Cron expression is required"

    try:
        from croniter import croniter  # type: ignore
    except ImportError:
        return None

    try:
        base = datetime.now(UTC)
        iterator = croniter(expression, base)
        preview = []
        for _ in range(count):
            next_time: Any = iterator.get_next(datetime)
            preview.append(next_time.strftime("%Y-%m-%d %H:%M"))
        return preview
    except (ValueError, KeyError) as exc:
        return str(exc)


__all__ = ["render_cron_expression_input"]
