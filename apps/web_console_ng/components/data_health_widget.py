"""Data health dashboard widget (P6T12.4).

Renders a card showing per-source pipeline activity status with
auto-refresh via ``ui.timer``.
"""

from __future__ import annotations

from nicegui import ui

from libs.data.data_pipeline.health_monitor import (
    DataSourceHealth,
    HealthStatus,
    format_age,
)

# Status icon and colour mapping
_STATUS_CONFIG: dict[HealthStatus, dict[str, str]] = {
    HealthStatus.OK: {"icon": "check_circle", "color": "text-green-600", "bg": "bg-green-50"},
    HealthStatus.STALE: {"icon": "warning", "color": "text-amber-600", "bg": "bg-amber-50"},
    HealthStatus.ERROR: {"icon": "error", "color": "text-red-600", "bg": "bg-red-50"},
}


def render_data_health(sources: list[DataSourceHealth]) -> None:
    """Render the data health widget content.

    Args:
        sources: Health results from ``HealthMonitor.check_all()``.
    """
    if not sources:
        ui.label("No data sources registered").classes("text-gray-400 text-sm")
        return

    # Overall status badge
    has_error = any(s.status == HealthStatus.ERROR for s in sources)
    has_stale = any(s.status == HealthStatus.STALE for s in sources)

    if has_error:
        badge_text = "Issues Detected"
        badge_class = "bg-red-100 text-red-700"
    elif has_stale:
        badge_text = "Issues Detected"
        badge_class = "bg-amber-100 text-amber-700"
    else:
        badge_text = "All OK"
        badge_class = "bg-green-100 text-green-700"

    with ui.row().classes("items-center gap-2 mb-2"):
        ui.label("Pipeline Activity").classes("font-semibold text-sm")
        ui.badge(badge_text).classes(badge_class)

    # Per-source rows
    for src in sources:
        cfg = _STATUS_CONFIG.get(src.status, _STATUS_CONFIG[HealthStatus.ERROR])
        age_text = format_age(src.age_seconds) if src.age_seconds is not None else "N/A"

        with ui.row().classes(f"items-center gap-2 px-2 py-1 rounded {cfg['bg']} w-full"):
            ui.icon(cfg["icon"]).classes(f"{cfg['color']} text-lg")
            ui.label(src.name).classes("text-sm font-medium flex-1")
            ui.label(f"Last run: {age_text}").classes("text-xs text-gray-600")
            ui.label(src.status.value.upper()).classes(
                f"text-xs font-semibold {cfg['color']}"
            )


__all__ = ["render_data_health"]
