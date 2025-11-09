# P2 Planning: Advanced Features & Live Trading Readiness

**Phase:** P2 (Advanced Features)
**Timeline:** 91-120 days (~30 days estimated)
**Status:** 📋 Planning
**Current Task:** [Not started]
**Previous Phase:** P1 (Hardening & Automation - 67% complete)
**Last Updated:** 2025-10-26

---

## 📊 Progress Summary

**Overall:** 0% (0/6 tasks complete)

| Track | Progress | Status |
|-------|----------|--------|
| **Track 1: Trading Infrastructure** | 0% (0/2) | 📋 Planning |
| **Track 2: Production Readiness** | 0% (0/3) | 📋 Planning |
| **Track 3: Live Rollout** | 0% (0/1) | 📋 Planning |

**Completed:**
- [None yet]

**Next:** T0 - TWAP Order Slicer

**See individual PxTy_TASK/PROGRESS/DONE.md files for detailed tracking**

---

## Executive Summary

P0 delivered a complete end-to-end paper trading platform (100%). P1 added production hardening with Redis, monitoring, risk management, and advanced strategies (67% complete). P2 focuses on sophisticated trading features and live trading preparation.

**Key P2 Goals:**
1. Add smart order routing with TWAP slicing for large orders
2. Implement multi-strategy capital allocation
3. Prepare for live trading with secrets management and operational controls
4. Build web console for manual intervention and monitoring
5. Add tax & compliance tracking for regulatory requirements
6. Execute graduated live rollout

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
   - See: `.claude/workflows/03-reviews.md`
   - Frequency: Every 30-60 minutes per component

3. **Deep Review** (MANDATORY before PR)
   - Use workflow: `.claude/workflows/03-reviews.md`
   - Tool: clink + gemini codereviewer → codex codereviewer
   - Reviews: architecture, safety, scalability, test coverage
   - Duration: ~3-5 minutes

**Review Cost Model:**
- Subscription-based: $320-370/month (predictable, unlimited reviews)
- See `CLAUDE.md` for details

---

## Previous Phase → This Phase Transition Analysis

### What P0 Delivered ✅ (100% Complete)

**Complete End-to-End Pipeline:**
- T1: Data ETL with corporate actions, quality gates, freshness checking
- T2: Baseline ML strategy (Alpha158) with MLflow tracking
- T3: Signal Service with model registry and hot reload
- T4: Execution Gateway with idempotent orders
- T5: Orchestrator Service coordinating signal→execution
- T6: Paper Run automation script with notional P&L

**Quality Metrics:**
- 152/152 tests passing (100% pass rate)
- Corporate actions with backward adjustment, cumulative, idempotent
- Quality gates with outlier detection for >30% moves
- Freshness checking with staleness detection

### What P1 Delivered ✅ (67% Complete - 8/12 Tasks)

**Production Infrastructure:**
- ✅ T1: Redis Integration (feature store + pub/sub)
- ✅ T2: DuckDB Analytics Layer
- ✅ T3: Timezone-Aware Timestamps
- ✅ T4: Operational Status Command
- ✅ T5: Real-Time Market Data Streaming (WebSocket)
- ✅ T7: Risk Management System (position limits, circuit breakers)
- ✅ T8: Monitoring & Alerting (Prometheus/Grafana)
- ✅ T6: Advanced Strategies (momentum, mean reversion, ensemble, backtesting framework)

**Still in P1 (4 remaining tasks):**
- ⏳ T0: Enhanced P&L Calculation (realized vs unrealized)
- ⏳ T9: Centralized Logging
- ⏳ T10: CI/CD Pipeline
- ⏳ T12: Walk-forward automation

### Deferred to P2

These features require P0+P1 infrastructure and are P2 priority:

| # | Feature | Why P2 | Effort | Priority |
|---|---------|--------|--------|----------|
| 1 | **TWAP Slicer** | Needs execution gateway + position tracking | 7-9 days | ⭐ HIGH |
| 2 | **Multi-Alpha Allocator** | Needs multiple strategies from P1 | 5-7 days | ⭐ HIGH |
| 3 | **Secrets Management** | Required before live trading | 5-7 days | ⭐ HIGH |
| 4 | **Web Console** | Nice-to-have for operations | 7-10 days | 🔶 MEDIUM |
| 5 | **Tax Tracking** | Required for live trading compliance | 7-10 days | ⭐ HIGH |
| 6 | **Live Rollout** | Final graduation to live trading | 3-5 days | ⭐ HIGH |

**Documentation:** See `docs/TASKS/P1_PLANNING.md` for P1 status

---

## P2 Tasks Breakdown

### Track 1: Trading Infrastructure

#### T0: TWAP Order Slicer ⭐ HIGH PRIORITY

**Goal:** Split large parent orders into smaller child slices for better execution

**Current State (P1):**
- Execution gateway submits single orders
- No support for slicing large orders
- Risk of market impact on large positions

