"""Context ribbon for data manifest transparency."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import format_datetime
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_MANIFEST_DATASETS,
    AlpacaSipManifestSummaryDTO,
    ManifestSummaryDTO,
)


def build_manifest_context_metrics(
    summary: AlpacaSipManifestSummaryDTO,
) -> list[dict[str, Any]]:
    """Build compact context metrics for the manifest transparency ribbon."""
    healthy = sum(1 for manifest in summary.manifests if manifest.validation_status == "passed")
    failed = sum(1 for manifest in summary.manifests if manifest.validation_status != "passed")
    issue_count = len(summary.warnings) + failed
    blocked_for_backtest = _count_backtest_blocked_rows(summary)
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
        {
            "label": "Issues",
            "value": issue_count,
            "tone": "negative" if issue_count else "positive",
        },
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
    tone_map = {
        "positive": "border-green-300",
        "negative": "border-red-300",
        "warning": "border-amber-300",
    }
    return tone_map.get(tone, "border-gray-200")


def _count_backtest_blocked_rows(summary: AlpacaSipManifestSummaryDTO) -> int:
    manifests_by_dataset = {manifest.dataset: manifest for manifest in summary.manifests}
    return sum(
        int(_dataset_blocks_backtest(dataset, manifests_by_dataset.get(dataset)))
        for dataset in ALPACA_SIP_MANIFEST_DATASETS
    )


def _dataset_blocks_backtest(
    dataset: str,
    manifest: ManifestSummaryDTO | None,
) -> bool:
    if manifest is None or manifest.validation_status != "passed":
        return True
    if dataset == ALPACA_SIP_DAILY_DATASET:
        return manifest.read_time_adjustment_mode != "available"
    return False


__all__ = ["build_manifest_context_metrics", "render_manifest_context_ribbon"]
