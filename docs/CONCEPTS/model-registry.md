# Model Registry

## Plain English Explanation

A model registry is a database that keeps track of all trained machine learning models in your trading system. Think of it like a library catalog - instead of tracking books, it tracks:

- **Which models exist** (e.g., "alpha_baseline version 1.0.0")
- **Where they're stored** (file path or MLflow location)
- **Which model is currently active** (being used for live trading)
- **How well they performed** (backtesting metrics like Sharpe ratio, IC)
- **When they were deployed** (timestamps for auditing)

Without a model registry, you'd have models scattered across different directories with no way to know which one is "live" or how to roll back to a previous version if something goes wrong.

## Why It Matters

### Real-World Impact

**1. Model Version Control**
- You train a new model every week with updated data
- How do you track which version is in production?
- How do you roll back if the new model performs poorly?
- Model registry solves this by maintaining a clear history

**2. Hot Deployment (Zero Downtime)**
- Without registry: Stop service → Replace model file → Restart service (30-60s downtime)
- With registry: Update database record → Service polls and reloads (< 1s, no downtime)

**3. Audit Trail**
- Regulator asks: "What model were you using on January 15th?"
- Without registry: Manual file inspection, check deployment logs (error-prone)
- With registry: Single SQL query showing active model and deployment time

**4. A/B Testing**
- Run two models side-by-side (one active, one testing)
- Compare performance before switching
- Easy to promote "testing" → "active" or rollback

**5. Disaster Recovery**
- Production server crashes, need to rebuild
- Registry tells you exactly which model version to restore
- Performance metrics help verify the restored model is correct

### Consequences of Not Having a Registry

**Example Failure Scenario:**
```
8:00 AM: Deploy new model v2.0 (file: alpha_baseline.txt)
10:00 AM: Strategy starts losing money
10:30 AM: Realize new model has bug
10:31 AM: Panic - which model was running before?
10:35 AM: Find old model file (maybe?) in backup
10:45 AM: Deploy old model, but is it the right version?
11:00 AM: Lost $50K due to 90 minutes of downtime and confusion
```

**With Model Registry:**
```
8:00 AM: Deploy new model v2.0 (update registry: status='active')
10:00 AM: Strategy starts losing money
10:30 AM: Realize new model has bug
10:31 AM: Query registry: "SELECT * FROM model_registry WHERE version < 'v2.0' ORDER BY activated_at DESC LIMIT 1"
10:32 AM: Run activation function: activate_model('alpha_baseline', 'v1.0.0')
10:33 AM: Service auto-reloads v1.0.0 (hot reload, no restart)
10:34 AM: Back to normal operations
Result: Lost $2K (4 minutes), avoided disaster
```

## Common Pitfalls

### 1. Forgetting to Update Registry After Training

**Symptom:** You train a new model, save it to disk, but forget to register it. Service keeps using old model.

**Solution:**
```python
# BAD: Train and save, but don't register
trainer.train()
trainer.save_model("artifacts/models/alpha_baseline_v2.txt")
# Service has no idea new model exists!

# GOOD: Automatically register after successful training
trainer.train()
model_path = trainer.save_model("artifacts/models/alpha_baseline_v2.txt")

# Register in database
db.execute("""
    INSERT INTO model_registry (strategy_name, version, model_path, status, ...)
    VALUES ('alpha_baseline', 'v2.0.0', %s, 'inactive', ...)
""", (model_path,))

# Manually activate when ready
activate_model('alpha_baseline', 'v2.0.0')
```

### 2. Multiple Services Reading Same Registry Without Locks

**Symptom:** Two signal services both think they should use different model versions.

**Cause:** Race condition during model switch.

**Solution:** Use database transactions and status states:
```sql
-- Atomic activation (all or nothing)
BEGIN;
UPDATE model_registry SET status='inactive', deactivated_at=NOW()
WHERE strategy_name='alpha_baseline' AND status='active';

UPDATE model_registry SET status='active', activated_at=NOW()
WHERE strategy_name='alpha_baseline' AND version='v2.0.0';
COMMIT;
```

### 3. Not Validating Model Loads Successfully

**Symptom:** Registry points to corrupted or missing model file. Service crashes on startup.

