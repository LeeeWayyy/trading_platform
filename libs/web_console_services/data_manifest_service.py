"""Manifest summary read model for data-management UI services."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel

from libs.data.data_quality.manifest import ManifestManager, SyncManifest
from libs.web_console_services.provider_signature import (
    ProviderSignatureDTO,
    sanitize_provider_signature,
)
from libs.web_console_services.schemas.data_management import SyncStatusDTO

ALPACA_SIP_DATASET_KEY = "alpaca_sip"
ALPACA_SIP_DAILY_DATASET = "alpaca_sip_daily"
ALPACA_SIP_CORP_ACTIONS_DATASET = "alpaca_sip_corp_actions"
ALPACA_SIP_MANIFEST_DATASETS: tuple[str, ...] = (
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_CORP_ACTIONS_DATASET,
)

_ALPACA_SIP_COMPANION_MAX_END_DATE_DRIFT_DAYS = 1
_DEFAULT_DATA_ROOT = Path(os.getenv("DATA_ROOT", "data")).resolve()


class ManifestSummaryDTO(BaseModel):
    """UI-facing summary of one sync manifest."""

    dataset: str
    manifest_reference: str
    manifest_id: str | None = None
    manifest_checksum: str
    manifest_version: int
    schema_version: str
    validation_status: str
    sync_timestamp: AwareDatetime
    sync_started_at: AwareDatetime | None = None
    sync_finished_at: AwareDatetime | None = None
    start_date: date
    end_date: date
    row_count: int
    file_count: int
    provider_id: str | None = None
    provider_version: str | None = None
    source_feed: str | None = None
    adjustment_mode: str | None = None
    canonical_storage_mode: str | None = None
    read_time_adjustment_mode: str | None = None
    symbol_set_hash: str | None = None
    query_params_hash: str | None = None
    provider_signature: ProviderSignatureDTO


class AlpacaSipManifestSummaryDTO(BaseModel):
    """Grouped Alpaca SIP manifest state for summary surfaces."""

    dataset: str = ALPACA_SIP_DATASET_KEY
    manifests: list[ManifestSummaryDTO]
    present_datasets: list[str]
    missing_datasets: list[str]
    latest_sync: AwareDatetime | None = None
    row_count: int
    schema_versions: list[str]
    validation_statuses: list[str]
    sync_validation_status: str
    source_status: str
    source_error_message: str | None = None
    source_error_rate_pct: float
    warnings: list[str]

    @property
    def has_any_manifest(self) -> bool:
        """Whether at least one expected Alpaca SIP manifest exists."""
        return bool(self.manifests)

    def to_sync_status(self) -> SyncStatusDTO:
        """Convert the group summary to the legacy sync-status DTO."""
        return SyncStatusDTO(
            dataset=self.dataset,
            last_sync=self.latest_sync,
            row_count=self.row_count,
            validation_status=self.sync_validation_status,
            schema_version=",".join(self.schema_versions) or None,
        )

    def apply_to_source_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Return a source-status spec updated from manifest state."""
        if not self.has_any_manifest:
            return spec

        now = datetime.now(UTC)
        latest_sync = self.latest_sync
        age_seconds = (
            max(0.0, (now - latest_sync).total_seconds()) if latest_sync is not None else 0.0
        )

        updated = dict(spec)
        updated["status"] = self.source_status
        updated["minutes_ago"] = max(0, int(age_seconds // 60))
        updated["row_count"] = self.row_count
        updated["error_rate_pct"] = self.source_error_rate_pct
        updated["error_message"] = self.source_error_message
        return updated


class DataManifestService:
    """Read-only manifest summary service.

    The default constructor uses the process-level ``DATA_ROOT`` captured at
    import time. Tests and alternate roots should pass ``data_root`` explicitly.
    """

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        manifest_manager: ManifestManager | None = None,
    ) -> None:
        self._data_root = data_root.resolve() if data_root is not None else _DEFAULT_DATA_ROOT
        self._manifest_manager = manifest_manager

    def get_alpaca_sip_summary(self) -> AlpacaSipManifestSummaryDTO:
        """Return grouped status for Alpaca SIP daily and corporate-actions manifests."""
        summaries = [
            summary
            for dataset in ALPACA_SIP_MANIFEST_DATASETS
            if (summary := self.get_manifest_summary(dataset)) is not None
        ]
        present = sorted(summary.dataset for summary in summaries)
        missing = sorted(set(ALPACA_SIP_MANIFEST_DATASETS) - set(present))
        validation_statuses = sorted({summary.validation_status for summary in summaries})
        schema_versions = sorted({summary.schema_version for summary in summaries})
        latest_sync = max((summary.sync_timestamp for summary in summaries), default=None)

        sync_validation_status = self._build_sync_validation_status(missing, validation_statuses)
        source_status, error_message = self._build_source_status(missing, validation_statuses)
        warnings = self._build_alpaca_sip_warnings(summaries, missing)

        return AlpacaSipManifestSummaryDTO(
            manifests=summaries,
            present_datasets=present,
            missing_datasets=missing,
            latest_sync=latest_sync,
            row_count=sum(summary.row_count for summary in summaries),
            schema_versions=schema_versions,
            validation_statuses=validation_statuses,
            sync_validation_status=sync_validation_status,
            source_status=source_status,
            source_error_message=error_message,
            source_error_rate_pct=0.0 if source_status == "ok" else 100.0,
            warnings=warnings,
        )

    def get_manifest_summary(self, dataset: str) -> ManifestSummaryDTO | None:
        """Load and summarize one manifest, returning ``None`` when absent."""
        manager = self._manager()
        manifest = manager.load_manifest(dataset)
        if manifest is None:
            return None
        manifest_reference = f"manifests://{dataset}.json"
        return self._summarize_manifest(manifest, manifest_reference)

    def _manager(self) -> ManifestManager:
        if self._manifest_manager is not None:
            return self._manifest_manager

        data_root = self._data_root
        return ManifestManager(
            storage_path=data_root / "manifests",
            lock_dir=data_root / "locks",
            data_root=data_root,
        )

    def _summarize_manifest(
        self,
        manifest: SyncManifest,
        manifest_reference: str,
    ) -> ManifestSummaryDTO:
        canonical_storage_mode = None
        read_time_adjustment_mode = None
        if manifest.dataset == ALPACA_SIP_DAILY_DATASET:
            canonical_storage_mode = "raw"
            read_time_adjustment_mode = "unavailable"

        provider_signature = sanitize_provider_signature(
            {
                "provider_id": manifest.provider_id,
                "provider_version": manifest.provider_version,
                "source_feed": manifest.source_feed,
                "adjustment_mode": manifest.adjustment_mode,
                "canonical_storage_mode": canonical_storage_mode,
                "read_time_adjustment_mode": read_time_adjustment_mode,
                "symbol_set_hash": manifest.symbol_set_hash,
                "query_params_hash": manifest.wrds_query_hash,
                "manifest_id": manifest.manifest_id,
                "manifest_reference": manifest_reference,
                "manifest_checksum": manifest.checksum,
                "manifest_version": str(manifest.manifest_version),
                "schema_version": manifest.schema_version,
                "sync_started_at": manifest.sync_started_at,
                "sync_finished_at": manifest.sync_finished_at,
                "data_roles": _data_roles_for_dataset(manifest.dataset),
                "dataset_keys": [manifest.dataset],
            }
        )

        return ManifestSummaryDTO(
            dataset=manifest.dataset,
            manifest_reference=manifest_reference,
            manifest_id=manifest.manifest_id,
            manifest_checksum=manifest.checksum,
            manifest_version=manifest.manifest_version,
            schema_version=manifest.schema_version,
            validation_status=manifest.validation_status,
            sync_timestamp=manifest.sync_timestamp,
            sync_started_at=manifest.sync_started_at,
            sync_finished_at=manifest.sync_finished_at,
            start_date=manifest.start_date,
            end_date=manifest.end_date,
            row_count=manifest.row_count,
            file_count=len(manifest.file_paths),
            provider_id=manifest.provider_id,
            provider_version=manifest.provider_version,
            source_feed=manifest.source_feed,
            adjustment_mode=manifest.adjustment_mode,
            canonical_storage_mode=canonical_storage_mode,
            read_time_adjustment_mode=read_time_adjustment_mode,
            symbol_set_hash=manifest.symbol_set_hash,
            query_params_hash=manifest.wrds_query_hash,
            provider_signature=provider_signature,
        )

    def _build_alpaca_sip_warnings(
        self,
        summaries: list[ManifestSummaryDTO],
        missing: list[str],
    ) -> list[str]:
        warnings = [f"alpaca_sip_missing_manifest:{dataset}" for dataset in missing]
        by_dataset = {summary.dataset: summary for summary in summaries}
        daily = by_dataset.get(ALPACA_SIP_DAILY_DATASET)
        corp_actions = by_dataset.get(ALPACA_SIP_CORP_ACTIONS_DATASET)
        if daily is None or corp_actions is None:
            return warnings

        if (
            daily.symbol_set_hash
            and corp_actions.symbol_set_hash
            and daily.symbol_set_hash != corp_actions.symbol_set_hash
        ):
            warnings.append("alpaca_sip_companion_symbol_set_mismatch")

        end_date_drift = abs((daily.end_date - corp_actions.end_date).days)
        if end_date_drift > _ALPACA_SIP_COMPANION_MAX_END_DATE_DRIFT_DAYS:
            warnings.append("alpaca_sip_companion_manifest_stale")

        return warnings

    @staticmethod
    def _build_sync_validation_status(
        missing: list[str],
        validation_statuses: list[str],
    ) -> str:
        if not validation_statuses:
            return "missing"
        if missing:
            return f"missing: {', '.join(missing)}"
        if validation_statuses == ["passed"]:
            return "ok"
        return ",".join(validation_statuses)

    @staticmethod
    def _build_source_status(
        missing: list[str],
        validation_statuses: list[str],
    ) -> tuple[str, str | None]:
        if not validation_statuses:
            return (
                "unknown",
                "Local SIP sync status unavailable until manifest-backed status lands",
            )
        if missing:
            return "error", f"Missing SIP manifests: {', '.join(missing)}"
        if validation_statuses == ["passed"]:
            return "ok", None
        return (
            "error",
            f"SIP manifest validation statuses: {', '.join(validation_statuses)}",
        )


def _data_roles_for_dataset(dataset: str) -> dict[str, str] | None:
    role_map = {
        ALPACA_SIP_DAILY_DATASET: {"universe": dataset, "prices": dataset},
        ALPACA_SIP_CORP_ACTIONS_DATASET: {"corp_actions": dataset},
    }
    return role_map.get(dataset)


__all__ = [
    "ALPACA_SIP_CORP_ACTIONS_DATASET",
    "ALPACA_SIP_DAILY_DATASET",
    "ALPACA_SIP_MANIFEST_DATASETS",
    "DataManifestService",
    "ManifestSummaryDTO",
    "AlpacaSipManifestSummaryDTO",
]
