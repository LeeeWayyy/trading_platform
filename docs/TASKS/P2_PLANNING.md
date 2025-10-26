# P2 Planning: Data Layer (Expanded)

**Phase:** P2 (Data Layer)
**Timeline:** 30-45 days
**Status:** 📋 Planning
**Current Task:** [Not started]
**Previous Phase:** P1 (Advanced Strategy Features)
**Last Updated:** 2025-10-26

---

## 📊 Progress Summary

**Overall:** 0% (0/5 tasks complete)

| Track | Progress | Status |
|-------|----------|--------|
| **Track 1: Data Quality & Integrity** | 0% (0/2) | 📋 Planning |
| **Track 2: Data Infrastructure** | 0% (0/3) | 📋 Planning |

**Completed:**
- [None yet]

**Next:** T0 - Corporate Actions Adjuster

**See individual PxTy_TASK/PROGRESS/DONE.md files for detailed tracking**

---

## Executive Summary

Phase 2 focuses on building trustworthy data infrastructure for both research and live trading. This phase implements critical data quality features that were deferred from P0/P1 to enable production-grade data handling.

**Key P2 Goals:**
1. Implement corporate action adjustments (splits, dividends) to ensure price continuity
2. Build data quality gates to prevent bad data from corrupting research/production
3. Create multi-source failover system for data reliability
4. Implement raw data archiving for auditability and replay capability
5. Build survivorship-bias-free universe tracking for accurate backtesting

**Development Workflow:**

All tasks in this phase follow the standard development workflow with **clink-based zen-mcp reviews**:

1. **Task Creation Review** (RECOMMENDED for complex tasks >4 hours)
   - Use workflow: `.claude/workflows/13-task-creation-review.md`
   - Tool: clink + gemini planner → codex planner
   - Validates: scope clarity, requirements completeness, safety requirements
   - Duration: ~2-3 minutes

2. **Progressive Implementation** (MANDATORY 4-step pattern per component)
   - Implement → Test → Quick Review → Commit
   - Quick review tool: clink + codex codereviewer
   - See: `.claude/workflows/03-zen-review-quick.md`
   - Frequency: Every 30-60 minutes per component

3. **Deep Review** (MANDATORY before PR)
   - Use workflow: `.claude/workflows/04-zen-review-deep.md`
   - Tool: clink + gemini codereviewer → codex codereviewer
   - Reviews: architecture, safety, scalability, test coverage
   - Duration: ~3-5 minutes

**Review Cost Model:**
- Subscription-based: $320-370/month (predictable, unlimited reviews)
- See `CLAUDE.md` for details

---

## Previous Phase → This Phase Transition Analysis

### What Previous Phase Delivered ✅

**Complete Deliverables from P1:**
- ✅ Mean reversion strategy implementation with 5 technical indicators
- ✅ Momentum strategy implementation with 9 technical indicators
- ✅ Ensemble combiner with 4 combination methods (simple/weighted/ranked/ml)
- ✅ Signal-based backtesting framework with comprehensive metrics
- ✅ Comprehensive test coverage (1210 tests passing, 86% coverage)

**Quality Metrics:**
- Test coverage: 86% (1210 passed)
- Documentation: Comprehensive READMEs for all strategies
- Code quality: mypy strict, ruff linting passing
- Reviewer approval: All P1 critical issues resolved

### Deferred Items from Previous Phase

These P0/P1 items were intentionally simplified to focus on strategy development and are now addressed in P2:

| # | Feature | Previous Implementation | This Phase Goal | Effort | Priority |
|---|---------|------------------------|-----------------|--------|----------|
| 1 | Corporate Actions | Assumed pre-adjusted prices | Implement split/dividend adjusters | 5-7 days | ⭐ HIGH |
| 2 | Data Quality | Basic validation only | Multi-layer quality gates with quarantine | 4-5 days | ⭐ HIGH |
| 3 | Data Sources | Single source (Alpaca) | Multi-source failover system | 5-7 days | 🔶 MEDIUM |
| 4 | Data Archiving | No raw data retention | Immutable raw tick/bar logs | 3-4 days | 🔷 LOW |
| 5 | Survivorship Bias | Active universe only | Historical tradability tracking | 4-5 days | 🔶 MEDIUM |

**Documentation:** See `docs/LESSONS_LEARNED/P1_retrospective.md` (to be created after P1T6 completion)

