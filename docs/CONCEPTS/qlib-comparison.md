# Qlib vs Trading Platform: Data Pipeline Comparison & Optimization Opportunities

**Document Version:** 1.0
**Date:** 2025-12-07
**Purpose:** Analyze Qlib's architecture to identify optimization opportunities for P4

---

## Executive Summary

After comprehensive analysis of Qlib's architecture against our P4 implementation, I've identified **5 high-value adoption opportunities** and **3 areas where our approach is superior**. The key insight is that Qlib excels at **research velocity** (expression DSL, caching) while our platform excels at **production safety** (PIT correctness, atomic operations).

### Recommendations Overview

| Component | Recommendation | Priority | Effort |
|-----------|----------------|----------|--------|
| Expression Engine (FormulaicFactor) | **ADOPT** - Phase 3 | P1 | 3-4d |
| Data Caching (DiskExpressionCache) | **ADOPT** - T2.8 | P1 | 2-3d |
| Alpha Metrics (IC/ICIR/Rank IC) | **ADOPT** - T2.5 | P0 | 2d |
| Rolling Workflow | **ADAPT** | P2 | 3d |
| Risk Model Estimators | **KEEP OURS** | - | - |
| PIT/Versioning | **KEEP OURS** | - | - |
| TAQ/High-Freq | **KEEP OURS (Polars)** | - | - |

---

## Detailed Comparison

### 1. Data Infrastructure

#### Qlib Approach
```
Qlib Data Pipeline:
                         ┌─────────────────┐
Raw Data (CSV/Parquet) → │ dump_bin.py     │ → Binary .bin files
                         │ (Normalize)      │   (Calendar, Instruments, Features)
                         └─────────────────┘
                                 ↓
                         ┌─────────────────┐
                         │ DataLoader      │ → DataHandler → Dataset
                         │ (QlibDataLoader)│   (Processing)   (Sampling)
                         └─────────────────┘
```

**Key Features:**
- Binary format optimized for sequential reads
- Region-specific configs (REG_US, REG_CN)
- Central `qlib.init()` with provider registration
- Abstract interfaces: `CalendarStorage`, `InstrumentStorage`, `FeatureStorage`

#### Our P4 Approach
```
P4 Data Pipeline:
                         ┌─────────────────┐
WRDS/External APIs    → │ SyncManager     │ → Parquet files
                         │ (Atomic Write)   │   (Month partitioned)
                         └─────────────────┘
                                 ↓
                         ┌─────────────────┐
                         │ LocalProvider   │ → ManifestManager → DatasetVersionManager
                         │ (CRSP/Compustat)│   (PIT Tracking)    (Snapshots)
                         └─────────────────┘
```

**Our Advantages:**
- **PIT Correctness**: Every query is time-travel aware via `DatasetVersionManager.query_as_of()`
- **Atomic Operations**: `_atomic_write_parquet()` with fsync, quarantine on failure
- **Schema Drift Detection**: `SchemaRegistry.detect_drift()` prevents breaking changes
- **Manifest Tracking**: Every dataset has checksums, row counts, date ranges

**Qlib Advantages:**
- Faster binary format for large-scale research
- Built-in region/frequency handling
- More mature data collection framework

#### Recommendation: **KEEP OURS** with selective adoption

Our Parquet-based approach is superior for:
- Production safety (atomic writes, PIT queries)
- Schema evolution (drift detection)
- Integration with modern tools (Polars, DuckDB)

However, adopt Qlib's **caching pattern** for expensive computations:
```python
# Proposed: libs/data_quality/cache.py
class DiskExpressionCache:
    """Cache computed features to disk for reuse."""

    def __init__(self, cache_dir: Path, ttl_days: int = 7):
        self.cache_dir = cache_dir
        self.ttl_days = ttl_days

    def get_or_compute(
        self,
        key: str,  # e.g., "momentum_12_1:2024-01-15:v1.2.3"
        compute_fn: Callable[[], pl.DataFrame],
    ) -> pl.DataFrame:
        """Return cached result or compute and cache."""
        cache_path = self._key_to_path(key)
        if cache_path.exists() and not self._is_stale(cache_path):
            return pl.read_parquet(cache_path)
        result = compute_fn()
        result.write_parquet(cache_path)
        return result
```