**P2 Requirements:**
```python
# apps/execution_gateway/order_slicer.py
from datetime import datetime, timedelta

class TWAPSlicer:
    """Time-Weighted Average Price order slicer"""

    def __init__(self, parent_order, n_slices: int = 6, horizon_minutes: int = 60):
        self.parent_id = parent_order.id
        self.symbol = parent_order.symbol
        self.side = parent_order.side
        self.total_qty = parent_order.qty
        self.n_slices = n_slices
        self.horizon = timedelta(minutes=horizon_minutes)

    def plan(self, start_time: datetime) -> list[dict]:
        """
        Generate slice schedule with proper remainder handling

        Supports both integer (stocks) and fractional (crypto/FX) quantities.
        For integer quantities: distributes evenly with remainder across first slices.
        For fractional quantities: uses decimal division to preserve precision.
        """
        if self.n_slices <= 0:
            raise ValueError("Number of slices must be positive")
        if self.total_qty <= 0:
            return []  # No quantity to allocate

        # Determine if quantity is integer or fractional
        is_integer_qty = (self.total_qty == int(self.total_qty))

        # Adjust number of slices (must be integer for range() compatibility)
        # For integer quantities < n_slices: reduce slices to avoid 0-qty slices (e.g., qty=3, slices=10 → 3)
        # For fractional quantities < 1: single slice to avoid dust (e.g., 0.5 BTC, slices=10 → 1 slice)
        # For fractional quantities >= 1: use requested slices (e.g., 1.5 BTC, slices=3 → 3 slices)
        if is_integer_qty and self.total_qty < self.n_slices:
            num_slices = int(self.total_qty)
        elif not is_integer_qty and self.total_qty < 1:
            num_slices = 1  # Don't split sub-unit quantities into dust
        else:
            num_slices = max(1, self.n_slices)

        interval = self.horizon / num_slices
        schedule = []
        # Initialize accumulator in same type as total_qty to avoid Decimal/float mixing
        allocated = type(self.total_qty)(0)

        if is_integer_qty:
            # Integer quantity: use integer distribution with remainder
            base_qty = int(self.total_qty / num_slices)
            remainder = int(self.total_qty) - (base_qty * num_slices)

            for i in range(num_slices):
                # Distribute remainder across first slices to ensure total_qty allocated
                slice_qty = base_qty + (1 if i < remainder else 0)
                allocated += slice_qty

                schedule.append({
                    'parent_id': self.parent_id,
                    'slice_num': i,
                    'qty': slice_qty,
                    'scheduled_time': start_time + (interval * i),
                    'symbol': self.symbol,
                    'side': self.side
                })
        else:
            # Fractional quantity: use decimal distribution to preserve precision
            for i in range(num_slices):
                if i == num_slices - 1:
                    # Last slice: allocate remaining to handle rounding
                    slice_qty = self.total_qty - allocated
                else:
                    slice_qty = self.total_qty / num_slices

                allocated += slice_qty

                schedule.append({
                    'parent_id': self.parent_id,
                    'slice_num': i,
                    'qty': slice_qty,
                    'scheduled_time': start_time + (interval * i),
                    'symbol': self.symbol,
                    'side': self.side
                })

        # Verify total allocation equals parent quantity (with floating point tolerance)
        assert abs(allocated - self.total_qty) < 1e-9, f"Allocation mismatch: {allocated} != {self.total_qty}"
        return schedule
```

**DDL for Parent/Child Tracking (extends existing orders table):**
```sql
-- Extend existing orders table with parent/child relationship fields
-- NOTE: Reuses existing lifecycle columns (status, submitted_at, filled_at, etc.)

ALTER TABLE orders ADD COLUMN parent_order_id TEXT REFERENCES orders(client_order_id);
ALTER TABLE orders ADD COLUMN slice_num INTEGER;  -- NULL for parent orders, 0-N for children
ALTER TABLE orders ADD COLUMN total_slices INTEGER;  -- Total child count for parent orders
ALTER TABLE orders ADD COLUMN scheduled_time TIMESTAMPTZ;  -- For TWAP scheduling

-- Index for parent-child lookups
CREATE INDEX IF NOT EXISTS idx_orders_parent_id ON orders(parent_order_id) WHERE parent_order_id IS NOT NULL;

-- View for easy parent order tracking
CREATE OR REPLACE VIEW parent_order_status AS
SELECT
  parent.client_order_id as parent_id,
  parent.symbol,
  parent.side,
  parent.qty as total_qty,
  parent.total_slices,
  COUNT(child.client_order_id) as slices_submitted,
  SUM(CASE WHEN child.status = 'filled' THEN child.filled_qty ELSE 0 END) as filled_qty,
  CASE
    WHEN COUNT(child.client_order_id) < parent.total_slices THEN 'pending'
    WHEN SUM(CASE WHEN child.status IN ('filled', 'canceled') THEN 1 ELSE 0 END) = parent.total_slices THEN 'complete'
    ELSE 'active'
  END as aggregated_status
FROM orders parent
LEFT JOIN orders child ON child.parent_order_id = parent.client_order_id
WHERE parent.parent_order_id IS NULL  -- Only parent orders
GROUP BY parent.client_order_id, parent.symbol, parent.side, parent.qty, parent.total_slices;
```

**Implementation Steps:**
1. **Create TWAPSlicer class** with configurable slicing parameters and remainder handling
2. **Extend existing orders table** with parent/child relationship columns (no new tables)
3. **Implement slice scheduler** with time-based execution
4. **Integrate with circuit breaker/kill switch**: Check breaker state before each slice submission
5. **Track fill progress** and update parent order status
6. **Handle partial fills** and slice failures
7. **Add tests** for slicing logic, fill tracking, error cases, circuit breaker integration
8. **Update execution gateway** to support sliced orders
9. **Add documentation** with examples and safety guarantees

**Acceptance Criteria:**
- [ ] Parent order splits into N child slices correctly (including remainder distribution)
- [ ] Child orders respect time intervals (TWAP)
- [ ] Minimum lot size respected (1 share minimum)
- [ ] Circuit breaker state checked before each slice submission
- [ ] Slicing honors kill switch (blocks new slices when active)
- [ ] Parent order tracks aggregate fill status
- [ ] Partial fills handled correctly
- [ ] Failed slices don't block other slices
- [ ] Tests cover happy path, partial fills, failures, circuit breaker integration
- [ ] Performance: Plan generation <100ms (measured via Python timeit decorator on plan() method in unit tests)
- [ ] All tests pass with >90% coverage
- [ ] ADR documenting TWAP algorithm and safety integration created

**Estimated Effort:** 7-9 days
- Implementation: 3-4 days
- DB migration + testing: 2-3 days
- Scheduler orchestration: 1-2 days
- Integration + documentation: 1 day

**Dependencies:** P0T4 (Execution Gateway), database, existing circuit breaker/kill switch
**Files to Create:**
- `apps/execution_gateway/order_slicer.py`
- `apps/execution_gateway/slice_scheduler.py` (NEW: handles time-based execution)
  - **Implementation:** APScheduler with `BackgroundScheduler`
  - **Precision:** ±1 second accuracy (sufficient for minute-level TWAP intervals)
  - **Mechanism:** Cron-based scheduling with asyncio integration
  - **Robustness:** Job persistence to Redis, automatic retry on failure
  - **Drift handling:** Each slice uses absolute timestamp (not relative delays)
- `tests/apps/execution_gateway/test_order_slicer.py`
- `tests/apps/execution_gateway/test_slice_scheduler.py` (NEW)
- `docs/ADRs/0015-twap-order-slicer.md`

