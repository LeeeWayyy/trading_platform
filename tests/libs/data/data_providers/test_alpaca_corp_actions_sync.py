"""Tests for Alpaca corporate-actions sync manager."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from libs.data.data_providers.alpaca_corp_actions_sync import (
    AlpacaCorporateActionsRestClient,
    AlpacaCorporateActionsSyncManager,
    CorporateActionRoundTripCheck,
)
from libs.data.data_quality.exceptions import DiskSpaceError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest
from libs.data.data_quality.types import DiskSpaceStatus


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


def test_rest_client_reuses_single_http_client_for_pages() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"corporate_actions": []}

    class FakeHTTPClient:
        def __init__(self) -> None:
            self.get_calls: list[tuple[str, dict[str, str | int]]] = []
            self.closed = False

        def get(self, path: str, params: dict[str, str | int]) -> FakeResponse:
            self.get_calls.append((path, dict(params)))
            return FakeResponse()

        def close(self) -> None:
            self.closed = True

    fake_http_client = FakeHTTPClient()
    with patch(
        "libs.data.data_providers.alpaca_corp_actions_sync.httpx.Client",
        return_value=fake_http_client,
    ) as client_factory:
        client = AlpacaCorporateActionsRestClient(api_key="key", secret_key="secret")
        client.get_corporate_actions({"limit": 1})
        client.get_corporate_actions({"limit": 2})
        client.close()

    assert client_factory.call_count == 1
    assert fake_http_client.get_calls == [
        ("/v1/corporate-actions", {"limit": 1}),
        ("/v1/corporate-actions", {"limit": 2}),
    ]
    assert fake_http_client.closed is True


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
    assert manifest.adjustment_mode == "raw"
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


def test_full_sync_chunks_large_symbol_requests(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {"corporate_actions": []},
            {"corporate_actions": []},
            {"corporate_actions": []},
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )
    manager.SYMBOL_REQUEST_CHUNK_SIZE = 2

    manager.full_sync(
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=["aapl", "MSFT", "NVDA", "TSLA", "GOOG"],
    )

    assert [request["symbols"] for request in client.requests] == [
        "AAPL,MSFT",
        "NVDA,TSLA",
        "GOOG",
    ]


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


def test_full_sync_flattens_grouped_dict_payloads(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": {
                    "cash_dividends": {
                        "ca-div-1": {
                            "id": "ca-div-1",
                            "symbol": "AAPL",
                            "process_date": "2024-02-15",
                            "rate": 0.24,
                        },
                        "ca-div-2": {
                            "id": "ca-div-2",
                            "symbol": "MSFT",
                            "process_date": "2024-03-15",
                            "rate": 0.75,
                        },
                    }
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
        symbols=["AAPL", "MSFT"],
    )

    df = pl.read_parquet(manifest.file_paths[0])
    assert df["id"].to_list() == ["ca-div-1", "ca-div-2"]
    assert df["ca_type"].to_list() == ["cash_dividends", "cash_dividends"]


def test_full_sync_flattens_grouped_dict_payloads_with_action_field_keys(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": {
                    "stock_splits": {
                        "symbol": {
                            "id": "ca-split-1",
                            "symbol": "AAPL",
                            "process_date": "2024-06-15",
                            "old_rate": 1.0,
                            "new_rate": 4.0,
                        }
                    }
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
    assert df["id"].to_list() == ["ca-split-1"]
    assert df["symbol"].to_list() == ["AAPL"]
    assert df["ca_type"].to_list() == ["stock_splits"]


def test_full_sync_flattens_nested_grouped_list_payloads(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": {
                    "cash_dividends": {
                        "AAPL": [
                            {
                                "id": "ca-div-1",
                                "symbol": "AAPL",
                                "process_date": "2024-02-15",
                                "rate": 0.24,
                            }
                        ],
                        "MSFT": [
                            {
                                "id": "ca-div-2",
                                "symbol": "MSFT",
                                "process_date": "2024-03-15",
                                "rate": 0.75,
                            }
                        ],
                    }
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
        symbols=["AAPL", "MSFT"],
    )

    df = pl.read_parquet(manifest.file_paths[0])
    assert df["id"].to_list() == ["ca-div-1", "ca-div-2"]
    assert df["ca_type"].to_list() == ["cash_dividends", "cash_dividends"]


@pytest.mark.parametrize("container_key", ["items", "results"])
def test_full_sync_reads_top_level_action_container_aliases(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    container_key: str,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                container_key: [
                    {
                        "id": f"ca-{container_key}",
                        "symbol": "AAPL",
                        "process_date": "2024-02-15",
                        "rate": 0.24,
                    }
                ]
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
    assert df["id"].to_list() == [f"ca-{container_key}"]


def test_full_sync_ignores_wrapper_scalars_when_flattening_items(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": {
                    "cash_dividends": {
                        "id": "page-summary-1",
                        "type": "response-wrapper",
                        "items": [
                            {
                                "id": "ca-div-1",
                                "symbol": "AAPL",
                                "process_date": "2024-02-15",
                                "rate": 0.24,
                            }
                        ],
                    }
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
    assert df["id"].to_list() == ["ca-div-1"]
    assert df["ca_type"].to_list() == ["cash_dividends"]


def test_full_sync_ignores_unexpected_grouped_scalars(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": {
                    "cash_dividends": {
                        "page": 1,
                        "metadata": {"count": 1},
                        "AAPL": [
                            {
                                "id": "ca-div-1",
                                "symbol": "AAPL",
                                "process_date": "2024-02-15",
                                "rate": 0.24,
                            }
                        ],
                    }
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
    assert df["id"].to_list() == ["ca-div-1"]
    assert df["ca_type"].to_list() == ["cash_dividends"]


def test_full_sync_deduplicates_present_ids_and_preserves_missing_ids(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": [
                    {
                        "id": "ca-1",
                        "symbol": "AAPL",
                        "process_date": "2024-02-01",
                        "cash": 0.1,
                    },
                    {
                        "symbol": "MSFT",
                        "process_date": "2024-02-01",
                        "cash": 0.2,
                    },
                    {
                        "id": "ca-1",
                        "symbol": "AAPL",
                        "process_date": "2024-02-02",
                        "cash": 0.3,
                    },
                    {
                        "symbol": "MSFT",
                        "process_date": "2024-02-02",
                        "cash": 0.4,
                    },
                ]
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
        symbols=["AAPL", "MSFT"],
    )

    df = pl.read_parquet(manifest.file_paths[0])
    assert df.height == 3
    assert df["id"].null_count() == 2
    row_with_id = df.filter(pl.col("id") == "ca-1")
    assert row_with_id["process_date"].to_list() == [datetime.date(2024, 2, 2)]
    assert row_with_id["cash"].to_list() == [0.3]


def test_fetch_all_pages_rejects_unbounded_pagination(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    client = FakeCorporateActionsClient(
        [
            {"corporate_actions": [], "next_page_token": "again"},
            {"corporate_actions": [], "next_page_token": "again"},
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )
    manager.MAX_PAGES_PER_REQUEST = 1

    with pytest.raises(RuntimeError, match="pagination exceeded"):
        manager._fetch_all_pages({"start": "2024-01-01", "end": "2024-12-31"})


def test_actions_from_payload_rejects_excessive_nesting() -> None:
    nested: dict[str, Any] = {"id": "ca-deep", "symbol": "AAPL", "process_date": "2024-02-15"}
    for _ in range(AlpacaCorporateActionsSyncManager.MAX_ACTION_NESTING_DEPTH + 1):
        nested = {"items": [nested]}

    with pytest.raises(ValueError, match="too deeply nested"):
        AlpacaCorporateActionsSyncManager._actions_from_payload(
            {"corporate_actions": {"cash_dividends": nested}}
        )


def test_full_sync_blocks_on_critical_disk_before_fetch(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeCorporateActionsClient([{"corporate_actions": [{"id": "ca-1"}]}])
    manager = AlpacaCorporateActionsSyncManager(
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
        manager.full_sync(
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 12, 31),
            symbols=["AAPL"],
        )

    assert client.requests == []


def test_pre_fetch_estimate_scales_unfiltered_syncs() -> None:
    estimate = AlpacaCorporateActionsSyncManager._estimate_pre_fetch_rows(
        start_date=datetime.date(2020, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=[],
        ca_types=[],
        ids=[],
    )

    assert estimate >= 250_000


def test_pre_fetch_estimate_scales_filtered_syncs() -> None:
    estimate = AlpacaCorporateActionsSyncManager._estimate_pre_fetch_rows(
        start_date=datetime.date(2020, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=[f"SYM{i}" for i in range(50)],
        ca_types=["cash_dividend", "stock_split", "merger", "spinoff", "name_change"],
        ids=[],
    )

    assert estimate >= 31_250


def test_pre_fetch_estimate_scales_id_requests_with_date_span() -> None:
    estimate = AlpacaCorporateActionsSyncManager._estimate_pre_fetch_rows(
        start_date=datetime.date(2020, 1, 1),
        end_date=datetime.date(2024, 12, 31),
        symbols=[],
        ca_types=[],
        ids=["ca-1", "ca-2"],
    )

    assert estimate > 2


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


def test_round_trip_checks_pass_known_matching_event(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    check = CorporateActionRoundTripCheck(
        label="AAPL split",
        symbol="AAPL",
        start_date=datetime.date(2020, 8, 1),
        end_date=datetime.date(2020, 9, 15),
        expected_type_tokens=("split",),
    )
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": [
                    {
                        "id": "split-1",
                        "symbol": "AAPL",
                        "ca_type": "stock_split",
                        "process_date": "2020-08-31",
                    }
                ]
            }
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    report = manager.run_round_trip_checks([check])

    assert report.status == "passed"
    assert report.results[0].matched_action_count == 1
    assert report.results[0].matched_ids == ("split-1",)
    assert report.results[0].matched_types == ("stock_split",)
    assert report.to_dict()["content_hash"] == report.content_hash
    assert client.requests[0]["symbols"] == "AAPL"


def test_round_trip_checks_fail_when_expected_type_missing(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    check = CorporateActionRoundTripCheck(
        label="AAPL split",
        symbol="AAPL",
        start_date=datetime.date(2020, 8, 1),
        end_date=datetime.date(2020, 9, 15),
        expected_type_tokens=("split",),
    )
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": [
                    {
                        "id": "div-1",
                        "symbol": "AAPL",
                        "ca_type": "cash_dividend",
                        "process_date": "2020-08-31",
                    }
                ]
            }
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    report = manager.run_round_trip_checks([check])

    assert report.status == "failed"
    assert report.results[0].raw_action_count == 1
    assert report.results[0].matched_action_count == 0


def test_round_trip_checks_ignore_tokens_outside_structured_type(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    check = CorporateActionRoundTripCheck(
        label="AAPL split",
        symbol="AAPL",
        start_date=datetime.date(2020, 8, 1),
        end_date=datetime.date(2020, 9, 15),
        expected_type_tokens=("split",),
    )
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": [
                    {
                        "id": "div-1",
                        "symbol": "AAPL",
                        "ca_type": "cash_dividend",
                        "process_date": "2020-08-31",
                        "description": "reverse split-off completed",
                    }
                ]
            }
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    report = manager.run_round_trip_checks([check])

    assert report.status == "failed"
    assert report.results[0].matched_action_count == 0


def test_round_trip_checks_match_any_structured_type_token(
    sync_paths: dict[str, Path],
    manifest_manager: ManifestManager,
) -> None:
    check = CorporateActionRoundTripCheck(
        label="AAPL cash dividend",
        symbol="AAPL",
        start_date=datetime.date(2024, 2, 1),
        end_date=datetime.date(2024, 2, 20),
        expected_type_tokens=("dividend", "cash"),
    )
    client = FakeCorporateActionsClient(
        [
            {
                "corporate_actions": [
                    {
                        "id": "div-1",
                        "symbol": "AAPL",
                        "ca_type": "dividend",
                        "process_date": "2024-02-15",
                    }
                ]
            }
        ]
    )
    manager = AlpacaCorporateActionsSyncManager(
        client=client,
        storage_path=sync_paths["storage"],
        manifest_manager=manifest_manager,
        data_root=sync_paths["data_root"],
    )

    report = manager.run_round_trip_checks([check])

    assert report.status == "passed"
    assert report.results[0].matched_action_count == 1


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
