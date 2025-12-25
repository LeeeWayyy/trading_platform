# P4T7 C2: Factor Exposure Heatmap - Component Plan

**Component:** C2 - T9.2 Factor Exposure Heatmap
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING
**Estimated Effort:** 2-3 days
**Dependencies:** C0 (Prep & Validation)

---

## Overview

Implement T9.2 Factor Exposure Heatmap page that enables researchers to visualize and analyze portfolio factor exposures over time.

## Acceptance Criteria (from P4T7_TASK.md)

- [ ] Interactive factor exposure heatmap (rows: factors, columns: dates or strategies)
- [ ] Color scale: red (negative) → white (neutral) → green (positive) exposure
- [ ] Portfolio vs benchmark exposure comparison view
- [ ] Time-series evolution of exposures (animated or slider-controlled)
- [ ] Drill-down to stock-level exposures for selected factor
- [ ] Factor definitions from `libs/factors/factor_definitions.py`
- [ ] Export heatmap as PNG or CSV data
- [ ] RBAC: VIEW_FACTOR_ANALYTICS permission required

---

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────────┐
│                   Streamlit Page: factor_heatmap.py             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Portfolio      │  │  Factor         │  │  Time Slider    │ │
│  │  Selector       │  │  Heatmap        │  │  / Animation    │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
│           │                    │                     │          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              Stock-Level Drill-Down Table                   ││
│  └─────────────────────────────────────────────────────────────┘│
└───────────┬─────────────────────┬─────────────────────┬─────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              FactorExposureService (services/)                   │
│  - get_portfolio_exposures(portfolio_id, date_range) → df       │
│  - get_benchmark_exposures(benchmark, date_range) → df          │
│  - get_stock_exposures(portfolio_id, factor, date) → df         │
│  - get_factor_definitions() → list[FactorDef]                   │
└───────────────────────────────────────────────────────────────────┘
            │                     │
            ▼                     ▼
┌─────────────────┐   ┌─────────────────┐
│ FactorBuilder   │   │ FactorDefs      │
│ (libs/factors/) │   │ (libs/factors/) │
└─────────────────┘   └─────────────────┘
```

### File Structure

```
apps/web_console/
├── pages/
│   └── factor_heatmap.py        # Main Streamlit page
├── components/
│   ├── heatmap_chart.py         # Interactive Plotly heatmap
│   ├── exposure_timeseries.py   # Time-series evolution
│   └── stock_exposure_table.py  # Stock-level drill-down
├── services/
│   └── factor_exposure_service.py  # Factor calculation service

tests/apps/web_console/
├── pages/
│   └── test_factor_heatmap.py   # Page integration tests
├── services/
│   └── test_factor_exposure_service.py  # Service unit tests