**Files to Modify:**
- `apps/execution_gateway/main.py` (add slicing endpoints + scheduler integration)
- `db/migrations/XXXX_extend_orders_for_slicing.sql` (extends existing orders table)

---

#### T1: Multi-Alpha Allocator ⭐ HIGH PRIORITY

**Goal:** Allocate capital across multiple strategies with risk-aware blending

**Current State (P1):**
- Multiple strategies implemented (baseline, momentum, mean reversion, ensemble)
- Each strategy produces independent signals
- No automated capital allocation across strategies

**P2 Requirements:**
```python
# libs/allocation/multi_alpha.py
import polars as pl
from typing import Literal

AllocMethod = Literal['rank_aggregation', 'inverse_vol', 'equal_weight']

class MultiAlphaAllocator:
    """Allocate capital across multiple alpha strategies"""

    def __init__(
        self,
        method: AllocMethod = 'rank_aggregation',
        per_strategy_max: float = 0.40,  # Max 40% to any strategy
        correlation_threshold: float = 0.70  # Alert if corr > 70%
    ):
        self.method = method
        self.per_strategy_max = per_strategy_max
        self.correlation_threshold = correlation_threshold

    def allocate(
        self,
        signals: dict[str, pl.DataFrame],  # strategy_id -> signals (symbol, score, weight)
        strategy_stats: dict[str, dict]     # strategy_id -> {vol, sharpe, ...}
    ) -> pl.DataFrame:
        """
        Allocate capital weights across strategies

        Integration with orchestrator:
        1. Orchestrator collects Signal objects from each strategy
        2. Converts Signal.target_weights (dict) to pl.DataFrame per strategy
        3. Passes dict[strategy_id, pl.DataFrame] to allocator
        4. Allocator returns blended pl.DataFrame with columns: [symbol, final_weight, contributing_strategies]
        5. Orchestrator converts back to single aggregated Signal for execution
        """
        if self.method == 'rank_aggregation':
            return self._rank_aggregation(signals)
        elif self.method == 'inverse_vol':
            return self._inverse_vol(signals, strategy_stats)
        elif self.method == 'equal_weight':
            return self._equal_weight(signals)

    def check_correlation(
        self,
        recent_returns: dict[str, pl.DataFrame]
    ) -> dict[str, float]:
        """Check inter-strategy correlation and alert if too high"""
        pass
```

**Implementation Steps:**
1. **Create MultiAlphaAllocator class** with 3 methods (rank aggregation, inverse-vol, equal-weight)
2. **Implement rank aggregation**: Average normalized ranks across strategies
3. **Implement inverse volatility**: Weight inversely to recent realized vol
4. **Implement correlation monitoring**: Alert when strategies too correlated
5. **Add per-strategy caps**: Enforce maximum allocation per strategy
6. **Create tests**: Unit tests for each method, integration tests for full allocation
7. **Update orchestrator integration**: Add Signal→DataFrame conversion helpers and allocator invocation
8. **Add documentation**: Allocation methodology, parameter tuning, and orchestrator integration pattern

**Acceptance Criteria:**
- [ ] Rank aggregation implemented correctly
- [ ] Inverse volatility weighting implemented
- [ ] Equal weight baseline implemented
- [ ] Per-strategy max enforced (40% default)
- [ ] Correlation monitoring alerts when correlation >70% (logged as WARNING + emitted to Redis pub/sub 'alerts' channel with metric name='strategy_correlation')
- [ ] Total allocated weight sums to 100% (or configured total)
- [ ] Tests cover all three methods
- [ ] Performance: Allocation <500ms for 10 strategies, 100 symbols (measured via pytest-benchmark in integration tests)
- [ ] All tests pass with >90% coverage
- [ ] ADR documenting allocation methodology created

**Estimated Effort:** 5-7 days
**Dependencies:** P1T6 (Advanced Strategies), P1T7 (Risk Management)
**Files to Create:**
- `libs/allocation/multi_alpha.py`
- `tests/libs/allocation/test_multi_alpha.py`
- `docs/ADRs/0016-multi-alpha-allocation.md`

**Files to Modify:**
- `apps/orchestrator/orchestrator.py` (integrate allocator)

---

### Track 2: Production Readiness

#### T2: Secrets Management ⭐ HIGH PRIORITY

**Goal:** Secure API keys and credentials before live trading

**Current State (P1):**
- Credentials stored in `.env` file
- API keys committed to repository (in `.gitignore` but risky)
- No key rotation mechanism

**P2 Requirements:**
```python
# libs/secrets/manager.py
from abc import ABC, abstractmethod

class SecretManager(ABC):
    """Abstract secret manager interface"""

    @abstractmethod
    def get_secret(self, name: str) -> str:
        """Retrieve secret by name"""
        pass

class VaultSecretManager(SecretManager):
    """HashiCorp Vault implementation"""

    def __init__(self, vault_addr: str, token: str):
        self.vault_addr = vault_addr
        self.token = token

    def get_secret(self, name: str) -> str:
        # Use hvac library to fetch from Vault
        pass

class AWSSecretsManager(SecretManager):
    """AWS Secrets Manager implementation"""

    def __init__(self, region: str):
        self.region = region

    def get_secret(self, name: str) -> str:
        # Use boto3 to fetch from AWS Secrets Manager
        pass
```

**Migration Steps:**
1. Choose secrets backend (Vault, AWS Secrets Manager, or Doppler)
2. Migrate credentials from `.env` to chosen backend
3. Update services to fetch secrets on startup
4. Add secret rotation mechanism (90-day rotation)
5. Remove `.env` from codebase (except template)
6. Add audit logging for secret access

**Implementation Steps:**
1. **Create SecretManager interface** with pluggable backends
2. **Implement Vault backend** (recommended for self-hosted)
3. **Implement AWS Secrets Manager backend** (if using AWS)
4. **Create dual-mode config pattern**: Secrets via SecretManager, non-secret config via Pydantic Settings (env vars)
5. **Update all services** to separate secrets (API keys, tokens) from config (ports, timeouts, feature flags)
6. **Add secret rotation script** for API keys
7. **Create migration guide** from `.env` to secrets backend with config separation
8. **Maintain `.env.template`** for local dev with placeholder secrets + real config
9. **Add tests** for secret retrieval, fallback handling, and dual-mode operation
10. **Update documentation** with security best practices and config vs secrets pattern

