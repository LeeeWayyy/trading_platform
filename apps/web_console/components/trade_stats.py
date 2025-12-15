"""Trade statistics display component."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import streamlit as st

BREAK_EVEN_EPSILON = Decimal("0.01")


def calculate_win_rate(winning: int, total: int) -> float:
    """Calculate win rate with divide-by-zero protection."""

    if total == 0:
        return 0.0
    return (winning / total) * 100


def calculate_profit_factor(gross_profit: Decimal, gross_loss: Decimal) -> float | None:
    """Return profit factor, or None when gross_loss is zero."""

    if gross_loss == 0:
        return None
    return float(gross_profit / gross_loss)


def _safe_int(value: Any) -> int:
    """Convert to int with a zero fallback for None/invalid values."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _maybe_decimal(value: Any) -> Decimal | None:
    """Convert to Decimal when possible, otherwise return None."""

    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_or_zero(value: Any) -> Decimal:
    """Convert to Decimal with zero fallback for invalid values."""

    decimal_value = _maybe_decimal(value)
    if decimal_value is None:
        return Decimal("0")
    return decimal_value


def _format_currency(value: Decimal | None) -> str:
    """Format currency values with fallback for missing data."""

    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def render_trade_stats(stats: dict[str, Any]) -> None:
    """Render aggregated trade statistics in three rows of Streamlit metrics."""

    total = _safe_int(stats.get("total_trades", 0))
    winning = _safe_int(stats.get("winning_trades", 0))
    losing = _safe_int(stats.get("losing_trades", 0))
    break_even = _safe_int(stats.get("break_even_trades", 0))

    total_pnl = _decimal_or_zero(stats.get("total_realized_pnl"))
    gross_profit = _decimal_or_zero(stats.get("gross_profit"))
    gross_loss = _decimal_or_zero(stats.get("gross_loss"))

    avg_win = _maybe_decimal(stats.get("avg_win"))
    avg_loss = _maybe_decimal(stats.get("avg_loss"))
    largest_win = _maybe_decimal(stats.get("largest_win"))
    largest_loss = _maybe_decimal(stats.get("largest_loss"))

    st.subheader("Trade Statistics")

    cols = st.columns(4)
    cols[0].metric("Total Trades", f"{total:,}")
    cols[1].metric("Win Rate", f"{calculate_win_rate(winning, total):.1f}%")
    cols[2].metric("Total P&L", _format_currency(total_pnl))

    profit_factor = calculate_profit_factor(gross_profit, gross_loss)
    cols[3].metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor is not None else "N/A")

    cols2 = st.columns(4)
    cols2[0].metric("Winning Trades", f"{winning:,}")
    cols2[1].metric("Losing Trades", f"{losing:,}")
    cols2[2].metric("Break-Even", f"{break_even:,}")
    cols2[3].metric("Gross Profit", _format_currency(gross_profit))

    cols3 = st.columns(4)
    cols3[0].metric("Avg Win", _format_currency(avg_win))
    cols3[1].metric("Avg Loss", _format_currency(avg_loss))
    cols3[2].metric("Largest Win", _format_currency(largest_win))
    cols3[3].metric("Largest Loss", _format_currency(largest_loss))

