"""Trade history table component."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd
import streamlit as st


def render_trade_table(
    trades: Sequence[dict[str, Any]],
    page_size: int,
    current_page: int,
) -> None:
    """Render trade history table."""

    if not trades:
        st.info("No trades found for the selected criteria.")
        return

    df = _trades_to_dataframe(trades)

    st.dataframe(
        df.style.map(_pnl_color, subset=["Realized P&L"]),
        use_container_width=True,
        hide_index=True,
    )

    st.caption(f"Showing {len(trades)} trades (page {current_page + 1}, page size {page_size})")


def _trades_to_dataframe(trades: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Convert trade dicts to display DataFrame."""

    return pd.DataFrame(
        [
            {
                "Date": trade.get("executed_at"),
                "Symbol": trade.get("symbol"),
                "Side": trade.get("side"),
                "Qty": trade.get("qty"),
                "Price": _format_decimal(trade.get("price")),
                "Realized P&L": _format_decimal(trade.get("realized_pnl")),
                "Strategy": trade.get("strategy_id"),
            }
            for trade in trades
        ]
    )


def _format_decimal(value: Any) -> float:
    """Convert Decimal/string to float for display."""

    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pnl_color(val: Any) -> str:
    """Return CSS color based on P&L value."""

    try:
        pnl_value = float(val)
    except (TypeError, ValueError):
        return ""

    if pnl_value > 0:
        return "color: green"
    if pnl_value < 0:
        return "color: red"
    return ""
