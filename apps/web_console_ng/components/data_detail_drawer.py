"""Selected-row manifest detail surface for the data page."""

from __future__ import annotations

import json
from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import (
    format_datetime,
    manifest_has_native_returns,
    summary_supports_split_adjustment,
)
from libs.data.data_pipeline.read_time_adjustment import (
    READ_TIME_ADJUSTMENT_AVAILABLE_REASON,
    READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED,
    READ_TIME_ADJUSTMENT_MODE_UNAVAILABLE,
)
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
    ManifestSummaryDTO,
)

_CANONICAL_STORAGE_MODES = {
    ALPACA_SIP_DAILY_DATASET: "raw",
    ALPACA_SIP_CORP_ACTIONS_DATASET: "not price bars",
}

_READ_TIME_ADJUSTMENT_MODES = {
    ALPACA_SIP_DAILY_DATASET: READ_TIME_ADJUSTMENT_MODE_UNAVAILABLE,
    ALPACA_SIP_CORP_ACTIONS_DATASET: "not applicable",
}


def build_manifest_detail_fields(
    summary: AlpacaSipManifestSummaryDTO,
    dataset: str,
) -> list[dict[str, str]]:
    """Build compact detail fields for a selected manifest row."""
    manifest = next(
        (item for item in summary.manifests if item.dataset == dataset),
        None,
    )
    if manifest is None:
        return _missing_detail_fields(summary, dataset)
    return _present_detail_fields(summary, manifest)


def render_manifest_detail_drawer(
    summary: AlpacaSipManifestSummaryDTO,
    dataset: str,
) -> None:
    """Render detail fields for a selected dataset."""
    fields = build_manifest_detail_fields(summary, dataset)
    columns = [
        {"name": "field", "label": "Field", "field": "field"},
        {"name": "value", "label": "Value", "field": "value"},
    ]
    ui.table(columns=columns, rows=fields).classes("w-full")


def _missing_detail_fields(
    summary: AlpacaSipManifestSummaryDTO,
    dataset: str,
) -> list[dict[str, str]]:
    blockers = ["alpaca_sip_untrusted_without_manifest"]
    if dataset == ALPACA_SIP_DAILY_DATASET:
        blockers.append("raw_sip_returns_unavailable")
    return [
        {"field": "Dataset", "value": dataset},
        {"field": "Local state", "value": "missing"},
        {"field": "Manifest status", "value": "missing"},
        {"field": "Trusted manifest-backed", "value": "false"},
        {"field": "Readiness", "value": "blocked"},
        {"field": "Reason codes", "value": ", ".join(blockers)},
        {"field": "Manifest provenance", "value": "unavailable"},
        {"field": "Provider signature", "value": "unavailable"},
        {"field": "Canonical storage mode", "value": _canonical_storage_mode(dataset)},
        {"field": "Read-time adjustment mode", "value": _read_time_adjustment_mode(dataset)},
        {"field": "Group status", "value": summary.sync_validation_status},
    ]


