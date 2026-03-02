"""Tests for UniverseManager (P6T15/T15.1)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers.universe import CRSPUnavailableError
from libs.data.universe_manager import (
    ConflictError,
    UniverseCorruptError,
    UniverseManager,
    UniverseNotFoundError,
)
from libs.web_console_services.schemas.universe import UniverseFilterDTO


@pytest.fixture()
def tmp_universes_dir(tmp_path: Path) -> Path:
    """Create a temporary universes directory."""
    d = tmp_path / "universes"
    d.mkdir()
    return d


@pytest.fixture()
def mock_universe_provider() -> MagicMock:
    """Create mock UniverseProvider."""
    provider = MagicMock()
    provider.get_constituents.return_value = pl.DataFrame(
        {"permno": [10001, 10002, 10003]}, schema={"permno": pl.Int64}
    )
    return provider


@pytest.fixture()
def mock_crsp_provider() -> MagicMock:
    """Create mock CRSPLocalProvider."""
    provider = MagicMock()
    # Return daily price data for enrichment
    provider.get_daily_prices.return_value = pl.DataFrame(
        {
            "date": [date(2026, 1, 10)] * 3,
            "permno": [10001, 10002, 10003],
            "ticker": ["AAPL", "MSFT", "GOOGL"],
            "prc": [200.0, -150.0, 180.0],  # Negative = bid/ask
            "vol": [50_000_000.0, 30_000_000.0, 20_000_000.0],
            "shrout": [15_000.0, 7_500.0, 12_000.0],  # $thousands
        }
    )
    return provider


@pytest.fixture()
def manager(
    tmp_universes_dir: Path,
    mock_universe_provider: MagicMock,
    mock_crsp_provider: MagicMock,
) -> UniverseManager:
    """Create UniverseManager with mocked providers."""
    return UniverseManager(
        universes_dir=tmp_universes_dir,
        universe_provider=mock_universe_provider,
        crsp_provider=mock_crsp_provider,
    )


@pytest.mark.unit()
class TestListUniverses:
    """Tests for listing built-in and custom universes."""

    def test_list_returns_built_in(self, manager: UniverseManager) -> None:
        result = manager.list_universes()
        ids = [u.id for u in result]
        assert "SP500" in ids
        assert "R1000" in ids

    def test_list_includes_custom(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        # Create a custom universe file
        (tmp_universes_dir / "test_universe.json").write_text(
            json.dumps({
                "id": "test_universe",
                "name": "Test Universe",
                "base_universe_id": "SP500",
                "filters": [],
                "exclude_symbols": [],
            })
        )
        result = manager.list_universes()
        custom = [u for u in result if u.universe_type == "custom"]
        assert len(custom) == 1
        assert custom[0].id == "test_universe"

    def test_list_skips_malformed_json(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        (tmp_universes_dir / "bad.json").write_text("{invalid json")
        result = manager.list_universes()
        # Should still include built-in, skip bad file
        assert len(result) >= 2


@pytest.mark.unit()
class TestGetConstituents:
    """Tests for constituent retrieval."""

    def test_get_built_in(
        self,
        manager: UniverseManager,
        mock_universe_provider: MagicMock,
    ) -> None:
        result = manager.get_constituents("SP500", date(2026, 1, 10))
        assert result.height == 3
        mock_universe_provider.get_constituents.assert_called_once_with(
            "SP500", date(2026, 1, 10)
        )

    def test_raises_without_provider(self, tmp_universes_dir: Path) -> None:
        mgr = UniverseManager(universes_dir=tmp_universes_dir)
        with pytest.raises(CRSPUnavailableError):
            mgr.get_constituents("SP500", date(2026, 1, 10))


@pytest.mark.unit()
class TestGetEnrichedConstituents:
    """Tests for enriched constituent retrieval."""

    def test_enriched_has_expected_columns(
        self, manager: UniverseManager
    ) -> None:
        result = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        assert "permno" in result.columns
        assert "ticker" in result.columns
        assert "market_cap" in result.columns
        assert "adv_20d" in result.columns
        assert result.height == 3

    def test_market_cap_uses_abs_prc(
        self, manager: UniverseManager
    ) -> None:
        """Market cap = abs(prc) * shrout. Negative prc should be abs'd."""
        result = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        # MSFT has prc=-150.0, shrout=7500.0 -> market_cap = 150*7500 = 1_125_000
        msft = result.filter(pl.col("ticker") == "MSFT")
        assert msft.height == 1
        mcap = msft["market_cap"][0]
        assert mcap == pytest.approx(150.0 * 7_500.0, rel=0.01)

    def test_caches_result(self, manager: UniverseManager) -> None:
        r1 = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        r2 = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        assert r1.equals(r2)