---

## P2 Tasks Breakdown

### Track 1: Data Quality & Integrity

#### T0: Corporate Actions Adjuster ⭐ HIGH PRIORITY

**Goal:** Adjust OHLCV data for stock splits and dividends to ensure price continuity for accurate backtesting

**Current State:**
- Currently using Alpaca's pre-adjusted prices
- No visibility into adjustment ratios or dates
- Cannot validate or replay adjustments

**P2 Requirements:**
```python
# libs/data_pipeline/corporate_actions.py
class CorporateActionAdjuster:
    """Adjust OHLCV for splits/dividends before writing Parquet."""

    def load_corporate_actions(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """Load split/div data from provider or manual CSV."""
        pass

    def adjust_prices(self, raw_df: pl.DataFrame, ca_df: pl.DataFrame) -> pl.DataFrame:
        """
        Returns DataFrame with adj_close, adj_open, adj_high, adj_low, adj_volume.

        Uses cumulative adjustment factors applied backward:
        1. Build price_factor and volume_factor columns via cumprod()
        2. For splits: price_factor *= (post_split / pre_split), volume_factor *= split_ratio
        3. For dividends: price_factor *= (1 - dividend / prior_close)^-1
        4. Handle same-day events by collapsing factors before applying
        5. Compute: adj_price = raw_price * price_factor, adj_volume = raw_volume / volume_factor

        Guardrails: missing prior_close, zero split ratios, idempotency on repeated ingests
        """
        pass
```

**Implementation Steps:**
1. **Create corporate actions data model** (`libs/data_pipeline/corporate_actions.py`)
   - Define CA schema (symbol, date, split_ratio, dividend)
   - Implement CSV loader for manual CA entries
   - Add API integration for automated CA fetching (Alpaca, Polygon)

2. **Implement price adjustment logic**
   - Backward adjustment algorithm (future splits adjust past prices)
   - Handle both splits and cash dividends
   - Validate adjustment continuity (no price jumps on CA dates)

3. **Integrate with data pipeline**
   - Update ETL to run CA adjustment before Parquet write
   - Add CA tracking table to DuckDB catalog
   - Implement adjustment audit log

4. **Create comprehensive tests**
   - Unit tests: 4:1 split, 2:1 split, reverse split (1:4), fractional ratios (3:2)
   - Unit tests: Cash dividends, special + ordinary dividends same day
   - Integration test: Multi-event sequences (split → div → split → reverse split)
   - Edge cases: Same-day split and dividend, symbols lacking CA data (warn and short-circuit)
   - Idempotency test: Repeated ingests produce same cumulative factors
   - Performance test: 5 years of SPY data adjusts in <1s

**Acceptance Criteria:**
- [ ] CA adjuster uses cumulative adjustment factors (not direct formula)
- [ ] Handles splits (forward/reverse/fractional) correctly
- [ ] Dividend adjustments produce continuous prices
- [ ] Same-day multiple events handled via collapsed factors
- [ ] Idempotent on repeated ingests (same factors)
- [ ] Missing CA data triggers warning and short-circuits
- [ ] Adjusted prices match Alpaca/Polygon within 0.01%
- [ ] Performance: 5y SPY data adjusts in <1s
- [ ] All tests pass with >90% coverage
- [ ] ADR documenting cumulative adjustment methodology created

**Estimated Effort:** 5-7 days
**Dependencies:** None (can start immediately)
**Files to Create:**
- `libs/data_pipeline/corporate_actions.py`
- `tests/libs/data_pipeline/test_corporate_actions.py`
- `docs/ADRs/ADR-XXX-corporate-action-adjustment.md`

**Files to Modify:**
- `libs/data_pipeline/etl.py` (integrate CA adjustment)
- `libs/duckdb_catalog.py` (add CA tracking table)

---

#### T1: Data Quality Ingestion Gate ⭐ HIGH PRIORITY

**Goal:** Implement multi-layer validation to prevent bad data from corrupting research and production

**Current State:**
- Basic schema validation only
- No outlier detection
- No staleness checks
- Bad data silently enters Parquet