**Acceptance Criteria:**
- [ ] SecretManager interface defined
- [ ] Vault backend implemented and tested
- [ ] AWS Secrets Manager backend implemented (optional)
- [ ] All services fetch secrets from manager (no more .env)
- [ ] Secret rotation script functional
- [ ] Audit logging for secret access
- [ ] `.env.template` provided (no actual secrets)
- [ ] Migration guide documented
- [ ] All tests pass with >90% coverage
- [ ] ADR documenting secrets architecture created

**Estimated Effort:** 5-7 days
- Backend provisioning (Vault/AWS setup): 1-2 days
- Implementation + migration: 2-3 days
- Service wiring + testing: 1-2 days
- CI regression testing + documentation: 1 day

**Dependencies:** Infrastructure (Vault or AWS account), CI pipeline access
**Files to Create:**
- `libs/secrets/manager.py`
- `libs/secrets/vault_backend.py`
- `libs/secrets/aws_backend.py`
- `tests/libs/secrets/test_secret_manager.py`
- `docs/ADRs/ADR-XXX-secrets-management.md`
- `docs/RUNBOOKS/secret-rotation.md`

**Files to Modify:**
- All `apps/*/main.py` (use SecretManager)
- `.gitignore` (remove .env, add .env.template)

---

#### T3: Web Console 🔶 MEDIUM PRIORITY

**Goal:** Build minimal web UI for manual intervention and monitoring

**Current State (P1):**
- CLI commands for operations (`make status`, etc.)
- No graphical interface
- No manual order entry capability

**P2 Requirements:**

**Tech Choice:** Streamlit (fast to build) or Next.js + FastAPI backend (more control)

**Features:**
1. **Dashboard**: Live positions, P&L, strategy status
2. **Manual Order Entry**: Submit orders with confirmation (audit logged)
3. **Strategy Controls**: Enable/disable strategies per symbol
4. **Emergency Kill Switch**: Cancel all orders, flatten positions, block new signals
5. **Audit Log Viewer**: View all manual actions with timestamps

**Streamlit Implementation (MVP):**
```python
# apps/web_console/app.py
import streamlit as st
import requests
import os

st.set_page_config(page_title="Trading Platform Console", layout="wide")

# Configuration via environment variables (avoid hardcoded URLs)
EXECUTION_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

# Cache data fetching to prevent blocking I/O on every UI interaction
@st.cache_data(ttl=10)  # Cache for 10 seconds
def fetch_positions():
    """Fetch positions from execution gateway with error handling"""
    try:
        response = requests.get(f"{EXECUTION_GATEWAY_URL}/api/v1/positions", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        st.error(f"Failed to fetch positions: {e}")
        return []

# Dashboard tab
with st.expander("📊 Dashboard", expanded=True):
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Total P&L", "$1,234.56", "+5.2%")
    with col2:
        st.metric("Open Positions", "12")
    with col3:
        st.metric("Strategies Active", "3/4")

    # Positions table from execution gateway (port 8002)
    # Using cached function to prevent blocking on every UI interaction
    positions = fetch_positions()
    if positions:
        st.dataframe(positions)
    else:
        st.warning("No positions data available")

# Manual Order Entry tab
with st.expander("📝 Manual Order Entry"):
    symbol = st.text_input("Symbol")
    side = st.selectbox("Side", ["buy", "sell"])
    qty = st.number_input("Quantity", min_value=1)

    if st.button("Submit Order"):
        with st.form("confirm_order"):
            st.warning(f"Confirm: {side.upper()} {qty} shares of {symbol}?")
            reason = st.text_input("Reason (required for audit)")
            if st.form_submit_button("Confirm"):
                # Submit with audit log
                pass

# Kill Switch tab
with st.expander("🚨 Emergency Kill Switch", expanded=False):
    st.error("WARNING: This will cancel all orders and flatten all positions")
    reason = st.text_area("Reason (required)")
    if st.button("ACTIVATE KILL SWITCH", type="primary"):
        if not reason:
            st.error("Reason required for audit")
        else:
            # Activate kill switch with audit
            pass
```

**Implementation Steps:**
1. **Choose tech stack** (Streamlit for MVP)
2. **Create dashboard** with positions, P&L, strategy status
3. **Add manual order entry** with confirmation dialog
4. **Implement strategy controls** (enable/disable toggles)
5. **Build kill switch** with confirmation and audit logging
6. **Add audit log viewer** with filtering
7. **Add authentication** (basic auth for MVP)
8. **Add tests** for backend endpoints
9. **Deploy as Docker container**
10. **Add documentation** with screenshots

**Acceptance Criteria:**
- [ ] Dashboard shows live positions and P&L
- [ ] Manual order entry works with confirmation
- [ ] All manual actions logged to audit table
- [ ] Strategy enable/disable toggles functional
- [ ] Kill switch cancels all orders and flattens positions
- [ ] Kill switch blocks new signals until manual reset
- [ ] Authentication required (basic auth minimum)
- [ ] UI loads and renders correctly on 768px viewport (tablet test via Playwright automated browser test)
- [ ] Docker container for deployment (Dockerfile builds successfully, container starts and serves on port 8501)
- [ ] All tests pass with >85% coverage
- [ ] User guide with screenshots

**Estimated Effort:** 7-10 days
**Dependencies:** P0T4 (Execution Gateway), P1T7 (Risk Management)
**Files to Create:**
- `apps/web_console/app.py` (Streamlit)
- `apps/web_console/Dockerfile`
- `tests/apps/web_console/test_endpoints.py`
- `docs/RUNBOOKS/web-console-user-guide.md`

**Files to Modify:**
- `docker-compose.yml` (add web console service)
- `db/migrations/XXXX_add_audit_log.sql`

---

#### T4: Tax & Compliance Tracking ⭐ HIGH PRIORITY

**Goal:** Track tax lots and generate compliance reports

**Current State (P1):**
- Orders and fills tracked
- No tax lot accounting
- No wash sale detection
- No compliance reporting

