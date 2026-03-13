"""Per-user audit log viewer component (T16.2)."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from libs.core.common.log_sanitizer import sanitize_dict


def render_user_activity_log(user_id: str, events: list[dict[str, Any]]) -> None:
    """Render filtered audit log events for a specific user.

    Args:
        user_id: The user whose activity to display.
        events: List of audit log row dicts (action, outcome, resource_type,
                resource_id, details, timestamp, user_id).
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl max-h-[80vh]"):
        ui.label(f"Activity Log: {user_id}").classes("text-lg font-bold mb-2")

        if not events:
            ui.label("No activity found").classes("text-gray-500")
        else:
            columns: list[dict[str, Any]] = [
                {"name": "time", "label": "Time", "field": "time", "sortable": True},
                {"name": "action", "label": "Action", "field": "action", "sortable": True},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "sortable": True},
                {"name": "actor", "label": "Actor", "field": "actor"},
                {"name": "resource", "label": "Resource", "field": "resource"},
                {"name": "details", "label": "Details", "field": "details"},
            ]

            table_rows = []
            for evt in events:
                created: Any = evt.get("timestamp") or evt.get("created_at")
                time_str = (
                    created.strftime("%Y-%m-%d %H:%M:%S")
                    if hasattr(created, "strftime")
                    else str(created or "-")
                )
                details_raw = evt.get("details")
                if isinstance(details_raw, dict):
                    details_raw = sanitize_dict(details_raw)
                details_str = str(details_raw) if details_raw else "-"
                # Truncate long details for display
                if len(details_str) > 120:
                    details_str = details_str[:117] + "..."
                table_rows.append(
                    {
                        "time": time_str,
                        "action": evt.get("action", "-"),
                        "outcome": evt.get("outcome", "-"),
                        "actor": evt.get("user_id", "-"),
                        "resource": f"{evt.get('resource_type', '')}/{evt.get('resource_id', '')}",
                        "details": details_str,
                    }
                )

            ui.table(
                columns=columns,
                rows=table_rows,
                row_key="time",
                pagination={"rowsPerPage": 20},
            ).classes("w-full")

        ui.button("Close", on_click=dialog.close).classes("mt-2")

    dialog.open()


__all__ = ["render_user_activity_log"]
