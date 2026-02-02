---
id: P6T11
title: "Professional Trading Terminal - Walk-Forward & Parameters"
phase: P6
task: T11
priority: P1
owner: "@development-team"
state: PLANNING
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T11.1-T11.4]
---

# P6T11: Walk-Forward & Parameters

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** PLANNING
**Priority:** P1 (Research Platform)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 11 of 18
**Dependency:** P5 complete

---

## Objective

Build walk-forward visualization and parameter analysis tools for strategy robustness validation.

**Success looks like:**
- Walk-forward timeline visualization (Gantt chart)
- Parameter stability heatmap with overfitting detection
- Decay curve visualization with half-life calculation
- Alpha cluster map for redundancy detection

---

## Implementation Plan

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Web Console (NiceGUI)                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │ walk_forward_   │  │ parameter_      │  │ alpha_cluster_  │             │
│  │ timeline.py     │  │ heatmap.py      │  │ map.py          │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
│           │                    │                    │                       │
│  ┌────────┴────────────────────┴────────┐  ┌───────┴───────────────────────┐
│  │       backtest.py (WF + Params tabs) │  │   alpha_explorer.py (Cluster) │
│  └────────────────────────────────┬─────┘  └───────┬───────────────────────┘
└───────────────────────────────────┼────────────────┼───────────────────────┘
                                    │                │
┌───────────────────────────────────┼────────────────┼───────────────────────┐
│                           Service Layer            │                       │
│  ┌────────────────────────────────┴──────────────┐ │                       │
│  │   backtest_analytics_service.py (EXTEND)      │ │                       │
│  │  - verify_job_ownership()        [existing]   │ │                       │
│  │  - get_backtest_result()         [existing]   │ │                       │
│  │  - run_quantile_analysis()       [existing]   │ │                       │
│  │  - get_walk_forward_result()     [NEW T11.1]  │ │                       │
│  │  - get_parameter_grid_results()  [NEW T11.2]  │ │                       │
│  └───────────────────────────────────────────────┘ │                       │
│                                                    │                       │
│  ┌─────────────────────────────────────────────────┴────────────────────┐  │
│  │           alpha_explorer_service.py (EXTEND)                         │  │
│  │  - get_decay_curve()              [existing]                         │  │
│  │  - compute_correlation()          [existing]                         │  │
│  │  - compute_alpha_clusters()       [NEW T11.4]                        │  │
│  │  - get_signal_metadata()          [NEW T11.4]                        │  │
│  └────────────────────────────────────┬─────────────────────────────────┘  │
└───────────────────────────────────────┼─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼─────────────────────────────────────┐
│                           Analytics Core                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐                        │
│  │ walk_forward.py      │  │ alpha_clustering.py  │                        │
│  │ (existing)           │  │ (NEW module)         │                        │
│  │ - WalkForwardResult  │  │ - ClusterResult      │                        │
│  │ - WindowResult       │  │ - hierarchical()     │                        │
│  └──────────────────────┘  └──────────────────────┘                        │
│                                                                             │
│  ┌──────────────────────┐  ┌──────────────────────┐                        │
│  │ param_search.py      │  │ result_storage.py    │                        │
│  │ (EXTEND SearchResult)│  │ (EXTEND - loaders)   │                        │
│  │ - param_names        │  │ - load_walk_forward  │                        │
│  │ - param_ranges       │  │ - load_param_search  │                        │
│  └──────────────────────┘  └──────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
1. Walk-Forward Visualization:
   BacktestResult.walk_forward_result → BacktestAnalyticsService → walk_forward_timeline.py

2. Parameter Heatmap:
   param_search.SearchResult → BacktestAnalyticsService → parameter_heatmap.py

3. Decay Curve (existing component):
   BacktestResult.decay_curve → AlphaExplorerService → decay_curve.py (enhance)

4. Alpha Cluster Map:
   Multiple BacktestResults → AlphaExplorerService.compute_correlation()
     → alpha_clustering.py → AlphaExplorerService → alpha_cluster_map.py
```

### Data Persistence / Artifact Storage

**New Artifact Files** (stored in `data/backtest_results/{job_id}/` - same as existing artifacts):

| Artifact | Format | Description |
|----------|--------|-------------|
| `walk_forward.json` | JSON | Walk-forward results (windows, metrics, overfitting ratio) |
| `param_search.json` | JSON | Parameter grid search results (all combinations, best params) |

**Walk-Forward Artifact Schema (`walk_forward.json`):**
```json
{
  "version": "1.0",
  "config": {
    "train_months": 12,
    "test_months": 3,
    "step_months": 3,
    "min_train_samples": 252,
    "overfitting_threshold": 2.0
  },
  "windows": [
    {
      "window_id": 1,
      "train_start": "2020-01-01",
      "train_end": "2020-12-31",
      "test_start": "2021-01-01",
      "test_end": "2021-03-31",
      "best_params": {"window": 20, "zscore": 2.0},
      "train_ic": 0.045,
      "test_ic": 0.032,
      "test_icir": 1.2
    }
  ],
  "aggregated": {
    "test_ic": 0.028,
    "test_icir": 1.1,
    "overfitting_ratio": 1.6,
    "is_overfit": false
  },
  "created_at": "2026-02-02T10:30:00Z"
}
```

**Parameter Search Artifact Schema (`param_search.json`):**
```json
{
  "version": "1.0",
  "param_names": ["window", "zscore"],
  "param_ranges": {
    "window": [10, 15, 20, 25, 30],
    "zscore": [1.0, 1.5, 2.0, 2.5, 3.0]
  },
  "metric_name": "mean_ic",
  "best_params": {"window": 20, "zscore": 2.0},
  "best_score": 0.045,
  "all_results": [
    {"params": {"window": 10, "zscore": 1.0}, "score": 0.012},
    {"params": {"window": 10, "zscore": 1.5}, "score": 0.018}
  ],
  "created_at": "2026-02-02T10:30:00Z"
}
```

**Summary.json Updates:**
Add new fields to existing `summary.json`:
```json
{
  "has_walk_forward": true,
  "has_param_search": true,
  "walk_forward_windows": 8,
  "walk_forward_overfit": false
}
```

**BacktestResultStorage Updates:**

| File | Changes |
|------|---------|
| `libs/trading/backtest/result_storage.py` | Add `load_walk_forward()`, `load_param_search()` methods |
| `libs/trading/backtest/worker.py` | Write artifacts after walk-forward/param-search execution |

```python
# result_storage.py additions
class BacktestResultStorage:
    def load_walk_forward(self, job_id: str) -> WalkForwardResult | None:
        """Load walk-forward results, returns None if not available or legacy job."""
        path = self._job_path(job_id) / "walk_forward.json"
        if not path.exists():
            return None  # Legacy job or walk-forward not run
        # ... deserialize to WalkForwardResult

    def load_param_search(self, job_id: str) -> SearchResult | None:
        """Load parameter search results, returns None if not available."""
        path = self._job_path(job_id) / "param_search.json"
        if not path.exists():
            return None
        # ... deserialize to SearchResult