**P2 Requirements:**
```python
# libs/data_pipeline/quality_gate.py
class DataQualityGate:
    """Validate data before storage; quarantine suspicious rows."""

    def check_schema(self, df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Validate required columns and data types."""
        pass

    def check_outliers(
        self,
        df: pl.DataFrame,
        ca_df: pl.DataFrame,
        threshold: float = 0.30,
        use_zscore: bool = False
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Flag >30% daily change WITHOUT corresponding corporate action.
        Parameterized per asset class. CA-aware guard only within event windows.
        Option to use z-score alternative for adaptive thresholds.
        Returns: (good_data, quarantined_data)
        """
        pass

    def check_staleness(self, df: pl.DataFrame, max_age_minutes: int = 30) -> bool:
        """Reject data older than threshold."""
        pass

    def check_gaps(self, df: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
        """Detect missing bars in expected trading hours."""
        pass
```

**Implementation Steps:**
1. **Schema validation layer**
   - Validate required columns (symbol, date, open, high, low, close, volume)
   - Check data types (price as float, volume as int, date as date)
   - Reject malformed rows

2. **Outlier detection layer**
   - Calculate daily returns per symbol
   - Flag >30% moves without CA explanation
   - Quarantine suspicious data for manual review

3. **Staleness and completeness checks**
   - Check data freshness (reject if >30min old for live, >1 day for historical)
   - Detect missing bars (gaps in expected trading hours)
   - Alert on systematic gaps (exchange halts, delisting)

4. **Enhanced quarantine system**
   - Create quarantine Parquet files separate from clean data
   - Store rich metadata: reason, detection_rule_id, payload_hash, ingest_timestamp, retention_policy
   - Implement CLI to review/replay quarantined batches
   - Support promote (to production) and reject (permanent quarantine) workflows
   - Add Redis expirations for quarantine metadata (configurable TTL)

**Acceptance Criteria:**
- [ ] Data freshness check BEFORE quality gate (block stale loads >30min for live, >1 day for historical)
- [ ] Schema validation rejects malformed data
- [ ] Outlier detection catches >30% moves without CA
- [ ] Outlier threshold parameterized per asset class (not hardcoded 30%)
- [ ] Z-score alternative available for adaptive thresholds
- [ ] CA-aware guard only active within defined event windows
- [ ] Staleness check rejects old data
- [ ] Quarantine system stores rich metadata (reason, rule_id, hash, timestamp, retention)
- [ ] CLI implemented to review/replay quarantined batches
- [ ] Promote/reject workflows functional
- [ ] Redis expirations configured for quarantine metadata (TTL)
- [ ] Quality metrics logged (% rejected, quarantine count)
- [ ] Performance: Quality gate adds <2s latency to ETL pipeline
- [ ] Performance: Quarantine review CLI responds in <1s
- [ ] Integration test validates end-to-end quality gate
- [ ] All tests pass with >90% coverage

**Estimated Effort:** 4-5 days
**Dependencies:** T0 (needs CA data to validate outliers)
**Files to Create:**
- `libs/data_pipeline/quality_gate.py`
- `tests/libs/data_pipeline/test_quality_gate.py`
- `docs/ADRs/ADR-XXX-data-quality-gates.md`

**Files to Modify:**
- `libs/data_pipeline/etl.py` (integrate quality gate)
- `libs/duckdb_catalog.py` (add quarantine table)

---

### Track 2: Data Infrastructure

#### T2: Multi-Source Failover System 🔶 MEDIUM PRIORITY

**Goal:** Implement pluggable data sources with automatic failover for reliability

**Current State:**
- Single data source (Alpaca)
- No failover if Alpaca down
- No health monitoring

**P2 Requirements:**
```python
# libs/data_pipeline/sources.py
from typing import Protocol

class DataSource(Protocol):
    """
    Explicit async/sync contract for data sources.
    All implementations MUST support both async and sync methods.
    """
    async def fetch_bars_async(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """Async fetch for concurrent ingests."""
        ...

    def fetch_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """Sync fetch for single-symbol queries."""
        ...

    def health_check(self) -> bool:
        """Return True if source is healthy."""
        ...

class DataSourceManager:
    """Manage multiple data sources with failover."""

    def __init__(self, sources: list[DataSource], health_check_interval: int = 60, redis_client=None):
        self.sources = sources  # Ordered by priority
        self.redis = redis_client  # For circuit breaker state shared across services
        self.health_cache = {}  # TTL cache for health check results

    def fetch_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """Try sources in priority order until success."""
        for source in self.sources:
            if self.is_healthy_cached(source):
                try:
                    return source.fetch_bars(symbol, start, end)
                except Exception:
                    self.trip_circuit_breaker(source)  # Shared via Redis
        raise AllSourcesFailedError()

    def is_healthy_cached(self, source: DataSource) -> bool:
        """Check health with TTL cache (60s) to avoid thrashing."""
        pass

    def trip_circuit_breaker(self, source: DataSource) -> None:
        """Set circuit breaker state in Redis with expiration."""
        pass
```