docs/CONCEPTS/
└── factor-exposure-visualization.md  # User documentation
```

---

## Implementation Details

### 1. FactorExposureService

```python
# apps/web_console/services/factor_exposure_service.py
"""Service layer for factor exposure calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from libs.factors.factor_builder import FactorBuilder


@dataclass
class FactorDefinition:
    """Factor definition for display."""
    name: str
    category: str  # value, quality, momentum, size, low_vol
    description: str


@dataclass
class ExposureData:
    """Factor exposure data for visualization."""
    exposures: pl.DataFrame  # [date, factor, exposure]
    factors: list[str]
    date_range: tuple[date, date]


class FactorExposureService:
    """Service for computing and retrieving factor exposures."""

    def __init__(self, factor_builder: FactorBuilder):
        self._builder = factor_builder

    def get_factor_definitions(self) -> list[FactorDefinition]:
        """Get all available factor definitions."""
        from libs.factors.factor_definitions import FACTOR_REGISTRY

        return [
            FactorDefinition(
                name=f.name,
                category=f.category,
                description=f.description,
            )
            for f in FACTOR_REGISTRY.values()
        ]

    def get_portfolio_exposures(
        self,
        portfolio_id: str,
        start_date: date,
        end_date: date,
        factors: list[str] | None = None,
    ) -> ExposureData:
        """Compute factor exposures for a portfolio over time.

        Args:
            portfolio_id: Portfolio to analyze
            start_date: Start of date range
            end_date: End of date range
            factors: Specific factors to compute (None = all)

        Returns:
            ExposureData with daily exposures for each factor
        """
        if factors is None:
            factors = [f.name for f in self.get_factor_definitions()]

        # Get portfolio holdings for each date
        # TODO: Integrate with position data source

        # For each date, compute factor exposures
        results = []
        current_date = start_date
        while current_date <= end_date:
            # Get holdings as of date
            holdings = self._get_portfolio_holdings(portfolio_id, current_date)

            if holdings is not None and not holdings.is_empty():
                # Get PERMNOs from holdings for factor computation
                permnos = holdings.select("permno").to_series().to_list()

                # Compute all factors for universe (uses actual FactorBuilder API)
                factor_result = self._builder.compute_all_factors(
                    as_of_date=current_date,
                    universe=permnos,
                )

                # Weight-average per-stock exposures by portfolio weights
                for factor in factors:
                    factor_exposures = factor_result.exposures.filter(
                        pl.col("factor_name") == factor
                    )
                    # Join with holdings weights
                    merged = factor_exposures.join(
                        holdings.select(["permno", "weight"]),
                        on="permno",
                    )
                    # Portfolio exposure = sum(weight * zscore)
                    portfolio_exposure = (
                        merged.select((pl.col("weight") * pl.col("zscore")).sum())
                        .item()
                    )
                    results.append({
                        "date": current_date,
                        "factor": factor,
                        "exposure": portfolio_exposure,
                    })

            current_date = current_date + timedelta(days=1)

        exposures_df = pl.DataFrame(results) if results else pl.DataFrame(
            schema={"date": pl.Date, "factor": pl.Utf8, "exposure": pl.Float64}
        )

        return ExposureData(
            exposures=exposures_df,
            factors=factors,
            date_range=(start_date, end_date),
        )

    def get_benchmark_exposures(
        self,
        benchmark: str,  # e.g., "SPY", "QQQ"
        start_date: date,
        end_date: date,
        factors: list[str] | None = None,
    ) -> ExposureData:
        """Get factor exposures for benchmark index.

        Args:
            benchmark: Benchmark ticker symbol
            start_date: Start of date range
            end_date: End of date range
            factors: Specific factors to compute (None = all)

        Returns:
            ExposureData with daily exposures for each factor
        """
        # Similar to portfolio exposures but for benchmark constituents
        # TODO: Implement benchmark constituent lookup
        ...

    def get_stock_exposures(
        self,
        portfolio_id: str,
        factor: str,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Get stock-level exposures for drill-down.

        Args:
            portfolio_id: Portfolio to analyze
            factor: Factor to display
            as_of_date: Date for exposures

        Returns:
            DataFrame with columns [symbol, weight, exposure, contribution]
        """
        holdings = self._get_portfolio_holdings(portfolio_id, as_of_date)

        if holdings is None or holdings.is_empty():
            return pl.DataFrame(schema={
                "symbol": pl.Utf8,
                "weight": pl.Float64,
                "exposure": pl.Float64,
                "contribution": pl.Float64,
            })

        # Get PERMNOs from holdings for factor computation
        permnos = holdings.select("permno").to_series().to_list()

        # Compute factor exposures using actual FactorBuilder API
        factor_result = self._builder.compute_factor(
            factor_name=factor,
            as_of_date=as_of_date,
            universe=permnos,
        )

        # Join exposures with holdings to get weights
        stock_exposures = factor_result.exposures.join(
            holdings.select(["permno", "symbol", "weight"]),
            on="permno",
        ).select([
            "symbol",
            "weight",
            pl.col("zscore").alias("exposure"),
        ])

        # Add contribution = weight * exposure
        return stock_exposures.with_columns([
            (pl.col("weight") * pl.col("exposure")).alias("contribution"),
        ]).sort("contribution", descending=True)

    def _get_portfolio_holdings(self, portfolio_id: str, as_of_date: date) -> pl.DataFrame | None:
        """Get portfolio holdings as of date.

        Returns DataFrame with columns [permno, symbol, weight]

        Data Source Options (to be finalized in C0):
        1. positions table via reconciler (current holdings)
        2. Trade journal historical snapshots
        3. Strategy target weights from signal_service

        For MVP, use positions table joined with CRSP linkage to get PERMNOs.
        """
        # Query positions table for portfolio holdings
        async def _fetch():
            async with self._db.connection() as conn:
                # Join positions with CRSP linkage table to get PERMNOs
                # The crsp_symbol_permno table maps ticker symbols to CRSP PERMNOs
                # This is essential for FactorBuilder which requires PERMNOs
                rows = await conn.fetch(
                    """
                    SELECT
                        p.symbol,
                        p.qty,
                        p.market_value,
                        cl.permno
                    FROM positions p
                    LEFT JOIN crsp_symbol_permno cl
                        ON p.symbol = cl.symbol
                        AND cl.start_date <= $2
                        AND (cl.end_date IS NULL OR cl.end_date >= $2)
                    WHERE p.strategy_id = $1
                      AND p.as_of_date <= $2
                    ORDER BY p.as_of_date DESC
                    """,
                    portfolio_id,
                    as_of_date,
                )
                if not rows:
                    return None

                # Convert to DataFrame and compute weights
                df = pl.DataFrame([dict(r) for r in rows])

                # Warn about missing PERMNO mappings
                missing = df.filter(pl.col("permno").is_null())
                if not missing.is_empty():
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Missing PERMNO mapping for symbols: {missing['symbol'].to_list()}"
                    )

                # Filter to only rows with valid PERMNOs
                df = df.filter(pl.col("permno").is_not_null())
                if df.is_empty():
                    return None

                total_value = df.select(pl.col("market_value").sum()).item()
                if total_value == 0:
                    return None

                return df.with_columns([
                    (pl.col("market_value") / total_value).alias("weight"),
                ]).select(["permno", "symbol", "weight"])

        from apps.web_console.utils.run_async import run_async
        return run_async(_fetch())
