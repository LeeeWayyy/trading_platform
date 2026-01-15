# P4T7 C1: Alpha Signal Explorer - Component Plan

**Component:** C1 - T9.1 Alpha Signal Explorer
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING
**Estimated Effort:** 3-4 days
**Dependencies:** C0 (Prep & Validation)

---

## Overview

Implement T9.1 Alpha Signal Explorer page that enables researchers to browse, analyze, and compare alpha signals from the model registry.

## Acceptance Criteria (from P4T7_TASK.md)

- [ ] Browse registered alpha signals from model registry with filtering (by status, type, IC range)
- [ ] IC (Information Coefficient) time-series visualization with rolling windows
- [ ] Rank IC and Pearson IC side-by-side comparison charts
- [ ] Decay curve analysis showing IC at multiple horizons (1, 2, 5, 10, 20, 60 days)
- [ ] Signal correlation matrix (heatmap) for selected signals
- [ ] Backtest quick-launch button for selected signal
- [ ] Export signal metadata and metrics to CSV
- [ ] RBAC: VIEW_ALPHA_SIGNALS permission required
- [ ] Pagination for large signal lists (default 25, max 100 per page)

---

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────────┐
│                   Streamlit Page: alpha_explorer.py             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Signal List    │  │  IC Charts      │  │ Decay Curve     │ │
│  │  (filterable)   │  │  (Plotly)       │  │ (Plotly)        │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
│           │                    │                     │          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Correlation    │  │  Signal Detail  │  │  Quick Actions  │ │
│  │  Matrix         │  │  Panel          │  │  (backtest)     │ │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
└───────────┼─────────────────────┼─────────────────────┼─────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              AlphaExplorerService (services/)                    │
│  - list_signals(filters) → list[SignalSummary]                  │
│  - get_signal_metrics(signal_id) → SignalMetrics                │
│  - get_ic_timeseries(signal_id) → pl.DataFrame                  │
│  - get_decay_curve(signal_id) → pl.DataFrame                    │
│  - compute_correlation(signal_ids) → pl.DataFrame               │
└───────────────────────────────────────────────────────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ ModelRegistry   │   │ AlphaMetrics    │   │ BacktestResult  │
│ (libs/models/)  │   │ (libs/alpha/)   │   │ Storage         │
└─────────────────┘   └─────────────────┘   └─────────────────┘
```

### File Structure

```
apps/web_console/
├── pages/
│   └── alpha_explorer.py        # Main Streamlit page
├── components/
│   ├── ic_chart.py              # IC time-series chart (Plotly)
│   ├── decay_curve.py           # Decay curve visualization
│   └── signal_correlation_matrix.py  # Correlation heatmap
├── services/
│   └── alpha_explorer_service.py    # Registry + metrics service

tests/apps/web_console/
├── pages/
│   └── test_alpha_explorer.py   # Page integration tests
├── services/
│   └── test_alpha_explorer_service.py  # Service unit tests