**Implementation Steps:**
1. **Abstract data source interface**
   - Define `DataSource` protocol with explicit async/sync contract (fetch_bars, fetch_bars_async, health_check)
   - Both async and sync methods REQUIRED for all implementations
   - Implement Alpaca source with both sync and async methods
   - Implement Polygon source (placeholder, requires API key)

2. **Health monitoring system with caching**
   - Periodic health checks (HTTP ping + freshness test)
   - Health check result cache with 60s TTL (avoid thrashing)
   - Circuit breaker per source stored in Redis (shared across services)
   - Circuit breaker with configurable expiration (e.g., 5 min quiet period)
   - Automatic recovery (re-enable after quiet period expires)

3. **Failover logic with performance optimization**
   - Try sources in priority order
   - Check Redis circuit breaker state before attempting fetch
   - Batch CA joins by symbol/date ranges for performance
   - Log source used for each fetch (audit trail)
   - Alert on primary source failure
   - Metrics: failover count, per-source success rate, latency per source

**Acceptance Criteria:**
- [ ] DataSource protocol defines explicit async/sync contract
- [ ] All implementations support both async and sync methods
- [ ] Supports multiple data sources via plugin interface
- [ ] Automatic failover when primary source fails
- [ ] Health check result cache implemented with 60s TTL
- [ ] Health checks detect source degradation
- [ ] Circuit breaker state shared via Redis (not in-memory)
- [ ] Circuit breaker prevents cascading failures
- [ ] Circuit breaker auto-recovery after quiet period
- [ ] Metrics track source reliability (failover count, success rate, latency)
- [ ] Performance: Failover completes within 10s of primary failure
- [ ] Performance: Health check cache reduces thrashing
- [ ] Integration test validates failover behavior
- [ ] All tests pass with >90% coverage

**Estimated Effort:** 5-7 days
**Dependencies:** None (parallel with T0/T1)
**Files to Create:**
- `libs/data_pipeline/sources.py`
- `libs/data_pipeline/sources/alpaca.py`
- `libs/data_pipeline/sources/polygon.py`
- `tests/libs/data_pipeline/test_sources.py`
- `docs/ADRs/ADR-XXX-multi-source-failover.md`

**Files to Modify:**
- `libs/data_pipeline/etl.py` (use DataSourceManager)

---

#### T3: Raw Data Recorder 🔷 LOW PRIORITY

**Goal:** Archive immutable raw tick/bar data for auditability and replay

**Current State:**
- Only adjusted data stored
- Cannot replay historical ingests
- No audit trail for data issues

**P2 Requirements:**
```python
# libs/data_pipeline/recorder.py
class RawDataRecorder:
    """Append-only archive of raw market data."""

    def record_bars(self, df: pl.DataFrame, source: str, ingest_ts: datetime) -> None:
        """
        Write raw data to immutable Parquet with metadata.
        Schema: symbol, date, open, high, low, close, volume, source, ingest_ts
        Storage: data/raw/YYYY/MM/DD/source_HHmmss.parquet
        """
        pass

    def replay(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """Replay historical raw data for debugging."""
        pass
```

**Implementation Steps:**
1. **Immutable storage design**
   - Append-only Parquet files (never overwrite)
   - Partition by date for efficient queries
   - Include ingest timestamp and source metadata

2. **Write path integration**
   - Record raw data BEFORE adjustments
   - Atomic writes (temp file → rename)
   - Compression for storage efficiency
   - **DuckDB single-writer thread safety**: Use single writer thread for Parquet sinks (`.collect().sink_parquet()` is safe)
   - Enable Polars streaming for large ingests if needed
   - Configure DuckDB `PRAGMA threads` for parallel reads (writes remain single-threaded)

