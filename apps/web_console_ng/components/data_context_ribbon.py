"""Context ribbon for data manifest transparency."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import format_datetime
from apps.web_console_ng.components.data_operations_grid import build_manifest_grid_rows
from libs.web_console_services.data_manifest_service import AlpacaSipManifestSummaryDTO


def build_manifest_context_metrics(
    summary: AlpacaSipManifestSummaryDTO,
) -> list[dict[str, Any]]:
    """Build compact context metrics for the manifest transparency ribbon."""
    healthy = sum(
        1
        for manifest in summary.manifests
        if manifest.validation_status == "passed"
    )
    failed = sum(
        1
        for manifest in summary.manifests
        if manifest.validation_status != "passed"
    )
    issue_count = len(summary.warnings) + failed
    rows = build_manifest_grid_rows(summary)
    blocked_for_backtest = sum(
        1 for row in rows if str(row["readiness"]).startswith("blocked:")
    )
    latest_sync = summary.latest_sync
    oldest_sync = min(
        (manifest.sync_timestamp for manifest in summary.manifests),
        default=None,
    )
    return [
        {"label": "Healthy", "value": healthy, "tone": "positive"},
        {"label": "Missing", "value": len(summary.missing_datasets), "tone": "negative"},
        {
            "label": "Untrusted",
            "value": len(summary.missing_datasets) + failed,
            "tone": "warning",
        },
        {"label": "Backtest Blocked", "value": blocked_for_backtest, "tone": "warning"},
        {"label": "Issues", "value": issue_count, "tone": "negative" if issue_count else "positive"},
        {"label": "Rows", "value": f"{summary.row_count:,}", "tone": "neutral"},
        {"label": "Latest Manifest", "value": format_datetime(latest_sync), "tone": "neutral"},
        {"label": "Oldest Manifest", "value": format_datetime(oldest_sync), "tone": "neutral"},
    ]


def render_manifest_context_ribbon(summary: AlpacaSipManifestSummaryDTO) -> None:
    """Render top-level manifest state metrics."""
    metrics = build_manifest_context_metrics(summary)
    with ui.row().classes("w-full gap-2 flex-wrap"):
        for metric in metrics:
            tone_class = _tone_class(str(metric["tone"]))
            with ui.card().classes(f"px-3 py-2 min-w-[108px] border {tone_class}"):
                ui.label(str(metric["label"])).classes("text-xs text-gray-500")
                ui.label(str(metric["value"])).classes("text-base font-bold")


def _tone_class(tone: str) -> str:
    if tone == "positive":
        return "border-green-300"
    if tone == "negative":
        return "border-red-300"
    if tone == "warning":
        return "border-amber-300"
    return "border-gray-200"


__all__ = ["build_manifest_context_metrics", "render_manifest_context_ribbon"]