docs/CONCEPTS/
└── alpha-signal-explorer.md     # User documentation
```

---

## Implementation Details

### 1. AlphaExplorerService

```python
# apps/web_console/services/alpha_explorer_service.py
"""Service layer for alpha signal exploration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from libs.models.registry import ModelRegistry
from libs.models.types import ModelMetadata, ModelStatus, ModelType

if TYPE_CHECKING:
    from libs.trading.alpha.metrics import AlphaMetricsAdapter


@dataclass
class SignalSummary:
    """Summary of alpha signal for list view.

    NOTE: ModelMetadata has NO 'name' or 'status' fields.
    - Display name is derived from model_id or parameters['name']
    - Status filtering available via ModelRegistry.list_models(status=...) if needed
    """
    signal_id: str  # model_id from ModelMetadata
    display_name: str  # Derived from model_id or parameters.get('name', model_id)
    version: str
    # NOTE: ModelMetadata has no status field - track separately via registry
    # For MVP, use 'active' for all loaded models; future: add status table
    mean_ic: float | None  # From metrics dict
    icir: float | None  # From metrics dict
    created_at: date
    # Backtest linkage via backtest_job_id in parameters dict
    # The job_id is stored at model registration time after backtest completes
    # This links to BacktestResultStorage.get_result(job_id)
    backtest_job_id: str | None  # Links to BacktestResultStorage


@dataclass
class SignalMetrics:
    """Detailed metrics for selected signal."""
    signal_id: str
    name: str
    version: str
    mean_ic: float
    icir: float
    hit_rate: float
    coverage: float
    average_turnover: float
    decay_half_life: float | None
    n_days: int
    start_date: date
    end_date: date


class AlphaExplorerService:
    """Service for browsing and analyzing alpha signals."""

    def __init__(
        self,
        registry: ModelRegistry,
        metrics_adapter: AlphaMetricsAdapter | None = None,
    ):
        self._registry = registry
        self._metrics = metrics_adapter

    def list_signals(
        self,
        status: ModelStatus | None = None,
        min_ic: float | None = None,
        max_ic: float | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[SignalSummary], int]:
        """List signals with filtering and pagination.

        Args:
            status: Filter by ModelStatus (staged, production, archived, failed)
            min_ic: Minimum IC threshold
            max_ic: Maximum IC threshold
            limit: Page size
            offset: Page offset

        Returns:
            Tuple of (signals, total_count)
        """
        models = self._registry.list_models(
            model_type=ModelType.alpha_weights,
            status=status,  # Pass status filter to registry
        )

        # Filter by IC range
        if min_ic is not None or max_ic is not None:
            models = [
                m for m in models
                if self._in_ic_range(m, min_ic, max_ic)
            ]

        total = len(models)
        page = models[offset:offset + limit]

        summaries = [self._to_summary(m) for m in page]
        return summaries, total

    def get_signal_metrics(self, signal_id: str) -> SignalMetrics:
        """Get detailed metrics for a signal.

        signal_id is the model_id from ModelMetadata (NOT model_type:version).
        Uses get_model_by_id to look up by model_id directly.
        """
        # signal_id IS model_id - look up directly
        metadata = self._registry.get_model_by_id(signal_id)
        if not metadata:
            raise ValueError(f"Signal not found: {signal_id}")

        # Load backtest result if available (via job_id in parameters)
        backtest_result = self._load_backtest_result(metadata)

        # ModelMetadata has no 'name' field - use parameters['name'] or model_id
        display_name = metadata.parameters.get("name", signal_id)

        return SignalMetrics(
            signal_id=signal_id,
            name=display_name,
            version=metadata.version,
            mean_ic=backtest_result.mean_ic if backtest_result else 0.0,
            icir=backtest_result.icir if backtest_result else 0.0,
            hit_rate=backtest_result.hit_rate if backtest_result else 0.0,
            coverage=backtest_result.coverage if backtest_result else 0.0,
            average_turnover=backtest_result.average_turnover if backtest_result else 0.0,
            decay_half_life=backtest_result.decay_half_life if backtest_result else None,
            n_days=backtest_result.n_days if backtest_result else 0,
            start_date=backtest_result.start_date if backtest_result else date.today(),
            end_date=backtest_result.end_date if backtest_result else date.today(),
        )

    def get_ic_timeseries(self, signal_id: str) -> pl.DataFrame:
        """Get daily IC time series for visualization.

        signal_id is the model_id - look up directly via get_model_by_id.

        Returns DataFrame with columns: [date, ic, rank_ic, rolling_ic_20d]
        """
        metadata = self._registry.get_model_by_id(signal_id)
        if not metadata:
            return pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64})

        backtest_result = self._load_backtest_result(metadata)

        if backtest_result is None:
            return pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64})

        # Add rolling IC
        daily_ic = backtest_result.daily_ic.with_columns([
            pl.col("rank_ic").rolling_mean(window_size=20).alias("rolling_ic_20d"),
        ])

        return daily_ic

    def get_decay_curve(self, signal_id: str) -> pl.DataFrame:
        """Get decay curve data for visualization.

        signal_id is the model_id - look up directly via get_model_by_id.

        Returns DataFrame with columns: [horizon, ic, rank_ic]
        """
        metadata = self._registry.get_model_by_id(signal_id)
        if not metadata:
            return pl.DataFrame(schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64})

        backtest_result = self._load_backtest_result(metadata)

        if backtest_result is None:
            return pl.DataFrame(schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64})

        return backtest_result.decay_curve

    def compute_correlation(self, signal_ids: list[str]) -> pl.DataFrame:
        """Compute correlation matrix for selected signals.

        signal_ids are model_ids - look up directly via get_model_by_id.

        Returns DataFrame with signal names as index/columns.
        """
        # Load daily signals for each
        signals_data = {}
        for sid in signal_ids:
            metadata = self._registry.get_model_by_id(sid)
            if not metadata:
                continue

            backtest_result = self._load_backtest_result(metadata)

            if backtest_result is not None:
                # Aggregate to daily cross-sectional mean signal
                daily_mean = (
                    backtest_result.daily_signals
                    .group_by("date")
                    .agg(pl.col("signal").mean())
                )
                # ModelMetadata has no 'name' - use parameters['name'] or model_id
                display_name = metadata.parameters.get("name", sid)
                signals_data[display_name] = daily_mean

        # Compute pairwise correlations
        if len(signals_data) < 2:
            return pl.DataFrame()

        # Join all signals on date and compute correlation matrix
        # ... (implementation details)

        return pl.DataFrame()  # placeholder

    def _to_summary(self, metadata: ModelMetadata) -> SignalSummary:
        """Convert ModelMetadata to SignalSummary.

        NOTE: ModelMetadata has no 'name' or 'status' fields.
        - Display name derived from parameters['name'] or model_id
        - Status tracking is separate (MVP: assume all loaded = active)
        - backtest_job_id comes from parameters['backtest_job_id'] set at registration
        """
        # Derive display name from parameters or model_id
        display_name = metadata.parameters.get("name", metadata.model_id)

        # Get backtest_job_id from parameters (set during model registration)
        backtest_job_id = (
            metadata.parameters.get("backtest_job_id")
            if metadata.parameters
            else None
        )

        return SignalSummary(
            signal_id=metadata.model_id,  # Use model_id directly
            display_name=display_name,
            version=metadata.version,
            mean_ic=metadata.metrics.get("mean_ic") if metadata.metrics else None,
            icir=metadata.metrics.get("icir") if metadata.metrics else None,
            created_at=metadata.created_at.date(),
            backtest_job_id=backtest_job_id,
        )

    def _in_ic_range(
        self, metadata: ModelMetadata, min_ic: float | None, max_ic: float | None
    ) -> bool:
        """Check if model's IC is within range."""
        ic = metadata.metrics.get("mean_ic") if metadata.metrics else None
        if ic is None:
            return False
        if min_ic is not None and ic < min_ic:
            return False
        if max_ic is not None and ic > max_ic:
            return False
        return True

    def _load_backtest_result(self, metadata: ModelMetadata | None):
        """Load backtest result from BacktestResultStorage.

        IMPORTANT: BacktestResultStorage is keyed by job_id (str), not run_id.
        The mapping approach:
        - ModelMetadata.parameters['backtest_job_id'] stores the job_id at registration time

        For MVP, we assume backtest_job_id is stored in ModelMetadata.parameters
        during the model registration process (after backtest completes).

        Example registration flow:
          1. Run backtest via backtest_jobs table -> job_id (str) created
          2. Register model -> registry.register(..., parameters={'backtest_job_id': job_id})
          3. Load here -> storage.get_result(job_id)

        Note: BacktestResultStorage.get_result() takes a str job_id, not UUID.

        CRITICAL: BacktestResultStorage uses SYNC database pool (not async).
        Must use get_sync_db_pool() from apps.web_console.utils.sync_db_pool,
        NOT the async pool from db_pool.py.
        """
        if metadata is None:
            return None

        # Get job_id from parameters (stored during model registration)
        job_id = metadata.parameters.get("backtest_job_id") if metadata.parameters else None
        if not job_id:
            return None

        # BacktestResultStorage uses SYNC psycopg pool - same as backtest.py page
        from apps.web_console.utils.sync_db_pool import get_sync_db_pool
        from libs.trading.backtest.result_storage import BacktestResultStorage

        try:
            pool = get_sync_db_pool()  # Sync pool, not async
            storage = BacktestResultStorage(pool)
            return storage.get_result(job_id)  # job_id is str, not UUID
        except Exception:
            # Log and return None on storage errors
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to load backtest result for job_id={job_id}"
            )
            return None
