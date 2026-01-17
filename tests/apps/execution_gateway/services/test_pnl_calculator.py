"""Tests for P&L calculation service.

This test suite validates P&L calculation logic extracted from main.py,
ensuring accuracy for:
- Position P&L calculations (long and short positions)
- Price resolution fallback hierarchy
- Daily performance series computation
- Drawdown tracking and peak equity calculation
- Edge cases (zero quantities, negative sequences, missing data)

Target: 90%+ coverage per Phase 1 requirements.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from apps.execution_gateway.schemas import Position
from apps.execution_gateway.services.pnl_calculator import (
    calculate_position_pnl,
    compute_daily_performance,
    resolve_and_calculate_pnl,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def sample_long_position() -> Position:
    """Sample long position for testing."""
    return Position(
        symbol="AAPL",
        qty=Decimal("100"),
        avg_entry_price=Decimal("150.00"),
        current_price=Decimal("155.00"),  # Database fallback price
        realized_pl=Decimal("0"),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture()
def sample_short_position() -> Position:
    """Sample short position for testing."""
    return Position(
        symbol="TSLA",
        qty=Decimal("-50"),  # Negative quantity for short
        avg_entry_price=Decimal("200.00"),
        current_price=Decimal("195.00"),  # Database fallback price
        realized_pl=Decimal("0"),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture()
def sample_zero_position() -> Position:
    """Sample flat position (zero quantity)."""
    return Position(
        symbol="MSFT",
        qty=Decimal("0"),
        avg_entry_price=Decimal("300.00"),
        current_price=Decimal("310.00"),
        realized_pl=Decimal("100.50"),
        updated_at=datetime.now(UTC),
    )


# ============================================================================
# Test calculate_position_pnl
# ============================================================================


def test_calculate_position_pnl_long_profit(sample_long_position: Position) -> None:
    """Test P&L calculation for profitable long position."""
    now = datetime.now(UTC)
    pnl = calculate_position_pnl(
        pos=sample_long_position,
        current_price=Decimal("155.00"),
        price_source="real-time",
        last_price_update=now,
    )

    # Expected: (155 - 150) * 100 = 500
    assert pnl.unrealized_pl == Decimal("500.00")
    # Expected: (500 / 15000) * 100 = 3.33%
    assert abs(pnl.unrealized_pl_pct - Decimal("3.33")) < Decimal("0.01")
    assert pnl.symbol == "AAPL"
    assert pnl.qty == Decimal("100")
    assert pnl.price_source == "real-time"
    assert pnl.last_price_update == now


def test_calculate_position_pnl_long_loss(sample_long_position: Position) -> None:
    """Test P&L calculation for losing long position."""
    pnl = calculate_position_pnl(
        pos=sample_long_position,
        current_price=Decimal("145.00"),
        price_source="database",
        last_price_update=None,
    )

    # Expected: (145 - 150) * 100 = -500
    assert pnl.unrealized_pl == Decimal("-500.00")
    # Expected: (-500 / 15000) * 100 = -3.33%
    assert abs(pnl.unrealized_pl_pct - Decimal("-3.33")) < Decimal("0.01")
    assert pnl.price_source == "database"


def test_calculate_position_pnl_short_profit(sample_short_position: Position) -> None:
    """Test P&L calculation for profitable short position."""
    pnl = calculate_position_pnl(
        pos=sample_short_position,
        current_price=Decimal("195.00"),
        price_source="real-time",
        last_price_update=datetime.now(UTC),
    )

    # Expected: (195 - 200) * (-50) = (-5) * (-50) = 250
    assert pnl.unrealized_pl == Decimal("250.00")
    # Expected: (250 / 10000) * 100 = 2.5%
    assert pnl.unrealized_pl_pct == Decimal("2.50")
    assert pnl.symbol == "TSLA"


def test_calculate_position_pnl_short_loss(sample_short_position: Position) -> None:
    """Test P&L calculation for losing short position."""
    pnl = calculate_position_pnl(
        pos=sample_short_position,
        current_price=Decimal("205.00"),
        price_source="fallback",
        last_price_update=None,
    )

    # Expected: (205 - 200) * (-50) = 5 * (-50) = -250
    assert pnl.unrealized_pl == Decimal("-250.00")
    # Expected: (-250 / 10000) * 100 = -2.5%
    assert pnl.unrealized_pl_pct == Decimal("-2.50")


def test_calculate_position_pnl_zero_quantity(sample_zero_position: Position) -> None:
    """Test P&L calculation for zero quantity (flat position)."""
    pnl = calculate_position_pnl(
        pos=sample_zero_position,
        current_price=Decimal("310.00"),
        price_source="real-time",
        last_price_update=datetime.now(UTC),
    )

    # Expected: Zero P&L for flat position
    assert pnl.unrealized_pl == Decimal("0")
    assert pnl.unrealized_pl_pct == Decimal("0")


def test_calculate_position_pnl_zero_entry_price() -> None:
    """Test P&L calculation with zero entry price (edge case)."""
    pos = Position(
        symbol="TEST",
        qty=Decimal("100"),
        avg_entry_price=Decimal("0"),  # Edge case
        current_price=Decimal("100"),
        realized_pl=Decimal("0"),
        updated_at=datetime.now(UTC),
    )

    pnl = calculate_position_pnl(
        pos=pos,
        current_price=Decimal("100"),
        price_source="real-time",
        last_price_update=None,
    )

    # Expected: Avoid division by zero
    assert pnl.unrealized_pl_pct == Decimal("0")


# ============================================================================
# Test resolve_and_calculate_pnl
# ============================================================================


def test_resolve_pnl_realtime_price_available(sample_long_position: Position) -> None:
    """Test price resolution when real-time price is available (tier 1)."""
    now = datetime.now(UTC)
    realtime_data = (Decimal("157.50"), now)

    pnl, is_realtime = resolve_and_calculate_pnl(sample_long_position, realtime_data)

    assert is_realtime is True
    assert pnl.price_source == "real-time"
    assert pnl.current_price == Decimal("157.50")
    assert pnl.last_price_update == now
    # Expected: (157.50 - 150) * 100 = 750
    assert pnl.unrealized_pl == Decimal("750.00")


def test_resolve_pnl_database_fallback(sample_long_position: Position) -> None:
    """Test price resolution fallback to database price (tier 2)."""
    # No real-time price, use database fallback
    realtime_data = (None, None)

    pnl, is_realtime = resolve_and_calculate_pnl(sample_long_position, realtime_data)

    assert is_realtime is False
    assert pnl.price_source == "database"
    assert pnl.current_price == Decimal("155.00")  # From pos.current_price
    assert pnl.last_price_update is None
    # Expected: (155 - 150) * 100 = 500
    assert pnl.unrealized_pl == Decimal("500.00")


def test_resolve_pnl_entry_price_fallback() -> None:
    """Test price resolution fallback to entry price (tier 3)."""
    pos = Position(
        symbol="AAPL",
        qty=Decimal("100"),
        avg_entry_price=Decimal("150.00"),
        current_price=None,  # No database price
        realized_pl=Decimal("0"),
        updated_at=datetime.now(UTC),
    )
    realtime_data = (None, None)  # No real-time price

    pnl, is_realtime = resolve_and_calculate_pnl(pos, realtime_data)

    assert is_realtime is False
    assert pnl.price_source == "fallback"
    assert pnl.current_price == Decimal("150.00")  # Entry price fallback
    assert pnl.last_price_update is None
    # Expected: Zero P&L when using entry price
    assert pnl.unrealized_pl == Decimal("0")
    assert pnl.unrealized_pl_pct == Decimal("0")


def test_resolve_pnl_short_position_realtime(sample_short_position: Position) -> None:
    """Test price resolution for short position with real-time price."""
    now = datetime.now(UTC)
    realtime_data = (Decimal("192.00"), now)

    pnl, is_realtime = resolve_and_calculate_pnl(sample_short_position, realtime_data)

    assert is_realtime is True
    assert pnl.price_source == "real-time"
    # Expected: (192 - 200) * (-50) = 400
    assert pnl.unrealized_pl == Decimal("400.00")


# ============================================================================
# Test compute_daily_performance
# ============================================================================


def test_compute_daily_performance_basic_series() -> None:
    """Test basic daily performance computation with filled dates."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100, "closing_trade_count": 2},
        # Missing 1/2 (should be filled with zero)
        {"trade_date": date(2024, 1, 3), "daily_realized_pl": -50, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 3)
    )

    # Verify series length (3 days with filled 1/2)
    assert len(daily) == 3

    # Verify first day
    assert daily[0].date == date(2024, 1, 1)
    assert daily[0].realized_pl == Decimal("100")
    assert daily[0].cumulative_realized_pl == Decimal("100")
    assert daily[0].peak_equity == Decimal("100")
    assert daily[0].drawdown_pct == Decimal("0")
    assert daily[0].closing_trade_count == 2

    # Verify filled day (1/2)
    assert daily[1].date == date(2024, 1, 2)
    assert daily[1].realized_pl == Decimal("0")
    assert daily[1].cumulative_realized_pl == Decimal("100")
    assert daily[1].peak_equity == Decimal("100")
    assert daily[1].drawdown_pct == Decimal("0")
    assert daily[1].closing_trade_count == 0

    # Verify last day
    assert daily[2].date == date(2024, 1, 3)
    assert daily[2].realized_pl == Decimal("-50")
    assert daily[2].cumulative_realized_pl == Decimal("50")
    assert daily[2].peak_equity == Decimal("100")
    # Drawdown: (50 - 100) / 100 * 100 = -50%
    assert daily[2].drawdown_pct == Decimal("-50")
    assert daily[2].closing_trade_count == 1

    # Verify totals
    assert total_realized == Decimal("50")
    assert max_drawdown == Decimal("-50")


