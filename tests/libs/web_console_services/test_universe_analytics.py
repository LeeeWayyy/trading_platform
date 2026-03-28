"""Tests for UniverseService analytics methods (P6T15/T15.2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers.universe import CRSPUnavailableError
from libs.data.universe_manager import UniverseManager, UniverseNotFoundError
from libs.web_console_services.universe_service import UniverseService


@pytest.fixture()
def tmp_universes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "universes"
    d.mkdir()
    return d


def _make_crsp_df(
    permnos: list[int],
    tickers: list[str],
    prices: list[float],
    volumes: list[float],
    shrouts: list[float],
    as_of: date | None = None,
) -> pl.DataFrame:
    """Build a mock CRSP daily prices DataFrame."""
    d = as_of or date(2026, 1, 10)
    return pl.DataFrame(
        {
            "date": [d] * len(permnos),
            "permno": permnos,
            "ticker": tickers,
            "prc": prices,
            "vol": volumes,
            "shrout": shrouts,
        }
    )


@pytest.fixture()
def mock_manager(tmp_universes_dir: Path) -> UniverseManager:
    provider = MagicMock()
    provider.get_constituents.return_value = pl.DataFrame(
        {"permno": [10001, 10002, 10003]}, schema={"permno": pl.Int64}
    )
    crsp = MagicMock()
    crsp.get_daily_prices.return_value = _make_crsp_df(
        permnos=[10001, 10002, 10003],
        tickers=["AAPL", "MSFT", "GOOGL"],
        prices=[200.0, 150.0, 180.0],
        volumes=[50_000_000.0, 30_000_000.0, 20_000_000.0],
        shrouts=[15_000.0, 7_500.0, 12_000.0],
    )
    return UniverseManager(
        universes_dir=tmp_universes_dir,
        universe_provider=provider,
        crsp_provider=crsp,
    )


@pytest.fixture()
def service(mock_manager: UniverseManager) -> UniverseService:
    return UniverseService(mock_manager)


def _admin_user() -> dict[str, Any]:
    return {"role": "admin", "user_id": "admin-1", "strategies": []}


def _viewer_user() -> dict[str, Any]:
    return {"role": "viewer", "user_id": "viewer-1", "strategies": []}


@pytest.mark.unit()
class TestGetUniverseAnalytics:
    """Tests for analytics computation."""

    @pytest.mark.asyncio()
    async def test_analytics_returns_correct_stats(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert result.universe_id == "SP500"
        assert result.symbol_count == 3
        assert result.avg_market_cap > 0
        assert result.median_adv > 0
        assert result.total_market_cap > 0

    @pytest.mark.asyncio()
    async def test_analytics_distributions_positive_only(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        for v in result.market_cap_distribution:
            assert v > 0
        for v in result.adv_distribution:
            assert v > 0

    @pytest.mark.asyncio()
    async def test_analytics_zero_null_filtered(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """Zero and null market_cap/adv values excluded from distributions."""
        mock_manager._crsp_provider.get_daily_prices.return_value = _make_crsp_df(
            permnos=[10001, 10002, 10003, 10004],
            tickers=["AAPL", "MSFT", "GOOGL", "ZERO"],
            prices=[200.0, 150.0, 180.0, 0.0],
            volumes=[50_000_000.0, 30_000_000.0, 20_000_000.0, 0.0],
            shrouts=[15_000.0, 7_500.0, 12_000.0, 0.0],
        )
        mock_manager._universe_provider.get_constituents.return_value = pl.DataFrame(
            {"permno": [10001, 10002, 10003, 10004]}, schema={"permno": pl.Int64}
        )
        mock_manager._enriched_cache.clear()

        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert len(result.market_cap_distribution) == 3
        assert len(result.adv_distribution) == 3

    @pytest.mark.asyncio()
    async def test_analytics_mock_flags(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert result.is_sector_mock is True
        assert result.is_factor_mock is True

    @pytest.mark.asyncio()
    async def test_analytics_sector_weights_sum_to_one(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        total = sum(result.sector_distribution.values())
        assert total == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio()
    async def test_analytics_has_eleven_sectors(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert len(result.sector_distribution) == 11

    @pytest.mark.asyncio()
    async def test_analytics_factor_keys(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        expected_keys = {"Market", "Size", "Value", "Momentum", "Volatility"}
        assert set(result.factor_exposure.keys()) == expected_keys

    @pytest.mark.asyncio()
    async def test_analytics_viewer_allowed_single_admin(
        self, service: UniverseService
    ) -> None:
        """P6T19: Viewer can view analytics — single-admin model."""
        result = await service.get_universe_analytics(
            _viewer_user(), "SP500", date(2026, 1, 10)
        )
        assert result is not None

    @pytest.mark.asyncio()
    async def test_analytics_not_found_returns_error_dto(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """UniverseNotFoundError returns DTO with error_message (not exception)."""
        mock_manager._universe_provider.get_constituents.side_effect = (
            UniverseNotFoundError("nonexistent")
        )
        mock_manager._enriched_cache.clear()

        result = await service.get_universe_analytics(
            _admin_user(), "nonexistent", date(2026, 1, 10)
        )
        assert result.error_message is not None
        assert "not found" in result.error_message
        assert result.symbol_count == 0

    @pytest.mark.asyncio()
    async def test_analytics_crsp_unavailable_returns_error_dto(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """CRSPUnavailableError returns DTO with crsp_unavailable flag."""
        mock_manager._crsp_provider.get_daily_prices.side_effect = (
            CRSPUnavailableError("No CRSP data")
        )
        mock_manager._enriched_cache.clear()

        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert result.crsp_unavailable is True
        assert result.error_message is not None

    @pytest.mark.asyncio()
    async def test_analytics_invalid_universe_id(
        self, service: UniverseService
    ) -> None:
        """Invalid universe ID format returns sanitized error_message."""
        result = await service.get_universe_analytics(
            _admin_user(), "../../etc/passwd", date(2026, 1, 10)
        )
        assert result.error_message is not None
        assert "Invalid universe ID" in result.error_message
        # Raw input must NOT leak to user
        assert "../../etc/passwd" not in result.error_message

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("bad_id", ["a.b", "a-b"])
    async def test_analytics_dot_hyphen_ids_handled_gracefully(
        self, service: UniverseService, bad_id: str,
    ) -> None:
        """IDs with dots/hyphens pass service validation but fail at manager.

        The service validator intentionally accepts these for built-in ID
        flexibility. The manager enforces stricter ``[a-z0-9_]`` for custom
        IDs at persistence time, raising ValueError which the service catches
        and returns a safe error DTO (no exception propagation).
        """
        result = await service.get_universe_analytics(
            _admin_user(), bad_id, date(2026, 1, 10)
        )
        assert result.error_message is not None
        # Manager's ValueError is caught — no raw exception reaches caller
        assert result.symbol_count == 0

    @pytest.mark.asyncio()
    async def test_analytics_space_in_id_rejected_by_service(
        self, service: UniverseService,
    ) -> None:
        """IDs with spaces are rejected by service validator."""
        result = await service.get_universe_analytics(
            _admin_user(), "has space", date(2026, 1, 10)
        )
        assert result.error_message is not None
        assert "Invalid universe ID" in result.error_message

    @pytest.mark.asyncio()
    async def test_analytics_skip_distributions(
        self, service: UniverseService
    ) -> None:
        """include_distributions=False omits distribution lists."""
        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10),
            include_distributions=False,
        )
        assert result.symbol_count == 3
        assert result.avg_market_cap > 0
        assert result.market_cap_distribution == []
        assert result.adv_distribution == []

    @pytest.mark.asyncio()
    async def test_analytics_empty_universe(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """Empty universe returns zero stats and empty distributions."""
        mock_manager._universe_provider.get_constituents.return_value = pl.DataFrame(
            {"permno": []}, schema={"permno": pl.Int64}
        )
        mock_manager._crsp_provider.get_daily_prices.return_value = pl.DataFrame(
            {
                "date": [],
                "permno": [],
                "ticker": [],
                "prc": [],
                "vol": [],
                "shrout": [],
            },
            schema={
                "date": pl.Date,
                "permno": pl.Int64,
                "ticker": pl.Utf8,
                "prc": pl.Float64,
                "vol": pl.Float64,
                "shrout": pl.Float64,
            },
        )
        mock_manager._enriched_cache.clear()

        result = await service.get_universe_analytics(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert result.symbol_count == 0
        assert result.avg_market_cap == 0.0
        assert result.median_adv == 0.0
        assert result.total_market_cap == 0.0
        assert result.market_cap_distribution == []
        assert result.adv_distribution == []
        assert result.sector_distribution == {}
        assert result.factor_exposure == {}


@pytest.mark.unit()
class TestCompareUniverses:
    """Tests for universe comparison."""

    @pytest.mark.asyncio()
    async def test_identical_universes_full_overlap(
        self, service: UniverseService
    ) -> None:
        result = await service.compare_universes(
            _admin_user(), "SP500", "SP500", date(2026, 1, 10)
        )
        assert result.overlap_count == 3
        assert result.overlap_pct == pytest.approx(100.0, abs=0.1)

    @pytest.mark.asyncio()
    async def test_comparison_returns_both_stats(
        self, service: UniverseService
    ) -> None:
        result = await service.compare_universes(
            _admin_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        assert result.universe_a_stats.universe_id == "SP500"
        assert result.universe_b_stats.universe_id == "R1000"

    @pytest.mark.asyncio()
    async def test_comparison_viewer_allowed_single_admin(
        self, service: UniverseService
    ) -> None:
        """P6T19: Viewer can compare universes — single-admin model."""
        result = await service.compare_universes(
            _viewer_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        assert result is not None

    @pytest.mark.asyncio()
    async def test_comparison_not_found_returns_error_dto(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """UniverseNotFoundError in one universe returns error in comparison DTO."""
        def _constituents(uid: str, *args: Any, **kwargs: Any) -> pl.DataFrame:
            if uid == "nonexistent":
                raise UniverseNotFoundError("nonexistent")
            return pl.DataFrame(
                {"permno": [10001, 10002, 10003]}, schema={"permno": pl.Int64}
            )

        mock_manager._universe_provider.get_constituents.side_effect = _constituents
        mock_manager._enriched_cache.clear()

        result = await service.compare_universes(
            _admin_user(), "SP500", "nonexistent", date(2026, 1, 10)
        )
        assert result.error_message is not None
        assert "not found" in result.error_message
        assert result.overlap_count == 0

    @pytest.mark.asyncio()
    async def test_comparison_invalid_id_returns_error(
        self, service: UniverseService
    ) -> None:
        """Invalid universe ID in comparison returns sanitized error DTO."""
        result = await service.compare_universes(
            _admin_user(), "SP500", "../../bad", date(2026, 1, 10)
        )
        assert result.error_message == "Invalid universe ID"
        # Raw path must NOT leak to user
        assert "../../bad" not in result.error_message

    @pytest.mark.asyncio()
    async def test_comparison_overlap_failure_returns_error(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """Exception during overlap fetch returns sanitized error DTO."""
        call_count = [0]
        original = mock_manager.get_enriched_constituents

        def _counted_get(uid: str, as_of: date) -> pl.DataFrame:
            call_count[0] += 1
            # Analytics phase (calls 1-2) succeeds; overlap (3+) fails
            if call_count[0] > 2:
                raise ValueError("Internal DB error")
            return original(uid, as_of)

        mock_manager.get_enriched_constituents = _counted_get  # type: ignore[assignment]

        result = await service.compare_universes(
            _admin_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        assert result.error_message is not None
        assert "Overlap computation unavailable" in result.error_message
        # Raw error text must NOT leak to user
        assert "Internal DB error" not in result.error_message

    @pytest.mark.asyncio()
    async def test_comparison_skips_distributions(
        self, service: UniverseService
    ) -> None:
        """Comparison stats should not include distribution lists."""
        result = await service.compare_universes(
            _admin_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        assert result.universe_a_stats.market_cap_distribution == []
        assert result.universe_b_stats.adv_distribution == []

    @pytest.mark.asyncio()
    async def test_comparison_overlap_pct_uses_smaller(
        self, service: UniverseService
    ) -> None:
        """Overlap percentage uses the smaller universe as denominator."""
        # Both universes return same 3 permnos from mock → 100% overlap
        result = await service.compare_universes(
            _admin_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        assert result.overlap_pct == pytest.approx(100.0, abs=0.1)

    @pytest.mark.asyncio()
    async def test_comparison_overlap_count(
        self, service: UniverseService
    ) -> None:
        result = await service.compare_universes(
            _admin_user(), "SP500", "SP500", date(2026, 1, 10)
        )
        assert result.overlap_count == 3

    @pytest.mark.asyncio()
    async def test_comparison_asymmetric_overlap(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """Asymmetric universes: overlap_pct uses smaller as denominator."""
        base_crsp = _make_crsp_df(
            permnos=[10001, 10002, 10003],
            tickers=["AAPL", "MSFT", "GOOGL"],
            prices=[200.0, 150.0, 180.0],
            volumes=[50_000_000.0, 30_000_000.0, 20_000_000.0],
            shrouts=[15_000.0, 7_500.0, 12_000.0],
        )

        def _constituents(uid: str, *args: Any, **kwargs: Any) -> pl.DataFrame:
            if uid == "SP500":
                return pl.DataFrame(
                    {"permno": [10001, 10002]}, schema={"permno": pl.Int64}
                )
            return pl.DataFrame(
                {"permno": [10001, 10003]},
                schema={"permno": pl.Int64},
            )

        def _crsp_filter(*args: Any, **kwargs: Any) -> pl.DataFrame:
            permnos = kwargs.get("permnos")
            if permnos:
                return base_crsp.filter(pl.col("permno").is_in(permnos))
            return base_crsp

        mock_manager._universe_provider.get_constituents.side_effect = _constituents
        mock_manager._crsp_provider.get_daily_prices.side_effect = _crsp_filter
        mock_manager._enriched_cache.clear()

        result = await service.compare_universes(
            _admin_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        # SP500={10001,10002}, R1000={10001,10003} → overlap={10001}=1
        # smaller=min(2,2)=2 → 50%
        assert result.overlap_count == 1
        assert result.overlap_pct == pytest.approx(50.0, abs=0.1)

    @pytest.mark.asyncio()
    async def test_comparison_no_overlap(
        self, service: UniverseService, mock_manager: UniverseManager
    ) -> None:
        """Disjoint universes produce 0 overlap."""
        base_crsp = _make_crsp_df(
            permnos=[10001, 10002, 10003],
            tickers=["AAPL", "MSFT", "GOOGL"],
            prices=[200.0, 150.0, 180.0],
            volumes=[50_000_000.0, 30_000_000.0, 20_000_000.0],
            shrouts=[15_000.0, 7_500.0, 12_000.0],
        )

        def _constituents(uid: str, *args: Any, **kwargs: Any) -> pl.DataFrame:
            if uid == "SP500":
                return pl.DataFrame(
                    {"permno": [10001]}, schema={"permno": pl.Int64}
                )
            return pl.DataFrame(
                {"permno": [10002]}, schema={"permno": pl.Int64}
            )

        def _crsp_filter(*args: Any, **kwargs: Any) -> pl.DataFrame:
            permnos = kwargs.get("permnos")
            if permnos:
                return base_crsp.filter(pl.col("permno").is_in(permnos))
            return base_crsp

        mock_manager._universe_provider.get_constituents.side_effect = _constituents
        mock_manager._crsp_provider.get_daily_prices.side_effect = _crsp_filter
        mock_manager._enriched_cache.clear()

        result = await service.compare_universes(
            _admin_user(), "SP500", "R1000", date(2026, 1, 10)
        )
        assert result.overlap_count == 0
        assert result.overlap_pct == pytest.approx(0.0, abs=0.1)
