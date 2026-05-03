"""Tests for Alpaca SIP bulk sync manager."""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from libs.data.data_providers.alpaca_sip_sync import AlpacaSIPSyncManager
from libs.data.data_quality.exceptions import DiskSpaceError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest
from libs.data.data_quality.types import DiskSpaceStatus


class FakeBar:
    """Minimal Alpaca SDK bar double."""

    def __init__(
        self,
        *,
        symbol: str,
        timestamp: datetime.datetime,
        close: float,
        volume: float = 1000.0,
    ) -> None:
        self.symbol = symbol
        self.timestamp = timestamp
        self.open = close - 1
        self.high = close + 1
        self.low = close - 2
        self.close = close
        self.volume = volume
        self.trade_count = 10.0
        self.vwap = close - 0.25


class FakeResponse:
    """Minimal BarSet response double."""

    def __init__(self, data: dict[str, list[Any]], next_page_token: str | None = None) -> None:
        self.data = data
        self.next_page_token = next_page_token


class FakeAlpacaClient:
    """Request-recording fake Alpaca client."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    def get_stock_bars(self, request_params: Any) -> Any:
        self.requests.append(request_params)
        if not self.responses:
            return FakeResponse({})
        return self.responses.pop(0)


class RaisingAlpacaClient:
    """Fake client that fails before returning a partition."""

    def get_stock_bars(self, request_params: Any) -> Any:
        raise RuntimeError("alpaca unavailable")


@pytest.fixture()
def sync_paths(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "data"
    paths = {
        "data_root": data_root,
        "storage": data_root / "alpaca" / "sip" / "daily",
        "manifests": data_root / "manifests",
        "locks": data_root / "locks",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


@pytest.fixture()
def manifest_manager(sync_paths: dict[str, Path]) -> ManifestManager:
    return ManifestManager(
        storage_path=sync_paths["manifests"],
        lock_dir=sync_paths["locks"],
        data_root=sync_paths["data_root"],
    )


def _save_manifest(
    manifest_manager: ManifestManager,
    *,
    file_paths: list[str],
    checksum: str = "old-checksum",
    row_count: int = 1,
) -> SyncManifest:
    manifest = SyncManifest(
        dataset="alpaca_sip_daily",
        sync_timestamp=datetime.datetime.now(datetime.UTC),
        start_date=datetime.date(2024, 1, 3),
        end_date=datetime.date(2024, 1, 3),
        row_count=row_count,
        checksum=checksum,
        schema_version="v1.0.0",
        wrds_query_hash="alpaca-sip-test",
        file_paths=file_paths,
        validation_status="passed",
    )
    with manifest_manager.acquire_lock("alpaca_sip_daily", writer_id="test") as token:
        manifest_manager.save_manifest(manifest, token)
    saved = manifest_manager.load_manifest("alpaca_sip_daily")
    assert saved is not None
    return saved


def test_full_sync_writes_partition_and_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    timestamp = datetime.datetime(2024, 1, 3, tzinfo=datetime.UTC)
    client = FakeAlpacaClient(
        [
            FakeResponse({"AAPL": [FakeBar(symbol="AAPL", timestamp=timestamp, close=100.0)]}),
            FakeResponse({"MSFT": [FakeBar(symbol="MSFT", timestamp=timestamp, close=200.0)]}),
        ]
    )
    manager = AlpacaSIPSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
        request_chunk_size=1,
    )

    manifest = manager.full_sync(["aapl", "MSFT"], start_year=2024, end_year=2024)

    assert not (sync_paths["storage"] / "2024.parquet").exists()
    assert len(manifest.file_paths) == 1
    partition = sync_paths["data_root"] / manifest.file_paths[0]
    assert partition.exists()
    assert partition.parent.parent == sync_paths["storage"] / "snapshots"
    assert partition.name == "2024.parquet"
    df = pl.read_parquet(partition)
    assert df["symbol"].to_list() == ["AAPL", "MSFT"]
    assert df["adj_close"].to_list() == [None, None]
    assert df["ret"].null_count() == 2
    assert manifest.row_count == 2
    assert manifest.start_date == datetime.date(2024, 1, 3)
    assert manifest.end_date == datetime.date(2024, 1, 3)
    assert manifest.file_paths == [str(partition.relative_to(sync_paths["data_root"]))]
    assert manifest.provider_id == "alpaca_sip"
    assert manifest.provider_version == "1.0"
    assert manifest.source_feed == "sip"
    assert manifest.adjustment_mode == "raw"
    assert manifest.manifest_id == f"alpaca_sip_daily@v1:{manifest.checksum}"
    assert manifest.symbol_set_hash is not None
    assert manifest.sync_started_at is not None
    assert manifest.sync_finished_at is not None

    saved_manifest = manifest_manager.load_manifest("alpaca_sip_daily")
    assert saved_manifest is not None
    assert saved_manifest.row_count == 2
    assert saved_manifest.file_paths == [str(partition.relative_to(sync_paths["data_root"]))]

    assert len(client.requests) == 2
    first_request_fields = client.requests[0].to_request_fields()
    assert first_request_fields["symbol_or_symbols"] == ["AAPL"]
    assert first_request_fields["feed"].value == "sip"
    assert first_request_fields["adjustment"].value == "raw"


def test_manager_rejects_non_raw_canonical_adjustment(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    with pytest.raises(ValueError, match="requires adjustment='raw'"):
        AlpacaSIPSyncManager(
            client=FakeAlpacaClient([]),
            storage_path=sync_paths["storage"],
            manifest_manager=manifest_manager,
            data_root=sync_paths["data_root"],
            adjustment="all",
        )


def test_full_sync_blocks_on_critical_disk_status(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeAlpacaClient([])
    manager = AlpacaSIPSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    def critical_space(_required_bytes: int) -> DiskSpaceStatus:
        return DiskSpaceStatus(
            level="critical",
            free_bytes=10,
            total_bytes=100,
            used_pct=0.90,
            message="critical test disk status",
        )

    monkeypatch.setattr(manifest_manager, "check_disk_space", critical_space)

    with pytest.raises(DiskSpaceError, match="critical test disk status"):
        manager.full_sync(["AAPL"], start_year=2024, end_year=2024)

    assert client.requests == []


def test_sync_year_partition_blocks_on_critical_disk_before_write(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = datetime.datetime(2024, 1, 3, tzinfo=datetime.UTC)
    client = FakeAlpacaClient(
        [FakeResponse({"AAPL": [FakeBar(symbol="AAPL", timestamp=timestamp, close=100.0)]})]
    )
    manager = AlpacaSIPSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    def critical_space(_required_bytes: int) -> DiskSpaceStatus:
        return DiskSpaceStatus(
            level="critical",
            free_bytes=10,
            total_bytes=100,
            used_pct=0.90,
            message="critical partition disk status",
        )

    monkeypatch.setattr(manifest_manager, "check_disk_space", critical_space)

    with pytest.raises(DiskSpaceError, match="critical partition disk status"):
        manager.sync_year_partition(["AAPL"], 2024)

    assert client.requests == []
    assert not (sync_paths["storage"] / "2024.parquet").exists()


def test_full_sync_failure_does_not_replace_existing_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    old_snapshot = sync_paths["storage"] / "snapshots" / "old" / "2024.parquet"
    old_snapshot.parent.mkdir(parents=True)
    pl.DataFrame({"date": [datetime.date(2024, 1, 3)], "symbol": ["AAPL"]}).write_parquet(
        old_snapshot
    )
    old_manifest = _save_manifest(
        manifest_manager,
        file_paths=[str(old_snapshot)],
        checksum="old-checksum",
    )
    manager = AlpacaSIPSyncManager(
        client=RaisingAlpacaClient(),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    with pytest.raises(RuntimeError, match="alpaca unavailable"):
        manager.full_sync(["AAPL"], start_year=2024, end_year=2024)

    current_manifest = manifest_manager.load_manifest("alpaca_sip_daily")
    assert current_manifest is not None
    assert current_manifest.manifest_version == old_manifest.manifest_version
    assert current_manifest.checksum == "old-checksum"
    assert current_manifest.file_paths == [str(old_snapshot)]
    assert old_snapshot.exists()


def test_full_sync_with_no_rows_does_not_update_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    old_snapshot = sync_paths["storage"] / "snapshots" / "old" / "2024.parquet"
    old_snapshot.parent.mkdir(parents=True)
    pl.DataFrame({"date": [datetime.date(2024, 1, 3)], "symbol": ["AAPL"]}).write_parquet(
        old_snapshot
    )
    old_manifest = _save_manifest(
        manifest_manager,
        file_paths=[str(old_snapshot)],
        checksum="old-checksum",
    )
    manager = AlpacaSIPSyncManager(
        client=FakeAlpacaClient([FakeResponse({})]),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    with pytest.raises(ValueError, match="No Alpaca SIP rows returned"):
        manager.full_sync(["AAPL"], start_year=2024, end_year=2024)

    current_manifest = manifest_manager.load_manifest("alpaca_sip_daily")
    assert current_manifest is not None
    assert current_manifest.manifest_version == old_manifest.manifest_version
    assert current_manifest.checksum == "old-checksum"
    assert current_manifest.file_paths == [str(old_snapshot)]


def test_sync_year_accepts_raw_dict_response_and_deduplicates(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeAlpacaClient(
        [
            {
                "AAPL": [
                    {
                        "t": "2024-01-03T00:00:00Z",
                        "o": 99.0,
                        "h": 101.0,
                        "l": 98.0,
                        "c": 100.0,
                        "v": 1000.0,
                        "n": 10.0,
                        "vw": 100.1,
                    },
                    {
                        "t": "2024-01-03T00:00:00Z",
                        "o": 100.0,
                        "h": 102.0,
                        "l": 99.0,
                        "c": 101.0,
                        "v": 1100.0,
                        "n": 11.0,
                        "vw": 101.1,
                    },
                ]
            }
        ]
    )
    manager = AlpacaSIPSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    partition = manager.sync_year_partition(["AAPL"], 2024)

    df = pl.read_parquet(partition.path)
    assert partition.row_count == 1
    assert df["close"].to_list() == [101.0]
    assert df["volume"].to_list() == [1100.0]


def test_sync_year_partition_fetches_paginated_bar_responses(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    class PagedSyncManager(AlpacaSIPSyncManager):
        def __init__(self) -> None:
            super().__init__(
                client=FakeAlpacaClient([]),
                storage_path=sync_paths["storage"],
                manifest_manager=manifest_manager,
                data_root=sync_paths["data_root"],
            )
            self.page_tokens: list[str | None] = []

        def _fetch_bars(
            self,
            symbols: Sequence[str],
            year: int,
            *,
            page_token: str | None = None,
        ) -> FakeResponse:
            assert list(symbols) == ["AAPL"]
            assert year == 2024
            self.page_tokens.append(page_token)
            if page_token is None:
                return FakeResponse(
                    {
                        "AAPL": [
                            FakeBar(
                                symbol="AAPL",
                                timestamp=datetime.datetime(2024, 1, 3, tzinfo=datetime.UTC),
                                close=100.0,
                            )
                        ]
                    },
                    next_page_token="page-2",
                )
            assert page_token == "page-2"
            return FakeResponse(
                {
                    "AAPL": [
                        FakeBar(
                            symbol="AAPL",
                            timestamp=datetime.datetime(2024, 1, 4, tzinfo=datetime.UTC),
                            close=101.0,
                        )
                    ]
                }
            )

    manager = PagedSyncManager()

    partition = manager.sync_year_partition(["AAPL"], 2024)

    df = pl.read_parquet(partition.path)
    assert manager.page_tokens == [None, "page-2"]
    assert partition.row_count == 2
    assert df["date"].to_list() == [datetime.date(2024, 1, 3), datetime.date(2024, 1, 4)]
    assert df["close"].to_list() == [100.0, 101.0]


def test_sync_year_partition_rejects_unbounded_pagination(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    class LoopingSyncManager(AlpacaSIPSyncManager):
        MAX_PAGES_PER_REQUEST = 1

        def _fetch_bars(
            self,
            symbols: Sequence[str],
            year: int,
            *,
            page_token: str | None = None,
        ) -> FakeResponse:
            assert list(symbols) == ["AAPL"]
            assert year == 2024
            return FakeResponse({"AAPL": []}, next_page_token="again")

    manager = LoopingSyncManager(
        client=FakeAlpacaClient([]),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    with pytest.raises(RuntimeError, match="pagination exceeded"):
        manager.sync_year_partition(["AAPL"], 2024)


def test_full_sync_rejects_empty_symbols(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    manager = AlpacaSIPSyncManager(
        client=FakeAlpacaClient([]),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    with pytest.raises(ValueError, match="symbols"):
        manager.full_sync([" ", ""], start_year=2024, end_year=2024)


def test_estimate_full_sync_counts_requests_rows_and_rate_limit_floor() -> None:
    estimate = AlpacaSIPSyncManager.estimate_full_sync(
        ["aapl", "MSFT", "aapl"],
        start_year=2023,
        end_year=2024,
        request_chunk_size=1,
        request_interval_seconds=0.5,
        requests_per_minute=120,
    )

    assert estimate.symbol_count == 2
    assert estimate.year_count == 2
    assert estimate.request_count == 4
    assert estimate.estimated_rows == 2 * 2 * 252
    assert estimate.estimated_storage_bytes == estimate.estimated_rows * 160
    assert estimate.estimated_throttle_seconds == 2.0
    assert estimate.estimated_rate_limit_floor_seconds == 2.0
    assert estimate.estimated_total_seconds == 2.0
    assert len(estimate.symbol_set_hash) == 64
    assert estimate.to_dict()["estimated_total_minutes"] == round(2.0 / 60.0, 4)


def test_estimate_full_sync_rejects_invalid_rate_limit() -> None:
    with pytest.raises(ValueError, match="requests_per_minute"):
        AlpacaSIPSyncManager.estimate_full_sync(
            ["AAPL"],
            start_year=2024,
            end_year=2024,
            requests_per_minute=0,
        )


def test_storage_path_must_be_under_data_root(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="within data_root"):
        AlpacaSIPSyncManager(
            client=FakeAlpacaClient([]),
            storage_path=outside,
            manifest_manager=manifest_manager,
            data_root=sync_paths["data_root"],
        )


def test_verify_integrity_detects_clean_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    timestamp = datetime.datetime(2024, 1, 3, tzinfo=datetime.UTC)
    client = FakeAlpacaClient(
        [FakeResponse({"AAPL": [FakeBar(symbol="AAPL", timestamp=timestamp, close=100.0)]})]
    )
    manager = AlpacaSIPSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )
    manager.full_sync(["AAPL"], start_year=2024, end_year=2024)

    assert manager.verify_integrity() == []


def test_verify_integrity_reports_checksum_mismatch(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    timestamp = datetime.datetime(2024, 1, 3, tzinfo=datetime.UTC)
    client = FakeAlpacaClient(
        [FakeResponse({"AAPL": [FakeBar(symbol="AAPL", timestamp=timestamp, close=100.0)]})]
    )
    manager = AlpacaSIPSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )
    manifest = manager.full_sync(["AAPL"], start_year=2024, end_year=2024)
    partition = sync_paths["data_root"] / manifest.file_paths[0]
    pl.DataFrame({"bad": [1]}).write_parquet(partition)

    errors = manager.verify_integrity()

    assert any("Checksum mismatch" in error for error in errors)


def test_verify_integrity_rejects_manifest_path_outside_storage_path(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.parquet"
    pl.DataFrame({"date": [datetime.date(2024, 1, 3)], "symbol": ["AAPL"]}).write_parquet(outside)
    _save_manifest(manifest_manager, file_paths=[str(outside)])
    manager = AlpacaSIPSyncManager(
        client=FakeAlpacaClient([]),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    errors = manager.verify_integrity()

    assert any("outside storage_path" in error for error in errors)
