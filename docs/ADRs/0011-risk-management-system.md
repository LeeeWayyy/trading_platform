# ADR-0011: Risk Management System

**Status:** Proposed
**Date:** 2025-10-19
**Author:** Claude Code
**Task:** P1.2T3 - Risk Management System

---

## Context

The trading platform currently lacks systematic risk controls. While the execution gateway handles order placement with idempotency and dry-run support, there are no safeguards to prevent:

1. **Excessive Position Sizes** - Single symbol positions can grow unbounded
2. **Portfolio Concentration** - Total notional exposure has no limits
3. **Catastrophic Losses** - No automatic shutdown on drawdown
4. **Stale Data Trading** - No checks for data freshness before orders
5. **Broker Errors** - No circuit breaker for repeated API failures

As we approach production trading, implementing a comprehensive risk management system is **critical** for:
- Capital preservation
- Regulatory compliance (pattern day trading rules, margin requirements)
- Operational safety
- Investor protection

**Current State (P1.2T1):**
- ✅ Real-time market data streaming (Alpaca WebSocket)
- ✅ Position tracking in PostgreSQL
- ✅ Real-time P&L calculation
- ❌ No position limits
- ❌ No loss limits
- ❌ No circuit breakers
- ❌ No automated risk monitoring

**CLAUDE.md Requirements:**
```python
# Example from CLAUDE.md (not yet implemented)
if redis.get("cb:state") == b"TRIPPED":
    raise CircuitBreakerTripped()

if abs(current_pos + order.qty) > limits.max_pos_per_symbol:
    raise RiskViolation()
```

---

## Decision

Implement a **centralized risk management library** (`libs/risk_management/`) with:

1. **Risk Limit Configuration** - Pydantic models for all limits
2. **Circuit Breaker State Machine** - Redis-backed with OPEN/TRIPPED states
3. **Pre-Trade Risk Checks** - Validate before order submission
4. **Post-Trade Monitoring** - Continuous drawdown tracking
5. **CLI Tools** - Manual circuit breaker controls

This will be **library-based, not service-based** because:
- Risk checks must be synchronous (no HTTP latency)
- Multiple services need risk checks (Execution Gateway, Orchestrator)
- State stored in Redis (already centralized)
- Simple integration via import

---

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Risk Management System                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────┐ │
│  │ Risk Limits       │   │ Circuit Breaker  │   │ Monitoring │ │
│  │ (config.py)       │   │ (breaker.py)     │   │ (monitor.py)│ │
│  │                   │   │                  │   │            │ │
│  │ • Position limits │   │ • State: OPEN/   │   │ • Drawdown │ │
│  │ • Notional limits │   │   TRIPPED        │   │ • Exposure │ │
│  │ • Loss limits     │   │ • Trip reasons   │   │ • Volatility│ │
│  │ • Blacklist       │   │ • Manual override│   │ • Alerts   │ │
│  └───────────────────┘   └──────────────────┘   └────────────┘ │
│           │                       │                      │       │
│           └───────────────────────┴──────────────────────┘       │
│                                   │                              │
│                      ┌────────────▼──────────────┐               │
│                      │   Risk Checker            │               │
│                      │   (checker.py)            │               │
│                      │                           │               │
│                      │ • Pre-trade validation   │               │
│                      │ • Position size checks   │               │
│                      │ • Notional checks        │               │
│                      │ • Circuit breaker check  │               │
│                      └────────────┬──────────────┘               │
│                                   │                              │
└───────────────────────────────────┼──────────────────────────────┘
                                    │
                   ┌────────────────┴────────────────┐
                   │                                 │
          ┌────────▼─────────┐           ┌──────────▼──────────┐
          │ Execution Gateway│           │  Orchestrator       │
          │ (pre-trade)      │           │  (signal gating)    │
          └──────────────────┘           └─────────────────────┘
```

### Data Flow

**Pre-Trade Flow:**
```
Signal → Orchestrator → Risk Check → Execution Gateway → Risk Check → Alpaca
                            ↓                                ↓
                     [PASS/BLOCK]                      [PASS/BLOCK]
                                                              ↓
                                                    Update Positions DB
