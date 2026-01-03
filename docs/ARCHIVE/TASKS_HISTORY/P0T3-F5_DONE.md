---
id: P0T3-F5
title: "Model Hot Reload"
phase: P0
task: T3
priority: P0
owner: "@development-team"
state: DONE
created: 2025-10-20
started: 2025-10-20
completed: 2025-10-20
duration: "Completed prior to task lifecycle system"
dependencies: []
related_adrs: []
related_docs: []
feature: F5
parent_task: P0T3
---


# P0T3-F5: Model Hot Reload ✅

**Phase:** P0 (MVP Core, 0-45 days)
**Status:** DONE (Completed prior to task lifecycle system)
**Priority:** P0
**Owner:** @development-team

---

## Original Implementation Guide

**Note:** This content was migrated from `docs/IMPLEMENTATION_GUIDES/p0t3-p5-hot-reload.md`
and represents work completed before the task lifecycle management system was implemented.

---

**Phase:** Phase 5 of 7
**Status:** ✅ Complete (100% test pass rate)
**Estimated Time:** 2-3 hours
**Dependencies:** P1-P4 (Database, Model Registry, Signal Generator, FastAPI App)

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Architecture](#architecture)
4. [Step-by-Step Implementation](#step-by-step-implementation)
5. [Code Walkthrough](#code-walkthrough)
6. [Testing Strategy](#testing-strategy)
7. [Troubleshooting](#troubleshooting)
8. [Key Learnings](#key-learnings)
9. [Next Steps](#next-steps)

---

## Overview

Phase 5 implements **zero-downtime model updates** through a hot reload mechanism. This allows the Signal Service to automatically detect and load new model versions from the database without requiring service restarts.

### What We Built

1. **Background Polling Task** - Automatically checks for model updates every 5 minutes
2. **Manual Reload Endpoint** - `/api/v1/model/reload` for on-demand reloads
3. **Graceful Degradation** - Failed reloads keep current model running
4. **Test Suite** - 6 integration tests validating hot reload behavior

### Key Features

- ✅ Zero-downtime updates (requests during reload use old model)
- ✅ Automatic polling every 5 minutes (configurable)
- ✅ Manual reload endpoint for CI/CD pipelines
- ✅ Graceful error handling (failed reload keeps current model)
- ✅ Comprehensive logging for observability

---

## Prerequisites

Before starting Phase 5, ensure:

1. **Phases 1-4 Complete**
   - Database with model_registry table
   - Model Registry client working
   - Signal Generator functional
   - FastAPI application running

2. **P1-P4 Tests Passing**
   ```bash
   python scripts/test_p1_p2_model_registry.py  # Should pass
   python scripts/test_p3_signal_generator.py   # Should pass
   python scripts/test_p4_fastapi.py            # Should pass (8/8)
   ```

3. **Service Running**
   ```bash
   python -m uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001
   ```

---

## Architecture

### Hot Reload Flow

```
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Application                       │
│                                                             │
│  ┌──────────────┐        ┌──────────────┐                 │
│  │   Lifespan   │───────▶│  Reload Task │                 │
│  │   Startup    │        │  (Background)│                 │
│  └──────────────┘        └──────────────┘                 │
│                                 │                           │
│                                 │ Every 5 min              │
│                                 ▼                           │
│                          ┌──────────────┐                  │
│                          │  Check DB    │                  │
│                          │  for new     │                  │
│                          │  version     │                  │
│                          └──────────────┘                  │
│                                 │                           │
│                       ┌─────────┴─────────┐               │
│                       │                   │               │
│                   No change          Version changed       │
│                       │                   │               │
│                       ▼                   ▼               │
│                   Continue          Reload model          │
│                   polling           Update global         │
│                                     state                 │
└─────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                        ┌──────────────────────┐
                        │ All subsequent       │
                        │ requests use         │
                        │ new model            │
                        └──────────────────────┘
```

### Manual Reload Flow

```
Client                  FastAPI                 ModelRegistry         Database
  │                        │                          │                  │
  │  POST /model/reload    │                          │                  │
  ├───────────────────────▶│                          │                  │
  │                        │  reload_if_changed()     │                  │
  │                        ├─────────────────────────▶│                  │
  │                        │                          │  SELECT active   │
  │                        │                          ├─────────────────▶│
  │                        │                          │  model metadata  │
  │                        │                          │◀─────────────────┤
  │                        │                          │                  │
  │                        │                      Compare versions       │
  │                        │                          │                  │
  │                        │     If changed:          │                  │
  │                        │     Load model from file │                  │
  │                        │     Update global state  │                  │
  │                        │◀─────────────────────────┤                  │
  │  {"reloaded": true}    │                          │                  │
  │◀───────────────────────┤                          │                  │
```

### Key Design Decisions

1. **Background Task Using asyncio**
   - Non-blocking, runs concurrently with request handling
   - Graceful shutdown via task cancellation
   - Resilient: continues polling even if one check fails

2. **Reload During Request Handling**
   - Requests during reload use old model (no downtime)
   - Global state update is atomic (Python GIL)
   - ModelRegistry handles concurrent access safely

3. **Configurable Polling Interval**
   - Default: 300 seconds (5 minutes)
   - Configurable via `model_reload_interval_seconds` in settings
   - Balance: freshness vs. database load

---

## Step-by-Step Implementation

### Step 1: Add asyncio Import (5 min)

**File:** `apps/signal_service/main.py`

Add `asyncio` import for background task management:

```python
import logging
import asyncio  # ← Add this
from contextlib import asynccontextmanager
```

**Why:** Need asyncio.create_task() and asyncio.sleep() for background polling.

---

### Step 2: Implement Background Polling Task (30 min)

**File:** `apps/signal_service/main.py`

Add background task function before the lifespan function:

```python
# ==============================================================================
# Background Tasks
# ==============================================================================

async def model_reload_task():
    """
    Background task to poll model registry and reload on version changes.

    This task runs continuously in the background, checking for model updates
    at regular intervals. If a new model version is detected in the database,
    it automatically reloads without requiring service restart.

    Behavior:
        1. Sleeps for configured interval (default: 300 seconds / 5 minutes)
        2. Checks database for model version changes
        3. Reloads model if version changed
        4. Logs reload events
        5. Continues polling even if one check fails (resilience)

    Configuration:
        Interval controlled by settings.model_reload_interval_seconds

    Example Log Output:
        2024-12-31 10:00:00 - INFO - Checking for model updates...
        2024-12-31 10:00:00 - INFO - Model auto-reloaded: alpha_baseline v1.0.1

    Notes:
        - Zero-downtime updates: requests during reload use old model
        - Graceful degradation: failed reload keeps current model
        - Thread-safe: ModelRegistry handles concurrent access

    See Also:
        - ModelRegistry.reload_if_changed() for reload logic
        - /api/v1/model/reload for manual reload endpoint
    """
    logger.info(
        f"Starting model reload task "
        f"(interval: {settings.model_reload_interval_seconds}s)"
    )

    while True:
        try:
            # Wait for configured interval
            await asyncio.sleep(settings.model_reload_interval_seconds)

            # Check for model updates
            logger.debug("Checking for model updates...")
            reloaded = model_registry.reload_if_changed(
                strategy=settings.default_strategy
            )

            if reloaded:
                logger.info(
                    f"Model auto-reloaded: "
                    f"{model_registry.current_metadata.strategy_name} "
                    f"v{model_registry.current_metadata.version}"
                )
            else:
                logger.debug("No model updates found")

        except Exception as e:
            logger.error(f"Model reload task failed: {e}", exc_info=True)
            # Continue polling even if one check fails
            # This provides resilience against transient errors
```

**Key Points:**

1. **Infinite Loop** - `while True` keeps task running
2. **Async Sleep** - `await asyncio.sleep()` yields control during wait
3. **Error Resilience** - Catch all exceptions, continue polling
4. **Logging** - INFO for reloads, DEBUG for no-op checks

---

### Step 3: Start Background Task in Lifespan (15 min)

**File:** `apps/signal_service/main.py`

Update lifespan function to start/stop background task:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    global model_registry, signal_generator

    # ... existing startup code ...

    # Step 4: Start background model reload task
    logger.info("Starting background model reload task...")
    reload_task = asyncio.create_task(model_reload_task())

    yield  # Application runs here

    # Shutdown - Cancel background task
    logger.info("Stopping background model reload task...")
    if 'reload_task' in locals():
        reload_task.cancel()
        try:
            await reload_task
        except asyncio.CancelledError:
            pass
    logger.info("Signal Service shutting down...")
```

**Key Points:**

1. **Create Task** - `asyncio.create_task()` starts task concurrently
2. **Graceful Shutdown** - Cancel task and await cancellation on shutdown
3. **CancelledError** - Expected exception when cancelling, suppress it

---

### Step 4: Add Manual Reload Endpoint (30 min)

**File:** `apps/signal_service/main.py`

Add endpoint after `/api/v1/model/info`:

```python
@app.post("/api/v1/model/reload", tags=["Model"])
async def reload_model():
    """
    Manually trigger model reload from database registry.

    This endpoint forces an immediate check for model version changes,
    bypassing the automatic polling interval. Useful for:
        - Testing model deployments
        - Urgent model updates
        - CI/CD pipelines
        - Debugging reload issues

    Behavior:
        1. Queries database for active model version
        2. Compares with currently loaded version
        3. Reloads model if version changed
        4. Returns reload status

    Returns:
        Dictionary with reload status and current version

    Status Codes:
        - 200: Reload check completed successfully
        - 500: Reload failed (database error, file not found, etc.)
        - 503: Model registry not initialized

    Example:
        POST /api/v1/model/reload

        Response (200 OK) - No change:
        {
            "reloaded": false,
            "version": "v1.0.0",
            "message": "Model already up to date"
        }

        Response (200 OK) - Reloaded:
        {
            "reloaded": true,
            "version": "v1.0.1",
            "previous_version": "v1.0.0",
            "message": "Model reloaded successfully"
        }

    Notes:
        - Safe to call multiple times (idempotent)
        - Zero-downtime: requests during reload use old model
        - Background task continues polling after manual reload

    Usage Example:
        # Shell script for CI/CD
        curl -X POST http://localhost:8001/api/v1/model/reload | jq

        # Python
        import requests
        response = requests.post("http://localhost:8001/api/v1/model/reload")
        print(response.json())
    """
    if model_registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model registry not initialized"
        )

    try:
        # Store previous version for comparison
        previous_version = (
            model_registry.current_metadata.version
            if model_registry.is_loaded
            else None
        )

        # Trigger reload check
        logger.info("Manual model reload requested")
        reloaded = model_registry.reload_if_changed(
            strategy=settings.default_strategy
        )

        # Get current version
        current_version = (
            model_registry.current_metadata.version
            if model_registry.is_loaded
            else "none"
        )

        # Build response
        response = {
            "reloaded": reloaded,
            "version": current_version,
        }

        if reloaded:
            response["previous_version"] = previous_version
            response["message"] = "Model reloaded successfully"
            logger.info(f"Manual reload successful: {previous_version} -> {current_version}")
        else:
            response["message"] = "Model already up to date"
            logger.info("Manual reload: no changes detected")

        return response

    except Exception as e:
        logger.error(f"Manual model reload failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}"
        )
```

**Key Points:**

1. **Idempotent** - Safe to call multiple times, returns status
2. **Detailed Response** - Includes previous/current version on reload
3. **Error Handling** - Catches all exceptions, returns 500 with details
4. **Logging** - All reload attempts logged for debugging

---

### Step 5: Create Test Script (45 min)

**File:** `scripts/test_p5_hot_reload.py`

Create comprehensive test suite (see file for full implementation):

```python
#!/usr/bin/env python3
"""Manual integration test for P5: Hot Reload Mechanism."""

def main():
    # Test 0: Service running
    # Test 1: Get current model version
    # Test 2: Manual reload (no change)
    # Test 3: Update model version in database
    # Test 4: Manual reload (with change)
    # Test 5: Verify signal generation still works
    # Test 6: Background polling info
    # Cleanup: Restore original model
```

**Test Coverage:**

1. ✅ Service connectivity
2. ✅ Model info endpoint
3. ✅ Manual reload (no change) - idempotency
4. ✅ Database model version update
5. ✅ Manual reload (with change) - actual reload
6. ✅ Zero-downtime (signal generation during/after reload)
7. ℹ️ Background polling (informational only)

---

## Code Walkthrough

### Background Task: Async Sleep Pattern

**Why use async sleep instead of time.sleep?**

```python
# ❌ BAD - Blocks entire event loop
import time
while True:
    time.sleep(300)  # Blocks all requests for 5 minutes!
    check_for_updates()

# ✅ GOOD - Yields control during sleep
import asyncio
while True:
    await asyncio.sleep(300)  # Other requests continue
    check_for_updates()
```

**Key Insight:** FastAPI runs on async event loop. `time.sleep()` blocks the entire loop, preventing ALL requests. `await asyncio.sleep()` yields control, allowing other requests to be handled.

### Graceful Shutdown: Task Cancellation

**Why catch CancelledError?**

```python
# In lifespan shutdown:
reload_task.cancel()
try:
    await reload_task
except asyncio.CancelledError:
    pass  # Expected when cancelling
```

**Flow:**
1. `cancel()` - Raises `CancelledError` in task
2. `await` - Wait for task to complete cancellation
3. `except CancelledError` - Suppress expected exception

**Without this:** Service shutdown would log error on every stop.

### Zero-Downtime Reload: Global State Update

**How does zero-downtime work?**

```python
# In ModelRegistry.reload_if_changed():
new_model = load_model_from_file(new_metadata.model_path)

# Atomic update (Python GIL ensures atomicity)
self._current_model = new_model
self._current_metadata = new_metadata
```

**Key Points:**
1. Load new model completely before updating state
2. Update is atomic (Python GIL)
3. Requests during load use old model
4. Requests after update use new model
5. No requests see partially-loaded model

### Error Resilience: Continue on Failure

**Why continue polling after error?**

```python
while True:
    try:
        # ... reload logic ...
    except Exception as e:
        logger.error(f"Reload failed: {e}")
        # Continue polling - don't crash task!
```

**Rationale:**
- Database connection errors are transient
- File I/O errors might be temporary
- One failed check shouldn't stop all future checks
- Current model remains available

**Without this:** Single database hiccup would stop all automatic reloads permanently.

---

## Testing Strategy

### Manual Testing

**1. Start Service**
```bash
python -m uvicorn apps.signal_service.main:app --host 0.0.0.0 --port 8001
```

Check logs for:
```
INFO - Starting background model reload task...
INFO - Starting model reload task (interval: 300s)
```

**2. Run P5 Test Suite**
```bash
python scripts/test_p5_hot_reload.py
```

Expected output:
```
✓ All P5 tests passed!

Hot reload mechanism is working correctly:
  - Manual reload endpoint functional
  - Model version updates detected
  - Zero-downtime reload verified
  - Signal generation works after reload
```

**3. Test Background Polling (Manual)**

```bash
# Terminal 1: Monitor service logs
tail -f /tmp/fastapi_p5.log | grep reload

# Terminal 2: Update model version in database
psql -U postgres -d trading_platform <<EOF
UPDATE model_registry
SET status = 'inactive'
WHERE status = 'active';

INSERT INTO model_registry (
    strategy_name, version, model_path, status,
    performance_metrics, config, activated_at
) VALUES (
    'alpha_baseline', 'v2.0.0',
    'artifacts/models/alpha_baseline.txt', 'active',
    '{"ic": 0.085}', '{}', NOW()
);
EOF

# Terminal 1: Wait 5-10 minutes, should see:
# INFO - Model auto-reloaded: alpha_baseline vv2.0.0
```

### Automated Testing

**Test Suite Coverage:**

| Test | What It Validates | Pass/Fail |
|------|-------------------|-----------|
| 0 | Service connectivity | ✅ |
| 1 | Model info endpoint | ✅ |
| 2 | Manual reload (no change) | ✅ |
| 3 | Database update | ✅ |
| 4 | Manual reload (with change) | ✅ |
| 5 | Signal generation after reload | ✅ |
| 6 | Background polling (info) | ℹ️ |

**Total: 6/6 tests passed (100%)**

### Performance Validation

**Reload Time Measurement:**

```bash
time curl -X POST http://localhost:8001/api/v1/model/reload
```

Expected: < 50ms (LightGBM model load is fast)

**Zero-Downtime Validation:**

```bash
# Terminal 1: Generate signals continuously
while true; do
  curl -s -X POST http://localhost:8001/api/v1/signals/generate \
    -H "Content-Type: application/json" \
    -d '{"symbols": ["AAPL", "MSFT", "GOOGL"], "as_of_date": "2024-12-31"}' \
    | jq -r '.metadata.model_version'
  sleep 1
done

# Terminal 2: Trigger reload
curl -X POST http://localhost:8001/api/v1/model/reload

# Terminal 1: Should see:
# v1.0.0
# v1.0.0  ← Reload happens here
# v1.0.1  ← New version immediately available
# v1.0.1
```

---

## Troubleshooting

### Issue 1: Background Task Not Starting

**Symptom:**
```
No log line: "Starting background model reload task..."
```

**Root Cause:** Lifespan function not starting task

**Fix:**
```python
# Ensure this is in lifespan after model load:
reload_task = asyncio.create_task(model_reload_task())
```

**Validation:**
```bash
grep "Starting background model reload task" /tmp/fastapi_p5.log
```

---

### Issue 2: Background Task Crashing Silently

**Symptom:**
Task starts but no periodic checks occur

**Root Cause:** Unhandled exception in task

**Fix:**
Add comprehensive error handling:
```python
async def model_reload_task():
    while True:
        try:
            # All logic here
        except Exception as e:
            logger.error(f"Reload failed: {e}", exc_info=True)
            # Continue polling!
```

**Validation:**
```bash
# Should see debug logs every 5 min (if LOG_LEVEL=DEBUG)
grep "Checking for model updates" /tmp/fastapi_p5.log
```

---

### Issue 3: Manual Reload Returns 503

**Symptom:**
```json
{"detail": "Model registry not initialized"}
```

**Root Cause:** ModelRegistry not created in lifespan

**Fix:**
Ensure lifespan initializes global `model_registry`:
```python
global model_registry, signal_generator
model_registry = ModelRegistry(settings.database_url)
```

**Validation:**
```bash
curl http://localhost:8001/api/v1/model/info
# Should return model metadata, not 503
```

---

### Issue 4: Reload Doesn't Detect New Version

**Symptom:**
Database has new active model, but reload returns `reloaded: false`

**Root Cause:** Multiple active models in database

**Fix:**
```sql
-- Check for multiple active models
SELECT strategy_name, version, status, activated_at
FROM model_registry
WHERE status = 'active';

-- Should see only ONE row. If multiple, deactivate old ones:
UPDATE model_registry
SET status = 'inactive', deactivated_at = NOW()
WHERE id = <old_model_id>;
```

**Validation:**
```bash
curl -X POST http://localhost:8001/api/v1/model/reload | jq
# Should return reloaded: true
```

---

### Issue 5: Service Doesn't Shutdown Cleanly

**Symptom:**
CTRL+C shows errors about task cancellation

**Root Cause:** Not awaiting cancelled task

**Fix:**
```python
# In lifespan shutdown:
if 'reload_task' in locals():
    reload_task.cancel()
    try:
        await reload_task  # ← Must await!
    except asyncio.CancelledError:
        pass
```

**Validation:**
```bash
# Start service, then CTRL+C
# Should see clean shutdown:
# INFO - Stopping background model reload task...
# INFO - Signal Service shutting down...
```

---

### Issue 6: High Database Load

**Symptom:**
Database showing many connections from signal service

**Root Cause:** Polling interval too short

**Fix:**
Increase polling interval in config:
```python
# apps/signal_service/config.py
model_reload_interval_seconds: int = 600  # 10 minutes instead of 5
```

**Tradeoff:**
- Lower interval → Fresher models, higher DB load
- Higher interval → Lower DB load, staler models

**Recommendation:** 300s (5 min) is good balance for most use cases

---

## Key Learnings

### 1. Async Background Tasks in FastAPI

**Lesson:** Use `asyncio.create_task()` in lifespan for long-running background work.

**Pattern:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_work())
    yield
    task.cancel()
    await task
```

**Pitfall:** Forgetting to cancel task causes resource leak.

---

### 2. Zero-Downtime Deployments

**Lesson:** Load new resource completely before updating global state.

**Pattern:**
```python
# ✅ GOOD - Atomic update
new_resource = load_resource()
self._resource = new_resource

# ❌ BAD - Partial state visible
self._resource = None
self._resource = load_resource()  # Requests see None!
```

**Key:** Python's GIL ensures assignment is atomic.

---

### 3. Error Resilience in Background Tasks

**Lesson:** Never let background tasks crash on transient errors.

**Pattern:**
```python
while True:
    try:
        # Risky operation
    except Exception as e:
        logger.error(f"Error: {e}")
        # Continue! Don't re-raise!
```

**Rationale:** One error shouldn't stop all future checks.

---

### 4. Idempotent API Endpoints

**Lesson:** Reload endpoint should be safe to call repeatedly.

**Implementation:**
```python
# Returns status of check, not just success
{
    "reloaded": false,  # No change
    "version": "v1.0.0",
    "message": "Model already up to date"
}
```

**Benefit:** Safe to call from automated scripts, no side effects.

---

### 5. Comprehensive Logging for Background Tasks

**Lesson:** Background tasks need extra logging since they're not request-driven.

**Levels:**
- INFO: Successful reloads, task start/stop
- DEBUG: Periodic checks with no change
- ERROR: Failed reloads (with exc_info=True)

**Example:**
```python
logger.info("Model auto-reloaded: alpha_baseline v1.0.1")
logger.debug("No model updates found")
logger.error("Reload failed: Connection refused", exc_info=True)
```

---

### 6. Test Automation for Background Behavior

**Lesson:** Background tasks are hard to test automatically.

**Solution:**
- Test trigger mechanism (manual reload endpoint)
- Provide manual test instructions for background polling
- Use informational test for long-running behavior

**P5 Approach:**
- Tests 0-5: Automated (manual reload endpoint)
- Test 6: Informational (background polling instructions)

---

## Next Steps

### Phase 6: Integration Tests

With hot reload complete, proceed to comprehensive integration testing:

1. **Feature Parity Tests** - Validate production features match research
2. **End-to-End Tests** - Full workflow from database to signals
3. **Performance Tests** - Latency, throughput benchmarks
4. **Contract Tests** - API conforms to OpenAPI spec

**File:** `docs/IMPLEMENTATION_GUIDES/t3-p6-integration-tests.md`

---

### Phase 7: Documentation & Deployment

Final phase: Production-ready documentation and deployment:

1. **API Documentation** - Complete OpenAPI specification
2. **Deployment Guide** - Docker, Kubernetes, monitoring
3. **Runbook** - Common operations and troubleshooting
4. **Performance Tuning** - Optimization recommendations

**File:** `docs/IMPLEMENTATION_GUIDES/t3-p7-documentation.md`

---

## Summary

**Phase 5 Achievements:**

✅ **Background Polling Task** - Automatic model updates every 5 minutes
✅ **Manual Reload Endpoint** - On-demand reload for CI/CD
✅ **Zero-Downtime Updates** - No service interruption during reload
✅ **Graceful Degradation** - Failed reloads keep current model
✅ **Comprehensive Tests** - 6/6 tests passing (100%)
✅ **Production-Ready** - Error handling, logging, observability

**Files Modified:**
- `apps/signal_service/main.py` (+150 lines)
- `scripts/test_p5_hot_reload.py` (NEW, 350 lines)

**Test Results:**
```
Total tests: 6
Passed: 6
Failed: 0
Pass rate: 100.0%
```

**Ready for Phase 6: Integration Tests** 🚀

---

## References

- **ADR-0004:** Signal Service Architecture
- **T3 Implementation Guide:** Complete T3 roadmap
- **FastAPI Lifespan:** https://fastapi.tiangolo.com/advanced/events/
- **asyncio Tasks:** https://docs.python.org/3/library/asyncio-task.html

---

## Migration Notes

**Migrated:** 2025-10-20
**Original File:** `docs/IMPLEMENTATION_GUIDES/p0t3-p5-hot-reload.md`
**Migration:** Automated migration to task lifecycle system

**Historical Context:**
This task was completed before the PxTy_TASK → _PROGRESS → _DONE lifecycle
system was introduced. The content above represents the implementation guide
that was created during development.

For new tasks, use the structured DONE template with:
- Summary of what was built
- Code references
- Test coverage details
- Zen-MCP review history
- Lessons learned
- Metrics

See `docs/TASKS/00-TEMPLATE_DONE.md` for the current standard format.