@pytest.mark.unit()
class TestApplyFilters:
    """Tests for filter application."""

    def test_market_cap_gt_filter(self, manager: UniverseManager) -> None:
        df = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        filtered = UniverseManager.apply_filters(
            df, [UniverseFilterDTO(field="market_cap", operator="gt", value=2_000_000)]
        )
        # Only AAPL (200*15000=3M) should pass
        assert filtered.height < df.height

    def test_adv_gt_filter(self, manager: UniverseManager) -> None:
        df = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        filtered = UniverseManager.apply_filters(
            df, [UniverseFilterDTO(field="adv_20d", operator="gt", value=5_000_000_000)]
        )
        assert filtered.height <= df.height

    def test_non_finite_filter_value_rejected(self) -> None:
        """NaN and Infinity must be rejected at validation time."""
        import math

        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="finite"):
            UniverseFilterDTO(field="market_cap", operator="gt", value=float("nan"))
        with pytest.raises(ValidationError, match="finite"):
            UniverseFilterDTO(field="market_cap", operator="gt", value=math.inf)
        with pytest.raises(ValidationError, match="finite"):
            UniverseFilterDTO(field="adv_20d", operator="lt", value=float("-inf"))

    def test_multiple_filters(self, manager: UniverseManager) -> None:
        df = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        filters = [
            UniverseFilterDTO(field="market_cap", operator="gt", value=0),
            UniverseFilterDTO(field="adv_20d", operator="gt", value=0),
        ]
        filtered = UniverseManager.apply_filters(df, filters)
        assert filtered.height <= df.height