```

**Post-Trade Monitoring Flow:**
```
Position Update → Monitor Drawdown → Check Thresholds → Trip Circuit Breaker?
                                                                  ↓
                                                          Update Redis State
                                                                  ↓
                                                          Publish Alert Event
```

---

## Risk Limits Design

### Configuration Model

```python
# libs/risk_management/config.py
from pydantic import BaseModel, Field
from decimal import Decimal

class PositionLimits(BaseModel):
    """Per-symbol position limits."""
    max_position_size: int = Field(
        default=1000,
        description="Maximum shares per symbol (absolute value)"
    )
    max_position_pct: Decimal = Field(
        default=Decimal("0.20"),
        description="Maximum position as % of portfolio (0.20 = 20%)"
    )

class PortfolioLimits(BaseModel):
    """Portfolio-level limits."""
    max_total_notional: Decimal = Field(
        default=Decimal("100000.00"),
        description="Maximum total notional exposure ($)"
    )
    max_long_exposure: Decimal = Field(
        default=Decimal("80000.00"),
        description="Maximum long exposure ($)"
    )
    max_short_exposure: Decimal = Field(
        default=Decimal("20000.00"),
        description="Maximum short exposure ($)"
    )

class LossLimits(BaseModel):
    """Loss limit configuration."""
    daily_loss_limit: Decimal = Field(
        default=Decimal("5000.00"),
        description="Maximum daily loss before circuit breaker trips ($)"
    )
    max_drawdown_pct: Decimal = Field(
        default=Decimal("0.10"),
        description="Maximum drawdown from peak equity (0.10 = 10%)"
    )

class RiskConfig(BaseModel):
    """Complete risk management configuration."""
    position_limits: PositionLimits = PositionLimits()
    portfolio_limits: PortfolioLimits = PortfolioLimits()
    loss_limits: LossLimits = LossLimits()
    blacklist: list[str] = Field(
        default_factory=list,
        description="Symbols forbidden from trading"
    )
```

**Configuration Source:** Environment variables with defaults

```python
# config/settings.py additions
RISK_MAX_POSITION_SIZE: int = 1000
RISK_DAILY_LOSS_LIMIT: Decimal = Decimal("5000.00")
RISK_MAX_DRAWDOWN_PCT: Decimal = Decimal("0.10")
RISK_BLACKLIST: list[str] = []
```

---

## Circuit Breaker Design

### State Machine

```
                     ┌──────────────────────┐
                     │       OPEN           │
                     │  (Trading Allowed)   │
                     └──────────┬───────────┘
                                │
                     Violation Detected
                     (drawdown/errors/stale)
                                │
                                ▼
                     ┌──────────────────────┐
                     │      TRIPPED         │
                     │  (Trading Blocked)   │
                     └──────────┬───────────┘
                                │
                    Manual Reset Required
                    (after conditions clear)
                                │
                                ▼
                     ┌──────────────────────┐
                     │    QUIET_PERIOD      │
                     │  (Monitoring only)   │
                     └──────────┬───────────┘
                                │
                     After 5 minutes
                                │
                                ▼
                     ┌──────────────────────┐
                     │       OPEN           │
                     └──────────────────────┘
```

### Redis Storage

**Key:** `circuit_breaker:state`
**Value:** JSON object

```json
{
  "state": "OPEN",  // OPEN | TRIPPED | QUIET_PERIOD
  "tripped_at": null,
  "trip_reason": null,
  "reset_at": null,
  "reset_by": null,
  "trip_count_today": 0
}
```

**Additional Keys:**
- `circuit_breaker:trip_history` - List of all trips (append-only log)
- `circuit_breaker:config` - Circuit breaker configuration (thresholds)

### Trip Conditions

```python
# Automatic trip triggers
TRIP_REASONS = {
    "DAILY_LOSS_EXCEEDED": "Daily loss limit breached",
    "MAX_DRAWDOWN": "Maximum drawdown exceeded",
    "DATA_STALE": "Market data >30 minutes old",
    "BROKER_ERRORS": "3+ consecutive Alpaca API failures",
    "MANUAL": "Manually tripped via CLI"
}
```

### When TRIPPED Behavior

**Blocked Actions:**
- ❌ New position entries (any order increasing abs(position))
- ❌ Signal generation (Orchestrator blocks all signals)
- ❌ Automated trading via `paper_run.py`

**Allowed Actions:**
- ✅ Position-reducing orders (closing positions)
- ✅ Query endpoints (GET /positions, /pnl, /health)
- ✅ Manual circuit breaker reset (after verification)

---

## Pre-Trade Risk Checks

### Validation Logic

```python
# libs/risk_management/checker.py
from libs.risk_management.config import RiskConfig
from libs.risk_management.breaker import CircuitBreaker

