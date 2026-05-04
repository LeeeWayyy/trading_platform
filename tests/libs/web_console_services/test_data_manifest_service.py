"""Tests for shared manifest summary service."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from libs.data.data_quality.manifest import SyncManifest
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_DATASET_KEY,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
)
from libs.web_console_services.provider_signature import sanitize_provider_signature

SYNC_TS = datetime(2026, 5, 1, 12, tzinfo=UTC)


def _write_manifest(
    data_root: Path,
    *,
    dataset: str,
    row_count: int = 10,
    validation_status: str = "passed",
    end_date: date = date(2026, 4, 30),
    symbol_set_hash: str | None = "symbols-v1",
) -> None:
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = SyncManifest(
        dataset=dataset,
        sync_timestamp=SYNC_TS,
        start_date=date(2026, 4, 1),
        end_date=end_date,
        row_count=row_count,
        checksum=f"{dataset}-checksum",
        schema_version="v1.0.0",
        wrds_query_hash=f"{dataset}-query-hash",
        file_paths=[f"alpaca/sip/{dataset}.parquet"],
        validation_status=validation_status,  # type: ignore[arg-type]
        provider_id="alpaca_sip",
        provider_version="2026.04",
        source_feed="sip",
        adjustment_mode="raw" if dataset == ALPACA_SIP_DAILY_DATASET else None,
        manifest_id=f"{dataset}@v1:{dataset}-checksum",
        symbol_set_hash=symbol_set_hash,
        sync_started_at=SYNC_TS,
        sync_finished_at=SYNC_TS,
    )
    (manifest_dir / f"{dataset}.json").write_text(manifest.model_dump_json())


def test_alpaca_sip_summary_reports_missing_when_no_manifests(tmp_path: Path) -> None:
    service = DataManifestService(data_root=tmp_path / "data")

    summary = service.get_alpaca_sip_summary()

    assert summary.present_datasets == []
    assert summary.missing_datasets == [
        ALPACA_SIP_CORP_ACTIONS_DATASET,
        ALPACA_SIP_DAILY_DATASET,
    ]
    assert summary.to_sync_status().validation_status == "missing"
    assert summary.apply_to_source_spec({"status": "unknown"}) == {"status": "unknown"}


def test_alpaca_sip_summary_marks_partial_companion_missing(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_manifest(data_root, dataset=ALPACA_SIP_DAILY_DATASET, row_count=42)
    service = DataManifestService(data_root=data_root)

    summary = service.get_alpaca_sip_summary()

    assert summary.row_count == 42
    assert summary.to_sync_status().validation_status == (
        f"missing: {ALPACA_SIP_CORP_ACTIONS_DATASET}"
    )
    updated = summary.apply_to_source_spec({"status": "unknown"})
    assert updated["status"] == "error"
    assert updated["row_count"] == 42
    assert updated["error_message"] == (f"Missing SIP manifests: {ALPACA_SIP_CORP_ACTIONS_DATASET}")


def test_alpaca_sip_summary_exposes_raw_daily_provenance(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_manifest(data_root, dataset=ALPACA_SIP_DAILY_DATASET, row_count=42)
    _write_manifest(data_root, dataset=ALPACA_SIP_CORP_ACTIONS_DATASET, row_count=3)
    service = DataManifestService(data_root=data_root)

    summary = service.get_alpaca_sip_summary()
    daily = next(item for item in summary.manifests if item.dataset == ALPACA_SIP_DAILY_DATASET)

    assert summary.to_sync_status().validation_status == "ok"
    assert summary.row_count == 45
    assert daily.canonical_storage_mode == "raw"
    assert daily.read_time_adjustment_mode == "unavailable"
    assert daily.provider_signature.canonical_storage_mode == "raw"
    assert daily.provider_signature.read_time_adjustment_mode == "unavailable"
    assert daily.manifest_reference == "manifests://alpaca_sip_daily.json"
    assert daily.provider_signature.manifest_reference == daily.manifest_reference
    assert daily.provider_signature.manifest_checksum == daily.manifest_checksum
    assert daily.provider_signature.query_params_hash == "alpaca_sip_daily-query-hash"
    assert str(data_root) not in daily.manifest_reference


def test_alpaca_sip_summary_adds_companion_warnings(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_manifest(
        data_root,
        dataset=ALPACA_SIP_DAILY_DATASET,
        end_date=date(2026, 4, 30),
        symbol_set_hash="daily-symbols",
    )
    _write_manifest(
        data_root,
        dataset=ALPACA_SIP_CORP_ACTIONS_DATASET,
        end_date=date(2026, 4, 27),
        symbol_set_hash="corp-symbols",
    )
    service = DataManifestService(data_root=data_root)

    summary = service.get_alpaca_sip_summary()

    assert "alpaca_sip_companion_symbol_set_mismatch" in summary.warnings
    assert "alpaca_sip_companion_manifest_stale" in summary.warnings


def test_provider_signature_sanitizer_drops_unknown_sensitive_fields() -> None:
    signature = sanitize_provider_signature(
        {
            "provider_id": "alpaca_sip",
            "manifest_checksum": "checksum",
            "manifest_reference": "https://example.test/file?X-Amz-Signature=secret",
            "provider_version": "Bearer abc.def.ghi",
            "schema_version": "password=hunter2",
            "query_params_hash": "https://user:password@example.test/path",
            "auth_header": "Bearer secret",
            "submit_token": "secret-token",
            "raw_request_body": "x" * 4096,
            "dataset_keys": [f"dataset_{idx}" for idx in range(40)],
        }
    )

    dumped = signature.model_dump(exclude_none=True)
    assert dumped == {
        "provider_id": "alpaca_sip",
        "manifest_checksum": "checksum",
        "dataset_keys": [f"dataset_{idx}" for idx in range(32)],
    }


def test_apply_to_source_spec_handles_missing_latest_sync_defensively(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    _write_manifest(data_root, dataset=ALPACA_SIP_DAILY_DATASET)
    manifest_summary = DataManifestService(data_root=data_root).get_manifest_summary(
        ALPACA_SIP_DAILY_DATASET
    )
    assert manifest_summary is not None
    summary = AlpacaSipManifestSummaryDTO(
        dataset=ALPACA_SIP_DATASET_KEY,
        manifests=[manifest_summary],
        present_datasets=[ALPACA_SIP_DAILY_DATASET],
        missing_datasets=[],
        latest_sync=None,
        row_count=10,
        schema_versions=["v1.0.0"],
        validation_statuses=["passed"],
        sync_validation_status="ok",
        source_status="ok",
        source_error_rate_pct=0.0,
        warnings=[],
    )

    updated = summary.apply_to_source_spec({"status": "unknown"})

    assert updated["status"] == "ok"
    assert updated["minutes_ago"] == 0


def test_unknown_manifest_dataset_has_no_data_roles(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_manifest(data_root, dataset="custom_dataset")

    summary = DataManifestService(data_root=data_root).get_manifest_summary("custom_dataset")

    assert summary is not None
    assert summary.provider_signature.data_roles is None