@pytest.mark.unit()
class TestSaveCustom:
    """Tests for custom universe CRUD."""

    def test_save_creates_json(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        uid = manager.save_custom(
            {"name": "Test Universe", "base_universe_id": "SP500"},
            created_by="user-1",
        )
        assert uid == "test_universe"
        path = tmp_universes_dir / "test_universe.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["name"] == "Test Universe"
        assert data["created_by"] == "user-1"

    def test_save_duplicate_raises_conflict(
        self, manager: UniverseManager
    ) -> None:
        manager.save_custom(
            {"name": "My Universe", "base_universe_id": "SP500"},
            created_by="user-1",
        )
        with pytest.raises(ConflictError):
            manager.save_custom(
                {"name": "My Universe", "base_universe_id": "SP500"},
                created_by="user-1",
            )

    def test_save_built_in_id_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ConflictError, match="built-in"):
            manager.save_custom(
                {"name": "SP500", "base_universe_id": "SP500"},
                created_by="user-1",
            )

    def test_save_no_source_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="Either base_universe_id or manual_symbols"):
            manager.save_custom(
                {"name": "No Source"},
                created_by="user-1",
            )

    def test_save_invalid_base_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="Unknown base universe"):
            manager.save_custom(
                {"name": "Bad Base", "base_universe_id": "INVALID_INDEX"},
                created_by="user-1",
            )

    def test_save_both_sources_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="Cannot specify both"):
            manager.save_custom(
                {
                    "name": "Both Sources",
                    "base_universe_id": "SP500",
                    "manual_symbols": ["AAPL"],
                },
                created_by="user-1",
            )

    def test_save_auth0_pipe_user_accepted(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        """Auth0 user IDs with pipe character (auth0|12345) must be accepted."""
        uid = manager.save_custom(
            {"name": "Auth0 Test", "base_universe_id": "SP500"},
            created_by="auth0|12345",
        )
        assert uid == "auth0_test"
        data = json.loads((tmp_universes_dir / "auth0_test.json").read_text())
        assert data["created_by"] == "auth0|12345"

    def test_save_blank_only_manual_symbols_rejected(
        self, manager: UniverseManager
    ) -> None:
        """Blank/whitespace-only manual_symbols must be rejected after normalization."""
        with pytest.raises(ValueError, match="no valid tickers after normalization"):
            manager.save_custom(
                {"name": "Blank Test", "manual_symbols": ["  ", "", " "]},
                created_by="user-1",
            )

    def test_save_empty_name_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="name is required"):
            manager.save_custom({"name": ""}, created_by="user-1")

    def test_save_invalid_filter_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="Invalid filter"):
            manager.save_custom(
                {
                    "name": "Bad Filter",
                    "base_universe_id": "SP500",
                    "filters": [{"field": "bad_field", "operator": "gt", "value": 1.0}],
                },
                created_by="user-1",
            )

    def test_save_invalid_ticker_format_rejected(
        self, manager: UniverseManager
    ) -> None:
        with pytest.raises(ValueError, match="Invalid ticker format"):
            manager.save_custom(
                {
                    "name": "Bad Tickers",
                    "manual_symbols": ["INVALID!!!"],
                },
                created_by="user-1",
            )

    def test_save_invalid_exclude_ticker_rejected(
        self, manager: UniverseManager
    ) -> None:
        with pytest.raises(ValueError, match="Invalid ticker format"):
            manager.save_custom(
                {
                    "name": "Bad Exclude",
                    "base_universe_id": "SP500",
                    "exclude_symbols": ["$$$BAD"],
                },
                created_by="user-1",
            )

    def test_save_normalizes_tickers(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        """Tickers are normalized (stripped, uppercased, deduped) on save."""
        manager.save_custom(
            {
                "name": "Normalized Test",
                "manual_symbols": [" aapl ", "MSFT", " aapl", "googl"],
                "exclude_symbols": [" tsla ", "TSLA"],
            },
            created_by="user-1",
        )
        saved = json.loads(
            (tmp_universes_dir / "normalized_test.json").read_text()
        )
        assert saved["manual_symbols"] == ["AAPL", "MSFT", "GOOGL"]
        assert saved["exclude_symbols"] == ["TSLA"]

    def test_save_per_user_limit(
        self, manager: UniverseManager
    ) -> None:
        for i in range(20):
            manager.save_custom(
                {"name": f"Universe {i}", "base_universe_id": "SP500"},
                created_by="user-limit",
            )
        with pytest.raises(ValueError, match="Maximum"):
            manager.save_custom(
                {"name": "Universe 20", "base_universe_id": "SP500"},
                created_by="user-limit",
            )

    def test_manual_list_exceeding_limit(
        self, manager: UniverseManager
    ) -> None:
        with pytest.raises(ValueError, match="5000"):
            manager.save_custom(
                {
                    "name": "Too Many Symbols",
                    "manual_symbols": [f"SYM{i}" for i in range(5001)],
                },
                created_by="user-1",
            )


@pytest.mark.unit()
class TestDeleteCustom:
    """Tests for custom universe deletion."""

    def test_delete_removes_file(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        manager.save_custom(
            {"name": "To Delete", "base_universe_id": "SP500"},
            created_by="user-1",
        )
        manager.delete_custom("to_delete")
        assert not (tmp_universes_dir / "to_delete.json").exists()

    def test_delete_built_in_rejected(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="built-in"):
            manager.delete_custom("SP500")

    def test_delete_nonexistent_raises(self, manager: UniverseManager) -> None:
        with pytest.raises(FileNotFoundError):
            manager.delete_custom("nonexistent")

    def test_delete_fsync_failure_still_invalidates_cache(
        self, manager: UniverseManager, tmp_universes_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache is invalidated even when fsync fails after unlink."""
        manager.save_custom(
            {"name": "Fsync Test", "base_universe_id": "SP500"},
            created_by="user-1",
        )

        import os

        original_open = os.open

        def _fail_fsync(path: str, flags: int, *a: object) -> int:
            # Only fail on directory open (O_RDONLY on the dir for fsync)
            if flags == os.O_RDONLY:
                raise OSError("simulated fsync failure")
            return original_open(path, flags)

        monkeypatch.setattr(os, "open", _fail_fsync)

        # Should NOT raise — fsync failure is best-effort
        manager.delete_custom("fsync_test")
        assert not (tmp_universes_dir / "fsync_test.json").exists()
        # Cache should still be invalidated (generation bumped)
        assert manager._universe_generation.get("fsync_test", 0) > 0


@pytest.mark.unit()
class TestPathSecurity:
    """Tests for path traversal protection."""

    def test_path_traversal_rejected(
        self, manager: UniverseManager
    ) -> None:
        """Path traversal is prevented by _validate_path_safety."""
        with pytest.raises(ValueError, match="Path traversal"):
            manager._validate_path_safety(
                manager._universes_dir / ".." / ".." / "etc" / "passwd"
            )

    def test_invalid_id_format(self, manager: UniverseManager) -> None:
        with pytest.raises(ValueError, match="Invalid universe ID"):
            manager._validate_universe_id("../bad")

    def test_valid_id_format(self, manager: UniverseManager) -> None:
        # Should not raise
        manager._validate_universe_id("valid_universe_123")


@pytest.mark.unit()
class TestManualListUniverse:
    """Tests for manual symbol list universes."""

    def test_manual_list_resolves_tickers(
        self,
        manager: UniverseManager,
        mock_crsp_provider: MagicMock,
        tmp_universes_dir: Path,
    ) -> None:
        mock_crsp_provider.ticker_to_permno.return_value = 10001

        # Create manual list universe
        manager.save_custom(
            {"name": "Manual Test", "manual_symbols": ["AAPL", "MSFT"]},
            created_by="user-1",
        )
        result = manager.get_enriched_constituents(
            "manual_test", date(2026, 1, 10)
        )
        assert result.height > 0

    def test_unresolved_tickers_returned(
        self,
        manager: UniverseManager,
        mock_crsp_provider: MagicMock,
        tmp_universes_dir: Path,
    ) -> None:
        from libs.data.data_quality.exceptions import DataNotFoundError

        mock_crsp_provider.ticker_to_permno.side_effect = DataNotFoundError(
            "not found"
        )

        manager.save_custom(
            {"name": "Bad Tickers", "manual_symbols": ["INVALID"]},
            created_by="user-1",
        )
        unresolved = manager.get_unresolved_tickers(
            "bad_tickers", date(2026, 1, 10)
        )
        assert "INVALID" in unresolved

    def test_ambiguous_ticker_uses_max_permno(
        self,
        manager: UniverseManager,
        mock_crsp_provider: MagicMock,
        tmp_universes_dir: Path,
    ) -> None:
        """Ambiguous tickers use max(permnos) for deterministic resolution."""
        from libs.data.data_providers.crsp_local_provider import (
            AmbiguousTickerError,
        )

        mock_crsp_provider.ticker_to_permno.side_effect = AmbiguousTickerError(
            "AMBI", date(2026, 1, 10), [88888, 99999]
        )

        manager.save_custom(
            {"name": "Ambiguous Test", "manual_symbols": ["AMBI"]},
            created_by="user-1",
        )
        manager.get_enriched_constituents(
            "ambiguous_test", date(2026, 1, 10)
        )
        # Should resolve with max permno (99999) for determinism
        assert mock_crsp_provider.get_daily_prices.called
        call_args = mock_crsp_provider.get_daily_prices.call_args
        assert 99999 in call_args.kwargs.get("permnos", call_args[1].get("permnos", []))

    def test_all_invalid_tickers_empty_result(
        self,
        manager: UniverseManager,
        mock_crsp_provider: MagicMock,
        tmp_universes_dir: Path,
    ) -> None:
        from libs.data.data_quality.exceptions import DataNotFoundError

        mock_crsp_provider.ticker_to_permno.side_effect = DataNotFoundError(
            "not found"
        )

        manager.save_custom(
            {"name": "All Invalid", "manual_symbols": ["X1", "X2", "X3"]},
            created_by="user-1",
        )
        result = manager.get_enriched_constituents(
            "all_invalid", date(2026, 1, 10)
        )
        assert result.height == 0


@pytest.mark.unit()
class TestManualListCrspUnavailable:
    """Tests for manual list when CRSP is unavailable."""

    def test_manual_list_raises_without_crsp(
        self, tmp_universes_dir: Path
    ) -> None:
        """Manual list universe raises CRSPUnavailableError without CRSP provider."""
        mgr = UniverseManager(universes_dir=tmp_universes_dir)
        mgr.save_custom(
            {"name": "Manual No CRSP", "manual_symbols": ["AAPL"]},
            created_by="user-1",
        )
        with pytest.raises(CRSPUnavailableError, match="CRSP provider required"):
            mgr.get_enriched_constituents("manual_no_crsp", date(2026, 1, 10))


@pytest.mark.unit()
class TestUnresolvedCacheByDate:
    """Tests for date-keyed unresolved ticker cache."""

    def test_different_dates_cached_independently(
        self,
        manager: UniverseManager,
        mock_crsp_provider: MagicMock,
        tmp_universes_dir: Path,
    ) -> None:
        from libs.data.data_quality.exceptions import DataNotFoundError

        call_count = 0

        def _side_effect(ticker: str, as_of: date) -> int:
            nonlocal call_count
            call_count += 1
            if as_of == date(2026, 1, 10):
                raise DataNotFoundError("not found on Jan 10")
            return 10001  # Resolves on other dates

        mock_crsp_provider.ticker_to_permno.side_effect = _side_effect

        manager.save_custom(
            {"name": "Date Cache Test", "manual_symbols": ["SYM1"]},
            created_by="user-1",
        )

        # Jan 10 → SYM1 unresolved
        unresolved_jan10 = manager.get_unresolved_tickers(
            "date_cache_test", date(2026, 1, 10)
        )
        assert "SYM1" in unresolved_jan10

        # Jan 15 → SYM1 resolved
        unresolved_jan15 = manager.get_unresolved_tickers(
            "date_cache_test", date(2026, 1, 15)
        )
        assert "SYM1" not in unresolved_jan15

    def test_enriched_populates_unresolved_cache(
        self,
        manager: UniverseManager,
        mock_crsp_provider: MagicMock,
        tmp_universes_dir: Path,
    ) -> None:
        from libs.data.data_quality.exceptions import DataNotFoundError

        mock_crsp_provider.ticker_to_permno.side_effect = DataNotFoundError(
            "not found"
        )

        manager.save_custom(
            {"name": "Cache Pop Test", "manual_symbols": ["BAD1"]},
            created_by="user-1",
        )

        # Call enriched first — should populate the cache
        manager.get_enriched_constituents("cache_pop_test", date(2026, 1, 10))

        # Now get_unresolved_tickers should use cached result (no extra calls)
        initial_call_count = mock_crsp_provider.ticker_to_permno.call_count
        unresolved = manager.get_unresolved_tickers(
            "cache_pop_test", date(2026, 1, 10)
        )
        assert "BAD1" in unresolved
        # Should not have made additional ticker_to_permno calls
        assert mock_crsp_provider.ticker_to_permno.call_count == initial_call_count


@pytest.mark.unit()
class TestUnknownUniverseId:
    """Tests for unknown universe ID handling."""

    def test_get_enriched_unknown_raises(
        self, manager: UniverseManager
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            manager.get_enriched_constituents("nonexistent_universe", date(2026, 1, 10))

    def test_get_metadata_unknown_raises(
        self, manager: UniverseManager
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            manager.get_universe_metadata("nonexistent_universe")


@pytest.mark.unit()
class TestListUniversesSorting:
    """Tests for universe list ordering."""

    def test_custom_sorted_by_name(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        # Create universes with names that sort differently than filenames
        (tmp_universes_dir / "z_first.json").write_text(
            json.dumps({
                "id": "z_first",
                "name": "Alpha Universe",
                "base_universe_id": "SP500",
                "filters": [],
                "exclude_symbols": [],
            })
        )
        (tmp_universes_dir / "a_second.json").write_text(
            json.dumps({
                "id": "a_second",
                "name": "Zeta Universe",
                "base_universe_id": "SP500",
                "filters": [],
                "exclude_symbols": [],
            })
        )
        result = manager.list_universes()
        custom = [u for u in result if u.universe_type == "custom"]
        assert len(custom) == 2
        # "Alpha Universe" should come before "Zeta Universe" (name sort)
        assert custom[0].name == "Alpha Universe"
        assert custom[1].name == "Zeta Universe"


@pytest.mark.unit()
class TestGetSymbolCount:
    """Tests for symbol count retrieval."""

    def test_returns_count_for_built_in(
        self, manager: UniverseManager
    ) -> None:
        count = manager.get_symbol_count("SP500", date(2026, 1, 10))
        assert count == 3

    def test_returns_none_without_provider(
        self, tmp_universes_dir: Path
    ) -> None:
        mgr = UniverseManager(universes_dir=tmp_universes_dir)
        assert mgr.get_symbol_count("SP500", date(2026, 1, 10)) is None

    def test_returns_none_on_crsp_error(
        self,
        manager: UniverseManager,
        mock_universe_provider: MagicMock,
    ) -> None:
        mock_universe_provider.get_constituents.side_effect = (
            CRSPUnavailableError("test")
        )
        assert manager.get_symbol_count("SP500", date(2026, 1, 10)) is None


@pytest.mark.unit()
class TestComputeLockCleanupOnFailure:
    """Tests that _compute_locks are cleaned up when enrichment fails."""

    def test_lock_removed_on_failure(
        self,
        tmp_universes_dir: Path,
        mock_universe_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        mock_crsp_provider.get_daily_prices.side_effect = RuntimeError("CRSP down")
        mgr = UniverseManager(
            universes_dir=tmp_universes_dir,
            universe_provider=mock_universe_provider,
            crsp_provider=mock_crsp_provider,
        )
        cache_key = ("SP500", date(2026, 1, 10))
        with pytest.raises(CRSPUnavailableError):
            mgr.get_enriched_constituents("SP500", date(2026, 1, 10))
        # Lock should have been cleaned up after failure
        assert cache_key not in mgr._compute_locks

    def test_lock_preserved_on_success(
        self, manager: UniverseManager
    ) -> None:
        manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        cache_key = ("SP500", date(2026, 1, 10))
        # Lock stays when entry is in cache
        assert cache_key in manager._compute_locks


@pytest.mark.unit()
class TestGenerationMismatchRecompute:
    """Tests that stale generation triggers recompute."""

    def test_recompute_on_generation_change(
        self, manager: UniverseManager
    ) -> None:
        """If universe generation changes during compute, result reflects new state."""
        original_compute = manager._compute_enriched
        call_count = [0]

        def _compute_with_gen_bump(uid: str, as_of: date) -> pl.DataFrame:
            call_count[0] += 1
            # Simulate a delete/recreate by bumping generation during first compute
            if call_count[0] == 1:
                with manager._cache_lock:
                    gen = manager._universe_generation.get(uid, 0)
                    manager._universe_generation[uid] = gen + 1
            return original_compute(uid, as_of)

        manager._compute_enriched = _compute_with_gen_bump  # type: ignore[assignment]

        result = manager.get_enriched_constituents("SP500", date(2026, 1, 10))
        # Should have computed twice: first stale, second fresh
        assert call_count[0] == 2
        assert len(result) > 0
        # Result should be cached
        cache_key = ("SP500", date(2026, 1, 10))
        assert cache_key in manager._enriched_cache

    def test_recompute_stops_after_max_retries(
        self,
        tmp_universes_dir: Path,
        mock_universe_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ) -> None:
        """Generation changes every compute should stop after max retries and raise."""
        mgr = UniverseManager(
            universes_dir=tmp_universes_dir,
            universe_provider=mock_universe_provider,
            crsp_provider=mock_crsp_provider,
        )
        original_compute = mgr._compute_enriched

        def _always_bump_gen(uid: str, as_of: date) -> pl.DataFrame:
            with mgr._cache_lock:
                gen = mgr._universe_generation.get(uid, 0)
                mgr._universe_generation[uid] = gen + 1
            return original_compute(uid, as_of)

        mgr._compute_enriched = _always_bump_gen  # type: ignore[assignment]

        # Should not loop forever — bounded retries, then fail closed
        with pytest.raises(ValueError, match="mutated concurrently"):
            mgr.get_enriched_constituents("SP500", date(2026, 1, 10))
        # Unstable generation must NOT be cached
        cache_key = ("SP500", date(2026, 1, 10))
        assert cache_key not in mgr._enriched_cache


@pytest.mark.unit()
class TestIdFilenameCanonicalisation:
    """Tests that canonical ID is always the filename stem."""

    def test_metadata_uses_filename_stem_not_json_id(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        """If JSON contains divergent 'id', filename stem wins."""
        (tmp_universes_dir / "real_id.json").write_text(
            json.dumps({
                "id": "wrong_id",
                "name": "Test Universe",
                "base_universe_id": "SP500",
                "filters": [],
                "exclude_symbols": [],
            })
        )
        meta = manager.get_universe_metadata("real_id")
        assert meta.id == "real_id"
        assert meta.name == "Test Universe"

    def test_list_uses_filename_stem(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        """list_universes uses filename stem as canonical ID."""
        (tmp_universes_dir / "stem_id.json").write_text(
            json.dumps({
                "id": "divergent_id",
                "name": "Stem Test",
                "base_universe_id": "SP500",
                "filters": [],
                "exclude_symbols": [],
            })
        )
        result = manager.list_universes()
        custom = [u for u in result if u.universe_type == "custom"]
        assert any(u.id == "stem_id" for u in custom)
        assert not any(u.id == "divergent_id" for u in custom)


@pytest.mark.unit()
class TestCorruptMetadataHandling:
    """Tests that corrupt/invalid metadata raises UniverseCorruptError."""

    def test_invalid_schema_raises_corrupt_error(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        """Invalid filter schema in JSON raises UniverseCorruptError."""
        (tmp_universes_dir / "bad_schema.json").write_text(
            json.dumps({
                "id": "bad_schema",
                "name": "Bad",
                "filters": [{"field": "invalid_field", "operator": "gt", "value": 1.0}],
            })
        )
        with pytest.raises(UniverseCorruptError, match="corrupt"):
            manager.get_universe_metadata("bad_schema")

    def test_malformed_json_raises_corrupt_error(
        self, manager: UniverseManager, tmp_universes_dir: Path
    ) -> None:
        """Non-JSON content raises UniverseCorruptError."""
        (tmp_universes_dir / "not_json.json").write_text("not valid json {{{")
        with pytest.raises(UniverseCorruptError, match="corrupt"):
            manager.get_universe_metadata("not_json")

    def test_nonexistent_raises_not_found(
        self, manager: UniverseManager
    ) -> None:
        """Missing universe raises UniverseNotFoundError."""
        with pytest.raises(UniverseNotFoundError, match="not found"):
            manager.get_universe_metadata("does_not_exist")