class RiskChecker:
    """Pre-trade risk validation."""

    def __init__(self, config: RiskConfig, breaker: CircuitBreaker):
        self.config = config
        self.breaker = breaker

    def validate_order(
        self,
        symbol: str,
        side: str,  # "buy" | "sell"
        qty: int,
        current_position: int = 0,
        current_portfolio_value: Decimal = Decimal("0.00")
    ) -> tuple[bool, str]:
        """
        Validate order against all risk limits.

        Returns:
            (is_valid, reason)
            - (True, "") if order passes all checks
            - (False, "reason") if blocked

        Example:
            >>> checker = RiskChecker(config, breaker)
            >>> is_valid, reason = checker.validate_order("AAPL", "buy", 100, 0, Decimal("50000"))
            >>> if not is_valid:
            ...     raise RiskViolation(reason)
        """
        # 1. Circuit breaker check (highest priority)
        if self.breaker.is_tripped():
            return (False, f"Circuit breaker TRIPPED: {self.breaker.get_trip_reason()}")

        # 2. Blacklist check
        if symbol in self.config.blacklist:
            return (False, f"Symbol {symbol} is blacklisted")

        # 3. Position size check
        new_position = current_position + (qty if side == "buy" else -qty)
        if abs(new_position) > self.config.position_limits.max_position_size:
            return (
                False,
                f"Position limit exceeded: {abs(new_position)} > "
                f"{self.config.position_limits.max_position_size}"
            )

        # 4. Portfolio concentration check
        # (implementation detail: requires price lookup)

        # 5. Daily loss limit check
        # (implementation detail: query today's P&L from DB)

        return (True, "")
```

### Integration Points

**Execution Gateway (`apps/execution_gateway/main.py`):**
```python
@app.post("/api/v1/orders")
async def submit_order(order: OrderRequest):
    """Submit order with pre-trade risk checks."""

    # Get current position
    position = db.get_position(order.symbol)

    # Risk check
    is_valid, reason = risk_checker.validate_order(
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        current_position=position.qty if position else 0
    )

    if not is_valid:
        raise HTTPException(status_code=403, detail=f"Risk violation: {reason}")

    # Proceed with order submission
    ...
```

**Orchestrator (`apps/orchestrator/orchestrator.py`):**
```python
async def execute_signals(signals: dict[str, float]):
    """Execute signals with circuit breaker check."""

    # Early check: circuit breaker
    if circuit_breaker.is_tripped():
        logger.warning(f"Circuit breaker TRIPPED: {circuit_breaker.get_trip_reason()}")
        return {"status": "blocked", "reason": circuit_breaker.get_trip_reason()}

    # Proceed with signal execution
    ...
```

---

## Post-Trade Monitoring

### Continuous Monitoring Process

**Background Task (runs every 60 seconds):**
```python
# libs/risk_management/monitor.py
import asyncio