---

### 2. Feature Engineering / Factor Definitions

#### Qlib Approach: Expression DSL
```python
# Qlib's FormulaicFactor pattern
from qlib.data.ops import Ref, Mean, Std

# Define momentum via expression
"(($close - Ref($close, 252)) / Ref($close, 252))"

# Custom operators
class MyCustomOp(ExpressionOps):
    def _load_internal(self, instrument, start, end, freq):
        # Custom computation
        return result
```

**Key Features:**
- String-based expression DSL
- Operator registration system (`qlib.data.ops`)
- Auto-caching of expressions
- Cross-sectional operators (CS_Rank, CS_ZScore)

#### Our P4 Approach: Static Class Definitions
```python
# libs/factors/factor_definitions.py
class MomentumFactor(FactorDefinition):
    name = "momentum_12_1"
    category = "momentum"
    lookback_days = 252
    skip_days = 21

    def compute(
        self, prices: pl.DataFrame, fundamentals: pl.DataFrame | None
    ) -> pl.DataFrame:
        returns = prices.select([
            pl.col("permno"),
            (pl.col("ret") + 1).product().over("permno") - 1
        ])
        return returns
```

**Our Advantages:**
- Full type safety and IDE support
- Explicit control over PIT lookback
- Testable as standalone units
- Clear documentation per factor

**Qlib Advantages:**
- Rapid prototyping (no class boilerplate)
- Expression caching built-in
- Easier factor combination/algebra

#### Recommendation: **HYBRID APPROACH** (Phase 3)

Keep static classes for production factors, add expression adapter for research:

```python
# Proposed: libs/factors/formulaic.py (Phase 3)
class FormulaicFactorAdapter:
    """Wrap Qlib-style expressions as FactorDefinition for research."""

    def __init__(self, name: str, expression: str):
        self.name = name
        self.expression = expression

    def to_factor_definition(self) -> FactorDefinition:
        """Convert expression to static FactorDefinition."""
        # Parse expression, generate equivalent static code
        ...

    def compute_fast(
        self,
        prices: pl.DataFrame,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Fast computation without full PIT tracking (research only)."""
        # Direct expression evaluation
        ...

# Usage in research notebooks:
mom = FormulaicFactorAdapter(
    "test_mom",
    "($close - Ref($close, 21)) / Ref($close, 21)"
)
exposures = mom.compute_fast(prices, date(2024, 1, 15))
```

---

### 3. Risk Models / Covariance Estimation

#### Qlib Approach
```python
# qlib/model/riskmodel/
class RiskModel(ABC):
    """Base for covariance estimation."""
    def predict(self, X: np.ndarray, **kwargs) -> np.ndarray: ...

class POETCovEstimator(RiskModel):
    """Principal Orthogonal Complement Thresholding."""

class ShrinkCovEstimator(RiskModel):
    """Ledoit-Wolf / OAS shrinkage."""

class StructuredCovEstimator(RiskModel):
    """PCA / Factor Analysis based."""
```

#### Our P4 Approach (T2.2-T2.4)
```python
# libs/risk/
class FactorCovarianceEstimator:
    """Exponentially-weighted factor covariance."""

class SpecificRiskEstimator:
    """Stock-level idiosyncratic risk with GARCH."""

class BarraRiskModel:
    """Complete Barra-style factor model."""

class RiskDecomposer:
    """MCTR/CCTR decomposition."""

class PortfolioOptimizer:
    """Mean-variance with constraints."""
```

**Our Advantages:**
- **Barra-specific**: Designed for equity factor models, not generic covariance
- **Integrated**: Single `BarraRiskModel` provides full pipeline
- **Production-ready**: Constraints, MCTR/CCTR, stress testing built-in
- **PIT-aware**: Version tracking on all outputs

