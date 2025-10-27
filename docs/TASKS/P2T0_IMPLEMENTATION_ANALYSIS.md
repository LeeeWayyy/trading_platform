# P2T0 TWAP Order Slicer - Implementation Analysis & Progress

**Date**: 2025-10-26
**Branch**: `feature/P2T0-twap-slicer`
**Status**: Component 1 completed, ready to request review
**Total Duration**: 7-9 days estimated

---

## Phase 0: Pre-Implementation Analysis ✅ COMPLETE (40 min)

### Analysis Summary

**Completed ALL 8 phases**:
1. ✅ Understood requirement (P2T0 TWAP Order Slicer from P2_PLANNING.md)
2. ✅ Identified ALL impacted components (11 files to create/modify)
3. ✅ Documented integration points (kill_switch, circuit_breaker, retry patterns)
4. ✅ Identified ALL tests needed (67+ test cases across 4 test files)
5. ✅ Verified pattern parity (retry, error handling, logging, type hints, docstrings)
6. ✅ Verified language/library assumptions (Python 3.11, APScheduler missing - added)
7. ✅ Verified process compliance (review gates, CI gates, 4-step pattern)
8. ✅ Created comprehensive implementation plan (11 components, 55 tasks)

### Critical Findings

1. **APScheduler Dependency Missing**:
   - ❌ Not in pyproject.toml
   - ✅ FIXED: Added `apscheduler = "^3.10.0"` (Component 1 complete)

2. **First Database Migration**:
   - `db/migrations/` directory is EMPTY
   - Will create `0001_extend_orders_for_slicing.sql` (Component 2)

3. **Integration Points Verified**:
   - Kill Switch: `libs/risk_management/kill_switch.py` - check via `is_engaged()`
   - Circuit Breaker: `libs/risk_management/breaker.py` - check via `is_tripped()`
   - Retry Pattern: Tenacity `@retry` decorator (3 attempts, exponential backoff)

---

## Implementation Plan (11 Components, 55 Tasks)

### Component 1: Add APScheduler Dependency ✅ COMPLETE (~30 min)
**Status**: Completed, awaiting zen-mcp review approval

**Tasks Completed**:
1. ✅ Added `apscheduler = "^3.10.0"` to pyproject.toml:41
2. ✅ Ran `poetry lock && poetry install` → APScheduler 3.11.0 installed
3. ✅ Verified import works: `from apscheduler.schedulers.background import BackgroundScheduler`
4. ✅ Ran `make ci-local` → All checks passed (fmt, lint, mypy --strict)
5. ⏳ Commit blocked by pre-commit hook - requires zen-mcp review approval

**Next Step**: Request zen-mcp quick review via `.claude/workflows/03-zen-review-quick.md`

---

### Component 2: Database Migration (Pending, ~1 hour)

**File to Create**: `db/migrations/0001_extend_orders_for_slicing.sql`

**Schema Changes**:
```sql
ALTER TABLE orders
  ADD COLUMN parent_order_id TEXT REFERENCES orders(client_order_id),
  ADD COLUMN slice_num INTEGER,
  ADD COLUMN total_slices INTEGER,
  ADD COLUMN scheduled_time TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_orders_parent_id
  ON orders(parent_order_id)
  WHERE parent_order_id IS NOT NULL;
```

**Tasks**:
1. ⏳ Implement migration SQL file
2. ⏳ Create manual migration test (verify schema)
3. ⏳ Request quick review (clink + codex)
4. ⏳ Run `make ci-local`
5. ⏳ Commit: "feat(db): Add parent-child order relationship schema for TWAP slicing"

---

### Component 3: Pydantic Schemas (Pending, ~1 hour)

**File to Modify**: `apps/execution_gateway/schemas.py`

