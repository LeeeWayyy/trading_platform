# Hot Reload

## Plain English Explanation

Hot reload is a technique that allows you to update a running application (like swapping out a machine learning model) **without restarting the service**. Think of it like changing the engine in a car while it's still driving.

In the context of trading systems, hot reload lets you:
- Deploy new model versions without downtime
- Update models based on latest market data
- Fix bugs in models without stopping live trading
- A/B test different model versions by quick switching

**How it works:**
1. New model is saved to database with new version number
2. Background task checks database every N minutes
3. If new version detected, loads new model into memory
4. Atomically swaps old model with new model
5. Next API request uses new model (zero downtime)

## Why It Matters

### Problem Without Hot Reload

**Traditional deployment:**
```
1. Build new model
2. Stop signal service
3. Deploy new model file
4. Restart signal service
5. Wait for service to be ready
```

**Consequences:**
- **Downtime**: 30-60 seconds of no signal generation
- **Missed opportunities**: Market moves during deployment
- **Risk**: If deployment fails at 3:59 PM, you miss market close
- **Manual process**: Requires human intervention

**Real-world example:**
You train a new model at 3:30 PM with latest market data. To deploy it before market close (4:00 PM), you need to:
- Stop the service (~5 seconds)
- Copy model file (~10 seconds)
- Restart service (~30 seconds)
- Verify health (~10 seconds)

Total: ~55 seconds of downtime. If anything goes wrong, you miss the closing auction.

### Solution With Hot Reload

**Hot reload deployment:**
```
1. Build new model
2. Register in database with status='active'
3. Wait up to 5 minutes (next poll)
4. Service automatically loads new model
5. Zero downtime, zero manual steps
```

**Benefits:**
- **Zero downtime**: Service never stops
- **Automatic**: No manual deployment steps
- **Safe**: Old model stays active until new one loads successfully
- **Fast rollback**: Just update database to reactivate old version

**Same example with hot reload:**
You train a new model at 3:30 PM. You:
- Register model in database (~1 second)
- Service detects change within 5 minutes
- New model active by 3:35 PM
- Zero downtime, zero risk

## Common Pitfalls

### Pitfall 1: Race Conditions

**Problem:** Multiple requests arrive during model swap.

**Example:**
```
Request A arrives → Uses old model
Model swap starts (500ms)
Request B arrives → Uses ??? (old or new?)
Model swap completes
Request C arrives → Uses new model
```

**Solution:** Atomic pointer swap
```python
# ❌ Wrong: Not atomic
self.current_model = None  # Brief window with no model!
self.current_model = new_model

# ✅ Correct: Atomic assignment
self.current_model = new_model  # Single operation
```

Python object assignment is atomic, so Request B will use either old or new model (never None or corrupt state).

### Pitfall 2: Memory Leaks

**Problem:** Old model not garbage collected, memory grows with each reload.

**Example:**
```python
# ❌ Wrong: Old model reference kept in list
self.model_history.append(self.current_model)  # Memory leak!
self.current_model = new_model

# ✅ Correct: Old model dereferenced
old_model = self.current_model  # Keep temporary reference
self.current_model = new_model
# old_model is now garbage collected (no more references)
```

**Detection:**
```python
import psutil
process = psutil.Process()
print(f"Memory: {process.memory_info().rss / 1024 / 1024:.1f} MB")
```

If memory grows by ~100MB with each reload and never decreases, you have a leak.

### Pitfall 3: Too Frequent Polling

**Problem:** Checking for updates every second wastes resources.

**Example:**
```python
# ❌ Wrong: Too frequent
while True:
    await asyncio.sleep(1)  # 86,400 DB queries per day!
    check_for_updates()

# ✅ Correct: Reasonable interval
while True:
    await asyncio.sleep(300)  # 288 DB queries per day
    check_for_updates()
```