```

**Backward Compatibility:**
- Legacy jobs without `walk_forward.json`/`param_search.json` return `None`
- UI displays "No walk-forward data available" state (not error)
- Summary.json missing fields treated as `false`/0

### Service Layer Pattern Compliance

**Decision:** Extend `BacktestAnalyticsService` instead of creating separate service.

**Rationale:**
- Avoids duplicating ownership enforcement and async/sync bridging
- Single entry point for all backtest artifacts
- Consistent pattern with existing codebase

**BacktestAnalyticsService Extensions:**
```python
class BacktestAnalyticsService:
    # ... existing methods ...

    async def get_walk_forward_result(
        self, job_id: str
    ) -> WalkForwardResult | None:
        """Get walk-forward results for a backtest job."""
        await self.verify_job_ownership(job_id)
        return await run_in_threadpool(
            self._storage.load_walk_forward, job_id
        )

    async def get_parameter_grid_results(
        self, job_id: str
    ) -> SearchResult | None:
        """Get parameter grid search results."""
        await self.verify_job_ownership(job_id)
        return await run_in_threadpool(
            self._storage.load_param_search, job_id
        )
```

**Architecture Diagram Update:**
```
Service Layer:
  BacktestAnalyticsService (single entry point)
    - verify_job_ownership()           # existing
    - get_backtest_result()            # existing
    - run_quantile_analysis()          # existing
    - get_walk_forward_result()        # NEW (T11.1)
    - get_parameter_grid_results()     # NEW (T11.2)
```

---

## Tasks (4 total)

### T11.1: Walk-Forward Visualization - MEDIUM PRIORITY

**Goal:** Visualize train/test windows and stability.

**Current State:**
- Walk-forward logic exists in `libs/trading/backtest/walk_forward.py`
- `WalkForwardResult` contains `list[WindowResult]` with train/test dates and metrics
- No UI visualization

**Implementation Details:**

#### Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/components/walk_forward_timeline.py` | CREATE | Gantt chart component |
| `libs/web_console_services/backtest_analytics_service.py` | MODIFY | Add `get_walk_forward_result()` method |
| `libs/trading/backtest/result_storage.py` | MODIFY | Add `load_walk_forward()` method |
| `libs/trading/backtest/worker.py` | MODIFY | Write `walk_forward.json` artifact |
| `apps/web_console_ng/pages/backtest.py` | MODIFY | Integrate walk-forward tab |

#### Component: `walk_forward_timeline.py`

```python
# Data structure from walk_forward.py
@dataclass(frozen=True)
class WindowResult:
    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    best_params: dict[str, Any]
    train_ic: float
    test_ic: float
    test_icir: float

# Component functions to implement:
def render_walk_forward_gantt(
    windows: list[WindowResult],
    title: str = "Walk-Forward Timeline",
    height: int = 400,
) -> None:
    """Render Gantt chart showing train/test windows."""
    # Use Plotly go.Bar with horizontal orientation
    # Blue bars for train periods
    # Green/Red bars for test periods (based on test_ic)
    # Hover shows: window_id, dates, train_ic, test_ic

def render_walk_forward_summary(
    result: WalkForwardResult,
) -> None:
    """Render summary metrics card."""
    # Display: aggregated_test_ic, aggregated_test_icir
    # Overfitting ratio with warning if > threshold
    # Number of windows, date range

def render_window_metrics_table(
    windows: list[WindowResult],
) -> None:
    """Render table of per-window metrics."""
    # Columns: window_id, train_dates, test_dates, train_ic, test_ic, icir
    # Highlight windows with poor test performance
```

#### Service Extensions (in `backtest_analytics_service.py`)

```python
# Add to existing BacktestAnalyticsService class:

class BacktestAnalyticsService:
    # ... existing __init__, verify_job_ownership, get_backtest_result, etc. ...

    async def get_walk_forward_result(
        self, job_id: str
    ) -> WalkForwardResult | None:
        """Get walk-forward results for a backtest job."""
        await self.verify_job_ownership(job_id)
        return await run_in_threadpool(
            self._storage.load_walk_forward, job_id
        )

    async def get_parameter_grid_results(
        self, job_id: str
    ) -> SearchResult | None:
        """Get parameter grid search results."""
        await self.verify_job_ownership(job_id)
        return await run_in_threadpool(
            self._storage.load_param_search, job_id
        )
```

#### Visualization Pattern (Gantt Chart)

```python
# Following pattern from drawdown_chart.py
import plotly.graph_objects as go

def render_walk_forward_gantt(windows: list[WindowResult], ...):
    # Build traces for train and test periods
    train_traces = []
    test_traces = []

    for w in windows:
        # Train bar (blue)
        train_traces.append(go.Bar(
            y=[f"Window {w.window_id}"],
            x=[(w.train_end - w.train_start).days],
            base=[w.train_start],
            orientation="h",
            marker_color="rgba(31, 119, 180, 0.8)",
            name="Train" if w.window_id == 1 else None,
            showlegend=(w.window_id == 1),
            hovertemplate=(
                f"Train: {w.train_start} to {w.train_end}<br>"
                f"IC: {w.train_ic:.4f}<extra></extra>"
            ),
        ))

        # Test bar (green if positive IC, red if negative)
        test_color = "rgba(44, 160, 44, 0.8)" if w.test_ic > 0 else "rgba(214, 39, 40, 0.8)"
        test_traces.append(go.Bar(
            y=[f"Window {w.window_id}"],
            x=[(w.test_end - w.test_start).days],
            base=[w.test_start],
            orientation="h",
            marker_color=test_color,
            name="Test" if w.window_id == 1 else None,
            showlegend=(w.window_id == 1),
            hovertemplate=(
                f"Test: {w.test_start} to {w.test_end}<br>"
                f"IC: {w.test_ic:.4f}<br>"
                f"ICIR: {w.test_icir:.4f}<extra></extra>"
            ),
        ))

    fig = go.Figure(data=train_traces + test_traces)
    fig.update_layout(
        barmode="overlay",
        xaxis_title="Date",
        yaxis_title="Window",
        height=height,
    )
```

