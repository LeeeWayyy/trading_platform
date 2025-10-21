# ADR-0007: Paper Run Automation Script

**Status:** Accepted
**Date:** 2025-01-17
**Deciders:** System Architect, DevOps Lead
**Tags:** automation, orchestration, cli, p&l, reporting

## Context

We have successfully implemented T1-T5, creating a complete trading infrastructure:
- T1: Data ETL with corporate actions and quality gates
- T2: Baseline ML strategy with MLflow
- T3: Signal Service with model registry and hot reload
- T4: Execution Gateway with idempotent order submission
- T5: Orchestrator Service coordinating T3→T4 workflow

However, users must manually:
1. Start all microservices (T3, T4, T5)
2. Call Orchestrator API with correct parameters
3. Parse results and calculate P&L
4. Format and display results
5. Schedule daily execution

**Problem:** No single command to run complete end-to-end paper trading workflow.

**Goal:** Create `paper_run.py` - a CLI script that executes the complete pipeline with one command, calculates P&L, and provides formatted reporting.

## Decision

We will implement `paper_run.py` as a **standalone CLI script** (not a microservice) that:

1. **Orchestrates the complete workflow** by calling the Orchestrator Service (T5) API
2. **Calculates P&L** from execution results
3. **Provides formatted reporting** to stdout/file
4. **Supports scheduling** via cron or similar
5. **Includes health checks** to verify all dependencies are running
6. **Handles errors gracefully** with clear user messages

### Architecture Choice: CLI Script vs Service

**Decision:** Standalone Python CLI script in `/scripts/paper_run.py`

**Rationale:**
- ✅ Simple one-command execution: `python scripts/paper_run.py`
- ✅ Easy to schedule with cron: `0 9 * * 1-5 python /path/to/paper_run.py`
- ✅ No additional service to manage (already have T3, T4, T5)
- ✅ Easy to run manually for testing
- ✅ Stdout/file output easy to redirect/capture
- ✅ Clear separation: services for core logic, script for automation

**Alternatives Considered:**

1. **FastAPI Microservice (T6 Service)**
   - ❌ Over-engineering for simple automation
   - ❌ Adds another service to manage
   - ❌ Would need endpoint to trigger run, then poll for completion
   - ❌ Complexity not justified for one-command automation

2. **Shell Script**
   - ❌ Limited error handling
   - ❌ No P&L calculation logic
   - ❌ Difficult to maintain
   - ❌ Poor cross-platform support

3. **Jupyter Notebook**
   - ❌ Not suitable for cron scheduling
   - ❌ Requires notebook server
   - ❌ Interactive, not automated

## Implementation Details

### 1. Script Location and Structure

```
scripts/
├── paper_run.py          # Main CLI script (NEW)
├── test_paper_run.py     # Integration tests (NEW)
└── ...existing scripts...
```

### 2. Command-Line Interface

```bash
# Basic usage (uses defaults from .env)
python scripts/paper_run.py

# Custom parameters
python scripts/paper_run.py \
  --symbols AAPL MSFT GOOGL \
  --capital 100000 \
  --max-position-size 20000 \
  --output results.json

# Specific date (for backtesting)
python scripts/paper_run.py --as-of-date 2024-12-31

# Dry run (check without executing)
python scripts/paper_run.py --dry-run
```

### 3. Dependencies and Health Checks

**Required Services:**
- Signal Service (T3) on http://localhost:8001
- Execution Gateway (T4) on http://localhost:8002
- Orchestrator Service (T5) on http://localhost:8003
- PostgreSQL database

**Health Check Strategy:**
```python
async def check_dependencies():
    """Verify all required services are healthy."""
    services = [
        ("Signal Service", "http://localhost:8001/"),
        ("Execution Gateway", "http://localhost:8002/"),
        ("Orchestrator", "http://localhost:8003/"),
    ]

    for name, url in services:
        try:
            response = await httpx.get(url, timeout=5.0)
            if response.status_code != 200:
                raise RuntimeError(f"{name} unhealthy: {response.status_code}")
        except Exception as e:
            raise RuntimeError(f"{name} unavailable: {e}")
```

### 4. P&L Calculation

**Simple P&L (MVP):**
- Track orders submitted vs accepted
- Calculate notional value of accepted orders
- Report execution success rate

**Formula:**
```python
total_notional = sum(order.qty * order.price for order in accepted_orders)
success_rate = num_accepted / num_submitted * 100
```