**P2 Requirements:**
```python
# libs/tax/lot_tracker.py
from enum import Enum
from datetime import date, timedelta

class LotMethod(Enum):
    FIFO = "fifo"
    LIFO = "lifo"
    SPEC_ID = "specific_id"

class TaxLotTracker:
    """Track tax lots for realized P&L calculation"""

    def __init__(self, method: LotMethod = LotMethod.FIFO):
        self.method = method

    def add_lot(
        self,
        symbol: str,
        qty: float,
        price: float,
        acquired_date: date,
        trade_id: str
    ):
        """Record new tax lot on buy"""
        pass

    def realize_gains(
        self,
        symbol: str,
        qty: float,
        price: float,  # Sale price (unadjusted)
        sold_date: date,
        trade_id: str
    ) -> dict:
        """
        Close lots on sell, return realized gains.

        CRITICAL: Integrates with P0 corporate action adjustments
        - Cost basis uses adjusted acquisition prices (from P0T1)
        - Sale price is market price (not adjusted - adjustment already in historical data)
        - Gains calculated: (sale_price - adjusted_cost_basis) * qty

        Returns: {
            'short_term_gain': 123.45,
            'long_term_gain': 67.89,
            'lots_closed': [...],
            'wash_sales': [...],
            'corporate_actions_applied': ['split_2_for_1_2024-01-15']  # Audit trail
        }
        """
        pass

    def check_wash_sale(
        self,
        symbol: str,
        sold_date: date,
        loss_amount: float
    ) -> bool:
        """Check if sale triggers wash sale rule (30-day window)"""
        # Wash sale if bought same stock within 30 days before/after loss
        pass
```

**DDL for Tax Lots:**
```sql
-- Enable UUID extension (PostgreSQL)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS tax_lots (
  id TEXT PRIMARY KEY DEFAULT uuid_generate_v4()::TEXT,  -- UUID for guaranteed uniqueness
  symbol TEXT NOT NULL,
  qty NUMERIC NOT NULL,
  price NUMERIC NOT NULL,
  acquired_date DATE NOT NULL,
  acquisition_trade_id TEXT NOT NULL,
  closed_qty NUMERIC DEFAULT 0,
  status TEXT CHECK (status IN ('open', 'closed')) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS realized_gains (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  lot_id TEXT NOT NULL REFERENCES tax_lots(id),
  qty NUMERIC NOT NULL,
  cost_basis NUMERIC NOT NULL,
  proceeds NUMERIC NOT NULL,
  gain_loss NUMERIC NOT NULL,
  term TEXT CHECK (term IN ('short_term', 'long_term')) NOT NULL,
  acquired_date DATE NOT NULL,
  sold_date DATE NOT NULL,
  is_wash_sale BOOLEAN DEFAULT FALSE,
  sale_trade_id TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wash_sales (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  disallowed_loss NUMERIC NOT NULL,
  original_sale_date DATE NOT NULL,
  triggering_purchase_date DATE NOT NULL,
  detected_at TIMESTAMPTZ DEFAULT now()
);
```

**Implementation Steps:**
1. **Create TaxLotTracker class** with FIFO/LIFO support
2. **Add tax_lots table** to database schema
3. **Integrate with P0 corporate action data**: Retrieve adjustment history for cost basis calculation
4. **Track lot creation** on all buy orders (store both original and adjusted prices)
5. **Track lot closure** on all sell orders with FIFO/LIFO
6. **Calculate realized gains** (short-term vs long-term) using adjusted cost basis
7. **Implement wash sale detection** (30-day rule)
8. **Generate trade blotter CSV** for daily exports (include corporate action footnotes)
9. **Add PDT monitoring** (pattern day trader rule)
10. **Create compliance report** job (daily/monthly)
11. **Add tests** for FIFO, LIFO, wash sales, corporate action integration, compliance
12. **Add documentation** with tax compliance guidance and corporate action handling
13. **Position limit checks** for regulatory requirements

**Acceptance Criteria:**
- [ ] Tax lots tracked for all buy orders (with corporate action adjustment history)
- [ ] FIFO and LIFO methods implemented
- [ ] Cost basis uses P0 corporate action adjustments (splits, dividends)
- [ ] Realized gains calculated correctly (short vs long term) with adjusted basis
- [ ] Wash sale detection functional (30-day window)
- [ ] Trade blotter CSV exported daily (includes corporate action footnotes)
- [ ] PDT monitoring warns on 4th day trade within 5 days
- [ ] Compliance report includes all required fields
- [ ] Tests cover FIFO, LIFO, wash sales, corporate action scenarios, edge cases
- [ ] Performance: Lot closure <100ms for 1000 lots (measured via pytest-benchmark with realistic lot distribution)
- [ ] All tests pass with >90% coverage
- [ ] Tax compliance guide documented (including corporate action treatment)

**Estimated Effort:** 7-10 days
- Core tracker implementation (FIFO/LIFO): 2-3 days
- Wash sale detection logic: 2-3 days
- Corporate action integration (P0 alignment): 1-2 days
- Compliance exports + PDT monitoring: 1-2 days
- Testing + documentation: 1 day

**Dependencies:** P0T4 (Execution Gateway), P0T1 (Corporate Actions), database
**Files to Create:**
- `libs/tax/lot_tracker.py`
- `libs/tax/compliance_reporter.py`
- `tests/libs/tax/test_lot_tracker.py`
- `tests/libs/tax/test_wash_sales.py`
- `docs/CONCEPTS/tax-accounting.md`
- `docs/RUNBOOKS/tax-compliance.md`

**Files to Modify:**
- `apps/execution_gateway/main.py` (track lots on fills)
- `db/migrations/XXXX_add_tax_lots.sql`

---

### Track 3: Live Rollout

#### T5: Live Rollout Preparation ⭐ HIGH PRIORITY

**Goal:** Prepare and execute graduated rollout to live trading

**Current State (P1):**
- Paper trading fully functional
- No live trading checklist
- No graduated rollout plan

**P2 Requirements:**