**Acceptance Criteria:**
- [ ] Gantt chart showing train/test windows with date axis
- [ ] Per-window IC/ICIR displayed on hover
- [ ] Visual distinction: train (blue), test positive (green), test negative (red)
- [ ] Summary card with aggregated metrics and overfitting warning
- [ ] Table view of all window metrics

---

### T11.2: Parameter Stability Heatmap - MEDIUM PRIORITY

**Goal:** Ensure parameter robustness (not overfitting).

**Current State:**
- Grid search exists in `libs/trading/backtest/param_search.py`
- Returns `SearchResult` (was GridSearchResult) with all parameter combinations and metrics
- No visualization of parameter landscape

**Implementation Details:**

#### Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/components/parameter_heatmap.py` | CREATE | 2D heatmap component |
| `libs/trading/backtest/param_search.py` | MODIFY | Extend SearchResult for visualization |
| `libs/web_console_services/backtest_analytics_service.py` | MODIFY | Add `get_parameter_grid_results()` method |

#### SearchResult Schema Evolution

**Current Schema** (from `param_search.py` - verified):
```python
@dataclass
class SearchResult:
    best_params: dict[str, Any]           # Optimal parameter combination
    best_score: float                      # Best metric value
    all_results: list[dict[str, Any]]     # Each dict has 'params' and 'score' keys
    # Example all_results entry: {"params": {"window": 20, "zscore": 2.0}, "score": 0.045}
```

**Extended Schema** (backward-compatible additions):
```python
@dataclass
class SearchResult:
    # Existing fields (DO NOT CHANGE - must match current code)
    best_params: dict[str, Any]
    best_score: float
    all_results: list[dict[str, Any]]     # {"params": {...}, "score": float}

    # NEW optional fields (with defaults for legacy data)
    param_names: list[str] = field(default_factory=list)
    param_ranges: dict[str, list] = field(default_factory=dict)
    metric_name: str = "mean_ic"  # Default; valid: "mean_ic", "icir", "hit_rate"
```

**How New Fields Are Populated** (in `grid_search()` function):
```python
def grid_search(
    alpha: AlphaDefinition,
    param_grid: dict[str, list[Any]],  # e.g., {"window": [10,20,30], "zscore": [1.0,2.0]}
    metric: str = "mean_ic",            # "mean_ic" | "icir" | "hit_rate"
    ...
) -> SearchResult:
    # ... existing grid search logic ...

    return SearchResult(
        best_params=best_params,
        best_score=best_score,
        all_results=all_results,          # [{"params": {...}, "score": float}, ...]
        # NEW: populated from function args
        param_names=list(param_grid.keys()),
        param_ranges=param_grid,
        metric_name=metric,
    )
```

**Migration Strategy:**
- Add new fields as optional with defaults (no breaking changes)
- New fields populated only when `grid_search()` is called with updated code
- Legacy SearchResult objects (without new fields) handled gracefully by service
- UI adapts to missing fields by inferring from `all_results`

**Affected Call Sites** (use existing field names, minimal changes):
- `libs/trading/backtest/walk_forward.py:optimize_window()` - no changes needed
- `libs/trading/backtest/param_search.py:grid_search()` - add new field population
- `tests/libs/trading/backtest/test_param_search.py` - add tests for new fields

#### Multi-Dimensional Grid Handling

For grids with >2 parameters, the UI provides:
1. **Axis Selection:** Dropdowns to select X and Y axis parameters
2. **Slice Selection:** Sliders/dropdowns to fix values for remaining parameters
3. **Best Slice:** Button to auto-select slice containing best parameters

```python
def render_parameter_axis_selector(
    param_names: list[str],
    current_x: str,
    current_y: str,
    on_change: Callable,
) -> None:
    """Render dropdown selectors for heatmap axes."""
    # Exclude selected params from other dropdown
    # Default: first two params or params with most variance

def render_slice_selector(
    search_result: SearchResult,
    fixed_params: dict[str, Any],
    excluded_params: set[str],  # X and Y axes
    on_change: Callable,
) -> None:
    """Render sliders/dropdowns for fixed parameter values."""
    # Show current slice, allow adjustment
    # Highlight if best params are in current slice
```

#### Component: `parameter_heatmap.py`

```python
@dataclass(frozen=True)
class ParameterGridPoint:
    """Single point in parameter grid."""
    params: dict[str, Any]  # e.g., {"window": 20, "zscore": 2.0}
    metric: float  # e.g., Sharpe ratio or IC
    is_best: bool  # Whether this is the optimal point

@dataclass(frozen=True)
class OverfittingAnalysis:
    """Analysis of parameter sensitivity."""
    is_isolated_peak: bool  # Best params surrounded by much worse
    stability_score: float  # 0-1, how stable is the plateau
    neighbors_mean: float  # Mean metric of neighboring points
    neighbors_std: float  # Std of neighboring points
    warning: str | None  # Warning message if overfitting suspected

def render_parameter_heatmap(
    grid_points: list[ParameterGridPoint],
    param_x: str,
    param_y: str,
    metric_name: str = "Sharpe Ratio",
    title: str = "Parameter Stability",
    height: int = 400,
) -> None:
    """Render 2D heatmap of parameter grid."""
    # Use Plotly go.Heatmap
    # X-axis: param_x values
    # Y-axis: param_y values
    # Color: metric value (RdYlGn colorscale)
    # Mark best point with annotation

def render_parameter_sensitivity(
    grid_points: list[ParameterGridPoint],
    analysis: OverfittingAnalysis,
) -> None:
    """Render sensitivity analysis summary."""
    # Stability score gauge
    # Warning if isolated peak detected
    # Recommended parameter ranges

def analyze_parameter_stability(
    grid_points: list[ParameterGridPoint],
    param_x: str,
    param_y: str,
) -> OverfittingAnalysis:
    """Analyze parameter grid for overfitting signs."""
    # Find best point
    # Calculate neighbors (adjacent in grid)
    # Check if peak is isolated (>2x better than neighbors)
    # Calculate stability score

# Configuration constants
ISOLATED_PEAK_THRESHOLD = 2.0  # Best metric > 2x neighbor mean → isolated
STABILITY_NEIGHBOR_COUNT = 8   # Max neighbors (Moore neighborhood)
```

**Neighbor Selection Method for Overfitting Analysis:**

For irregular/sparse grids, neighbor selection uses **sorted parameter ordering**:

```python
def _get_neighbors(
    grid_points: list[ParameterGridPoint],
    best_point: ParameterGridPoint,
    param_x: str,
    param_y: str,
) -> list[ParameterGridPoint]:
    """Get neighboring points in parameter grid."""
    # 1. Sort unique values for each parameter
    x_values = sorted(set(p.params[param_x] for p in grid_points))
    y_values = sorted(set(p.params[param_y] for p in grid_points))

    # 2. Find best point indices
    best_xi = x_values.index(best_point.params[param_x])
    best_yi = y_values.index(best_point.params[param_y])

    # 3. Get adjacent indices (Moore neighborhood: 8 neighbors)
    neighbor_indices = [
        (best_xi + dx, best_yi + dy)
        for dx in [-1, 0, 1]
        for dy in [-1, 0, 1]
        if not (dx == 0 and dy == 0)  # Exclude self
        and 0 <= best_xi + dx < len(x_values)
        and 0 <= best_yi + dy < len(y_values)
    ]

    # 4. Map indices back to param values and find matching points
    neighbors = []
    for xi, yi in neighbor_indices:
        target_x = x_values[xi]
        target_y = y_values[yi]
        for p in grid_points:
            if p.params[param_x] == target_x and p.params[param_y] == target_y:
                neighbors.append(p)
                break
        # Note: Missing grid point is OK (sparse grid) - just skip

    return neighbors

def _compute_stability_score(
    best_metric: float,
    neighbor_metrics: list[float],
) -> float:
    """Compute stability score (0-1, higher = more stable)."""
    if not neighbor_metrics:
        return 0.0  # No neighbors = can't assess stability

    neighbor_mean = sum(neighbor_metrics) / len(neighbor_metrics)
    neighbor_std = (sum((m - neighbor_mean) ** 2 for m in neighbor_metrics) / len(neighbor_metrics)) ** 0.5

    if neighbor_mean == 0:
        return 0.0

    # Score based on: (1) relative difference to neighbors, (2) neighbor variance
    relative_diff = abs(best_metric - neighbor_mean) / abs(neighbor_mean)
    cv = neighbor_std / abs(neighbor_mean) if neighbor_mean != 0 else float('inf')

    # Stable if: small relative diff AND low neighbor variance
    stability = max(0, 1 - relative_diff) * max(0, 1 - cv)
    return min(1.0, stability)
```

#### Visualization Pattern (Heatmap)

```python
# Following pattern from correlation_matrix.py
import plotly.graph_objects as go

def render_parameter_heatmap(grid_points, param_x, param_y, ...):
    # Extract unique values for each parameter
    x_values = sorted(set(p.params[param_x] for p in grid_points))
    y_values = sorted(set(p.params[param_y] for p in grid_points))

    # Build 2D matrix
    z_matrix = [[None] * len(x_values) for _ in range(len(y_values))]
    best_point = None

    for p in grid_points:
        xi = x_values.index(p.params[param_x])
        yi = y_values.index(p.params[param_y])
        z_matrix[yi][xi] = p.metric
        if p.is_best:
            best_point = (xi, yi, p.metric)

    fig = go.Figure(data=go.Heatmap(
        z=z_matrix,
        x=[str(v) for v in x_values],
        y=[str(v) for v in y_values],
        colorscale="RdYlGn",
        text=[[f"{v:.3f}" if v else "" for v in row] for row in z_matrix],
        texttemplate="%{text}",
        hovertemplate=(
            f"{param_x}: %{{x}}<br>"
            f"{param_y}: %{{y}}<br>"
            f"{metric_name}: %{{z:.4f}}<extra></extra>"
        ),
    ))

    # Mark best point
    if best_point:
        fig.add_annotation(
            x=best_point[0],
            y=best_point[1],
            text="★",
            showarrow=False,
            font=dict(size=20, color="black"),
        )

    fig.update_layout(
        xaxis_title=param_x,
        yaxis_title=param_y,
        height=height,
    )
```

**Acceptance Criteria:**
- [ ] 2D heatmap (X: param1, Y: param2, Color: metric)
- [ ] Best parameters marked with star annotation
- [ ] Isolated peak detection with warning
- [ ] Stability score calculation
- [ ] Support for multiple parameter pairs (dropdown selector)

---

### T11.3: Decay Curve Visualization - MEDIUM PRIORITY

**Goal:** Show how quickly alpha decays.

**Current State:**
- `decay_curve.py` component exists in `apps/web_console_ng/components/`
- Already renders IC at different lags
- Missing: half-life annotation, turnover recommendation

**Implementation Details:**

#### Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `apps/web_console_ng/components/decay_curve.py` | MODIFY | Add half-life annotation, turnover rec |
| `libs/trading/alpha/research_platform.py` | VERIFY | Ensure decay_half_life is computed |

#### Enhancements to `decay_curve.py`

```python
# Current signature (already exists):
def render_decay_curve(
    decay_curve: pl.DataFrame | None,
    half_life: float | None = None,
    title: str = "Signal Decay Curve",
    height: int = 350,
) -> None:
    """Render decay curve with IC at different lags."""
    # EXISTING: Rank IC trace (blue)
    # EXISTING: Pearson IC trace (gray dotted)

    # ENHANCEMENT: Add half-life vertical line with label
    if half_life is not None:
        # Compute max_ic from decay_curve data for annotation positioning
        max_ic = decay_curve.select("rank_ic").max().item()

        fig.add_vline(
            x=half_life,
            line=dict(color="red", width=2, dash="dash"),
        )
        fig.add_annotation(
            x=half_life,
            y=0.8 * max_ic,  # Position at 80% of max IC height
            text=f"Half-life: {half_life:.1f}d",
            showarrow=True,
            arrowhead=2,
        )

def render_turnover_recommendation(
    half_life: float | None,
    decay_curve: pl.DataFrame | None,
) -> None:
    """Render turnover frequency recommendation."""
    # Based on half-life:
    # - half_life < 3: "High-frequency rebalancing (daily)"
    # - 3 <= half_life < 10: "Medium-frequency (2-3 days)"
    # - 10 <= half_life < 20: "Low-frequency (weekly)"
    # - half_life >= 20: "Very low frequency (bi-weekly+)"

    # Show in card format with recommendation
```

**Note:** The decay curve component largely exists. This task focuses on:
1. Ensuring half-life annotation is properly rendered
2. Adding turnover frequency recommendation based on half-life
3. Integration into backtest page if not already present

**Acceptance Criteria:**
- [ ] IC at different lags (1d, 2d, 5d, 10d, 20d) - EXISTING
- [ ] Decay curve plot (X: lag, Y: IC) - EXISTING
- [ ] Half-life vertical line with annotation - ENHANCE
- [ ] Turnover frequency recommendation card - NEW

---

### T11.4: Alpha Cluster Map - LOW PRIORITY

