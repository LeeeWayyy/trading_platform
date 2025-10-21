# P1 Planning: Advanced Features & Production Readiness

**Phase:** P1 (Advanced Features)
**Timeline:** Days 46-90 (~11-18 days estimated for MVP simplifications + new features)
**Status:** ✅ In Progress (8/12 tasks complete, 67%)
**Current Task:** T0 - Enhanced P&L Calculation OR T6 - Advanced Strategies
**Previous Phase:** P0 MVP (100% Complete - 6/6 tasks delivered)
**Last Updated:** October 21, 2024

---

## 📊 Progress Summary

**Overall:** 67% (8/12 tasks complete)

| Track | Progress | Status |
|-------|----------|--------|
| **Track 1: Infrastructure** | 80% (4/5) | 🔄 In Progress |
| **Track 2: New Features** | 67% (2/3) | 🔄 In Progress |
| **Track 3: Production** | 50% (2/4) | 🔄 In Progress |

**Completed:**
- ✅ T1 (P1T1) - Redis Integration (Oct 18, PR#10)
- ✅ T2 (P1T2) - DuckDB Analytics Layer (Oct 18, PR#12)
- ✅ T3 (P1T3) - Timezone Timestamps (Oct 18, PR#13)
- ✅ T4 (P1T4) - Operational Status (Oct 18, PR#14)
- ✅ T5 (P1T5) - Real-Time Market Data (Oct 19, PR#15)
- ✅ T7 (P1T7) - Risk Management System (Oct 19, PR#16)
- ✅ T8 (P1T8) - Monitoring & Alerting (Oct 21, PR#25)
- ✅ Test Coverage Enhancement (80%+ target) (Oct 20, PR#20-21)

**Next:** T0 - Enhanced P&L Calculation (3-5 days) OR T6 - Advanced Strategies (7-10 days, optional)

---

## Executive Summary

P0 MVP delivered a complete end-to-end paper trading platform (100% complete, 152/152 tests passing). P1 focuses on adding advanced features and production-grade capabilities by implementing the features that were intentionally simplified or deferred during MVP development.

**Key P1 Goals:**
1. ✅ Implement intentionally deferred MVP simplifications (8-13 days)
2. 🆕 Add new advanced features (real-time data, advanced strategies)
3. 🔧 Production hardening (monitoring, alerting)

---

## P0 MVP → P1 Transition Analysis

### What P0 Delivered ✅

**Complete End-to-End Pipeline:**
- T1: Data ETL with corporate actions and quality gates
- T2: Baseline ML strategy with MLflow tracking
- T3: Signal Service with model registry and hot reload
- T4: Execution Gateway with idempotent orders
- T5: Orchestrator Service coordinating T3→T4
- T6: Paper Run automation script with notional P&L

**Quality Metrics:**
- 152/152 tests passing (100% pass rate)
- 17,200+ lines of documentation
- 9,700+ lines of production code
- 4,600+ lines of test code

### Intentional MVP Simplifications

During P0, four features from the original plan were consciously simplified or deferred to maintain MVP focus. These are **not technical debt** - they are planned enhancements with clear implementation paths.

| # | Feature | Original Plan | MVP Implementation | P1 Effort | Priority |
|---|---------|--------------|-------------------|-----------|----------|
| 1 | **DuckDB Catalog** | Phase 2 analytics layer | Parquet + Polars | 1-2 days | Medium |
| 2 | **Redis Integration** | Phase 5 feature store + pub/sub | HTTP calls | 3-5 days | High |
| 3 | **Complete P&L** | Phase 5 full P&L report | Notional value only | 3-5 days | High |
| 4 | **`make status`** | Phase 5 operational command | Direct API queries | 1 day | Low |

**Total:** ~8-13 days to implement all deferred features

**Documentation:** See `docs/LESSONS_LEARNED/t6-paper-run-retrospective.md` for detailed analysis

---

## P1 Tasks Breakdown

### Track 1: Infrastructure Enhancements (from P0 Simplifications)

#### T0: Enhanced P&L Calculation ⭐ HIGH PRIORITY

**Goal:** Replace notional P&L with realized/unrealized breakdown

**Current State (P0):**
- `paper_run.py` calculates total notional value only
- Validates order sizing but doesn't track actual profit/loss
- Documented in `docs/CONCEPTS/pnl-calculation.md`

**P1 Requirements:**
```python
# Calculate complete P&L
{
  'realized_pnl': Decimal('1234.56'),      # From closed positions
  'unrealized_pnl': Decimal('789.01'),     # Mark-to-market on open
  'total_pnl': Decimal('2023.57'),         # realized + unrealized
  'per_symbol': {
    'AAPL': {'realized': 500, 'unrealized': 200},
    'MSFT': {'realized': -100, 'unrealized': 300}
  }
}
```

**Implementation Steps:**
1. **Query T4 for positions** (`GET /api/v1/positions`)
2. **Fetch current market prices** (Alpaca API or data feed)
3. **Calculate unrealized P&L:**
   ```python
   unrealized_pnl = (current_price - avg_entry_price) * qty
   ```
4. **Track realized P&L** (from T4 positions with qty=0)
5. **Update paper_run.py** with enhanced calculation
6. **Add tests** for P&L calculation accuracy
7. **Update documentation** (pnl-calculation.md)

**Acceptance Criteria:**
- [ ] Realized P&L tracked from closed positions
- [ ] Unrealized P&L calculated with current prices
- [ ] Per-symbol P&L breakdown
- [ ] Tests verify calculation accuracy
- [ ] Documentation updated with examples

**Estimated Effort:** 3-5 days
**Dependencies:** T4 (Execution Gateway), market data access
**Files to Modify:** `scripts/paper_run.py`, `docs/CONCEPTS/pnl-calculation.md`

---

#### T1: Redis Integration ⭐ HIGH PRIORITY

**Goal:** Add Redis for online feature store and event-driven orchestration

**Current State (P0):**
- Redis configured but not used
- Services communicate via HTTP
- No feature caching

**P1 Requirements:**

**Use Case 1: Online Feature Store**
```python
# Cache features in Redis for fast lookup
r.setex(f"features:{symbol}:{date}", 3600, json.dumps(features))

# Signal service retrieves cached features
cached = r.get(f"features:AAPL:2025-01-17")
```

**Use Case 2: Event Bus for Orchestration**
```python
# Signal service publishes when signals ready
r.publish('signals.generated', json.dumps({
  'symbols': ['AAPL', 'MSFT'],
  'timestamp': '2025-01-17T09:00:00Z'
}))

# Orchestrator subscribes and reacts
pubsub = r.pubsub()
pubsub.subscribe('signals.generated')
for message in pubsub.listen():
    trigger_orchestration(message)
```

**Implementation Steps:**
1. **Add Redis client** to requirements.txt
2. **Create Redis manager** (`libs/redis_client.py`)
3. **Implement feature caching** in T3 signal service
4. **Add pub/sub** for signal generation events
5. **Update orchestrator** to subscribe to events
6. **Add health checks** for Redis connectivity
7. **Add tests** for caching and pub/sub
8. **Update documentation** with Redis architecture

**Acceptance Criteria:**
- [ ] Features cached in Redis with TTL
- [ ] Pub/sub working for orchestration events
- [ ] Health checks verify Redis connectivity
- [ ] Tests validate caching and event flow
- [ ] Documentation includes Redis patterns

**Estimated Effort:** 3-5 days
**Dependencies:** Redis server running
**Files to Create:** `libs/redis_client.py`
**Files to Modify:** `apps/signal_service/`, `apps/orchestrator/`

---

#### T2: DuckDB Analytics Layer 🔶 MEDIUM PRIORITY

**Goal:** Add SQL interface for ad-hoc analytics on historical data

**Current State (P0):**
- Data in Parquet files (`data/adjusted/YYYY-MM-DD/`)
- Polars for ETL operations
- No SQL query capability

**P1 Requirements:**
```sql
-- Ad-hoc queries on historical data
SELECT
  symbol,
  date,
  close,
  volume
FROM read_parquet('data/adjusted/*/AAPL.parquet')
WHERE date >= '2024-01-01'
  AND close > 150
ORDER BY date DESC
LIMIT 100;

-- Join across multiple symbols
SELECT
  a.date,
  a.close as aapl_close,
  m.close as msft_close
FROM read_parquet('data/adjusted/*/AAPL.parquet') a
JOIN read_parquet('data/adjusted/*/MSFT.parquet') m
  ON a.date = m.date
WHERE a.date >= '2024-01-01';
```

**Implementation Steps:**
1. **Add DuckDB dependency** to requirements.txt
2. **Create catalog module** (`libs/duckdb_catalog.py`)
3. **Register Parquet files** as virtual tables
4. **Create helper functions** for common queries
5. **Add Jupyter notebook** with query examples
6. **Add tests** for catalog functionality
7. **Update documentation** with query patterns

**Acceptance Criteria:**
- [ ] DuckDB can query existing Parquet files
- [ ] Helper functions for common analytics
- [ ] Jupyter notebook with examples
- [ ] Tests verify query correctness
- [ ] Documentation includes SQL patterns

**Estimated Effort:** 1-2 days
**Dependencies:** Existing Parquet data
**Files to Create:** `libs/duckdb_catalog.py`, `notebooks/analytics.ipynb`

---

#### T3: Timezone-Aware Timestamps 🔶 MEDIUM PRIORITY

**Goal:** Add UTC timestamps for production-grade logging

**Current State (P0):**
- `datetime.now()` without timezone
- System time used for all logs

**P1 Requirements:**
```python
from datetime import datetime, timezone

# Console output with timezone
timestamp = datetime.now(timezone.utc)
print(f"Run started: {timestamp.isoformat()}")
# Output: "Run started: 2025-01-17T14:30:00+00:00"

# JSON export with timezone
{
  "timestamp": "2025-01-17T14:30:00+00:00",
  "timezone": "UTC"
}
```

**Implementation Steps:**
1. **Update paper_run.py** to use `datetime.now(timezone.utc)`
2. **Update console output** formatting
3. **Update JSON export** with timezone info
4. **Add regression tests** validating timezone presence
5. **Update documentation** with timezone examples

**Acceptance Criteria:**
- [ ] All timestamps use UTC
- [ ] Console output shows timezone
- [ ] JSON exports include timezone
- [ ] Tests verify timezone correctness
- [ ] Documentation updated

**Estimated Effort:** 1 day
**Dependencies:** None
**Files to Modify:** `scripts/paper_run.py`

---

#### T4: Operational Status Command 🔷 LOW PRIORITY

**Goal:** Create `make status` wrapper for operational overview

**Current State (P0):**
- Makefile has placeholder
- APIs exist but no unified view

**P1 Requirements:**
```bash
$ make status

=== Trading Platform Status ===

📊 Positions (T4):
  AAPL: 100 shares @ $150.00 avg, unrealized P&L: +$300
  MSFT: -50 shares @ $300.00 avg, unrealized P&L: -$150

📋 Recent Runs (T5):
  2025-01-17 09:00: SUCCESS (3 signals, 3 orders accepted)
  2025-01-16 09:00: SUCCESS (2 signals, 2 orders accepted)

💰 Latest P&L:
  Realized: +$1,234.56
  Unrealized: +$150.00
  Total: +$1,384.56

🔧 Services:
  ✓ Signal Service (T3): healthy
  ✓ Execution Gateway (T4): healthy
  ✓ Orchestrator (T5): healthy
```

**Implementation Steps:**
1. **Create `scripts/operational_status.sh`**
2. **Query T4 API** for positions
3. **Query T5 API** for recent runs
4. **Format output** with colors and emojis
5. **Add health checks** for all services
6. **Update Makefile** to call script
7. **Add tests** for script logic

**Acceptance Criteria:**
- [ ] `make status` shows complete overview
- [ ] Formatted output with colors
- [ ] Health checks for all services
- [ ] Error handling for service downtime
- [ ] Documentation updated

**Estimated Effort:** 1 day
**Dependencies:** T4, T5 APIs
**Files to Create:** `scripts/operational_status.sh`
**Files to Modify:** `Makefile`

---

### Track 2: New Advanced Features 🆕

#### T5: Real-Time Market Data Streaming

**Goal:** Add WebSocket connection for real-time price updates

**Current State:** Data loaded from Parquet files (batch)

**P1 Requirements:**
- WebSocket connection to Alpaca real-time data
- Stream prices for active positions
- Update unrealized P&L in real-time
- Store tick data to TimescaleDB or InfluxDB

**Estimated Effort:** 5-7 days
**Priority:** High

---

#### T6: Advanced Trading Strategies

**Goal:** Implement additional ML strategies beyond Alpha158 baseline

**P1 Requirements:**
- Mean reversion strategy
- Momentum strategy
- Multi-model ensemble
- Strategy backtesting framework

**Estimated Effort:** 7-10 days
**Priority:** Medium

---

#### T7: Risk Management System

**Goal:** Add risk limits and circuit breakers

**P1 Requirements:**
- Position size limits
- Daily loss limits
- Correlation checks
- Automatic position reduction

**Estimated Effort:** 5-7 days
**Priority:** High

---

### Track 3: Production Hardening 🔧

#### T8: Monitoring and Alerting

**Goal:** Add Prometheus metrics and Grafana dashboards

**P1 Requirements:**
- Prometheus metrics for all services
- Grafana dashboards for P&L, orders, positions
- AlertManager for critical events
- PagerDuty integration

**Estimated Effort:** 5-7 days
**Priority:** High

---

#### T9: Centralized Logging

**Goal:** Structured logging with ELK or Loki

**P1 Requirements:**
- Structured JSON logging
- Log aggregation (Elasticsearch or Loki)
- Log correlation with trace IDs
- Log retention policies

**Estimated Effort:** 3-5 days
**Priority:** Medium

---

#### T10: CI/CD Pipeline

**Goal:** Automated testing and deployment

**P1 Requirements:**
- GitHub Actions for test automation
- Docker image building
- Automated deployment to staging
- Integration testing in CI

**Estimated Effort:** 3-5 days
**Priority:** Medium

---

## P1 Roadmap & Priorities

### Phase 1A: Infrastructure Enhancements (Mostly Complete)

**Goal:** Complete deferred P0 features

**Status:** 80% complete (4/5 tasks done)

**Completed:**
1. ✅ **T1: Redis Integration** (3-5 days) - Enables real-time features
2. ✅ **T2: DuckDB Analytics** (1-2 days) - SQL interface for analysis
3. ✅ **T3: Timezone Awareness** (1 day) - Production logging requirement
4. ✅ **T4: Operational Status** (1 day) - Convenience feature

**Remaining:**
1. **T0: Enhanced P&L** (3-5 days) - Critical for performance tracking

### Phase 1B: New Advanced Features (Mostly Complete)

**Goal:** Add real-time capabilities and risk management

**Status:** 67% complete (2/3 tasks done)

**Completed:**
1. ✅ **T5: Real-Time Data** (5-7 days) - Foundation for live trading
2. ✅ **T7: Risk Management** (5-7 days) - Critical for safety

**Remaining:**
1. **T6: Advanced Strategies** (7-10 days) - Optional, can be P2

### Phase 1C: Production Hardening (In Progress)

**Goal:** Production-grade monitoring and deployment

**Status:** 50% complete (2/4 tasks done)

**Completed:**
1. ✅ **T8: Monitoring & Alerting** (5-7 days) - Operational visibility
2. ✅ **Test Coverage Enhancement** (80%+ target achieved)

**Remaining:**
1. **T9: Centralized Logging** (3-5 days) - Debugging capability
2. **T10: CI/CD Pipeline** (3-5 days) - Automation

---

## Total P1 Effort Estimates

### Minimum Viable P1 (Phase 1A only)
- **Time:** 8-13 days
- **Focus:** Complete deferred P0 features
- **Output:** Full-featured platform with complete P&L, Redis integration, analytics

### Recommended P1 (Phase 1A + 1B + 1C)
- **Time:** 29-44 days (~6-9 weeks)
- **Focus:** Production-ready real-time trading platform
- **Output:** Live trading capable system with monitoring and risk management

### Full P1 (All phases)
- **Time:** 36-54 days (~7-11 weeks)
- **Focus:** Complete advanced feature set
- **Output:** Multi-strategy production platform

---

## Success Metrics

### Phase 1A Success Criteria
- [ ] Realized/unrealized P&L calculated correctly
- [ ] Redis caching reduces signal generation latency by 50%
- [ ] DuckDB enables sub-second ad-hoc queries
- [ ] All timestamps are timezone-aware (UTC)
- [ ] `make status` provides complete operational overview

### Phase 1B Success Criteria
- [ ] Real-time prices streaming with < 100ms latency
- [ ] Risk limits prevent catastrophic losses
- [ ] Circuit breakers trigger on 5% daily loss

### Phase 1C Success Criteria
- [ ] All services emit Prometheus metrics
- [ ] Grafana dashboards show real-time P&L
- [ ] Alerts fire within 1 minute of issues
- [ ] CI/CD deploys in < 10 minutes

---

## Testing Strategy

### Unit Tests
- All new functionality has 90%+ test coverage
- Mock external dependencies (Redis, Alpaca API)
- Fast execution (< 5 seconds for full suite)

### Integration Tests
- Test Redis pub/sub workflows
- Test P&L calculation with real position data
- Test real-time data streaming

### End-to-End Tests
- Complete paper trading run with Redis
- Verify P&L accuracy over multi-day period
- Test circuit breaker activation

### Performance Tests
- Redis caching latency < 10ms
- Real-time data lag < 100ms
- P&L calculation < 100ms for 100 positions

---

## Documentation Requirements

### For Each Task
- [ ] ADR documenting technical decisions
- [ ] Implementation guide with examples
- [ ] API documentation (if new endpoints)
- [ ] Updated README with new features
- [ ] Lessons learned / retrospective

### New Concept Docs Needed
- [ ] `docs/CONCEPTS/redis-patterns.md` - Redis architecture
- [ ] `docs/CONCEPTS/real-time-trading.md` - WebSocket streaming
- [ ] `docs/CONCEPTS/risk-management.md` - Risk limits and circuit breakers

---

## Dependencies & Prerequisites

### Infrastructure
- [ ] Redis server (6.x+) running
- [ ] TimescaleDB or InfluxDB for tick data
- [ ] Prometheus + Grafana stack
- [ ] GitHub Actions configured

### External Services
- [ ] Alpaca real-time data subscription
- [ ] PagerDuty account (optional)

### Skills/Knowledge
- [ ] Redis pub/sub patterns
- [ ] WebSocket programming
- [ ] Prometheus metrics design
- [ ] Risk management principles

---

## Risk & Mitigation

### Risk 1: Real-Time Data Complexity
**Impact:** High
**Probability:** Medium
**Mitigation:** Start with polling, iterate to WebSocket

### Risk 2: Redis Learning Curve
**Impact:** Medium
**Probability:** Low
**Mitigation:** Extensive documentation, simple use cases first

### Risk 3: P&L Calculation Accuracy
**Impact:** High
**Probability:** Low
**Mitigation:** Comprehensive tests, validate against broker statements

---

## Next Steps

### Immediate (Post-P0)
1. ✅ Review P0 retrospective
2. ✅ Finalize P1 plan (this document)
3. ⏳ Prioritize Phase 1A tasks
4. ⏳ Begin T1.1 (Enhanced P&L) implementation

### This Week
- [ ] Create branches for T1.1 and T1.2
- [ ] Draft ADRs for Redis integration
- [ ] Research real-time data APIs

### This Month
- [ ] Complete Phase 1A (MVP simplifications)
- [ ] Begin Phase 1B (real-time features)

---

## CI/CD & Technical Debt

### Mypy Type Checking Issues (Found in PR #18)

**Status:** 📋 Backlog - Not blocking, address during maintenance window

**Context:** First PR with automated CI/CD testing revealed pre-existing mypy type checking errors. These are not introduced by recent changes but exist in the codebase.

**Issues Found (5 total):**

1. **Missing `alpaca.data.live` import stub**
   - **File:** `libs/market_data/alpaca_stream.py:14`
   - **Error:** Cannot find implementation or library stub
   - **Fix:** Install alpaca-py type stubs OR add `# type: ignore`
   - **Priority:** Low (non-blocking)

2. **Missing `alpaca.data.models` import stub**
   - **File:** `libs/market_data/alpaca_stream.py:15`
   - **Error:** Cannot find implementation or library stub
   - **Fix:** Install alpaca-py type stubs OR add `# type: ignore`
   - **Priority:** Low (non-blocking)

3. **Missing `tenacity` import stub**
   - **File:** `libs/redis_client/client.py:27`
   - **Error:** Cannot find implementation or library stub
   - **Fix:** `pip install types-tenacity`
   - **Priority:** Low (non-blocking)

4. **Missing `duckdb` import stub**
   - **File:** `libs/duckdb_catalog.py:48`
   - **Error:** Cannot find implementation or library stub
   - **Fix:** Install duckdb type stubs OR add `# type: ignore`
   - **Priority:** Low (non-blocking)

5. **Duplicate module name: `execution_gateway.schemas`**
   - **File:** `apps/execution_gateway/schemas.py`
   - **Error:** Source file found twice under different module names
   - **Fix:** Investigate PYTHONPATH/import configuration
   - **Priority:** Medium (confusing for type checker)

**Recommended Approach:**

Option A (Quick fix - 15 min):
```bash
pip install types-tenacity  # Fix issue #3
# Add type: ignore for alpaca and duckdb imports
```

Option B (Proper fix - 1-2 hours):
```bash
# Install all missing type stubs
pip install types-tenacity
pip install types-duckdb  # if available
# Research alpaca-py type stub availability
# Fix PYTHONPATH for execution_gateway.schemas
```

**Timeline:** Address during next maintenance window or when working on affected files

**Reference:** PR #18 CI failure logs

---

### Test Coverage Issues (Found in PR #19)

**Status:** ✅ **COMPLETE** - 81% coverage achieved (Oct 20, 2024, PR #21)

**Context:** Mypy --strict migration (PR #19) revealed that overall test coverage is only **17%** (5353 statements, 4453 missed). This is a pre-existing project-wide issue that needs systematic addressing.

**Coverage Breakdown:**

**Modules with 0% Coverage (NOT TESTED - 889 statements):**
1. **apps/execution_gateway/main.py** - 226 statements, 0% coverage
   - FastAPI endpoints for order submission, positions, P&L
   - Priority: **High** (core business logic)

2. **apps/execution_gateway/database.py** - 132 statements, 0% coverage
   - Database client for orders and positions
   - Priority: **High** (data integrity critical)

3. **apps/orchestrator/main.py** - 91 statements, 0% coverage
   - Orchestrator FastAPI endpoints
   - Priority: **Medium** (integration layer)

4. **apps/orchestrator/orchestrator.py** - 114 statements, 0% coverage
   - Core orchestration business logic
   - Priority: **High** (workflow coordination)

5. **apps/market_data_service/main.py** - 108 statements, 0% coverage
   - Market data streaming service
   - Priority: **Medium**

6. **libs/risk_management/breaker.py** - 152 statements, 0% coverage
   - Circuit breaker implementation
   - Priority: **Critical** (safety mechanism)

7. **libs/risk_management/checker.py** - 66 statements, 0% coverage
   - Risk validation logic
   - Priority: **Critical** (safety mechanism)

**Modules with Low Coverage (<50%):**
- apps/signal_service/main.py - 25% (228 statements, 162 missed)
- apps/signal_service/model_registry.py - 35% (93 statements, 57 missed)
- apps/execution_gateway/alpaca_client.py - 18% (153 statements, 119 missed)

**Modules with Good Coverage (>80%):**
- ✅ libs/duckdb_catalog.py - 100%
- ✅ libs/redis_client/* - 82-100%
- ✅ libs/data_pipeline/* - 91-100%

**Recommended Action Plan:**

**Phase 1: Critical Safety & Business Logic (Target: 60% overall)**
- **Week 1:** Add integration tests for FastAPI endpoints (425 statements)
  - Execution gateway endpoints (order submission, positions, P&L)
  - Orchestrator endpoints (run orchestration)
  - Market data service endpoints

- **Week 2:** Add unit tests for risk management (218 statements)
  - Circuit breaker logic (trip conditions, recovery)
  - Risk checker validation

- **Week 3:** Add unit tests for database client (132 statements)
  - Order CRUD operations
  - Position updates
  - Transaction handling

**Phase 2: Core Services (Target: 75% overall)**
- **Week 4:** Improve signal service coverage (25% → 80%)
  - Model registry tests
  - Signal generation tests
  - FastAPI endpoint tests

- **Week 5:** Improve alpaca client coverage (18% → 80%)
  - Order submission tests
  - Position fetching tests
  - Error handling tests

**Phase 3: Orchestration Layer (Target: 80% overall)**
- **Week 6:** Add orchestrator business logic tests
  - Workflow coordination
  - Service integration
  - Error recovery

**Estimated Effort:** 6 weeks (30 working days)

**Success Metrics:**
- [ ] Overall coverage ≥ 80% (from current 17%)
- [ ] All critical modules (risk, database) ≥ 90%
- [ ] All business logic ≥ 85%
- [ ] All FastAPI endpoints have integration tests
- [ ] CI enforces 80% minimum coverage

**Timeline:** Schedule after Phase 1A completion, before production deployment

**Reference:** PR #19 CI logs

---

## Related Documents

- [P0 Tasks](./P0_TASKS.md) - Completed MVP tasks (100%)
- [T6 Retrospective](../LESSONS_LEARNED/t6-paper-run-retrospective.md) - P0 lessons learned
- [Trading Platform Realization Plan](../trading_platform_realization_plan.md) - Original plan
- [Task Index](./INDEX.md) - All task files and status

**Archived References:**
- [P1 Progress Tracker](../ARCHIVE/P1_PROGRESS_20241021.md) - Archived Oct 21 (merged into this file)
- [Next Task](../ARCHIVE/NEXT_TASK_20241021.md) - Replaced by CLI: `./scripts/tasks.py next`
- [File Rename Map](../ARCHIVE/FILE_RENAME_MAP_20241018.md) - Documentation reorganization reference

---

**Last Updated:** October 21, 2024
**Status:** In Progress (73% complete, 8/11 tasks)
**Next Review:** After Phase 1C completion
