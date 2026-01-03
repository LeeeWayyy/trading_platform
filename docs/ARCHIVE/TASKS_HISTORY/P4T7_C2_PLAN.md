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
from datetime import date, timedelta
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from typing import Any
    from apps.web_console.utils.db_pool import AsyncConnectionAdapter
    from libs.factors.factor_builder import FactorBuilder
    # NOTE: get_current_user() returns dict, not a typed User class
    # Use dict[str, Any] for user type until auth types are formalized


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
    """Service for computing and retrieving factor exposures.

    Uses StrategyScopedDataAccess for positions to enforce RBAC authorization.
    """

    def __init__(
        self,
        factor_builder: FactorBuilder,
        db_adapter: "AsyncConnectionAdapter",
        redis_client: "Any",  # Redis client from get_redis_client() - may be None
        user: dict,  # User dict from get_current_user()
    ):
        """Initialize with factor builder, database adapter, and user context.

        Args:
            factor_builder: FactorBuilder instance for computing exposures
            db_adapter: AsyncConnectionAdapter from apps.web_console.utils.db_pool
            redis_client: Redis client for StrategyScopedDataAccess caching (may be None)
            user: User dict from get_current_user() (for strategy authorization)
        """
        self._builder = factor_builder
        self._db = db_adapter
        self._redis = redis_client
        self._user = user

    def get_factor_definitions(self) -> list[FactorDefinition]:
        """Get all available factor definitions.

        Uses CANONICAL_FACTORS from libs/factors/factor_definitions.py
        NOTE: category is an instance property, so we must instantiate the class.
        """
        from libs.factors import CANONICAL_FACTORS

        return [
            FactorDefinition(
                name=name,
                category=factor_cls().category,  # Instantiate to access property
                description=factor_cls().description,
            )
            for name, factor_cls in CANONICAL_FACTORS.items()
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
    ) -> ExposureData | None:
        """Get factor exposures for benchmark index.

        OUT OF MVP SCOPE: Benchmark comparison requires external data source
        (CRSP index constituents or ETF holdings feed) not currently available.

        Args:
            benchmark: Benchmark ticker symbol
            start_date: Start of date range
            end_date: End of date range
            factors: Specific factors to compute (None = all)

        Returns:
            None for MVP (feature not available)

        Future: Implement with CRSP index constituents or external ETF holdings data.
        """
        # MVP: Return None - benchmark comparison not available without data source
        import logging
        logging.getLogger(__name__).info(
            f"Benchmark comparison not available for MVP: {benchmark}"
        )
        return None

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
        """Get portfolio holdings using strategy-scoped access.

        IMPORTANT: Returns CURRENT positions only. Historical position snapshots
        are not currently available in the codebase. The as_of_date parameter is
        used only for PERMNO mapping (ticker to PERMNO as of that date).

        For time-series analysis, all dates in the range will show the same
        (current) portfolio composition. Historical exposure tracking requires
        position snapshot tables (future enhancement).

        Returns DataFrame with columns [permno, symbol, weight]

        Data Source: Uses StrategyScopedDataAccess.get_positions() which enforces
        strategy-level authorization based on user permissions.

        NOTE: portfolio_id maps to strategy_id in this implementation.
        The user's authorized strategies are determined by StrategyScopedDataAccess.

        PERMNO Mapping: Use CRSPLocalProvider.ticker_to_permno() from
        libs/data_providers/crsp_local_provider.py (not a separate table)

        Handles both long and short positions using absolute market values for weighting.
        """
        from pathlib import Path
        from libs.data_providers.crsp_local_provider import CRSPLocalProvider
        from libs.data_quality.exceptions import DataNotFoundError
        from libs.data_quality.manifest import ManifestManager

        # NOTE: positions table is GLOBAL (no strategy_id column) so we can't use
        # StrategyScopedDataAccess.get_positions() which filters by strategy_id.
        # Instead, query positions directly with VIEW_ALL_POSITIONS permission gate.
        async def _fetch():
            from apps.web_console.auth.permissions import Permission, has_permission
            import logging

            # SECURITY: Positions are GLOBAL (symbol-keyed, NO strategy_id).
            # Require VIEW_ALL_POSITIONS to prevent leaking positions across users.
            # Future enhancement: Add position-strategy mapping table for proper scoping.
            if not has_permission(self._user, Permission.VIEW_ALL_POSITIONS):
                logging.getLogger(__name__).warning(
                    f"User {self._user.get('user_id')} denied access to global positions - "
                    "VIEW_ALL_POSITIONS permission required"
                )
                return None

            # Direct query to positions table (global, no strategy_id)
            # Schema: symbol, qty, avg_entry_price, current_price (no strategy_id)
            async with self._db.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT symbol, qty, avg_entry_price, current_price
                        FROM positions
                        WHERE qty != 0
                        """,
                    )
                    rows = await cur.fetchall()

            if not rows:
                return None

            # Compute market values and weights
            # Include both long and short positions using absolute market value
            data = []
            total_abs_value = 0
            for row in rows:
                # Schema uses 'qty' column (not 'quantity')
                qty = row.get("qty", 0)
                # Skip zero positions
                if qty == 0:
                    continue
                # Skip positions with NULL current_price (can't compute market value)
                # This may occur for delisted securities or pricing gaps
                if row.get("current_price") is None:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Skipping {row['symbol']}: NULL current_price"
                    )
                    continue
                # Use absolute market value for weighting (handles shorts)
                market_value = qty * row["current_price"]
                abs_value = abs(market_value)
                total_abs_value += abs_value
                data.append({
                    "symbol": row["symbol"],
                    "qty": qty,  # Use extracted qty variable
                    "market_value": market_value,  # Signed for exposure direction
                    "abs_value": abs_value,  # Absolute for weight calculation
                })

            if total_abs_value == 0:
                return None

            # Map symbols to PERMNOs using CRSPLocalProvider
            # CRSPLocalProvider requires storage_path and manifest_manager
            storage_path = Path("data/wrds/crsp/daily")
            manifest_manager = ManifestManager(Path("data/manifests"))
            crsp = CRSPLocalProvider(storage_path, manifest_manager)

            result = []
            for row_data in data:
                try:
                    permno = crsp.ticker_to_permno(row_data["symbol"], as_of_date)
                    # Use signed weight (negative for shorts, positive for longs)
                    # This preserves exposure direction while using absolute total for scaling
                    result.append({
                        "permno": permno,
                        "symbol": row_data["symbol"],
                        "weight": row_data["market_value"] / total_abs_value,
                    })
                except DataNotFoundError:
                    # Symbol not found in CRSP - skip with warning
                    # DataNotFoundError from libs.data_quality.exceptions
                    import logging
                    logging.getLogger(__name__).warning(
                        f"No PERMNO mapping for {row_data['symbol']} as of {as_of_date}"
                    )

            if not result:
                return None

            return pl.DataFrame(result)

        from apps.web_console.utils.async_helpers import run_async
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

    # MVP LIMITATION: Positions are global (not per-strategy) in current schema
    # Display warning to users about data scope
    st.warning(
        "**Note:** Position data is aggregated across all strategies. "
        "Per-portfolio position filtering will be available in a future update."
    )

    # Initialize service with required dependencies
    from pathlib import Path
    from libs.factors.factor_builder import FactorBuilder
    from libs.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data_providers.compustat_local_provider import CompustatLocalProvider
    from libs.data_quality.manifest import ManifestManager
    from apps.web_console.utils.db_pool import get_db_pool, get_redis_client

    # FactorBuilder requires data providers and manifest manager
    manifest_manager = ManifestManager(Path("data/manifests"))
    crsp_provider = CRSPLocalProvider(
        storage_path=Path("data/wrds/crsp/daily"),
        manifest_manager=manifest_manager,
    )
    compustat_provider = CompustatLocalProvider(
        storage_path=Path("data/wrds/compustat"),
        manifest_manager=manifest_manager,
    )
    factor_builder = FactorBuilder(
        crsp_provider=crsp_provider,
        compustat_provider=compustat_provider,
        manifest_manager=manifest_manager,
    )
    db_adapter = get_db_pool()
    redis_client = get_redis_client()

    # Service requires user for StrategyScopedDataAccess authorization
    # NOTE: get_redis_client() may return None if Redis not available
    service = FactorExposureService(factor_builder, db_adapter, redis_client, user)

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
from unittest.mock import AsyncMock, MagicMock

from apps.web_console.services.factor_exposure_service import FactorExposureService


@pytest.fixture
def mock_factor_builder():
    """Mock FactorBuilder with compute_all_factors returning FactorResult.

    FactorBuilder.compute_all_factors returns FactorResult with exposures DataFrame.
    The actual API does not have compute_portfolio_exposure method.
    """
    import polars as pl

    builder = MagicMock()
    # Mock compute_all_factors to return FactorResult-like structure
    mock_result = MagicMock()
    mock_result.exposures = pl.DataFrame({
        "permno": [10001, 10002],
        "date": ["2024-01-15", "2024-01-15"],
        "factor_name": ["momentum_12_1", "momentum_12_1"],
        "raw_value": [0.15, 0.20],
        "zscore": [1.5, 2.0],
        "percentile": [0.75, 0.85],
    })
    mock_result.as_of_date = "2024-01-15"
    builder.compute_all_factors.return_value = mock_result
    builder.list_factors.return_value = ["momentum_12_1", "book_to_market", "roe"]
    return builder


@pytest.fixture
def mock_db_adapter():
    """Mock AsyncConnectionAdapter for database access.

    Must properly mock the async context manager pattern:
    async with adapter.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(...)
            rows = await cur.fetchall()
    """
    from unittest.mock import AsyncMock
    from contextlib import asynccontextmanager

    # Create mock cursor with async methods
    mock_cursor = MagicMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"symbol": "AAPL", "qty": 100, "current_price": 150.0},
        {"symbol": "GOOGL", "qty": 50, "current_price": 140.0},
    ])
    mock_cursor.fetchone = AsyncMock(return_value=None)

    # Create mock connection with cursor context manager
    mock_conn = MagicMock()

    @asynccontextmanager
    async def cursor_cm():
        yield mock_cursor

    mock_conn.cursor = cursor_cm

    # Create mock adapter with connection context manager
    adapter = MagicMock()

    @asynccontextmanager
    async def conn_cm():
        yield mock_conn

    adapter.connection = conn_cm
    return adapter


@pytest.fixture
def mock_redis_adapter():
    """Create a mock AsyncRedisAdapter for tests."""
    from unittest.mock import MagicMock, AsyncMock
    from contextlib import asynccontextmanager

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()

    adapter = MagicMock()

    @asynccontextmanager
    async def client_cm():
        yield mock_redis

    adapter.client = client_cm
    return adapter


@pytest.fixture
def mock_user():
    """Create a mock User for tests."""
    from unittest.mock import MagicMock
    user = MagicMock()
    user.user_id = "test_user"
    user.strategies = ["alpha_baseline"]
    return user


def test_get_factor_definitions(mock_factor_builder, mock_db_adapter, mock_redis_adapter, mock_user):
    """Test that factor definitions are returned from CANONICAL_FACTORS."""
    service = FactorExposureService(mock_factor_builder, mock_db_adapter, mock_redis_adapter, mock_user)

    defs = service.get_factor_definitions()

    assert len(defs) > 0
    assert all(d.name for d in defs)
    assert all(d.category for d in defs)


def test_get_portfolio_exposures_returns_data(mock_factor_builder, mock_db_adapter, mock_redis_adapter, mock_user):
    """Test portfolio exposures returns properly structured data."""
    service = FactorExposureService(mock_factor_builder, mock_db_adapter, mock_redis_adapter, mock_user)

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