**Solution:** Validate model loads before marking as active:
```python
def safe_activate_model(strategy: str, version: str):
    """Activate model only if it loads successfully."""
    # Get model metadata
    metadata = get_model_metadata(strategy, version)

    # Try loading model first
    try:
        model = lgb.Booster(model_file=metadata.model_path)
        # Validate model is usable
        _ = model.predict([[0.0] * 158])  # Test prediction
    except Exception as e:
        logger.error(f"Model failed to load: {e}")
        # Mark as 'failed' in registry
        mark_model_failed(strategy, version, str(e))
        raise

    # Only activate if load succeeded
    activate_model(strategy, version)
```

### 4. Stale Model Paths (File Moved or Deleted)

**Symptom:** Registry says model is at `/old/path/model.txt`, but file was moved to `/new/path/`.

**Solution:** Use absolute paths and validate on load:
```python
def load_model(metadata: ModelMetadata) -> lgb.Booster:
    model_path = Path(metadata.model_path).resolve()  # Absolute path

    if not model_path.exists():
        raise FileNotFoundError(
            f"Model file not found: {model_path}\n"
            f"Registry entry may be stale. Check if file was moved."
        )

    return lgb.Booster(model_file=str(model_path))
```

### 5. Forgetting to Include Performance Metrics

**Symptom:** You have 5 model versions but don't know which one performed best.

**Solution:** Always store backtest metrics when registering:
```python
# After backtesting
backtest_metrics = {
    "ic": 0.082,              # Information coefficient
    "sharpe": 1.45,           # Risk-adjusted return
    "max_drawdown": -0.12,    # Worst loss
    "win_rate": 0.55,         # Winning days
    "annualized_return": 0.18 # 18% annual return
}

db.execute("""
    INSERT INTO model_registry (..., performance_metrics)
    VALUES (..., %s)
""", (json.dumps(backtest_metrics),))
```

Now when choosing which model to activate, you can query:
```sql
SELECT version, performance_metrics->>'sharpe' as sharpe
FROM model_registry
WHERE strategy_name = 'alpha_baseline'
ORDER BY (performance_metrics->>'sharpe')::float DESC
LIMIT 5;
```

## Examples

### Example 1: Weekly Model Update Workflow

**Scenario:** Every Sunday, you train a new model with the latest data.

```python
# 1. Train new model
from strategies.alpha_baseline.train import train_baseline_model

trainer = train_baseline_model()
metrics = trainer.metrics  # {'valid_ic': 0.085, 'sharpe': 1.52, ...}

# 2. Save model with versioned filename
from datetime import datetime
version = datetime.now().strftime("v%Y%m%d")  # e.g., "v20250117"
model_path = f"artifacts/models/alpha_baseline_{version}.txt"
trainer.save_model(model_path)

# 3. Register in database
import psycopg2

with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO model_registry
            (strategy_name, version, model_path, status, performance_metrics, mlflow_run_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            'alpha_baseline',
            version,
            model_path,
            'inactive',  # Don't activate yet, test first
            json.dumps(metrics),
            trainer.mlflow_run_id
        ))
        conn.commit()

# 4. Test new model in paper trading (status='testing')
# ... run for a day or two ...

# 5. If tests pass, activate
with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        # Deactivate old
        cur.execute("""
            UPDATE model_registry
            SET status='inactive', deactivated_at=NOW()
            WHERE strategy_name='alpha_baseline' AND status='active'
        """)

        # Activate new
        cur.execute("""
            UPDATE model_registry
            SET status='active', activated_at=NOW()
            WHERE strategy_name='alpha_baseline' AND version=%s
        """, (version,))

        conn.commit()

# 6. Signal service auto-detects change and reloads (hot reload)
# Within 5 minutes (polling interval), new model is live
```

**Timeline:**
- Sunday 8:00 PM: Train and register new model (status='inactive')
- Sunday 8:30 PM: Manual testing (load model, generate test signals)
- Monday 9:00 AM: Activate for paper trading (status='testing')
- Tuesday 9:00 AM: Review results, decide to go live
- Tuesday 9:05 AM: Update status to 'active'
- Tuesday 9:10 AM: Signal service polls registry, reloads new model
- Tuesday 9:11 AM: New model generating live signals

### Example 2: Emergency Rollback

**Scenario:** New model is losing money, need to rollback immediately.