**Qlib Advantages:**
- More estimator variety (POET, OAS)
- Better for research experimentation

#### Recommendation: **KEEP OURS**

Our risk infrastructure is more complete for production. Optional: add POET estimator as alternative:

```python
# libs/risk/poet_estimator.py (optional enhancement)
class POETCovarianceEstimator:
    """POET estimator for comparison with Barra approach."""
    # Implement if research needs arise
```

---

### 4. Alpha Metrics & Evaluation

#### Qlib Approach
```python
# qlib/contrib/evaluate.py
def risk_analysis(returns: pd.Series) -> dict:
    """Mean, std, annualized return, IR, max drawdown."""

def indicator_analysis(indicators: pd.DataFrame) -> dict:
    """Weighted PA, fulfill rate, positive rate."""

# qlib/contrib/report/analysis_model/
def model_performance_graph():
    """IC, ICIR, Rank IC, autocorrelation, turnover."""
```

**Key Metrics:**
- IC (Information Coefficient)
- ICIR (IC Information Ratio)
- Rank IC / Rank ICIR
- Grouped IC by sector/industry
- Autocorrelation of alphas

#### Our P4 Plan (T2.5-T2.6)
```python
# Planned: libs/alpha/metrics.py
class AlphaMetrics:
    """Basic IC computation."""
    def compute_ic(self, signal: pl.DataFrame, returns: pl.DataFrame) -> float: ...
```

**Gap Analysis:**
- We don't have grouped IC (by sector)
- No Rank IC implementation
- No ICIR time series
- No built-in visualization

#### Recommendation: **ADOPT** Qlib metrics with wrapper

```python
# Proposed: libs/alpha/metrics.py (T2.5 enhancement)
class AlphaMetricsAdapter:
    """Wrap Qlib Analysis with Polars inputs and local fallback."""

    def __init__(self, use_qlib: bool = True):
        self._use_qlib = use_qlib and self._qlib_available()

    def compute_ic(
        self,
        signal: pl.DataFrame,  # columns: date, symbol, signal
        returns: pl.DataFrame,  # columns: date, symbol, ret
        method: Literal["pearson", "rank"] = "rank",
    ) -> float:
        """Compute IC (cross-sectional correlation per date, then average)."""
        if self._use_qlib:
            # Convert Polars → Pandas for Qlib
            return self._qlib_ic(signal, returns, method)
        return self._local_ic(signal, returns, method)

    def compute_grouped_ic(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        groups: pl.DataFrame,  # columns: symbol, sector
    ) -> pl.DataFrame:
        """IC per sector group - key for understanding alpha sources."""
        ...

    def compute_icir(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        window: int = 20,
    ) -> pl.DataFrame:
        """Rolling ICIR for alpha stability analysis."""
        ...

    def _local_ic(self, signal, returns, method) -> float:
        """Pure Polars implementation (no Qlib dependency)."""
        merged = signal.join(returns, on=["date", "symbol"])
        if method == "rank":
            merged = merged.with_columns([
                pl.col("signal").rank().over("date").alias("signal"),
                pl.col("ret").rank().over("date").alias("ret"),
            ])
        # Cross-sectional correlation per date
        daily_ic = merged.group_by("date").agg(
            pl.corr("signal", "ret").alias("ic")
        )
        return daily_ic["ic"].mean()
```

---

### 5. TAQ / High-Frequency Data

#### Qlib Approach
```python
# examples/highfreq/
class HighFreqHandler:
    """Normalize 1-min data with custom operators."""

# qlib/contrib/ops/high_freq.py
class DayCumsum(ExpressionOps): ...
class DayLast(ExpressionOps): ...
class FFillNan(ExpressionOps): ...
```

**Storage:**
- Binary format via dump_bin.py
- Arctic backend for order book data (MongoDB)
- Custom operators for intraday patterns

