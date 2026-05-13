"""Unit tests for libs.web_console_services.data_quality_service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from threading import get_ident
from typing import cast
from unittest.mock import patch

import pytest

from libs.web_console_services.alert_acknowledgment_store import (
    InMemoryAlertAcknowledgmentStore,
)
from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
    ManifestSummaryDTO,
)
from libs.web_console_services.data_quality_service import (
    _MAX_QUALITY_REPORT_BYTES,
    AlpacaQualityReportStore,
    DataQualityService,
    QualityReportState,
    _build_alpaca_sip_quality_summary,
)
from libs.web_console_services.provider_signature import ProviderSignatureDTO


class FakePersistentAlertAcknowledgmentStore(InMemoryAlertAcknowledgmentStore):
    """In-memory store that advertises durable persistence for tests."""

    is_persistent = True


@dataclass(frozen=True)
class DummyUser:
    user_id: str


class FakeManifestService:
    def __init__(self, summary: AlpacaSipManifestSummaryDTO) -> None:
        self._summary = summary
        self.thread_ids: list[int] = []

    def get_alpaca_sip_summary(self) -> AlpacaSipManifestSummaryDTO:
        self.thread_ids.append(get_ident())
        return self._summary


class RaisingManifestService:
    def __init__(self) -> None:
        self.thread_ids: list[int] = []

    def get_alpaca_sip_summary(self) -> AlpacaSipManifestSummaryDTO:
        self.thread_ids.append(get_ident())
        raise RuntimeError("manifest store unavailable")


class ThreadTrackingReportStore:
    def __init__(
        self,
        *,
        integrity_report: QualityReportState | None = None,
        feed_delta_report: QualityReportState | None = None,
    ) -> None:
        self.integrity_report = integrity_report
        self.feed_delta_report = feed_delta_report
        self.thread_ids: list[int] = []

    def get_integrity_report(self) -> QualityReportState | None:
        self.thread_ids.append(get_ident())
        return self.integrity_report

    def get_feed_delta_report(self) -> QualityReportState | None:
        self.thread_ids.append(get_ident())
        return self.feed_delta_report


@pytest.fixture()
def service() -> DataQualityService:
    return DataQualityService()


def _manifest(dataset: str, *, validation_status: str = "passed") -> ManifestSummaryDTO:
    return ManifestSummaryDTO(
        dataset=dataset,
        manifest_reference=f"manifests://{dataset}.json",
        manifest_id=f"{dataset}@v1:checksum",
        manifest_checksum=f"{dataset}-checksum",
        manifest_version=1,
        schema_version="v1",
        validation_status=validation_status,
        sync_timestamp=datetime(2026, 5, 1, 12, tzinfo=UTC),
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        row_count=10,
        file_count=1,
        provider_id="alpaca_sip",
        provider_version="2026.04",
        source_feed="sip",
        adjustment_mode="raw" if dataset == ALPACA_SIP_DAILY_DATASET else None,
        canonical_storage_mode="raw" if dataset == ALPACA_SIP_DAILY_DATASET else None,
        read_time_adjustment_mode=("unavailable" if dataset == ALPACA_SIP_DAILY_DATASET else None),
        provider_signature=ProviderSignatureDTO(
            provider_id="alpaca_sip",
            manifest_id=f"{dataset}@v1:checksum",
            manifest_reference=f"manifests://{dataset}.json",
            manifest_checksum=f"{dataset}-checksum",
            dataset_keys=[dataset],
        ),
    )


def _summary(
    manifests: list[ManifestSummaryDTO],
    *,
    missing: list[str] | None = None,
    warnings: list[str] | None = None,
) -> AlpacaSipManifestSummaryDTO:
    statuses = sorted({manifest.validation_status for manifest in manifests})
    return AlpacaSipManifestSummaryDTO(
        manifests=manifests,
        present_datasets=sorted(manifest.dataset for manifest in manifests),
        missing_datasets=missing or [],
        latest_sync=max((manifest.sync_timestamp for manifest in manifests), default=None),
        row_count=sum(manifest.row_count for manifest in manifests),
        schema_versions=sorted({manifest.schema_version for manifest in manifests}),
        validation_statuses=statuses,
        sync_validation_status="ok" if statuses == ["passed"] and not missing else "missing",
        source_status="ok" if statuses == ["passed"] and not missing else "error",
        source_error_message=None if statuses == ["passed"] and not missing else "missing",
        source_error_rate_pct=0.0 if statuses == ["passed"] and not missing else 100.0,
        warnings=warnings or [],
    )


def _write_quality_report(
    quality_dir: Path,
    filename: str,
    *,
    report_type: str,
    status: str,
    content_hash: str,
    timeframe: str = "1Day",
) -> Path:
    quality_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "report_type": report_type,
        "status": status,
        "content_hash": content_hash,
        "start": "2024-04-22T00:00:00+00:00",
        "end": "2024-04-26T00:00:00+00:00",
        "timeframe": timeframe,
    }
    if report_type == "alpaca_feed_delta":
        payload["tolerances"] = {"version": "alpaca-iex-sip-delta-v1"}
    path = quality_dir / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.asyncio()
async def test_get_validation_results_filters_by_dataset_permission(
    service: DataQualityService,
) -> None:
    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "compustat"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        results = await service.get_validation_results(DummyUser(user_id="user-1"), dataset=None)

    datasets = {item.dataset for item in results}
    assert datasets == {"crsp", "compustat"}


@pytest.mark.asyncio()
async def test_get_validation_results_requires_dataset_access(service: DataQualityService) -> None:
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=False,
        ),
    ):
        with pytest.raises(PermissionError, match="Dataset access required"):
            await service.get_validation_results(DummyUser(user_id="user-1"), dataset="taq")


@pytest.mark.asyncio()
async def test_get_anomaly_alerts_filters_severity_and_acknowledged(
    service: DataQualityService,
) -> None:
    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "fama_french"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        alerts = await service.get_anomaly_alerts(
            DummyUser(user_id="user-1"), severity="warning", acknowledged=False
        )

    assert {alert.dataset for alert in alerts} == {"crsp", "fama_french"}
    assert all(alert.severity == "warning" for alert in alerts)
    assert all(alert.acknowledged is False for alert in alerts)


@pytest.mark.asyncio()
async def test_get_anomaly_alerts_hydrates_acknowledged_state_from_store() -> None:
    """After an operator acknowledges an alert, subsequent reads must
    surface the acknowledgment so the UI does not re-show the same alert
    as unacked and the ``acked`` filter is not empty (Codex PR #225 P2)."""
    store = FakePersistentAlertAcknowledgmentStore()
    svc = DataQualityService(acknowledgment_store=store)

    # Persist an acknowledgment for the first mocked alert. ``alert-1``
    # maps to _SUPPORTED_DATASETS[0] which is "crsp" under the legacy
    # resolver.
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
    ):
        await svc.acknowledge_alert(
            DummyUser(user_id="user-1"), alert_id="alert-1", reason="triage"
        )

        unacked = await svc.get_anomaly_alerts(
            DummyUser(user_id="user-1"), severity=None, acknowledged=False
        )
        acked = await svc.get_anomaly_alerts(
            DummyUser(user_id="user-1"), severity=None, acknowledged=True
        )

    assert "alert-1" not in {a.id for a in unacked}
    assert {a.id for a in acked} == {"alert-1"}
    (only,) = acked
    assert only.acknowledged is True
    assert only.acknowledged_by == "user-1"