```

### 2. IC Chart Component

```python
# apps/web_console/components/ic_chart.py
"""IC time-series visualization component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_ic_chart(daily_ic: pl.DataFrame) -> None:
    """Render IC time-series chart with Pearson and Rank IC.

    Args:
        daily_ic: DataFrame with columns [date, ic, rank_ic, rolling_ic_20d]
    """
    if daily_ic.is_empty():
        st.info("No IC data available for this signal.")
        return

    fig = go.Figure()

    # Rank IC (primary)
    fig.add_trace(go.Scatter(
        x=daily_ic["date"].to_list(),
        y=daily_ic["rank_ic"].to_list(),
        name="Rank IC",
        mode="lines",
        line=dict(color="blue", width=1),
        opacity=0.5,
    ))

    # Rolling 20-day Rank IC
    if "rolling_ic_20d" in daily_ic.columns:
        fig.add_trace(go.Scatter(
            x=daily_ic["date"].to_list(),
            y=daily_ic["rolling_ic_20d"].to_list(),
            name="Rolling 20d Rank IC",
            mode="lines",
            line=dict(color="blue", width=2),
        ))

    # Pearson IC (secondary)
    fig.add_trace(go.Scatter(
        x=daily_ic["date"].to_list(),
        y=daily_ic["ic"].to_list(),
        name="Pearson IC",
        mode="lines",
        line=dict(color="gray", width=1, dash="dot"),
        opacity=0.5,
    ))

    # Zero line
    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    fig.update_layout(
        title="Information Coefficient Over Time",
        xaxis_title="Date",
        yaxis_title="IC",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=400,
    )

    st.plotly_chart(fig, use_container_width=True)
```

### 3. Decay Curve Component

```python
# apps/web_console/components/decay_curve.py
"""Decay curve visualization component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_decay_curve(decay_curve: pl.DataFrame, half_life: float | None = None) -> None:
    """Render decay curve showing IC at multiple horizons.

    Args:
        decay_curve: DataFrame with columns [horizon, ic, rank_ic]
        half_life: Optional half-life in days for annotation
    """
    if decay_curve.is_empty():
        st.info("No decay curve data available for this signal.")
        return

    fig = go.Figure()

    # Rank IC decay
    fig.add_trace(go.Scatter(
        x=decay_curve["horizon"].to_list(),
        y=decay_curve["rank_ic"].to_list(),
        name="Rank IC",
        mode="lines+markers",
        line=dict(color="blue", width=2),
        marker=dict(size=8),
    ))

    # Pearson IC decay
    fig.add_trace(go.Scatter(
        x=decay_curve["horizon"].to_list(),
        y=decay_curve["ic"].to_list(),
        name="Pearson IC",
        mode="lines+markers",
        line=dict(color="gray", width=1, dash="dot"),
        marker=dict(size=6),
    ))

    # Zero line
    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    # Half-life annotation
    if half_life is not None:
        fig.add_vline(x=half_life, line_dash="dot", line_color="red")
        fig.add_annotation(
            x=half_life, y=0.5,
            text=f"Half-life: {half_life:.1f}d",
            showarrow=True,
            arrowhead=2,
        )

    fig.update_layout(
        title="Signal Decay Curve",
        xaxis_title="Horizon (days)",
        yaxis_title="IC",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=350,
    )

    st.plotly_chart(fig, use_container_width=True)
