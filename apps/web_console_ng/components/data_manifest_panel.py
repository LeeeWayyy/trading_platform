"""Manifest transparency panel for the data-management page."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_context_ribbon import (
    render_manifest_context_ribbon,
)
from apps.web_console_ng.components.data_detail_drawer import (
    render_manifest_detail_drawer,
)
from apps.web_console_ng.components.data_operations_grid import (
    build_manifest_grid_rows,
    render_manifest_operations_grid,
)
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
)


def render_manifest_transparency_panel(
    summary: AlpacaSipManifestSummaryDTO,
) -> None:
    """Render Phase 1 manifest transparency UI for Alpaca SIP."""
    rows = build_manifest_grid_rows(summary)
    selected_dataset = _default_selected_dataset(rows)

    with ui.column().classes("w-full p-4 mb-4 border border-gray-200 rounded"):
        with ui.row().classes("w-full items-center justify-between gap-4"):
            ui.label("Manifest Transparency").classes("text-lg font-bold")
            ui.label(_group_status_text(summary)).classes("text-sm text-gray-600")

        render_manifest_context_ribbon(summary)

        if summary.warnings:
            with ui.row().classes("w-full gap-2 mt-3 flex-wrap"):
                for warning in summary.warnings:
                    ui.label(warning).classes(
                        "text-xs bg-amber-100 text-amber-800 px-2 py-1 rounded"
                    )

        with ui.row().classes("w-full gap-4 mt-4 items-start flex-wrap"):
            with ui.column().classes("flex-[2_1_760px] min-w-0 overflow-x-auto"):
                render_manifest_operations_grid(rows)
            with ui.column().classes("flex-[1_1_360px] min-w-[280px]"):
                ui.label("Selected Manifest").classes("font-bold mb-2")
                dataset_select = ui.select(
                    label="Dataset",
                    options=[row["dataset"] for row in rows],
                    value=selected_dataset,
                ).classes("w-full mb-2")
                detail_container = ui.column().classes("w-full")

                def render_details(dataset: str | None) -> None:
                    detail_container.clear()
                    with detail_container:
                        render_manifest_detail_drawer(
                            summary,
                            str(dataset or selected_dataset),
                        )

                dataset_select.on_value_change(lambda event: render_details(event.value))
                render_details(selected_dataset)


def _default_selected_dataset(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        if row["dataset"] == ALPACA_SIP_DAILY_DATASET:
            return str(row["dataset"])
    return str(rows[0]["dataset"]) if rows else ALPACA_SIP_DAILY_DATASET


def _group_status_text(summary: AlpacaSipManifestSummaryDTO) -> str:
    if not summary.has_any_manifest:
        return "Alpaca SIP manifests missing"
    if summary.missing_datasets:
        return f"Missing companion: {', '.join(summary.missing_datasets)}"
    if summary.source_status == "ok":
        return "Alpaca SIP manifests present"
    return summary.source_error_message or summary.sync_validation_status


__all__ = ["render_manifest_transparency_panel"]
