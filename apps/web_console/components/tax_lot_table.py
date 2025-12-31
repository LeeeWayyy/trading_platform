"""Tax lot table component."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from apps.web_console.services.tax_lot_service import TaxLot


def render_tax_lot_table(
    lots: list[TaxLot],
    *,
    on_close: Callable[[str], bool] | None = None,
) -> None:
    """Render tax lots with optional close action."""

    st.subheader("Tax Lots")

    if not lots:
        st.info("No tax lots available.")
        empty = pd.DataFrame(
            columns=[
                "Symbol",
                "Quantity",
                "Cost Basis",
                "Acquisition Date",
                "Strategy",
                "Status",
                "Actions",
            ]
        )
        st.dataframe(empty, use_container_width=True)
        return

    header_cols = st.columns([2, 2, 2, 2, 2, 2, 1])
    header_cols[0].markdown("**Symbol**")
    header_cols[1].markdown("**Quantity**")
    header_cols[2].markdown("**Cost Basis**")
    header_cols[3].markdown("**Acquisition Date**")
    header_cols[4].markdown("**Strategy**")
    header_cols[5].markdown("**Status**")
    header_cols[6].markdown("**Actions**")

    for lot in lots:
        cols = st.columns([2, 2, 2, 2, 2, 2, 1])
        cols[0].write(lot.symbol)
        cols[1].write(_format_decimal(lot.quantity))
        cols[2].write(_format_decimal(lot.cost_basis))
        cols[3].write(_format_dt(lot.acquisition_date))
        cols[4].write(lot.strategy_id or "-")
        cols[5].write(lot.status)

        if on_close is None or lot.status.lower() == "closed":
            cols[6].write("-")
            continue

        if cols[6].button("Close", key=f"close_{lot.lot_id}"):
            success = on_close(lot.lot_id)
            if success:
                st.success(f"Closed tax lot {lot.lot_id}.")
                st.rerun()
            else:
                st.error(f"Failed to close tax lot {lot.lot_id}.")


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value}"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_tax_lot_table"]