```

### 4. Main Page

```python
# apps/web_console/pages/alpha_explorer.py
"""Alpha Signal Explorer page."""

from __future__ import annotations

import os

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.decay_curve import render_decay_curve
from apps.web_console.components.ic_chart import render_ic_chart
from apps.web_console.services.alpha_explorer_service import AlphaExplorerService
from libs.models.registry import ModelRegistry
from libs.models.types import ModelStatus

FEATURE_ALPHA_EXPLORER = os.getenv("FEATURE_ALPHA_EXPLORER", "false").lower() in {
    "1", "true", "yes", "on",
}
DEFAULT_PAGE_SIZE = 25


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Alpha Signal Explorer", page_icon="📈", layout="wide")
    st.title("Alpha Signal Explorer")

    if not FEATURE_ALPHA_EXPLORER:
        st.info("Feature not available.")
        return

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_ALPHA_SIGNALS):
        st.error("Permission denied: VIEW_ALPHA_SIGNALS required.")
        st.stop()

    # Initialize service - ModelRegistry requires registry_dir
    from pathlib import Path
    from libs.trading.alpha.metrics import AlphaMetricsAdapter

    registry_dir = Path(os.getenv("MODEL_REGISTRY_DIR", "data/models"))
    registry = ModelRegistry(registry_dir=registry_dir)
    metrics_adapter = AlphaMetricsAdapter()  # Optional for display-only features
    service = AlphaExplorerService(registry, metrics_adapter)

    # Sidebar filters
    with st.sidebar:
        st.header("Filters")

        # NOTE: ModelMetadata has no status field
        # Status filter (ModelStatus: staged, production, archived, failed)
        status_options = ["All", "staged", "production", "archived", "failed"]
        status_selection = st.selectbox("Status", status_options, index=0)
        status_filter = ModelStatus(status_selection) if status_selection != "All" else None

        # IC range filter
        min_ic = st.number_input("Min IC", value=0.0, step=0.01)
        max_ic = st.number_input("Max IC", value=1.0, step=0.01)

    # Signal list with status and IC filtering
    signals, total = service.list_signals(
        status=status_filter,
        min_ic=min_ic if min_ic > 0 else None,
        max_ic=max_ic if max_ic < 1 else None,
        limit=DEFAULT_PAGE_SIZE,
    )

    st.caption(f"Showing {len(signals)} of {total} signals")

    if not signals:
        st.info("No signals found matching filters.")
        return

    # Signal table
    signal_names = [s.display_name for s in signals]
    selected_idx = st.selectbox("Select Signal", range(len(signal_names)), format_func=lambda i: signal_names[i])
    selected = signals[selected_idx]

    # Signal details
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Signal Metrics")
        metrics = service.get_signal_metrics(selected.signal_id)

        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("Mean IC", f"{metrics.mean_ic:.3f}")
        mcol2.metric("ICIR", f"{metrics.icir:.2f}")
        mcol3.metric("Hit Rate", f"{metrics.hit_rate:.1%}")

        mcol1.metric("Coverage", f"{metrics.coverage:.1%}")
        mcol2.metric("Turnover", f"{metrics.average_turnover:.1%}")
        if metrics.decay_half_life:
            mcol3.metric("Half-life", f"{metrics.decay_half_life:.1f}d")

    with col2:
        st.subheader("Quick Actions")
        if st.button("Launch Backtest", type="primary"):
            # Redirect to backtest page with pre-filled config
            st.session_state["backtest_signal"] = selected.signal_id
            st.switch_page("pages/backtest.py")

        if st.button("Export Metrics"):
            # Export to CSV
            pass

    st.divider()

    # Charts
    tab1, tab2, tab3 = st.tabs(["IC Time Series", "Decay Curve", "Correlation"])

    with tab1:
        ic_data = service.get_ic_timeseries(selected.signal_id)
        render_ic_chart(ic_data)

    with tab2:
        decay_data = service.get_decay_curve(selected.signal_id)
        render_decay_curve(decay_data, metrics.decay_half_life)

    with tab3:
        st.info("Select multiple signals to view correlation matrix")


