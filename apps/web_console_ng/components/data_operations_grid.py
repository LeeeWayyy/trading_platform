"""Dense manifest-backed operations grid for the data page."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import format_datetime
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_MANIFEST_DATASETS,
    AlpacaSipManifestSummaryDTO,
    ManifestSummaryDTO,
)


def build_manifest_grid_rows(
    summary: AlpacaSipManifestSummaryDTO,
) -> list[dict[str, Any]]:
    """Build one dense operations-grid row per expected Alpaca SIP manifest."""
    by_dataset = {manifest.dataset: manifest for manifest in summary.manifests}
    return [
        _row_from_manifest_or_missing(dataset, by_dataset.get(dataset), summary)
        for dataset in ALPACA_SIP_MANIFEST_DATASETS
    ]


def render_manifest_operations_grid(rows: list[dict[str, Any]]) -> None:
    """Render the manifest operations grid."""
    columns: list[dict[str, Any]] = [
        {"name": "dataset", "label": "Dataset", "field": "dataset", "sortable": True},
        {"name": "raw_state", "label": "Raw", "field": "raw_state"},
        {"name": "local_state", "label": "Local", "field": "local_state", "sortable": True},
        {
            "name": "manifest_status",
            "label": "Manifest",
            "field": "manifest_status",
            "sortable": True,
        },
        {"name": "last_sync", "label": "Last Sync", "field": "last_sync", "sortable": True},
        {"name": "readiness", "label": "Readiness", "field": "readiness"},
        {"name": "issues", "label": "Issues", "field": "issues", "sortable": True},
        {"name": "row_count", "label": "Rows", "field": "row_count", "sortable": True},
        {"name": "date_range", "label": "Date Range", "field": "date_range"},
        {"name": "provider", "label": "Provider", "field": "provider"},
        {"name": "schema_version", "label": "Schema", "field": "schema_version"},
        {
            "name": "adjustment_state",
            "label": "Adjustment",
            "field": "adjustment_state",
        },
    ]
    ui.table(columns=columns, rows=rows, row_key="dataset").classes("w-full")


def _row_from_manifest_or_missing(
    dataset: str,
    manifest: ManifestSummaryDTO | None,
    summary: AlpacaSipManifestSummaryDTO,
) -> dict[str, Any]:
    warnings = _warnings_for_dataset(dataset, summary)
    is_daily = dataset == ALPACA_SIP_DAILY_DATASET
    raw_state = "Raw OHLC" if is_daily else "-"
    adjustment_state = (
        "adj_close: not available; ret: not available" if is_daily else "not price bars"
    )
    if manifest is None:
        readiness = "blocked: alpaca_sip_untrusted_without_manifest"
        if is_daily:
            readiness = f"{readiness}; raw_sip_returns_unavailable"
        return {
            "dataset": dataset,
            "family": "alpaca_sip",
            "raw_state": raw_state,
            "local_state": "missing",
            "manifest_status": "missing",
            "last_sync": "-",
            "readiness": readiness,
            "issues": len(warnings),
            "issue_codes": warnings,
            "row_count": 0,
            "date_range": "-",
            "provider": "alpaca_sip",
            "schema_version": "-",
            "manifest_id": None,
            "manifest_reference": None,
            "manifest_checksum": None,
            "adjustment_state": adjustment_state,
            "canonical_storage_mode": "raw" if is_daily else None,
            "read_time_adjustment_mode": "unavailable" if is_daily else None,
            "trusted_manifest_backed": False,
        }

    validation_ok = manifest.validation_status == "passed"
    issue_codes = list(warnings)
    if not validation_ok:
        issue_codes.append(f"manifest_validation_{manifest.validation_status}")

    if not validation_ok:
        readiness = "blocked: untrusted_manifest_validation_failed"
        if is_daily:
            readiness = f"{readiness}; raw_sip_returns_unavailable"
    elif is_daily:
        readiness = "blocked: raw_sip_returns_unavailable"
    elif warnings:
        readiness = "warn: companion manifest issue"
    else:
        readiness = "corporate actions only"

    return {
        "dataset": dataset,
        "family": "alpaca_sip",
        "raw_state": raw_state,
        "local_state": "present",
        "manifest_status": manifest.validation_status,
        "last_sync": format_datetime(manifest.sync_timestamp),
        "readiness": readiness,
        "issues": len(issue_codes),
        "issue_codes": sorted(set(issue_codes)),
        "row_count": manifest.row_count,
        "date_range": f"{manifest.start_date.isoformat()} to {manifest.end_date.isoformat()}",
        "provider": manifest.provider_id or "alpaca_sip",
        "schema_version": manifest.schema_version,
        "manifest_id": manifest.manifest_id,
        "manifest_reference": manifest.manifest_reference,
        "manifest_checksum": manifest.manifest_checksum,
        "adjustment_state": adjustment_state,
        "canonical_storage_mode": manifest.canonical_storage_mode,
        "read_time_adjustment_mode": manifest.read_time_adjustment_mode,
        "trusted_manifest_backed": validation_ok,
    }


def _warnings_for_dataset(
    dataset: str,
    summary: AlpacaSipManifestSummaryDTO,
) -> list[str]:
    warnings: list[str] = []
    for warning in summary.warnings:
        if warning == f"alpaca_sip_missing_manifest:{dataset}":
            warnings.append("alpaca_sip_untrusted_without_manifest")
        elif not warning.startswith("alpaca_sip_missing_manifest:") and dataset in {
            ALPACA_SIP_DAILY_DATASET,
            ALPACA_SIP_CORP_ACTIONS_DATASET,
        }:
            warnings.append(warning)
    if dataset == ALPACA_SIP_DAILY_DATASET:
        warnings.append("raw_sip_returns_unavailable")
    return sorted(set(warnings))


__all__ = ["build_manifest_grid_rows", "render_manifest_operations_grid"]
