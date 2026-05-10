"""Tests for Phase 1 data manifest UI read models."""

from __future__ import annotations

from datetime import UTC, date, datetime

from apps.web_console_ng.components.data_context_ribbon import (
    build_manifest_context_metrics,
)
from apps.web_console_ng.components.data_detail_drawer import (
    build_manifest_detail_fields,
)
from apps.web_console_ng.components.data_operations_grid import build_manifest_grid_rows
from apps.web_console_ng.components.data_quality_section import build_quality_signal_rows
from apps.web_console_ng.components.data_readiness_section import build_readiness_rows
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
    ManifestSummaryDTO,
)
from libs.web_console_services.provider_signature import ProviderSignatureDTO
from libs.web_console_services.schemas.data_management import (
    DataQualitySignalDTO,
    DataQualitySummaryDTO,
    DataReadinessCheckDTO,
    DataReadinessDTO,
)

NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


def _manifest(
    dataset: str,
    *,
    row_count: int = 10,
    validation_status: str = "passed",
    end: date = date(2026, 4, 30),
    symbol_hash: str | None = "symbols-v1",
    read_time_adjustment_mode: str | None = None,
) -> ManifestSummaryDTO:
    is_daily = dataset == ALPACA_SIP_DAILY_DATASET
    adjustment_read_mode = (
        read_time_adjustment_mode
        if read_time_adjustment_mode is not None
        else "unavailable"
        if is_daily
        else None
    )
    signature = ProviderSignatureDTO(
        provider_id="alpaca_sip",
        provider_version="2026.04",
        source_feed="sip",
        adjustment_mode="raw" if is_daily else None,
        canonical_storage_mode="raw" if is_daily else None,
        read_time_adjustment_mode=adjustment_read_mode,
        symbol_set_hash=symbol_hash,
        query_params_hash=f"{dataset}-query",
        manifest_id=f"{dataset}@v1:checksum",
        manifest_reference=f"manifests://{dataset}.json",
        manifest_checksum=f"{dataset}-checksum",
        manifest_version="1",
        schema_version="v1.0.0",
        sync_started_at=NOW,
        sync_finished_at=NOW,
        data_roles={("prices" if is_daily else "corp_actions"): dataset},
        dataset_keys=[dataset],
    )
    return ManifestSummaryDTO(
        dataset=dataset,
        manifest_reference=f"manifests://{dataset}.json",
        manifest_id=f"{dataset}@v1:checksum",
        manifest_checksum=f"{dataset}-checksum",
        manifest_version=1,
        schema_version="v1.0.0",
        validation_status=validation_status,
        sync_timestamp=NOW,
        sync_started_at=NOW,
        sync_finished_at=NOW,
        start_date=date(2026, 4, 1),
        end_date=end,
        row_count=row_count,
        file_count=1,
        provider_id="alpaca_sip",
        provider_version="2026.04",
        source_feed="sip",
        adjustment_mode="raw" if is_daily else None,
        canonical_storage_mode="raw" if is_daily else None,
        read_time_adjustment_mode=adjustment_read_mode,
        symbol_set_hash=symbol_hash,
        query_params_hash=f"{dataset}-query",
        provider_signature=signature,
    )


def _summary(
    manifests: list[ManifestSummaryDTO],
    *,
    missing: list[str] | None = None,
    warnings: list[str] | None = None,
) -> AlpacaSipManifestSummaryDTO:
    missing_datasets = missing if missing is not None else []
    statuses = sorted({manifest.validation_status for manifest in manifests})
    return AlpacaSipManifestSummaryDTO(
        manifests=manifests,
        present_datasets=sorted(manifest.dataset for manifest in manifests),
        missing_datasets=missing_datasets,
        latest_sync=max((manifest.sync_timestamp for manifest in manifests), default=None),
        row_count=sum(manifest.row_count for manifest in manifests),
        schema_versions=sorted({manifest.schema_version for manifest in manifests}),
        validation_statuses=statuses,
        sync_validation_status=(
            "missing" if not manifests else "ok" if statuses == ["passed"] else ",".join(statuses)
        ),
        source_status="unknown" if not manifests else "ok",
        source_error_message=None,
        source_error_rate_pct=0.0 if manifests else 100.0,
        warnings=warnings or [],
    )