3. **Replay functionality**
   - Query raw data by symbol and date range
   - Support debugging data quality issues
   - Compare raw vs adjusted data
   - Handle large ingests without memory overflow
   - Respect replay ordering (ingest_timestamp ASC)

**Acceptance Criteria:**
- [ ] Raw data archived before adjustments
- [ ] Append-only storage (no overwrites)
- [ ] Metadata includes source and ingest timestamp
- [ ] Replay functionality works for historical queries
- [ ] Storage compression reduces disk usage
- [ ] Integration test validates archiving and replay

**Estimated Effort:** 3-4 days
**Dependencies:** T0 (integrate before CA adjustment)
**Files to Create:**
- `libs/data_pipeline/recorder.py`
- `tests/libs/data_pipeline/test_recorder.py`

**Files to Modify:**
- `libs/data_pipeline/etl.py` (add raw data recording)

---

#### T4: Survivorship-Bias-Free Universe Tracking 🔶 MEDIUM PRIORITY

**Goal:** Track historical tradability to enable accurate backtesting without survivorship bias

**Current State:**
- Only current active universe tracked
- Cannot backtest on delisted stocks
- Survivorship bias inflates backtest results

**P2 Requirements:**
```python
# libs/data_pipeline/universe.py
class UniverseTracker:
    """Track symbol tradability over time."""

    def update_universe(self, date: date, tradable: list[str], halted: list[str], delisted: list[str]) -> None:
        """Update universe table with daily tradability status."""
        pass

    def get_tradable_universe(self, date: date) -> list[str]:
        """Return symbols tradable on given date."""
        pass

    def get_tradability_history(self, symbol: str) -> pl.DataFrame:
        """Return listing/delisting/halt history for symbol."""
        pass
```

**DDL:**
```sql
CREATE TABLE IF NOT EXISTS universe (
  symbol TEXT,
  start_date DATE,
  end_date DATE,
  tradable BOOLEAN DEFAULT TRUE,
  reason TEXT,  -- 'listed', 'delisted', 'halt', 'merger'
  PRIMARY KEY(symbol, start_date)
);
```

**Implementation Steps:**
1. **Universe data model**
   - Define tradability states (listed, halted, delisted, merged)
   - Implement date range tracking (start_date, end_date)
   - Handle IPOs, delistings, halts, mergers

2. **Daily universe updates**
   - Fetch active universe from data provider
   - Detect new listings (IPOs)
   - Detect delistings (compare to previous day)
   - Update universe table atomically

3. **Backtest integration**
   - Filter signals to only tradable symbols on each date
   - Exclude delisted stocks from future dates
   - Warn on backtest using dead stocks

**Acceptance Criteria:**
- [ ] Universe table tracks tradability over time
- [ ] Detects IPOs and delistings automatically
- [ ] get_tradable_universe returns correct symbols for historical dates
- [ ] Backtest framework respects tradability constraints
- [ ] Integration test validates survivorship-free backtesting

**Estimated Effort:** 4-5 days
**Dependencies:** None (parallel with T0-T3)
**Files to Create:**
- `libs/data_pipeline/universe.py`
- `tests/libs/data_pipeline/test_universe.py`
- `docs/ADRs/ADR-XXX-survivorship-free-universe.md`

**Files to Modify:**
- `libs/duckdb_catalog.py` (add universe table)
- `strategies/backtest/evaluator.py` (filter by tradability)

---

## P2 Roadmap & Priorities

### Phase Breakdown

**Priority Order:**
1. **T0: Corporate Actions Adjuster** (5-7 days) - Critical for price continuity and accurate backtesting
2. **T1: Data Quality Gate** (4-5 days) - Prevents bad data corruption, depends on T0
3. **T4: Survivorship-Free Universe** (4-5 days) - Eliminates backtest bias, can run parallel
4. **T2: Multi-Source Failover** (5-7 days) - Improves reliability, lower priority than quality
5. **T3: Raw Data Recorder** (3-4 days) - Nice-to-have for debugging, lowest priority

**Parallel Tracks:**
- Track 1 (T0 → T1): Sequential, T1 depends on T0
- Track 2 (T2, T3, T4): Can run in parallel with Track 1

---

## Lean MVP Recommendations

Following the lean MVP approach from the realization plan, P2 integrates essential best practices:

### ✅ Already Integrated

