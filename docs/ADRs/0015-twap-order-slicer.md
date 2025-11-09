# ADR-0015: TWAP Order Slicer with APScheduler

**Status:** Accepted
**Date:** 2025-10-27
**Deciders:** Lee (implemented P2T0 TWAP Order Slicer)
**Tags:** execution, TWAP, scheduling, API

## Context

Large order execution can cause significant market impact. TWAP (Time-Weighted Average Price) is a standard execution algorithm that splits large parent orders into smaller child slices distributed evenly over time to minimize market impact.

The execution gateway needs the ability to:
1. Accept TWAP order requests via REST API
2. Automatically slice large orders into smaller child orders
3. Schedule child order execution at regular intervals
4. Provide query and cancellation APIs for slice management
5. Enforce trading safety guardrails (kill switch, circuit breaker) on every slice

## Decision

Implement TWAP order slicing with the following architecture:

### Components

1. **TWAPSlicer** (`order_slicer.py`):
   - Stateless slicer class for creating slicing plans
   - Deterministic order ID generation via hash-based approach
   - Front-loaded remainder distribution
   - Validation for qty/duration relationship

2. **SliceScheduler** (`slice_scheduler.py`):
   - APScheduler BackgroundScheduler for time-based execution
   - Mandatory safety guards: kill switch + circuit breaker checks before EVERY slice
   - Tenacity retry (3 attempts, exponential backoff) for transient failures
   - Defense-in-depth cancellation guards (DB-first + dual checks)

3. **Database Layer** (extends `database.py`):
   - `create_parent_order()` - Creates parent with total_slices metadata
   - `create_child_slice()` - Creates child with parent_order_id, slice_num, scheduled_time
   - `get_slices_by_parent_id()` - Retrieves all slices for a parent
   - `cancel_pending_slices()` - Cancels all pending_new slices for a parent

4. **REST API Endpoints** (extends `main.py`):
   - POST /api/v1/orders/slice - Submit TWAP order (creates parent + children, schedules execution)
   - GET /api/v1/orders/{parent_id}/slices - Query all slices for a parent
   - DELETE /api/v1/orders/{parent_id}/slices - Cancel pending slices

### Key Design Choices

**APScheduler over Celery:**
- Pros: Simpler dependency, in-process (no separate worker), sufficient for TWAP use case
- Cons: No distributed task queue, limited to single process
- Rationale: TWAP slicing is time-critical and benefits from in-process execution. Celery's distributed features unnecessary for this use case.

**Hash-based Deterministic IDs:**
- Parent/child order IDs generated via SHA256 hash of order parameters + trade date
- Ensures idempotency across retries and prevents duplicate orders
- Consistent with existing order ID generation pattern

**Defense-in-Depth Cancellation:**
- DB update BEFORE scheduler job removal (prevents race)
- Early guard at start of _execute_slice() (first line of defense)
- Pre-submit guard before broker submission (second line of defense)
- Database as source of truth for cancellation status

**Retry Policy:**
- Only retry AlpacaConnectionError (transient network failures)
- Non-retryable: AlpacaValidationError, AlpacaRejectionError (terminal failures)
- Max 3 attempts with exponential backoff (2s, 4s, 8s)

## Consequences

### Positive

- **Market Impact Reduction:** Large orders distributed over time reduce slippage
- **Safety First:** Mandatory kill switch + circuit breaker checks on every slice
- **Idempotency:** Deterministic IDs prevent duplicate orders on retry
- **Observability:** Structured logging with parent_order_id, slice_num for tracing
- **Flexibility:** Supports market, limit, stop, stop_limit order types
- **Testability:** 100% test coverage on core components (TWAPSlicer, SliceScheduler)

### Negative

- **Single-Process Limitation:** APScheduler runs in-process, cannot distribute across workers
- **No Persistence:** Scheduled jobs lost on service restart (mitigated by boot-time rescheduling in future iteration)
- **Limited Scalability:** Cannot handle thousands of concurrent TWAP orders (acceptable for current phase)

### Risks

- **Service Restart:** Scheduled slices lost if service restarts mid-execution
  - Mitigation: Future iteration will add boot-time rescheduling from DB
- **Race Conditions:** Potential race between cancellation and execution
  - Mitigation: Defense-in-depth with dual DB guards + DB-first cancellation
- **Clock Skew:** Scheduled times depend on system clock accuracy
  - Mitigation: Use UTC timezone, rely on NTP for time sync

### Migration Path

**Phase 1 (Current):** In-process APScheduler with manual recovery
**Phase 2 (Future):** Add boot-time rescheduling from database
**Phase 3 (Future):** Evaluate Celery migration if distributed execution needed

## Implementation

**Commits:**
- c673ab5: SliceScheduler with APScheduler
- c47777f: POST /api/v1/orders/slice endpoint
- eb4fcf6: GET/DELETE slice management endpoints

**Test Coverage:**
- TWAPSlicer: 28 tests (100% coverage)
- SliceScheduler: 17 tests (100% coverage)
- Database methods: 14 tests (integrated)
- API endpoints: 3 minimal tests (endpoint registration)

**Documentation:**
- Code: Comprehensive docstrings following DOCUMENTATION_STANDARDS.md
- Examples: Embedded in docstrings (doctest format)
- See inline code documentation for implementation details

## References

- [ADR-0014: Execution Gateway Architecture](./0014-execution-gateway-architecture.md)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
- [Python PEP 8 - Exception Names](https://peps.python.org/pep-0008/#exception-names)