if __name__ == "__main__":
    main()
```

---

## Testing Strategy

### Unit Tests

```python
# tests/apps/web_console/services/test_alpha_explorer_service.py

import pytest
from datetime import UTC, datetime
from unittest.mock import MagicMock

from apps.web_console.services.alpha_explorer_service import (
    AlphaExplorerService,
    SignalSummary,
)
from libs.models.types import EnvironmentMetadata, ModelMetadata, ModelStatus, ModelType


@pytest.fixture
def mock_registry():
    """Mock registry returning ModelMetadata with correct fields.

    NOTE: ModelMetadata has no 'name' or 'status' fields.
    Display name is derived from parameters['name'] or model_id.

    EnvironmentMetadata requires: python_version, dependencies_hash, platform,
    created_by, numpy_version, polars_version (sklearn_version, cvxpy_version optional).
    """
    registry = MagicMock()
    registry.list_models.return_value = [
        ModelMetadata(
            model_id="momentum_alpha_v1",
            model_type=ModelType.alpha_weights,
            version="v1.0.0",
            created_at=datetime.now(UTC),
            dataset_version_ids={"crsp": "v1.0.0"},
            snapshot_id="snap-001",
            checksum_sha256="abc123",
            env=EnvironmentMetadata(
                python_version="3.11.5",
                dependencies_hash="sha256abc123",
                platform="linux-x86_64",
                created_by="test_user",
                numpy_version="1.26.0",
                polars_version="0.20.0",
            ),
            config={},
            config_hash="cfg123",
            parameters={
                "name": "momentum_alpha",  # Display name
                "backtest_job_id": "job-123",  # Links to BacktestResultStorage
            },
            metrics={"mean_ic": 0.05, "icir": 1.2},
        ),
    ]
    return registry