**Why 5 minutes is good:**
- Models change maybe 1-4 times per day
- 5 min delay acceptable (not trading on second-to-second changes)
- Low database load (288 queries/day vs 86,400)
- Can manually trigger if urgent

### Pitfall 4: Failed Reload Breaks Service

**Problem:** New model fails to load, service crashes.

**Example:**
```python
# ❌ Wrong: Failed load crashes service
self.current_model = load_model(new_path)  # Raises exception
# Service is now broken!

# ✅ Correct: Keep old model on failure
try:
    new_model = load_model(new_path)
    self.current_model = new_model
except Exception as e:
    logger.error(f"Failed to load model: {e}")
    # self.current_model is unchanged, service continues
```

**Best practice:** Always validate new model before swapping:
```python
new_model = load_model(new_path)

# Smoke test: Can it make predictions?
test_features = np.random.randn(1, 158)
prediction = new_model.predict(test_features)
assert prediction.shape == (1,), "Model output shape wrong"

# All checks passed, safe to swap
self.current_model = new_model
```

### Pitfall 5: Forgetting to Deactivate Old Models

**Problem:** Database has multiple active models, unclear which to load.

**Example:**
```sql
-- ❌ Wrong: Multiple active models
SELECT * FROM model_registry WHERE status = 'active';
 id | strategy_name  | version  | status
----+----------------+----------+--------
  1 | alpha_baseline | v1.0.0   | active
  2 | alpha_baseline | v1.1.0   | active  -- Forgot to deactivate v1.0.0!
```

**Solution:** Transactionally deactivate old + activate new
```sql
-- ✅ Correct: Single active model
BEGIN;
UPDATE model_registry
SET status = 'inactive', deactivated_at = NOW()
WHERE strategy_name = 'alpha_baseline' AND status = 'active';

INSERT INTO model_registry (strategy_name, version, status, activated_at)
VALUES ('alpha_baseline', 'v1.1.0', 'active', NOW());
COMMIT;
```

## Examples

### Example 1: Background Polling Pattern

**Scenario:** Service checks for model updates every 5 minutes.

```python
import asyncio
from datetime import datetime

async def model_reload_task(registry: ModelRegistry):
    """Background task that polls for model updates."""
    while True:
        # Sleep first to allow service startup to complete
        await asyncio.sleep(300)  # 5 minutes

        try:
            # Check if database has new version
            reloaded = registry.reload_if_changed("alpha_baseline")

            if reloaded:
                version = registry.current_metadata.version
                logger.info(f"Model auto-reloaded: {version}")
            else:
                logger.debug("Model up to date")

        except Exception as e:
            # Don't crash the service on reload failure
            logger.error(f"Model reload failed: {e}", exc_info=True)

# Start background task when service starts
@app.on_event("startup")
async def startup():
    reload_task = asyncio.create_task(model_reload_task(registry))

# Stop background task when service shuts down
@app.on_event("shutdown")
async def shutdown():
    reload_task.cancel()
    await reload_task
```

**Timeline:**
```
00:00 - Service starts, loads v1.0.0
00:05 - Poll #1: v1.0.0 (no change)
00:10 - Poll #2: v1.0.0 (no change)
00:12 - New model v1.1.0 registered in database
00:15 - Poll #3: Detects v1.1.0, reloads → new model active
00:20 - Poll #4: v1.1.0 (no change)
```

**Latency:** Up to 5 minutes between model registration and activation. If this is too slow, reduce polling interval or use manual reload endpoint.

### Example 2: Manual Reload Endpoint

**Scenario:** Immediate model reload without waiting for background task.

```python
@app.post("/api/v1/model/reload")
async def reload_model():
    """Manually trigger model reload from database."""
    previous_version = registry.current_metadata.version

    reloaded = registry.reload_if_changed("alpha_baseline")

    if reloaded:
        current_version = registry.current_metadata.version
        return {
            "reloaded": True,
            "previous_version": previous_version,
            "current_version": current_version,
            "message": f"Model reloaded: {previous_version} → {current_version}"
        }
    else:
        return {
            "reloaded": False,
            "version": previous_version,
            "message": "Model already up to date"
        }
```