@pytest.mark.asyncio()
async def test_get_anomaly_alerts_excludes_manifest_backed_alpaca_sip_placeholder(
    service: DataQualityService,
) -> None:
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        alerts = await service.get_anomaly_alerts(
            DummyUser(user_id="user-1"), severity=None, acknowledged=None
        )

    assert "alpaca_sip" not in {alert.dataset for alert in alerts}


@pytest.mark.asyncio()
async def test_get_quarantine_status_excludes_manifest_backed_alpaca_sip_placeholder(
    service: DataQualityService,
) -> None:
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        entries = await service.get_quarantine_status(DummyUser(user_id="user-1"))

    assert "alpaca_sip" not in {entry.dataset for entry in entries}


@pytest.mark.asyncio()
async def test_acknowledge_alert_idempotent_with_persistent_store() -> None:
    store = FakePersistentAlertAcknowledgmentStore()
    svc = DataQualityService(acknowledgment_store=store)

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
    ):
        first = await svc.acknowledge_alert(
            DummyUser(user_id="user-1"),
            alert_id="alert-1",
            reason="triage",
            source="anomaly_alert",
            issue_scope={"page": "data_management"},
        )
        second = await svc.acknowledge_alert(
            DummyUser(user_id="user-1"),
            alert_id="alert-1",
            reason="ignore",
        )

    assert svc.acknowledgments_persistent is True
    assert first.id == second.id
    assert second.reason == "triage"
    assert second.acknowledged_by == "user-1"
    assert first.source == "anomaly_alert"
    # Issue scope captures dataset/metric/severity plus any caller additions.
    assert first.issue_scope["dataset"] == first.dataset
    assert first.issue_scope["metric"] == first.metric
    assert first.issue_scope["severity"] == first.severity
    assert first.issue_scope["page"] == "data_management"