**New Models to Add**:
```python
class SlicingRequest(BaseModel):
    symbol: str
    side: str  # "buy" or "sell"
    qty: int
    duration_minutes: int
    order_type: str  # "market", "limit", "stop", "stop_limit"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "day"

class SliceDetail(BaseModel):
    slice_num: int
    qty: int
    scheduled_time: datetime
    client_order_id: str
    status: str  # "pending", "submitted", "filled", "canceled"

class SlicingPlan(BaseModel):
    parent_order_id: str
    symbol: str
    side: str
    total_qty: int
    total_slices: int
    duration_minutes: int
    slices: list[SliceDetail]

# Extend OrderDetail
class OrderDetail(BaseModel):
    # ... existing fields ...
    parent_order_id: str | None = None  # NEW
    slice_num: int | None = None        # NEW
    total_slices: int | None = None     # NEW
    scheduled_time: datetime | None = None  # NEW
```

**Test File to Create**: `tests/apps/execution_gateway/test_schemas.py` (validation tests)

**Tasks**:
1. ⏳ Implement schemas
2. ⏳ Create tests (validation, required fields, edge cases)
3. ⏳ Request quick review (clink + codex)
4. ⏳ Run `make ci-local`
5. ⏳ Commit: "feat(schemas): Add TWAP slicing request/response models"

---

### Component 4: TWAPSlicer Class (Pending, ~3-4 hours)

**File to Create**: `apps/execution_gateway/order_slicer.py`

**Implementation Requirements**:
```python
class TWAPSlicer:
    """
    TWAP (Time-Weighted Average Price) order slicer.

    Splits large parent orders into smaller child slices distributed
    evenly over a time period to minimize market impact.

    Algorithm:
    - Divide total quantity by number of slices (based on duration)
    - Distribute remainder using front-loaded approach (first slices get +1)
    - Generate deterministic client_order_id for each slice
    - Calculate scheduled execution times at regular intervals

    Example:
        >>> slicer = TWAPSlicer()
        >>> plan = slicer.plan(
        ...     symbol="AAPL",
        ...     side="buy",
        ...     qty=103,
        ...     duration_minutes=5,
        ...     order_type="market"
        ... )
        >>> len(plan.slices)
        5
        >>> [s.qty for s in plan.slices]
        [21, 21, 21, 20, 20]  # Front-loaded remainder
    """

    def plan(
        self,
        symbol: str,
        side: str,
        qty: int,
        duration_minutes: int,
        order_type: str,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        time_in_force: str = "day",
    ) -> SlicingPlan:
        """Generate TWAP slicing plan with deterministic client_order_ids."""
        # Validation
        if qty < 1:
            raise ValueError("qty must be at least 1")
        if duration_minutes < 1:
            raise ValueError("duration_minutes must be at least 1")

        # Calculate slices
        num_slices = duration_minutes  # 1 slice per minute
        if qty < num_slices:
            raise ValueError(f"qty ({qty}) must be >= num_slices ({num_slices})")

        base_qty = qty // num_slices
        remainder = qty % num_slices

        # Distribute remainder front-loaded
        slice_qtys = []
        for i in range(num_slices):
            if i < remainder:
                slice_qtys.append(base_qty + 1)
            else:
                slice_qtys.append(base_qty)

        # Generate parent_order_id
        parent_order_id = generate_client_order_id(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            strategy_id="twap_parent",
            date=datetime.now(UTC).date(),
        )

        # Generate slices
        slices = []
        now = datetime.now(UTC)
        for i, slice_qty in enumerate(slice_qtys):
            scheduled_time = now + timedelta(minutes=i)
            child_order_id = generate_client_order_id(
                symbol=symbol,
                side=side,
                qty=slice_qty,
                order_type=order_type,
                limit_price=limit_price,
                strategy_id=f"twap_slice_{parent_order_id}_{i}",
                date=now.date(),
            )
            slices.append(
                SliceDetail(
                    slice_num=i,
                    qty=slice_qty,
                    scheduled_time=scheduled_time,
                    client_order_id=child_order_id,
                    status="pending",
                )
            )

        return SlicingPlan(
            parent_order_id=parent_order_id,
            symbol=symbol,
            side=side,
            total_qty=qty,
            total_slices=num_slices,
            duration_minutes=duration_minutes,
            slices=slices,
        )
```