class RiskMonitor:
    """Background task for continuous risk monitoring."""

    async def start(self):
        """Start monitoring loop."""
        while True:
            try:
                await self.check_risk_conditions()
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Risk monitor error: {e}")
                await asyncio.sleep(60)

    async def check_risk_conditions(self):
        """Check all risk conditions and trip breaker if needed."""

        # 1. Check daily loss limit
        daily_pnl = await self.get_daily_pnl()
        if daily_pnl < -self.config.loss_limits.daily_loss_limit:
            await self.breaker.trip("DAILY_LOSS_EXCEEDED")
            return

        # 2. Check max drawdown
        drawdown_pct = await self.calculate_drawdown()
        if drawdown_pct > self.config.loss_limits.max_drawdown_pct:
            await self.breaker.trip("MAX_DRAWDOWN")
            return

        # 3. Check data staleness
        latest_price_timestamp = await self.get_latest_price_timestamp()
        if (datetime.now(UTC) - latest_price_timestamp).seconds > 1800:  # 30 minutes
            await self.breaker.trip("DATA_STALE")
            return
```

**Integration:** Run as background task in Execution Gateway startup

```python
# apps/execution_gateway/main.py
@app.on_event("startup")
async def startup():
    # Start risk monitor
    monitor = RiskMonitor(config=risk_config, breaker=circuit_breaker)
    asyncio.create_task(monitor.start())
```

---

## CLI Tools

### Circuit Breaker Management

**Trip Circuit Breaker:**
```bash
# scripts/circuit_breaker.py trip
python scripts/circuit_breaker.py trip --reason "MANUAL" --note "End of trading day"
```

**Reset Circuit Breaker:**
```bash
# scripts/circuit_breaker.py reset
python scripts/circuit_breaker.py reset
```

**Status Check:**
```bash
# scripts/circuit_breaker.py status
python scripts/circuit_breaker.py status

# Output:
# Circuit Breaker Status: TRIPPED
# Tripped At: 2025-10-19 15:30:00 UTC
# Reason: DAILY_LOSS_EXCEEDED
# Daily Loss: -$5,234.56 (limit: -$5,000.00)
```

**Makefile Integration:**
```makefile
# Makefile additions
circuit-trip:
	python scripts/circuit_breaker.py trip --reason MANUAL

circuit-reset:
	python scripts/circuit_breaker.py reset

circuit-status:
	python scripts/circuit_breaker.py status
```

---

## Testing Strategy

### Unit Tests

**`tests/risk_management/test_checker.py`** (~300 lines)
- Position limit validation
- Blacklist enforcement
- Circuit breaker blocking
- Edge cases (zero position, short positions)

**`tests/risk_management/test_breaker.py`** (~200 lines)
- State transitions (OPEN → TRIPPED → QUIET_PERIOD → OPEN)
- Manual trip/reset
- Trip reason persistence
- Concurrent access (Redis locking)

**`tests/risk_management/test_monitor.py`** (~250 lines)
- Daily loss detection
- Drawdown calculation
- Data staleness detection
- Automatic circuit breaker triggering

### Integration Tests

**`tests/integration/test_risk_integration.py`** (~400 lines)
- End-to-end: order submission blocked when circuit tripped
- End-to-end: position limit enforcement
- End-to-end: daily loss triggers circuit breaker
- End-to-end: manual reset workflow

### Acceptance Tests

**Critical Scenarios:**
1. ✅ Order rejected when position limit exceeded
2. ✅ Circuit breaker trips when daily loss limit breached
3. ✅ All trading blocked when circuit breaker TRIPPED
4. ✅ Position-reducing orders allowed when TRIPPED
5. ✅ Manual reset requires conditions cleared + quiet period
6. ✅ Orchestrator skips signal execution when TRIPPED

---

## Database Changes

### New Tables

```sql
-- docs/DB/risk_management_schema.sql

-- Circuit breaker trip history (append-only audit log)
CREATE TABLE IF NOT EXISTS circuit_breaker_trips (
    id SERIAL PRIMARY KEY,
    tripped_at TIMESTAMPTZ NOT NULL,
    trip_reason TEXT NOT NULL,
    daily_loss NUMERIC,
    max_drawdown_pct NUMERIC,
    reset_at TIMESTAMPTZ,
    reset_by TEXT,
    notes TEXT
);

-- Risk limit violations (logged but not blocking)
CREATE TABLE IF NOT EXISTS risk_violations (
    id SERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    violation_type TEXT NOT NULL,  -- 'position_limit', 'loss_limit', etc.
    symbol TEXT,
    attempted_qty INT,
    current_position INT,
    limit_value NUMERIC,
    blocked BOOLEAN NOT NULL DEFAULT TRUE
);