1. **Data Freshness Check** (Gemini feedback)
   - ✅ Integrated into T1 acceptance criteria (line 258)
   - ✅ Staleness check BEFORE quality gate blocks stale loads
   - ✅ Parameterized thresholds: >30min for live, >1 day for historical

2. **CLI for Operations** (Gemini feedback)
   - ✅ Integrated into T1 implementation (lines 253-255)
   - ✅ Quarantine review CLI with promote/reject workflows
   - ✅ Performance target: <1s response time (line 271)

3. **Simplified Universe Config** (Gemini feedback)
   - ✅ T4 already supports static symbol list approach for MVP
   - ✅ Can start with CSV config before implementing auto-detection
   - ✅ Recommended: Begin with 10-20 high-volume symbols (SPY, AAPL, MSFT, etc.)

### 🔶 Deferred to P3+ (Execution Pipeline)

4. **DRY_RUN Flag** (Gemini feedback)
   - 🔶 Out of scope for P2 (Data Layer focuses on ingestion, not execution)
   - 🔶 Will be addressed in P3 when building execution pipeline
   - 🔶 Note: ETL pipeline can be tested independently without DRY_RUN flag

### MVP Implementation Strategy

**Minimum Viable P2 (15-20 days):**
- Focus: T0 + T1 only (corporate actions + quality gates)
- Universe: Static CSV with 10-20 symbols
- Data sources: Alpaca only (defer multi-source to later)
- Raw archiving: Defer T3 to P3+ if time-constrained

**Recommended Full P2 (30-45 days):**
- All 5 tasks with complete failover and auditability
- Positions project for production-grade data handling

---

## Total P2 Effort Estimates

### Minimum Viable P2
- **Time:** 15-20 days
- **Focus:** T0 + T1 only (corporate actions + quality gates)
- **Output:** Production-grade data quality for research and live trading

### Recommended P2
- **Time:** 30-45 days (~6 weeks)
- **Focus:** All 5 tasks (full phase scope)
- **Output:** Complete trustworthy data infrastructure with failover and auditability

---

## Success Metrics

### P2 Success Criteria
- [ ] Corporate actions applied correctly (validated against provider data)
- [ ] <1% of data quarantined by quality gates (healthy rejection rate)
- [ ] Zero bad data reaches Parquet (100% gate effectiveness)
- [ ] Failover tested and working (manual source outage test)
- [ ] Raw data archived for all ingests
- [ ] Survivorship-free backtests produce realistic Sharpe ratios

### Performance Targets
- [ ] CA adjustment adds <1s latency per symbol
- [ ] Quality gate adds <2s latency to ETL pipeline
- [ ] Failover completes within 10s of primary failure
- [ ] Raw data storage <20% overhead vs adjusted data

---

## Testing Strategy

### Unit Tests
- Target: >90% coverage for all new modules
- Focus: Edge cases (multi-CA, extreme outliers, failover edge cases)
- Performance: All unit tests <5s total

### Integration Tests
- ETL pipeline with CA adjustment + quality gate end-to-end
- Multi-source failover with simulated outage
- Universe tracking with IPO/delisting scenarios
- Raw data archiving and replay

### End-to-End Tests
- Historical data backfill with all P2 features enabled
- Live data ingest with quality gate + CA + recording
- Backtest replay using survivorship-free universe

### Performance Tests
- CA adjustment on 10 years of data for SPY
- Quality gate throughput (rows/sec)
- Failover latency measurement

---

## Documentation Requirements

### For Each Task
- [ ] ADR documenting technical decisions (T0, T1, T2, T4)
- [ ] Implementation guide with code examples
- [ ] API documentation for public interfaces
- [ ] Updated README with new features
- [ ] Lessons learned added to retrospective

### New Concept Docs Needed
- [ ] `docs/CONCEPTS/corporate-actions.md` - Explain splits, dividends, adjustment methodology
- [ ] `docs/CONCEPTS/data-quality.md` - Quality gate philosophy and thresholds
- [ ] `docs/CONCEPTS/survivorship-bias.md` - Why it matters, how we prevent it

---

## Dependencies & Prerequisites

### Infrastructure
- [ ] DuckDB catalog running (already exists from P0)
- [ ] Parquet storage path configured (already exists)
- [ ] Redis online for caching (already exists from P1)

### External Services
- [ ] Alpaca API access (already configured)
- [ ] Polygon API key (optional, for T2)
- [ ] Corporate action data source (Alpaca or manual CSV)