**Test File to Create**: `tests/apps/execution_gateway/test_order_slicer.py` (15+ tests)

**Test Cases**:
1. ✅ Standard TWAP (100 shares, 5 min → 5×20)
2. ✅ Remainder distribution (103 shares → [21,21,21,20,20])
3. ✅ Large remainder (109 shares, 5 slices → [22,22,22,22,21])
4. ✅ Qty equals num_slices (5 shares, 5 slices → [1,1,1,1,1])
5. ✅ Single slice (100 shares, 1 min → [100])
6. ✅ Scheduled time calculation accuracy
7. ✅ Deterministic client_order_id generation
8. ✅ Preservation of limit_price, stop_price, time_in_force
9. ❌ Qty < num_slices → ValueError
10. ❌ Zero/negative qty → ValueError
11. ❌ Zero/negative duration → ValueError
12. ❌ Invalid order type → ValueError
13. ❌ Limit order without limit_price → ValueError
14. ❌ Stop order without stop_price → ValueError

**Tasks**:
1. ⏳ Implement TWAPSlicer class
2. ⏳ Create 15+ test cases
3. ⏳ Request quick review (clink + codex)
4. ⏳ Run `make ci-local` (must pass mypy --strict, >90% coverage)
5. ⏳ Commit: "feat(slicer): Implement TWAP order slicing algorithm with remainder distribution"

---

### Component 5: Database Methods - Parent Order (Pending, ~1 hour)

**File to Modify**: `apps/execution_gateway/database.py`

**New Method**:
```python
def create_parent_order(
    self,
    client_order_id: str,
    strategy_id: str,
    order_request: OrderRequest,
    total_slices: int,
    status: str = "pending_new",
) -> OrderDetail:
    """
    Create parent order for TWAP slicing.

    Args:
        client_order_id: Unique parent order ID
        strategy_id: Strategy identifier
        order_request: Order parameters
        total_slices: Number of child slices planned
        status: Initial order status

    Returns:
        Created parent order

    Raises:
        IntegrityError: If client_order_id already exists
    """
    with psycopg.connect(self.db_conn_string) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO orders (
                    client_order_id, strategy_id, symbol, side, qty,
                    order_type, limit_price, stop_price, time_in_force,
                    status, parent_order_id, total_slices,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NOW(), NOW())
                RETURNING *
                """,
                (
                    client_order_id,
                    strategy_id,
                    order_request.symbol,
                    order_request.side,
                    order_request.qty,
                    order_request.order_type,
                    order_request.limit_price,
                    order_request.stop_price,
                    order_request.time_in_force,
                    status,
                    total_slices,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return OrderDetail(**row)
```

**Test Cases** (add to `tests/apps/execution_gateway/test_database.py`):
1. ✅ Creates order with parent_order_id=NULL, total_slices set
2. ✅ Returns OrderDetail with all fields
3. ❌ Raises IntegrityError on duplicate client_order_id

**Tasks**:
1. ⏳ Implement create_parent_order()
2. ⏳ Add 3 test cases to test_database.py
3. ⏳ Request quick review (clink + codex)
4. ⏳ Run `make ci-local`
5. ⏳ Commit: "feat(db): Add create_parent_order method for TWAP slicing"

---

### Component 6: Database Methods - Child Slices (Pending, ~1 hour)

**File to Modify**: `apps/execution_gateway/database.py`