**Pre-Live Checklist:**
```markdown
## Live Trading Readiness Checklist

### 🔒 Security
- [ ] Secrets migrated from .env to Vault/AWS (T2)
- [ ] API keys rotated within last 90 days
- [ ] Read-only dashboard credentials separated
- [ ] Audit logging enabled for all manual actions
- [ ] 2FA enabled on brokerage account

### 🛡️ Safety
- [ ] Circuit breakers tested and functional (P1T7)
- [ ] Kill switch tested and functional (T3)
- [ ] Position limits configured and enforced
- [ ] Daily loss limits configured
- [ ] Stale order cleanup tested (>15m cancellation)

### 🧪 Testing
- [ ] Backtest replay parity verified (same signals)
- [ ] Paper trading run for 2+ weeks without issues
- [ ] Stress tests passed (data drop, broker errors, position desync)
- [ ] Reconciler tested and functional
- [ ] All integration tests passing

### 📊 Monitoring
- [ ] Prometheus metrics exporting (P1T8)
- [ ] Grafana dashboards configured
- [ ] Alerts configured and tested
- [ ] PagerDuty/email alerts functional
- [ ] Log aggregation operational

### 💰 Brokerage
- [ ] Account verified (buying power, margin, shorting)
- [ ] Market hours policy defined (regular vs extended)
- [ ] Supported order types documented
- [ ] Position limits per regulation verified
- [ ] PDT monitoring active (if <$25k account)

### 📋 Operations
- [ ] Runbooks created for common scenarios
- [ ] Disaster recovery playbook tested
- [ ] Manual override procedures documented
- [ ] Web console accessible and tested (T3)
- [ ] Team completes operational training checklist (documented walkthrough of: kill switch activation, manual order entry, reading logs, disaster recovery steps)
```

**Graduated Rollout Schedule:**
```markdown
## Phase 1: Tiny Notional (Days 1-3)
- **Capital:** $100-$500
- **Symbols:** 1-2 (highly liquid: SPY, QQQ)
- **Goal:** Verify end-to-end live execution
- **Success Criteria:**
  - Orders fill successfully
  - P&L tracking accurate
  - No circuit breaker trips
  - Monitoring captures all events

## Phase 2: Expanded Symbols (Days 4-7)
- **Capital:** $500-$2,000
- **Symbols:** 3-5 (add AAPL, MSFT, etc.)
- **Goal:** Test multi-symbol coordination
- **Success Criteria:**
  - All strategies execute correctly
  - Allocator works across symbols
  - Risk limits enforced
  - No operational issues

## Phase 3: Full Strategy Activation (Days 8-14)
- **Capital:** $2,000-$10,000
- **Symbols:** Full universe (10-20)
- **Goal:** Run all strategies at scale
- **Success Criteria:**
  - All strategies profitable or within expectations
  - Slippage within backtest assumptions
  - Real-time data stable
  - Tax tracking accurate

## Phase 4: Capital Ramp (Days 15-30)
- **Capital:** Gradual increase to target (e.g., $50k)
- **Symbols:** Full universe
- **Goal:** Scale to production capacity
- **Success Criteria:**
  - Performance matches backtests
  - Infrastructure handles load
  - Costs within budget
  - Team confident in operations
```

**Brokerage Account Monitoring:**
```python
# libs/brokerage/account_monitor.py
from alpaca.trading import TradingClient

class AccountMonitor:
    """Monitor brokerage account status and compliance"""

    def __init__(self, client: TradingClient):
        self.client = client

    def check_buying_power(self) -> float:
        """Verify buying power available"""
        account = self.client.get_account()
        return float(account.buying_power)

    def check_pdt_status(self) -> dict:
        """Check pattern day trader status"""
        account = self.client.get_account()
        return {
            'is_pdt': account.pattern_day_trader,
            'day_trades_left': 3 - account.daytrade_count if not account.pattern_day_trader else None,
            'requires_25k': not account.pattern_day_trader and float(account.equity) < 25000
        }

    def check_margin_status(self) -> dict:
        """
        Check margin and shorting eligibility

        CRITICAL: trading_blocked indicates if account is blocked from trading
        (e.g., violations), NOT margin eligibility. Cash accounts with no
        violations will have trading_blocked=False but multiplier=1.

        Use multiplier to determine margin capability:
        - multiplier = 1: Cash account (no margin)
        - multiplier = 2: Standard margin account (Reg T)
        - multiplier = 4: Day trading account (>=$25k equity)
        """
        account = self.client.get_account()
        multiplier = float(account.multiplier)

        return {
            'margin_enabled': multiplier > 1,  # True if margin account
            'account_type': 'margin' if multiplier > 1 else 'cash',
            'shorting_enabled': account.shorting_enabled,
            'multiplier': multiplier,
            'day_trading_buying_power': multiplier >= 4  # >=4x for pattern day traders
        }

    def enforce_market_hours(self, allow_extended: bool = False) -> bool:
        """
        Check if trading allowed based on market hours policy

        Uses broker's calendar API to handle:
        - Early closes (half-day before holidays)
        - Holidays (market closed)
        - Daylight Saving Time transitions
        - Extended hours (4 AM - 8 PM ET)
        """
        clock = self.client.get_clock()
        if not clock.is_open:
            return False

        # During extended hours, is_open=True but we may want to restrict to regular hours only
        if not allow_extended:
            # Fetch today's official market hours from calendar API
            # This handles early closes, holidays, and DST correctly
            today_str = clock.timestamp.date().isoformat()
            calendar = self.client.get_calendar(start=today_str, end=today_str)

            if not calendar:
                return False  # Market is closed today (holiday)

            market_open = calendar[0].open
            market_close = calendar[0].close

            # All times are timezone-aware (UTC)
            if not (market_open <= clock.timestamp <= market_close):
                return False  # Outside regular trading hours

        return True
```

**Implementation Steps:**
1. **Create live trading checklist** (pre-live verification)
2. **Define graduated rollout schedule** (4 phases over 30 days)
3. **Implement account monitor** for brokerage checks
4. **Create market hours enforcement** with extended hours policy
5. **Add position limit verification** against regulatory requirements
6. **Create runbooks** for common scenarios (outage, data failure, accidental position)
7. **Build disaster recovery playbook** with exact commands
8. **Create monitoring dashboard** for live rollout progress
9. **Add tests** for account checks and market hours
10. **Document rollout process** with decision criteria