### Skills/Knowledge
- [ ] Polars DataFrame operations (already learned in P0/P1)
- [ ] Corporate action methodology (to learn in T0)
- [ ] Data quality best practices (to learn in T1)

---

## Risk & Mitigation

### Risk 1: Corporate Action Data Availability
**Impact:** High (incorrect adjustments corrupt all backtests)
**Probability:** Medium (Alpaca CA data may be incomplete)
**Mitigation:**
- Start with manual CSV for major events (AAPL, TSLA splits)
- Validate against multiple sources (Alpaca, Polygon, Yahoo)
- Implement adjustment audit log for manual review

### Risk 2: Quality Gate False Positives
**Impact:** Medium (legitimate data quarantined)
**Probability:** Low (30% threshold is conservative)
**Mitigation:**
- Tune outlier threshold based on historical data
- Implement quarantine review workflow (promote good data)
- Alert on high quarantine rates for manual investigation

### Risk 3: Multi-Source Integration Complexity
**Impact:** Low (delays T2 only, doesn't block other tasks)
**Probability:** Medium (different API schemas, rate limits)
**Mitigation:**
- Design clean abstraction layer (DataSource protocol)
- Implement Polygon integration as separate task
- Start with Alpaca-only, add sources incrementally

### Risk 4: Storage Growth from Raw Data
**Impact:** Low (disk space manageable)
**Probability:** High (raw data doubles storage)
**Mitigation:**
- Implement compression (Parquet with zstd)
- Retention policy (keep raw data 1 year, then purge)
- S3/MinIO for cold storage (cheaper than local disk)

### Risk 5: Performance Degradation from New Pipeline Steps
**Impact:** Medium (slower ingests could delay signals)
**Probability:** Medium (CA adjustment + quality gates add latency)
**Mitigation:**
- Benchmark: 5y SPY CA adjustment <1s (T0 acceptance criteria)
- Benchmark: Quality gate adds <2s latency (T1 acceptance criteria)
- Batch CA joins by symbol/date ranges for efficiency (T2 implementation)
- Enable Polars streaming for large ingests (T3 implementation)
- Configure DuckDB `PRAGMA threads` for parallel reads (T3 implementation)
- Monitor ETL pipeline latency via Prometheus metrics
- Alert on latency regression >20% from baseline

---

## Next Steps

### Immediate (Phase Start)
1. [ ] Review P1 retrospective (once P1T6 complete)
2. [ ] Finalize P2 plan (this document)
3. [ ] Generate task files: `./scripts/tasks.py generate-tasks-from-phase P2`
4. [ ] Begin first task: `./scripts/tasks.py start P2T0`

### This Week
- [ ] Complete T0 planning and start implementation
- [ ] Set up corporate action data sources (Alpaca API + manual CSV)
- [ ] Create ADR for CA adjustment methodology

### This Month
- [ ] Complete Track 1 (T0 + T1): Corporate actions + quality gates
- [ ] Start Track 2 parallel tasks (T2, T4)
- [ ] Mid-phase review and adjust estimates

---

## Technical Debt & Known Issues

**From P0/P1:**
1. **Data staleness not monitored**
   - **Fix:** Addressed in T1 (quality gate staleness check)
   - **Priority:** HIGH

2. **No audit trail for data issues**
   - **Fix:** Addressed in T3 (raw data recorder)
   - **Priority:** MEDIUM

3. **Single point of failure (Alpaca)**
   - **Fix:** Addressed in T2 (multi-source failover)
   - **Priority:** MEDIUM

**New Debt (acceptable for P2):**
- Corporate action data relies on external sources (not self-hosted)
- Quality gate thresholds are static (not adaptive)
- Polygon integration deferred (can add later if needed)

---

## Related Documents

- [P1_PLANNING.md](./P1_PLANNING.md) - Previous phase (Advanced Strategies)
- [Master Plan](../trading_platform_realization_plan.md) - Overall project roadmap
- [CLAUDE.md](../CLAUDE.md) - Development workflow and zen-mcp review process
- [.claude/workflows/12-phase-management.md](../.claude/workflows/12-phase-management.md) - Phase management workflow

---

**Last Updated:** 2025-10-26
**Status:** Planning (0% complete, 0/5 tasks)
**Next Review:** After T0 completion