**Usage:**
```bash
# Deploy new model
./scripts/register_model.sh alpha_baseline v1.1.0 artifacts/models/alpha_v1.1.0.txt

# Trigger immediate reload (don't wait for background poll)
curl -X POST http://localhost:8001/api/v1/model/reload

# Response:
{
  "reloaded": true,
  "previous_version": "v1.0.0",
  "current_version": "v1.1.0",
  "message": "Model reloaded: v1.0.0 → v1.1.0"
}
```

**Latency:** Immediate (< 1 second). Use this for urgent model updates.

### Example 3: Version Comparison Pattern

**Scenario:** Only reload if database version differs from current version.

```python
def reload_if_changed(self, strategy: str) -> bool:
    """Reload model only if database has newer version."""

    # Step 1: Fetch latest metadata from database
    latest_metadata = self.get_active_model_metadata(strategy)

    # Step 2: Compare with current version (if model loaded)
    if self.is_loaded:
        current_version = self.current_metadata.version

        if current_version == latest_metadata.version:
            # Versions match, no reload needed
            logger.debug(f"Model {strategy} already at {current_version}")
            return False

        logger.info(
            f"Model version changed: {current_version} → {latest_metadata.version}"
        )

    # Step 3: Load new model
    self.load_model(latest_metadata)
    return True
```

**Example execution:**
```
Attempt 1: Current=v1.0.0, DB=v1.0.0 → No reload (return False)
Attempt 2: Current=v1.0.0, DB=v1.0.0 → No reload (return False)
New model registered: DB=v1.1.0
Attempt 3: Current=v1.0.0, DB=v1.1.0 → Reload! (return True)
Attempt 4: Current=v1.1.0, DB=v1.1.0 → No reload (return False)
```

**Key insight:** Version comparison prevents redundant reloads, saving CPU and preventing unnecessary log spam.

### Example 4: Rollback Pattern

**Scenario:** New model performs poorly, need to rollback to previous version.

**Step 1:** Find previous model version
```sql
SELECT id, version, status, activated_at, deactivated_at
FROM model_registry
WHERE strategy_name = 'alpha_baseline'
ORDER BY activated_at DESC
LIMIT 5;

 id | version | status   | activated_at        | deactivated_at
----+---------+----------+---------------------+-------------------
  3 | v1.2.0  | active   | 2024-01-15 14:30:00 | NULL              -- Current (bad)
  2 | v1.1.0  | inactive | 2024-01-10 09:00:00 | 2024-01-15 14:30:00 -- Rollback target
  1 | v1.0.0  | inactive | 2024-01-01 08:00:00 | 2024-01-10 09:00:00
```

**Step 2:** Deactivate current, reactivate previous
```sql
BEGIN;

-- Deactivate bad model
UPDATE model_registry
SET status = 'inactive', deactivated_at = NOW()
WHERE id = 3;

-- Reactivate previous model
UPDATE model_registry
SET status = 'active', activated_at = NOW(), deactivated_at = NULL
WHERE id = 2;

COMMIT;
```

**Step 3:** Trigger reload
```bash
curl -X POST http://localhost:8001/api/v1/model/reload
```

**Timeline:**
```
14:30 - Deploy v1.2.0 (has bug)
14:35 - Bug detected in monitoring
14:36 - Rollback SQL executed
14:36 - Manual reload triggered
14:36 - Service now using v1.1.0 again (good)
```

**Total rollback time:** < 1 minute (vs 5-10 minutes with traditional deployment).

### Example 5: A/B Testing Pattern

**Scenario:** Run two models simultaneously, route 50% traffic to each.

