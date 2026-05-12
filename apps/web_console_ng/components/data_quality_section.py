"""Manifest-backed data quality section components."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import format_datetime
from libs.web_console_services.schemas.data_management import DataQualitySummaryDTO


def build_quality_signal_rows(summary: DataQualitySummaryDTO) -> list[dict[str, Any]]:
    """Build compact rows for quality-signal display."""
    return [
        {
            "check": signal.check,
            "status": signal.status,
            "source": signal.source,
            "observed_at": format_datetime(signal.observed_at),
            "message": signal.message,
            "reason_codes": ", ".join(signal.reason_codes) if signal.reason_codes else "-",
        }
        for signal in summary.signals
    ]


def render_quality_summary(summary: DataQualitySummaryDTO) -> None:
    """Render manifest/report-backed quality state."""
    with ui.column().classes("w-full p-4 mb-4 border border-gray-200 rounded"):
        ui.label("Alpaca SIP Quality Inputs").classes("text-lg font-bold")
        with ui.row().classes("gap-3 flex-wrap"):
            with ui.card().classes("p-3 min-w-[220px]"):
                ui.label("Overall").classes("text-xs text-gray-500")
                ui.label(summary.status).classes(
                    f"text-lg font-bold {_quality_status_class(summary.status)}"
                )
                ui.label(f"Generated: {format_datetime(summary.generated_at)}").classes(
                    "text-xs text-gray-500"
                )
            with ui.card().classes("p-3 min-w-[280px]"):
                ui.label("Acknowledgments").classes("text-xs text-gray-500")
                ack_state = "persistent" if summary.acknowledgments_persistent else "unavailable"
                ui.label(ack_state).classes(
                    f"text-lg font-bold {_quality_status_class(ack_state)}"
                )
                ui.label(summary.acknowledgment_status_source).classes("text-xs text-gray-500")

        rows = build_quality_signal_rows(summary)
        ui.table(
            columns=[
                {"name": "check", "label": "Check", "field": "check"},
                {"name": "status", "label": "Status", "field": "status"},
                {"name": "source", "label": "Source", "field": "source"},
                {"name": "observed_at", "label": "Observed", "field": "observed_at"},
                {"name": "message", "label": "Message", "field": "message"},
                {"name": "reason_codes", "label": "Reason Codes", "field": "reason_codes"},
            ],
            rows=rows,
            pagination={"rowsPerPage": 10},
        ).classes("w-full")


def _quality_status_class(status: str) -> str:
    if status == "failed":
        return "text-red-700"
    if status in {"warning", "unavailable"}:
        return "text-amber-700"
    return "text-green-700"


__all__ = ["build_quality_signal_rows", "render_quality_summary"]