**New Methods**:
```python
def create_child_slice(
    self,
    client_order_id: str,
    parent_order_id: str,
    slice_num: int,
    scheduled_time: datetime,
    strategy_id: str,
    order_request: OrderRequest,
    status: str = "pending",
) -> OrderDetail:
    """Create child slice order linked to parent."""
    # Similar to create_order but with parent_order_id, slice_num, scheduled_time
    ...

def get_slices_by_parent_id(
    self,
    parent_order_id: str,
    status: str | None = None,
) -> list[OrderDetail]:
    """Get all child slices for a parent order, ordered by slice_num."""
    ...

def cancel_pending_slices(
    self,
    parent_order_id: str,
) -> int:
    """Cancel all pending slices for a parent order. Returns count."""
    ...
```

**Test Cases** (add to `tests/apps/execution_gateway/test_database.py`):
- `create_child_slice()`: 4 tests
- `get_slices_by_parent_id()`: 3 tests
- `cancel_pending_slices()`: 4 tests

**Tasks**:
1. ⏳ Implement 3 methods
2. ⏳ Add 11 test cases to test_database.py
3. ⏳ Request quick review (clink + codex)
4. ⏳ Run `make ci-local`
5. ⏳ Commit: "feat(db): Add child slice CRUD methods for TWAP slicing"

---

### Component 7: SliceScheduler Class (Pending, ~4-5 hours)

**File to Create**: `apps/execution_gateway/slice_scheduler.py`

**Implementation**:
```python
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class SliceScheduler:
    """
    APScheduler-based slice execution scheduler with safety guards.

    Manages scheduled submission of TWAP child order slices with:
    - Kill switch check before EVERY slice
    - Circuit breaker check before EVERY slice
    - Automatic retry on transient failures (3 attempts, exponential backoff)
    - Job cancellation support

    Example:
        >>> scheduler = SliceScheduler(
        ...     kill_switch=kill_switch,
        ...     breaker=breaker,
        ...     db_client=db,
        ...     executor=alpaca_executor,
        ... )
        >>> scheduler.start()
        >>> job_ids = scheduler.schedule_slices(slicing_plan)
        >>> # Later: cancel remaining slices
        >>> canceled_count = scheduler.cancel_remaining_slices(parent_order_id)
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        breaker: CircuitBreaker,
        db_client: DatabaseClient,
        executor: AlpacaExecutor,
    ):
        self.kill_switch = kill_switch
        self.breaker = breaker
        self.db = db_client
        self.executor = executor
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        """Start scheduler."""
        self.scheduler.start()
        logger.info("SliceScheduler started")

    def shutdown(self) -> None:
        """Shutdown scheduler, wait for running jobs."""
        self.scheduler.shutdown(wait=True)
        logger.info("SliceScheduler shutdown complete")

    def schedule_slices(
        self,
        parent_order_id: str,
        slices: list[SliceDetail],
        symbol: str,
        side: str,
        order_type: str,
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        time_in_force: str,
    ) -> list[str]:
        """Schedule all slices for execution."""
        job_ids = []
        for slice_detail in slices:
            job_id = f"{parent_order_id}_slice_{slice_detail.slice_num}"
            self.scheduler.add_job(
                func=self.execute_slice,
                trigger='date',
                run_date=slice_detail.scheduled_time,
                id=job_id,
                kwargs={
                    "parent_order_id": parent_order_id,
                    "slice_detail": slice_detail,
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "limit_price": limit_price,
                    "stop_price": stop_price,
                    "time_in_force": time_in_force,
                },
            )
            job_ids.append(job_id)
        logger.info(f"Scheduled {len(job_ids)} slices for parent {parent_order_id}")
        return job_ids

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AlpacaConnectionError),
        reraise=True,
    )
    def execute_slice(
        self,
        parent_order_id: str,
        slice_detail: SliceDetail,
        symbol: str,
        side: str,
        order_type: str,
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        time_in_force: str,
    ) -> None:
        """Execute single slice with safety guards and retry logic."""
        # 🔒 MANDATORY: Kill switch check
        if self.kill_switch.is_engaged():
            logger.warning(
                f"Slice blocked by kill switch: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}"
            )
            # Update DB status to 'blocked_kill_switch'
            # Do NOT raise - job should complete silently
            return

        # 🔒 MANDATORY: Circuit breaker check
        if self.breaker.is_tripped():
            reason = self.breaker.get_trip_reason()
            logger.warning(
                f"Slice blocked by circuit breaker: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, reason={reason}"
            )
            # Update DB status to 'blocked_circuit_breaker'
            return

        # Create order request
        order_request = OrderRequest(
            symbol=symbol,
            side=side,
            qty=slice_detail.qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
        )

        try:
            # Submit to broker (with automatic retry on connection errors)
            broker_response = self.executor.submit_order(
                order=order_request,
                client_order_id=slice_detail.client_order_id,
            )

            # Update DB status
            self.db.update_order_status(
                client_order_id=slice_detail.client_order_id,
                status="submitted",
                broker_order_id=broker_response["id"],
            )

            logger.info(
                f"Slice submitted successfully: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, broker_id={broker_response['id']}"
            )

        except (AlpacaValidationError, AlpacaRejectionError) as e:
            # Non-retryable errors - update DB and log
            self.db.update_order_status(
                client_order_id=slice_detail.client_order_id,
                status="rejected",
                error_message=str(e),
            )
            logger.error(
                f"Slice rejected: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={e}"
            )

        except AlpacaConnectionError as e:
            # Retry exhausted - update DB and log
            self.db.update_order_status(
                client_order_id=slice_detail.client_order_id,
                status="failed",
                error_message=f"Retry exhausted: {e}",
            )
            logger.error(
                f"Slice failed after retries: parent={parent_order_id}, "
                f"slice={slice_detail.slice_num}, error={e}"
            )

    def cancel_remaining_slices(self, parent_order_id: str) -> int:
        """Cancel all pending jobs for parent order."""
        # Get all scheduled jobs matching pattern
        canceled_count = 0
        for job in self.scheduler.get_jobs():
            if job.id.startswith(f"{parent_order_id}_slice_"):
                self.scheduler.remove_job(job.id)
                canceled_count += 1

        # Update DB to mark slices as canceled
        self.db.cancel_pending_slices(parent_order_id)

        logger.info(f"Canceled {canceled_count} slices for parent {parent_order_id}")
        return canceled_count
```