**Acceptance Criteria:**
- [ ] Pre-live checklist completed 100%
- [ ] Graduated rollout schedule defined
- [ ] Brokerage account verified (buying power, margin, shorting)
- [ ] Market hours enforcement functional
- [ ] Extended hours policy documented
- [ ] PDT monitoring warns before 4th day trade
- [ ] Position limits verified against regulation
- [ ] Runbooks created for: outage, data failure, accidental position, model rollback
- [ ] Disaster recovery playbook tested
- [ ] Team trained on procedures
- [ ] All tests pass with >90% coverage
- [ ] Live trading approval from stakeholders

**Estimated Effort:** 3-5 days (preparation) + 30 days (execution)
**Dependencies:** All P0, P1, P2 tasks complete
**Files to Create:**
- `libs/brokerage/account_monitor.py`
- `tests/libs/brokerage/test_account_monitor.py`
- `docs/RUNBOOKS/live-rollout-checklist.md`
- `docs/RUNBOOKS/graduated-rollout-schedule.md`
- `docs/RUNBOOKS/disaster-recovery-playbook.md`
- `docs/RUNBOOKS/common-scenarios.md`

**Files to Modify:**
- `apps/orchestrator/orchestrator.py` (market hours enforcement)
- Grafana dashboards (add live rollout tracking)

---

## P2 Roadmap & Priorities

### Phase Breakdown

**Priority Order:**
1. **T0: TWAP Slicer** (7-9 days) - Better execution for large orders
2. **T1: Multi-Alpha Allocator** (5-7 days) - Capital allocation across strategies
3. **T2: Secrets Management** (5-7 days) - Security requirement for live
4. **T4: Tax & Compliance** (7-10 days) - Regulatory requirement
5. **T3: Web Console** (7-10 days) - Operational convenience (can be parallel)
6. **T5: Live Rollout** (3-5 days prep + 30 days execution) - Final graduation

**Parallel Tracks:**
- Track 1 (T0, T1): Can run sequentially or parallel
- Track 2 (T2, T4): Should complete before T5
- Track 3 (T3): Can run in parallel with Track 1
- Track 4 (T5): Starts only after all others complete

---

## Total P2 Effort Estimates

**IMPORTANT:** These estimates assume P1 is 100% complete. If P1 tasks remain, add their effort before calculating P2 timeline.

### Minimum Viable P2
- **Time:** 24-33 days (preparation only, no web console)
- **Focus:** T0, T1, T2, T4 only (7-9 + 5-7 + 5-7 + 7-10 = 24-33 days)
- **Breakdown:** TWAP 7-9 + Allocator 5-7 + Secrets 5-7 + Tax 7-10 days
- **Output:** Live-trading capable system without UI

### Recommended P2
- **Time:** 34-48 days (preparation) + 30 days (rollout)
- **Focus:** All 6 tasks (full phase scope with updated estimates)
- **Output:** Complete live trading system with operational UI and graduated rollout

**Breakdown (Recommended):**
- T0 TWAP: 7-9 days
- T1 Allocator: 5-7 days
- T2 Secrets: 5-7 days
- T3 Web Console: 7-10 days
- T4 Tax: 7-10 days
- T5 Rollout Prep: 3-5 days
- **Subtotal:** 34-48 days prep
- Rollout Execution: 30 days (phases 1-4)
- **Total:** 34-48 days prep + 30 days rollout = 64-78 days end-to-end

---

## Success Metrics

### P2 Success Criteria
- [ ] TWAP slicer reduces market impact by >20% vs single orders
- [ ] Multi-alpha allocator diversifies across 3+ strategies
- [ ] All secrets migrated from .env to secure backend
- [ ] Tax lots tracked for 100% of trades
- [ ] Live trading checklist 100% complete before go-live
- [ ] Graduated rollout completes all 4 phases without incidents

### Performance Targets
- [ ] TWAP slice planning <100ms
- [ ] Multi-alpha allocation <500ms for 10 strategies
- [ ] Secret retrieval <50ms
- [ ] Tax lot closure <100ms for 1000 lots
- [ ] Web console page load <2s

---

## Testing Strategy

### Unit Tests
- Target: >90% coverage for all new modules
- Mock external dependencies (Vault, brokerage API)
- Fast execution (<10 seconds for full suite)

### Integration Tests
- Test TWAP execution with mock fills
- Test multi-alpha allocation end-to-end
- Test secret rotation workflow
- Test tax lot accounting with real-world scenarios

### End-to-End Tests
- Complete paper run with TWAP + allocator
- Verify tax accounting over multi-day period
- Test kill switch activation and recovery
- Test graduated rollout Phase 1 (tiny notional)

### Performance Tests
- TWAP planning latency under load
- Allocator performance with 100 symbols
- Secret manager concurrent access
- Web console load testing (10 concurrent users)

---

## Documentation Requirements

### For Each Task
- [ ] ADR documenting technical decisions
- [ ] Implementation guide with examples
- [ ] API documentation (if new endpoints)
- [ ] Updated README with new features
- [ ] Lessons learned / retrospective

### New Concept Docs Needed
- [ ] `docs/CONCEPTS/order-slicing.md` - TWAP algorithm explained
- [ ] `docs/CONCEPTS/multi-alpha-allocation.md` - Allocation methods
- [ ] `docs/CONCEPTS/tax-accounting.md` - Tax lot methodology
- [ ] `docs/RUNBOOKS/live-rollout-checklist.md` - Pre-live verification
- [ ] `docs/RUNBOOKS/graduated-rollout-schedule.md` - Rollout phases
- [ ] `docs/RUNBOOKS/disaster-recovery-playbook.md` - Emergency procedures

---

## Dependencies & Prerequisites

### ✅ P1 Completion Gate Status

**UPDATED:** P1 is 87% complete (13/15 tasks). Core infrastructure tasks required for P2 are DONE.