**Goal:** Group similar alphas to avoid redundant exposure.

**Current State:**
- Correlation computation exists in `alpha_combiner.py`
- `AlphaExplorerService.compute_correlation()` returns correlation matrix
- No hierarchical clustering or dendrogram visualization

**UI Integration Target:**
- **Page:** `apps/web_console_ng/pages/alpha_explorer.py`
- **Tab Name:** "Clustering" (new tab in alpha explorer)
- **Navigation:** Alpha Explorer → Clustering tab
- **Components:** Dendrogram, Correlation Heatmap, Cluster Summary, Assignments Table

**Implementation Details:**

#### Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `libs/trading/alpha/alpha_clustering.py` | CREATE | Clustering algorithms |
| `apps/web_console_ng/components/alpha_cluster_map.py` | CREATE | Dendrogram and cluster UI |
| `libs/web_console_services/alpha_explorer_service.py` | MODIFY | Add clustering methods |
| `apps/web_console_ng/pages/alpha_explorer.py` | MODIFY | Add Clustering tab |

#### Correlation Matrix Schema (from `alpha_combiner.py`)

The expected correlation matrix format from `AlphaExplorerService.compute_correlation()`:

```python
# Schema: pl.DataFrame with columns:
# - "signal" (str): Signal name/ID (first column, acts as row label)
# - <signal_1> (float): Correlation with signal_1
# - <signal_2> (float): Correlation with signal_2
# - ... (one column per signal)
#
# Example:
# ┌─────────┬──────────┬──────────┬──────────┐
# │ signal  │ alpha_1  │ alpha_2  │ alpha_3  │
# ├─────────┼──────────┼──────────┼──────────┤
# │ alpha_1 │ 1.000    │ 0.750    │ 0.230    │
# │ alpha_2 │ 0.750    │ 1.000    │ 0.120    │
# │ alpha_3 │ 0.230    │ 0.120    │ 1.000    │
# └─────────┴──────────┴──────────┴──────────┘
#
# Validation rules:
# - Matrix must be symmetric (corr[i,j] == corr[j,i])
# - Diagonal must be 1.0
# - Values must be in [-1, 1] range
# - NaN values indicate missing correlations (handle gracefully)
```

**Signal ID to Display Name Mapping:**

`AlphaExplorerService` uses model_id with suffix for uniqueness but UI needs human-readable names:

```python
@dataclass(frozen=True)
class SignalMetadata:
    """Metadata for correlation matrix display."""
    signal_id: str       # Unique ID used in matrix (e.g., "momentum_v2_20240101")
    display_name: str    # Human-readable name (e.g., "Momentum v2")
    alpha_name: str      # Base alpha name (e.g., "momentum")
    created_at: date     # For disambiguation

# AlphaExplorerService provides mapping:
async def get_signal_metadata(
    self, signal_ids: list[str]
) -> dict[str, SignalMetadata]:
    """Get display metadata for signals."""
    # Returns {signal_id: SignalMetadata} mapping

# UI uses mapping for display:
def render_alpha_dendrogram(cluster_result, signal_metadata):
    labels = [
        signal_metadata.get(a.signal_id, SignalMetadata(a.signal_id, a.signal_id, "", date.today())).display_name
        for a in cluster_result.assignments
    ]
```

**Handling Missing/Invalid Correlation Matrix:**

```python
def render_alpha_cluster_map(correlation_matrix: pl.DataFrame | None, ...):
    # Case 1: No data
    if correlation_matrix is None:
        with ui.card().classes("w-full p-4"):
            ui.label("No correlation data available").classes("text-gray-500")
            ui.label("Select at least 2 alphas to compute correlation.").classes("text-sm text-gray-400")
        return

    # Case 2: Insufficient signals (<2)
    signal_cols = [c for c in correlation_matrix.columns if c != "signal"]
    if len(signal_cols) < 2:
        with ui.card().classes("w-full p-4"):
            ui.label("Insufficient alphas for clustering").classes("text-yellow-600")
            ui.label(f"Found {len(signal_cols)} alpha(s). Need at least 2.").classes("text-sm")
        return

    # Case 3: Validation fails (asymmetric, out of range)
    validation_errors = _validate_correlation_matrix(correlation_matrix)
    if validation_errors:
        with ui.card().classes("w-full p-4 bg-red-50"):
            ui.label("Invalid correlation matrix").classes("text-red-600 font-bold")
            for err in validation_errors[:3]:
                ui.label(f"• {err}").classes("text-sm text-red-500")
        return
```

#### Module: `alpha_clustering.py`

```python
from dataclasses import dataclass
from scipy.cluster.hierarchy import linkage, fcluster, dendrogram
from scipy.spatial.distance import squareform
import polars as pl
import numpy as np

@dataclass(frozen=True)
class ClusterAssignment:
    """Cluster assignment for a single alpha."""
    signal_id: str
    signal_name: str
    cluster_id: int

@dataclass(frozen=True)
class HighlyCorrelatedPair:
    """Pair of highly correlated alphas."""
    signal_1: str
    signal_2: str
    correlation: float

@dataclass(frozen=True)
class ClusterResult:
    """Result of hierarchical clustering."""
    assignments: list[ClusterAssignment]
    linkage_matrix: np.ndarray  # For dendrogram
    n_clusters: int
    highly_correlated_pairs: list[HighlyCorrelatedPair]
    correlation_matrix: pl.DataFrame

def validate_correlation_matrix(
    correlation_matrix: pl.DataFrame,
) -> list[str]:
    """Validate correlation matrix before clustering. Returns list of errors."""
    errors = []
    signal_cols = [c for c in correlation_matrix.columns if c != "signal"]

    if len(signal_cols) < 2:
        errors.append(f"Need at least 2 signals, got {len(signal_cols)}")
        return errors

    corr_values = correlation_matrix.select(signal_cols).to_numpy()

    # Check symmetric
    if not np.allclose(corr_values, corr_values.T, equal_nan=True):
        errors.append("Matrix is not symmetric")

    # Check diagonal = 1
    if not np.allclose(np.diag(corr_values), 1.0, equal_nan=True):
        errors.append("Diagonal values must be 1.0")

    # Check range [-1, 1]
    finite_vals = corr_values[np.isfinite(corr_values)]
    if np.any((finite_vals < -1) | (finite_vals > 1)):
        errors.append("Values must be in [-1, 1] range")

    return errors


def compute_hierarchical_clusters(
    correlation_matrix: pl.DataFrame,
    distance_threshold: float = 0.3,  # 1 - correlation
    correlation_threshold: float = 0.7,  # Flag pairs above this
    method: str = "average",  # Linkage method
) -> ClusterResult:
    """Compute hierarchical clusters from correlation matrix.

    NOTE: Validation should be called by service layer BEFORE this function.
    This function assumes valid input (symmetric, [-1,1] range, no NaNs in core).
    """
    # Extract signal names (first column or index)
    signal_names = correlation_matrix.columns[1:]  # Skip 'signal' column

    # Convert correlation to distance: distance = 1 - |correlation|
    corr_values = correlation_matrix.select(signal_names).to_numpy()
    distance_matrix = 1 - np.abs(corr_values)

    # Compute linkage
    condensed_dist = squareform(distance_matrix)
    Z = linkage(condensed_dist, method=method)

    # Assign clusters based on threshold
    cluster_ids = fcluster(Z, t=distance_threshold, criterion="distance")

    # Build assignments
    assignments = [
        ClusterAssignment(
            signal_id=name,
            signal_name=name,
            cluster_id=int(cid),
        )
        for name, cid in zip(signal_names, cluster_ids)
    ]

    # Find highly correlated pairs
    pairs = []
    for i, name_i in enumerate(signal_names):
        for j, name_j in enumerate(signal_names):
            if i < j and abs(corr_values[i, j]) > correlation_threshold:
                pairs.append(HighlyCorrelatedPair(
                    signal_1=name_i,
                    signal_2=name_j,
                    correlation=corr_values[i, j],
                ))

    return ClusterResult(
        assignments=assignments,
        linkage_matrix=Z,
        n_clusters=len(set(cluster_ids)),
        highly_correlated_pairs=pairs,
        correlation_matrix=correlation_matrix,
    )
```