```python
# Find currently active model
with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT version, activated_at
            FROM model_registry
            WHERE strategy_name='alpha_baseline' AND status='active'
        """)
        current = cur.fetchone()
        print(f"Current: {current}")  # ('v20250117', '2025-01-17 09:05:00')

        # Find previous active model
        cur.execute("""
            SELECT version, deactivated_at, performance_metrics
            FROM model_registry
            WHERE strategy_name='alpha_baseline'
              AND status='inactive'
              AND deactivated_at IS NOT NULL
            ORDER BY deactivated_at DESC
            LIMIT 1
        """)
        previous = cur.fetchone()
        print(f"Previous: {previous}")  # ('v20250110', '2025-01-17 09:05:00', {...})

# Rollback to previous version
from apps.signal_service.model_registry import activate_model

activate_model('alpha_baseline', 'v20250110')

# Or use SQL directly
with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            -- Deactivate current (bad) model
            UPDATE model_registry
            SET status='failed', deactivated_at=NOW()
            WHERE strategy_name='alpha_baseline' AND version='v20250117';

            -- Reactivate previous (good) model
            UPDATE model_registry
            SET status='active', activated_at=NOW()
            WHERE strategy_name='alpha_baseline' AND version='v20250110';
        """)
        conn.commit()

# Trigger immediate reload (don't wait for polling)
import httpx

response = httpx.post("http://localhost:8001/model/reload")
print(response.json())  # {'reloaded': True, 'version': 'v20250110'}
```

**Result:** Rollback completed in ~30 seconds (query + reload), no service restart needed.

### Example 3: Comparing Model Versions

**Scenario:** You have 5 model versions and want to pick the best one.

```sql
-- Show all models sorted by Sharpe ratio
SELECT
    version,
    status,
    performance_metrics->>'sharpe' as sharpe,
    performance_metrics->>'ic' as ic,
    performance_metrics->>'max_drawdown' as max_dd,
    activated_at,
    deactivated_at
FROM model_registry
WHERE strategy_name = 'alpha_baseline'
ORDER BY (performance_metrics->>'sharpe')::float DESC;

/*
 version    | status   | sharpe | ic    | max_dd | activated_at        | deactivated_at
------------+----------+--------+-------+--------+---------------------+--------------------
 v20250117  | active   | 1.52   | 0.085 | -0.11  | 2025-01-17 09:05:00 | NULL
 v20250110  | inactive | 1.45   | 0.082 | -0.12  | 2025-01-10 09:00:00 | 2025-01-17 09:05:00
 v20250103  | inactive | 1.38   | 0.078 | -0.14  | 2025-01-03 09:00:00 | 2025-01-10 09:00:00
 v20241227  | inactive | 1.22   | 0.072 | -0.15  | 2024-12-27 09:00:00 | 2025-01-03 09:00:00
 v20241220  | failed   | 0.85   | 0.045 | -0.22  | NULL                | NULL
*/
```

**Insight:** v20250117 (current) has best Sharpe (1.52) and IC (0.085). v20241220 was marked 'failed' (never activated, failed validation).

## Further Reading

### Academic & Industry References
- [MLOps: Model Registry Best Practices (Google)](https://cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning)
- [MLflow Model Registry Documentation](https://mlflow.org/docs/latest/model-registry.html)
- [Uber Michelangelo: ML Platform at Scale](https://www.uber.com/blog/michelangelo-machine-learning-platform/)

### Related Documentation
- `/docs/ADRs/0004-signal-service-architecture.md` - Why we chose database registry over MLflow-only
- `/docs/IMPLEMENTATION_GUIDES/t3-signal-service.md` - Step-by-step implementation
- `/docs/CONCEPTS/hot-reload.md` - How hot reload works with model registry
- `/docs/CONCEPTS/feature-parity.md` - Ensuring research models match production

### Trading-Specific Considerations
- [Quantitative Trading Model Deployment (QuantConnect)](https://www.quantconnect.com/docs/)
- [Model Risk Management (Federal Reserve)](https://www.federalreserve.gov/supervisionreg/topics/model-risk-management.htm)
- [Algorithmic Trading Compliance (SEC)](https://www.sec.gov/rules/concept/2010/34-61358.pdf)

## Summary

A model registry is essential infrastructure for production ML systems. It provides:

- ✅ **Version control** - Track all model versions
- ✅ **Audit trail** - Know which model was live when
- ✅ **Hot deployment** - Update models without downtime
- ✅ **Rollback capability** - Revert to previous version in seconds
- ✅ **Performance tracking** - Compare models objectively
- ✅ **Disaster recovery** - Know exactly which model to restore

**Cost of not having it:** Manual file management, downtime during deploys, no audit trail, difficult rollbacks, production incidents.

**Cost of building it:** 2-4 hours to implement database schema and client library.

**ROI:** Pays for itself the first time you need to rollback a bad model.