def test_grid_rows_make_missing_manifests_and_raw_sip_blockers_visible() -> None:
    summary = _summary(
        [],
        missing=[ALPACA_SIP_DAILY_DATASET, ALPACA_SIP_CORP_ACTIONS_DATASET],
        warnings=[
            f"alpaca_sip_missing_manifest:{ALPACA_SIP_DAILY_DATASET}",
            f"alpaca_sip_missing_manifest:{ALPACA_SIP_CORP_ACTIONS_DATASET}",
        ],
    )

    rows = build_manifest_grid_rows(summary)
    daily = next(row for row in rows if row["dataset"] == ALPACA_SIP_DAILY_DATASET)

    assert daily["local_state"] == "missing"
    assert daily["manifest_status"] == "missing"
    assert daily["raw_state"] == "Raw OHLC"
    assert daily["trusted_manifest_backed"] is False
    assert "alpaca_sip_untrusted_without_manifest" in daily["readiness"]
    assert "raw_sip_returns_unavailable" in daily["readiness"]
    assert daily["adjustment_state"] == "adj_close: not available; ret: not available"


def test_grid_and_detail_expose_present_raw_manifest_provenance() -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, row_count=42),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET, row_count=3),
        ]
    )

    rows = build_manifest_grid_rows(summary)
    daily = next(row for row in rows if row["dataset"] == ALPACA_SIP_DAILY_DATASET)
    corp_actions = next(row for row in rows if row["dataset"] == ALPACA_SIP_CORP_ACTIONS_DATASET)
    detail = build_manifest_detail_fields(summary, ALPACA_SIP_DAILY_DATASET)
    detail_map = {item["field"]: item["value"] for item in detail}

    assert daily["trusted_manifest_backed"] is True
    assert daily["canonical_storage_mode"] == "raw"
    assert daily["read_time_adjustment_mode"] == "unavailable"
    assert daily["manifest_id"] == "alpaca_sip_daily@v1:checksum"
    assert corp_actions["readiness"] == "corporate actions only"
    assert detail_map["Manifest checksum"] == "alpaca_sip_daily-checksum"
    assert detail_map["Canonical storage mode"] == "raw"
    assert detail_map["Read-time adjustment mode"] == "unavailable"
    assert detail_map["adj_close"] == "not available"
    assert detail_map["ret"] == "not available"
    assert "raw_sip_returns_unavailable" in detail_map["Backtest readiness"]
    assert "provider_id" in detail_map["Provider signature"]


def test_invalid_manifest_is_untrusted_and_blocked() -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, validation_status="failed"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )

    rows = build_manifest_grid_rows(summary)
    daily = next(row for row in rows if row["dataset"] == ALPACA_SIP_DAILY_DATASET)
    detail = build_manifest_detail_fields(summary, ALPACA_SIP_DAILY_DATASET)
    detail_map = {item["field"]: item["value"] for item in detail}
    metrics = {
        metric["label"]: metric["value"] for metric in build_manifest_context_metrics(summary)
    }

    assert daily["trusted_manifest_backed"] is False
    assert daily["issues"] >= 1
    assert "untrusted_manifest_validation_failed" in daily["readiness"]
    assert "manifest_validation_failed" in daily["issue_codes"]
    assert detail_map["Trusted manifest-backed"] == "false"
    assert "untrusted_manifest_validation_failed" in detail_map["Readiness"]
    assert "untrusted_manifest_validation_failed" in detail_map["Backtest readiness"]
    assert metrics["Untrusted"] == 1