def test_compute_daily_performance_all_negative_sequence() -> None:
    """Test daily performance with all-negative P&L (losing sequence)."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": -100, "closing_trade_count": 1},
        {"trade_date": date(2024, 1, 2), "daily_realized_pl": -50, "closing_trade_count": 1},
        {"trade_date": date(2024, 1, 3), "daily_realized_pl": 20, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 3)
    )

    # Verify peak is seeded by first cumulative (-100)
    assert daily[0].peak_equity == Decimal("-100")
    assert daily[0].drawdown_pct == Decimal("0")

    # Verify drawdown calculation with negative peak
    # Day 2: cumulative = -150, peak = -100, drawdown = (-150 - (-100)) / 100 * 100 = -50%
    assert daily[1].cumulative_realized_pl == Decimal("-150")
    assert daily[1].peak_equity == Decimal("-100")
    assert daily[1].drawdown_pct == Decimal("-50")

    # Day 3: cumulative = -130, peak = -100, drawdown = (-130 - (-100)) / 100 * 100 = -30%
    assert daily[2].cumulative_realized_pl == Decimal("-130")
    assert daily[2].peak_equity == Decimal("-100")
    assert daily[2].drawdown_pct == Decimal("-30")

    assert total_realized == Decimal("-130")
    assert max_drawdown == Decimal("-50")


def test_compute_daily_performance_peak_updates() -> None:
    """Test that peak equity updates correctly as cumulative increases."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100, "closing_trade_count": 1},
        {"trade_date": date(2024, 1, 2), "daily_realized_pl": 200, "closing_trade_count": 2},
        {"trade_date": date(2024, 1, 3), "daily_realized_pl": 50, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 3)
    )

    # Verify peak updates
    assert daily[0].peak_equity == Decimal("100")
    assert daily[1].peak_equity == Decimal("300")  # New peak
    assert daily[2].peak_equity == Decimal("350")  # New peak

    # Verify no drawdown when always increasing
    assert daily[0].drawdown_pct == Decimal("0")
    assert daily[1].drawdown_pct == Decimal("0")
    assert daily[2].drawdown_pct == Decimal("0")

    assert total_realized == Decimal("350")
    assert max_drawdown == Decimal("0")