**Future Enhancements (P1):**
- Actual P&L from position fills and current prices
- Realized vs unrealized P&L
- Per-symbol P&L breakdown
- Cumulative P&L over time

### 5. Output Formats

**Console Output (default):**
```
================================================================================
  PAPER TRADING RUN - 2025-01-17 09:00:00 EST
================================================================================

Symbols:     AAPL, MSFT, GOOGL
Capital:     $100,000.00
Max Position: $20,000.00

[1/5] Checking dependencies...
  ✓ Signal Service (http://localhost:8001)
  ✓ Execution Gateway (http://localhost:8002)
  ✓ Orchestrator (http://localhost:8003)

[2/5] Triggering orchestration run...
  Run ID: 550e8400-e29b-41d4-a716-446655440000

[3/5] Waiting for completion...
  Status: completed (4.2s)

[4/5] Calculating P&L...
  Signals Generated:  3
  Orders Submitted:   3
  Orders Accepted:    3
  Orders Rejected:    0
  Total Notional:     $60,000.00
  Success Rate:       100%

[5/5] Saving results...
  ✓ Saved to: /path/to/results/2025-01-17_paper_run.json

================================================================================
  PAPER RUN COMPLETE - Status: SUCCESS
================================================================================
```

**JSON Output (--output flag):**
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2025-01-17T09:00:00-05:00",
  "parameters": {
    "symbols": ["AAPL", "MSFT", "GOOGL"],
    "capital": 100000.00,
    "max_position_size": 20000.00
  },
  "results": {
    "num_signals": 3,
    "num_orders_submitted": 3,
    "num_orders_accepted": 3,
    "num_orders_rejected": 0,
    "total_notional": 60000.00,
    "success_rate": 1.0,
    "duration_seconds": 4.2
  },
  "orders": [
    {
      "symbol": "AAPL",
      "side": "buy",
      "qty": 133,
      "status": "accepted",
      "client_order_id": "abc123..."
    }
  ],
  "status": "success"
}
```

### 6. Error Handling

**Error Categories:**

1. **Dependency Errors** (exit code 1)
   - Service unavailable
   - Database connection failed
   - Health check failed

2. **Orchestration Errors** (exit code 2)
   - API returned error
   - Timeout waiting for completion
   - Partial failure (some orders rejected)

3. **Data Errors** (exit code 3)
   - Invalid parameters
   - Missing configuration
   - File write errors

**Example Error Output:**
```
❌ ERROR: Signal Service unavailable

The Signal Service at http://localhost:8001 is not responding.

Troubleshooting:
1. Check if service is running: ps aux | grep signal_service
2. Start service: uvicorn apps.signal_service.main:app --port 8001
3. Check logs: tail -f logs/signal_service.log

Exiting with code 1.
```

### 7. Scheduling Support

**Cron Example (Daily at 9:00 AM EST):**
```bash
# Add to crontab -e
0 9 * * 1-5 cd /path/to/trading_platform && \
    source .venv/bin/activate && \
    python scripts/paper_run.py --output /var/log/trading/paper_run.json \
    >> /var/log/trading/paper_run.log 2>&1
```

**Systemd Timer Example:**
```ini
# /etc/systemd/system/paper-run.timer
[Unit]
Description=Daily paper trading run

[Timer]
OnCalendar=Mon-Fri *-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### 8. Configuration

**Default Configuration (.env):**
```bash
# Paper Run Defaults
PAPER_RUN_SYMBOLS=AAPL,MSFT,GOOGL
PAPER_RUN_CAPITAL=100000
PAPER_RUN_MAX_POSITION_SIZE=20000

# Service URLs (from T3, T4, T5)
SIGNAL_SERVICE_URL=http://localhost:8001
EXECUTION_GATEWAY_URL=http://localhost:8002
ORCHESTRATOR_URL=http://localhost:8003
```

**Command-line arguments override .env values.**

## Consequences

### Positive

1. ✅ **One-Command Execution** - Complete pipeline in single command
2. ✅ **Easy Scheduling** - Cron/systemd timer compatible
3. ✅ **Clear Reporting** - Formatted output for humans and machines
4. ✅ **Health Checks** - Fails fast if dependencies unavailable
5. ✅ **Low Overhead** - No additional service to manage
6. ✅ **Flexible** - CLI args override defaults
7. ✅ **Testable** - Can mock HTTP calls for unit tests
8. ✅ **Educational** - Clear example of service orchestration