def test_list_signals_returns_summaries(mock_registry):
    service = AlphaExplorerService(mock_registry, None)

    signals, total = service.list_signals()

    assert total == 1
    assert len(signals) == 1
    assert signals[0].display_name == "momentum_alpha"  # From parameters['name']
    assert signals[0].mean_ic == 0.05


def test_list_signals_filters_by_ic_range(mock_registry):
    service = AlphaExplorerService(mock_registry, None)

    # IC 0.05 is above min_ic=0.06
    signals, total = service.list_signals(min_ic=0.06)

    assert total == 0
```

### Integration Tests

```python
# tests/apps/web_console/test_alpha_explorer.py

import pytest

from apps.web_console.pages.alpha_explorer import main


@pytest.fixture
def authenticated_user(monkeypatch):
    """Set up authenticated researcher user."""
    monkeypatch.setenv("FEATURE_ALPHA_EXPLORER", "true")
    # Mock auth
    ...


def test_alpha_explorer_loads(authenticated_user, streamlit_test_client):
    """Test page loads without error."""
    response = streamlit_test_client.get("/alpha_explorer")
    assert response.status_code == 200
```

---

## Deliverables

1. **AlphaExplorerService:** Service for registry queries and metrics
2. **IC Chart Component:** Plotly-based IC time-series visualization
3. **Decay Curve Component:** Plotly-based decay curve visualization
4. **Signal Correlation Matrix:** Heatmap for signal correlation
5. **Alpha Explorer Page:** Main Streamlit page
6. **Tests:** Unit and integration tests
7. **Documentation:** `docs/CONCEPTS/alpha-signal-explorer.md`

---

## Verification Checklist

- [ ] Signal list displays with filtering
- [ ] IC time-series chart renders correctly
- [ ] Decay curve chart renders correctly
- [ ] Backtest quick-launch works
- [ ] Export functionality works
- [ ] RBAC enforcement tested
- [ ] Pagination works for large lists
- [ ] All tests pass
