"""Tests for CRSP Local Data Provider."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from libs.data_providers.crsp_local_provider import (
    CRSP_COLUMNS,
    CRSP_SCHEMA,
    VALID_COLUMNS,
    AmbiguousTickerError,
    CRSPLocalProvider,
    ManifestVersionChangedError,
)
from libs.data_quality.exceptions import DataNotFoundError
from libs.data_quality.manifest import ManifestManager, SyncManifest


@pytest.fixture
def mock_crsp_data(tmp_path: Path) -> tuple[Path, ManifestManager, list[Path]]:
    """Create mock CRSP parquet files and manifest for testing.

    Creates data/wrds/crsp/daily/ structure with:
    - PERMNO 10001: AAPL, trades 2020-01-02 to 2022-12-30
    - PERMNO 10002: DELISTED, trades 2020-01-02 to 2021-12-15 (delisted)
    - PERMNO 10003: NEWIPO, trades 2022-01-03 to 2022-12-30 (IPO in 2022)
    - PERMNO 10004: RENAMED -> NEWNAME, trades 2020-01-02 to 2022-12-30 (ticker changed mid-2021)
    - PERMNO 10005: RENAMED (same ticker as 10004 during overlap period)
    """
    # Create directory structure
    data_root = tmp_path / "data"
    crsp_dir = data_root / "wrds" / "crsp" / "daily"
    crsp_dir.mkdir(parents=True)

    # Create manifest directory
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    lock_dir = data_root / "locks"
    lock_dir.mkdir(parents=True)

    file_paths: list[Path] = []

    # Generate 2020 data
    data_2020 = {
        "date": [
            date(2020, 1, 2),
            date(2020, 1, 2),
            date(2020, 1, 2),
            date(2020, 1, 3),
            date(2020, 1, 3),
            date(2020, 1, 3),
            date(2020, 6, 15),
            date(2020, 6, 15),
            date(2020, 6, 15),
        ],
        "permno": [10001, 10002, 10004, 10001, 10002, 10004, 10001, 10002, 10004],
        "cusip": ["AAPL", "DEL", "REN", "AAPL", "DEL", "REN", "AAPL", "DEL", "REN"],
        "ticker": [
            "AAPL",
            "DELISTED",
            "RENAMED",
            "AAPL",
            "DELISTED",
            "RENAMED",
            "AAPL",
            "DELISTED",
            "RENAMED",
        ],
        "ret": [0.01, 0.02, 0.015, -0.005, 0.01, -0.02, 0.03, -0.01, 0.005],
        "prc": [100.0, 50.0, 75.0, 99.5, 50.5, 73.5, 103.0, 49.5, 75.4],
        "vol": [1000000, 500000, 750000, 1100000, 480000, 760000, 950000, 510000, 740000],
        "shrout": [
            1000000,
            500000,
            600000,
            1000000,
            500000,
            600000,
            1000000,
            500000,
            600000,
        ],
    }
    df_2020 = pl.DataFrame(data_2020)
    path_2020 = crsp_dir / "2020.parquet"
    df_2020.write_parquet(path_2020)
    file_paths.append(path_2020)

    # Generate 2021 data (DELISTED ends here, RENAMED changes ticker)
    data_2021 = {
        "date": [
            date(2021, 6, 1),
            date(2021, 6, 1),
            date(2021, 6, 1),
            date(2021, 6, 1),  # PERMNO 10005 appears with same ticker as 10004
            date(2021, 12, 15),
            date(2021, 12, 15),
            date(2021, 12, 30),
            date(2021, 12, 30),
        ],
        "permno": [10001, 10002, 10004, 10005, 10001, 10002, 10001, 10004],
        "cusip": ["AAPL", "DEL", "REN", "AMB", "AAPL", "DEL", "AAPL", "REN"],
        "ticker": [
            "AAPL",
            "DELISTED",
            "RENAMED",
            "RENAMED",  # Same ticker as 10004 - creates ambiguity
            "AAPL",
            "DELISTED",  # Last day for DELISTED
            "AAPL",
            "NEWNAME",  # Ticker changed
        ],
        "ret": [0.02, 0.01, 0.025, 0.01, -0.01, 0.005, 0.015, -0.005],
        "prc": [
            150.0,
            45.0,
            80.0,
            25.0,
            148.5,
            45.2,
            150.7,
            79.6,
        ],
        "vol": [1200000, 400000, 800000, 200000, 1150000, 420000, 1180000, 780000],
        "shrout": [1000000, 500000, 600000, 300000, 1000000, 500000, 1000000, 600000],
    }
    df_2021 = pl.DataFrame(data_2021)
    path_2021 = crsp_dir / "2021.parquet"
    df_2021.write_parquet(path_2021)
    file_paths.append(path_2021)

    # Generate 2022 data (NEWIPO starts here)
    data_2022 = {
        "date": [
            date(2022, 1, 3),
            date(2022, 1, 3),
            date(2022, 1, 3),
            date(2022, 6, 15),
            date(2022, 6, 15),
            date(2022, 6, 15),
            date(2022, 12, 30),
            date(2022, 12, 30),
            date(2022, 12, 30),
        ],
        "permno": [10001, 10003, 10004, 10001, 10003, 10004, 10001, 10003, 10004],
        "cusip": ["AAPL", "NEW", "REN", "AAPL", "NEW", "REN", "AAPL", "NEW", "REN"],
        "ticker": [
            "AAPL",
            "NEWIPO",
            "NEWNAME",
            "AAPL",
            "NEWIPO",
            "NEWNAME",
            "AAPL",
            "NEWIPO",
            "NEWNAME",
        ],
        "ret": [0.01, 0.05, 0.02, 0.02, -0.03, 0.01, -0.01, 0.02, -0.005],
        "prc": [
            160.0,
            30.0,
            82.0,
            163.2,
            29.1,
            82.8,
            161.6,
            29.7,
            82.4,
        ],
        "vol": [1300000, 1000000, 850000, 1250000, 900000, 820000, 1280000, 950000, 830000],
        "shrout": [1000000, 800000, 600000, 1000000, 800000, 600000, 1000000, 800000, 600000],
    }
    df_2022 = pl.DataFrame(data_2022)
    path_2022 = crsp_dir / "2022.parquet"
    df_2022.write_parquet(path_2022)
    file_paths.append(path_2022)

    # Create manifest manager
    manifest_manager = ManifestManager(
        storage_path=manifest_dir,
        lock_dir=lock_dir,
        data_root=data_root,
    )

    # Create manifest file directly (bypass lock for test)
    manifest_data = {
        "dataset": "crsp_daily",
        "sync_timestamp": datetime.now(UTC).isoformat(),
        "start_date": "2020-01-02",
        "end_date": "2022-12-30",
        "row_count": 26,
        "checksum": "abc123",
        "checksum_algorithm": "sha256",
        "schema_version": "v1.0.0",
        "wrds_query_hash": "query123",
        "file_paths": [str(p) for p in file_paths],
        "validation_status": "passed",
        "manifest_version": 1,
    }
    manifest_file = manifest_dir / "crsp_daily.json"
    with open(manifest_file, "w") as f:
        json.dump(manifest_data, f)

    return data_root, manifest_manager, file_paths


class TestCRSPLocalProviderInit:
    """Tests for CRSPLocalProvider initialization."""

    def test_init_valid_path(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Valid storage path within data_root succeeds."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        provider = CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        assert provider.storage_path == storage_path.resolve()

    def test_path_outside_data_root_rejected(self, tmp_path: Path) -> None:
        """Storage path outside data_root raises ValueError."""
        data_root = tmp_path / "data"
        data_root.mkdir()
        outside_path = tmp_path / "outside"
        outside_path.mkdir()

        manifest_manager = MagicMock(spec=ManifestManager)

        with pytest.raises(ValueError, match="must be within data_root"):
            CRSPLocalProvider(
                storage_path=outside_path,
                manifest_manager=manifest_manager,
                data_root=data_root,
            )


class TestCRSPLocalProviderSchema:
    """Tests for schema validation."""

    def test_query_returns_correct_schema(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Verify returned DataFrame has expected columns and types."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_daily_prices(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 1, 31),
            )

        # Verify columns present
        assert set(df.columns) == set(CRSP_COLUMNS)

        # Verify types
        assert df.schema["date"] == pl.Date
        assert df.schema["permno"] == pl.Int64
        assert df.schema["ticker"] == pl.Utf8

    def test_invalid_columns_raises_error(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Requesting invalid columns raises ValueError."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(ValueError, match="Invalid columns"):
                provider.get_daily_prices(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 1, 31),
                    columns=["date", "permno", "invalid_col"],
                )


class TestCRSPLocalProviderPointInTime:
    """Tests for point-in-time filtering."""

    def test_point_in_time_excludes_future_ipos(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Securities that IPO after as_of_date are excluded."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Query 2022 data but with as_of_date in 2021
            # NEWIPO (10003) IPO'd in 2022, should be excluded
            df = provider.get_daily_prices(
                start_date=date(2022, 1, 1),
                end_date=date(2022, 12, 31),
                as_of_date=date(2021, 12, 31),
            )

        # NEWIPO (10003) should not be in results
        permnos = df["permno"].unique().to_list()
        assert 10003 not in permnos
        # But AAPL and NEWNAME should be
        assert 10001 in permnos
        assert 10004 in permnos

    def test_ipo_handling_appears_on_correct_date(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Stock appears in universe on its first trading date (not before)."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # NEWIPO first trades on 2022-01-03
            universe_before = provider.get_universe(
                as_of_date=date(2022, 1, 2), include_delisted=False
            )
            universe_on = provider.get_universe(
                as_of_date=date(2022, 1, 3), include_delisted=False
            )

        # NEWIPO should not be in universe before IPO
        assert 10003 not in universe_before["permno"].to_list()
        # But should be on IPO date
        assert 10003 in universe_on["permno"].to_list()


class TestCRSPLocalProviderSurvivorshipBias:
    """Tests for survivorship-bias-free data access."""

    def test_delisted_stocks_included_by_default(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Delisted stocks are included for survivorship-bias-free analysis."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Get universe after DELISTED was delisted
            universe = provider.get_universe(
                as_of_date=date(2022, 6, 1), include_delisted=True
            )

        # DELISTED (10002) should be included
        assert 10002 in universe["permno"].to_list()

    def test_delisting_handling_disappears_on_correct_date(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Stock disappears from active universe on delisting date."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # DELISTED last trades on 2021-12-15
            universe_on = provider.get_universe(
                as_of_date=date(2021, 12, 15), include_delisted=False
            )
            universe_after = provider.get_universe(
                as_of_date=date(2021, 12, 16), include_delisted=False
            )

        # DELISTED should be in active universe on last trading day
        assert 10002 in universe_on["permno"].to_list()
        # But not after
        assert 10002 not in universe_after["permno"].to_list()

    def test_get_universe_exclude_delisted(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Can optionally exclude delisted stocks."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            universe_with = provider.get_universe(
                as_of_date=date(2022, 6, 1), include_delisted=True
            )
            universe_without = provider.get_universe(
                as_of_date=date(2022, 6, 1), include_delisted=False
            )

        # DELISTED in one, not the other
        assert 10002 in universe_with["permno"].to_list()
        assert 10002 not in universe_without["permno"].to_list()


class TestCRSPLocalProviderPartitionPruning:
    """Tests for manifest-aware partition pruning."""

    def test_partition_pruning_only_reads_needed_years(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Query for 2022 data only reads 2022.parquet."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            manifest = provider._get_manifest()
            paths = provider._get_partition_paths_from_manifest(
                manifest, date(2022, 1, 1), date(2022, 12, 31)
            )

        # Should only include 2022.parquet
        assert len(paths) == 1
        assert paths[0].stem == "2022"

    def test_partition_pruning_spans_years(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Query spanning Dec 2021 - Jan 2022 reads both files."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            manifest = provider._get_manifest()
            paths = provider._get_partition_paths_from_manifest(
                manifest, date(2021, 12, 1), date(2022, 1, 31)
            )

        # Should include both 2021 and 2022
        years = {p.stem for p in paths}
        assert years == {"2021", "2022"}


class TestCRSPLocalProviderTickerMapping:
    """Tests for ticker/PERMNO mapping."""

    def test_ticker_to_permno_mapping(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Map ticker to PERMNO at specific date."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            permno = provider.ticker_to_permno("AAPL", date(2021, 6, 1))

        assert permno == 10001

    def test_ticker_to_permno_not_found_raises(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Unknown ticker raises DataNotFoundError."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="not found"):
                provider.ticker_to_permno("UNKNOWN", date(2021, 6, 1))

    def test_ticker_to_permno_ambiguous_raises(self, tmp_path: Path) -> None:
        """Ambiguous ticker raises AmbiguousTickerError with details.

        Note: The metadata cache uses the LAST ticker for each PERMNO, so ambiguity
        is only detected when multiple PERMNOs have the same FINAL ticker and
        overlapping active date ranges.
        """
        # Create specific test data with two PERMNOs having same final ticker
        data_root = tmp_path / "data"
        crsp_dir = data_root / "wrds" / "crsp" / "daily"
        crsp_dir.mkdir(parents=True)

        manifest_dir = data_root / "manifests"
        manifest_dir.mkdir(parents=True)
        lock_dir = data_root / "locks"
        lock_dir.mkdir(parents=True)

        # Create data where two different PERMNOs end with the same ticker "DUPE"
        # and have overlapping active date ranges
        data = {
            "date": [
                date(2021, 1, 4),
                date(2021, 1, 4),
                date(2021, 6, 15),
                date(2021, 6, 15),
                date(2021, 12, 30),
                date(2021, 12, 30),
            ],
            "permno": [20001, 20002, 20001, 20002, 20001, 20002],
            "cusip": ["DUP1", "DUP2", "DUP1", "DUP2", "DUP1", "DUP2"],
            "ticker": ["DUPE", "DUPE", "DUPE", "DUPE", "DUPE", "DUPE"],
            "ret": [0.01, 0.02, 0.015, 0.025, -0.01, 0.01],
            "prc": [100.0, 50.0, 101.5, 51.25, 100.5, 51.75],
            "vol": [1000000, 500000, 1100000, 520000, 1050000, 510000],
            "shrout": [1000000, 500000, 1000000, 500000, 1000000, 500000],
        }
        df = pl.DataFrame(data)
        path = crsp_dir / "2021.parquet"
        df.write_parquet(path)

        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=lock_dir,
            data_root=data_root,
        )

        # Create manifest
        manifest_data = {
            "dataset": "crsp_daily",
            "sync_timestamp": datetime.now(UTC).isoformat(),
            "start_date": "2021-01-04",
            "end_date": "2021-12-30",
            "row_count": 6,
            "checksum": "abc123",
            "checksum_algorithm": "sha256",
            "schema_version": "v1.0.0",
            "wrds_query_hash": "query123",
            "file_paths": [str(path)],
            "validation_status": "passed",
            "manifest_version": 1,
        }
        manifest_file = manifest_dir / "crsp_daily.json"
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with CRSPLocalProvider(
            storage_path=crsp_dir,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Both 20001 and 20002 have ticker "DUPE" and are active on 2021-06-15
            with pytest.raises(AmbiguousTickerError) as exc_info:
                provider.ticker_to_permno("DUPE", date(2021, 6, 15))

        assert exc_info.value.ticker == "DUPE"
        assert set(exc_info.value.permnos) == {20001, 20002}

    def test_permno_to_ticker_mapping(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Map PERMNO to ticker at specific date."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            ticker = provider.permno_to_ticker(10001, date(2021, 6, 1))

        assert ticker == "AAPL"

    def test_ticker_changes_historical_lookup(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Handle ticker changes over time."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # PERMNO 10004 was "RENAMED" before, "NEWNAME" after
            ticker_before = provider.permno_to_ticker(10004, date(2021, 6, 1))
            ticker_after = provider.permno_to_ticker(10004, date(2021, 12, 30))

        assert ticker_before == "RENAMED"
        assert ticker_after == "NEWNAME"

    def test_ticker_to_permno_historical_ticker(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """ticker_to_permno correctly finds PERMNO using historical ticker.

        This tests that we can look up a company by its old ticker after it
        has been renamed. For example, looking up "FB" when company is now "META".
        The fix queries the actual daily data instead of relying on cached metadata
        which only stores the final ticker.
        """
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # PERMNO 10004 was "RENAMED" in 2020, later changed to "NEWNAME" in late 2021
            # Use 2020-06-15 when only PERMNO 10004 had "RENAMED" (10005 didn't exist yet)
            permno_by_old_ticker = provider.ticker_to_permno(
                "RENAMED", date(2020, 6, 15)
            )

            # Also verify we can look up by the new ticker on a later date
            permno_by_new_ticker = provider.ticker_to_permno(
                "NEWNAME", date(2021, 12, 30)
            )

            # Both should return the same PERMNO
            assert permno_by_old_ticker == 10004
            assert permno_by_new_ticker == 10004

    def test_get_universe_returns_point_in_time_ticker(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """get_universe returns point-in-time ticker, not future ticker.

        This tests that the universe returns the ticker that was valid ON the
        as_of_date, not the final/current ticker. This prevents look-ahead bias.
        """
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Get universe on 2020-06-15 when PERMNO 10004 was "RENAMED"
            # (Using 2020 to avoid ambiguity with PERMNO 10005 which also
            # had "RENAMED" in 2021)
            universe_before = provider.get_universe(
                as_of_date=date(2020, 6, 15),
                include_delisted=False,
            )

            # Get universe on 2021-12-30 when PERMNO 10004 is "NEWNAME"
            universe_after = provider.get_universe(
                as_of_date=date(2021, 12, 30),
                include_delisted=False,
            )

        # Find PERMNO 10004 in both universes
        ticker_before = universe_before.filter(pl.col("permno") == 10004)["ticker"][0]
        ticker_after = universe_after.filter(pl.col("permno") == 10004)["ticker"][0]

        # The ticker should reflect the point-in-time value
        assert ticker_before == "RENAMED"
        assert ticker_after == "NEWNAME"


class TestCRSPLocalProviderPriceHandling:
    """Tests for CRSP negative price handling."""

    def test_negative_prices_returned_raw_by_default(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Negative prices (bid/ask average) returned as-is by default."""
        # Create data with negative price
        data_root, manifest_manager, _ = mock_crsp_data

        # Add a file with negative price
        crsp_dir = data_root / "wrds" / "crsp" / "daily"
        neg_data = {
            "date": [date(2020, 1, 2)],
            "permno": [99999],
            "cusip": ["NEG"],
            "ticker": ["NEGPRC"],
            "ret": [0.01],
            "prc": [-50.0],  # Negative = bid/ask average
            "vol": [100000],
            "shrout": [100000],
        }
        df_neg = pl.DataFrame(neg_data)
        path_neg = crsp_dir / "2020.parquet"
        # Append to existing
        df_existing = pl.read_parquet(path_neg)
        df_combined = pl.concat([df_existing, df_neg])
        df_combined.write_parquet(path_neg)

        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_daily_prices(
                start_date=date(2020, 1, 2),
                end_date=date(2020, 1, 2),
                permnos=[99999],
                adjust_prices=False,
            )

        assert df["prc"][0] == -50.0  # Raw negative preserved

    def test_adjust_prices_returns_absolute_values(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """adjust_prices=True returns abs(prc)."""
        data_root, manifest_manager, _ = mock_crsp_data

        # Add a file with negative price
        crsp_dir = data_root / "wrds" / "crsp" / "daily"
        neg_data = {
            "date": [date(2020, 1, 2)],
            "permno": [99998],
            "cusip": ["NEG2"],
            "ticker": ["NEGPRC2"],
            "ret": [0.01],
            "prc": [-75.5],  # Negative = bid/ask average
            "vol": [100000],
            "shrout": [100000],
        }
        df_neg = pl.DataFrame(neg_data)
        path_neg = crsp_dir / "2020.parquet"
        df_existing = pl.read_parquet(path_neg)
        df_combined = pl.concat([df_existing, df_neg])
        df_combined.write_parquet(path_neg)

        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_daily_prices(
                start_date=date(2020, 1, 2),
                end_date=date(2020, 1, 2),
                permnos=[99998],
                adjust_prices=True,
            )

        assert df["prc"][0] == 75.5  # Absolute value


class TestCRSPLocalProviderEdgeCases:
    """Tests for edge cases."""

    def test_returns_empty_for_no_data(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Returns empty DataFrame when no data matches query."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_daily_prices(
                start_date=date(2020, 1, 1),
                end_date=date(2020, 1, 1),
                permnos=[99999999],  # Non-existent
            )

        assert df.is_empty()
        # But should have correct schema
        assert set(df.columns) == set(CRSP_COLUMNS)

    def test_future_date_returns_empty(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Query for future dates returns empty DataFrame."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_daily_prices(
                start_date=date(2030, 1, 1),
                end_date=date(2030, 12, 31),
            )

        assert df.is_empty()

    def test_date_range_no_partitions(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Query for years without partitions returns empty."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            df = provider.get_daily_prices(
                start_date=date(2019, 1, 1),  # Before our data
                end_date=date(2019, 12, 31),
            )

        assert df.is_empty()


class TestCRSPLocalProviderManifestConsistency:
    """Tests for manifest-aware snapshot consistency."""

    def test_no_manifest_raises_data_not_found(self, tmp_path: Path) -> None:
        """DataNotFoundError if no manifest exists."""
        data_root = tmp_path / "data"
        crsp_dir = data_root / "wrds" / "crsp" / "daily"
        crsp_dir.mkdir(parents=True)

        manifest_dir = data_root / "manifests"
        manifest_dir.mkdir(parents=True)
        lock_dir = data_root / "locks"
        lock_dir.mkdir(parents=True)

        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=lock_dir,
            data_root=data_root,
        )

        with CRSPLocalProvider(
            storage_path=crsp_dir,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="No manifest found"):
                provider.get_daily_prices(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 12, 31),
                )

    def test_manifest_version_change_raises(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """ManifestVersionChangedError if manifest changes during query."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Mock manifest version change during query
            original_get_manifest = provider._get_manifest
            call_count = 0

            def mock_get_manifest() -> SyncManifest:
                nonlocal call_count
                call_count += 1
                manifest = original_get_manifest()
                if call_count > 1:
                    # Simulate version change on second call
                    manifest.manifest_version = 999
                return manifest

            provider._get_manifest = mock_get_manifest  # type: ignore[method-assign]

            with pytest.raises(ManifestVersionChangedError):
                provider.get_daily_prices(
                    start_date=date(2020, 1, 1),
                    end_date=date(2020, 12, 31),
                )


class TestCRSPLocalProviderConnectionManagement:
    """Tests for connection management."""

    def test_connection_cache_invalidation(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """invalidate_cache() clears metadata cache."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            # Populate cache
            _ = provider._get_security_metadata()
            assert provider._security_metadata is not None

            # Invalidate
            provider.invalidate_cache()
            assert provider._security_metadata is None

    def test_context_manager_closes_connection(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Connection closed on context manager exit."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        provider = CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        with provider:
            # Force connection creation
            _ = provider._ensure_connection()
            assert provider._conn is not None

        # After context exit
        assert provider._conn is None


class TestCRSPLocalProviderSecurityTimeline:
    """Tests for security timeline."""

    def test_get_security_timeline(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """Get complete trading history for a security."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            timeline = provider.get_security_timeline(10001)

        # Should have all dates for AAPL
        assert not timeline.is_empty()
        assert "date" in timeline.columns
        assert "ticker" in timeline.columns
        # All should be AAPL
        assert all(t == "AAPL" for t in timeline["ticker"].to_list())

    def test_get_security_timeline_not_found(
        self, mock_crsp_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        """DataNotFoundError for non-existent PERMNO."""
        data_root, manifest_manager, _ = mock_crsp_data
        storage_path = data_root / "wrds" / "crsp" / "daily"

        with CRSPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        ) as provider:
            with pytest.raises(DataNotFoundError, match="has no trading history"):
                provider.get_security_timeline(99999999)