def _present_detail_fields(
    summary: AlpacaSipManifestSummaryDTO,
    manifest: ManifestSummaryDTO,
) -> list[dict[str, str]]:
    signature = manifest.provider_signature.model_dump(exclude_none=True)
    warnings = [
        warning
        for warning in summary.warnings
        if not warning.startswith("alpaca_sip_missing_manifest:")
    ]
    validation_ok = manifest.validation_status == "passed"
    split_adjustment_available = summary_supports_split_adjustment(summary)
    native_returns_available = manifest_has_native_returns(manifest)
    readiness = _readiness_for_present_manifest(
        manifest,
        warnings,
        split_adjustment_available=split_adjustment_available,
    )
    fields = [
        {"field": "Dataset", "value": manifest.dataset},
        {"field": "Local state", "value": "present"},
        {"field": "Manifest status", "value": manifest.validation_status},
        {"field": "Trusted manifest-backed", "value": str(validation_ok).lower()},
        {"field": "Readiness", "value": readiness},
        {"field": "Manifest ID", "value": _value(manifest.manifest_id)},
        {"field": "Manifest reference", "value": manifest.manifest_reference},
        {"field": "Manifest checksum", "value": manifest.manifest_checksum},
        {"field": "Manifest version", "value": str(manifest.manifest_version)},
        {"field": "Schema version", "value": manifest.schema_version},
        {"field": "Row count", "value": f"{manifest.row_count:,}"},
        {"field": "File count", "value": str(manifest.file_count)},
        {"field": "Date range", "value": f"{manifest.start_date} to {manifest.end_date}"},
        {"field": "Last sync", "value": format_datetime(manifest.sync_timestamp)},
        {"field": "Sync started", "value": format_datetime(manifest.sync_started_at)},
        {"field": "Sync finished", "value": format_datetime(manifest.sync_finished_at)},
        {"field": "Provider ID", "value": _value(manifest.provider_id)},
        {"field": "Provider version", "value": _value(manifest.provider_version)},
        {"field": "Source feed", "value": _value(manifest.source_feed)},
        {"field": "Adjustment mode", "value": _value(manifest.adjustment_mode)},
        {"field": "Canonical storage mode", "value": _canonical_storage_mode(manifest.dataset)},
        {
            "field": "Read-time adjustment mode",
            "value": _read_time_adjustment_mode_for_manifest(
                manifest,
                split_adjustment_available=split_adjustment_available,
            ),
        },
        {"field": "Symbol set hash", "value": _value(manifest.symbol_set_hash)},
        {"field": "Query/params hash", "value": _value(manifest.query_params_hash)},
        {"field": "Warnings", "value": ", ".join(warnings) if warnings else "-"},
        {
            "field": "Provider signature",
            "value": json.dumps(signature, default=str, sort_keys=True),
        },
    ]
    if manifest.dataset == ALPACA_SIP_DAILY_DATASET:
        derived_value = _daily_return_column_state(
            split_adjustment_available=split_adjustment_available,
            native_returns_available=native_returns_available,
        )
        fields.extend(
            [
                {"field": "adj_close", "value": derived_value},
                {"field": "ret", "value": derived_value},
                {
                    "field": "Backtest readiness",
                    "value": readiness,
                },
            ]
        )
    return fields


def _canonical_storage_mode(dataset: str) -> str:
    return _CANONICAL_STORAGE_MODES.get(dataset, "-")


def _read_time_adjustment_mode(dataset: str) -> str:
    return _READ_TIME_ADJUSTMENT_MODES.get(dataset, "-")


def _read_time_adjustment_mode_for_manifest(
    manifest: ManifestSummaryDTO,
    *,
    split_adjustment_available: bool,
) -> str:
    dataset = manifest.dataset
    if dataset == ALPACA_SIP_DAILY_DATASET and split_adjustment_available:
        return READ_TIME_ADJUSTMENT_MODE_SPLIT_ADJUSTED
    if dataset == ALPACA_SIP_DAILY_DATASET and manifest.read_time_adjustment_mode:
        return manifest.read_time_adjustment_mode
    return _read_time_adjustment_mode(dataset)


def _value(value: Any) -> str:
    return "-" if value is None else str(value)


def _readiness_for_present_manifest(
    manifest: ManifestSummaryDTO,
    warnings: list[str],
    *,
    split_adjustment_available: bool,
) -> str:
    is_daily = manifest.dataset == ALPACA_SIP_DAILY_DATASET
    if manifest.validation_status != "passed":
        readiness = "blocked: untrusted_manifest_validation_failed"
        if is_daily:
            readiness = f"{readiness}; raw_sip_returns_unavailable"
        return readiness
    if is_daily:
        if split_adjustment_available:
            return f"ready: {READ_TIME_ADJUSTMENT_AVAILABLE_REASON}"
        if manifest_has_native_returns(manifest):
            return "ready: native adjusted returns available"
        return "blocked: raw_sip_returns_unavailable"
    if warnings:
        return "warn: companion manifest issue"
    return "corporate actions only"


def _daily_return_column_state(
    *,
    split_adjustment_available: bool,
    native_returns_available: bool,
) -> str:
    if split_adjustment_available:
        return "derived split-adjusted"
    if native_returns_available:
        return "available"
    return "not available"


__all__ = ["build_manifest_detail_fields", "render_manifest_detail_drawer"]