def test_compute_daily_performance_empty_rows() -> None:
    """Test daily performance computation with empty data."""
    daily, total_realized, max_drawdown = compute_daily_performance(
        [], date(2024, 1, 1), date(2024, 1, 3)
    )

    assert daily == []
    assert total_realized == Decimal("0")
    assert max_drawdown == Decimal("0")


def test_compute_daily_performance_range_expansion() -> None:
    """Test that date range expands to cover all returned data."""
    # Database returns dates outside requested range
    rows = [
        {"trade_date": date(2023, 12, 30), "daily_realized_pl": 50, "closing_trade_count": 1},
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100, "closing_trade_count": 1},
        {"trade_date": date(2024, 1, 5), "daily_realized_pl": 75, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 3)  # Original range
    )

    # Range should expand to 12/30 - 1/5
    assert daily[0].date == date(2023, 12, 30)
    assert daily[-1].date == date(2024, 1, 5)
    assert len(daily) == 7  # 12/30 to 1/5 inclusive

    assert total_realized == Decimal("225")


def test_compute_daily_performance_invalid_date_types() -> None:
    """Test that invalid date types are filtered out."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100, "closing_trade_count": 1},
        {"trade_date": "2024-01-02", "daily_realized_pl": 50, "closing_trade_count": 1},  # Invalid
        {"trade_date": None, "daily_realized_pl": 25, "closing_trade_count": 1},  # Invalid
        {"trade_date": date(2024, 1, 3), "daily_realized_pl": 75, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 3)
    )

    # Should only process valid dates (1/1 and 1/3), fill 1/2
    assert len(daily) == 3
    assert daily[0].date == date(2024, 1, 1)
    assert daily[1].date == date(2024, 1, 2)  # Filled
    assert daily[2].date == date(2024, 1, 3)

    # Only valid rows contribute to total
    assert total_realized == Decimal("175")


def test_compute_daily_performance_zero_peak_edge_case() -> None:
    """Test drawdown calculation when peak is exactly zero."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100, "closing_trade_count": 1},
        {"trade_date": date(2024, 1, 2), "daily_realized_pl": -100, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 2)
    )

    # Day 1: cumulative = 100, peak = 100
    assert daily[0].peak_equity == Decimal("100")

    # Day 2: cumulative = 0, peak = 100
    # Drawdown: (0 - 100) / 100 * 100 = -100%
    assert daily[1].cumulative_realized_pl == Decimal("0")
    assert daily[1].peak_equity == Decimal("100")
    assert daily[1].drawdown_pct == Decimal("-100")

    assert max_drawdown == Decimal("-100")