#### Our P4 Approach (T1.7-T1.8, T3.1)
```python
# libs/data_providers/taq_storage.py
class TAQStorageManager:
    """Month-partitioned Parquet with atomic writes."""

# libs/data_providers/taq_query_provider.py
class TAQQueryProvider:
    """DuckDB-backed queries with time-range filtering."""

# libs/analytics/microstructure.py
class MicrostructureAnalyzer:
    """VPIN, realized volatility, spread/depth stats."""
```

**Our Advantages:**
- **Polars/DuckDB**: Modern stack, excellent for columnar HF data
- **Atomic Writes**: Crash-safe storage
- **PIT Integration**: Manifests track every TAQ sync
- **No External DB**: No MongoDB dependency

**Qlib Advantages:**
- Arctic for order book (tick-by-tick)
- More HF operators built-in

#### Recommendation: **KEEP OURS (Polars/DuckDB)**

Our approach is already better for TAQ analytics:
- Polars outperforms Pandas for large HF datasets
- DuckDB enables SQL queries without database server
- No serialization overhead to Qlib's binary format

Add explicit boundary constraint (already in P4T2_TASK v1.9):
```
**Architectural Constraint: Polars/DuckDB Boundary**
> All TAQ feature generation (RV, VPIN, spread, depth) in Polars/DuckDB only.
> Qlib receives daily aggregates via Parquet export.
```

---

### 6. Experiment Tracking / Model Registry

#### Qlib Approach
```python
# qlib/workflow/
class QlibRecorder:
    """MLflow-backed experiment tracking."""
    def start_exp(self, name: str): ...
    def log_params(self, params: dict): ...
    def log_metrics(self, metrics: dict): ...
    def save_objects(self, **kwargs): ...

# qlib/workflow/task/manage.py
class TaskManager:
    """MongoDB-backed task queue for rolling experiments."""
```

#### Our P4 Plan (T2.8)
```python
# Planned: libs/models/registry.py
@dataclass
class ModelMetadata:
    model_id: str
    model_type: str
    version: str
    created_at: datetime
    dataset_version_ids: dict[str, str]
    factor_list: list[str]
    parameters: dict
    checksum_sha256: str
    metrics: dict[str, float]
```

**Gap Analysis:**
- No experiment tracking system
- No task queue for rolling backtests
- Missing `config`, `config_hash`, `feature_formulas` fields

#### Recommendation: **ENHANCE** T2.8 with Qlib patterns

```python
# Enhanced: libs/models/registry.py (T2.8)
@dataclass
class ModelMetadata:
    model_id: str
    model_type: str
    version: str
    created_at: datetime

    # PIT tracking (existing)
    dataset_version_ids: dict[str, str]
    snapshot_id: str  # NEW: DatasetVersionManager snapshot

    # Model definition (existing)
    factor_list: list[str]
    parameters: dict
    checksum_sha256: str

    # Performance (existing)
    metrics: dict[str, float]
    env: EnvironmentMetadata

    # NEW: Training reproducibility (from Qlib pattern)
    config: dict                    # Full training config blob
    config_hash: str                # SHA-256 for dedup
    feature_formulas: list[str] | None  # Phase 3: FormulaicFactor expressions
```

---

### 7. Rolling Workflow / Online Learning

#### Qlib Approach
```python
# qlib/contrib/rolling/base.py
class Rolling:
    """Generalized rolling backtester."""
    def run(self):
        for task in self.generate_tasks():
            self.execute_task(task)
        self.ensemble_results()

# qlib/workflow/online/
class OnlineManager:
    """Production model updates."""
    def first_train(self): ...
    def routine(self): ...  # Daily update
```

#### Our P4 Approach
Currently no rolling workflow system.

#### Recommendation: **ADAPT** for future Phase