#### Service-Layer Call Path (in `alpha_explorer_service.py`)

```python
from libs.trading.alpha.alpha_clustering import (
    validate_correlation_matrix,
    compute_hierarchical_clusters,
    ClusterResult,
)

class AlphaExplorerService:
    # ... existing methods ...

    async def compute_alpha_clusters(
        self,
        signal_ids: list[str],
        distance_threshold: float = 0.3,
        correlation_threshold: float = 0.7,
    ) -> ClusterResult | list[str]:
        """Compute alpha clusters with validation.

        Returns:
            ClusterResult on success, or list[str] of validation errors on failure.
        """
        # 1. Get correlation matrix (existing method)
        correlation_matrix = await self.compute_correlation(signal_ids)

        if correlation_matrix is None:
            return ["No correlation data available"]

        # 2. Validate before clustering (service responsibility)
        validation_errors = validate_correlation_matrix(correlation_matrix)
        if validation_errors:
            return validation_errors  # Return errors to UI for display

        # 3. Compute clusters (assumes valid input)
        return await run_in_threadpool(
            compute_hierarchical_clusters,
            correlation_matrix,
            distance_threshold,
            correlation_threshold,
        )
```

**Error Handling Flow:**
```
UI calls service.compute_alpha_clusters(signal_ids)
  → service calls compute_correlation()
  → service calls validate_correlation_matrix()
  → if errors: return list[str] to UI
  → UI displays error messages (see render_alpha_cluster_map)
  → if valid: service calls compute_hierarchical_clusters()
  → return ClusterResult to UI
  → UI renders dendrogram/heatmap
```

**Test Coverage for Validation Call:**
```python
# In test_alpha_explorer_service.py (extend existing)
async def test_compute_alpha_clusters_validates_matrix():
    """Verify validation is called before clustering."""
    service = AlphaExplorerService(...)

    # Create invalid correlation matrix (asymmetric)
    invalid_matrix = pl.DataFrame({
        "signal": ["a", "b"],
        "a": [1.0, 0.5],
        "b": [0.3, 1.0],  # Asymmetric: 0.5 != 0.3
    })

    with patch.object(service, 'compute_correlation', return_value=invalid_matrix):
        result = await service.compute_alpha_clusters(["a", "b"])

    assert isinstance(result, list)  # Returns errors, not ClusterResult
    assert any("symmetric" in err.lower() for err in result)

async def test_compute_alpha_clusters_success():
    """Verify valid matrix produces ClusterResult."""
    service = AlphaExplorerService(...)

    valid_matrix = pl.DataFrame({
        "signal": ["a", "b"],
        "a": [1.0, 0.5],
        "b": [0.5, 1.0],
    })

    with patch.object(service, 'compute_correlation', return_value=valid_matrix):
        result = await service.compute_alpha_clusters(["a", "b"])

    assert isinstance(result, ClusterResult)
```

#### Component: `alpha_cluster_map.py`

```python
import plotly.figure_factory as ff
import plotly.graph_objects as go

def render_alpha_dendrogram(
    cluster_result: ClusterResult,
    title: str = "Alpha Cluster Dendrogram",
    height: int = 500,
) -> None:
    """Render hierarchical clustering dendrogram."""
    signal_names = [a.signal_name for a in cluster_result.assignments]

    # NOTE on create_dendrogram usage:
    # - X: Distance matrix (1 - |correlation|), used only if linkagefun is None
    # - linkagefun: When provided, X is passed to this function but we IGNORE it
    #   and return our precomputed linkage matrix instead. This ensures the
    #   dendrogram matches our ClusterResult exactly.
    # - The lambda ignores x (distance input) and returns precomputed linkage
    fig = ff.create_dendrogram(
        X=1 - np.abs(cluster_result.correlation_matrix.select(signal_names).to_numpy()),
        labels=signal_names,
        linkagefun=lambda x: cluster_result.linkage_matrix,  # x ignored, use precomputed
    )

    fig.update_layout(
        title=title,
        xaxis_title="Alpha",
        yaxis_title="Distance (1 - |correlation|)",
        height=height,
    )

    with ui.card().classes("w-full"):
        ui.plotly(fig).classes("w-full")

def render_correlation_heatmap(
    cluster_result: ClusterResult,
    title: str = "Alpha Correlation Matrix",
    height: int = 500,
) -> None:
    """Render correlation matrix heatmap."""
    # Following pattern from correlation_matrix.py
    signal_names = [a.signal_name for a in cluster_result.assignments]
    corr_values = cluster_result.correlation_matrix.select(signal_names).to_numpy()

    fig = go.Figure(data=go.Heatmap(
        z=corr_values,
        x=signal_names,
        y=signal_names,
        colorscale="RdYlGn",
        zmin=-1,
        zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in corr_values],
        texttemplate="%{text}",
    ))

    fig.update_layout(title=title, height=height)

    with ui.card().classes("w-full"):
        ui.plotly(fig).classes("w-full")

def render_cluster_summary(
    cluster_result: ClusterResult,
) -> None:
    """Render cluster summary and warnings."""
    with ui.card().classes("w-full p-4"):
        ui.label("Alpha Clusters").classes("text-lg font-bold mb-2")

        # Cluster count
        ui.label(f"Found {cluster_result.n_clusters} distinct clusters")

        # Highly correlated pairs warning
        if cluster_result.highly_correlated_pairs:
            ui.label("⚠️ Highly Correlated Pairs:").classes("text-yellow-600 mt-4")
            for pair in cluster_result.highly_correlated_pairs[:5]:  # Show top 5
                ui.label(
                    f"  {pair.signal_1} ↔ {pair.signal_2}: {pair.correlation:.3f}"
                ).classes("text-sm text-gray-600 ml-4")

def render_cluster_assignments_table(
    cluster_result: ClusterResult,
) -> None:
    """Render table of cluster assignments."""
    # Build DataFrame for display
    data = [
        {"Alpha": a.signal_name, "Cluster": a.cluster_id}
        for a in sorted(cluster_result.assignments, key=lambda x: x.cluster_id)
    ]
    df = pl.DataFrame(data)

    with ui.card().classes("w-full p-4"):
        ui.label("Cluster Assignments").classes("text-lg font-bold mb-2")
        ui.table.from_polars(df).classes("w-full")
```