**Database setup:**
```sql
-- Two active models for same strategy
INSERT INTO model_registry (strategy_name, version, model_path, status, config)
VALUES
  ('alpha_baseline', 'v1.0.0', 'models/v1.0.0.txt', 'active', '{"cohort": "A"}'),
  ('alpha_baseline', 'v1.1.0', 'models/v1.1.0.txt', 'active', '{"cohort": "B"}');
```

**Routing logic:**
```python
def get_model_for_request(symbol: str) -> ModelRegistry:
    """Route request to model A or B based on symbol hash."""

    # Deterministic routing: same symbol always gets same model
    cohort = "A" if hash(symbol) % 2 == 0 else "B"

    # Load appropriate model
    config_filter = f"config->>'cohort' = '{cohort}'"
    metadata = registry.get_active_model_metadata(
        "alpha_baseline",
        additional_filter=config_filter
    )

    return registry.load_model(metadata)

# Usage
@app.post("/api/v1/signals/generate")
async def generate_signals(request: SignalRequest):
    model = get_model_for_request(request.symbols[0])
    predictions = model.predict(features)
    # ... rest of logic
```

**Analysis after 1 week:**
```sql
SELECT
    config->>'cohort' AS cohort,
    COUNT(*) AS requests,
    AVG(sharpe_ratio) AS avg_sharpe
FROM predictions
GROUP BY config->>'cohort';

 cohort | requests | avg_sharpe
--------+----------+------------
 A      | 1,247    | 1.42
 B      | 1,253    | 1.58       -- Winner!
```

Model B has better Sharpe ratio → deactivate A, keep only B.

## Further Reading

### Technical Resources
- [Zero-Downtime Deployments (Martin Fowler)](https://martinfowler.com/bliki/BlueGreenDeployment.html)
- [Reloading Service Configurations Without Downtime](https://dohost.us/index.php/2025/07/30/reloading-service-configurations-applying-changes-without-downtime/)
- [Python asyncio Documentation](https://docs.python.org/3/library/asyncio.html)

### Related Concepts
- **Blue-Green Deployment**: Similar idea but at infrastructure level (two environments, swap traffic)
- **Canary Deployment**: Gradual rollout (1% → 10% → 100% of traffic)
- **Feature Flags**: Enable/disable features without deployment
- **Circuit Breaker**: Automatically stop using bad model if metrics degrade

### Trading System Context
- **Model drift**: Models degrade over time, hot reload enables quick updates
- **Market regimes**: Different models for bull/bear markets, switch based on regime detection
- **Compliance**: Auditors want to know which model version was used for each trade (track in metadata)

### Alternative Approaches

**1. Restart-based deployment** (what we replaced)
- Pros: Simple, well-understood
- Cons: Downtime, manual, risky

**2. Multi-instance deployment**
- Pros: Zero downtime at infrastructure level
- Cons: Requires load balancer, more complex, higher cost

**3. Container orchestration (Kubernetes)**
- Pros: Production-grade, handles failures well
- Cons: Overkill for small systems, steep learning curve

**Why we chose hot reload:**
- Zero downtime without infrastructure complexity
- Single-instance systems can use it
- Fast rollback (just update DB)
- Educational value (learn async Python patterns)

## Summary

**Hot reload is essential for production trading systems because:**
1. **Zero downtime** - Market never sleeps, neither should your service
2. **Fast updates** - Deploy new models in seconds, not minutes
3. **Safe rollback** - Revert to previous version instantly
4. **Low complexity** - No infrastructure changes needed
5. **Automatic** - Background polling eliminates manual steps

**Key implementation patterns:**
- Background polling (5-minute intervals)
- Manual reload endpoint (for urgent updates)
- Version comparison (avoid redundant reloads)
- Atomic swaps (no race conditions)
- Error handling (failed reload doesn't break service)

**Remember:** Hot reload is a feature, not a substitute for proper testing. Always validate models in staging before production deployment.