**P1 Core Requirements (for P2):**
- [x] **P1T9: Centralized Logging** ✅ DONE (Oct 22, PR#28) - Debugging operational issues
- [x] **P1T10: CI/CD Pipeline** ✅ DONE (Oct 23, PR#30) - Safe deployment automation
- [x] **P1T12: Workflow Review & Pre-commit Automation** ✅ DONE (Oct 25, PR#32) - Quality gates

**P1 Optional Tasks (can defer to P2/P3):**
- [ ] **P1T0: Enhanced P&L Calculation** ⏳ OPTIONAL - Can implement alongside P2T4 (Tax & Compliance)
- [ ] **P1T13: Documentation Enhancement** ⏳ OPTIONAL - Low priority, can defer
- [ ] **Walk-forward Validation** ⏳ NOT IMPLEMENTED - Mentioned in strategy READMEs as future enhancement

**Rationale for Optional Status:**
- **P1T0 (Enhanced P&L)**: While valuable for live trading, basic P&L calculation exists in P0. Enhanced breakdown (realized/unrealized) can be added during P2T4 (Tax & Compliance) implementation since both touch similar accounting logic.
- **P1T13 (Documentation)**: Nice-to-have improvement, not blocking for P2 implementation.
- **Walk-forward Validation**: P1T6 delivered mean reversion, momentum, ensemble, and backtesting framework (PR #35). Walk-forward optimization was mentioned as future work in strategy READMEs but NOT implemented. Current backtesting framework is sufficient for P2; walk-forward can be added later if needed for strategy validation.

**Gate Status:** ✅ **PASSED** - All core P1 infrastructure complete, P2 can proceed

**Verification:**
1. ✅ Core P1 tasks verified in `/docs/TASKS/P1_PLANNING.md` (87% complete)
2. ⏳ Run full regression: `make test && make lint` (verify before starting P2T0)
3. ✅ P1 acceptance criteria met for core tasks
4. ✅ Can proceed with P2T0 (TWAP Slicer)

---

### Infrastructure
- [ ] HashiCorp Vault or AWS Secrets Manager account
- [ ] Brokerage account with live API access
- [ ] Sufficient capital for graduated rollout ($100-$50k+)
- [ ] Docker for web console deployment

### External Services
- [ ] Alpaca live API credentials
- [ ] PagerDuty account (optional, for alerts)
- [ ] SSL certificates for web console (if public)

### Skills/Knowledge
- [ ] TWAP algorithm and smart order routing
- [ ] Portfolio allocation theory (rank aggregation, inverse-vol)
- [ ] Tax lot accounting (FIFO, LIFO, wash sales)
- [ ] Web development (Streamlit or Next.js)
- [ ] Secrets management best practices
- [ ] **Operational readiness and incident response training** (kill switch activation, manual overrides, log analysis, disaster recovery)

---

## Risk & Mitigation

### Risk 1: Market Impact from Large Orders
**Impact:** High (slippage costs reduce profitability)
**Probability:** Medium (depends on order sizes)
**Mitigation:**
- TWAP slicer spreads orders over time (T0)
- Benchmark slippage against backtests
- Start with small position sizes in rollout
- Monitor realized vs assumed slippage

### Risk 2: Incorrect Tax Accounting
**Impact:** High (tax penalties, audit risk)
**Probability:** Low (if tested thoroughly)
**Mitigation:**
- Comprehensive tests for FIFO, LIFO, wash sales
- Validate against broker tax reports
- Consult with CPA before live trading
- Use conservative accounting (FIFO default)

### Risk 3: Security Breach of API Keys
**Impact:** Critical (account takeover, financial loss)
**Probability:** Low (with proper secrets management)
**Mitigation:**
- Secrets backend with encryption (T2)
- 90-day key rotation policy
- Audit logging for all secret access
- 2FA on brokerage account
- Read-only credentials for dashboards

### Risk 4: Live Trading Losses
**Impact:** High (real money at risk)
**Probability:** Medium (strategies may not perform)
**Mitigation:**
- Graduated rollout starts with $100-$500 (T5)
- Circuit breakers limit daily losses (P1T7)
- Kill switch for emergency stops (T3)
- Extensive paper trading validation (2+ weeks)
- Real-time monitoring and alerts (P1T8)

### Risk 5: Operational Errors
**Impact:** Medium (manual mistakes)
**Probability:** Low (with proper tools and training)
**Mitigation:**
- Web console with confirmation dialogs (T3)
- Audit logging for all manual actions
- Runbooks for common scenarios
- Team training before live trading
- Disaster recovery playbook tested

---

## Next Steps

### Immediate (Phase Start)
1. [x] ~~Review P1 progress~~ → **CRITICAL: Complete ALL remaining P1 tasks FIRST (P1T0, P1T9, P1T10, P1T12)**
2. [ ] **DO NOT START P2** until P1 completion gate verified (see Dependencies section)
3. [ ] Finalize P2 plan (this document) - obtain stakeholder approval
4. [ ] After P1 complete: Generate task files: `./scripts/tasks.py generate-tasks-from-phase P2`
5. [ ] After P1 complete: Begin first task: `./scripts/tasks.py start P2T0`

### This Week
- [ ] Design TWAP algorithm
- [ ] Research multi-alpha allocation methods
- [ ] Choose secrets backend (Vault vs AWS)

### This Month
- [ ] Complete Track 1 (TWAP + Allocator)
- [ ] Complete Track 2 (Secrets + Tax)
- [ ] Begin Track 3 (Web Console)

---

## Technical Debt & Known Issues

**From P1 (Remaining):**
1. **Enhanced P&L Calculation** (P1T0)
   - Status: Deferred to P1
   - Impact: Using notional P&L only
   - Plan: Complete before live trading

2. **Centralized Logging** (P1T9)
   - Status: Deferred to P1
   - Impact: Logs scattered across services
   - Plan: Complete before live trading (debugging requirement)

3. **CI/CD Pipeline** (P1T10)
   - Status: Deferred to P1
   - Impact: Manual deployments
   - Plan: Complete before scaling operations

**New Debt (Acceptable for P2):**
- VWAP slicing deferred (TWAP only for P2)
- Mean-variance allocator deferred (rank/inverse-vol only for P2)
- Advanced web console features deferred (basic MVP for P2)

---

## Related Documents

- [P0 Tasks](./P0_TASKS.md) - Completed MVP tasks (100%)
- [P1 Planning](./P1_PLANNING.md) - Previous phase (67% complete)
- [Trading Platform Realization Plan](../trading_platform_realization_plan.md) - Master plan
- [Task Index](./INDEX.md) - All task files and status

---

**Last Updated:** 2025-10-26
**Status:** Planning (0% complete, 0/6 tasks)
**Next Review:** After T0 completion