### Negative

1. ❌ **Not Real-Time** - Script-based, not event-driven
2. ❌ **Limited P&L** - Simple notional calculation (not mark-to-market)
3. ❌ **No Web UI** - CLI only (addressed in P2)
4. ❌ **Synchronous** - Blocks until completion

### Mitigations

- **Real-Time:** P1 will add event-driven orchestration
- **P&L:** P1 will add position tracking and mark-to-market
- **Web UI:** P2 includes web console
- **Async:** Script uses async/await internally for I/O

## Alternatives Considered

### Alternative 1: Add /run Endpoint to Orchestrator (T5)

**Description:** Add synchronous endpoint to Orchestrator that blocks until complete.

**Pros:**
- Reuses existing service
- No new script needed

**Cons:**
- HTTP timeout issues (run may take minutes)
- No formatted output (just JSON)
- No P&L calculation
- No health checks
- Still need script to call it and format output

**Decision:** Rejected - doesn't solve the problem, just moves it.

### Alternative 2: Make paper_run.py a FastAPI Service

**Description:** Create T6 as a microservice with /trigger endpoint.

**Pros:**
- Consistent with other components
- Can poll for status via API

**Cons:**
- Over-engineering for one-command automation
- Adds service management complexity
- Harder to schedule (need to call API then poll)
- No stdout for cron logging

**Decision:** Rejected - unnecessary complexity for MVP.

### Alternative 3: Integrate into Existing Service (T5)

**Description:** Add paper_run functionality directly to Orchestrator Service.

**Pros:**
- One less file

**Cons:**
- Violates single responsibility (orchestration vs automation)
- Harder to test automation separately
- Couples scheduling logic to service

**Decision:** Rejected - better separation of concerns with standalone script.

## Implementation Phases

### Phase 1: Core Script (MVP)
- Basic CLI with argparse
- Health checks for T3, T4, T5
- Call Orchestrator API
- Wait for completion
- Simple P&L calculation (notional)
- Formatted console output

### Phase 2: Enhanced Reporting
- JSON file output
- HTML report generation
- CSV export
- Email notifications (optional)

### Phase 3: Advanced P&L (P1)
- Query positions from T4
- Fetch current prices
- Calculate unrealized P&L
- Per-symbol breakdown
- Cumulative tracking

## Testing Strategy

### Unit Tests
```python
def test_parse_arguments():
    """Test CLI argument parsing."""

def test_calculate_simple_pnl():
    """Test notional P&L calculation."""

def test_format_console_output():
    """Test output formatting."""
```

### Integration Tests
```python
@pytest.mark.asyncio
async def test_health_checks_with_mocked_services():
    """Test health check logic with httpx mocks."""

@pytest.mark.asyncio
async def test_full_run_with_mocked_orchestrator():
    """Test complete workflow with mocked T5 API."""
```

### End-to-End Tests
```python
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_paper_run_against_real_services():
    """Test against running T3, T4, T5 services in DRY_RUN mode."""
```

## Success Metrics

- ✅ One command executes complete pipeline
- ✅ < 10 seconds total runtime (excluding orchestration time)
- ✅ Clear error messages for all failure modes
- ✅ 100% test coverage for P&L calculation
- ✅ Successful scheduling with cron (manual verification)
- ✅ Output format is both human-readable and machine-parseable

## Documentation Requirements

1. **Concept Doc:** `/docs/CONCEPTS/pnl-calculation.md` - Explain P&L types
2. **Implementation Guide:** `/docs/IMPLEMENTATION_GUIDES/p0t6-paper-run.md` - Complete walkthrough
3. **Update README.md:** Add quick start example with `paper_run.py`
4. **Update CLAUDE.md:** Add to common commands

## Related ADRs

- ADR-0006: Orchestrator Service (T5) - Provides API that `paper_run.py` calls
- ADR-0005: Execution Gateway (T4) - Source of order execution data
- ADR-0004: Signal Service (T3) - Signal generation

## References

- [P0_TICKETS.md - T6 Requirements](../TASKS/P0_TASKS.md#t6)
- [Trading Platform Realization Plan](../trading_platform_realization_plan.md)
- [GIT_WORKFLOW.md](../STANDARDS/GIT_WORKFLOW.md) - Commit and PR guidelines
