"""Readiness section for manifest-backed data workflows."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import format_datetime
from libs.web_console_services.schemas.data_management import DataReadinessDTO


def build_readiness_rows(readiness: DataReadinessDTO) -> list[dict[str, Any]]:
    """Build rows for compact readiness display."""
    return [
        {
            "status": check.status,
            "code": check.code,
            "source": check.source,
            "message": check.message,
            "action": check.action_label or "-",
            "target": check.target_section or "-",
        }
        for check in readiness.checks
    ]


def render_readiness_section(readiness_items: list[DataReadinessDTO]) -> None:
    """Render workflow readiness summaries."""
    if not readiness_items:
        return
    with ui.column().classes("w-full p-4 mb-4 border border-gray-200 rounded"):
        ui.label("Backtest Readiness").classes("text-lg font-bold")
        with ui.row().classes("gap-3 flex-wrap"):
            for readiness in readiness_items:
                with ui.card().classes("p-3 min-w-[240px]"):
                    ui.label(readiness.dataset).classes("font-bold")
                    ui.label(f"Workflow: {readiness.workflow}").classes("text-xs text-gray-500")
                    ui.label(f"Status: {readiness.status}").classes(
                        f"text-sm font-bold {_status_class(readiness.status)}"
                    )
                    if readiness.blockers:
                        ui.label("Blockers: " + ", ".join(readiness.blockers)).classes(
                            "text-xs text-red-700"
                        )
                    if readiness.warnings:
                        ui.label("Warnings: " + ", ".join(readiness.warnings)).classes(
                            "text-xs text-amber-700"
                        )
                    ui.label(f"Checked: {format_datetime(readiness.generated_at)}").classes(
                        "text-xs text-gray-500"
                    )

        for readiness in readiness_items:
            rows = build_readiness_rows(readiness)
            if not rows:
                continue
            ui.label(f"{readiness.dataset} checks").classes("font-bold mt-3")
            ui.table(
                columns=[
                    {"name": "status", "label": "Status", "field": "status"},
                    {"name": "code", "label": "Reason Code", "field": "code"},
                    {"name": "source", "label": "Source", "field": "source"},
                    {"name": "message", "label": "Message", "field": "message"},
                    {"name": "action", "label": "Action", "field": "action"},
                    {"name": "target", "label": "Target", "field": "target"},
                ],
                rows=rows,
                pagination={"rowsPerPage": 10},
            ).classes("w-full")


def _status_class(status: str) -> str:
    if status == "blocked":
        return "text-red-700"
    if status == "warning":
        return "text-amber-700"
    return "text-green-700"


__all__ = ["build_readiness_rows", "render_readiness_section"]
