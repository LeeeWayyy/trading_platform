"""Tests for Alpaca corporate-actions sync manager."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from libs.data.data_providers.alpaca_corp_actions_sync import (
    AlpacaCorporateActionsSyncManager,
)
from libs.data.data_quality.manifest import ManifestManager, SyncManifest


class FakeCorporateActionsClient:
    """Request-recording fake corporate-actions client."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, str | int]] = []

    def get_corporate_actions(self, params: dict[str, str | int]) -> dict[str, Any]:
        self.requests.append(dict(params))
        if not self.responses:
            return {"corporate_actions": []}
        return self.responses.pop(0)


@pytest.fixture()
def sync_paths(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "data"
    paths = {
        "data_root": data_root,
        "storage": data_root / "alpaca" / "sip" / "corp_actions",
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
        dataset="alpaca_sip_corp_actions",
        sync_timestamp=datetime.datetime.now(datetime.UTC),
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        row_count=row_count,
        checksum=checksum,
        schema_version="v1.0.0",
        wrds_query_hash="alpaca-corp-actions-test",
        file_paths=file_paths,
        validation_status="passed",
    )
    with manifest_manager.acquire_lock("alpaca_sip_corp_actions", writer_id="test") as token:
        manifest_manager.save_manifest(manifest, token)
    saved = manifest_manager.load_manifest("alpaca_sip_corp_actions")
    assert saved is not None
    return saved


def test_full_sync_writes_corporate_actions_and_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": [
                    {
                        "id": "ca-1",
                        "symbol": "aapl",
                        "type": "split",
                        "process_date": "2024-02-01",
                        "ex_date": "2024-02-02",
                        "old_rate": "1",
                        "new_rate": "4",
                    }
                ],
                "next_page_token": "page-2",
            },
            {
                "corporate_actions": [
                    {
                        "id": "ca-2",
                        "symbol": "MSFT",
                        "type": "dividend",
                        "process_date": "2024-03-01",
                        "payable_date": "2024-03-15",
                        "cash": "0.75",
                    }
                ]
            },
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
        limit=50,
    )

    manifest = manager.full_sync(
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=["aapl", "MSFT", "AAPL"],
        ca_types=["split", "dividend"],
    )

    assert manifest.dataset == "alpaca_sip_corp_actions"
    assert manifest.row_count == 2
    assert len(manifest.file_paths) == 1
    assert manifest.provider_id == "alpaca_sip"
    assert manifest.provider_version == "1.0"
    assert manifest.source_feed == "sip"
    assert manifest.adjustment_mode == "all"
    assert manifest.manifest_id == f"alpaca_sip_corp_actions@v1:{manifest.checksum}"
    assert manifest.symbol_set_hash is not None
    assert manifest.sync_started_at is not None
    assert manifest.sync_finished_at is not None
    partition = Path(manifest.file_paths[0])
    assert partition.exists()
    assert partition.parent.parent == sync_paths["storage"] / "snapshots"

    df = pl.read_parquet(partition)
    assert df["id"].to_list() == ["ca-1", "ca-2"]
    assert df["symbol"].to_list() == ["AAPL", "MSFT"]
    assert df["ca_type"].to_list() == ["split", "dividend"]
    assert df["new_rate"].to_list() == [4.0, None]
    assert '"symbol": "aapl"' in df["raw"][0]

    saved_manifest = manifest_manager.load_manifest("alpaca_sip_corp_actions")
    assert saved_manifest is not None
    assert saved_manifest.file_paths == [str(partition)]

    assert client.requests[0]["symbols"] == "AAPL,MSFT"
    assert client.requests[0]["types"] == "split,dividend"
    assert client.requests[0]["start"] == "2024-01-01"
    assert client.requests[0]["end"] == "2024-12-31"
    assert client.requests[0]["limit"] == 50
    assert client.requests[0]["sort"] == "asc"
    assert client.requests[1]["page_token"] == "page-2"


def test_full_sync_allows_empty_result_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient([{"corporate_actions": []}])
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    manifest = manager.full_sync(
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=["AAPL"],
    )

    assert manifest.row_count == 0
    assert Path(manifest.file_paths[0]).exists()
    assert pl.read_parquet(manifest.file_paths[0]).is_empty()


def test_full_sync_preserves_grouped_live_payload_types(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": {
                    "cash_dividends": [
                        {
                            "id": "ca-div-1",
                            "symbol": "AAPL",
                            "process_date": "2024-02-15",
                            "ex_date": "2024-02-09",
                            "record_date": "2024-02-12",
                            "payable_date": "2024-02-15",
                            "rate": 0.24,
                        }
                    ]
                }
            }
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    manifest = manager.full_sync(
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=["AAPL"],
    )

    df = pl.read_parquet(manifest.file_paths[0])
    assert df["ca_type"].to_list() == ["cash_dividends"]
    assert df["cash"].to_list() == [0.24]
    assert '"rate": 0.24' in df["raw"][0]


def test_full_sync_rejects_ids_with_filters(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    manager = AlpacaCorporateActionsSyncManager(
        client=FakeCorporateActionsClient([]),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    with pytest.raises(ValueError, match="ids cannot be combined"):
        manager.full_sync(
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 12, 31),
            symbols=["AAPL"],
            ids=["ca-1"],
        )


def test_verify_integrity_passes_for_current_manifest(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [{"corporate_actions": [{"id": "ca-1", "symbol": "AAPL", "process_date": "2024-01-02"}]}]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )
    manager.full_sync(
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=["AAPL"],
    )

    assert manager.verify_integrity() == []


def test_verify_integrity_rejects_manifest_path_outside_storage(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.parquet"
    pl.DataFrame({"id": ["bad"]}).write_parquet(outside)
    _save_manifest(manifest_manager, file_paths=[str(outside)])
    manager = AlpacaCorporateActionsSyncManager(
        client=FakeCorporateActionsClient([]),
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    errors = manager.verify_integrity()

    assert len(errors) == 1
    assert "outside storage_path" in errors[0]