@pytest.mark.asyncio()
async def test_acknowledge_alert_accepts_explicit_dataset_for_manifest_id() -> None:
    """Manifest-derived signal ids do not match the legacy mock format.

    The UI knows ``alert.dataset`` and passes it explicitly; the service
    must NOT try to parse it out of ``alert_id``.
    """
    store = FakePersistentAlertAcknowledgmentStore()
    svc = DataQualityService(acknowledgment_store=store)

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
    ):
        ack = await svc.acknowledge_alert(
            DummyUser(user_id="user-1"),
            alert_id="alpaca-sip-manifest-pairing",
            reason="triage",
            dataset="alpaca_sip",
            metric="manifest_pairing",
            severity="warning",
        )

    assert ack.dataset == "alpaca_sip"
    assert ack.metric == "manifest_pairing"
    assert ack.alert_id == "alpaca-sip-manifest-pairing"


@pytest.mark.asyncio()
async def test_acknowledge_alert_falls_back_to_resolver_when_dataset_omitted() -> None:
    """When the caller does not pass dataset, the legacy alert-{idx}
    parser still runs as a best-effort fallback for backwards compat."""
    store = FakePersistentAlertAcknowledgmentStore()
    svc = DataQualityService(acknowledgment_store=store)

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
    ):
        ack = await svc.acknowledge_alert(
            DummyUser(user_id="user-1"),
            alert_id="alert-1",
            reason="triage",
        )

    # alert-1 maps to _SUPPORTED_DATASETS[0] which is "crsp".
    assert ack.dataset == "crsp"


@pytest.mark.asyncio()
async def test_acknowledge_alert_unavailable_when_store_not_persistent() -> None:
    svc = DataQualityService()  # defaults to in-memory non-persistent store

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
        pytest.raises(RuntimeError, match="quality_acknowledgment_persistence_unavailable"),
    ):
        await svc.acknowledge_alert(
            DummyUser(user_id="user-1"), alert_id="alert-1", reason="triage"
        )

    assert svc.acknowledgments_persistent is False


def test_resolve_alert_dataset_invalid_id() -> None:
    with pytest.raises(ValueError, match="Could not resolve dataset"):
        DataQualityService._resolve_alert_dataset("bad-id")


