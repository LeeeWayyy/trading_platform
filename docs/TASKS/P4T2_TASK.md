# P4T2: Phase 2 - Analytics Infrastructure

**Task ID:** P4T2
**Phase:** P4 (Advanced Features & Research Infrastructure)
**Timeline:** Phase 2 - Analytics (Weeks 5-10, 6 weeks with buffer)
**Priority:** P0 - Core research infrastructure for alpha development
**Estimated Effort:** 39-49 days (13 subtasks across 3 parallel tracks)
**Status:** Approved (v1.9 - Gemini ✓ Codex ✓ - Ready for implementation)
**Created:** 2025-12-07
**Last Updated:** 2025-12-07 (v1.9 - Approved by Gemini + Codex after 9 review iterations)

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
**Status:** Pending
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
- [ ] **Performance test**: Download 1 month of aggregates for 500 stocks in <30 minutes
- [ ] **Lock contention test**: Simulated dual writers correctly blocked
- [ ] >90% test coverage

---

### T1.8: TAQ Query Interface
**Effort:** 2-3 days | **PR:** `feat(p4): taq query interface`
**Status:** Pending
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
**Status:** Pending
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

---

### T2.2: Risk Model - Covariance Estimation
**Effort:** 4-5 days | **PR:** `feat(p4): factor covariance estimation`
**Status:** Pending
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
**Status:** Pending
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
**Status:** Pending
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
**Effort:** 3-4 days | **PR:** `feat(p4): alpha framework`
**Status:** Pending
**Dependencies:** T2.1

**Deliverables:**
- Alpha signal definition framework
- Point-in-time backtesting engine
- IC and ICIR analysis
- Alpha decay curve analysis

**Files to Create:**
- `libs/alpha/__init__.py`
- `libs/alpha/research_platform.py`
- `libs/alpha/alpha_library.py`
- `tests/libs/alpha/__init__.py`
- `tests/libs/alpha/test_research_platform.py`
- `docs/CONCEPTS/alpha-research.md`

**PIT/Versioning Requirements:**
- All alpha backtests MUST use `dataset_version_id` from P4T1
- Alpha outputs stored with `as_of_date`, `data_version_id`, and `backtest_id`
- PIT validation: compare signals at T using data known at T vs T-1

**Acceptance Criteria:**
- [ ] Alpha signal definition framework with Protocol
- [ ] Point-in-time backtesting engine respecting dataset versions
- [ ] IC and ICIR analysis with statistical significance
- [ ] Alpha decay curve analysis
- [ ] **Throughput test**: Backtest 5 years / 3000 symbols with PIT cache in <5 minutes
- [ ] PIT validation tests (T vs T-1 delta checks)
- [ ] >90% test coverage

---

### T2.6: Alpha Advanced Analytics
**Effort:** 2-3 days | **PR:** `feat(p4): alpha advanced`
**Status:** Pending
**Dependencies:** T2.5

**Deliverables:**
- Overfitting detection (OOS testing)
- Multiple testing correction (Bonferroni, FDR)
- Alpha combiner (composite signals)
- Signal correlation analysis

**Files to Create:**
- `libs/alpha/alpha_combiner.py`
- `libs/alpha/overfitting_detection.py`
- `tests/libs/alpha/test_alpha_combiner.py`
- `docs/CONCEPTS/alpha-overfitting.md`

---

### T2.7: Factor Attribution Analysis
**Effort:** 3-4 days | **PR:** `feat(p4): factor attribution`
**Status:** Pending
**Dependencies:** T2.1, T1.5 (Fama-French) - COMPLETE

**Deliverables:**
- Fama-French regression (3/5/6-factor)
- Rolling factor exposure tracking
- Conditional attribution (up/down markets)
- Performance attribution output

**Files to Create:**
- `libs/analytics/__init__.py`
- `libs/analytics/attribution.py`
- `tests/libs/analytics/__init__.py`
- `tests/libs/analytics/test_attribution.py`
- `docs/CONCEPTS/performance-attribution.md`

---

### T2.8: Model Registry Integration
**Effort:** 3-4 days | **PR:** `feat(p4): model registry`
**Status:** Pending
**Dependencies:** T2.4, T2.6, T1.6 (Dataset Versioning)

**Problem:** No clear path to deploy trained risk models, factor definitions, or alpha weights to production. Need versioned storage with provenance tracking.

**Deliverables:**
- Model serialization with SHA-256 checksum
- Versioned model storage with immutable versions
- Production model loader with compatibility checks
- Integration contract for signal_service

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
    factor_list: list[str]     # Factors included
    parameters: dict           # Model parameters/config
    # Validation
    checksum_sha256: str       # Artifact checksum
    metrics: dict[str, float]  # Performance metrics (R², IC, etc.)
    # Environment
    env: EnvironmentMetadata   # Python version, deps hash, etc.
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

## Track 3: Market Microstructure (T3.1-T3.3)

### T3.1: Microstructure Analytics
**Effort:** 4-5 days | **PR:** `feat(p4): microstructure analytics`
**Status:** Pending
**Priority:** P1 (Advanced analytics)
**Dependencies:** T1.7 (TAQ Storage), T1.8 (TAQ Query Interface) - MUST complete T1.8 before starting

**Problem:** Need realized volatility, intraday patterns, and order flow analytics for execution and alpha.

**Deliverables:**
- Realized volatility calculation (5-min sampling)
- Intraday volatility patterns (U-shape analysis)
- HAR volatility forecasting model
- VPIN (Volume-synchronized PIN) calculation using Bulk Volume Classification (BVC)
- Spread and depth analysis

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

class ExecutionQualityAnalyzer:
    """Analyze execution quality against benchmarks."""

    def __init__(self, taq_provider: TAQQueryProvider):
        self.taq = taq_provider

    def analyze_execution(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        fills: list[dict],  # [{"time": ..., "price": ..., "qty": ...}]
        decision_time: datetime
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

---

### T3.3: Event Study Framework
**Effort:** 3-4 days | **PR:** `feat(p4): event study framework`
**Status:** Pending
**Dependencies:** T1.3 (CRSP) - COMPLETE

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

## Related Documents

- [P4T1_TASK.md](./P4T1_TASK.md) - Data Infrastructure (Dependency - COMPLETE)
- [P4_PLANNING.md](./P4_PLANNING.md) - Overall P4 planning
- [docs/ADRs/ADR-016-data-provider-protocol.md](../ADRs/ADR-016-data-provider-protocol.md) - Provider patterns

---

**Last Updated:** 2025-12-07
**Status:** Draft (Pending Review)
**Next Step:** Request planning reviews from Gemini and Codex
