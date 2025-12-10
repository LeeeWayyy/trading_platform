# P4T2: Phase 2 - Analytics Infrastructure

**Task ID:** P4T2
**Phase:** P4 (Advanced Features & Research Infrastructure)
**Timeline:** Phase 2 - Analytics (Weeks 5-10, 6 weeks with buffer)
**Priority:** P0 - Core research infrastructure for alpha development
**Estimated Effort:** 39-49 days (13 subtasks across 3 parallel tracks)
**Status:** ✅ Complete
**Created:** 2025-12-07
**Last Updated:** 2025-12-09 (All 13 tasks complete - Ready for PR)

---

## Progress Tracker

| Task | Status | PR | Notes |
|------|--------|-----|-------|
| **Track 1: TAQ Completion** | | | |
| T1.7 TAQ Storage | ✅ Complete | `feat(P4T2): TAQ Storage and Query Implementation` | |
| T1.8 TAQ Query | ✅ Complete | (same PR) | |
| **Track 2: Factor & Risk** | | | |
| T2.1 Multi-Factor Model | ✅ Complete | `feat(P4T2): Multi-Factor Model Construction` | |
| T2.2 Factor Covariance | ✅ Complete | `feat(P4T2): Factor Covariance & Specific Risk Estimation` | |
| T2.3 Risk Analytics | ✅ Complete | `feat(P4T2): Portfolio Risk Analytics` | |
| T2.4 Optimizer & Stress | ✅ Complete | `feat(P4T2): Portfolio Optimizer & Stress Testing` | |
| T2.5 Alpha Framework | ✅ Complete | `feat(P4T2): Alpha Research Framework` | Qlib optional dependency |
| T2.6 Alpha Advanced | ✅ Complete | `feat(P4T2): Alpha Advanced Analytics` | |
| T2.7 Factor Attribution | ✅ Complete | `feat(P4T2): Factor Attribution Analysis Library` | |
| T2.8 Model Registry | ✅ Complete | `feat(P4T2): Model Registry & Deployment Versioning` | +DiskExpressionCache |
| **Track 3: Microstructure** | | | |
| T3.1 Microstructure | ✅ Complete | `feat(P4T2): Microstructure Analytics Library` | |
| T3.2 Execution Quality | ✅ Complete | `feat(P4T2): Execution Quality Analysis Library` | |
| T3.3 Event Study | ✅ Complete | `feat(P4T2): Event Study Framework` | |

**Progress:** 13/13 tasks complete (100%)

---

## Executive Summary

Phase 2 builds the analytics infrastructure for quantitative research. This phase includes three parallel tracks:

1. **Track 1 TAQ Completion** (T1.7-T1.8): High-frequency tick data storage and query interface
2. **Track 2 Factor & Risk Analytics** (T2.1-T2.8): Factor models, risk analytics, alpha research
3. **Track 3 Market Microstructure** (T3.1-T3.3): Volatility, execution quality, event studies

**Goal:** Production-ready factor models, risk analytics, alpha research platform, and market microstructure tools.

**Key Deliverables:**
- TAQ Data Storage & Query Interface (T1.7-T1.8)
- Multi-Factor Model Construction (T2.1)
- Risk Model with Covariance Estimation (T2.2-T2.3)
- Portfolio Optimizer & Stress Testing (T2.4)
- Alpha Research Framework (T2.5-T2.6)
- Factor Attribution Analysis (T2.7)
- Model Registry for Production Deployment (T2.8)
- Microstructure Analytics (T3.1)
- Execution Quality Analysis (T3.2)
- Event Study Framework (T3.3)

**Parallel Execution Plan (Rebalanced v2):**
- **Developer A (Track 2 + Attribution):** T2.1 → T2.2 → T2.3 → T2.4 → T2.5 → T2.6 → T2.7 (~25-32 days)
- **Developer B (Track 1 TAQ + Track 3 + Registry):** T1.7 → T1.8 → T3.1 → T3.2 → T3.3 → T2.8 (~19-24 days)

**Note:** T2.7 moved back to Dev A (Week 10) to avoid Dev B overload. Dev A uses integration testing buffer. Dev B completes T2.8 in parallel.

**Dependencies from P4T1 (COMPLETE):**
- T1.3 CRSP Local Provider
- T1.4 Compustat Local Provider
- T1.5 Fama-French Local Provider
- T1.6 Dataset Versioning
- T4.1-T4.3 Unified Data Fetcher & ETL

---

## PIT/Versioning Integration Contract

All P4T2 components MUST integrate with P4T1 Dataset Versioning (T1.6):

**Unified PIT API (from P4T1 T1.6):**
```python
# libs/data_quality/versioning.py (COMPLETE from P4T1)
class DatasetVersionManager:
    """Manages dataset versions for reproducibility."""

    def create_snapshot(
        self,
        version_tag: str,
        datasets: list[str] | None = None,
        use_cas: bool = True,
    ) -> SnapshotManifest:
        """Create immutable snapshot of current dataset states."""

    def get_snapshot(self, version_tag: str) -> SnapshotManifest | None:
        """Retrieve snapshot metadata by version tag."""

    def query_as_of(
        self,
        dataset: str,
        as_of_date: date,
    ) -> tuple[Path, SnapshotManifest]:
        """Get dataset path as it existed on given date (time-travel)."""

    def link_backtest(
        self,
        backtest_id: str,
        version_tag: str,
        datasets: list[str] | None = None,
    ) -> BacktestLinkage:
        """Atomically link backtest to snapshot for reproducibility."""
```

**Manifest Fields (all P4T2 outputs):**
```python
@dataclass
class P4T2OutputMetadata:
    output_type: str           # 'factor', 'covariance', 'alpha', 'microstructure'
    as_of_date: date
    dataset_version_ids: dict[str, str]  # {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}
    computation_timestamp: datetime
    reproducibility_hash: str  # SHA-256 of inputs
```

**PIT Regression Test Plan:**
- Replay factor computation at T using data snapshot at T vs T-1
- Assert outputs differ (proving PIT correctness)
- Run weekly as part of CI to catch regressions

**PIT/Versioning Contract Tests:**
```python
# tests/integration/test_pit_contracts.py
@pytest.mark.contract
def test_version_manager_available():
    """Fail fast if DatasetVersionManager is not initialized."""
    manager = get_version_manager()
    assert manager is not None, "DatasetVersionManager not available"

@pytest.mark.contract
def test_snapshot_retrieval():
    """Verify snapshots can be retrieved."""
    manager = get_version_manager()
    snapshots = manager.list_snapshots()
    assert len(snapshots) > 0, "No snapshots available - P4T1 dependency not met"

@pytest.mark.contract
def test_query_as_of_works():
    """Verify time-travel queries work."""
    manager = get_version_manager()
    path, manifest = manager.query_as_of("crsp", date.today() - timedelta(days=1))
    assert path.exists(), "Snapshot data path does not exist"

@pytest.mark.contract
def test_stale_data_detection():
    """Verify stale data triggers appropriate response."""
    manager = get_version_manager()
    # Get snapshot older than 30 days
    old_snapshot = manager.query_as_of("crsp", date.today() - timedelta(days=45))
    # In STRICT_VERSION_MODE, this should raise or warn
    assert old_snapshot[1].created_at > datetime.now(UTC) - timedelta(days=30)
```

**Fail-Fast Gates (CI Pre-Flight):**
```yaml
# .github/workflows/p4t2-tests.yml
jobs:
  pit-preflight:
    runs-on: ubuntu-latest
    steps:
      - name: Verify P4T1 manifests exist
        run: |
          if [ ! -f "data/manifests/crsp.json" ]; then
            echo "::error::CRSP manifest missing - P4T1 dependency not met"
            exit 1
          fi
          if [ ! -f "data/manifests/compustat.json" ]; then
            echo "::error::Compustat manifest missing - P4T1 dependency not met"
            exit 1
          fi
      - name: Verify DatasetVersionManager loads
        run: |
          python -c "from libs.data_quality.versioning import DatasetVersionManager; print('OK')"
      - name: Check snapshot availability
        run: |
          python -c "
          from libs.data_quality.versioning import DatasetVersionManager
          from libs.data_quality.manifest import ManifestManager
          mm = ManifestManager()
          vm = DatasetVersionManager(mm)
          snaps = vm.list_snapshots()
          if not snaps:
              print('::warning::No snapshots found - PIT tests may fail')
          else:
              print(f'Found {len(snaps)} snapshots')
          "
```

**Pipeline Blocking on PIT Failure:**
- If `pit-preflight` fails: block all P4T2 jobs, emit alert
- If snapshot retrieval fails during job: mark job as skipped with `PIT_UNAVAILABLE` status
- Nightly jobs continue with warning if snapshots are stale (>24h)
- Production pipelines BLOCK on any PIT failure (STRICT_VERSION_MODE=true)

**P4T1 Manifest Fallback (CI/Nightly Jobs):**
```python
# libs/data_quality/pit_guard.py
class PITGuard:
    """Guard for PIT-dependent operations with fallback handling."""

    def __init__(self, version_manager: DatasetVersionManager, strict: bool = True):
        self.vm = version_manager
        self.strict = strict  # True in production, False in CI

    def require_snapshot(self, dataset: str, as_of: date) -> tuple[Path, SnapshotManifest]:
        """
        Get snapshot with fallback handling.

        In strict mode: raises PITUnavailableError
        In non-strict mode: logs warning, returns None to skip
        """
        try:
            return self.vm.query_as_of(dataset, as_of)
        except (SnapshotNotFoundError, DataNotFoundError) as e:
            if self.strict:
                raise PITUnavailableError(f"P4T1 dependency not met: {e}") from e
            logger.warning("PIT unavailable, skipping: %s", e)
            return None, None

    def check_manifests_exist(self) -> dict[str, bool]:
        """Check which P4T1 manifests are available."""
        required = ["crsp", "compustat", "fama_french"]
        results = {}
        for ds in required:
            manifest_path = Path(f"data/manifests/{ds}.json")
            results[ds] = manifest_path.exists()
        return results
```

**CI Job Skip Semantics:**
```yaml
# When P4T1 manifests missing, jobs use this pattern
- name: Check PIT availability
  id: pit-check
  run: |
    RESULT=$(python -c "from libs.data_quality.pit_guard import PITGuard; print(PITGuard.check_manifests_exist())")
    if echo "$RESULT" | grep -q "False"; then
      echo "pit_available=false" >> $GITHUB_OUTPUT
      echo "::warning::P4T1 manifests incomplete - PIT-dependent tests will skip"
    else
      echo "pit_available=true" >> $GITHUB_OUTPUT
    fi

- name: Run factor tests
  if: steps.pit-check.outputs.pit_available == 'true'
  run: pytest tests/libs/factors/ -v

- name: Skip notice
  if: steps.pit-check.outputs.pit_available == 'false'
  run: echo "::notice::Factor tests SKIPPED - P4T1 manifests unavailable"
```

**Alert on Missing Manifests:**
- Metric: `pit.manifest.missing{dataset="crsp"}` - emitted when check fails
- Alert: PagerDuty if missing in production, Slack if missing in CI
- Runbook: Verify P4T1 sync completed successfully, re-run sync if needed

**Snapshot-Based Computation Pattern (ALL pipelines must use):**
```python
# Required pattern for all P4T2 compute paths
# 1. Create or retrieve a named snapshot
version_manager = DatasetVersionManager(manifest_manager)
snapshot = version_manager.get_snapshot(snapshot_version_tag)
if snapshot is None:
    snapshot = version_manager.create_snapshot(snapshot_version_tag, datasets=["crsp", "compustat"])

# 2. Get data paths from snapshot (immutable)
data_path, manifest = version_manager.query_as_of("crsp", as_of_date)

# 3. Compute using snapshotted data
result = FactorBuilder(crsp_path=data_path).compute_factor(...)
result.metadata.dataset_version_ids = {
    ds: snapshot.datasets[ds].sync_manifest_version
    for ds in snapshot.datasets
}

# 4. Link backtest for reproducibility (optional but recommended)
version_manager.link_backtest(backtest_id, snapshot_version_tag)
```

**Version Drift Handling Policy:**
- `check_compatibility()` returns `(compatible: bool, drift_warning: str | None)`
- If drift detected AND `STRICT_VERSION_MODE=true`: **block** operation, raise `VersionDriftError`
- If drift detected AND `STRICT_VERSION_MODE=false`: **warn** via logger + metric, continue
- Default: `STRICT_VERSION_MODE=true` in production, `false` in development
- Circuit breaker triggers after 3 consecutive drift blocks

---

## Qlib Integration Strategy

> **Reference:** See [docs/CONCEPTS/qlib-comparison.md](../CONCEPTS/qlib-comparison.md) for comprehensive analysis of Qlib vs our platform.

**Philosophy:** Leverage Qlib's optimized components where beneficial while maintaining platform independence and PIT correctness.

**Key Findings from Analysis:**
- **ADOPT:** Alpha Metrics (IC/ICIR/Rank IC), DiskExpressionCache, Config Hash
- **KEEP OURS:** PIT/Versioning, Atomic Writes, Polars/DuckDB for TAQ, Barra Risk Model
- **DEFER:** FormulaicFactor (Phase 3)

### Integration Boundaries

| Component | Approach | Rationale |
|-----------|----------|-----------|
| **Factor Definitions** | Static Python classes (current) | PIT-safe, explicit control |
| **FormulaicFactor** | **Phase 3** - Qlib expression adapter | Enables rapid research iteration |
| **Alpha Metrics** | Wrap `qlib.contrib.evaluate` with fallback | Battle-tested IC/ICIR, grouped analysis |
| **TAQ/Microstructure** | Polars/DuckDB only, export daily aggregates | Avoid serialization overhead |
| **Model Training** | Optional Qlib DumpData cache | Performance for heavy workloads |

### FormulaicFactor (Phase 3 Enhancement)

**Deferred to Phase 3** to avoid disrupting current T2.1 implementation. Placeholder `feature_formulas` field added to T2.8 Model Registry.

```python
# libs/factors/formulaic_factor.py (PHASE 3)
class FormulaicFactor(FactorDefinition):
    """Adapter for Qlib expression DSL."""

    def __init__(self, name: str, formula: str):
        self._name = name
        self._formula = formula  # e.g., "Mean($close, 5) / $close"

    def compute(self, prices, fundamentals, as_of_date) -> pl.DataFrame:
        # CRITICAL: Must receive pre-fetched DataFrames from DatasetVersionManager
        # NEVER use Qlib auto-loader (look-ahead risk)
        return qlib_expression_engine.evaluate(self._formula, data)
```

**PIT Safeguards Required (Phase 3):**
- Expression allowlist (no future-looking refs like `Ref($close, -1)`)
- Pre-fetched data from DatasetVersionManager only
- Sandbox validation before production use
- `reproducibility_hash` in outputs

### Polars/DuckDB Boundary (Architectural Constraint)

**Decision:** All high-frequency TAQ feature generation (RV, VPIN, spread, depth) happens in **Polars/DuckDB only**. Qlib receives **daily aggregates** via Parquet.

```
TAQ Tick Data
     │
     ▼
┌─────────────────────────────────┐
│  Polars/DuckDB Processing       │
│  (RV, VPIN, spread, depth)      │
└─────────────────────────────────┘
     │
     ▼ Export daily aggregates
┌─────────────────────────────────┐
│  data/analytics/microstructure/ │
│  ├── rv_daily.parquet           │
│  ├── rv_30min.parquet (future)  │
│  └── vpin_daily.parquet         │
└─────────────────────────────────┘
     │
     ▼ Ingest as features
┌─────────────────────────────────┐
│  Qlib Models (daily features)   │
└─────────────────────────────────┘
```

**Rationale:**
- Avoids Pandas/Polars ↔ Qlib serialization overhead
- TAQ data volume too large for Qlib's standard interfaces
- Daily aggregates sufficient for most factor models
- Intraday horizons (5m, 30m) available for execution analytics

### DiskExpressionCache (T2.8 Enhancement)

**Adopted from Qlib:** Qlib's `DiskExpressionCache` pattern caches computed features to disk to avoid redundant computation. We adapt this for T2.8 Model Registry.

```python
# libs/data_quality/cache.py (T2.8 deliverable)
class DiskExpressionCache:
    """Cache computed factors/features to disk for reuse.

    Key format: "{factor_name}:{as_of_date}:{dataset_version_id}:{snapshot_id}:{config_hash}"
    Example: "momentum_12_1:2024-01-15:crsp_v1.2.3:snap_20241215:a1b2c3d4"

    CRITICAL: All components MUST be included to ensure PIT safety:
    - snapshot_id: Prevents stale data from wrong time-travel context
    - config_hash: Prevents stale data from changed hyperparameters
    Cache miss occurs if any component differs.
    """

    def __init__(self, cache_dir: Path, ttl_days: int = 7):
        self.cache_dir = cache_dir
        self.ttl_days = ttl_days

    def get_or_compute(
        self,
        factor_name: str,
        as_of_date: date,
        snapshot_id: str,  # REQUIRED: DatasetVersionManager snapshot
        version_ids: dict[str, str],  # PIT tracking
        config_hash: str,  # REQUIRED: SHA-256 of computation config
        compute_fn: Callable[[], pl.DataFrame],
    ) -> tuple[pl.DataFrame, bool]:  # (data, was_cached)
        """Return cached result or compute and cache."""
        key = self._build_key(factor_name, as_of_date, version_ids, snapshot_id, config_hash)
        cache_path = self._key_to_path(key)
        if cache_path.exists() and not self._is_stale(cache_path):
            return pl.read_parquet(cache_path), True
        result = compute_fn()
        self._write_with_metadata(cache_path, result, version_ids, snapshot_id, config_hash)
        return result, False

    def invalidate_by_snapshot(self, snapshot_id: str) -> int:
        """Invalidate all cache entries for a specific snapshot."""
        ...

    def invalidate_by_dataset_update(self, dataset: str, new_version: str) -> int:
        """Hook called when DatasetVersionManager detects version update."""
        ...

    def invalidate_by_config_change(self, factor_name: str) -> int:
        """Invalidate cache when factor config changes."""
        ...
```

**Use Cases:**
- Factor exposures that don't change daily
- Risk model outputs for stable universes
- Alpha signal caches during research

### Qlib Optional Dependencies Handling (from Codex review)

**Problem:** Qlib should be an optional dependency - production services (execution_gateway, signal_service) should NOT require Qlib installation.

**Installation Strategy:**
```bash
# Base install (no Qlib)
pip install .

# Research install (with Qlib)
pip install .[qlib]  # or poetry install --with qlib
```

**pyproject.toml Configuration:**
```toml
[project.optional-dependencies]
qlib = ["qlib>=0.9.0"]

# Or with Poetry:
[tool.poetry.group.qlib]
optional = true

[tool.poetry.group.qlib.dependencies]
qlib = "^0.9.0"
```

