"""Tests for manifest-backed data readiness service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import get_ident
from typing import cast

import pytest

from libs.web_console_services.data_manifest_service import (
    ALPACA_SIP_CORP_ACTIONS_DATASET,
    ALPACA_SIP_DAILY_DATASET,
    ALPACA_SIP_DATASET_KEY,
    AlpacaSipManifestSummaryDTO,
    DataManifestService,
    ManifestSummaryDTO,
)
from libs.web_console_services.data_readiness_service import (
    ALPACA_SIP_COMPANION_MANIFEST_STALE,
    ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH,
    ALPACA_SIP_MANIFEST_VALIDATION_FAILED,
    ALPACA_SIP_UNTRUSTED_WITHOUT_MANIFEST,
    CRSP_UNIVERSE_MANIFEST_DATASET,
    CRSP_UNIVERSE_UNAVAILABLE,
    HYBRID_CRSP_SIP_DATASET_KEY,
    HYBRID_PRICE_COMPONENT_BLOCKED,
    HYBRID_PRICE_COMPONENT_READY,
    RAW_SIP_RETURNS_UNAVAILABLE,
    DataReadinessService,
)
from libs.web_console_services.provider_signature import ProviderSignatureDTO

NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


@dataclass(frozen=True)
class DummyUser:
    user_id: str = "user-1"


class FakeManifestService:
    def __init__(
        self,
        summary: AlpacaSipManifestSummaryDTO,
        crsp_manifest: ManifestSummaryDTO | None = None,
    ) -> None:
        self._summary = summary
        self._crsp_manifest = crsp_manifest
        self.thread_ids: list[int] = []

    def get_alpaca_sip_summary(self) -> AlpacaSipManifestSummaryDTO:
        self.thread_ids.append(get_ident())
        return self._summary

    def get_manifest_summary(self, dataset: str) -> ManifestSummaryDTO | None:
        self.thread_ids.append(get_ident())
        if dataset == CRSP_UNIVERSE_MANIFEST_DATASET:
            return self._crsp_manifest
        return None


def _manifest(
    dataset: str,
    *,
    validation_status: str = "passed",
    read_time_adjustment_mode: str | None = None,
) -> ManifestSummaryDTO:
    is_daily = dataset == ALPACA_SIP_DAILY_DATASET
    provider_id = "crsp" if dataset == CRSP_UNIVERSE_MANIFEST_DATASET else "alpaca_sip"
    source_feed = None if dataset == CRSP_UNIVERSE_MANIFEST_DATASET else "sip"
    return ManifestSummaryDTO(
        dataset=dataset,
        manifest_reference=f"manifests://{dataset}.json",
        manifest_id=f"{dataset}@v1:checksum",
        manifest_checksum=f"{dataset}-checksum",
        manifest_version=1,
        schema_version="v1",
        validation_status=validation_status,
        sync_timestamp=NOW,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        row_count=10,
        file_count=1,
        provider_id=provider_id,
        provider_version="2026.04",
        source_feed=source_feed,
        adjustment_mode="raw" if is_daily else None,
        canonical_storage_mode="raw" if is_daily else None,
        read_time_adjustment_mode=(
            read_time_adjustment_mode
            if read_time_adjustment_mode is not None
            else "unavailable"
            if is_daily
            else None
        ),
        provider_signature=ProviderSignatureDTO(
            provider_id=provider_id,
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
        source_error_rate_pct=0.0 if statuses == ["passed"] and not missing else 100.0,
        warnings=warnings or [],
    )


def test_alpaca_sip_simple_backtest_blocks_missing_manifests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [],
        missing=[ALPACA_SIP_DAILY_DATASET, ALPACA_SIP_CORP_ACTIONS_DATASET],
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    readiness = service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "simple_backtest")

    assert readiness.status == "blocked"
    assert ALPACA_SIP_UNTRUSTED_WITHOUT_MANIFEST in readiness.blockers
    assert RAW_SIP_RETURNS_UNAVAILABLE not in readiness.blockers


def test_alpaca_sip_readiness_uses_distinct_code_for_invalid_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, validation_status="failed"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ],
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    readiness = service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "quality_analysis")

    assert readiness.status == "blocked"
    assert ALPACA_SIP_MANIFEST_VALIDATION_FAILED in readiness.blockers
    assert ALPACA_SIP_UNTRUSTED_WITHOUT_MANIFEST not in readiness.blockers


def test_alpaca_sip_readiness_exposes_pairing_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ],
        warnings=[
            ALPACA_SIP_COMPANION_MANIFEST_STALE,
            ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH,
        ],
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    readiness = service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "quality_analysis")

    assert readiness.status == "warning"
    assert ALPACA_SIP_COMPANION_MANIFEST_STALE in readiness.warnings
    assert ALPACA_SIP_COMPANION_SYMBOL_SET_MISMATCH in readiness.warnings


def test_alpaca_sip_simple_backtest_blocks_pairing_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ],
        warnings=[ALPACA_SIP_COMPANION_MANIFEST_STALE],
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    readiness = service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "simple_backtest")

    assert readiness.status == "blocked"
    assert ALPACA_SIP_COMPANION_MANIFEST_STALE in readiness.blockers
    assert RAW_SIP_RETURNS_UNAVAILABLE not in readiness.blockers


def test_alpaca_sip_simple_backtest_ready_when_adjustment_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ],
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    readiness = service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "simple_backtest")

    assert readiness.status == "ready"
    assert RAW_SIP_RETURNS_UNAVAILABLE not in readiness.blockers


@pytest.mark.asyncio()
async def test_readiness_async_offloads_manifest_io(monkeypatch: pytest.MonkeyPatch) -> None:
    main_thread_id = get_ident()
    manifest_service = FakeManifestService(
        _summary(
            [
                _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
                _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
            ],
        )
    )
    service = DataReadinessService(manifest_service=cast(DataManifestService, manifest_service))
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    readiness = await service.get_readiness_async(
        DummyUser(),
        ALPACA_SIP_DATASET_KEY,
        "simple_backtest",
    )

    assert readiness.status == "ready"
    assert manifest_service.thread_ids
    assert all(thread_id != main_thread_id for thread_id in manifest_service.thread_ids)


@pytest.mark.asyncio()
async def test_readiness_sync_api_rejects_async_context() -> None:
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(_summary([])))
    )

    with pytest.raises(RuntimeError, match="use get_readiness_async"):
        service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "simple_backtest")


def test_readiness_rejects_unsupported_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(_summary([])))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip"})

    with pytest.raises(ValueError, match="Unsupported readiness dataset"):
        service.get_readiness(DummyUser(), "unsupported", "simple_backtest")


def test_alpaca_sip_readiness_requires_sync_view_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(_summary([])))
    )
    monkeypatch.setattr(
        "libs.web_console_services.data_readiness_service.has_permission",
        lambda _user, _permission: False,
    )
    monkeypatch.setattr(
        "libs.web_console_services.data_readiness_service.has_dataset_permission",
        lambda _user, _dataset: True,
    )

    with pytest.raises(PermissionError, match="view_data_sync"):
        service.get_readiness(DummyUser(), ALPACA_SIP_DATASET_KEY, "simple_backtest")


def test_hybrid_readiness_requires_crsp_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {HYBRID_CRSP_SIP_DATASET_KEY})

    readiness = service.get_readiness(
        DummyUser(),
        HYBRID_CRSP_SIP_DATASET_KEY,
        "hybrid_research_backtest",
    )

    assert readiness.status == "blocked"
    assert CRSP_UNIVERSE_UNAVAILABLE in readiness.blockers
    assert HYBRID_PRICE_COMPONENT_BLOCKED in readiness.blockers
    assert RAW_SIP_RETURNS_UNAVAILABLE not in readiness.blockers


def test_hybrid_readiness_requires_hybrid_dataset_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )
    service = DataReadinessService(
        manifest_service=cast(DataManifestService, FakeManifestService(summary))
    )
    _allow_readiness(monkeypatch, {"alpaca_sip", "crsp"})

    with pytest.raises(PermissionError, match=HYBRID_CRSP_SIP_DATASET_KEY):
        service.get_readiness(
            DummyUser(),
            HYBRID_CRSP_SIP_DATASET_KEY,
            "hybrid_research_backtest",
        )


def test_hybrid_readiness_treats_composite_permission_as_sufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )
    service = DataReadinessService(
        manifest_service=cast(
            DataManifestService,
            FakeManifestService(summary, _manifest(CRSP_UNIVERSE_MANIFEST_DATASET)),
        )
    )
    _allow_readiness(monkeypatch, {HYBRID_CRSP_SIP_DATASET_KEY})

    readiness = service.get_readiness(
        DummyUser(),
        HYBRID_CRSP_SIP_DATASET_KEY,
        "hybrid_research_backtest",
    )

    assert readiness.status == "ready"
    assert HYBRID_PRICE_COMPONENT_READY not in readiness.blockers
    assert CRSP_UNIVERSE_UNAVAILABLE not in readiness.blockers
    assert RAW_SIP_RETURNS_UNAVAILABLE not in readiness.blockers
    assert [check.code for check in readiness.checks if check.source == "manifest"] == [
        HYBRID_PRICE_COMPONENT_READY,
        "crsp_universe_available",
    ]


def test_hybrid_readiness_blocks_stale_price_component_for_return_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ],
        warnings=[ALPACA_SIP_COMPANION_MANIFEST_STALE],
    )
    service = DataReadinessService(
        manifest_service=cast(
            DataManifestService,
            FakeManifestService(summary, _manifest(CRSP_UNIVERSE_MANIFEST_DATASET)),
        )
    )
    _allow_readiness(monkeypatch, {HYBRID_CRSP_SIP_DATASET_KEY})

    readiness = service.get_readiness(
        DummyUser(),
        HYBRID_CRSP_SIP_DATASET_KEY,
        "hybrid_research_backtest",
    )

    assert readiness.status == "blocked"
    assert readiness.blockers == [HYBRID_PRICE_COMPONENT_BLOCKED]


def test_hybrid_readiness_scrubs_sip_details_without_direct_sip_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )
    service = DataReadinessService(
        manifest_service=cast(
            DataManifestService,
            FakeManifestService(summary, _manifest(CRSP_UNIVERSE_MANIFEST_DATASET)),
        )
    )
    _allow_readiness(monkeypatch, {HYBRID_CRSP_SIP_DATASET_KEY})

    readiness = service.get_readiness(
        DummyUser(),
        HYBRID_CRSP_SIP_DATASET_KEY,
        "hybrid_research_backtest",
    )

    assert readiness.status == "blocked"
    assert readiness.blockers == [HYBRID_PRICE_COMPONENT_BLOCKED]
    assert all("alpaca_sip" not in check.code for check in readiness.checks)


def test_hybrid_readiness_uses_crsp_daily_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _summary(
        [
            _manifest(ALPACA_SIP_DAILY_DATASET, read_time_adjustment_mode="available"),
            _manifest(ALPACA_SIP_CORP_ACTIONS_DATASET),
        ]
    )
    service = DataReadinessService(
        manifest_service=cast(
            DataManifestService,
            FakeManifestService(summary, _manifest(CRSP_UNIVERSE_MANIFEST_DATASET)),
        )
    )
    _allow_readiness(monkeypatch, {"alpaca_sip", "crsp", HYBRID_CRSP_SIP_DATASET_KEY})

    readiness = service.get_readiness(
        DummyUser(),
        HYBRID_CRSP_SIP_DATASET_KEY,
        "hybrid_research_backtest",
    )

    assert readiness.status == "ready"
    assert CRSP_UNIVERSE_UNAVAILABLE not in readiness.blockers
    assert RAW_SIP_RETURNS_UNAVAILABLE not in readiness.blockers


def _allow_readiness(monkeypatch: pytest.MonkeyPatch, datasets: set[str]) -> None:
    monkeypatch.setattr(
        "libs.web_console_services.data_readiness_service.has_permission",
        lambda _user, _permission: True,
    )
    monkeypatch.setattr(
        "libs.web_console_services.data_readiness_service.has_dataset_permission",
        lambda _user, dataset: dataset in datasets,
    )