-- Daily risk metrics snapshot (once per day)
CREATE TABLE IF NOT EXISTS daily_risk_metrics (
    date DATE PRIMARY KEY,
    peak_equity NUMERIC NOT NULL,
    end_equity NUMERIC NOT NULL,
    max_drawdown_pct NUMERIC NOT NULL,
    max_position_size INT NOT NULL,
    max_notional_exposure NUMERIC NOT NULL,
    circuit_breaker_trips INT NOT NULL DEFAULT 0
);
```

---

## Alternatives Considered

### Alternative 1: Risk Management Service (Microservice)

**Pros:**
- Centralized risk state
- Independent deployment
- Easier to scale

**Cons:**
- **Added latency** (HTTP call for every order)
- Single point of failure
- Over-engineering for current scale

**Rejected:** Library approach is simpler and faster

### Alternative 2: Database-Based Circuit Breaker

**Pros:**
- Persistent state
- SQL queries for history

**Cons:**
- **Slower than Redis** (critical path)
- No pub/sub for real-time updates

**Rejected:** Redis is faster for real-time checks

### Alternative 3: Hard-Coded Limits

**Pros:**
- Simplest implementation

**Cons:**
- No flexibility
- Requires code changes to adjust limits

**Rejected:** Pydantic config allows environment-based tuning

---

## Consequences

### Positive

1. **Capital Protection** - Systematic limits prevent catastrophic losses
2. **Regulatory Compliance** - Pattern day trading rules, margin requirements
3. **Operational Safety** - Circuit breaker prevents runaway trading
4. **Auditability** - All violations logged to database
5. **Flexibility** - Environment-based configuration tuning
6. **Performance** - Redis-backed checks add <5ms latency

### Negative

1. **Complexity** - New library to maintain
2. **False Positives** - May block legitimate orders near limits
3. **Manual Intervention** - Circuit breaker reset requires human approval
4. **Testing Burden** - Complex state machine requires extensive tests

### Mitigations

- **False Positives:** Conservative limits (e.g., 80% of hard limit triggers warning)
- **Manual Intervention:** Clear runbook in `docs/RUNBOOKS/circuit-breaker-recovery.md`
- **Testing:** TDD approach with 90%+ coverage requirement

---

## Implementation Plan

### Phase 1: Core Library (Days 1-2)
- ✅ Risk limit models (`config.py`)
- ✅ Circuit breaker state machine (`breaker.py`)
- ✅ Risk checker (`checker.py`)
- ✅ Unit tests (90%+ coverage)

### Phase 2: Integration (Days 3-4)
- ✅ Execution Gateway integration
- ✅ Orchestrator integration
- ✅ Database migrations
- ✅ Integration tests

### Phase 3: Monitoring & CLI (Days 5-6)
- ✅ Post-trade monitor (`monitor.py`)
- ✅ CLI tools (`scripts/circuit_breaker.py`)
- ✅ Background task integration
- ✅ End-to-end tests

### Phase 4: Documentation (Day 7)
- ✅ Concepts guide (`docs/CONCEPTS/risk-management.md`)
- ✅ Implementation guide (`docs/IMPLEMENTATION_GUIDES/p1.2t3-risk-management.md`)
- ✅ Runbook (`docs/RUNBOOKS/circuit-breaker-recovery.md`)
- ✅ Lessons learned

---

## References

- **CLAUDE.md** - Risk management patterns (Circuit Breakers section)
- **docs/GETTING_STARTED/GLOSSARY.md** - Circuit breaker definition
- **docs/STANDARDS/DOCUMENTATION_STANDARDS.md** - Docstring requirements
- **docs/STANDARDS/TESTING.md** - Test pyramid and coverage requirements

---

## Decision Status

**Status:** Proposed
**Review Required:** Yes (architectural change per CLAUDE.md)
**Approval:** Pending

---

## Changelog

- 2025-10-19: Initial ADR created for P1.2T3