**Runtime Handling:**
```python
# libs/alpha/metrics.py
def _qlib_available() -> bool:
    """Check if Qlib is installed."""
    try:
        import qlib.contrib.evaluate
        return True
    except ImportError:
        return False

QLIB_INSTALLED = _qlib_available()

class AlphaMetricsAdapter:
    def __init__(self, prefer_qlib: bool = True):
        self._use_qlib = prefer_qlib and QLIB_INSTALLED
        if prefer_qlib and not QLIB_INSTALLED:
            logger.info("Qlib not installed, using local metrics implementation")
```

**Test Handling:**
```python
# tests/libs/alpha/test_metrics_contract.py
import pytest

qlib = pytest.importorskip("qlib", reason="Qlib not installed")

@pytest.mark.parametrize("backend", ["qlib", "polars"])
def test_ic_parity(backend):
    """Test IC calculation parity between backends."""
    if backend == "qlib":
        pytest.importorskip("qlib")
    ...
```

**CI Configuration:**
```yaml
# .github/workflows/test.yml
jobs:
  test-base:
    # Tests without Qlib (production-like)
    steps:
      - run: pip install .
      - run: pytest tests/ -m "not requires_qlib"

  test-qlib:
    # Tests with Qlib (research-like)
    steps:
      - run: pip install .[qlib]
      - run: pytest tests/ -m "qlib_contract"
```

**Acceptance Criteria (Cross-cutting):**
- [ ] Base install works without Qlib (`pip install .` succeeds)
- [ ] Qlib install via extra (`pip install .[qlib]` succeeds)
- [ ] All adapters gracefully handle `ImportError`
- [ ] Contract tests skip with clear message when Qlib unavailable
- [ ] CI runs both base and Qlib test suites
- [ ] Production services never import Qlib directly

---

## Architecture Overview

### Module Structure

```
libs/
├── data_providers/           # EXISTING from P4T1
│   ├── taq_storage.py        # T1.7: TAQ data storage
│   └── taq_query_provider.py # T1.8: TAQ query interface
│
├── factors/                  # NEW: Factor construction and analytics
│   ├── __init__.py
│   ├── factor_builder.py     # T2.1: Cross-sectional factor computation
│   ├── factor_definitions.py # T2.1: Canonical factor definitions
│   └── factor_analytics.py   # T2.1: IC analysis, decay, neutralization
│
├── risk/                     # NEW: Quantitative risk analytics
│   ├── __init__.py
│   ├── factor_covariance.py  # T2.2: Factor return & covariance estimation
│   ├── specific_risk.py      # T2.2: Idiosyncratic variance estimation
│   ├── barra_model.py        # T2.3: Barra-style risk decomposition
│   ├── risk_decomposition.py # T2.3: Portfolio risk attribution
│   ├── portfolio_optimizer.py # T2.4: Mean-variance optimization
│   └── stress_testing.py     # T2.4: Historical & hypothetical scenarios
│
├── alpha/                    # NEW: Alpha research platform
│   ├── __init__.py
│   ├── research_platform.py  # T2.5: PIT backtesting engine
│   ├── alpha_library.py      # T2.5: Canonical alpha definitions
│   ├── alpha_combiner.py     # T2.6: Composite signal construction
│   └── overfitting_detection.py # T2.6: OOS testing, FDR correction
│
├── analytics/                # NEW: Cross-cutting analytics
│   ├── __init__.py
│   ├── attribution.py        # T2.7: Factor attribution for strategy returns
│   ├── microstructure.py     # T3.1: Realized volatility, VPIN
│   ├── volatility.py         # T3.1: HAR volatility forecasting
│   ├── execution_quality.py  # T3.2: VWAP, TWAP, implementation shortfall
│   └── event_study.py        # T3.3: CAR, PEAD analysis
│
└── models/                   # NEW: Model registry for production deployment
    ├── __init__.py
    ├── registry.py           # T2.8: Versioned model storage
    └── serialization.py      # T2.8: Pickle/joblib with metadata
```

### Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         P4T2 ANALYTICS DATA FLOW                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    P4T1 DATA PROVIDERS (COMPLETE)                    │   │
│  │  CRSP • Compustat • Fama-French • yfinance • Unified Fetcher        │   │
│  └───────────────────────────────────┬──────────────────────────────────┘   │
│                                      │                                       │
│         ┌────────────────────────────┼────────────────────────────┐         │
│         │                            │                            │         │
│         ▼                            ▼                            ▼         │
│  ┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐  │
│  │ TRACK 1 TAQ     │    │ TRACK 2 FACTOR/RISK │    │ TRACK 3 MICROSTRUC │  │
│  │                 │    │                     │    │                     │  │
│  │ T1.7 Storage    │    │ T2.1 Factor Builder │    │ T3.1 Microstructure│  │
│  │ T1.8 Query      │    │ T2.2 Covariance     │    │ T3.2 Exec Quality  │  │
│  │                 │    │ T2.3 Risk Analytics │    │ T3.3 Event Study   │  │
│  │                 │    │ T2.4 Optimizer      │    │                     │  │
│  │                 │    │ T2.5 Alpha Platform │    │                     │  │
│  │                 │    │ T2.6 Alpha Advanced │    │                     │  │
│  │                 │    │ T2.7 Attribution    │    │                     │  │
│  │                 │    │ T2.8 Model Registry │    │                     │  │
│  └────────┬────────┘    └──────────┬──────────┘    └──────────┬──────────┘  │
│           │                        │                          │              │
│           └────────────────────────┼──────────────────────────┘              │
│                                    ▼                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                       PRODUCTION INTEGRATION                          │   │
│  │  Model Registry → signal_service • risk_manager • strategies         │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Output Storage Schemas

All P4T2 outputs are stored in DuckDB/Parquet with consistent versioning metadata.

**Factor Exposures (T2.1):**
```sql
-- data/analytics/factor_exposures.parquet (partitioned by date)
CREATE TABLE factor_exposures (
    date DATE NOT NULL,
    permno INTEGER NOT NULL,
    factor_name VARCHAR NOT NULL,        -- 'value', 'momentum', 'quality', 'size', 'low_vol'
    raw_value DOUBLE,                    -- Raw factor value
    zscore DOUBLE,                       -- Cross-sectional z-score
    percentile DOUBLE,                   -- Cross-sectional percentile [0,1]
    dataset_version_id VARCHAR NOT NULL, -- e.g., 'crsp_v1.2.3'
    computation_timestamp TIMESTAMP NOT NULL,
    PRIMARY KEY (date, permno, factor_name)
);
```

**Factor Returns (T2.2):**
```sql
-- data/analytics/factor_returns.parquet
CREATE TABLE factor_returns (
    date DATE NOT NULL,
    factor_name VARCHAR NOT NULL,
    daily_return DOUBLE NOT NULL,        -- Daily factor return from cross-sectional regression
    t_statistic DOUBLE,                  -- T-stat of regression coefficient
    r_squared DOUBLE,                    -- Cross-sectional R² for the day
    dataset_version_id VARCHAR NOT NULL,
    PRIMARY KEY (date, factor_name)
);
```

**Covariance Matrix (T2.2):**
```sql
-- data/analytics/covariance_matrices.parquet
CREATE TABLE covariance_matrices (
    as_of_date DATE NOT NULL,
    factor_i VARCHAR NOT NULL,
    factor_j VARCHAR NOT NULL,
    covariance DOUBLE NOT NULL,          -- Pairwise covariance
    correlation DOUBLE NOT NULL,         -- Pairwise correlation
    halflife_days INTEGER NOT NULL,      -- Decay parameter used
    shrinkage_intensity DOUBLE,          -- Ledoit-Wolf shrinkage applied
    dataset_version_id VARCHAR NOT NULL,
    PRIMARY KEY (as_of_date, factor_i, factor_j)
);
```

**Specific Risk (T2.2):**
```sql
-- data/analytics/specific_risk.parquet
CREATE TABLE specific_risk (
    as_of_date DATE NOT NULL,
    permno INTEGER NOT NULL,
    specific_variance DOUBLE NOT NULL,   -- Idiosyncratic variance
    specific_vol DOUBLE NOT NULL,        -- Annualized specific volatility
    dataset_version_id VARCHAR NOT NULL,
    PRIMARY KEY (as_of_date, permno)
);
```

**Risk Decomposition Outputs (T2.3):**
```sql
-- data/analytics/portfolio_risk.parquet
CREATE TABLE portfolio_risk (
    analysis_id VARCHAR NOT NULL,        -- UUID
    portfolio_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    total_risk DOUBLE NOT NULL,          -- Annualized portfolio volatility
    factor_risk DOUBLE NOT NULL,         -- Systematic risk contribution
    specific_risk DOUBLE NOT NULL,       -- Idiosyncratic risk contribution
    var_95 DOUBLE NOT NULL,              -- 95% VaR (daily)
    var_99 DOUBLE,                       -- 99% VaR (daily)
    cvar_95 DOUBLE NOT NULL,             -- Expected shortfall
    model_version VARCHAR NOT NULL,
    dataset_version_id VARCHAR NOT NULL,
    computation_timestamp TIMESTAMP NOT NULL,
    PRIMARY KEY (analysis_id)
);

-- data/analytics/factor_contributions.parquet
CREATE TABLE factor_contributions (
    analysis_id VARCHAR NOT NULL,        -- Links to portfolio_risk
    factor_name VARCHAR NOT NULL,
    marginal_contribution DOUBLE NOT NULL,  -- MCTR
    component_contribution DOUBLE NOT NULL, -- CCTR
    percent_contribution DOUBLE NOT NULL,   -- % of total risk
    PRIMARY KEY (analysis_id, factor_name)
);
```

**Optimizer Outputs (T2.4):**
```sql
-- data/analytics/optimizer_solutions.parquet
CREATE TABLE optimizer_solutions (
    solution_id VARCHAR NOT NULL,        -- UUID
    as_of_date DATE NOT NULL,
    objective VARCHAR NOT NULL,          -- 'min_variance', 'max_sharpe', 'risk_parity'
    status VARCHAR NOT NULL,             -- 'optimal', 'suboptimal', 'infeasible'
    expected_return DOUBLE,
    expected_risk DOUBLE NOT NULL,
    sharpe_ratio DOUBLE,
    turnover DOUBLE NOT NULL,            -- From previous portfolio
    transaction_cost DOUBLE NOT NULL,
    solver_time_ms INTEGER NOT NULL,
    model_version VARCHAR NOT NULL,
    dataset_version_id VARCHAR NOT NULL,
    PRIMARY KEY (solution_id)
);

-- data/analytics/optimal_weights.parquet
CREATE TABLE optimal_weights (
    solution_id VARCHAR NOT NULL,        -- Links to optimizer_solutions
    symbol VARCHAR NOT NULL,
    weight DOUBLE NOT NULL,              -- Target weight
    delta_weight DOUBLE NOT NULL,        -- Change from current
    PRIMARY KEY (solution_id, symbol)
);

-- data/analytics/stress_test_results.parquet
CREATE TABLE stress_test_results (
    test_id VARCHAR NOT NULL,            -- UUID
    portfolio_id VARCHAR NOT NULL,
    scenario_name VARCHAR NOT NULL,      -- 'GFC_2008', 'COVID_2020', 'rate_shock'
    scenario_type VARCHAR NOT NULL,      -- 'historical', 'hypothetical'
    as_of_date DATE NOT NULL,
    portfolio_pnl DOUBLE NOT NULL,       -- P&L under scenario
    portfolio_drawdown DOUBLE NOT NULL,
    factor_impacts JSON NOT NULL,        -- {"value": -0.05, "momentum": 0.02, ...}
    worst_position VARCHAR,              -- Symbol with worst loss
    worst_position_loss DOUBLE,
    model_version VARCHAR NOT NULL,
    PRIMARY KEY (test_id)
);
```

**Risk/Optimizer Success Metrics:**
- VaR exceedance rate within 5% of target (e.g., 95% VaR exceeded ~5% of days)
- Optimizer solves in <5s for 500 stocks
- Stress test P&L matches historical replay within 1%

**Alpha Backtest Results (T2.5):**
```sql
-- data/analytics/alpha_backtests.parquet
CREATE TABLE alpha_backtests (
    backtest_id VARCHAR NOT NULL,        -- UUID
    alpha_name VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    ic DOUBLE,                           -- Information coefficient
    icir DOUBLE,                         -- IC information ratio (rolling)
    turnover DOUBLE,                     -- Signal turnover
    quintile_spread DOUBLE,              -- Q5 - Q1 return spread
    dataset_version_ids JSON NOT NULL,   -- {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}
    computation_timestamp TIMESTAMP NOT NULL,
    PRIMARY KEY (backtest_id, as_of_date)
);
```

**Microstructure Outputs (T3.1):**
```sql
-- data/analytics/realized_volatility.parquet
CREATE TABLE realized_volatility (
    date DATE NOT NULL,
    symbol VARCHAR NOT NULL,
    rv_5min DOUBLE NOT NULL,             -- 5-minute sampled RV
    rv_10min DOUBLE,                     -- 10-minute sampled RV
    rv_close_to_close DOUBLE,            -- Close-to-close RV
    vpin DOUBLE,                         -- Volume-synchronized PIN
    dataset_version_id VARCHAR NOT NULL, -- TAQ version
    PRIMARY KEY (date, symbol)
);
```

**Execution Quality Outputs (T3.2):**
```sql
-- data/analytics/execution_analysis.parquet
CREATE TABLE execution_analysis (
    analysis_id VARCHAR NOT NULL,        -- UUID
    symbol VARCHAR NOT NULL,
    execution_date DATE NOT NULL,
    side VARCHAR NOT NULL,               -- 'buy' or 'sell'
    total_shares INTEGER NOT NULL,
    total_notional DOUBLE NOT NULL,
    arrival_price DOUBLE NOT NULL,       -- Price at decision time
    execution_price DOUBLE NOT NULL,     -- Volume-weighted avg fill price
    vwap_benchmark DOUBLE NOT NULL,      -- Market VWAP over execution window
    twap_benchmark DOUBLE NOT NULL,      -- Market TWAP over execution window
    implementation_shortfall_bps DOUBLE NOT NULL,  -- (exec - arrival) / arrival * 10000
    vwap_slippage_bps DOUBLE NOT NULL,   -- (exec - vwap) / vwap * 10000
    market_impact_bps DOUBLE,            -- Estimated permanent impact
    timing_cost_bps DOUBLE,              -- Delay cost
    dataset_version_id VARCHAR NOT NULL, -- TAQ version
    PRIMARY KEY (analysis_id)
);
```

**Execution Quality Success Metrics:**
- Implementation shortfall within ±10 bps of manual calculation
- VWAP benchmark matches TAQ-derived VWAP within ±1 bps
- Analysis latency <5s for single-day execution

**Event Study Outputs (T3.3):**
```sql
-- data/analytics/event_studies.parquet
CREATE TABLE event_studies (
    study_id VARCHAR NOT NULL,           -- UUID
    event_type VARCHAR NOT NULL,         -- 'earnings', 'index_rebalance', 'custom'
    symbol VARCHAR NOT NULL,
    event_date DATE NOT NULL,
    pre_window_days INTEGER NOT NULL,    -- Days before event in window
    post_window_days INTEGER NOT NULL,   -- Days after event in window
    estimation_window_days INTEGER NOT NULL,  -- Days for market model
    car_pre DOUBLE NOT NULL,             -- CAR before event
    car_post DOUBLE NOT NULL,            -- CAR after event
    car_window DOUBLE NOT NULL,          -- CAR over full window
    abnormal_volume DOUBLE,              -- Abnormal volume ratio
    t_statistic DOUBLE NOT NULL,         -- T-stat for significance
    p_value DOUBLE NOT NULL,
    is_significant BOOLEAN NOT NULL,     -- p < 0.05
    dataset_version_id VARCHAR NOT NULL, -- CRSP version
    PRIMARY KEY (study_id)
);

-- data/analytics/pead_analysis.parquet
CREATE TABLE pead_analysis (
    analysis_id VARCHAR NOT NULL,
    event_date DATE NOT NULL,
    surprise_quintile INTEGER NOT NULL,  -- 1-5 (1=worst, 5=best)
    n_events INTEGER NOT NULL,           -- Number of events in quintile
    car_60d DOUBLE NOT NULL,             -- 60-day CAR
    t_statistic DOUBLE NOT NULL,
    dataset_version_id VARCHAR NOT NULL,
    PRIMARY KEY (analysis_id, surprise_quintile)
);
```

**Event Study Success Metrics:**
- CAR calculation matches academic literature methodology (market model)
- T-statistics computed with Newey-West standard errors
- PEAD drift magnitude within 1σ of published empirical results

**Analytics Output DR/Backup:**
```
data/analytics/                         # All P4T2 analytics outputs
├── factor_exposures.parquet
├── factor_returns.parquet
├── covariance_matrices.parquet
├── specific_risk.parquet
├── alpha_backtests.parquet
├── realized_volatility.parquet
├── execution_analysis.parquet
└── event_studies.parquet
```

- **Backup location:** `data/backups/analytics/` (daily)
- **Off-site:** S3/GCS sync via rclone (same config as TAQ/Model Registry)
- **Backup schedule:** Daily at 04:00 UTC (after nightly computations complete)
- **RPO (Recovery Point Objective):** 24 hours
- **RTO (Recovery Time Objective):** 2 hours (fast restore - smaller than TAQ)
- **Retention:** 90 days rolling for backups, permanent for production outputs
- **Checksum validation:** SHA-256 per file, stored in `analytics_manifest.json`

**Restore procedure:**
```bash
# List available backups
ls -la data/backups/analytics/

# Restore from backup
rsync -av data/backups/analytics/2024-12-06/ data/analytics/

# Verify checksums
python scripts/verify_analytics.py --date 2024-12-06

# Or restore from off-site
rclone sync remote:backups/analytics/2024-12-06/ data/analytics/
```

**Post-restore validation:**
- Checksum match for all parquet files
- Row count matches manifest
- Date range coverage verified
- PIT regression test passes

---

## Track 1: TAQ Data Completion (T1.7-T1.8)

### T1.7: TAQ Data Storage & Sync
**Effort:** 3-4 days | **PR:** `feat(p4): taq storage`
**Status:** ✅ Complete
**Priority:** P1 (Enables microstructure analytics)
**Dependencies:** T1.2 (WRDS Sync Manager) - COMPLETE from P4T1

**Problem:** High-frequency tick data from WRDS TAQ requires specialized tiered storage due to volume.

**Deliverables:**
- Tiered TAQ data storage architecture
- Pre-computed aggregates download from WRDS (Tier 1: 1-min OHLCV, daily RV)
- Sample dataset management (Tier 3: representative days)
- Storage schema with time-based partitioning
- Compression and retention policies

**Clarification on Aggregates:**
- **Download, NOT compute locally:** WRDS provides pre-computed 1-min bars and daily stats
- This is a **download job**, not a local tick aggregation
- Effort estimate assumes downloading existing aggregates, not processing raw ticks
- Local tick processing (if ever needed) would be a separate task

