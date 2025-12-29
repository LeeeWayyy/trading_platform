"""Stock-level exposure drill-down table."""

from __future__ import annotations

import polars as pl
import streamlit as st


def render_stock_exposure_table(df: pl.DataFrame) -> None:
    """Render stock-level exposures for a selected factor.

    Args:
        df: Polars DataFrame with columns [symbol, weight, exposure, contribution].

    Returns:
        None. Renders a Streamlit table with optional summary metrics.

    Example:
        >>> data = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "weight": [0.1],
        ...     "exposure": [1.2],
        ...     "contribution": [0.12],
        ... })
        >>> render_stock_exposure_table(data)
    """

    if df is None or df.is_empty():
        st.info("No stock-level exposure data available.")
        return

    required = {"symbol", "weight", "exposure", "contribution"}
    missing = required.difference(df.columns)
    if missing:
        st.error(f"Missing required columns: {sorted(missing)}")
        return

    total_contribution = df.select(pl.col("contribution").sum()).item()
    top_row = df.row(0)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Exposure", f"{total_contribution:.3f}")
    col2.metric("Top Contributor", str(top_row[0]))
    col3.metric("Stocks", str(df.height))

    # Convert fractional weight to percentage for display (0.1 -> 10.0)
    display_df = df.with_columns((pl.col("weight") * 100).alias("weight_pct"))

    st.dataframe(
        display_df.select(["symbol", "weight_pct", "exposure", "contribution"]).to_pandas(),
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "weight_pct": st.column_config.NumberColumn("Weight (%)", format="%.2f%%"),
            "exposure": st.column_config.NumberColumn("Exposure", format="%.3f"),
            "contribution": st.column_config.NumberColumn("Contribution", format="%.4f"),
        },
        hide_index=True,
        use_container_width=True,
    )


__all__ = ["render_stock_exposure_table"]
