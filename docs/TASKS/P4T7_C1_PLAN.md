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
    from libs.alpha.metrics import AlphaMetricsAdapter


@dataclass
class SignalSummary:
    """Summary of alpha signal for list view."""
    signal_id: str  # model_id from ModelMetadata
    name: str  # Derived from model_id or parameters
    version: str
    status: ModelStatus  # staged, production, archived, failed
    mean_ic: float | None  # From metrics dict
    icir: float | None  # From metrics dict
    created_at: date
    # Backtest linkage via run_id or experiment_id (NOT backtest_id)
    run_id: str | None  # Links to BacktestResultStorage
    experiment_id: str | None


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
        metrics_adapter: AlphaMetricsAdapter,
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

        Returns:
            Tuple of (signals, total_count)
        """
        models = self._registry.list_models(
            model_type=ModelType.alpha_weights,
            status=status,
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
        """Get detailed metrics for a signal."""
        # Parse signal_id to get model_type and version
        model_type, version = self._parse_signal_id(signal_id)
        metadata = self._registry.get_model_metadata(model_type, version)

        # Load backtest result if available
        backtest_result = self._load_backtest_result(metadata.run_id)

        return SignalMetrics(
            signal_id=signal_id,
            name=metadata.name,
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

        Returns DataFrame with columns: [date, ic, rank_ic, rolling_ic_20d]
        """
        model_type, version = self._parse_signal_id(signal_id)
        metadata = self._registry.get_model_metadata(model_type, version)
        backtest_result = self._load_backtest_result(metadata.run_id)

        if backtest_result is None:
            return pl.DataFrame(schema={"date": pl.Date, "ic": pl.Float64, "rank_ic": pl.Float64})

        # Add rolling IC
        daily_ic = backtest_result.daily_ic.with_columns([
            pl.col("rank_ic").rolling_mean(window_size=20).alias("rolling_ic_20d"),
        ])

        return daily_ic

    def get_decay_curve(self, signal_id: str) -> pl.DataFrame:
        """Get decay curve data for visualization.

        Returns DataFrame with columns: [horizon, ic, rank_ic]
        """
        model_type, version = self._parse_signal_id(signal_id)
        metadata = self._registry.get_model_metadata(model_type, version)
        backtest_result = self._load_backtest_result(metadata.run_id)

        if backtest_result is None:
            return pl.DataFrame(schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64})

        return backtest_result.decay_curve

    def compute_correlation(self, signal_ids: list[str]) -> pl.DataFrame:
        """Compute correlation matrix for selected signals.

        Returns DataFrame with signal names as index/columns.
        """
        # Load daily signals for each
        signals_data = {}
        for sid in signal_ids:
            model_type, version = self._parse_signal_id(sid)
            metadata = self._registry.get_model_metadata(model_type, version)
            backtest_result = self._load_backtest_result(metadata.run_id)

            if backtest_result is not None:
                # Aggregate to daily cross-sectional mean signal
                daily_mean = (
                    backtest_result.daily_signals
                    .group_by("date")
                    .agg(pl.col("signal").mean())
                )
                signals_data[metadata.name] = daily_mean

        # Compute pairwise correlations
        if len(signals_data) < 2:
            return pl.DataFrame()

        # Join all signals on date and compute correlation matrix
        # ... (implementation details)

        return pl.DataFrame()  # placeholder

    def _parse_signal_id(self, signal_id: str) -> tuple[str, str]:
        """Parse signal_id into model_type and version."""
        parts = signal_id.split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid signal_id format: {signal_id}")
        return parts[0], parts[1]

    def _to_summary(self, metadata: ModelMetadata) -> SignalSummary:
        """Convert ModelMetadata to SignalSummary."""
        return SignalSummary(
            signal_id=f"{metadata.model_type}:{metadata.version}",
            name=metadata.name,
            version=metadata.version,
            status=metadata.status,
            mean_ic=metadata.metrics.get("mean_ic") if metadata.metrics else None,
            icir=metadata.metrics.get("icir") if metadata.metrics else None,
            created_at=metadata.created_at.date(),
            run_id=metadata.run_id,
            experiment_id=metadata.experiment_id,
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

    def _load_backtest_result(self, run_id: str | None):
        """Load backtest result from BacktestResultStorage via run_id.

        The run_id from ModelMetadata links to backtest results.
        See libs/backtest/result_storage.py for the storage API.
        """
        if run_id is None:
            return None
        # TODO: Load from backtest result storage
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

    # Initialize service
    registry = ModelRegistry()  # TODO: Get from dependency injection
    service = AlphaExplorerService(registry, None)

    # Sidebar filters
    with st.sidebar:
        st.header("Filters")

        status_filter = st.selectbox(
            "Status",
            ["All", "staged", "production", "archived", "failed"],  # Per ModelStatus enum
        )
        status = None if status_filter == "All" else ModelStatus(status_filter)

        min_ic = st.number_input("Min IC", value=0.0, step=0.01)
        max_ic = st.number_input("Max IC", value=1.0, step=0.01)

    # Signal list
    signals, total = service.list_signals(
        status=status,
        min_ic=min_ic if min_ic > 0 else None,
        max_ic=max_ic if max_ic < 1 else None,
        limit=DEFAULT_PAGE_SIZE,
    )

    st.caption(f"Showing {len(signals)} of {total} signals")

    if not signals:
        st.info("No signals found matching filters.")
        return

    # Signal table
    signal_names = [s.name for s in signals]
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
from unittest.mock import MagicMock

from apps.web_console.services.alpha_explorer_service import (
    AlphaExplorerService,
    SignalSummary,
)
from libs.models.types import ModelMetadata, ModelStatus, ModelType


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.list_models.return_value = [
        ModelMetadata(
            model_type=ModelType.alpha_weights,
            version="v1.0",
            name="momentum_alpha",
            status=ModelStatus.production,  # Per ModelStatus enum: staged, production, archived, failed
            metrics={"mean_ic": 0.05, "icir": 1.2},
            created_at=datetime.now(UTC),
            run_id="run-123",  # Links to BacktestResultStorage
        ),
    ]
    return registry


def test_list_signals_returns_summaries(mock_registry):
    service = AlphaExplorerService(mock_registry, None)

    signals, total = service.list_signals()

    assert total == 1
    assert len(signals) == 1
    assert signals[0].name == "momentum_alpha"
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