def test_compute_daily_performance_zero_cumulative_first_day() -> None:
    """Test computation when first day has zero P&L."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 0, "closing_trade_count": 0},
        {"trade_date": date(2024, 1, 2), "daily_realized_pl": 100, "closing_trade_count": 1},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 2)
    )

    # Day 1: cumulative = 0, peak = 0
    assert daily[0].cumulative_realized_pl == Decimal("0")
    assert daily[0].peak_equity == Decimal("0")
    # Drawdown with zero peak should be 0 (avoid division by zero)
    assert daily[0].drawdown_pct == Decimal("0")

    # Day 2: cumulative = 100, new peak = 100
    assert daily[1].cumulative_realized_pl == Decimal("100")
    assert daily[1].peak_equity == Decimal("100")

    assert total_realized == Decimal("100")
    assert max_drawdown == Decimal("0")


def test_calculate_position_pnl_preserves_symbol_info(sample_long_position: Position) -> None:
    """Test that P&L calculation preserves all position info."""
    now = datetime.now(UTC)
    pnl = calculate_position_pnl(
        pos=sample_long_position,
        current_price=Decimal("155.00"),
        price_source="real-time",
        last_price_update=now,
    )

    # Verify all position fields are preserved
    assert pnl.symbol == sample_long_position.symbol
    assert pnl.qty == sample_long_position.qty
    assert pnl.avg_entry_price == sample_long_position.avg_entry_price
    assert pnl.current_price == Decimal("155.00")


def test_resolve_pnl_with_zero_realtime_price() -> None:
    """Test price resolution with zero as real-time price (edge case)."""
    pos = Position(
        symbol="AAPL",
        qty=Decimal("100"),
        avg_entry_price=Decimal("150.00"),
        current_price=Decimal("155.00"),
        realized_pl=Decimal("0"),
        updated_at=datetime.now(UTC),
    )
    now = datetime.now(UTC)
    # Zero is a valid price (though unlikely in practice)
    realtime_data = (Decimal("0"), now)

    pnl, is_realtime = resolve_and_calculate_pnl(pos, realtime_data)

    assert is_realtime is True
    assert pnl.price_source == "real-time"
    assert pnl.current_price == Decimal("0")
    # P&L: (0 - 150) * 100 = -15000
    assert pnl.unrealized_pl == Decimal("-15000.00")


def test_compute_daily_performance_missing_closing_trade_count() -> None:
    """Test handling of rows with missing closing_trade_count."""
    rows = [
        {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100},  # Missing closing_trade_count
        {"trade_date": date(2024, 1, 2), "daily_realized_pl": 50, "closing_trade_count": None},  # Explicit None
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 1), date(2024, 1, 2)
    )

    # Should default to 0 for missing/None closing_trade_count
    assert daily[0].closing_trade_count == 0
    assert daily[1].closing_trade_count == 0

    assert total_realized == Decimal("150")


def test_compute_daily_performance_single_day() -> None:
    """Test computation with a single day of data."""
    rows = [
        {"trade_date": date(2024, 1, 15), "daily_realized_pl": 250, "closing_trade_count": 3},
    ]

    daily, total_realized, max_drawdown = compute_daily_performance(
        rows, date(2024, 1, 15), date(2024, 1, 15)
    )

    assert len(daily) == 1
    assert daily[0].date == date(2024, 1, 15)
    assert daily[0].realized_pl == Decimal("250")
    assert daily[0].cumulative_realized_pl == Decimal("250")
    assert daily[0].peak_equity == Decimal("250")
    assert daily[0].drawdown_pct == Decimal("0")
    assert daily[0].closing_trade_count == 3

    assert total_realized == Decimal("250")
    assert max_drawdown == Decimal("0")


def test_calculate_position_pnl_breakeven() -> None:
    """Test P&L calculation at breakeven (price = entry)."""
    pos = Position(
        symbol="AAPL",
        qty=Decimal("100"),
        avg_entry_price=Decimal("150.00"),
        current_price=Decimal("150.00"),
        realized_pl=Decimal("0"),
        updated_at=datetime.now(UTC),
    )

    pnl = calculate_position_pnl(
        pos=pos,
        current_price=Decimal("150.00"),  # Same as entry
        price_source="real-time",
        last_price_update=datetime.now(UTC),
    )

    assert pnl.unrealized_pl == Decimal("0")
    assert pnl.unrealized_pl_pct == Decimal("0")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
