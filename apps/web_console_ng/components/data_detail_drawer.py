"""Selected-row manifest detail surface for the data page."""

from __future__ import annotations

import json
from typing import Any

from nicegui import ui

from apps.web_console_ng.components.data_management_common import format_datetime
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
    ManifestSummaryDTO,
)


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
    readiness = _readiness_for_present_manifest(manifest, warnings)
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
            "value": _read_time_adjustment_mode(manifest.dataset),
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
        fields.extend(
            [
                {"field": "adj_close", "value": "not available"},
                {"field": "ret", "value": "not available"},
                {"field": "Backtest readiness", "value": readiness},
            ]
        )
    return fields


def _canonical_storage_mode(dataset: str) -> str:
    if dataset == ALPACA_SIP_DAILY_DATASET:
        return "raw"
    if dataset == ALPACA_SIP_CORP_ACTIONS_DATASET:
        return "not price bars"
    return "-"


def _read_time_adjustment_mode(dataset: str) -> str:
    if dataset == ALPACA_SIP_DAILY_DATASET:
        return "unavailable"
    if dataset == ALPACA_SIP_CORP_ACTIONS_DATASET:
        return "not applicable"
    return "-"


def _value(value: Any) -> str:
    return "-" if value is None else str(value)


def _readiness_for_present_manifest(
    manifest: ManifestSummaryDTO,
    warnings: list[str],
) -> str:
    is_daily = manifest.dataset == ALPACA_SIP_DAILY_DATASET
    if manifest.validation_status != "passed":
        readiness = "blocked: untrusted_manifest_validation_failed"
        if is_daily:
            readiness = f"{readiness}; raw_sip_returns_unavailable"
        return readiness
    if is_daily:
        return "blocked: raw_sip_returns_unavailable"
    if warnings:
        return "warn: companion manifest issue"
    return "corporate actions only"


__all__ = ["build_manifest_detail_fields", "render_manifest_detail_drawer"]