```

### 2. Heatmap Chart Component

```python
# apps/web_console/components/heatmap_chart.py
"""Interactive factor exposure heatmap component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_heatmap(
    portfolio_exposures: pl.DataFrame,
    benchmark_exposures: pl.DataFrame | None = None,
    show_diff: bool = False,
) -> None:
    """Render interactive factor exposure heatmap.

    Args:
        portfolio_exposures: DataFrame with [date, factor, exposure]
        benchmark_exposures: Optional benchmark exposures for comparison
        show_diff: If True, show portfolio - benchmark difference
    """
    if portfolio_exposures.is_empty():
        st.info("No exposure data available.")
        return

    # Pivot to matrix form: rows=factors, columns=dates
    pivot = portfolio_exposures.pivot(
        index="factor",
        on="date",
        values="exposure",
    )

    if show_diff and benchmark_exposures is not None:
        benchmark_pivot = benchmark_exposures.pivot(
            index="factor",
            on="date",
            values="exposure",
        )
        # Compute difference
        # ... (align and subtract)

    factors = pivot["factor"].to_list()
    dates = [col for col in pivot.columns if col != "factor"]
    z_values = pivot.drop("factor").to_numpy()

    fig = go.Figure(data=go.Heatmap(
        z=z_values,
        x=dates,
        y=factors,
        colorscale=[
            [0, "red"],      # Negative exposure
            [0.5, "white"],  # Neutral
            [1, "green"],    # Positive exposure
        ],
        zmid=0,  # Center color scale at zero
        colorbar=dict(title="Exposure"),
        hovertemplate="Factor: %{y}<br>Date: %{x}<br>Exposure: %{z:.3f}<extra></extra>",
    ))

    fig.update_layout(
        title="Factor Exposure Heatmap",
        xaxis_title="Date",
        yaxis_title="Factor",
        height=400,
    )

    st.plotly_chart(fig, use_container_width=True)


def render_heatmap_with_animation(
    exposures: pl.DataFrame,
    animation_speed_ms: int = 500,
) -> None:
    """Render heatmap with time animation.

    Uses Plotly animation frames for time evolution.
    """
    # Group by date for animation frames
    dates = exposures.select("date").unique().sort("date").to_series().to_list()

    if len(dates) < 2:
        render_heatmap(exposures)
        return

    # Create animation frames
    frames = []
    for d in dates:
        day_data = exposures.filter(pl.col("date") == d)
        frame = go.Frame(
            data=[go.Bar(
                x=day_data["factor"].to_list(),
                y=day_data["exposure"].to_list(),
                marker_color=[
                    "red" if e < 0 else "green"
                    for e in day_data["exposure"].to_list()
                ],
            )],
            name=str(d),
        )
        frames.append(frame)

    fig = go.Figure(
        data=frames[0].data,
        frames=frames,
        layout=go.Layout(
            title="Factor Exposures Over Time",
            xaxis_title="Factor",
            yaxis_title="Exposure",
            updatemenus=[dict(
                type="buttons",
                buttons=[
                    dict(label="Play", method="animate",
                         args=[None, {"frame": {"duration": animation_speed_ms}}]),
                    dict(label="Pause", method="animate",
                         args=[[None], {"frame": {"duration": 0}, "mode": "immediate"}]),
                ],
            )],
            sliders=[dict(
                active=0,
                steps=[dict(method="animate", args=[[str(d)]], label=str(d)) for d in dates],
            )],
        ),
    )

    st.plotly_chart(fig, use_container_width=True)
```

### 3. Stock Exposure Table Component

```python
# apps/web_console/components/stock_exposure_table.py
"""Stock-level exposure drill-down table."""

from __future__ import annotations

import polars as pl
import streamlit as st


def render_stock_exposures(
    stock_exposures: pl.DataFrame,
    factor_name: str,
) -> None:
    """Render stock-level exposure table for drill-down.

    Args:
        stock_exposures: DataFrame with [symbol, weight, exposure, contribution]
        factor_name: Name of factor for display
    """
    if stock_exposures.is_empty():
        st.info(f"No stock-level data for {factor_name}.")
        return

    st.subheader(f"Stock Exposures: {factor_name}")

    # Summary stats
    total_contribution = stock_exposures.select(pl.col("contribution").sum()).item()
    top_contributor = stock_exposures.row(0)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Exposure", f"{total_contribution:.3f}")
    col2.metric("Top Contributor", top_contributor[0])  # symbol
    col3.metric("Stocks", str(stock_exposures.height))

    # Display table
    st.dataframe(
        stock_exposures.to_pandas(),
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "weight": st.column_config.NumberColumn("Weight", format="%.2f%%"),
            "exposure": st.column_config.NumberColumn("Exposure", format="%.3f"),
            "contribution": st.column_config.NumberColumn("Contribution", format="%.4f"),
        },
        hide_index=True,
        use_container_width=True,
    )

    # Export button
    csv = stock_exposures.write_csv()
    st.download_button(
        "Export to CSV",
        csv,
        f"stock_exposures_{factor_name}.csv",
        "text/csv",
    )
```

### 4. Main Page

```python
# apps/web_console/pages/factor_heatmap.py
"""Factor Exposure Heatmap page."""

from __future__ import annotations

import os
from datetime import date, timedelta

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.heatmap_chart import render_heatmap
from apps.web_console.components.stock_exposure_table import render_stock_exposures
from apps.web_console.services.factor_exposure_service import FactorExposureService

FEATURE_FACTOR_HEATMAP = os.getenv("FEATURE_FACTOR_HEATMAP", "false").lower() in {
    "1", "true", "yes", "on",
}


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Factor Exposure Heatmap", page_icon="🎨", layout="wide")
    st.title("Factor Exposure Heatmap")

    if not FEATURE_FACTOR_HEATMAP:
        st.info("Feature not available.")
        return

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_FACTOR_ANALYTICS):
        st.error("Permission denied: VIEW_FACTOR_ANALYTICS required.")
        st.stop()

    # Initialize service
    service = FactorExposureService(None)  # TODO: Inject factor builder

    # Sidebar controls
    with st.sidebar:
        st.header("Configuration")

        # Portfolio selection
        portfolio_id = st.selectbox(
            "Portfolio",
            ["alpha_baseline", "momentum_strategy", "value_strategy"],
        )

        # Benchmark comparison
        benchmark = st.selectbox(
            "Benchmark (optional)",
            ["None", "SPY", "QQQ", "IWM"],
        )
        show_benchmark = benchmark != "None"
        show_diff = st.checkbox("Show Difference vs Benchmark", disabled=not show_benchmark)

        # Date range
        today = date.today()
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start", today - timedelta(days=90))
        with col2:
            end_date = st.date_input("End", today)

        # Factor selection
        factor_defs = service.get_factor_definitions()
        factor_names = [f.name for f in factor_defs]
        selected_factors = st.multiselect(
            "Factors",
            factor_names,
            default=factor_names[:5],  # Default to first 5
        )

    # Get exposure data
    with st.spinner("Computing exposures..."):
        portfolio_data = service.get_portfolio_exposures(
            portfolio_id, start_date, end_date, selected_factors
        )

        benchmark_data = None
        if show_benchmark:
            benchmark_data = service.get_benchmark_exposures(
                benchmark, start_date, end_date, selected_factors
            )

    # Render heatmap
    render_heatmap(
        portfolio_data.exposures,
        benchmark_data.exposures if benchmark_data else None,
        show_diff=show_diff,
    )

    st.divider()

    # Drill-down
    st.subheader("Stock-Level Drill-Down")

    col1, col2 = st.columns(2)
    with col1:
        drill_factor = st.selectbox("Select Factor", selected_factors)
    with col2:
        drill_date = st.date_input("As of Date", end_date)

    if st.button("Show Stock Exposures"):
        stock_data = service.get_stock_exposures(portfolio_id, drill_factor, drill_date)
        render_stock_exposures(stock_data, drill_factor)


if __name__ == "__main__":
    main()
```

---

## Testing Strategy

### Unit Tests

```python
# tests/apps/web_console/services/test_factor_exposure_service.py

import pytest
from datetime import date
from unittest.mock import MagicMock

from apps.web_console.services.factor_exposure_service import FactorExposureService


@pytest.fixture
def mock_factor_builder():
    builder = MagicMock()
    builder.compute_portfolio_exposure.return_value = 0.15
    return builder


def test_get_factor_definitions(mock_factor_builder):
    service = FactorExposureService(mock_factor_builder)

    defs = service.get_factor_definitions()

    assert len(defs) > 0
    assert all(d.name for d in defs)
    assert all(d.category for d in defs)


def test_get_portfolio_exposures_returns_data(mock_factor_builder):
    service = FactorExposureService(mock_factor_builder)

    data = service.get_portfolio_exposures(
        "test_portfolio",
        date(2024, 1, 1),
        date(2024, 1, 5),
        ["value", "momentum"],
    )

    assert data.factors == ["value", "momentum"]
    assert data.date_range == (date(2024, 1, 1), date(2024, 1, 5))
```

---

## Deliverables

1. **FactorExposureService:** Service for factor exposure calculations
2. **Heatmap Chart Component:** Plotly-based interactive heatmap
3. **Stock Exposure Table:** Drill-down to stock level
4. **Factor Heatmap Page:** Main Streamlit page
5. **Tests:** Unit and integration tests
6. **Documentation:** `docs/CONCEPTS/factor-exposure-visualization.md`

---

## Verification Checklist

- [ ] Heatmap renders with correct color scale (red-white-green)
- [ ] Benchmark comparison works
- [ ] Time-series slider/animation works
- [ ] Stock-level drill-down displays correctly
- [ ] Export to PNG and CSV works
- [ ] RBAC enforcement tested
- [ ] All tests pass