@pytest.mark.asyncio()
async def test_get_validation_results_uses_manifest_for_alpaca_sip() -> None:
    main_thread_id = get_ident()
    manifest_service = FakeManifestService(
        _summary([], missing=[ALPACA_SIP_DAILY_DATASET, ALPACA_SIP_CORP_ACTIONS_DATASET])
    )
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset="alpaca_sip",
        )

    assert [result.validation_type for result in results] == [
        "manifest_summary",
        "manifest_pairing",
    ]
    assert results[0].status == "error"
    assert results[0].actual_value == "missing"
    assert results[1].status == "error"
    assert manifest_service.thread_ids
    assert all(thread_id != main_thread_id for thread_id in manifest_service.thread_ids)


@pytest.mark.asyncio()
async def test_get_validation_results_explains_failed_alpaca_manifest_without_source_error() -> (
    None
):
    manifest_summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, validation_status="failed"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    ).model_copy(update={"source_error_message": None})
    manifest_service = FakeManifestService(manifest_summary)
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset="alpaca_sip",
        )

    assert results[0].status == "error"
    assert (
        results[0].error_message == "Alpaca SIP manifest validation failed: alpaca_sip_daily=failed"
    )
    assert results[0].actual_value == "failed"


@pytest.mark.asyncio()
async def test_get_validation_results_sorts_failed_alpaca_manifest_message() -> None:
    manifest_summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, validation_status="failed"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET, validation_status="failed"),
        ]
    ).model_copy(update={"source_error_message": None})
    manifest_service = FakeManifestService(manifest_summary)
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset="alpaca_sip",
        )

    assert results[0].error_message == (
        "Alpaca SIP manifest validation failed: "
        f"{ALPACA_SIP_CORP_ACTIONS_DATASET}=failed, {ALPACA_SIP_DAILY_DATASET}=failed"
    )


@pytest.mark.asyncio()
async def test_get_validation_results_explains_missing_alpaca_manifest_without_source_error() -> (
    None
):
    manifest_summary = _summary([]).model_copy(update={"source_error_message": None})
    manifest_service = FakeManifestService(manifest_summary)
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset="alpaca_sip",
        )

    assert results[0].status == "error"
    assert results[0].error_message == "No Alpaca SIP manifests found."
    assert results[0].actual_value == "failed"


@pytest.mark.asyncio()
async def test_get_validation_results_appends_alpaca_manifest_for_all_datasets() -> None:
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ]
        )
    )
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "alpaca_sip"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        results = await svc.get_validation_results(DummyUser(user_id="user-1"), dataset=None)

    alpaca_results = [result for result in results if result.dataset == "alpaca_sip"]
    crsp_results = [result for result in results if result.dataset == "crsp"]
    assert [result.validation_type for result in alpaca_results] == [
        "manifest_summary",
        "manifest_pairing",
    ]
    assert [result.status for result in alpaca_results] == ["ok", "ok"]
    assert len(crsp_results) == 1
    assert crsp_results[0].status == "ok"


@pytest.mark.asyncio()
async def test_get_validation_results_prioritizes_alpaca_manifest_when_limited() -> None:
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ]
        )
    )
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "alpaca_sip"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset=None,
            limit=1,
        )

    assert [result.dataset for result in results] == ["alpaca_sip"]
    assert results[0].validation_type == "manifest_summary"


@pytest.mark.asyncio()
async def test_get_validation_results_includes_alpaca_pairing_signal() -> None:
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
            warnings=["alpaca_sip_companion_manifest_stale"],
        )
    )
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset="alpaca_sip",
        )

    pairing = next(result for result in results if result.validation_type == "manifest_pairing")
    assert pairing.status == "warning"
    assert pairing.actual_value == "warning"
    assert pairing.error_message == "Companion manifests need operator review."


@pytest.mark.asyncio()
async def test_get_validation_results_raises_when_alpaca_manifest_unavailable() -> None:
    main_thread_id = get_ident()
    manifest_service = RaisingManifestService()
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))

    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "alpaca_sip"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        with pytest.raises(RuntimeError, match="manifest store unavailable"):
            await svc.get_validation_results(DummyUser(user_id="user-1"), dataset=None)

    assert manifest_service.thread_ids
    assert all(thread_id != main_thread_id for thread_id in manifest_service.thread_ids)