**Storage Schema:**
```
data/wrds/taq/            # Consistent with P4T1 data warehouse structure
├── aggregates/           # Tier 1: Pre-computed, always local
│   ├── 1min_bars/
│   │   ├── 2024/
│   │   │   ├── 01/
│   │   │   │   ├── AAPL.parquet
│   │   │   │   └── ...
│   │   └── ...
│   ├── daily_rv/         # Daily realized volatility
│   │   └── all_stocks.parquet
│   └── spread_stats/     # Intraday spread/volume stats
│       └── all_stocks.parquet
│
├── samples/              # Tier 3: Full tick for sample days
│   ├── 2024-01-15/       # Representative normal day
│   │   ├── AAPL.parquet
│   │   └── ...
│   ├── 2024-03-15/       # High volatility day
│   └── ...
│
└── manifest.json         # Storage manifest with sync status
```

**TAQ Manifest Schema:**
```json
{
  "dataset": "taq",
  "dataset_version_id": "taq_v1.0.0",
  "wrds_version_id": "wrds_taq_2024q4",
  "sync_date": "2024-12-07T02:00:00Z",
  "sync_type": "incremental",
  "tiers": {
    "aggregates": {
      "symbols": ["AAPL", "MSFT", "..."],
      "date_range": ["2024-01-01", "2024-12-06"],
      "checksum_sha256": "abc123...",
      "size_bytes": 5368709120
    },
    "samples": {
      "dates": ["2024-01-15", "2024-03-15", "2024-08-05"],
      "symbols_per_date": 500,
      "checksum_sha256": "def456..."
    }
  },
  "last_verified": "2024-12-07T03:00:00Z"
}
```

**TAQ DR/Backup:**
- **Backup:** Daily rsync to `data/backups/taq/` at 03:00 UTC
- **Off-site:** Optional S3/GCS sync via rclone (same config as Model Registry)
- **RPO:** 24 hours (daily backup)
- **RTO:** 4 hours (full restore from backup or WRDS re-sync)
- **Restore command:** `scripts/taq_sync.py restore --from-backup <date>`
- **Verification:** Post-restore checksum validation against manifest

**Implementation:**

```python
# libs/data_providers/taq_storage.py
from dataclasses import dataclass
from enum import Enum

class TAQTier(Enum):
    """TAQ data storage tiers."""
    AGGREGATES = "aggregates"  # Tier 1: Pre-computed, local
    ON_DEMAND = "on_demand"    # Tier 2: WRDS direct query (no storage)
    SAMPLES = "samples"        # Tier 3: Full tick for sample days

@dataclass
class TAQStorageConfig:
    """Configuration for TAQ storage."""
    storage_path: Path
    aggregate_symbols: list[str]  # Symbols for Tier 1
    sample_dates: list[date]      # Dates for Tier 3
    retention_days: int = 365     # Keep 1 year of aggregates
    compression: str = "zstd"     # Parquet compression

class TAQStorage:
    """
    Tiered storage for WRDS TAQ tick data.

    Tier 1: Pre-computed aggregates (local, ~100GB for 500 stocks, 5 years)
    Tier 2: On-demand WRDS queries (no local storage)
    Tier 3: Full tick samples for algorithm development (~5-10GB per day)
    """

    def __init__(
        self,
        config: TAQStorageConfig,
        wrds_client: WRDSClient,
        lock_manager: LockManager
    ):
        self.config = config
        self.wrds = wrds_client
        self.lock = lock_manager

    def sync_aggregates(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date
    ) -> None:
        """
        Sync pre-computed aggregates from WRDS.

        Computes 1-minute OHLCV bars, daily realized volatility,
        and spread statistics.
        """
        ...

    def sync_sample_day(
        self,
        sample_date: date,
        symbols: list[str]
    ) -> None:
        """
        Download full tick data for a sample day.

        Used for algorithm development and backtesting.
        """
        ...

    def get_1min_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> pl.DataFrame:
        """
        Get 1-minute OHLCV bars from local storage.

        Returns DataFrame: [timestamp, open, high, low, close, volume, vwap]
        """
        ...

    def get_daily_realized_vol(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> pl.DataFrame:
        """
        Get daily realized volatility from local storage.

        Returns DataFrame: [date, rv_5min, rv_10min, close_to_close]
        """
        ...
```

**WRDS Aggregates Fallback Plan:**
- **Primary:** Download pre-computed aggregates from WRDS (1-min bars, daily RV)
- **Fallback (if WRDS aggregates unavailable):** On-demand query for top 50 symbols only
  - Estimated cost: ~2GB/day download, ~$50 WRDS credits
  - Latency: 5-10 min per symbol-day (rate limited)
- **Go/No-Go Checkpoint:** Before T1.7 starts, verify WRDS API supports aggregate download
- **Escalation:** If aggregates unavailable, escalate to project lead for scope adjustment

**Gating Dependencies:**
- WRDS credentials configured and tested
- WRDS aggregate API availability confirmed
- Network access to WRDS from execution environment

**Files to Create:**
- `libs/data_providers/taq_storage.py`
- `scripts/taq_sync.py` (CLI)
- `tests/libs/data_providers/test_taq_storage.py`
- `docs/CONCEPTS/taq-data.md`
- `docs/RUNBOOKS/taq-storage.md`

**PIT/Versioning Requirements:**
- TAQ manifest MUST include `dataset_version_id` linking to WRDS sync snapshot
- Aggregates stored with `sync_date` and `wrds_version_id` metadata
- Sample days tagged with exact WRDS query timestamp for reproducibility

**Acceptance Criteria:**
- [ ] Tiered storage with 3 tiers implemented
- [ ] 1-minute bar download from WRDS pre-computed aggregates
- [ ] Daily realized volatility download
- [ ] Sample day download capability
- [ ] Storage manifest tracking sync status with dataset_version_id
- [ ] Uses same locking pattern as P4T1 providers (LockManager)
- [ ] **Performance test**: Download 1 month of aggregates for 500 stocks (tiered SLAs below)
  - **Tier 1 (Baseline)**: <60 minutes on 4-core/16GB with 10 Mbps WRDS connection
  - **Tier 2 (Target)**: <30 minutes on 8-core/32GB with 50 Mbps WRDS connection
  - **Fallback**: If WRDS rate-limited, reduce to top 100 symbols + retry with exponential backoff
  - **Hardware assumptions**: Requires SSD storage, minimum 50GB free disk
- [ ] **Lock contention test**: Simulated dual writers correctly blocked
- [ ] >90% test coverage

---

### T1.8: TAQ Query Interface
**Effort:** 2-3 days | **PR:** `feat(p4): taq query interface`
**Status:** ✅ Complete
**Priority:** P1 (Enables on-demand analysis)
**Dependencies:** T1.7 (TAQ Storage)

**Problem:** Need unified interface for both local TAQ storage and on-demand WRDS queries.

**Deliverables:**
- On-demand query interface for WRDS TAQ (Tier 2)
- Local query provider for stored data (Tier 1, 3)
- Unified API for both local and remote queries
- Query cost estimation and limits
- Rate limiting for WRDS queries

**Implementation:**

```python
# libs/data_providers/taq_query_provider.py
@dataclass
class TAQQueryCost:
    """Estimated cost for a TAQ query."""
    estimated_rows: int
    estimated_gb: float
    estimated_time_seconds: int
    requires_approval: bool  # True if exceeds daily limit

class TAQQueryProvider:
    """
    Unified interface for TAQ data queries.

    Automatically routes to local storage or WRDS based on availability.
    """

    def __init__(
        self,
        storage: TAQStorage,
        wrds_client: WRDSClient,
        daily_query_limit_gb: float = 10.0
    ):
        self.storage = storage
        self.wrds = wrds_client
        self.daily_limit = daily_query_limit_gb
        self._daily_usage: float = 0.0

    def get_trades(
        self,
        symbol: str,
        date: date,
        start_time: time | None = None,
        end_time: time | None = None
    ) -> pl.DataFrame:
        """
        Get tick-level trade data.

        Checks local storage first, falls back to WRDS on-demand.
        Returns DataFrame: [timestamp, price, size, exchange, conditions]
        """
        ...

    def get_quotes(
        self,
        symbol: str,
        date: date,
        start_time: time | None = None,
        end_time: time | None = None
    ) -> pl.DataFrame:
        """
        Get tick-level quote data (NBBO).

        Returns DataFrame: [timestamp, bid, ask, bid_size, ask_size]
        """
        ...

    def estimate_query_cost(
        self,
        symbol: str,
        date: date,
        data_type: Literal["trades", "quotes"]
    ) -> TAQQueryCost:
        """
        Estimate cost before executing query.

        Checks against daily limit and returns approval requirement.
        """
        ...

    def get_1min_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> pl.DataFrame:
        """
        Get 1-minute bars (always from local aggregates).
        """
        return self.storage.get_1min_bars(symbol, start_date, end_date)
```

**Endpoint Strategy Decision: CLI-Only**
- TAQ queries are **CLI-only** for Phase 2 (no FastAPI endpoints)
- Rationale: TAQ queries are for research/analysis, not real-time production use
- If production TAQ access needed later, add FastAPI endpoints in future phase
- CLI provides: `taq_query.py trades AAPL 2024-01-15` and `taq_query.py bars AAPL 2024-01-01 2024-01-31`

**Files to Create:**
- `libs/data_providers/taq_query_provider.py`
- `scripts/taq_query.py` (CLI for on-demand queries)
- `tests/libs/data_providers/test_taq_query_provider.py`
- `docs/CONCEPTS/taq-query-interface.md`

**Acceptance Criteria:**
- [ ] Unified interface for local and remote queries
- [ ] Automatic routing based on data availability
- [ ] Query cost estimation before execution
- [ ] Daily usage tracking with configurable limits
- [ ] Rate limiting for WRDS queries
- [ ] **Soak test**: Rate limiter correctly throttles under sustained load
- [ ] **Test**: Daily limit exceeded returns graceful error (not silent fail)
- [ ] >90% test coverage

---

## Track 2: Factor & Risk Analytics (T2.1-T2.8)

### T2.1: Multi-Factor Model Construction
**Effort:** 4-5 days | **PR:** `feat(p4): factor builder`
**Status:** ✅ Complete
**Priority:** P0 (Foundation for all analytics)
**Dependencies:** T1.3 (CRSP), T1.4 (Compustat) - COMPLETE from P4T1

**Problem:** No systematic way to compute standard equity factors (value, momentum, quality, size, low-vol) from local data warehouse.

**Deliverables:**
- Factor definition registry with canonical factor zoo
- Cross-sectional factor computation (z-scores, percentile ranks)
- Sector-neutralization for industry-adjusted factors
- Composite factor construction (equal weight, IC-weighted)
- Factor correlation and turnover analytics

**Implementation:**

```python
# libs/factors/factor_builder.py
from typing import Protocol

class FactorDefinition(Protocol):
    """Protocol for factor computation."""
    name: str
    category: str  # 'value', 'momentum', 'quality', 'size', 'low_vol'

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame,
        as_of_date: date
    ) -> pl.DataFrame:
        """
        Compute factor exposures as of a specific date.

        Returns DataFrame: [permno, date, factor_value, factor_zscore]
        Must be point-in-time correct (no look-ahead bias).
        """
        ...

@dataclass
class FactorConfig:
    """Configuration for factor computation."""
    winsorize_pct: float = 0.01      # Winsorize at 1%/99% percentiles
    neutralize_sector: bool = True   # Sector-neutralize factors
    min_stocks_per_sector: int = 5   # Minimum for neutralization
    lookback_days: int = 252         # For momentum, volatility

class FactorBuilder:
    """
    Build factor exposures from local data warehouse.

    Uses CRSP for prices/returns and Compustat for fundamentals.
    All computations are point-in-time correct.
    """

    def __init__(
        self,
        crsp_provider: CRSPLocalProvider,
        compustat_provider: CompustatLocalProvider,
        config: FactorConfig | None = None
    ):
        self.crsp = crsp_provider
        self.compustat = compustat_provider
        self.config = config or FactorConfig()
        self._registry: dict[str, FactorDefinition] = {}

    def register_factor(self, factor: FactorDefinition) -> None:
        """Register a factor definition."""
        self._registry[factor.name] = factor

    def compute_factor(
        self,
        factor_name: str,
        as_of_date: date,
        universe: list[str] | None = None
    ) -> pl.DataFrame:
        """Compute single factor for given date."""
        ...

    def compute_all_factors(
        self,
        as_of_date: date,
        universe: list[str] | None = None
    ) -> pl.DataFrame:
        """Compute all registered factors for given date."""
        ...

    def compute_composite(
        self,
        factor_names: list[str],
        weights: list[float] | Literal["equal", "ic_weighted"],
        as_of_date: date
    ) -> pl.DataFrame:
        """Compute composite factor from multiple factors."""
        ...
```

**Canonical Factors:**
- **Value**: Book-to-Market (HML), Earnings Yield, Cash Flow Yield
- **Momentum**: 12-1 month return (UMD), 6-month return
- **Quality**: ROE (RMW), Gross Profitability, Accruals
- **Size**: Log Market Cap (SMB)
- **Low-Vol**: Realized Volatility, Beta

**Files to Create:**
- `libs/factors/__init__.py`
- `libs/factors/factor_builder.py`
- `libs/factors/factor_definitions.py`
- `libs/factors/factor_analytics.py`
- `tests/libs/factors/__init__.py`
- `tests/libs/factors/test_factor_builder.py`
- `tests/libs/factors/test_factor_definitions.py`
- `docs/CONCEPTS/factor-investing.md`
- `docs/ADRs/ADR-0020-factor-model-architecture.md`

**PIT/Versioning Requirements (per P4T1 T1.6):**
- All factor computations MUST use `dataset_version_id` from P4T1 versioning
- Factor outputs stored with `as_of_date` and `data_version_id` metadata
- Factor outputs must be reproducible given same version_id
- Validation tests to detect look-ahead bias (compare T vs T-1 data)

**Acceptance Criteria:**
- [ ] 5+ canonical factors implemented (value, momentum, quality, size, low-vol)
- [ ] Cross-sectional z-score computation with winsorization
- [ ] Sector neutralization using Compustat GICS codes (explicit dependency)
- [ ] Point-in-time correctness verified (no look-ahead) with PIT validation tests
- [ ] Factor correlation matrix computation
- [ ] `validate()` method checking for nulls, infs, z-scores within +/- 5 sigma
- [ ] Outputs stored with dataset_version_id metadata for reproducibility
- [ ] >90% test coverage