def test_context_metrics_count_missing_and_pairing_warnings() -> None:
    summary = _summary(
        [
            _manifest(
                ALPACA_SIP_DAILY_DATASET,
                end=date(2026, 4, 30),
                symbol_hash="daily-symbols",
            ),
            _manifest(
                ALPACA_SIP_CORP_ACTIONS_DATASET,
                end=date(2026, 4, 27),
                symbol_hash="corp-symbols",
            ),
        ],
        warnings=[
            "alpaca_sip_companion_symbol_set_mismatch",
            "alpaca_sip_companion_manifest_stale",
        ],
    )

    metrics = {
        metric["label"]: metric["value"] for metric in build_manifest_context_metrics(summary)
    }
    rows = build_manifest_grid_rows(summary)

    assert metrics["Healthy"] == 2
    assert metrics["Missing"] == 0
    assert metrics["Backtest Blocked"] == 1
    assert metrics["Issues"] == 2
    assert all(row["issues"] >= 1 for row in rows)


def test_context_metrics_clear_backtest_blockers_when_inputs_ready() -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )

    metrics = {
        metric["label"]: metric["value"] for metric in build_manifest_context_metrics(summary)
    }

    assert metrics["Backtest Blocked"] == 0


def test_missing_companion_warning_stays_on_missing_row() -> None:
    summary = _summary(
        [_manifest(ALPACA_SIP_DAILY_DATASET)],
        missing=[ALPACA_SIP_CORP_ACTIONS_DATASET],
        warnings=[f"alpaca_sip_missing_manifest:{ALPACA_SIP_CORP_ACTIONS_DATASET}"],
    )

    rows = build_manifest_grid_rows(summary)
    metrics = {
        metric["label"]: metric["value"] for metric in build_manifest_context_metrics(summary)
    }
    daily = next(row for row in rows if row["dataset"] == ALPACA_SIP_DAILY_DATASET)
    corp_actions = next(row for row in rows if row["dataset"] == ALPACA_SIP_CORP_ACTIONS_DATASET)

    assert metrics["Backtest Blocked"] == 2
    assert "alpaca_sip_untrusted_without_manifest" not in daily["issue_codes"]
    assert "alpaca_sip_untrusted_without_manifest" in corp_actions["issue_codes"]


def test_readiness_rows_expose_action_targets() -> None:
    readiness = DataReadinessDTO(
        dataset="alpaca_sip",
        workflow="simple_backtest",
        status="blocked",
        generated_at=NOW,
        blockers=["raw_sip_returns_unavailable"],
        checks=[
            DataReadinessCheckDTO(
                code="raw_sip_returns_unavailable",
                status="blocked",
                message="Raw SIP returns are unavailable.",
                source="read_time_adjustment_policy",
                action_label="Use adjusted returns",
                target_section="backtest",
            )
        ],
    )

    rows = build_readiness_rows(readiness)

    assert rows == [
        {
            "status": "blocked",
            "code": "raw_sip_returns_unavailable",
            "source": "read_time_adjustment_policy",
            "message": "Raw SIP returns are unavailable.",
            "action": "Use adjusted returns",
            "target": "backtest",
        }
    ]


def test_quality_signal_rows_expose_source_and_reason_codes() -> None:
    summary = DataQualitySummaryDTO(
        dataset="alpaca_sip",
        status="warning",
        generated_at=NOW,
        acknowledgments_persistent=False,
        acknowledgment_status_source="in_memory_store_unavailable",
        signals=[
            DataQualitySignalDTO(
                dataset="alpaca_sip",
                check="alpaca_feed_delta",
                status="unavailable",
                source="report_store",
                observed_at=None,
                message="No report persisted.",
                reason_codes=["alpaca_feed_delta_report_unavailable"],
            )
        ],
    )

    rows = build_quality_signal_rows(summary)

    assert rows[0]["source"] == "report_store"
    assert rows[0]["observed_at"] == "-"
    assert rows[0]["reason_codes"] == "alpaca_feed_delta_report_unavailable"