@pytest.mark.asyncio()
async def test_get_validation_results_reuses_provided_alpaca_summary() -> None:
    manifest_service = RaisingManifestService()
    svc = DataQualityService(manifest_service=cast(DataManifestService, manifest_service))
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        results = await svc.get_validation_results(
            DummyUser(user_id="user-1"),
            dataset="alpaca_sip",
            alpaca_sip_summary=summary,
        )

    assert [result.validation_type for result in results] == [
        "manifest_summary",
        "manifest_pairing",
    ]
    assert manifest_service.thread_ids == []


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_marks_unavailable_report_inputs(
    tmp_path: Path,
) -> None:
    main_thread_id = get_ident()
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        )
    )
    svc = DataQualityService(
        manifest_service=cast(DataManifestService, manifest_service),
        data_root=tmp_path,
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        summary = await svc.get_alpaca_sip_quality_summary(DummyUser(user_id="user-1"))

    statuses = {signal.check: signal.status for signal in summary.signals}
    assert summary.status == "unavailable"
    assert statuses["manifest_validation"] == "passed"
    assert statuses["alpaca_sip_integrity"] == "unavailable"
    assert statuses["alpaca_feed_delta"] == "unavailable"
    assert statuses["quality_acknowledgment_persistence"] == "unavailable"
    assert summary.acknowledgments_persistent is False
    assert manifest_service.thread_ids
    assert all(thread_id != main_thread_id for thread_id in manifest_service.thread_ids)


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_loads_persisted_report_statuses(
    tmp_path: Path,
) -> None:
    quality_dir = tmp_path / "quality"
    integrity_path = _write_quality_report(
        quality_dir,
        "alpaca_sip_integrity_test.json",
        report_type="alpaca_sip_integrity",
        status="passed",
        content_hash="integrity-hash",
    )
    feed_delta_path = _write_quality_report(
        quality_dir,
        "alpaca_iex_sip_delta_test.json",
        report_type="alpaca_feed_delta",
        status="warning",
        content_hash="feed-delta-hash",
    )
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        )
    )
    svc = DataQualityService(
        manifest_service=cast(DataManifestService, manifest_service),
        data_root=tmp_path,
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        summary = await svc.get_alpaca_sip_quality_summary(DummyUser(user_id="user-1"))

    signals = {signal.check: signal for signal in summary.signals}
    assert summary.status == "warning"
    assert signals["alpaca_sip_integrity"].status == "passed"
    assert signals["alpaca_feed_delta"].status == "warning"
    assert (
        "alpaca_sip_integrity_hash:integrity-hash" in signals["alpaca_sip_integrity"].reason_codes
    )
    assert "alpaca_feed_delta_timeframe:1Day" in signals["alpaca_feed_delta"].reason_codes
    assert signals["alpaca_sip_integrity"].observed_at == datetime.fromtimestamp(
        integrity_path.stat().st_mtime,
        UTC,
    )
    assert signals["alpaca_feed_delta"].observed_at == datetime.fromtimestamp(
        feed_delta_path.stat().st_mtime,
        UTC,
    )
    assert "hash=feed-delta-hash" in signals["alpaca_feed_delta"].message


def test_alpaca_report_store_uses_latest_pointer_before_directory_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quality_dir = tmp_path / "quality"
    _write_quality_report(
        quality_dir,
        "alpaca_sip_integrity_old.json",
        report_type="alpaca_sip_integrity",
        status="failed",
        content_hash="old-hash",
    )
    _write_quality_report(
        quality_dir,
        "alpaca_sip_integrity_latest.json",
        report_type="alpaca_sip_integrity",
        status="passed",
        content_hash="latest-hash",
    )
    store = AlpacaQualityReportStore(data_root=tmp_path)

    def fail_scan(_quality_dir: Path, _pattern: str) -> Path | None:
        raise AssertionError("directory scan should not run when latest pointer exists")

    monkeypatch.setattr(store, "_latest_report_path", fail_scan)

    report = store.get_integrity_report()

    assert report is not None
    assert report.status == "passed"
    assert report.content_hash == "latest-hash"


def test_alpaca_report_store_ignores_scan_symlink_outside_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    quality_dir = data_root / "quality"
    quality_dir.mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_report = _write_quality_report(
        outside_dir,
        "alpaca_sip_integrity_evil.json",
        report_type="alpaca_sip_integrity",
        status="passed",
        content_hash="outside-hash",
    )
    (quality_dir / "alpaca_sip_integrity_evil.json").symlink_to(outside_report)

    report = AlpacaQualityReportStore(data_root=data_root).get_integrity_report()

    assert report is None


def test_alpaca_report_store_rejects_oversized_report(tmp_path: Path) -> None:
    quality_dir = tmp_path / "quality"
    quality_dir.mkdir(parents=True)
    report_path = quality_dir / "alpaca_sip_integrity_large.json"
    report_path.write_text("x" * (_MAX_QUALITY_REPORT_BYTES + 1), encoding="utf-8")

    report = AlpacaQualityReportStore(data_root=tmp_path).get_integrity_report()

    assert report is None


def test_alpaca_report_store_allows_configured_absolute_path_outside_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    external_root = tmp_path / "external_quality"
    outside_report = _write_quality_report(
        external_root,
        "alpaca_sip_integrity_external.json",
        report_type="alpaca_sip_integrity",
        status="passed",
        content_hash="external-hash",
    )
    monkeypatch.setenv("ALPACA_SIP_INTEGRITY_REPORT", str(outside_report))
    monkeypatch.setenv("ALPACA_QUALITY_REPORT_ROOTS", str(external_root))

    report = AlpacaQualityReportStore(data_root=data_root).get_integrity_report()

    assert report is not None
    assert report.path == outside_report
    assert report.content_hash == "external-hash"
    assert report.observed_at == datetime.fromtimestamp(outside_report.stat().st_mtime, UTC)


def test_alpaca_report_store_rejects_untrusted_configured_absolute_path_outside_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    outside_report = _write_quality_report(
        tmp_path / "untrusted_quality",
        "alpaca_sip_integrity_external.json",
        report_type="alpaca_sip_integrity",
        status="passed",
        content_hash="external-hash",
    )
    monkeypatch.setenv("ALPACA_SIP_INTEGRITY_REPORT", str(outside_report))
    monkeypatch.delenv("ALPACA_QUALITY_REPORT_ROOTS", raising=False)

    report = AlpacaQualityReportStore(data_root=data_root).get_integrity_report()

    assert report is None


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_loads_report_store_off_event_loop() -> None:
    main_thread_id = get_ident()
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        )
    )
    report_store = ThreadTrackingReportStore(
        integrity_report=QualityReportState(
            report_type="alpaca_sip_integrity",
            status="passed",
            raw_status="passed",
            content_hash="integrity-hash",
            start="2024-04-22T00:00:00+00:00",
            end="2024-04-26T00:00:00+00:00",
            timeframe="1Day",
        ),
        feed_delta_report=QualityReportState(
            report_type="alpaca_feed_delta",
            status="passed",
            raw_status="passed",
            content_hash="feed-delta-hash",
            start="2024-04-22T00:00:00+00:00",
            end="2024-04-26T00:00:00+00:00",
            timeframe="1Day",
        ),
    )
    svc = DataQualityService(
        manifest_service=cast(DataManifestService, manifest_service),
        report_store=cast(AlpacaQualityReportStore, report_store),
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        summary = await svc.get_alpaca_sip_quality_summary(DummyUser(user_id="user-1"))

    statuses = {signal.check: signal.status for signal in summary.signals}
    assert statuses["alpaca_sip_integrity"] == "passed"
    assert statuses["alpaca_feed_delta"] == "passed"
    assert report_store.thread_ids
    assert all(thread_id != main_thread_id for thread_id in report_store.thread_ids)


def test_alpaca_sip_quality_summary_builder_passes_when_persisted_inputs_available() -> None:
    summary = _build_alpaca_sip_quality_summary(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        ),
        integrity_reports_available=True,
        feed_delta_reports_available=True,
        acknowledgments_persistent=True,
    )

    statuses = {signal.check: signal.status for signal in summary.signals}
    assert summary.status == "passed"
    assert set(statuses.values()) == {"passed"}
    assert summary.acknowledgments_persistent is True
    assert summary.acknowledgment_status_source == "persistent_store"