**Phase 3 Enhancement:** FormulaicFactor adapter for Qlib expression DSL (see [Qlib Integration Strategy](#qlib-integration-strategy)). Current implementation uses static Python classes for PIT safety. Phase 3 will add dynamic formula support with expression allowlist validation.

---

### T2.2: Risk Model - Covariance Estimation
**Effort:** 4-5 days | **PR:** `feat(p4): factor covariance estimation`
**Status:** ✅ Complete
**Priority:** P0 (Foundation for portfolio optimization)
**Dependencies:** T2.1 (Factor Builder)

**Deliverables:**
- Daily factor return calculation via cross-sectional regression
- Factor covariance matrix with exponential decay weighting
- Stock-level specific risk (idiosyncratic variance) estimation
- Newey-West HAC correction for autocorrelation
- Shrinkage estimators for stability (Ledoit-Wolf)

**Implementation:**

```python
# libs/risk/factor_covariance.py
@dataclass
class CovarianceConfig:
    """Configuration for covariance estimation."""
    halflife_days: int = 60
    min_observations: int = 126
    newey_west_lags: int = 5
    shrinkage_target: str = "identity"
    shrinkage_intensity: float | None = None  # None = Ledoit-Wolf optimal

class FactorCovarianceEstimator:
    """Estimate factor covariance matrix from stock returns."""

    def estimate_factor_returns(
        self,
        start_date: date,
        end_date: date
    ) -> pl.DataFrame:
        """Compute daily factor returns via cross-sectional regression."""
        ...

    def estimate_covariance(
        self,
        as_of_date: date
    ) -> np.ndarray:
        """Estimate factor covariance with exponential decay."""
        ...

    def estimate_with_shrinkage(
        self,
        as_of_date: date
    ) -> tuple[np.ndarray, float]:
        """Estimate covariance with Ledoit-Wolf shrinkage."""
        ...

# libs/risk/specific_risk.py
class SpecificRiskEstimator:
    """Estimate stock-level idiosyncratic risk."""

    def estimate(
        self,
        as_of_date: date,
        factor_cov: np.ndarray,
        factor_loadings: pl.DataFrame
    ) -> pl.DataFrame:
        """Estimate specific risk per stock."""
        ...
```

**Files to Create:**
- `libs/risk/__init__.py`
- `libs/risk/factor_covariance.py`
- `libs/risk/specific_risk.py`
- `tests/libs/risk/__init__.py`
- `tests/libs/risk/test_factor_covariance.py`
- `tests/libs/risk/test_specific_risk.py`
- `docs/CONCEPTS/covariance-estimation.md`

**PIT/Versioning Requirements (per P4T1 T1.6):**
- Covariance estimation MUST use factor returns computed from versioned datasets
- Covariance outputs stored with `as_of_date` and source `data_version_id`
- Reproducibility: same version_id + as_of_date = identical covariance matrix

**Acceptance Criteria:**
- [ ] Daily factor return extraction via cross-sectional regression
- [ ] Exponential decay weighting with configurable half-life
- [ ] Newey-West HAC correction
- [ ] Ledoit-Wolf shrinkage estimator
- [ ] Specific risk estimation per stock
- [ ] Covariance matrix is positive semi-definite (validated with eigenvalue check)
- [ ] `validate()` method checking for nulls, infs, correlation bounds [-1, 1]
- [ ] Performance test: estimate for 500 stocks in <10 seconds
- [ ] Outputs stored with dataset_version_id metadata
- [ ] >90% test coverage

---

### T2.3: Risk Model - Portfolio Analytics
**Effort:** 3-4 days | **PR:** `feat(p4): portfolio risk analytics`
**Status:** ✅ Complete
**Dependencies:** T2.2

**Deliverables:**
- Portfolio risk decomposition (factor vs specific)
- Marginal and component risk contributions
- VaR and CVaR (Expected Shortfall)
- Barra-style risk model implementation

**Files to Create:**
- `libs/risk/barra_model.py`
- `libs/risk/risk_decomposition.py`
- `tests/libs/risk/test_barra_model.py`
- `tests/libs/risk/test_risk_decomposition.py`
- `docs/CONCEPTS/risk-models.md`

---

### T2.4: Portfolio Optimizer & Stress Testing
**Effort:** 3-4 days | **PR:** `feat(p4): portfolio optimizer`
**Status:** ✅ Complete
**Dependencies:** T2.3, cvxpy solver availability

**Gating Dependencies:**
- cvxpy installed with working solver (ECOS, OSQP, or SCS)
- Install: `poetry add cvxpy` (project uses Poetry, NOT pip)
- Solver tested: `python -c "import cvxpy; print(cvxpy.installed_solvers())"` returns valid solvers
- If no solver: `poetry add cvxpy[ECOS]` or use OSQP as fallback

**Deliverables:**
- Mean-variance optimization with constraints
- Transaction cost modeling
- Historical stress testing (2008, 2020)
- Hypothetical scenario analysis

**Files to Create:**
- `libs/risk/portfolio_optimizer.py`
- `libs/risk/stress_testing.py`
- `tests/libs/risk/test_portfolio_optimizer.py`
- `tests/libs/risk/test_stress_testing.py`
- `docs/ADRs/ADR-0021-risk-model-implementation.md`

**Acceptance Criteria:**
- [ ] Mean-variance optimization with cvxpy solver
- [ ] Box, sector, and factor exposure constraints
- [ ] Transaction cost modeling (linear, optional quadratic market impact)
- [ ] 3+ historical stress scenarios (GFC 2008, COVID 2020, rate shock)
- [ ] Custom scenario input capability
- [ ] **Performance test**: Optimization solves in <5 seconds for 500 stocks
- [ ] **Stress test**: Optimizer handles ill-conditioned covariance (near-singular)
- [ ] **Stress test**: Optimizer returns infeasible result gracefully on impossible constraints
- [ ] >90% test coverage

---

### T2.5: Alpha Research Framework
**Effort:** 4-5 days | **PR:** `feat(p4): alpha framework`
**Status:** Pending
**Dependencies:** T2.1, Qlib installed (for metrics adapter), Sector Classifier (GICS or FF48)

> **Note:** Effort increased from 3-4d to 4-5d per Gemini/Codex review due to dual-backend parity testing complexity.

**Deliverables:**
- Alpha signal definition framework
- Point-in-time backtesting engine
- IC and ICIR analysis via Qlib Analysis adapter (with local fallback)
- **Grouped IC** (per sector/industry) - Qlib `qlib.contrib.evaluate`
- **Rank IC** (more robust than Pearson) - Qlib Analysis module
- Alpha decay curve analysis
- Turnover analysis
- **NEW (from review):** Signal Autocorrelation (essential for cost estimation)
- **NEW (from review):** Hit Rate / Positive Rate
- **NEW (from review):** Coverage % (universe coverage by signal)
- **NEW (from review):** Long/Short Spread (Top vs Bottom decile)

**Dependency Management:**
- Qlib should be installed as an **optional** dependency group:
  ```bash
  poetry add qlib --group research --optional
  ```
- Production environments (execution gateway, signal service) will NOT install this group.
- `AlphaMetricsAdapter` must gracefully handle `ImportError`.

**Qlib Metrics Adapter:**
```python
# libs/alpha/metrics.py
class AlphaMetricsAdapter:
    """Wrap Qlib Analysis module with local fallback."""

    def __init__(self, use_qlib: bool = True):
        self._use_qlib = use_qlib and self._qlib_available()

    def compute_ic(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        method: Literal["pearson", "rank"] = "rank"
    ) -> float:
        """Compute IC with Qlib or local fallback."""
        if self._use_qlib:
            return qlib_ic_adapter(signal, returns, method)
        return self._local_ic(signal, returns, method)

    def compute_grouped_ic(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        groups: pl.DataFrame  # sector/industry mapping
    ) -> pl.DataFrame:
        """Compute IC per group (sector, industry)."""
        if self._use_qlib:
            return qlib_grouped_ic(signal, returns, groups)
        return self._local_grouped_ic(signal, returns, groups)

    def _qlib_available(self) -> bool:
        """Check if Qlib Analysis module is available."""
        try:
            import qlib.contrib.evaluate
            return True
        except ImportError:
            return False
```

**Files to Create:**
- `libs/alpha/__init__.py`
- `libs/alpha/research_platform.py`
- `libs/alpha/alpha_library.py`
- `libs/alpha/metrics.py` - **NEW: Qlib metrics adapter**
- `tests/libs/alpha/__init__.py`
- `tests/libs/alpha/test_research_platform.py`
- `tests/libs/alpha/test_metrics.py` - **NEW: Adapter tests**
- `docs/CONCEPTS/alpha-research.md`

**PIT/Versioning Requirements:**
- All alpha backtests MUST use `dataset_version_id` from P4T1
- Alpha outputs stored with `as_of_date`, `data_version_id`, and `backtest_id`
- PIT validation: compare signals at T using data known at T vs T-1

**Acceptance Criteria:**
- [ ] Alpha signal definition framework with Protocol
- [ ] Point-in-time backtesting engine respecting dataset versions
- [ ] IC and ICIR analysis with statistical significance
- [ ] **Qlib metrics adapter** with `qlib.contrib.evaluate` wrapper
- [ ] **Grouped IC** (per sector) analysis
- [ ] **Rank IC** computation (more robust than Pearson)
- [ ] **Local fallback** when Qlib unavailable
- [ ] **Parity test**: Qlib rank IC vs local IC on same sample within tolerance
- [ ] Alpha decay curve analysis
- [ ] **NEW:** Signal Autocorrelation (lag-1, lag-5, lag-20)
- [ ] **NEW:** Hit Rate / Positive Rate (% of correct direction predictions)
- [ ] **NEW:** Coverage % (% of universe with valid signal)
- [ ] **NEW:** Long/Short Spread (top decile - bottom decile returns)
- [ ] **Throughput test**: Backtest with PIT cache (tiered SLAs below)
  - **Tier 1 (Baseline)**: 3 years / 500 symbols in <5 minutes on 4-core/16GB
  - **Tier 2 (Target)**: 5 years / 3000 symbols in <5 minutes on 8-core/32GB with warm cache
  - **Fallback**: If memory-limited, process in date-batches (252 days per batch)
  - **Hardware assumptions**: 8GB RAM per worker, ProcessPoolExecutor with 4 workers
- [ ] PIT validation tests (T vs T-1 delta checks)
- [ ] >90% test coverage

**Validation Gates (from Gemini/Codex review):**
- [ ] **Benchmark gate**: Per-metric tolerance thresholds (NOT blanket ≤5%)
  | Metric | Tolerance | Rationale |
  |--------|-----------|-----------|
  | Pearson IC | ≤3% | Sensitive to outliers |
  | Rank IC | ≤1% | More stable, tighter tolerance |
  | Grouped IC | ≤5% | Sector grouping adds variance |
  | ICIR | ≤5% | Ratio metric, moderate tolerance |
  | Autocorrelation | ≤2% | Direct correlation, tight |
  | Hit Rate | ≤1% | Binary metric, very stable |
  | Coverage | ≤0.5% | Count-based, near-exact |
  - Golden fixtures: `tests/fixtures/alpha_metrics_golden.parquet` (min 252 days, 500 symbols)
  - Snapshot requirement: Must use same `snapshot_id` for both backends
  - Sample size: Minimum 10,000 signal-return pairs for statistical validity
- [ ] **Packaging gate**: Wheel installs without Qlib by default; `pip install .[qlib]` enables adapter
- [ ] **Contract tests**: Shared test suite runs against both backends with same assertions
  - Test file: `tests/libs/alpha/test_metrics_contract.py`
  - Parametrized over `backend=["qlib", "polars"]`
  - Tests skip gracefully when Qlib not installed (`pytest.importorskip("qlib")`)
- [ ] **Prioritize Qlib**: Trust Qlib math initially if divergence detected; investigate local impl

**Metric Definitions & NaN Handling (from Codex review):**
```python
# Precise formulas and NaN treatment
class MetricDefinitions:
    """Canonical metric definitions for parity testing."""

    @staticmethod
    def hit_rate(signal: pl.Series, returns: pl.Series) -> float:
        """% of correct direction predictions. NaN signals excluded."""
        valid = signal.is_not_null() & returns.is_not_null()
        correct = (signal.filter(valid).sign() == returns.filter(valid).sign()).sum()
        return correct / valid.sum() if valid.sum() > 0 else float("nan")

    @staticmethod
    def coverage(signal: pl.Series, universe_size: int) -> float:
        """% of universe with valid (non-NaN, non-zero) signal."""
        valid = signal.is_not_null() & (signal != 0)
        return valid.sum() / universe_size

    @staticmethod
    def autocorrelation(signal: pl.Series, lag: int = 1) -> float:
        """Signal autocorrelation at specified lag. NaN-aware."""
        return signal.drop_nulls().to_pandas().autocorr(lag=lag)

    @staticmethod
    def long_short_spread(
        signal: pl.Series, returns: pl.Series, decile: int = 10
    ) -> float:
        """Top decile mean return - Bottom decile mean return."""
        # Exclude NaN signals from ranking
        valid_mask = signal.is_not_null()
        # ... quantile-based implementation
```

**Caching & Performance Strategy (from Codex review):**
- Use `DiskExpressionCache` (T2.8) for computed signals/factors
- Prefetch PIT snapshots for date ranges before batch computation
- Parallelize cross-sectional IC computation across dates (ProcessPoolExecutor)
- Memory ceiling: 8GB per worker for fixture runs
- Target: 5y × 3000 symbols < 5 minutes with warm cache

---

### T2.6: Alpha Advanced Analytics
**Effort:** 2-3 days | **PR:** `feat(p4): alpha advanced`
**Status:** Pending
**Dependencies:** T2.5 (metrics adapter, PIT cache), DatasetVersionManager

**Deliverables (Core):**
- Alpha combiner (composite signals)
- Signal correlation analysis
- Qlib turnover analysis integration

**Deliverables (STRETCH - defer if behind at Week 9 checkpoint):**
- Overfitting detection (OOS testing)
- Multiple testing correction (Bonferroni, FDR)

**Decision Gate:** End of Week 9 - if T2.5+T2.6 combined exceed 7 days, defer overfitting/OOS to Phase 3.

**Alpha Combiner Specification (from Codex review):**
```python
# libs/alpha/alpha_combiner.py
class AlphaCombiner:
    """Combine multiple alpha signals into composite."""

    def __init__(self, weighting: Literal["equal", "ic", "ir", "vol_parity"] = "ic"):
        """
        Weighting methods:
        - equal: Simple average
        - ic: Weight by trailing IC (information coefficient)
        - ir: Weight by trailing IR (IC / IC_std)
        - vol_parity: Weight by inverse signal volatility
        """
        self.weighting = weighting

    def combine(
        self,
        signals: dict[str, pl.DataFrame],  # {signal_name: signal_df}
        lookback_days: int = 252,
        normalize: bool = True,  # Cross-sectional z-score before combining
    ) -> pl.DataFrame:
        """
        Combine signals with specified weighting.

        Input contract:
        - Each signal_df has columns: ["date", "symbol", "signal"]
        - Signals are cross-sectionally z-scored if normalize=True
        - NaN signals excluded from weighting calculation

        Output contract:
        - Returns DataFrame with ["date", "symbol", "composite_signal", "weights"]
        """
        ...
```

**Correlation Analysis (from Codex review):**
- Apply winsorization (1st/99th percentile) before correlation to handle outliers
- Handle async symbols (different trading calendars) via inner join on dates
- Report both Pearson and Spearman correlations
- Flag highly correlated pairs (|corr| > 0.7) for redundancy warning

**Overfitting Detection (STRETCH - from Codex review):**
```python
class OverfittingDetector:
    """Detect overfitted alpha signals."""

    def __init__(
        self,
        split_method: Literal["rolling", "blocked_cv", "purged_cv"] = "rolling",
        n_splits: int = 5,
        embargo_days: int = 5,  # Gap between train/test to prevent leakage
    ):
        """
        Split methods:
        - rolling: Expanding window train, fixed test
        - blocked_cv: Time-blocked cross-validation
        - purged_cv: Purged k-fold with embargo (de Prado)
        """
        ...

    def detect(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        min_oos_ic: float = 0.02,  # Minimum OOS IC to pass
        max_is_oos_gap: float = 0.5,  # Max (IS_IC - OOS_IC) / IS_IC
    ) -> OverfittingResult:
        """Returns IS/OOS metrics and overfitting probability."""
        ...
```

**Turnover Contract (from Codex review):**
- Input: Signal DataFrame with ["date", "symbol", "signal"]
- Output: Daily turnover = sum(|weight_t - weight_t-1|) / 2
- Qlib fallback: Use local implementation if Qlib unavailable
- Validate against manual calculation on golden fixture

**Files to Create:**
- `libs/alpha/alpha_combiner.py`
- `libs/alpha/overfitting_detection.py` - **STRETCH**
- `tests/libs/alpha/test_alpha_combiner.py`
- `docs/CONCEPTS/alpha-overfitting.md` - **STRETCH**

**Acceptance Criteria (Core):**
- [ ] Alpha combiner with equal/IC-weighted/IR-weighted/vol-parity methods
- [ ] Signal correlation matrix with winsorization
- [ ] Async symbol handling (inner join on dates)
- [ ] Redundancy warning for |corr| > 0.7 pairs
- [ ] Qlib turnover metric validated against manual calculation
- [ ] Turnover local fallback when Qlib unavailable

**Acceptance Criteria (STRETCH):**
- [ ] Overfitting detection via OOS testing with rolling/blocked_cv/purged_cv
- [ ] Embargo period to prevent train/test leakage
- [ ] Multiple testing correction (Bonferroni, FDR)
- [ ] >90% test coverage

---

### T2.7: Factor Attribution Analysis
**Effort:** 3-4 days | **PR:** `feat(p4): factor attribution`
**Status:** Pending
**Dependencies:** T2.1, T1.5 (Fama-French) - COMPLETE, Sector Classifier (GICS/FF48), DatasetVersionManager

**Deliverables (Core):**
- Fama-French regression (3/5/6-factor) - reuses T1.5 provider
- Rolling factor exposure tracking
- Performance attribution output

**Deliverables (STRETCH):**
- Conditional attribution (up/down markets)

**Note:** Leverages existing Fama-French provider from T1.5. Focus on core FF regression; conditional analysis is stretch.

**Regression Specification (from Codex review):**
```python
# libs/analytics/attribution.py
from statsmodels.regression.linear_model import OLS
from statsmodels.stats.sandwich_covariance import cov_hac_simple  # Newey-West

class FactorAttribution:
    """Fama-French factor attribution with robust standard errors."""

    def __init__(
        self,
        model: Literal["ff3", "ff5", "ff6"] = "ff5",
        window_days: int = 252,
        rebalance_freq: Literal["daily", "weekly", "monthly"] = "monthly",
        std_errors: Literal["ols", "hc3", "newey_west"] = "newey_west",
        newey_west_lags: int = 5,  # sqrt(T) rule of thumb
    ):
        """
        Regression specification:
        - OLS: Standard errors assume homoskedasticity
        - HC3: Heteroskedasticity-consistent (White)
        - Newey-West: HAC for serial correlation (recommended)

        Window vs Rebalance:
        - window_days: Lookback for regression
        - rebalance_freq: How often to re-estimate (affects output granularity)
        """
        self.model = model
        self.window_days = window_days
        self.rebalance_freq = rebalance_freq
        self.std_errors = std_errors
        self.nw_lags = newey_west_lags

    def fit(
        self,
        portfolio_returns: pl.DataFrame,  # ["date", "return"]
        ff_factors: pl.DataFrame,  # ["date", "mkt_rf", "smb", "hml", ...]
        min_obs: int = 60,  # Minimum observations for valid regression
    ) -> AttributionResult:
        """
        Run factor attribution regression.

        Returns AttributionResult with:
        - alpha: Annualized alpha (bps)
        - betas: Factor loadings dict
        - t_stats: T-statistics for each coefficient
        - r_squared: Adjusted R²
        - residual_vol: Idiosyncratic volatility
        """
        ...

    def check_multicollinearity(
        self,
        ff_factors: pl.DataFrame,
        vif_threshold: float = 5.0,
    ) -> list[str]:
        """
        Check for factor multicollinearity using VIF.
        Returns list of warnings for VIF > threshold.
        """
        ...
```

**Data Filters (from Codex review):**
- **Microcap filter**: Exclude stocks below 20th percentile market cap (or $100M)
- **Currency filter**: USD-denominated only (or specify currency)
- **Minimum history**: Require 60+ daily observations for regression
- **Survivorship**: Use PIT universe from DatasetVersionManager

**Output Schema for Dashboard/Registry (from Codex review):**
```python
@dataclass
class AttributionResult:
    """Factor attribution output for dashboard and registry."""
    # Identification
    portfolio_id: str
    as_of_date: date
    dataset_version_id: str
    regression_config: dict  # {model, window, std_errors, ...}

    # Core metrics
    alpha_annualized_bps: float
    alpha_t_stat: float
    r_squared_adj: float
    residual_vol_annualized: float

    # Factor loadings
    betas: dict[str, float]  # {"mkt_rf": 1.05, "smb": -0.2, ...}
    beta_t_stats: dict[str, float]
    beta_p_values: dict[str, float]

    # Diagnostics
    n_observations: int
    multicollinearity_warnings: list[str]
    durbin_watson: float  # Autocorrelation diagnostic

    def to_registry_dict(self) -> dict:
        """Serialize for model registry storage."""
        ...

    def to_dashboard_dict(self) -> dict:
        """Serialize for web dashboard display."""
        ...
```

**Files to Create:**
- `libs/analytics/__init__.py`
- `libs/analytics/attribution.py`
- `tests/libs/analytics/__init__.py`
- `tests/libs/analytics/test_attribution.py`
- `docs/CONCEPTS/performance-attribution.md`

**Acceptance Criteria (Core):**
- [ ] Fama-French 3/5/6-factor regression
- [ ] Rolling factor exposure (252-day window)
- [ ] Performance attribution output with alpha, betas, R²
- [ ] Robust standard errors (Newey-West default)
- [ ] Multicollinearity check (VIF)
- [ ] Microcap/currency filters
- [ ] Output schema for dashboard and registry
- [ ] Uses T1.5 FamaFrenchLocalProvider
- [ ] Uses DatasetVersionManager for PIT universe
- [ ] >90% test coverage

**Acceptance Criteria (STRETCH):**
- [ ] Conditional attribution (up/down market regimes)
- [ ] Regime detector for market state classification

---

### T2.8: Model Registry Integration
**Effort:** 5-6 days | **PR:** `feat(p4): model registry`
**Status:** Pending
**Dependencies:** T2.4, T2.6, T1.6 (Dataset Versioning)

> **Note:** Effort increased from 3-4d to 5-6d per Gemini/Codex review due to schema additions, migration complexity, CLI wiring, and E2E integration tests.

**Problem:** No clear path to deploy trained risk models, factor definitions, or alpha weights to production. Need versioned storage with provenance tracking.

**Deliverables:**
- Model serialization with SHA-256 checksum
- Versioned model storage with immutable versions
- Production model loader with compatibility checks
- Integration contract for signal_service
- **NEW:** Training config capture (`config` blob + `config_hash`)
- **NEW:** Feature formula tracking (`feature_formulas` - Phase 3 placeholder)
- **NEW:** DatasetVersionManager snapshot linkage (`snapshot_id`)
- **NEW (from review):** Qlib recorder-style experiment metadata (`experiment_id`, `run_id`)
- **NEW (from review):** E2E integration tests (alpha → registry → signal_service load)

**Storage Backend Specification:**
```
data/models/
├── registry.db           # DuckDB catalog of registered models
├── artifacts/
│   ├── risk_model/
│   │   ├── v1.0.0/
│   │   │   ├── model.pkl       # Serialized model
│   │   │   ├── metadata.json   # Model metadata
│   │   │   └── checksum.sha256
│   │   └── v1.1.0/
│   └── alpha_weights/
│       └── v1.0.0/
└── manifest.json         # Registry manifest
```

**DuckDB Schema:**
```sql
-- Model registry catalog
CREATE TABLE models (
    model_id VARCHAR PRIMARY KEY,          -- 'risk_model_v1.0.0'
    model_type VARCHAR NOT NULL,           -- 'risk_model', 'alpha_weights', 'factor_definitions'
    version VARCHAR NOT NULL,              -- Semantic version
    status VARCHAR NOT NULL DEFAULT 'staged', -- 'staged', 'production', 'archived', 'failed'
    artifact_path VARCHAR NOT NULL,        -- Relative path to artifact dir
    checksum_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    promoted_at TIMESTAMP,
    archived_at TIMESTAMP,
    UNIQUE(model_type, version)            -- Immutable: no duplicate versions
);

-- Promotion history for audit
CREATE TABLE promotion_history (
    id INTEGER PRIMARY KEY,
    model_id VARCHAR NOT NULL REFERENCES models(model_id),
    from_status VARCHAR NOT NULL,
    to_status VARCHAR NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    changed_by VARCHAR NOT NULL            -- 'cli:user' or 'api:service'
);

-- Status enum enforcement via CHECK constraint
ALTER TABLE models ADD CONSTRAINT valid_status
    CHECK (status IN ('staged', 'production', 'archived', 'failed'));
```

**Supported Artifact Types & Required Metadata:**
| Artifact Type | Serialization | Required Metadata Fields |
|---------------|---------------|-------------------------|
| `risk_model` | pickle/joblib | factor_list, halflife_days, shrinkage_intensity |
| `alpha_weights` | JSON | alpha_names, combination_method, ic_threshold |
| `factor_definitions` | pickle | factor_names, categories, lookback_days |
| `feature_transforms` | pickle | feature_names, normalization_params |

**Environment/Deps Metadata (ALL artifacts):**
```python
@dataclass
class EnvironmentMetadata:
    python_version: str           # e.g., "3.11.5"
    dependencies_hash: str        # SHA-256 of sorted requirements.txt
    platform: str                 # e.g., "linux-x86_64"
    created_by: str               # User/service that created

    # Key library versions for compatibility
    numpy_version: str
    polars_version: str
    sklearn_version: str | None
    cvxpy_version: str | None
```

**Storage Durability & Backup:**
- **Primary:** Local filesystem (`data/models/`)
- **Backup:** Optional S3/GCS sync via `rclone` (configured in env)
- **Backup frequency:** Daily at 02:00 UTC
- **Retention:** 90 days for backups
- **Recovery:** `model restore --from-backup <date>` CLI command

**Metadata Schema:**
```python
@dataclass
class ModelMetadata:
    model_id: str              # Unique identifier
    model_type: str            # 'risk_model', 'alpha_weights', 'factor_definitions'
    version: str               # Semantic version (immutable)
    created_at: datetime
    # Provenance tracking (FULL linkage to P4T1 versioning)
    dataset_version_ids: dict[str, str]  # {'crsp': 'v1.2.3', 'compustat': 'v1.0.1', 'taq': 'v1.0.0'}
    snapshot_id: str           # NEW: DatasetVersionManager snapshot ID
    factor_list: list[str]     # Factors included
    parameters: dict           # Model parameters/config
    # Validation
    checksum_sha256: str       # Artifact checksum
    metrics: dict[str, float]  # Performance metrics (R², IC, etc.)
    # Environment
    env: EnvironmentMetadata   # Python version, deps hash, etc.
    # NEW: Training config for reproducibility
    config: dict               # NEW: Training config blob (hyperparams, settings)
    config_hash: str           # NEW: SHA-256 of config for integrity
    feature_formulas: list[str] | None  # NEW: Phase 3 placeholder for FormulaicFactor
    # NEW (from review): Qlib recorder-style experiment tracking
    experiment_id: str | None  # NEW: Experiment grouping ID (e.g., "alpha_research_2024Q1")
    run_id: str | None         # NEW: Individual training run ID (UUID)
    dataset_uri: str | None    # NEW: Reference to dataset location for Qlib compatibility
    qlib_version: str | None   # NEW: Qlib version if used during training
```

**Config Field Contents:**
```python
# Example config blob for risk_model
{
    "model_type": "barra_risk_model",
    "hyperparameters": {
        "halflife_days": 60,
        "shrinkage_intensity": 0.2,
        "min_observations": 126
    },
    "feature_definitions": ["momentum_12m", "value_btm", "quality_roe"],
    "training_window": {"start": "2020-01-01", "end": "2024-12-01"},
    "qlib_version": "0.9.0"  # If Qlib used
}
```

**PIT Enforcement During Registration:**
```python
def register_model(self, model, metadata: ModelMetadata) -> str:
    # MUST validate dataset versions at registration time
    for dataset, version_id in metadata.dataset_version_ids.items():
        if not self.version_registry.validate_exists(dataset, version_id):
            raise InvalidDatasetVersionError(f"{dataset}:{version_id} not found in P4T1 registry")
    # Store full dict, not just string
    ...
```

**Lifecycle & Promotion Flow:**
```
staging → production promotion:
1. Register model with status='staged'
2. Run validation tests in paper trading
3. Approve via CLI: `model promote risk_model v1.0.0`
4. Status changes to 'production', previous production → 'archived'
5. signal_service polls registry, loads new version

Rollback:
1. `model rollback risk_model` → revert to previous 'archived' version
2. Immediate effect via registry poll
```

**Retention Policy:**
- Production models: retained indefinitely
- Staged models: 30 days after promotion or rejection
- Archived models: 90 days after archival
- Artifacts: checksum re-validated on every load
- GC job: weekly cleanup of expired artifacts

**Concurrency & Locking:**
- Single-writer lock via DuckDB transaction (aligned with P4T1 policy)
- Readers never blocked (snapshot isolation)
- Partial write detection: temp file + atomic rename pattern

**Promotion Gates:**
- Minimum metrics thresholds: IC > 0.02, Sharpe > 0.5 (configurable)
- Mandatory paper trading period: 24 hours minimum
- Manual approval required for production promotion

**CLI/API Surface:**
```bash
# CLI commands
model register <type> <path> --version <semver>
model promote <type> <version>
model rollback <type>
model list <type> --status [staged|production|archived]
model validate <type> <version>  # checksum + load test
model restore --from-backup <date>  # Disaster recovery
```

**FastAPI Endpoints (mounted at `/api/v1/models`):**
```python
# Endpoint definitions with auth/response schemas
# Response models with dataset_version_ids for auditability
@dataclass
class CurrentModelResponse:
    model_type: str
    version: str
    checksum: str
    dataset_version_ids: dict[str, str]  # {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}

@dataclass
class ModelMetadataResponse:
    model_id: str
    model_type: str
    version: str
    status: str
    artifact_path: str
    checksum_sha256: str
    dataset_version_ids: dict[str, str]  # Full provenance tracking
    factor_list: list[str]
    parameters: dict
    metrics: dict[str, float]
    env: EnvironmentMetadata
    created_at: datetime
    promoted_at: datetime | None

@router.get("/{model_type}/current", response_model=CurrentModelResponse)
async def get_current_model(
    model_type: ModelType,
    auth: ServiceToken = Depends(verify_service_token)  # JWT service token
) -> CurrentModelResponse:
    """
    Returns: {
        "model_type": "risk_model",
        "version": "v1.0.0",
        "checksum": "abc123...",
        "dataset_version_ids": {"crsp": "v1.2.3", "compustat": "v1.0.1"}
    }
    Auth: Bearer token with 'model:read' scope
    Note: dataset_version_ids enables consumers to verify data alignment
    """

@router.get("/{model_type}/{version}", response_model=ModelMetadataResponse)
async def get_model_metadata(
    model_type: ModelType,
    version: str,
    auth: ServiceToken = Depends(verify_service_token)
) -> ModelMetadataResponse:
    """
    Returns: Full metadata including artifact_path, env, training_data_version
    """

@router.post("/{model_type}/{version}/validate", response_model=ValidationResult)
async def validate_model(
    model_type: ModelType,
    version: str,
    auth: ServiceToken = Depends(verify_service_token)  # Requires 'model:write' scope
) -> ValidationResult:
    """
    Triggers checksum verification + test load
    Returns: {"valid": true, "load_time_ms": 85, "checksum_match": true}
    """
```

**Auth/Timeout/Backoff Configuration:**
```python
# Service client configuration
MODEL_REGISTRY_CONFIG = {
    "base_url": "${MODEL_REGISTRY_URL:-http://localhost:8005}",
    "auth": {
        "type": "bearer",
        "token_env": "MODEL_REGISTRY_TOKEN",  # JWT with service scopes
        "scopes_required": ["model:read"]
    },
    "timeout": {
        "connect": 5.0,      # seconds
        "read": 30.0,        # seconds (model metadata can be large)
        "write": 60.0        # seconds (validation can take time)
    },
    "retry": {
        "max_attempts": 3,
        "backoff_base": 1.0,      # seconds
        "backoff_factor": 2.0,    # exponential: 1s, 2s, 4s
        "retry_on": [502, 503, 504]  # Gateway errors only
    },
    "circuit_breaker": {
        "failure_threshold": 5,
        "recovery_timeout": 60    # seconds before retry
    }
}
```

**Polling & Hot-Reload:**
- signal_service polls `/models/*/current` every 60 seconds
- On version change: load new model → validate → swap atomically
- Fallback: if load fails, keep last-good version, emit alert
- If no last-good exists: trigger circuit breaker, block signal generation

**signal_service Integration Contract:**
```python
# Integration hook for production loading
class ProductionModelLoader:
    """Load models from registry for signal_service."""

    def get_risk_model(self, version: str | None = None) -> BarraRiskModel:
        """Get risk model (latest production if version=None)."""
        ...

    def get_alpha_weights(self, version: str | None = None) -> dict[str, float]:
        """Get alpha combiner weights."""
        ...

    def check_compatibility(
        self,
        model_id: str,
        current_versions: dict[str, str]  # {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}
    ) -> tuple[bool, list[str]]:
        """
        Check if model is compatible with current data versions.

        Returns: (compatible, drift_warnings)
        - If ANY dataset version differs: compatible=False, warnings list populated
        - Behavior controlled by STRICT_VERSION_MODE:
          - true (production): raises VersionDriftError, blocks load
          - false (development): logs warning, allows load with drift_warnings
        """
        ...

    def on_load_failure(self, model_id: str, error: Exception) -> None:
        """Handle load failure - triggers circuit breaker warning."""
        ...
```

**risk_manager Integration:**
- Covariance matrix exposed via `get_factor_covariance(as_of_date)`
- Specific risk via `get_specific_risk(symbols, as_of_date)`
- VaR/CVaR via `compute_var(portfolio, confidence)`
- Pre-trade limit hooks: optimizer results validated against risk limits before order

**risk_manager REST API (mounted at `/api/v1/risk`):**

**Request/Response Schemas:**
```python
# Covariance endpoint
class CovarianceRequest(BaseModel):
    as_of_date: date                     # Date for covariance estimation
    version_id: str | None = None        # Model version (None = current)
    factors: list[str] | None = None     # Subset of factors (None = all)

class CovarianceResponse(BaseModel):
    factor_names: list[str]              # Ordered list of factor names
    covariance_matrix: list[list[float]] # NxN matrix (row-major)
    correlation_matrix: list[list[float]] # NxN correlation
    dataset_version_id: str              # Source data version
    model_version: str                   # Risk model version
    as_of_date: date
    computation_timestamp: datetime

# VaR endpoint
class VaRRequest(BaseModel):
    portfolio: dict[str, float]          # symbol -> weight (must sum to ~1.0)
    confidence: float = 0.95             # VaR confidence level (0.95 or 0.99)
    horizon_days: int = 1                # Forecast horizon
    include_decomposition: bool = True   # Include factor contributions

class VaRResponse(BaseModel):
    var: float                           # Value at Risk (as decimal, e.g., 0.023 = 2.3%)
    cvar: float                          # Conditional VaR (expected shortfall)
    total_risk: float                    # Portfolio volatility
    factor_contribution: dict[str, float] | None  # Factor risk breakdown
    specific_risk: float | None          # Idiosyncratic risk contribution
    model_version: str
    dataset_version_id: str

# Pre-trade check endpoint
class PreTradeCheckRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int                        # Shares
    price: float | None = None           # Limit price (None = market)
    strategy_id: str | None = None       # For strategy-specific limits

class PreTradeCheckResponse(BaseModel):
    approved: bool
    checks: dict[str, Literal["pass", "fail", "warn"]]
    warnings: list[str]
    rejection_reason: str | None = None
    estimated_var_impact: float | None   # How much this trade increases VaR
```

**Error Codes:**
```python
# HTTP 422 - Validation errors
{"detail": "Portfolio weights must sum to approximately 1.0", "code": "INVALID_WEIGHTS"}
{"detail": "Unknown symbol: XYZ", "code": "UNKNOWN_SYMBOL"}
{"detail": "Confidence must be 0.95 or 0.99", "code": "INVALID_CONFIDENCE"}

# HTTP 503 - Service unavailable
{"detail": "Risk model not loaded", "code": "MODEL_UNAVAILABLE"}
{"detail": "Covariance matrix stale (>24h)", "code": "STALE_COVARIANCE"}
```

@router.post("/covariance", response_model=CovarianceResponse)
async def get_factor_covariance(
    request: CovarianceRequest,
    auth: ServiceToken = Depends(verify_service_token)  # Requires 'risk:read' scope
) -> CovarianceResponse:
    """Get factor covariance matrix. Cache-Control: max-age=3600 (1 hour TTL)."""

@router.post("/var", response_model=VaRResponse)
async def compute_var(
    request: VaRRequest,
    auth: ServiceToken = Depends(verify_service_token)  # Requires 'risk:read' scope
) -> VaRResponse:
    """Compute portfolio VaR and risk decomposition."""

@router.post("/pre-trade-check", response_model=PreTradeCheckResponse)
async def pre_trade_check(
    request: PreTradeCheckRequest,
    auth: ServiceToken = Depends(verify_service_token)  # Requires 'risk:check' scope
) -> PreTradeCheckResponse:
    """Validate trade against risk limits. No cache (real-time)."""
```

**Caching & TTL:**
- Covariance matrix: cached 1 hour (recalculated daily)
- Specific risk: cached 1 hour
- Pre-trade checks: no cache (real-time)

**signal_service Factor/Alpha Refresh:**
```python
# How signal_service gets updated factor definitions and alpha weights
class SignalServiceModelManager:
    def __init__(self, model_loader: ProductionModelLoader):
        self._loader = model_loader
        self._factor_defs: dict = {}
        self._alpha_weights: dict = {}
        self._last_refresh: datetime = None

    async def refresh_if_needed(self) -> None:
        """Called every 60 seconds by background task."""
        current_factor_version = await self._loader.get_current_version("factor_definitions")
        current_alpha_version = await self._loader.get_current_version("alpha_weights")

        if self._needs_refresh(current_factor_version, current_alpha_version):
            self._factor_defs = await self._loader.get_factor_definitions()
            self._alpha_weights = await self._loader.get_alpha_weights()
            self._last_refresh = datetime.utcnow()
            logger.info("Refreshed factor/alpha models", versions={...})

    def get_signal(self, symbol: str, as_of_date: date) -> float:
        """Compute signal using current factor defs and alpha weights."""
        # Uses self._factor_defs and self._alpha_weights
        ...
```

**Error Semantics:**
```python
# HTTP response codes
200 OK           # Success
404 Not Found    # Model/version not found
409 Conflict     # Version already exists (immutable)
422 Unprocessable # Checksum mismatch, corrupt artifact
503 Unavailable  # Registry temporarily locked
```

**Circuit Breaker Integration:**
- on_load_failure → emit `model.load.failed` metric
- 3 consecutive failures → trigger circuit breaker warning
- Manual intervention required to clear

**Model Registry DR Runbook:**

*Failure Scenarios:*

| Scenario | Detection | Response | RTO |
|----------|-----------|----------|-----|
| DuckDB catalog corrupt | Checksum mismatch on load | Restore from backup | 30 min |
| Artifact directory missing | FileNotFoundError on load | Restore from backup | 30 min |
| API service down | Health check fails | Restart service, fallback to cache | 5 min |
| All models invalid | Validation fails on all versions | Manual intervention + restore | 1 hr |

*Recovery Procedures:*
```bash
# 1. Check registry health
python scripts/registry_health.py

# 2. Restore DuckDB catalog from backup
cp data/backups/models/registry.db.$(date +%Y%m%d) data/models/registry.db

# 3. Verify artifacts match catalog
python scripts/verify_registry.py --repair

# 4. If artifacts missing, restore from off-site
rclone sync remote:backups/models/artifacts/ data/models/artifacts/

# 5. Validate restored state
python scripts/registry_health.py --full-validation
```

*Failover Policy:*
- **Read-only mode:** If writes fail, service continues serving last-known-good models
- **Cache fallback:** signal_service caches current model in memory for 24 hours
- **Manual override:** `FORCE_MODEL_VERSION=v1.0.0` env var bypasses registry lookup

**risk_manager DR Runbook:**

*Failure Scenarios:*

| Scenario | Detection | Response | RTO |
|----------|-----------|----------|-----|
| Covariance stale (>24h) | Timestamp check | Recompute or use last-good | 15 min |
| Risk model unavailable | 503 from registry | Use cached covariance | 5 min |
| VaR computation fails | Exception in compute | Return error, allow manual override | Immediate |
| Pre-trade check blocked | Circuit breaker active | Alert, require manual approval | Manual |

*Circuit Breaker Thresholds:*
```python
RISK_MANAGER_CIRCUIT_BREAKER = {
    "failure_threshold": 5,       # Consecutive failures to trip
    "recovery_timeout": 120,      # Seconds before retry
    "half_open_requests": 1,      # Requests in half-open state
    "metrics": [
        "risk.compute.failed",
        "risk.model.unavailable",
        "risk.covariance.stale"
    ],
    "alert_channels": ["pagerduty", "slack"]
}
```

*Recovery Procedures:*
```bash
# 1. Check risk_manager health
curl http://localhost:8006/health

# 2. Force covariance recompute
python scripts/recompute_covariance.py --as-of today

# 3. Clear circuit breaker (if safe)
redis-cli SET "cb:risk_manager:state" "CLOSED"

# 4. Verify pre-trade checks working
python scripts/test_pretrade.py --symbol AAPL --qty 100
```

---

## Service Integration Sequence

**signal_service Hot-Reload Flow:**
```
signal_service          ModelRegistry              Artifacts
     |                       |                         |
     |---GET /current------->|                         |
     |<--{version: v1.1.0}---|                         |
     |                       |                         |
     | (if version changed)  |                         |
     |---GET /v1.1.0-------->|                         |
     |<--{metadata, path}----|                         |
     |                       |                         |
     |---load(path)---------------------------------->|
     |<--model object---------------------------------|
     |                       |                         |
     |---validate(model)---->|                         |
     |                       |                         |
     | (atomic swap)         |                         |
     | self._model = model   |                         |
```

**risk_manager Pre-Trade Check Flow:**
```
execution_gateway    risk_manager       RiskModel      Optimizer
     |                    |                 |              |
     |--check_order------>|                 |              |
     |                    |--get_cov------->|              |
     |                    |<--cov_matrix----|              |
     |                    |                 |              |
     |                    |--compute_var--->|              |
     |                    |<--var_estimate--|              |
     |                    |                 |              |
     |                    | (validate limits)              |
     |<--{approved: bool}-|                 |              |
```

**Fallback Policy:**
1. If model load fails: keep last-good version, emit alert
2. If no last-good: block signal generation, trigger circuit breaker
3. If version drift detected: log warning, continue with current model
4. Manual rollback available via CLI: `model rollback <type>`

**Files to Create:**
- `libs/models/__init__.py`
- `libs/models/registry.py`
- `libs/models/serialization.py`
- `libs/models/loader.py` (production loader)
- `tests/libs/models/__init__.py`
- `tests/libs/models/test_registry.py`
- `tests/libs/models/test_serialization.py`
- `tests/libs/models/test_loader.py`
- `docs/CONCEPTS/model-registry.md`
- `docs/ADRs/ADR-0022-model-deployment.md`

**Acceptance Criteria:**
- [ ] DuckDB-based registry catalog with artifacts directory
- [ ] Pickle/joblib serialization with SHA-256 checksum verification
- [ ] Immutable versioning (no overwrites, version = permanent)
- [ ] Provenance: training_data_version linked to P4T1 dataset versions
- [ ] ProductionModelLoader with latest version retrieval
- [ ] Compatibility check for data version drift with warning on mismatch
- [ ] Promotion/rollback CLI commands functional
- [ ] **Test**: load failure returns graceful error (not crash)
- [ ] **Test**: checksum mismatch detected and rejected
- [ ] **Concurrency test**: Simultaneous writers correctly serialized
- [ ] **Corruption test**: Partial artifact upload detected and rejected
- [ ] **Latency test**: ProductionModelLoader version flip in <100ms
- [ ] **E2E corruption/rollback test**: Full scenario validation (see spec below)
- [ ] >90% test coverage

**DiskExpressionCache Acceptance Criteria (from Gemini/Codex review):**
- [ ] Cache key format: `{factor}:{date}:{version}:{snapshot_id}:{config_hash}` - ALL components required
- [ ] Cache invalidation hooks tied to dataset version updates
- [ ] Cache invalidation on config_hash change
- [ ] TTL-based expiration (default 7 days, configurable)
- [ ] Atomic writes using existing `_atomic_write_parquet` helper
- [ ] **Property tests**: Key canonicalization produces deterministic hashes
- [ ] **Concurrency test**: Parallel cache writers maintain atomicity
- [ ] **PIT safety test**: Cache miss on snapshot_id mismatch (no stale data)
- [ ] **Config safety test**: Cache miss on config_hash mismatch

**E2E Integration Tests (from review):**
- [ ] **E2E path test**: alpha → registry → signal_service load
- [ ] **Migration dry-run**: Test on copy of registry.db before production
- [ ] **Rollback test**: Verify rollback to previous version works

**Migration Plan for Existing Artifacts (from Codex review):**
```python
# scripts/migrate_registry.py
class RegistryMigration:
    """Migrate existing model artifacts to new registry schema."""

    def __init__(self, legacy_path: Path, new_registry_path: Path):
        self.legacy_path = legacy_path
        self.new_path = new_registry_path

    def discover_legacy_artifacts(self) -> list[LegacyArtifact]:
        """Scan for existing model files without registry entries."""
        ...

    def generate_metadata(self, artifact: LegacyArtifact) -> ModelMetadata:
        """
        Generate metadata for legacy artifacts:
        - Infer model_type from path/filename
        - Set dataset_version_ids to "UNKNOWN_LEGACY"
        - Set config_hash to hash of artifact file
        - Mark experiment_id as "migration_batch_{date}"
        """
        ...

    def migrate_artifact(
        self,
        artifact: LegacyArtifact,
        dry_run: bool = True,
    ) -> MigrationResult:
        """
        Migrate single artifact:
        1. Copy artifact to new artifacts/ directory
        2. Generate metadata JSON
        3. Register in DuckDB catalog
        4. Verify checksum after copy
        """
        ...

    def run_migration(self, dry_run: bool = True) -> MigrationReport:
        """
        Full migration workflow:
        1. Backup existing registry.db (if exists)
        2. Discover legacy artifacts
        3. Generate migration plan
        4. If dry_run: print plan, don't execute
        5. If not dry_run: execute migration
        6. Verify all artifacts accessible via registry
        """
        ...
```

**Migration CLI:**
```bash
# Discover what would be migrated
python scripts/migrate_registry.py --dry-run

# Execute migration (creates backup first)
python scripts/migrate_registry.py --execute

# Verify migration success
python scripts/migrate_registry.py --verify
```

**Migration Acceptance Criteria:**
- [ ] Legacy artifact discovery (scan artifacts/ for unregistered models)
- [ ] Metadata inference for legacy models (best-effort)
- [ ] Dry-run mode shows migration plan without executing
- [ ] Automatic backup of registry.db before migration
- [ ] Verification step confirms all migrated artifacts loadable
- [ ] Rollback script if migration fails

**Semantic Versioning for Compatibility (from Gemini/Codex review):**
```python
# libs/models/compatibility.py
from packaging.version import Version

class VersionCompatibilityChecker:
    """
    Semantic versioning logic for dataset compatibility.

    Policy:
    - MAJOR version change: BLOCK (breaking schema changes)
    - MINOR version change: WARN (new columns, backward compatible)
    - PATCH version change: ALLOW (bug fixes, no schema change)
    """

    def check_compatibility(
        self,
        model_versions: dict[str, str],  # Versions model was trained on
        current_versions: dict[str, str],  # Current data versions
        strict_mode: bool = False,  # Production = True
    ) -> CompatibilityResult:
        """
        Check if model is compatible with current data versions.

        Returns:
        - compatible: bool
        - level: "exact" | "patch_drift" | "minor_drift" | "major_drift"
        - warnings: list[str]
        """
        results = {}
        for dataset, model_ver in model_versions.items():
            current_ver = current_versions.get(dataset)
            if current_ver is None:
                results[dataset] = ("missing", f"{dataset} not available")
                continue

            model_v = Version(model_ver)
            current_v = Version(current_ver)

            if model_v == current_v:
                results[dataset] = ("exact", None)
            elif model_v.major != current_v.major:
                results[dataset] = ("major_drift", f"{dataset}: {model_ver} → {current_ver}")
            elif model_v.minor != current_v.minor:
                results[dataset] = ("minor_drift", f"{dataset}: {model_ver} → {current_ver}")
            else:  # Only patch differs
                results[dataset] = ("patch_drift", f"{dataset}: {model_ver} → {current_ver}")

        # Determine overall compatibility
        has_major = any(r[0] == "major_drift" for r in results.values())
        has_minor = any(r[0] == "minor_drift" for r in results.values())

        if has_major:
            return CompatibilityResult(
                compatible=False,
                level="major_drift",
                warnings=[r[1] for r in results.values() if r[1]],
            )
        elif has_minor and strict_mode:
            return CompatibilityResult(
                compatible=False,
                level="minor_drift",
                warnings=[r[1] for r in results.values() if r[1]],
            )
        else:
            return CompatibilityResult(
                compatible=True,
                level="patch_drift" if has_minor or any(r[0] == "patch_drift" for r in results.values()) else "exact",
                warnings=[r[1] for r in results.values() if r[1]],
            )
```

**Compatibility Acceptance Criteria:**
- [ ] Semantic version parsing using `packaging.version`
- [ ] MAJOR drift blocks load in all modes
- [ ] MINOR drift blocks in strict_mode, warns otherwise
- [ ] PATCH drift always allowed with warning
- [ ] Missing dataset blocks load with clear error

**Cache/Registry Boundary Clarification (from Codex review):**
```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CACHE vs REGISTRY OWNERSHIP                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  DiskExpressionCache (libs/factors/cache.py)                            │
│  ├── Owns: Computed factor/signal values (intermediate results)         │
│  ├── Key generation: Factor computation layer generates keys            │
│  ├── Key format: {factor}:{date}:{version}:{snapshot_id}:{config_hash} │
│  ├── Eviction: TTL-based (7 days default)                               │
│  └── NOT owned by: Model registry                                        │
│                                                                          │
│  ModelRegistry (libs/models/registry.py)                                │
│  ├── Owns: Trained model artifacts (final outputs)                      │
│  ├── Key generation: Registry generates model_id                        │
│  ├── Key format: {model_type}:{semantic_version}                        │
│  ├── Eviction: Policy-based (staged: 30d, archived: 90d)               │
│  └── NOT owned by: Factor cache                                          │
│                                                                          │
│  Integration Point:                                                      │
│  └── ModelMetadata.dataset_version_ids REFERENCES cache keys            │
│      (allows cache invalidation when model data requirements change)    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**FastAPI Auth/ACL (from Codex review):**
- All registry endpoints require JWT service token
- Scopes: `model:read` (GET), `model:write` (POST/PUT), `model:admin` (DELETE)
- Audit log for all write operations

**E2E Corruption & Rollback Test Specification:**
```python
# tests/e2e/test_registry_resilience.py
@pytest.mark.e2e
def test_corruption_detection_and_rollback():
    """
    Full end-to-end test for corruption detection and automatic rollback.

    Scenario:
    1. Register and promote v1.0.0 (production)
    2. Register v1.1.0 with corrupted artifact (bad checksum)
    3. Attempt to promote v1.1.0
    4. Assert: promotion fails with ChecksumMismatchError
    5. Assert: v1.0.0 remains in production (no state change)
    6. Assert: v1.1.0 marked as 'failed' in registry
    7. Assert: alert metric 'model.validation.failed' emitted

    Recovery path:
    8. Re-register v1.1.0 with correct artifact
    9. Promote v1.1.0
    10. Assert: v1.1.0 now production, v1.0.0 archived
    """

@pytest.mark.e2e
def test_version_drift_detection_and_handling():
    """
    E2E test for dataset version drift detection.

    Scenario:
    1. Register model trained on crsp v1.2.3
    2. Update local CRSP to v1.3.0
    3. ProductionModelLoader.check_compatibility() called
    4. Assert (STRICT_VERSION_MODE=true): VersionDriftError raised
    5. Assert: circuit breaker increments failure count
    6. Assert: fallback to last-good model (or block if none)

    Rollback path:
    7. Rollback CRSP to v1.2.3 (or retrain model on v1.3.0)
    8. Retry load
    9. Assert: load succeeds, circuit breaker resets
    """
```

---

### T2.9: Covariance Synthetic Verification (Enhancement)
**Effort:** 1-2 days | **PR:** `feat(p4): covariance verification`
**Status:** Pending
**Priority:** P2 (Quality assurance for T2.2)
**Dependencies:** T2.2 (Covariance Estimation) - COMPLETE

> **Note:** This is an enhancement task from Gemini review to add synthetic data verification for the already-implemented covariance estimators.

**Problem:** Complex estimators (Ledoit-Wolf, Newey-West) are prone to subtle implementation errors. Need verification against known ground truth.

**Deliverables:**
- Synthetic covariance matrix generator with known properties
- Verification tests that estimators recover known matrices
- Property-based tests for edge cases

**Implementation:**
```python
# tests/libs/risk/test_covariance_synthetic.py
import numpy as np
from hypothesis import given, strategies as st

class SyntheticCovarianceGenerator:
    """Generate synthetic data with known covariance for verification."""

    @staticmethod
    def generate_known_covariance(
        n_factors: int = 5,
        n_days: int = 500,
        condition_number: float = 10.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Generate returns with known factor covariance.

        Returns:
        - true_cov: Known ground truth covariance matrix
        - returns: Simulated returns drawn from this covariance
        """
        # Generate positive definite covariance with controlled condition number
        # This ensures we know the "true" answer
        ...

    @staticmethod
    def generate_with_autocorrelation(
        n_factors: int = 5,
        n_days: int = 500,
        ar_coef: float = 0.3,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Generate returns with known autocorrelation for Newey-West testing."""
        ...


class TestCovarianceRecovery:
    """Verify estimators recover known covariance matrices."""

    def test_ledoit_wolf_recovers_known_cov(self):
        """Ledoit-Wolf should recover true covariance within tolerance."""
        true_cov, returns = SyntheticCovarianceGenerator.generate_known_covariance(
            n_factors=5, n_days=1000, condition_number=10.0
        )
        estimator = FactorCovarianceEstimator(config=CovarianceConfig())
        estimated_cov, shrinkage = estimator.estimate_with_shrinkage_from_returns(returns)

        # Frobenius norm relative error should be < 10%
        rel_error = np.linalg.norm(estimated_cov - true_cov) / np.linalg.norm(true_cov)
        assert rel_error < 0.10, f"Recovery error too high: {rel_error:.2%}"

    def test_newey_west_with_autocorrelation(self):
        """Newey-West should handle autocorrelated returns."""
        true_cov, returns = SyntheticCovarianceGenerator.generate_with_autocorrelation(
            n_factors=5, n_days=500, ar_coef=0.3
        )
        # Without HAC correction, standard errors are biased
        # With HAC correction, should recover true standard errors
        ...

    @given(st.integers(min_value=3, max_value=20))
    def test_positive_definite_for_any_dimension(self, n_factors: int):
        """Property test: output is always positive semi-definite."""
        _, returns = SyntheticCovarianceGenerator.generate_known_covariance(
            n_factors=n_factors, n_days=max(n_factors * 10, 100)
        )
        estimator = FactorCovarianceEstimator()
        cov = estimator.estimate_from_returns(returns)
        eigenvalues = np.linalg.eigvalsh(cov)
        assert all(ev >= -1e-10 for ev in eigenvalues), "Not positive semi-definite"
```

**Files to Create:**
- `tests/libs/risk/test_covariance_synthetic.py`
- `tests/libs/risk/synthetic_generators.py`

**Acceptance Criteria:**
- [ ] Synthetic covariance generator with controlled condition number
- [ ] Ledoit-Wolf recovery test (< 10% Frobenius error on 1000 samples)
- [ ] Newey-West autocorrelation handling test
- [ ] Property test: output always positive semi-definite
- [ ] Edge case: near-singular matrix handling
- [ ] Edge case: high condition number (ill-conditioned) handling

---

### T1.9: TAQ Storage Quota Management (Enhancement)
**Effort:** 1-2 days | **PR:** `feat(p4): taq quota management`
**Status:** Pending
**Priority:** P2 (Operational safety for T1.7)
**Dependencies:** T1.7 (TAQ Storage) - COMPLETE

> **Note:** This is an enhancement task from Gemini review to add disk usage quota and retention policy for TAQ storage.

**Problem:** Uncompressed tick data can rapidly exhaust disk space. Need quota enforcement and retention policies.

**Deliverables:**
- Disk usage monitoring for TAQ storage
- Configurable quota limits with alerts
- Retention policy enforcer (age-based, size-based)
- Cleanup CLI commands

**Implementation:**
```python
# libs/data_providers/taq_quota_manager.py
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TAQQuotaConfig:
    """Configuration for TAQ storage quotas."""
    max_total_gb: float = 500.0           # Total storage limit
    max_per_symbol_gb: float = 10.0       # Per-symbol limit
    warn_threshold_pct: float = 80.0      # Warn at 80% usage
    critical_threshold_pct: float = 95.0  # Block new writes at 95%
    retention_days: int = 365             # Keep data for 1 year
    min_free_space_gb: float = 50.0       # Minimum free disk space

class TAQQuotaManager:
    """Manage TAQ storage quotas and retention."""

    def __init__(self, storage_path: Path, config: TAQQuotaConfig):
        self.storage_path = storage_path
        self.config = config

    def get_usage_stats(self) -> TAQUsageStats:
        """
        Get current storage usage statistics.

        Returns:
        - total_gb: Total storage used
        - by_symbol: dict[symbol, size_gb]
        - by_tier: dict[tier, size_gb]
        - oldest_data: date of oldest data
        - newest_data: date of newest data
        """
        ...

    def check_quota(self, estimated_write_gb: float) -> QuotaCheckResult:
        """
        Check if write would exceed quota.

        Returns:
        - allowed: bool
        - current_usage_pct: float
        - message: str (warning or error)
        """
        ...

    def enforce_retention(self, dry_run: bool = True) -> RetentionReport:
        """
        Delete data older than retention_days.

        Returns report of what was/would be deleted.
        """
        ...

    def cleanup_by_size(
        self,
        target_free_gb: float,
        strategy: Literal["oldest_first", "largest_first"] = "oldest_first",
        dry_run: bool = True,
    ) -> CleanupReport:
        """
        Free up space by deleting data.

        Prioritizes Tier 3 (sample data) over Tier 1 (aggregates).
        """
        ...

    def emit_alerts(self, usage_stats: TAQUsageStats) -> list[Alert]:
        """Generate alerts for quota thresholds."""
        if usage_stats.usage_pct >= self.config.critical_threshold_pct:
            return [Alert(level="critical", message=f"TAQ storage at {usage_stats.usage_pct:.1f}%")]
        elif usage_stats.usage_pct >= self.config.warn_threshold_pct:
            return [Alert(level="warning", message=f"TAQ storage at {usage_stats.usage_pct:.1f}%")]
        return []
```

**CLI Commands:**
```bash
# Check current usage
python scripts/taq_quota.py status

# Dry-run retention enforcement
python scripts/taq_quota.py enforce-retention --dry-run

# Actually enforce retention
python scripts/taq_quota.py enforce-retention --execute

# Free up space (oldest first)
python scripts/taq_quota.py cleanup --target-free-gb 100 --dry-run
```

**Files to Create:**
- `libs/data_providers/taq_quota_manager.py`
- `scripts/taq_quota.py`
- `tests/libs/data_providers/test_taq_quota_manager.py`
- `docs/RUNBOOKS/taq-storage-management.md`

**Acceptance Criteria:**
- [ ] Disk usage calculation by symbol, tier, and total
- [ ] Quota check before new writes (block at critical threshold)
- [ ] Warning alerts at configurable threshold
- [ ] Age-based retention enforcement
- [ ] Size-based cleanup with tier prioritization
- [ ] Dry-run mode for all destructive operations
- [ ] CLI for operational management
- [ ] Integration with existing TAQStorageManager

---

## Track 3: Market Microstructure (T3.1-T3.3)

### T3.1: Microstructure Analytics
**Effort:** 4-5 days | **PR:** `feat(p4): microstructure analytics`
**Status:** ✅ Complete
**Priority:** P1 (Advanced analytics)
**Dependencies:** T1.7 (TAQ Storage), T1.8 (TAQ Query Interface) - MUST complete T1.8 before starting

**Problem:** Need realized volatility, intraday patterns, and order flow analytics for execution and alpha.

**⚠️ Architectural Constraint: Polars/DuckDB Boundary**

> **All TAQ feature generation (RV, VPIN, spread, depth) is implemented in Polars/DuckDB only.**
> Qlib receives daily aggregates via Parquet export. See [Qlib Integration Strategy](#qlib-integration-strategy).

**Rationale:**
- Avoids Pandas/Polars ↔ Qlib serialization overhead
- TAQ data volume too large for Qlib's standard interfaces
- Daily aggregates sufficient for factor models; intraday available for execution

**Output Schema (Multi-Horizon):**
```
data/analytics/microstructure/
├── rv_daily.parquet      # Daily RV → Qlib models
├── rv_30min.parquet      # 30-min RV → Execution analytics
├── rv_5min.parquet       # 5-min RV → Research only
├── vpin_daily.parquet    # Daily VPIN → Qlib models
└── spread_stats.parquet  # Daily spread/depth stats
```

**Deliverables:**
- Realized volatility calculation (5-min sampling)
- Intraday volatility patterns (U-shape analysis)
- HAR volatility forecasting model
- VPIN (Volume-synchronized PIN) calculation using Bulk Volume Classification (BVC)
- Spread and depth analysis
- **Multi-horizon Parquet exports** (daily, 30min, 5min)

**VPIN Trade Classification:**
Uses **Bulk Volume Classification (BVC)** method per Easley et al. (2012):
- Volume bars (not time bars) for synchronization
- Trade classification via normalized price change: V_buy = V * Phi((P - P_prev) / sigma)
- No need for quote-level data (more robust than Lee-Ready)

**Implementation:**

```python
# libs/analytics/microstructure.py
class MicrostructureAnalyzer:
    """Analyze market microstructure from tick data."""

    def __init__(self, taq_provider: TAQQueryProvider):
        self.taq = taq_provider

    def compute_realized_volatility(
        self,
        symbol: str,
        date: date,
        sampling_freq_minutes: int = 5
    ) -> float:
        """
        Compute realized volatility using fixed-interval sampling.

        Uses 5-minute returns by default (standard in literature).
        """
        ...

    def compute_vpin(
        self,
        symbol: str,
        date: date,
        bucket_size: int = 50  # Number of buckets
    ) -> pl.DataFrame:
        """
        Compute Volume-synchronized Probability of Informed trading.

        Returns DataFrame: [bucket, vpin, volume, imbalance]
        """
        ...

    def analyze_intraday_pattern(
        self,
        symbol: str,
        start_date: date,
        end_date: date
    ) -> pl.DataFrame:
        """
        Analyze U-shape intraday volatility pattern.

        Returns DataFrame: [time_bucket, avg_volatility, avg_spread, avg_volume]
        """
        ...

# libs/analytics/volatility.py
class HARVolatilityModel:
    """
    Heterogeneous Autoregressive (HAR) model for volatility forecasting.

    RV_t+1 = c + b_d*RV_t + b_w*RV_t^w + b_m*RV_t^m + e
    """

    def fit(
        self,
        realized_vol: pl.DataFrame  # [date, rv]
    ) -> None:
        """Fit HAR model to realized volatility series."""
        ...

    def forecast(
        self,
        horizon_days: int = 1
    ) -> float:
        """Forecast volatility for given horizon."""
        ...
```

**Files to Create:**
- `libs/analytics/microstructure.py`
- `libs/analytics/volatility.py`
- `tests/libs/analytics/test_microstructure.py`
- `tests/libs/analytics/test_volatility.py`
- `docs/CONCEPTS/microstructure.md`
- `docs/CONCEPTS/realized-volatility.md`

**PIT/Versioning Requirements:**
- Microstructure outputs tagged with source TAQ `dataset_version_id`
- Realized volatility series reproducible given same TAQ version

**Acceptance Criteria:**
- [ ] Realized volatility with configurable sampling frequency
- [ ] VPIN calculation with volume bucketing using BVC method
- [ ] Intraday pattern analysis
- [ ] HAR volatility model
- [ ] Spread and depth statistics
- [ ] Outputs tagged with TAQ dataset_version_id
- [ ] >90% test coverage

---

### T3.2: Execution Quality Analysis
**Effort:** 3-4 days | **PR:** `feat(p4): execution quality`
**Status:** Pending
**Dependencies:** T3.1, T1.8 (TAQ Query Interface)

**Problem:** Need to analyze trade execution quality against standard benchmarks.

**Deliverables:**
- Execution benchmarks (VWAP, TWAP, arrival price)
- Implementation shortfall calculation
- Optimal execution timing recommendations
- Integration with execution_gateway fills

**Implementation:**

```python
# libs/analytics/execution_quality.py
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal

# NEW (from review): Fills Schema Contract for execution_gateway integration
class Fill(BaseModel):
    """Single fill from execution_gateway.

    This schema defines the contract between execution_gateway and
    the execution quality analyzer. Any changes MUST be coordinated
    with execution_gateway team.
    """
    timestamp: datetime = Field(..., description="Fill execution timestamp (UTC)")
    price: float = Field(..., gt=0, description="Fill price per share")
    quantity: int = Field(..., gt=0, description="Number of shares filled")
    exchange: str | None = Field(None, description="Exchange where fill occurred")
    order_id: str = Field(..., description="Parent order ID from execution_gateway")
    client_order_id: str = Field(..., description="Idempotent client order ID")

    # Optional fields for advanced analysis
    liquidity_flag: Literal["add", "remove"] | None = Field(
        None, description="Whether fill added or removed liquidity"
    )
    rebate_bps: float | None = Field(None, description="Exchange rebate/fee in bps")


class FillBatch(BaseModel):
    """Batch of fills for a single order execution.

    Provided by execution_gateway for quality analysis.
    """
    symbol: str
    side: Literal["buy", "sell"]
    fills: list[Fill]
    decision_time: datetime = Field(..., description="When order decision was made")
    submission_time: datetime = Field(..., description="When order was submitted to broker")
    total_target_qty: int = Field(..., description="Total quantity intended to fill")

    @property
    def total_filled_qty(self) -> int:
        return sum(f.quantity for f in self.fills)

    @property
    def avg_fill_price(self) -> float:
        if not self.fills:
            return 0.0
        total_value = sum(f.price * f.quantity for f in self.fills)
        return total_value / self.total_filled_qty


@dataclass
class ExecutionAnalysis:
    """Results of execution quality analysis."""
    arrival_price: float
    execution_price: float
    vwap_benchmark: float
    twap_benchmark: float
    implementation_shortfall_bps: float
    market_impact_bps: float
    timing_cost_bps: float
    # NEW: Additional metrics
    fill_rate: float  # total_filled_qty / total_target_qty
    execution_duration_seconds: float
    avg_fill_latency_ms: float | None  # If timing data available


class ExecutionQualityAnalyzer:
    """Analyze execution quality against benchmarks."""

    def __init__(self, taq_provider: TAQQueryProvider):
        self.taq = taq_provider

    def analyze_execution(
        self,
        fill_batch: FillBatch,  # NEW: Typed contract instead of loose dict
    ) -> ExecutionAnalysis:
        """
        Analyze execution quality for a series of fills.

        Computes:
        - Implementation shortfall vs arrival price
        - VWAP slippage
        - TWAP slippage
        - Market impact estimate
        """
        ...

    def compute_vwap(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> float:
        """Compute VWAP over time window."""
        ...

    def recommend_execution_window(
        self,
        symbol: str,
        target_date: date,
        order_size_shares: int
    ) -> dict:
        """
        Recommend optimal execution window based on liquidity patterns.

        Returns timing recommendation with expected cost estimate.
        """
        ...
```

**Files to Create:**
- `libs/analytics/execution_quality.py`
- `tests/libs/analytics/test_execution_quality.py`
- `docs/CONCEPTS/execution-analysis.md`

**Acceptance Criteria:**
- [ ] VWAP and TWAP benchmark computation
- [ ] Implementation shortfall calculation
- [ ] Market impact estimation
- [ ] Integration with execution_gateway order data
- [ ] >90% test coverage

**Fills Schema Contract (from review):**
- [ ] `Fill` and `FillBatch` Pydantic models defined in shared location
- [ ] Schema versioning strategy documented (for future changes)
- [ ] execution_gateway exports fills in `FillBatch` format
- [ ] Validation tests for schema edge cases (partial fills, multi-exchange)
- [ ] Contract documented in `docs/CONCEPTS/execution-analysis.md`

**Extended Fills Schema (from Codex review):**
```python
# libs/analytics/execution_quality.py - Extended fill handling

class FillStatus(str, Enum):
    """Fill lifecycle status."""
    FILLED = "filled"           # Normal fill
    PARTIAL = "partial"         # Partial fill (more fills expected)
    CANCELLED = "cancelled"     # Order cancelled before full fill
    AMENDED = "amended"         # Price/qty amended (ref to previous)

class ExtendedFill(BaseModel):
    """Extended fill with lifecycle and timing details."""

    # Core fields (from basic Fill)
    timestamp: datetime = Field(..., description="Fill execution timestamp (UTC)")
    price: float = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    order_id: str
    client_order_id: str

    # Lifecycle fields (from Codex review)
    status: FillStatus = FillStatus.FILLED
    amends_fill_id: str | None = Field(None, description="ID of amended fill (if status=amended)")
    cancel_reason: str | None = Field(None, description="Reason if cancelled")

    # Venue details
    exchange: str | None = None
    venue_order_id: str | None = Field(None, description="Exchange's internal order ID")
    liquidity_flag: Literal["add", "remove"] | None = None

    # Cost details
    fee_amount: float = Field(0.0, description="Total fee/rebate (negative = rebate)")
    fee_currency: str = "USD"

    # Timing details (for latency analysis)
    broker_received_at: datetime | None = Field(None, description="When broker received order")
    exchange_ack_at: datetime | None = Field(None, description="Exchange acknowledgment time")
    fill_reported_at: datetime | None = Field(None, description="When fill was reported to us")


class FillBatchExtended(BaseModel):
    """Extended fill batch with clock sync and validation."""

    symbol: str
    side: Literal["buy", "sell"]
    fills: list[ExtendedFill]
    decision_time: datetime
    submission_time: datetime
    total_target_qty: int

    # Clock sync (from Codex review)
    clock_source: Literal["ntp", "exchange", "local"] = "ntp"
    clock_drift_ms: float | None = Field(None, description="Estimated clock drift at fill time")

    # Symbol mapping (from Codex review)
    exchange_symbol: str | None = Field(None, description="Symbol as used on exchange (may differ)")
    symbol_mapping_version: str | None = Field(None, description="Version of symbol mapping used")

    @validator("fills")
    def validate_fill_sequence(cls, fills: list[ExtendedFill]) -> list[ExtendedFill]:
        """Validate fills are in timestamp order and quantities make sense."""
        if not fills:
            return fills
        # Check timestamp ordering
        for i in range(1, len(fills)):
            if fills[i].timestamp < fills[i-1].timestamp:
                raise ValueError(f"Fills not in timestamp order at index {i}")
        return fills

    @property
    def has_cancels(self) -> bool:
        return any(f.status == FillStatus.CANCELLED for f in self.fills)

    @property
    def has_amendments(self) -> bool:
        return any(f.status == FillStatus.AMENDED for f in self.fills)

    @property
    def net_filled_qty(self) -> int:
        """Net filled quantity (excludes cancelled/amended)."""
        return sum(
            f.quantity for f in self.fills
            if f.status in (FillStatus.FILLED, FillStatus.PARTIAL)
        )
```

**Timezone & Clock Sync Requirements (from Codex review):**
- All timestamps MUST be UTC (enforce via Pydantic validator)
- Arrival price timestamp: Use `decision_time` for IS calculation
- If `broker_received_at` available, use for broker latency analysis
- Clock drift >100ms triggers warning in analysis output
- Symbol mapping: Some symbols differ between data sources (SPY vs SPY.P)

**Arrival Timestamp Source:**
- Primary: `decision_time` from signal_service (when signal was generated)
- Fallback: `submission_time` - 50ms (estimated decision-to-submit latency)
- Document source in analysis output for auditability

**Extended Acceptance Criteria (from Codex review):**
- [ ] Handle partial fills (track cumulative fill progress)
- [ ] Handle cancelled orders (exclude from quality metrics)
- [ ] Handle amended fills (use latest price/qty for analysis)
- [ ] Clock drift detection and warning
- [ ] Symbol mapping validation (execution_gateway symbol → TAQ symbol)
- [ ] Timezone enforcement (reject non-UTC timestamps)
- [ ] Join with T3.1 microstructure spreads for realistic IS calculation

---

### T3.3: Event Study Framework
**Effort:** 3-4 days | **PR:** `feat(p4): event study framework`
**Status:** Pending
**Dependencies:** T1.3 (CRSP) - COMPLETE, T1.5 (Fama-French), Corporate Actions Calendar, Earnings Calendar

**Problem:** Need framework for analyzing stock price reactions to events (earnings, index changes).

**Deliverables:**
- Cumulative abnormal returns (CAR) calculation
- Post-earnings announcement drift (PEAD) analysis
- Index rebalance effect analysis
- Statistical significance testing

**Implementation:**

```python
# libs/analytics/event_study.py
@dataclass
class EventStudyResult:
    """Results of event study analysis."""
    event_date: date
    symbol: str
    car_pre: float      # CAR before event
    car_post: float     # CAR after event
    car_window: float   # CAR over full window
    abnormal_volume: float
    t_statistic: float
    is_significant: bool

class EventStudyFramework:
    """Framework for event study analysis."""

    def __init__(
        self,
        crsp_provider: CRSPLocalProvider,
        fama_french_provider: FamaFrenchLocalProvider
    ):
        self.crsp = crsp_provider
        self.ff = fama_french_provider

    def compute_car(
        self,
        symbol: str,
        event_date: date,
        pre_window: int = 5,
        post_window: int = 20,
        estimation_window: int = 120
    ) -> EventStudyResult:
        """
        Compute cumulative abnormal return around event.

        Uses market model to estimate expected returns,
        then computes abnormal returns as actual - expected.
        """
        ...

    def analyze_pead(
        self,
        earnings_events: pl.DataFrame,  # [symbol, event_date, surprise_pct]
        holding_period_days: int = 60
    ) -> pl.DataFrame:
        """
        Analyze post-earnings announcement drift.

        Returns DataFrame: [surprise_quintile, car_60d, t_stat]
        """
        ...

    def analyze_index_rebalance(
        self,
        index_changes: pl.DataFrame,  # [symbol, date, action (add/drop)]
        pre_window: int = 20,
        post_window: int = 20
    ) -> pl.DataFrame:
        """
        Analyze price impact of index additions/deletions.

        Returns DataFrame: [action, car_pre, car_post, avg_volume_change]
        """
        ...
```

**Files to Create:**
- `libs/analytics/event_study.py`
- `tests/libs/analytics/test_event_study.py`
- `docs/CONCEPTS/event-studies.md`

**Acceptance Criteria:**
- [ ] Market model estimation for expected returns
- [ ] CAR calculation with configurable windows
- [ ] PEAD analysis by earnings surprise quintile
- [ ] Index rebalance effect analysis
- [ ] T-statistics and significance testing
- [ ] >90% test coverage

**Methodology Specification (from Codex review):**
```python
# libs/analytics/event_study.py - Extended methodology

class ExpectedReturnModel(str, Enum):
    """Models for expected return estimation."""
    MARKET_MODEL = "market_model"       # R_i = alpha + beta * R_m + epsilon
    MEAN_ADJUSTED = "mean_adjusted"     # R_i = mean(R_i) + epsilon
    FF3 = "fama_french_3"               # 3-factor model
    FF5 = "fama_french_5"               # 5-factor model


class SignificanceTest(str, Enum):
    """Statistical tests for abnormal returns."""
    T_TEST = "t_test"                   # Standard parametric t-test
    PATELL = "patell"                   # Standardized residual test
    BMP = "bmp"                         # Boehmer-Musumeci-Poulsen (robust to cross-correlation)
    SIGN = "sign"                       # Non-parametric sign test
    RANK = "rank"                       # Non-parametric rank test (Corrado)


class EventStudyConfig(BaseModel):
    """Configuration for event study methodology."""

    # Estimation window (from Codex review)
    estimation_window_days: int = Field(
        120,
        ge=60,
        le=252,
        description="Trading days for model estimation (min 60 for stability)"
    )
    gap_days: int = Field(
        10,
        ge=0,
        description="Gap between estimation and event window (prevents leakage)"
    )

    # Event window
    pre_event_days: int = Field(5, ge=0)
    post_event_days: int = Field(20, ge=1)

    # Model selection (from Codex review)
    expected_return_model: ExpectedReturnModel = ExpectedReturnModel.MARKET_MODEL
    significance_test: SignificanceTest = SignificanceTest.BMP  # Robust default

    # Multiple testing (from Codex review)
    multiple_testing_correction: Literal["none", "bonferroni", "fdr", "holm"] = "fdr"
    significance_level: float = Field(0.05, gt=0, lt=1)

    # Overlap handling (from Codex review)
    handle_overlapping_events: Literal["drop_later", "drop_both", "aggregate", "allow"] = "drop_later"
    min_days_between_events: int = Field(
        10,
        description="Events closer than this are considered overlapping"
    )


class EventStudyFrameworkExtended:
    """Extended event study framework with methodology options."""

    def __init__(
        self,
        crsp_provider: CRSPLocalProvider,
        ff_provider: FamaFrenchLocalProvider,
        config: EventStudyConfig | None = None,
    ):
        self.crsp = crsp_provider
        self.ff = ff_provider
        self.config = config or EventStudyConfig()

    def estimate_expected_returns(
        self,
        symbol: str,
        estimation_end: date,
    ) -> ExpectedReturnParams:
        """
        Estimate expected return model parameters.

        Uses config.estimation_window_days ending at estimation_end,
        with config.gap_days before the event window starts.
        """
        ...

    def compute_abnormal_returns(
        self,
        symbol: str,
        event_date: date,
        model_params: ExpectedReturnParams,
    ) -> pl.DataFrame:
        """
        Compute abnormal returns: AR_t = R_t - E[R_t]

        Returns DataFrame: [date, return, expected_return, abnormal_return, t_from_event]
        """
        ...

    def test_significance(
        self,
        abnormal_returns: pl.DataFrame,
        test: SignificanceTest,
    ) -> SignificanceResult:
        """
        Test statistical significance of abnormal returns.

        BMP test (default): Robust to cross-sectional correlation
        Patell test: Standardized by estimation-period variance
        """
        ...

    def correct_multiple_testing(
        self,
        p_values: list[float],
        method: str,
    ) -> list[float]:
        """
        Apply multiple testing correction.

        FDR (default): Benjamini-Hochberg false discovery rate control
        Bonferroni: Conservative, controls family-wise error rate
        Holm: Step-down Bonferroni (more powerful)
        """
        ...

    def filter_overlapping_events(
        self,
        events: pl.DataFrame,
    ) -> pl.DataFrame:
        """
        Handle overlapping events per config.handle_overlapping_events.

        Returns filtered events with 'overlap_flag' column.
        """
        ...
```

**Calendar Dependencies (from Codex review):**
- **Earnings Calendar**: Required for PEAD analysis
  - Source: Compustat or external provider (Alpha Vantage, etc.)
  - Fields: `symbol`, `announcement_date`, `fiscal_quarter`, `eps_actual`, `eps_estimate`
- **Corporate Actions Calendar**: Required for index changes, splits, dividends
  - Source: CRSP or external provider
  - Fields: `symbol`, `date`, `action_type`, `details`
- **Trading Calendar**: US market holidays for window calculations
  - Source: `pandas_market_calendars` or equivalent

**Extended Acceptance Criteria (from Codex review):**
- [ ] Configurable estimation window (60-252 days)
- [ ] Gap between estimation and event window (default 10 days)
- [ ] Market model AND Fama-French model options
- [ ] BMP robust significance test (default)
- [ ] Multiple testing correction (FDR default)
- [ ] Overlapping event detection and handling
- [ ] Earnings calendar integration for PEAD
- [ ] Trading calendar for window calculations
- [ ] Daily horizon only (intraday deferred to future)

---

## Execution Timeline

**Note:** Timeline revised per review feedback. Workload rebalanced: T2.7 and T2.8 moved to Developer B. Week 8 split across Weeks 8-9.

### Week 5: Factor Foundation + TAQ Storage
| Developer | Task | Effort | Deliverable |
|-----------|------|--------|-------------|
| A | T2.1 Factor Builder | 4-5d | Factor construction, 5 factors |
| B | T1.7 TAQ Storage | 3-4d | Tiered storage, aggregates download |

### Week 6: Covariance + TAQ Query
| Developer | Task | Effort | Deliverable |
|-----------|------|--------|-------------|
| A | T2.2 Covariance | 4-5d | Factor returns, shrinkage |
| B | T1.8 TAQ Query | 2-3d | Query interface complete |

### Week 7: Risk Analytics + Microstructure
| Developer | Task | Effort | Deliverable |
|-----------|------|--------|-------------|
| A | T2.3 Risk Analytics | 3-4d | Barra model, VaR, CVaR |
| B | T3.1 Microstructure | 4-5d | RV, VPIN, HAR model |

### Week 8: Optimizer + Execution Quality
| Developer | Task | Effort | Deliverable |
|-----------|------|--------|-------------|
| A | T2.4 Optimizer | 3-4d | Mean-var optimization, stress testing |
| B | T3.2 Execution Quality | 3-4d | VWAP/IS, market impact |

### Week 9: Alpha + Event Study
| Developer | Task | Effort | Deliverable |
|-----------|------|--------|-------------|
| A | T2.5 Alpha Framework, T2.6 Alpha Advanced | 5-7d | IC analysis, overfitting detection |
| B | T3.3 Event Study | 3-4d | CAR/PEAD analysis |

### Week 10: Attribution + Registry + Integration
| Developer | Task | Effort | Deliverable |
|-----------|------|--------|-------------|
| A | T2.7 Attribution, Integration testing | 3-4d + buffer | FF regression, cross-track validation |
| B | T2.8 Model Registry | 3-4d | Model serialization, deployment |

**Note:** T2.7 (Attribution) moved back to Dev A in Week 10 to balance workload. Dev A has buffer capacity from integration testing.

### Integration & Buffer Policy

**Shared Buffer (3 days):**
- Days 1-2: Cross-track integration testing (signal_service ↔ Model Registry ↔ risk_manager)
- Day 3: PIT regression test full run + performance benchmark validation

**Integration Freeze Date:** End of Week 9, Friday 18:00 UTC
- All feature code complete and merged
- Only bug fixes and test additions after freeze
- No new features or refactors in Week 10

**Week 9-10 Risk Mitigation:**
- If T2.5+T2.6 exceed 7 days: defer T2.6 advanced analytics (combiner, overfitting) to post-Phase 2
- PIT regression tests run nightly starting Week 7 to catch issues early
- Pre-stage test fixtures and data for integration tests in Week 8

**Dev A Timeline Contingency (25-32 days in ~30 working days):**

The Dev A workload is aggressive. Contingency plan:

| Trigger | Action | Impact |
|---------|--------|--------|
| T2.4 (optimizer) exceeds 4 days | Simplify to box constraints only, defer sector/factor constraints | -1 day |
| T2.5+T2.6 exceeds 7 days | Defer T2.6 entirely (combiner, overfitting detection) to Phase 3 | -2-3 days |
| T2.7 cannot start by Week 10 Day 1 | Move T2.7 to Dev B (swap with integration testing) | Rebalances load |
| Integration tests blocked | Parallelize: Dev A does T2.7, Dev B does integration | No net delay |

**Early Warning Signals:**
- End of Week 7: If T2.3 not complete, trigger T2.6 deferral decision
- End of Week 8: If T2.4 not complete, simplify optimizer scope
- End of Week 9: If T2.6 not complete, defer remaining items

**Scope Reduction Priority (if needed):**
1. **First cut:** T2.6 overfitting detection (nice-to-have for Phase 2)
2. **Second cut:** T2.6 alpha combiner (can use equal weights initially)
3. **Third cut:** T2.4 sector constraints (box constraints sufficient for MVP)
4. **Never cut:** T2.1-T2.3 (core risk model), T2.5 (alpha framework), T2.8 (registry)

**Preemptive De-Scoping (Applied at Start):**
To reduce Dev A overrun risk, the following items are **deferred to Phase 3** by default:
- T2.6 overfitting detection (Bonferroni, FDR correction) - defer unless ahead of schedule
- T2.4 sector exposure constraints - use box constraints only for MVP
- T2.7 conditional attribution (up/down markets) - basic FF regression only

This reduces Dev A core scope to ~22-28 days, providing ~5 days buffer.

**Mid-Week 6 Checkpoint (Go/No-Go):**

| Criteria | Met? | Action if NOT Met |
|----------|------|-------------------|
| T2.1 Factor Builder complete and merged | Yes/No | STOP: Cannot proceed without factors |
| T2.2 Covariance started | Yes/No | Reassess timeline, consider task swap with Dev B |
| TAQ aggregates downloading successfully | Yes/No | Trigger WRDS fallback plan |
| PIT contract tests passing | Yes/No | Fix P4T1 integration before proceeding |

**Decision Matrix at Mid-Week 6:**
- If T2.1 NOT complete: **Escalate** - critical path blocked
- If T2.2 NOT started: **Swap** T2.7 to Dev B, give Dev A more T2.2 time
- If behind by >2 days: **Cut** T2.6 entirely, simplify T2.4

**Go/No-Go Checkpoints:**
| Checkpoint | Date | Criteria |
|------------|------|----------|
| TAQ aggregates available | Week 5 Day 1 | WRDS API test passes |
| Factor builder baseline | Week 5 Day 5 | 3+ factors compute correctly |
| Covariance stability | Week 6 Day 5 | PSD check passes on 500 stocks |
| Integration freeze | Week 9 Day 5 | All feature PRs merged |
| Phase complete | Week 10 Day 5 | All acceptance criteria met |

---

## Performance Benchmarks

**Hardware Assumptions:** 8-core CPU, 32GB RAM, SSD storage, 100Mbps network

| Component | Benchmark | Target | Data Volume |
|-----------|-----------|--------|-------------|
| TAQ download | 1 month aggregates, 500 stocks | <30 min | ~5GB |
| TAQ query | 1 day ticks, 10 stocks | <5 sec | ~100MB |
| Factor computation | All factors, 3000 stocks | <60 sec | ~500MB |
| Covariance estimation | 500 stocks, 252 days | <10 sec | ~50MB |
| Alpha backtest | 5 years, 3000 stocks | <5 min | ~2GB |
| Optimizer solve | 500 stocks, 10 factors | <5 sec | ~10MB |
| Model registry load | Risk model | <100 ms | ~50MB |

**API Load SLAs (Model Registry & risk_manager):**

| Endpoint | Throughput | p50 Latency | p95 Latency | Concurrent Readers |
|----------|------------|-------------|-------------|-------------------|
| GET /models/{type}/current | 100 req/s | <10 ms | <50 ms | 50 |
| GET /models/{type}/{version} | 50 req/s | <20 ms | <100 ms | 20 |
| POST /models/{type}/validate | 10 req/s | <500 ms | <2s | 5 |
| POST /risk/covariance | 20 req/s | <50 ms | <200 ms | 10 |
| POST /risk/var | 50 req/s | <20 ms | <100 ms | 20 |
| POST /risk/pre-trade-check | 100 req/s | <10 ms | <50 ms | 50 |

**Load Test Specification:**
```python
# tests/load/test_registry_load.py
@pytest.mark.load
def test_registry_current_model_throughput():
    """Verify /current endpoint handles 100 req/s sustained."""
    # Use locust or k6 to generate load
    # Assert: p95 < 50ms, 0% errors at 100 req/s for 5 min

@pytest.mark.load
def test_concurrent_readers():
    """Verify 50 concurrent readers don't block."""
    # Spawn 50 threads, each calling GET /current
    # Assert: all complete within 100ms, no timeouts
```

**Chaos/Stress Tests:**
- TAQ: Rate limiter under 10x sustained load
- Registry: 10 concurrent writers (expect serialization)
- Registry: Corrupt artifact mid-write (expect rejection)
- Registry: DuckDB lock contention (expect graceful wait)
- Loader: Version flip during active request (expect atomic swap)
- Covariance: Near-singular matrix (expect regularization)
- Optimizer: Infeasible constraints (expect graceful error)

---

## Test Automation & CI Mapping

**Test Harness Configuration:**
```python
# tests/conftest.py fixtures
@pytest.fixture
def pit_test_data():
    """Synthetic dataset with known T vs T-1 differences."""
    return load_fixture("pit_regression_data.parquet")  # ~50MB

@pytest.fixture
def perf_benchmark_data():
    """Sampled real data for performance tests."""
    return load_fixture("benchmark_500_stocks_1y.parquet")  # ~200MB
```

**CI Job Mapping:**

| Test Category | Job Name | Trigger | Owner | Data | Threshold |
|---------------|----------|---------|-------|------|-----------|
| Unit tests | `test-unit` | Every PR | All devs | Mocks | 100% pass |
| Integration tests | `test-integration` | Every PR | All devs | Fixtures | 100% pass |
| PIT regression | `test-pit-weekly` | Weekly (Sun 02:00) | Dev A | Recorded snapshots | Outputs must differ T vs T-1 |
| TAQ perf | `bench-taq` | Nightly | Dev B | 1 month / 500 stocks | <30 min download |
| Factor perf | `bench-factor` | Nightly | Dev A | 3000 stocks | <60 sec |
| Covariance perf | `bench-covariance` | Nightly | Dev A | 500 stocks / 252 days | <10 sec |
| Alpha backtest perf | `bench-alpha` | Nightly | Dev A | 5 years / 3000 stocks | <5 min |
| Registry stress | `stress-registry` | Weekly | Dev B | Concurrent writers | Serialization works |
| Hot-reload soak | `soak-hot-reload` | Weekly | Dev B | Version flip loop | <100ms swap, no drops |
| risk_manager load | `load-risk-manager` | Weekly | Dev A | Burst orders | <50ms pre-trade check |

**CI Pipeline Structure:**
```yaml
# .github/workflows/p4t2-tests.yml
jobs:
  unit-integration:
    runs-on: ubuntu-latest
    steps: [checkout, setup-python, pip install, pytest tests/ -m "not slow"]

  nightly-benchmarks:
    runs-on: self-hosted  # Needs more resources
    schedule: "0 3 * * *"  # 03:00 UTC daily
    steps: [checkout, setup, pytest tests/ -m "benchmark" --benchmark-json=results.json]

  weekly-pit-regression:
    runs-on: self-hosted
    schedule: "0 2 * * 0"  # 02:00 UTC Sunday
    steps: [checkout, setup, pytest tests/ -m "pit_regression"]

  weekly-stress:
    runs-on: self-hosted
    schedule: "0 4 * * 0"  # 04:00 UTC Sunday
    steps: [checkout, setup, pytest tests/ -m "stress"]
```

**Performance Runner Gating:**
```yaml
# Pre-flight check for self-hosted runner availability
# Run BEFORE any perf/stress jobs to fail fast
check-perf-runner:
  runs-on: ubuntu-latest
  steps:
    - name: Check self-hosted runner available
      run: |
        # Query GitHub API for runner status
        RUNNER_STATUS=$(gh api repos/$REPO/actions/runners --jq '.runners[] | select(.name=="perf-runner") | .status')
        if [ "$RUNNER_STATUS" != "online" ]; then
          echo "::warning::Self-hosted perf runner offline, skipping perf jobs"
          exit 78  # Neutral exit - skip but don't fail
        fi
```

**Fallback if Self-Hosted Runner Unavailable:**
- **Scale-down mode:** Run benchmarks on ubuntu-latest with reduced dataset (100 stocks, 1 month)
- **Skip mode:** Mark perf jobs as skipped, log warning, proceed with unit/integration only
- **Alert:** Emit `ci.perf_runner.unavailable` metric for monitoring

**Fixture Pre-provisioning:**
- `tests/fixtures/benchmark_500_stocks_1y.parquet` (~200MB) - stored in Git LFS
- `tests/fixtures/pit_regression_data.parquet` (~50MB) - stored in Git LFS
- Pre-download in setup step: `git lfs pull --include="tests/fixtures/*.parquet"`

**Contract Tests (signal_service & risk_manager):**
```python
# tests/integration/test_service_contracts.py

@pytest.mark.contract
def test_signal_service_model_polling(mock_registry):
    """Verify signal_service correctly polls and hot-reloads."""
    # 1. Start with v1.0.0
    # 2. Registry returns v1.1.0
    # 3. Assert signal_service loads v1.1.0 within 60s
    # 4. Assert old model cleanly released

@pytest.mark.contract
def test_risk_manager_covariance_consumption(mock_registry, mock_risk_model):
    """Verify risk_manager correctly uses registry-loaded covariance."""
    # 1. Load risk_model from registry
    # 2. Call compute_var with test portfolio
    # 3. Assert result matches expected VaR

@pytest.mark.contract
def test_circuit_breaker_on_load_failure(mock_registry):
    """Verify circuit breaker triggers on repeated load failures."""
    # 1. Mock registry to return corrupt artifact
    # 2. Attempt 3 loads
    # 3. Assert circuit breaker state = TRIPPED
```

## Success Metrics

| Metric | Target | Status |
|--------|--------|--------|
| **Factor coverage** | 5+ canonical factors | Pending |
| **Risk model R²** | >60% variance explained | Pending |
| **VaR accuracy** | <5% exceedance at 95% | Pending |
| **Alpha IC** | >0.03 for composite | Pending |
| **Optimization speed** | <5s for 500 stocks | Pending |
| **RV accuracy** | Match published benchmarks | Pending |
| **Test coverage** | >90% | Pending |

---

## Dependencies

### From P4T1 (COMPLETE):
- T1.2 WRDS Sync Manager (for T1.7 TAQ)
- T1.3 CRSP Local Provider (for T2.1, T3.3)
- T1.4 Compustat Local Provider (for T2.1)
- T1.5 Fama-French Local Provider (for T2.7, T3.3)
- T1.6 Dataset Versioning (for T2.8 model versioning)

### External Libraries:
- cvxpy - Convex optimization solver
- scikit-learn - Ledoit-Wolf estimator
- statsmodels - Regression, HAC standard errors
- numpy/scipy - Linear algebra

---

## Risk Factors

| Risk | Mitigation |
|------|------------|
| TAQ data volume | Tiered storage, aggressive aggregation |
| Covariance numerical instability | Shrinkage estimators, regularization |
| Look-ahead bias in alpha research | Strict point-in-time validation |
| WRDS query limits | Rate limiting, cost estimation |
| Optimization solver failures | Constraint relaxation, fallback heuristics |
| Late Model Registry delivery | Manual fallback: load from known file path for integration testing |
| WRDS aggregates unavailable | Verify WRDS API supports pre-computed download; fallback to query-based |

---

## ADR Scope Note

The following implementation details will be specified in ADRs created during implementation:
- **ADR-0020 (Factor Model):** pin_version call sites, factor definition versioning, PIT regression test harness
- **ADR-0021 (Risk Model):** covariance near-singular handling, optimizer constraint relaxation
- **ADR-0022 (Model Deployment):** runtime env capture (Python version, deps hash), GC job cron spec, backup/restore procedures, DuckDB schema migration, signal_service auth/timeout/backoff config

This planning document defines WHAT to build. ADRs define HOW to build it.

---

## Known Limitations & Tech Debt

### TD-001: Factor Builder `snapshot_date` Not Implemented (Phase 3)

**Location:** `libs/alpha/factor_builder.py:118-122`

**Current Behavior:**
```python
if snapshot_date is not None:
    raise NotImplementedError(
        "snapshot_date for point-in-time factor retrieval is Phase 3 scope. "
        "Requires integration with DatasetVersionManager.query_as_of()."
    )
```

**Impact:**
- Factor builder currently only supports current-date factor retrieval
- Cannot perform point-in-time factor lookups for backtesting reproducibility
- Does not affect production forward-looking factor generation

**Why Deferred:**
- Core factor building functionality works for research and production use cases
- PIT factor retrieval requires deeper integration with DatasetVersionManager
- T1.6 versioning infrastructure exists but factor-level PIT requires additional work

**Resolution Path (Phase 3):**
1. Add `snapshot_date` parameter handling in `_build_factor_expr()`
2. Integrate with `DatasetVersionManager.query_as_of()` for historical data paths
3. Add PIT test coverage for factor builder
4. Estimated effort: 2-3 days

**Workaround:**
For backtesting with PIT compliance, use the data provider layer directly with `as_of` parameters before passing to factor builder.

---

## Related Documents

- [P4T1_TASK.md](./P4T1_TASK.md) - Data Infrastructure (Dependency - COMPLETE)
- [P4_PLANNING.md](./P4_PLANNING.md) - Overall P4 planning
- [docs/ADRs/ADR-016-data-provider-protocol.md](../ADRs/ADR-016-data-provider-protocol.md) - Provider patterns

---

**Last Updated:** 2025-12-09
**Status:** ✅ Complete (Ready for PR)
**Next Step:** Final CI validation and PR creation