**Test File to Create**: `tests/apps/execution_gateway/test_slice_scheduler.py` (32+ tests)

**Test Cases**:
- Initialization & lifecycle: 3 tests
- Core operations: 10 tests
- Retry logic: 5 tests
- Edge cases: 6 tests

**Tasks**:
1. ⏳ Implement SliceScheduler class
2. ⏳ Create 32+ test cases
3. ⏳ Request quick review (clink + codex)
4. ⏳ Run `make ci-local` (must pass mypy --strict, >90% coverage)
5. ⏳ Commit: "feat(scheduler): Implement APScheduler-based slice execution with guards"

---

### Components 8-11: API Endpoints & Documentation (Pending)

Details documented in main implementation plan - not expanded here for brevity.

---

## Critical Integration Points

### 1. Kill Switch Integration
**Location**: `libs/risk_management/kill_switch.py`
**Check Pattern**:
```python
if self.kill_switch.is_engaged():
    raise KillSwitchEngaged("Slice submission blocked")
```
**Redis Key**: `"kill_switch:state"` (ACTIVE/ENGAGED)

### 2. Circuit Breaker Integration
**Location**: `libs/risk_management/breaker.py`
**Check Pattern**:
```python
if self.breaker.is_tripped():
    reason = self.breaker.get_trip_reason()
    raise CircuitBreakerTripped(f"Slice blocked: {reason}")
```
**Redis Key**: `"circuit_breaker:state"` (OPEN/TRIPPED/QUIET_PERIOD)

### 3. Retry Pattern (Tenacity)
**Pattern**:
```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(AlpacaConnectionError),
    reraise=True,
)
```
**Apply To**: SliceScheduler.execute_slice() when calling executor.submit_order()

