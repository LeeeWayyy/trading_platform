"""Unit tests for libs.web_console_services.data_quality_service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import get_ident
from typing import cast
from unittest.mock import patch

import pytest

from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
    ManifestSummaryDTO,
)
from libs.web_console_services.data_quality_service import (
    DataQualityService,
    _build_alpaca_sip_quality_summary,
)
from libs.web_console_services.provider_signature import ProviderSignatureDTO


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
async def test_acknowledge_alert_idempotent(service: DataQualityService) -> None:
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
    ):
        first = await service.acknowledge_alert(
            DummyUser(user_id="user-1"), alert_id="alert-1", reason="triage"
        )
        second = await service.acknowledge_alert(
            DummyUser(user_id="user-1"), alert_id="alert-1", reason="ignore"
        )

    assert first.id == second.id
    assert second.reason == "triage"
    assert second.acknowledged_by == "user-1"


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

    assert [result.validation_type for result in results] == ["manifest_summary"]
    assert results[0].status == "error"
    assert results[0].actual_value == "missing"
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

    by_dataset = {result.dataset: result for result in results}
    assert set(by_dataset) == {"crsp", "alpaca_sip"}
    assert by_dataset["crsp"].status == "ok"
    assert by_dataset["alpaca_sip"].validation_type == "manifest_summary"
    assert by_dataset["alpaca_sip"].status == "ok"


@pytest.mark.asyncio()
async def test_get_validation_results_degrades_when_alpaca_manifest_unavailable() -> None:
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
        results = await svc.get_validation_results(DummyUser(user_id="user-1"), dataset=None)

    by_dataset = {result.dataset: result for result in results}
    assert set(by_dataset) == {"crsp", "alpaca_sip"}
    assert by_dataset["crsp"].status == "ok"
    assert by_dataset["alpaca_sip"].status == "unavailable"
    assert by_dataset["alpaca_sip"].actual_value == "unavailable"
    assert by_dataset["alpaca_sip"].error_message == "Alpaca SIP manifest summary unavailable."
    assert manifest_service.thread_ids
    assert all(thread_id != main_thread_id for thread_id in manifest_service.thread_ids)


@pytest.mark.asyncio()
async def test_alpaca_sip_quality_summary_marks_unavailable_report_inputs() -> None:
    main_thread_id = get_ident()
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
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
