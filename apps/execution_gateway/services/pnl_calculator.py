"""P&L calculation functions for positions and performance.

This module extracts P&L calculation logic from main.py, providing pure functions
for calculating position P&L, resolving prices from multiple sources, and
computing daily performance metrics.

Design Rationale:
    - Pure functions (no global state)
    - Easy to test in isolation
    - Clear separation of calculation logic from API handlers
    - Supports both real-time and historical P&L calculations

Usage:
    from apps.execution_gateway.services.pnl_calculator import (
        calculate_position_pnl,
        resolve_and_calculate_pnl,
        compute_daily_performance,
    )

    # Calculate P&L for a position
    pnl = calculate_position_pnl(
        pos=position,
        current_price=Decimal("150.25"),
        price_source="real-time",
        last_price_update=datetime.now(UTC),
    )

    # Compute daily performance series
    daily, total_realized, max_drawdown = compute_daily_performance(
        rows=db_rows,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from apps.execution_gateway.schemas import DailyPnL, Position, RealtimePositionPnL


def calculate_position_pnl(
    pos: Position,
    current_price: Decimal,
    price_source: Literal["real-time", "database", "fallback"],
    last_price_update: datetime | None,
) -> RealtimePositionPnL:
    """Calculate unrealized P&L for a single position.

    This function computes unrealized profit/loss based on the difference between
    the current market price and the average entry price. The calculation works
    correctly for both long and short positions.

    Args:
        pos: Position from database with entry price and quantity
        current_price: Current market price for the position
        price_source: Source of current price (real-time/database/fallback)
        last_price_update: Timestamp of last price update (if available)

    Returns:
        RealtimePositionPnL: Position with calculated P&L values including:
            - unrealized_pl: Dollar amount of unrealized profit/loss
            - unrealized_pl_pct: Percentage return on invested capital

    Notes:
        - For long positions: P&L = (current_price - entry_price) * qty
        - For short positions: P&L = (current_price - entry_price) * qty (negative qty)
        - P&L percentage is based on actual profit/loss relative to invested capital
        - Zero quantity or zero entry price results in 0% P&L to avoid division errors

    Example:
        >>> pos = Position(symbol="AAPL", qty=100, avg_entry_price=Decimal("150"))
        >>> pnl = calculate_position_pnl(
        ...     pos=pos,
        ...     current_price=Decimal("155"),
        ...     price_source="real-time",
        ...     last_price_update=datetime.now(UTC),
        ... )
        >>> pnl.unrealized_pl  # (155 - 150) * 100 = 500
        Decimal('500')
        >>> pnl.unrealized_pl_pct  # (500 / 15000) * 100 = 3.33%
        Decimal('3.33')
    """
    # Calculate unrealized P&L
    unrealized_pl = (current_price - pos.avg_entry_price) * pos.qty

    # Calculate P&L percentage based on actual profit/loss
    # This works correctly for both long and short positions
    unrealized_pl_pct = (
        (unrealized_pl / (pos.avg_entry_price * abs(pos.qty))) * Decimal("100")
        if pos.avg_entry_price > 0 and pos.qty != 0
        else Decimal("0")
    )

    return RealtimePositionPnL(
        symbol=pos.symbol,
        qty=pos.qty,
        avg_entry_price=pos.avg_entry_price,
        current_price=current_price,
        price_source=price_source,
        unrealized_pl=unrealized_pl,
        unrealized_pl_pct=unrealized_pl_pct,
        last_price_update=last_price_update,
    )


def resolve_and_calculate_pnl(
    pos: Position,
    realtime_price_data: tuple[Decimal | None, datetime | None],
) -> tuple[RealtimePositionPnL, bool]:
    """Resolve price from multiple sources and calculate P&L for a position.

    Implements three-tier price fallback strategy:
    1. Real-time price from Redis (Market Data Service) - most current
    2. Database price (last known price) - stale but available
    3. Entry price (ultimate fallback) - shows 0% P&L but prevents errors

    This fallback hierarchy ensures P&L calculations can always complete even
    when real-time data sources are unavailable.

    Args:
        pos: Position from database with entry price and quantity
        realtime_price_data: Tuple of (price, timestamp) from batch Redis fetch
            - price: Real-time price if available, None otherwise
            - timestamp: When the price was last updated

    Returns:
        Tuple of (position P&L, is_realtime flag):
            - position P&L: RealtimePositionPnL with calculated values
            - is_realtime: True if using real-time price, False for fallback

    Notes:
        - Extracted from get_realtime_pnl for improved modularity
        - Makes main endpoint loop more concise and readable
        - Replaces deprecated single-symbol _fetch_realtime_price_from_redis
        - is_realtime flag helps distinguish current vs stale/fallback prices

    Example:
        >>> pos = Position(symbol="AAPL", qty=100, avg_entry_price=Decimal("150"))
        >>> # Real-time price available
        >>> pnl, is_rt = resolve_and_calculate_pnl(
        ...     pos, (Decimal("155"), datetime.now(UTC))
        ... )
        >>> is_rt
        True
        >>> pnl.price_source
        'real-time'
        >>>
        >>> # No real-time price, use database fallback
        >>> pos.current_price = Decimal("154")
        >>> pnl, is_rt = resolve_and_calculate_pnl(pos, (None, None))
        >>> is_rt
        False
        >>> pnl.price_source
        'database'

    See Also:
        - Gemini review: apps/execution_gateway/main.py MEDIUM priority refactoring
    """
    realtime_price, last_price_update = realtime_price_data

    # Three-tier price fallback
    current_price: Decimal
    price_source: Literal["real-time", "database", "fallback"]
    is_realtime: bool

    if realtime_price is not None:
        current_price, price_source, is_realtime = realtime_price, "real-time", True
    elif pos.current_price is not None:
        current_price, price_source, is_realtime = pos.current_price, "database", False
        last_price_update = None
    else:
        current_price, price_source, is_realtime = pos.avg_entry_price, "fallback", False
        last_price_update = None

    # Calculate P&L with resolved price
    position_pnl = calculate_position_pnl(pos, current_price, price_source, last_price_update)

    return position_pnl, is_realtime


def compute_daily_performance(
    rows: list[dict[str, Any]], start_date: date, end_date: date
) -> tuple[list[DailyPnL], Decimal, Decimal]:
    """Build filled daily series with cumulative P&L and drawdown.

    This function processes daily realized P&L data and computes:
    - Cumulative realized P&L (running total)
    - Peak equity (highest cumulative P&L achieved)
    - Drawdown percentage (decline from peak as percentage of peak)
    - Maximum drawdown (worst drawdown in the period)

    The function fills missing dates with zero P&L to create a continuous series.
    Drawdown is measured versus the running peak of cumulative P&L. The peak is
    initialized to the first cumulative value to correctly capture sequences
    that begin with losses (all-negative series).

    Args:
        rows: List of database rows with daily P&L data. Each row must contain:
            - trade_date: date of the trading day
            - daily_realized_pl: realized profit/loss for the day
            - closing_trade_count: number of trades that closed positions
        start_date: Start of the date range (inclusive)
        end_date: End of the date range (inclusive)

    Returns:
        Tuple of (daily series, total realized P&L, max drawdown):
            - daily series: List of DailyPnL objects with filled dates
            - total realized P&L: Sum of all realized P&L in the period
            - max drawdown: Worst drawdown percentage (negative value)

    Notes:
        - Missing dates are filled with zero P&L and previous cumulative values
        - First trade date seeds the peak to handle all-negative sequences
        - Drawdown uses absolute peak to handle negative starting equity
        - Range expansion: If database returns dates outside requested range,
          the function expands to cover them (supports mocked test data)

    Design Decision:
        The range expansion behavior is kept in production code to ensure test/prod
        parity and gracefully handle edge cases where fill timestamps cross date
        boundaries. Alternative would be to move to test helper, but risks divergence.

    Example:
        >>> rows = [
        ...     {"trade_date": date(2024, 1, 1), "daily_realized_pl": 100, "closing_trade_count": 2},
        ...     {"trade_date": date(2024, 1, 3), "daily_realized_pl": -50, "closing_trade_count": 1},
        ... ]
        >>> daily, total, max_dd = compute_daily_performance(
        ...     rows, date(2024, 1, 1), date(2024, 1, 3)
        ... )
        >>> len(daily)  # 3 days (includes filled 1/2)
        3
        >>> total  # 100 + (-50) = 50
        Decimal('50')
        >>> max_dd  # Worst drawdown percentage
        Decimal('-50.0')  # From peak of 100 to 50
    """
    if not rows:
        return [], Decimal("0"), Decimal("0")

    # DESIGN DECISION: Expand requested range to cover returned data.
    # This supports mocked data in tests where mock databases may return dates outside
    # the requested range. Keeping this in production code ensures test/prod parity
    # and gracefully handles edge cases where fill timestamps cross date boundaries.
    # Alternative: Move to test helper, but risks test/prod divergence.
    trade_dates: list[date] = [
        t for t in (r.get("trade_date") for r in rows) if isinstance(t, date)
    ]
    if trade_dates:
        earliest = min(trade_dates)
        latest = max(trade_dates)
        if earliest < start_date:
            start_date = earliest
        if latest > end_date:
            end_date = latest

    # Build lookup dictionary for fast access
    rows_by_date: dict[date, dict[str, Decimal | int]] = {}
    for r in rows:
        trade_date_raw = r.get("trade_date")
        if not isinstance(trade_date_raw, date):
            continue
        rows_by_date[trade_date_raw] = {
            "realized_pl": Decimal(str(r.get("daily_realized_pl", 0))),
            "closing_trade_count": int(r.get("closing_trade_count") or 0),
        }

    # Initialize tracking variables
    daily: list[DailyPnL] = []
    cumulative = Decimal("0")
    peak: Decimal | None = None  # first cumulative value will seed peak
    max_drawdown = Decimal("0")

    # Skip leading days with no data so peak is seeded by first real trade day
    first_trade_date = min(rows_by_date.keys()) if rows_by_date else start_date
    current = max(start_date, first_trade_date)
    one_day = timedelta(days=1)

    # Build daily series with filled dates
    while current <= end_date:
        day_data = rows_by_date.get(
            current, {"realized_pl": Decimal("0"), "closing_trade_count": 0}
        )
        realized = cast(Decimal, day_data["realized_pl"])
        closing_count = int(day_data["closing_trade_count"])

        # Update cumulative P&L and peak
        cumulative += realized
        if peak is None:
            peak = cumulative
        if cumulative > peak:
            peak = cumulative

        # Calculate drawdown percentage
        # Use absolute peak to handle negative starting equity; avoid divide by zero
        if peak != 0:
            drawdown_pct = (cumulative - peak) / abs(peak) * Decimal("100")
        else:
            drawdown_pct = Decimal("0")

        # Track maximum drawdown (most negative value)
        if drawdown_pct < max_drawdown:
            max_drawdown = drawdown_pct

        daily.append(
            DailyPnL(
                date=current,
                realized_pl=realized,
                cumulative_realized_pl=cumulative,
                peak_equity=peak,
                drawdown_pct=drawdown_pct,
                closing_trade_count=closing_count,
            )
        )

        current += one_day

    return daily, cumulative, max_drawdown