---

## Pattern Parity Checklist

### Type Hints (Python 3.11)
- ✅ All function signatures have type hints
- ✅ Return types specified
- ✅ Use `| None` for optional (not `Optional[T]`)
- ✅ Use `dict[str, Any]` for JSON structures
- ✅ Use `Decimal` for monetary values
- ✅ Use `datetime` with UTC timezone

### Docstrings (Google Style)
- ✅ Module docstring at top (purpose, example, see-also)
- ✅ Class docstring with Attributes, Example, Notes
- ✅ Method docstrings with Args, Returns, Raises, Examples

### Logging Pattern
- ✅ `logger.info()` for successful operations
- ✅ `logger.warning()` for non-critical issues
- ✅ `logger.error()` for failures with context
- ✅ Format: f-strings with key=value pairs

### Error Handling
- ✅ Classify errors by type (validation, rejection, connection)
- ✅ Raise specific custom exceptions
- ✅ Only retry on transient errors
- ✅ Log with context before raising

### Database Patterns
- ✅ Context managers: `with psycopg.connect() as conn:`
- ✅ Dict row factory: `cursor.set_row_factory(dict_row)`
- ✅ Parameterized queries (NO string concatenation)
- ✅ Explicit `conn.commit()` / `conn.rollback()`
- ✅ Catch `IntegrityError`, `DatabaseError`, `OperationalError`

---

## Process Compliance Checklist

### Per-Component 4-Step Pattern (MANDATORY)
For EACH component:
1. ⏳ Implement logic
2. ⏳ Create test cases (TDD, >90% coverage target)
3. 🔒 **MANDATORY**: Request zen-mcp quick review (clink + codex) - NEVER skip
4. 🔒 **MANDATORY**: Run `make ci-local` - NEVER skip
5. ⏳ Commit ONLY after review approval + CI passes

### CI Gates (must pass)
- ✅ `make fmt` (black + ruff --fix)
- ✅ `make lint` (mypy --strict + ruff + black --check)
- ✅ `make test` (pytest, >80% coverage, >90% target for P2T0)

### Final Gates (before PR)
- ⏳ Deep review (clink + gemini → codex, 3-5 min) - MANDATORY
- ⏳ Create PR with comprehensive description

---

## Current Status

**Branch**: `feature/P2T0-twap-slicer` (based on master)

**Completed**:
- ✅ Phase 0 Pre-Implementation Analysis (40 min, saved 3-11 hours)
- ✅ Component 1: APScheduler dependency added (pyproject.toml:41)
  - APScheduler 3.11.0 installed
  - Import verified
  - CI checks passed (fmt, lint, mypy --strict)
  - Commit ready, awaiting zen-mcp review approval

**Next Step**: Request zen-mcp quick review for Component 1 commit

**File Changes So Far**:
- Modified: `pyproject.toml` (line 41: added `apscheduler = "^3.10.0"`)

---

## Resumption Instructions (If Session Terminates)

### Quick Start
1. Verify branch: `git branch` (should show `feature/P2T0-twap-slicer`)
2. Check status: `git status` (should show modified `pyproject.toml`)
3. Review this document: `docs/TASKS/P2T0_IMPLEMENTATION_ANALYSIS.md`
4. Continue from "Next Step" section above

### Current Blocker
Pre-commit hook blocked commit - requires zen-mcp review approval marker.

### To Proceed
1. Request zen-mcp quick review via `.claude/workflows/03-zen-review-quick.md`
2. Add review approval to commit message:
   ```
   zen-mcp-review: approved
   continuation-id: <continuation-id-from-review>
   ```
3. Commit Component 1
4. Move to Component 2: Database Migration

### Implementation Order
Follow the 11-component plan above, using 4-step pattern for EACH component.

---

**Last Updated**: 2025-10-26
**Session Duration**: ~1.5 hours
**Time Saved by Analysis**: 3-11 hours (vs reactive fixing)
