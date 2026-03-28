"""Unit tests for ExposureService (P6T15/T15.3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from libs.web_console_data.exposure_queries import ExposureQueryResult
from libs.web_console_services.exposure_service import (
    _BIAS_AMBER_THRESHOLD,
    _BIAS_RED_THRESHOLD,
    ExposureService,
    _aggregate_exposures,
    _build_total,
    _compute_bias_warning,
)


@pytest.fixture()
def operator_user() -> dict[str, Any]:
    return {"role": "operator", "user_id": "operator-1", "strategies": ["alpha1"]}


@pytest.fixture()
def viewer_user() -> dict[str, Any]:
    return {"role": "viewer", "user_id": "viewer-1", "strategies": []}


@pytest.fixture()
def researcher_user() -> dict[str, Any]:
    return {"role": "researcher", "user_id": "researcher-1", "strategies": ["alpha1"]}


def _make_position(
    symbol: str,
    qty: float,
    current_price: float | None,
    avg_entry_price: float = 100.0,
    strategy: str = "alpha1",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "qty": qty,
        "current_price": current_price,
        "avg_entry_price": avg_entry_price,
        "unrealized_pl": None,
        "realized_pl": 0,
        "updated_at": "2026-01-10T12:00:00Z",
        "last_trade_at": None,
        "strategy": strategy,
    }


class TestAggregateExposures:
    def test_empty_positions_returns_mock(self) -> None:
        result = ExposureQueryResult(positions=[], excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        assert is_placeholder is True
        assert len(exposures) == 3  # 3 mock strategies
        assert exposures[0].strategy == "Momentum Alpha"

    def test_single_strategy_all_long(self) -> None:
        positions = [
            _make_position("AAPL", 100, 150.0),
            _make_position("MSFT", 50, 200.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)

        assert is_placeholder is False
        assert len(exposures) == 1
        e = exposures[0]
        assert e.strategy == "alpha1"
        assert e.long_notional == 25000.0  # 100*150 + 50*200
        assert e.short_notional == 0.0
        assert e.gross_notional == 25000.0
        assert e.net_notional == 25000.0
        assert e.net_pct == 100.0
        assert e.position_count == 2

    def test_single_strategy_all_short(self) -> None:
        positions = [
            _make_position("AAPL", -100, 150.0),
            _make_position("MSFT", -50, 200.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)

        assert is_placeholder is False
        e = exposures[0]
        assert e.long_notional == 0.0
        assert e.short_notional == 25000.0
        assert e.net_notional == -25000.0
        assert e.net_pct == -100.0

    def test_multi_strategy_breakdown(self) -> None:
        positions = [
            _make_position("AAPL", 100, 150.0, strategy="alpha1"),
            _make_position("MSFT", -50, 200.0, strategy="alpha1"),
            _make_position("GOOGL", 30, 100.0, strategy="beta1"),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)

        assert is_placeholder is False
        assert len(exposures) == 2

        alpha = next(e for e in exposures if e.strategy == "alpha1")
        assert alpha.long_notional == 15000.0  # 100*150
        assert alpha.short_notional == 10000.0  # 50*200
        assert alpha.position_count == 2

        beta = next(e for e in exposures if e.strategy == "beta1")
        assert beta.long_notional == 3000.0  # 30*100
        assert beta.short_notional == 0.0
        assert beta.position_count == 1

    def test_missing_current_price_falls_back(self) -> None:
        positions = [
            _make_position("AAPL", 100, None, avg_entry_price=120.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, _ = _aggregate_exposures(result)

        assert exposures[0].long_notional == 12000.0  # 100*120
        assert exposures[0].fallback_price_count == 1

    def test_malformed_qty_skipped(self) -> None:
        """Corrupted qty values are skipped and counted as missing."""
        positions = [
            _make_position("AAPL", 100, 150.0),
            {
                "symbol": "BAD",
                "qty": "not_a_number",
                "current_price": 100.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, _ = _aggregate_exposures(result)

        assert exposures[0].long_notional == 15000.0  # Only AAPL
        assert exposures[0].missing_price_count == 1  # BAD skipped
        assert exposures[0].position_count == 2  # Both counted

    def test_zero_gross_no_division_error(self) -> None:
        # Position with qty=0 shouldn't happen (filtered by query), but test safety
        positions = [
            _make_position("AAPL", 0, 150.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)

        # qty=0 means neither long nor short, so no notional contributed
        assert is_placeholder is False
        assert exposures[0].net_pct == 0.0

    def test_non_finite_values_skipped(self) -> None:
        """NaN/inf prices or quantities are skipped or fall back to entry price."""
        positions = [
            _make_position("AAPL", 100, 150.0),
            _make_position("BAD1", float("nan"), 100.0),  # Bad qty → unvalued
            _make_position("BAD2", 50, float("inf")),  # Inf price → fallback to 100
            _make_position("BAD3", 50, "not_a_number"),  # Parse error → unvalued
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, _ = _aggregate_exposures(result)

        # AAPL: 100*150=15000, BAD2 falls back to avg_entry_price=100: 50*100=5000
        assert exposures[0].long_notional == 20000.0
        assert exposures[0].missing_price_count == 2  # BAD1 (nan qty), BAD3 (parse)
        assert exposures[0].fallback_price_count == 1  # BAD2 (inf → entry price)
        assert exposures[0].position_count == 4  # All counted


    def test_negative_live_price_falls_back_to_entry(self) -> None:
        """Negative live price should use avg_entry_price as fallback."""
        positions = [
            _make_position("AAPL", 100, -5.0, avg_entry_price=120.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, _ = _aggregate_exposures(result)

        # Should use avg_entry_price=120 → long_notional=12000
        assert exposures[0].long_notional == 12000.0
        assert exposures[0].fallback_price_count == 1
        assert exposures[0].missing_price_count == 0

    def test_negative_live_price_no_entry_is_unvalued(self) -> None:
        """Negative live price with no valid avg_entry_price → unvalued."""
        positions = [
            _make_position("AAPL", 100, -5.0, avg_entry_price=0.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, _ = _aggregate_exposures(result)

        assert exposures[0].long_notional == 0.0
        assert exposures[0].missing_price_count == 1


class TestBuildTotal:
    def test_total_equals_sum_of_strategies(self) -> None:
        positions = [
            _make_position("AAPL", 100, 150.0, strategy="alpha1"),
            _make_position("MSFT", -50, 200.0, strategy="beta1"),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=0
        )

        assert total.long_total == sum(e.long_notional for e in exposures)
        assert total.short_total == sum(e.short_notional for e in exposures)
        assert total.gross_total == sum(e.gross_notional for e in exposures)
        assert total.net_total == sum(e.net_notional for e in exposures)
        assert total.strategy_count == 2
        assert total.is_placeholder is False

    def test_placeholder_flag_set(self) -> None:
        result = ExposureQueryResult(positions=[], excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=0
        )
        assert total.is_placeholder is True

    def test_partial_data_warning(self) -> None:
        positions = [_make_position("AAPL", 100, 150.0)]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=3)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=3
        )

        assert total.is_partial is True
        assert total.data_quality_warning is not None
        assert "3 symbols excluded" in total.data_quality_warning

    def test_excluded_positions_no_mock(self) -> None:
        """When all positions are excluded, no mock data is shown."""
        result = ExposureQueryResult(positions=[], excluded_symbol_count=2)
        exposures, is_placeholder = _aggregate_exposures(result)

        # No mock fallback when real risk exists but can't be attributed
        assert is_placeholder is False
        assert len(exposures) == 0

        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=2
        )
        assert total.is_placeholder is False
        assert total.is_partial is True
        assert total.data_quality_warning is not None
        assert "2 symbols excluded" in total.data_quality_warning

    def test_no_partial_when_placeholder_without_exclusions(self) -> None:
        """Fresh system with no trades: no partial flag."""
        result = ExposureQueryResult(positions=[], excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=0
        )
        assert total.is_placeholder is True
        assert total.is_partial is False
        assert total.data_quality_warning is None


class TestBiasWarning:
    def test_no_warning_below_threshold(self) -> None:
        warning, severity = _compute_bias_warning(5.0)
        assert warning is None
        assert severity is None

        warning, severity = _compute_bias_warning(-5.0)
        assert warning is None
        assert severity is None

        warning, severity = _compute_bias_warning(0.0)
        assert warning is None
        assert severity is None

    def test_amber_warning_at_threshold(self) -> None:
        warning, severity = _compute_bias_warning(_BIAS_AMBER_THRESHOLD + 1)
        assert warning is not None
        assert "Long" in warning
        assert severity == "amber"

    def test_red_warning_at_threshold(self) -> None:
        warning, severity = _compute_bias_warning(_BIAS_RED_THRESHOLD + 1)
        assert warning is not None
        assert "Severe" in warning
        assert "Long" in warning
        assert severity == "red"

    def test_short_bias_direction(self) -> None:
        warning, severity = _compute_bias_warning(-(_BIAS_AMBER_THRESHOLD + 1))
        assert warning is not None
        assert "Short" in warning
        assert severity == "amber"


class TestExposureServicePermission:
    @pytest.mark.asyncio()
    async def test_viewer_allowed_single_admin(self, viewer_user: dict[str, Any]) -> None:
        """P6T19: Viewer can view exposure — single-admin model."""
        service = ExposureService()
        mock_result = ExposureQueryResult(positions=[], excluded_symbol_count=0)

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            exposures, total = await service.get_strategy_exposure(
                viewer_user, AsyncMock()
            )
        assert total is not None

    @pytest.mark.asyncio()
    async def test_operator_allowed(self, operator_user: dict[str, Any]) -> None:
        service = ExposureService()
        mock_result = ExposureQueryResult(positions=[], excluded_symbol_count=0)

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            exposures, total = await service.get_strategy_exposure(
                operator_user, AsyncMock()
            )
            assert total.is_placeholder is True
            assert len(exposures) == 3  # mock strategies

    @pytest.mark.asyncio()
    async def test_researcher_allowed(self, researcher_user: dict[str, Any]) -> None:
        service = ExposureService()
        mock_result = ExposureQueryResult(positions=[], excluded_symbol_count=0)

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            exposures, total = await service.get_strategy_exposure(
                researcher_user, AsyncMock()
            )
            assert total.is_placeholder is True

    @pytest.mark.asyncio()
    async def test_real_positions_no_mock(self, operator_user: dict[str, Any]) -> None:
        service = ExposureService()
        positions = [
            _make_position("AAPL", 100, 150.0),
        ]
        mock_result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            exposures, total = await service.get_strategy_exposure(
                operator_user, AsyncMock()
            )
            assert total.is_placeholder is False
            assert len(exposures) == 1
            assert exposures[0].strategy == "alpha1"


class TestMissingPriceTracking:
    def test_missing_price_counted_and_included_in_position_count(self) -> None:
        positions = [
            _make_position("AAPL", 100, 150.0),
            {
                "symbol": "XYZ",
                "qty": 50,
                "current_price": None,
                "avg_entry_price": None,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, _ = _aggregate_exposures(result)

        assert exposures[0].position_count == 2  # Both positions counted
        assert exposures[0].missing_price_count == 1
        assert exposures[0].long_notional == 15000.0  # Only AAPL contributes

    def test_missing_price_sets_partial_flag(self) -> None:
        """Missing price data should mark total as partial."""
        positions = [
            _make_position("AAPL", 100, 150.0),
            {
                "symbol": "XYZ",
                "qty": 50,
                "current_price": None,
                "avg_entry_price": None,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=0
        )

        assert total.is_partial is True
        assert total.missing_price_count == 1

    def test_missing_price_data_quality_warning(self) -> None:
        positions = [
            _make_position("AAPL", 100, 150.0),
            {
                "symbol": "XYZ",
                "qty": 50,
                "current_price": None,
                "avg_entry_price": None,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=0
        )

        assert total.missing_price_count == 1
        assert total.data_quality_warning is not None
        assert "1 position could not be valued" in total.data_quality_warning

    def test_fallback_price_data_quality_warning(self) -> None:
        """Fallback pricing is surfaced in data_quality_warning."""
        positions = [
            _make_position("AAPL", 100, None, avg_entry_price=120.0),
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=0
        )

        assert total.fallback_price_count == 1
        assert total.data_quality_warning is not None
        assert "1 position using entry price" in total.data_quality_warning

    def test_combined_excluded_and_missing_price_warnings(self) -> None:
        positions = [
            _make_position("AAPL", 100, 150.0),
            {
                "symbol": "XYZ",
                "qty": 50,
                "current_price": None,
                "avg_entry_price": None,
                "unrealized_pl": None,
                "realized_pl": 0,
                "updated_at": "2026-01-10T12:00:00Z",
                "last_trade_at": None,
                "strategy": "alpha1",
            },
        ]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=2)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=2
        )

        assert total.data_quality_warning is not None
        assert "2 symbols excluded" in total.data_quality_warning
        assert "1 position could not be valued" in total.data_quality_warning


class TestUnmappedPositions:
    def test_unmapped_positions_in_data_quality_warning(self) -> None:
        positions = [_make_position("AAPL", 100, 150.0)]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures,
            is_placeholder=is_placeholder,
            excluded_symbol_count=0,
            unmapped_position_count=3,
        )

        assert total.is_partial is True
        assert total.unmapped_position_count == 3
        assert total.data_quality_warning is not None
        assert "3 positions without strategy mapping" in total.data_quality_warning

    def test_no_unmapped_no_warning(self) -> None:
        positions = [_make_position("AAPL", 100, 150.0)]
        result = ExposureQueryResult(positions=positions, excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures,
            is_placeholder=is_placeholder,
            excluded_symbol_count=0,
            unmapped_position_count=0,
        )

        assert total.unmapped_position_count == 0
        assert total.data_quality_warning is None


class TestNoStrategiesAssigned:
    @pytest.mark.asyncio()
    async def test_no_strategies_treated_as_admin_single_admin(self) -> None:
        """P6T19: Empty strategies treated as VIEW_ALL — single-admin model."""
        user = {"role": "operator", "user_id": "op-1", "strategies": []}
        service = ExposureService()
        mock_result = ExposureQueryResult(positions=[], excluded_symbol_count=0)

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            exposures, total = await service.get_strategy_exposure(user, AsyncMock())

        # Single-admin: strategies=None (VIEW_ALL), so no "no strategies" warning
        assert total.is_placeholder is True
        # Warning is NOT set because strategies is None (VIEW_ALL path)
        assert total.data_quality_warning is None or "No authorized" not in (total.data_quality_warning or "")


class TestUnmappedScopedToAdmin:
    @pytest.mark.asyncio()
    async def test_all_users_see_unmapped_single_admin(self) -> None:
        """P6T19: All users see unmapped counts — single-admin model."""
        user = {"role": "operator", "user_id": "op-1", "strategies": ["alpha1"]}
        service = ExposureService()
        positions = [_make_position("AAPL", 100, 150.0)]
        mock_result = ExposureQueryResult(
            positions=positions, excluded_symbol_count=0, unmapped_position_count=5
        )

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            _, total = await service.get_strategy_exposure(user, AsyncMock())

        # Single-admin: has_view_all is True, unmapped counts visible
        assert total.unmapped_position_count == 5

    @pytest.mark.asyncio()
    async def test_empty_positions_with_unmapped_shows_unmapped_single_admin(self) -> None:
        """P6T19: Empty positions with unmapped shows real unmapped count — single-admin model."""
        user = {"role": "operator", "user_id": "op-1", "strategies": ["alpha1"]}
        service = ExposureService()
        mock_result = ExposureQueryResult(
            positions=[], excluded_symbol_count=0, unmapped_position_count=5
        )

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            exposures, total = await service.get_strategy_exposure(user, AsyncMock())

        # Single-admin: has_view_all True, scoped_unmapped=5, so mock fallback may trigger
        # but unmapped_position_count is the real count
        assert total.unmapped_position_count == 5

    @pytest.mark.asyncio()
    async def test_admin_sees_unmapped(self) -> None:
        """Admin (VIEW_ALL_STRATEGIES) should see global unmapped counts."""
        user = {"role": "admin", "user_id": "admin-1", "strategies": ["alpha1"]}
        service = ExposureService()
        positions = [_make_position("AAPL", 100, 150.0)]
        mock_result = ExposureQueryResult(
            positions=positions, excluded_symbol_count=0, unmapped_position_count=5
        )

        with patch(
            "libs.web_console_services.exposure_service.get_strategy_positions",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            _, total = await service.get_strategy_exposure(user, AsyncMock())

        assert total.unmapped_position_count == 5
        assert total.data_quality_warning is not None
        assert "5 positions without strategy mapping" in total.data_quality_warning


class TestPartialWithNoExposures:
    """When positions exist but cannot be attributed, totals should be zero-valued
    with is_partial=True.  The page layer uses this to render 'exposure unavailable'
    instead of misleading $0 cards."""

    def test_excluded_only_returns_zero_partial_total(self) -> None:
        result = ExposureQueryResult(positions=[], excluded_symbol_count=3)
        exposures, is_placeholder = _aggregate_exposures(result)
        total = _build_total(
            exposures, is_placeholder=is_placeholder, excluded_symbol_count=3
        )

        assert exposures == []
        assert is_placeholder is False
        assert total.is_partial is True
        assert total.gross_total == 0.0
        assert total.net_total == 0.0
        assert total.strategy_count == 0
        assert total.data_quality_warning is not None
        assert "3 symbols excluded" in total.data_quality_warning

    def test_unmapped_only_returns_zero_partial_total(self) -> None:
        result = ExposureQueryResult(positions=[], excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(
            result, scoped_unmapped=4
        )
        total = _build_total(
            exposures,
            is_placeholder=is_placeholder,
            excluded_symbol_count=0,
            unmapped_position_count=4,
        )

        assert exposures == []
        assert is_placeholder is False
        assert total.is_partial is True
        assert total.gross_total == 0.0
        assert total.data_quality_warning is not None
        assert "4 positions without strategy mapping" in total.data_quality_warning

    def test_excluded_and_unmapped_combined(self) -> None:
        result = ExposureQueryResult(positions=[], excluded_symbol_count=2)
        exposures, is_placeholder = _aggregate_exposures(
            result, scoped_unmapped=3
        )
        total = _build_total(
            exposures,
            is_placeholder=is_placeholder,
            excluded_symbol_count=2,
            unmapped_position_count=3,
        )

        assert exposures == []
        assert total.is_partial is True
        assert "2 symbols excluded" in (total.data_quality_warning or "")
        assert "3 positions without strategy mapping" in (total.data_quality_warning or "")


class TestAllFlatPortfolio:
    def test_all_flat_falls_through_to_mock(self) -> None:
        """qty=0 filtered by ExposureQueries, so empty positions = mock fallback."""
        result = ExposureQueryResult(positions=[], excluded_symbol_count=0)
        exposures, is_placeholder = _aggregate_exposures(result)
        assert is_placeholder is True