**Acceptance Criteria:**
- [ ] Alpha correlation matrix heatmap
- [ ] Hierarchical clustering with configurable threshold
- [ ] Dendrogram visualization
- [ ] Highly correlated pairs flagged (> 0.7 threshold)
- [ ] Cluster assignments table

---

## Dependencies

```
┌─────────────────────────────────────────────────────────────────┐
│                         Existing Code                           │
├─────────────────────────────────────────────────────────────────┤
│ libs/trading/backtest/walk_forward.py                           │
│   └── WalkForwardResult, WindowResult, WalkForwardOptimizer     │
│                                                                 │
│ libs/trading/backtest/param_search.py                           │
│   └── SearchResult, grid_search()                               │
│                                                                 │
│ libs/trading/alpha/alpha_combiner.py                            │
│   └── compute_correlation(), CorrelationAnalysisResult          │
│                                                                 │
│ apps/web_console_ng/components/decay_curve.py                   │
│   └── render_decay_curve() (already implemented)                │
│                                                                 │
│ libs/web_console_services/alpha_explorer_service.py             │
│   └── get_decay_curve(), compute_correlation()                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         New Code (P6T11)                        │
├─────────────────────────────────────────────────────────────────┤
│ T11.1: walk_forward_timeline.py + BacktestAnalyticsService ext  │
│ T11.2: parameter_heatmap.py (uses SearchResult)                 │
│ T11.3: decay_curve.py enhancements (half-life, turnover rec)    │
│ T11.4: alpha_clustering.py + alpha_cluster_map.py               │
└─────────────────────────────────────────────────────────────────┘
```

---

## File Structure (New/Modified)

```
libs/
├── trading/
│   ├── alpha/
│   │   └── alpha_clustering.py                    # NEW - T11.4
│   └── backtest/
│       ├── result_storage.py                      # MODIFY - add artifact loaders
│       ├── worker.py                              # MODIFY - write artifacts
│       └── param_search.py                        # MODIFY - extend SearchResult
├── web_console_services/
│   ├── backtest_analytics_service.py              # MODIFY - add walk-forward/param methods
│   └── alpha_explorer_service.py                  # MODIFY - add clustering + metadata

apps/web_console_ng/
├── components/
│   ├── walk_forward_timeline.py                   # NEW - T11.1
│   ├── parameter_heatmap.py                       # NEW - T11.2
│   ├── decay_curve.py                             # MODIFY - T11.3
│   └── alpha_cluster_map.py                       # NEW - T11.4
├── pages/
│   ├── backtest.py                                # MODIFY - add tabs
│   └── alpha_explorer.py                          # MODIFY - add Clustering tab

tests/
├── libs/
│   ├── trading/
│   │   ├── alpha/
│   │   │   └── test_alpha_clustering.py           # NEW
│   │   └── backtest/
│   │       └── test_result_storage.py             # MODIFY - add artifact tests
│   └── web_console_services/
│       └── test_backtest_analytics_service.py     # MODIFY - add new method tests
└── apps/web_console_ng/
    └── components/
        ├── test_walk_forward_timeline.py          # NEW
        ├── test_parameter_heatmap.py              # NEW
        └── test_alpha_cluster_map.py              # NEW
```

---

## Testing Strategy

### Unit Tests (libs/)

**test_alpha_clustering.py:**
- `test_compute_hierarchical_clusters_basic` - Basic clustering with 3-4 alphas
- `test_compute_hierarchical_clusters_identical` - All identical signals → 1 cluster
- `test_compute_hierarchical_clusters_uncorrelated` - Independent signals → N clusters
- `test_highly_correlated_pairs_detection` - Pairs above threshold flagged
- `test_distance_threshold_effect` - Different thresholds produce different clusters
- `test_linkage_matrix_shape` - Verify scipy-compatible output
- `test_empty_correlation_matrix` - Edge case handling
- `test_correlation_matrix_with_nans` - NaN values handled gracefully
- `test_correlation_matrix_asymmetric` - Validation catches asymmetric matrices
- `test_single_alpha_clustering` - Edge case with only one alpha

**test_backtest_analytics_service.py** (extend existing):
- `test_get_walk_forward_result_success` - Valid job returns result
- `test_get_walk_forward_result_not_found` - Missing job returns None
- `test_get_walk_forward_result_legacy_job` - Legacy job without artifact returns None
- `test_get_parameter_grid_results_success` - Grid search results retrieval
- `test_get_parameter_grid_results_missing` - Missing artifact returns None
- `test_verify_job_ownership_denied` - Unauthorized access blocked
- `test_run_in_threadpool_path` - Verify async/sync bridging works

**test_result_storage.py** (extend existing):
- `test_load_walk_forward_success` - Valid artifact deserializes correctly
- `test_load_walk_forward_missing_file` - Missing file returns None
- `test_load_walk_forward_invalid_json` - Corrupted file handled gracefully
- `test_load_walk_forward_schema_v1` - Version 1.0 schema parsed correctly
- `test_load_param_search_success` - Valid artifact deserializes correctly
- `test_load_param_search_legacy_schema` - Legacy SearchResult fields work
- `test_save_walk_forward_roundtrip` - Serialize/deserialize preserves data

### Component Tests (apps/)