Not needed in P4T2, but consider for Phase 3:
```python
# Future: libs/workflows/rolling.py
class RollingWorkflow:
    """Rolling backtest with PIT integration."""

    def __init__(
        self,
        model_factory: Callable[[], Model],
        train_window: int,
        test_window: int,
        version_manager: DatasetVersionManager,
    ):
        self.model_factory = model_factory
        self.train_window = train_window
        self.test_window = test_window
        self.vm = version_manager

    def run(
        self,
        start_date: date,
        end_date: date,
    ) -> RollingResults:
        """Execute rolling backtest with PIT snapshots."""
        tasks = self._generate_rolling_tasks(start_date, end_date)
        results = []
        for task in tasks:
            # Each task uses snapshot at task.as_of_date
            snapshot = self.vm.query_as_of("crsp", task.as_of_date)
            model = self.model_factory()
            model.fit(task.train_data)
            preds = model.predict(task.test_data)
            results.append((task, preds))
        return RollingResults(results)
```

---

## Summary: What to Adopt vs Keep

### ADOPT from Qlib

| Feature | Where | Priority | Implementation |
|---------|-------|----------|----------------|
| **DiskExpressionCache** | T2.8 Model Registry | P1 | `libs/data_quality/cache.py` |
| **Alpha Metrics** (IC, ICIR, Rank IC, Grouped IC) | T2.5 Alpha Framework | P0 | `libs/alpha/metrics.py` |
| **FormulaicFactor Adapter** | Phase 3 | P1 | `libs/factors/formulaic.py` |
| **Config Hash for Reproducibility** | T2.8 | P1 | `ModelMetadata.config_hash` |

### KEEP Our Approach

| Component | Reason |
|-----------|--------|
| **PIT/Versioning** (`DatasetVersionManager`) | Superior production safety, time-travel queries |
| **Atomic Writes** (`_atomic_write_parquet`) | Crash-safe, fsync guarantees |
| **Schema Drift Detection** | Prevents breaking changes in production |
| **Polars/DuckDB for TAQ** | Better performance than Qlib's binary format for HF |
| **Barra Risk Model** | More complete than Qlib's generic estimators |
| **Static Factor Classes** | Type safety, testability, PIT control |

### NOT NEEDED

| Qlib Feature | Reason to Skip |
|--------------|----------------|
| Binary .bin format | Parquet + Polars is faster and more interoperable |
| Arctic/MongoDB | Adds infrastructure complexity |
| MLflow integration | Our registry is simpler and sufficient |
| Task Manager (MongoDB) | Not needed for our scale |

---

## Implementation Roadmap

### Phase 2 (Current - P4T2)

**T2.5 Alpha Framework:**
```
- Add AlphaMetricsAdapter with Qlib metrics wrapper
- Implement local fallback (pure Polars)
- Include grouped IC, ICIR, Rank IC
```

**T2.8 Model Registry:**
```
- Add config, config_hash, feature_formulas, snapshot_id fields
- Implement DiskExpressionCache for factor caching
- Increase effort from 3-4d to 4-5d
```

### Phase 3 (Future)

**FormulaicFactor Adapter:**
```
- Parse Qlib-style expressions
- Generate equivalent Polars operations
- Bridge research notebooks to production factors
- Effort: 3-4d
```

**Rolling Workflow:**
```
- Adapt Qlib's Rolling pattern with PIT integration
- Support parallel task execution
- Effort: 3d
```

---

## Appendix: Key Qlib Patterns Reference

### Expression DSL Syntax
```
# Price-based
$close, $open, $high, $low, $volume

# Time-shift
Ref($close, 5)        # Close 5 days ago
Mean($close, 20)      # 20-day SMA
Std($close, 20)       # 20-day std

# Cross-sectional
CSRank($close)        # Cross-sectional rank
CSZScore($close)      # Cross-sectional z-score

# Combinations
"($close - Mean($close, 20)) / Std($close, 20)"  # Z-score
"CSRank(Ref($close, 1) / Ref($close, 21) - 1)"   # Ranked momentum
```

### DataHandler Processing Flow
```
DataLoader → [Infer Processors] → [Learn Processors] → Dataset
               (Always run)         (Training only)
```

### Risk Model Hierarchy
```
RiskModel (base)
├── POETCovEstimator    # Thresholding
├── ShrinkCovEstimator  # Ledoit-Wolf/OAS
└── StructuredCovEstimator  # PCA/FA
```

---

**Document Author:** Claude
**Review Status:** Draft - Pending Codex review