def test_alpaca_sip_quality_summary_builder_preserves_failed_report_status() -> None:
    summary = _build_alpaca_sip_quality_summary(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        ),
        integrity_report=QualityReportState(
            report_type="alpaca_sip_integrity",
            status="failed",
            raw_status="failed",
            content_hash="deadbeefcafebabe",
            start="2024-04-22T00:00:00+00:00",
            end="2024-04-26T00:00:00+00:00",
            timeframe="1Day",
        ),
        feed_delta_reports_available=True,
        acknowledgments_persistent=True,
    )

    integrity = next(signal for signal in summary.signals if signal.check == "alpaca_sip_integrity")
    assert summary.status == "failed"
    assert integrity.status == "failed"
    assert "alpaca_sip_integrity_report_failed" in integrity.reason_codes
    assert "alpaca_sip_integrity_hash:deadbeefcafebabe" in integrity.reason_codes
    assert "timeframe=1Day" in integrity.message


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_warns_on_pairing_warning() -> None:
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
            warnings=["alpaca_sip_companion_manifest_stale"],
        )
    )
    svc = DataQualityService(
        manifest_service=cast(DataManifestService, manifest_service),
        integrity_reports_available=True,
        feed_delta_reports_available=True,
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        summary = await svc.get_alpaca_sip_quality_summary(DummyUser(user_id="user-1"))

    pairing = next(signal for signal in summary.signals if signal.check == "manifest_pairing")
    assert summary.status == "warning"
    assert pairing.status == "warning"
    assert pairing.reason_codes == ["alpaca_sip_companion_manifest_stale"]


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_fails_missing_companion_manifest() -> None:
    manifest_service = FakeManifestService(
        _summary(
            [_manifest(ALPACA_SIP_DAILY_DATASET)],
            missing=[ALPACA_SIP_CORP_ACTIONS_DATASET],
        )
    )
    svc = DataQualityService(
        manifest_service=cast(DataManifestService, manifest_service),
        integrity_reports_available=True,
        feed_delta_reports_available=True,
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        summary = await svc.get_alpaca_sip_quality_summary(DummyUser(user_id="user-1"))

    pairing = next(signal for signal in summary.signals if signal.check == "manifest_pairing")
    assert summary.status == "failed"
    assert pairing.status == "failed"
    assert pairing.reason_codes == [
        f"alpaca_sip_missing_manifest:{ALPACA_SIP_CORP_ACTIONS_DATASET}"
    ]


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_fails_invalid_companion_manifest() -> None:
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET, validation_status="failed"),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        )
    )
    svc = DataQualityService(
        manifest_service=cast(DataManifestService, manifest_service),
        integrity_reports_available=True,
        feed_delta_reports_available=True,
    )

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        summary = await svc.get_alpaca_sip_quality_summary(DummyUser(user_id="user-1"))

    pairing = next(signal for signal in summary.signals if signal.check == "manifest_pairing")
    assert summary.status == "failed"
    assert pairing.status == "failed"
    assert pairing.reason_codes == [
        f"alpaca_sip_manifest_validation_failed:{ALPACA_SIP_DAILY_DATASET}"
    ]