**test_walk_forward_timeline.py:**
- `test_render_gantt_empty_windows` - Empty state handled
- `test_render_gantt_single_window` - Single window rendered
- `test_render_gantt_multiple_windows` - Multiple windows with correct colors
- `test_render_summary_with_overfitting_warning` - Warning displayed when overfit
- `test_render_window_metrics_table` - Table with all columns

**test_parameter_heatmap.py:**
- `test_render_heatmap_2d` - Basic 2D rendering
- `test_render_heatmap_best_point_marked` - Star annotation present
- `test_analyze_stability_isolated_peak` - Overfitting detection
- `test_analyze_stability_plateau` - Stable plateau detected
- `test_render_with_none_values` - Missing grid points handled
- `test_render_sparse_grid` - Non-rectangular parameter combinations
- `test_render_with_nan_metrics` - NaN metric values handled
- `test_render_large_grid_truncation` - Grid >20x20 shows warning and samples
- `test_multi_dimensional_axis_selection` - 3+ param grids slice correctly
- `test_render_single_param_line_chart` - 1D grid renders as line chart

**test_alpha_cluster_map.py:**
- `test_render_dendrogram` - Dendrogram renders without error
- `test_render_correlation_heatmap` - Heatmap renders correctly
- `test_render_cluster_summary_with_warnings` - Warnings displayed
- `test_render_assignments_table` - Table sorted by cluster

### Integration Tests

- Walk-forward visualization with real BacktestResult
- Parameter heatmap with grid_search output
- Alpha clustering with multiple backtest results

### E2E Tests

- Navigate to backtest page → Walk-forward tab → Gantt renders
- Navigate to backtest page → Parameters tab → Heatmap renders
- Navigate to alpha explorer → Cluster map → Dendrogram renders

---

## Implementation Order

**Phase 0: Storage & Schema Foundation** (Required First)
1. Define artifact schemas (`walk_forward.json`, `param_search.json`)
2. Extend `BacktestResultStorage` with `load_walk_forward()`, `load_param_search()`
3. Update `worker.py` to write artifacts after execution
4. Update `summary.json` schema with new fields
5. Add serialization/deserialization tests

**Phase 1: T11.1 Walk-Forward Visualization** (Medium Priority)
1. Extend `BacktestAnalyticsService` with `get_walk_forward_result()`
2. Create Gantt chart component (`walk_forward_timeline.py`)
3. Integrate into backtest page (new "Walk-Forward" tab)
4. Add service and component tests

**Phase 2: T11.2 Parameter Stability Heatmap** (Medium Priority)
1. Extend `BacktestAnalyticsService` with `get_parameter_grid_results()`
2. Extend `SearchResult` with optional visualization fields
3. Create heatmap component (`parameter_heatmap.py`)
4. Add overfitting analysis logic
5. Integrate into backtest page (new "Parameters" tab)

**Phase 3: T11.3 Decay Curve Enhancements** (Medium Priority)
1. Enhance existing `decay_curve.py` component
2. Add turnover recommendation card
3. Verify half-life annotation rendering

**Phase 4: T11.4 Alpha Cluster Map** (Low Priority)
1. Create clustering module (`alpha_clustering.py`)
2. Extend `AlphaExplorerService` with clustering methods
3. Create cluster visualization component (`alpha_cluster_map.py`)
4. Integrate into alpha explorer page (new "Clustering" tab)

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| scipy dependency for clustering | scipy already in requirements (test_fama_french uses it) |
| Plotly dendrogram complexity | Use figure_factory.create_dendrogram for simplicity |
| Large parameter grids | Limit grid display to 20x20 max with explicit UX (see below) |
| Many alphas in cluster map | Limit to 20 alphas with explicit UX (see below) |

### Large Grid/Alpha Truncation UX Behavior

**Parameter Heatmap (>20x20 grid):**
```python
MAX_HEATMAP_AXIS = 20

def render_parameter_heatmap_with_limits(...):
    x_values = sorted(set(p.params[param_x] for p in grid_points))
    y_values = sorted(set(p.params[param_y] for p in grid_points))

    if len(x_values) > MAX_HEATMAP_AXIS or len(y_values) > MAX_HEATMAP_AXIS:
        # Show warning banner
        with ui.card().classes("w-full p-4 bg-yellow-50 border-yellow-200"):
            ui.label("⚠️ Grid too large for heatmap display").classes("font-bold text-yellow-700")
            ui.label(
                f"Grid has {len(x_values)}×{len(y_values)} points. "
                f"Displaying sampled subset of {MAX_HEATMAP_AXIS}×{MAX_HEATMAP_AXIS}."
            ).classes("text-sm text-yellow-600")

            # Option to see full data
            with ui.row():
                ui.button("Download Full Grid (CSV)", on_click=lambda: export_grid_csv(...))
                ui.button("Show Best Region", on_click=lambda: zoom_to_best(...))

        # Sample uniformly around best point
        x_values = _sample_around_best(x_values, best_x, MAX_HEATMAP_AXIS)
        y_values = _sample_around_best(y_values, best_y, MAX_HEATMAP_AXIS)
```

**Alpha Cluster Map (>20 alphas):**
```python
MAX_CLUSTER_ALPHAS = 20

def render_alpha_cluster_map_with_limits(signal_ids: list[str], ...):
    if len(signal_ids) > MAX_CLUSTER_ALPHAS:
        # Show warning and selection UI
        with ui.card().classes("w-full p-4 bg-yellow-50 border-yellow-200"):
            ui.label("⚠️ Too many alphas for cluster visualization").classes("font-bold text-yellow-700")
            ui.label(
                f"Selected {len(signal_ids)} alphas. Maximum is {MAX_CLUSTER_ALPHAS}."
            ).classes("text-sm text-yellow-600")

            # Selection options
            with ui.row():
                ui.button("Use Top 20 by IC", on_click=lambda: filter_by_ic(...))
                ui.button("Use Top 20 by ICIR", on_click=lambda: filter_by_icir(...))
                ui.button("Select Manually", on_click=lambda: show_selection_dialog(...))

        return  # Don't render until selection is made

    # Proceed with normal rendering
    ...
```

---

## Definition of Done

- [ ] All 4 tasks implemented
- [ ] Walk-forward visualization working (Gantt + summary + table)
- [ ] Parameter stability heatmap functional (heatmap + overfitting analysis)
- [ ] Decay curve enhanced (half-life annotation + turnover recommendation)
- [ ] Alpha cluster map available (dendrogram + correlation matrix + warnings)
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-02-02 (Rev 8 - Addressed final review suggestions)
**Status:** PLANNING (Approved by Gemini, Codex)
