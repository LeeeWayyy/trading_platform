# Model Registry

## Plain English Explanation

A model registry is a database that keeps track of all trained machine learning models in your trading system. Think of it like a library catalog - instead of tracking books, it tracks:

- **Which models exist** (e.g., "risk_model version v1.0.0")
- **Where they're stored** (artifact path with SHA-256 checksums)
- **Which model is currently active** (status: `staged`, `production`, `archived`, `failed`)
- **How well they performed** (metrics: IC, Sharpe, max drawdown)
- **When they were deployed** (timestamps for auditing)
- **Which data they were trained on** (dataset_version_ids linked to P4T1)

Without a model registry, you'd have models scattered across different directories with no way to know which one is "live" or how to roll back to a previous version if something goes wrong.

## Our Implementation (T2.8)

Our model registry uses **DuckDB** as an embedded catalog with a file-based artifact store:

```
data/models/
├── registry.db           # DuckDB catalog (query index)
├── manifest.json         # Registry manifest (DR/discoverability)
├── artifacts/
│   ├── risk_model/
│   │   ├── v1.0.0/
│   │   │   ├── model.pkl
│   │   │   ├── metadata.json   # AUTHORITATIVE source
│   │   │   └── checksum.sha256
│   │   └── v1.1.0/
│   ├── alpha_weights/
│   └── factor_definitions/
└── backups/              # Daily backups
```

### Key Features

| Feature | Implementation |
|---------|----------------|
| Storage | DuckDB (embedded, ACID-compliant) |
| Artifacts | Pickle/joblib with SHA-256 checksums |
| Versioning | Semantic versions (immutable) |
| Provenance | Linked to P4T1 dataset versions |
| Hot-reload | ProductionModelLoader with polling |
| API | FastAPI with JWT authentication |

### Model Types

```python
class ModelType(str, Enum):
    risk_model = "risk_model"           # BarraRiskModel
    alpha_weights = "alpha_weights"     # Alpha combination weights
    factor_definitions = "factor_definitions"  # Factor configs
    feature_transforms = "feature_transforms"  # Normalization params
```

### Model Status Lifecycle

```
staged → production → archived
           ↓
         failed (on validation failure)
```

- **staged**: Newly registered, awaiting validation/promotion
- **production**: Currently active in signal_service
- **archived**: Demoted, kept for audit trail
- **failed**: Validation failed, cannot be promoted

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

## Promotion Gates

Before a model can be promoted to production, it must pass these gates:

| Gate | Threshold | Purpose |
|------|-----------|---------|
| Information Coefficient (IC) | > 0.02 | Minimum predictive power |
| Sharpe Ratio | > 0.5 | Risk-adjusted performance |
| Paper Trading Period | >= 24 hours | Live validation |

```python
# Promotion gates are enforced automatically
from libs.models import ModelRegistry

registry = ModelRegistry(Path("data/models"))

# This will raise PromotionGateError if gates not met
result = registry.promote_model("risk_model", "v1.0.0")
```

## Version Compatibility

Models are trained on specific dataset versions. When loading a model, the registry checks version compatibility:

**STRICT_VERSION_MODE=true (production default):**
- ANY dataset version drift → BLOCK load
- Missing dataset → BLOCK always

**STRICT_VERSION_MODE=false (development):**
- ANY dataset version drift → WARN, allow load
- Missing dataset → BLOCK always

```python
from libs.models import VersionCompatibilityChecker

checker = VersionCompatibilityChecker(strict_mode=True)
result = checker.check_compatibility(
    model_versions={"crsp": "v1.2.3", "compustat": "v1.0.1"},
    current_versions={"crsp": "v1.2.4", "compustat": "v1.0.1"}
)
# result.compatible = False (crsp version drift)
# result.level = "drift"
# result.warnings = ["crsp: model trained on v1.2.3, current is v1.2.4"]
```

## DiskExpressionCache

Computed factors are cached with a 5-component key for PIT safety:

```
{factor_name}:{as_of_date}:{dataset_version_id}:{snapshot_id}:{config_hash}
```

This ensures:
- **Factor identity** - Different factors don't collide
- **Date safety** - Point-in-time correctness
- **Dataset safety** - Invalidates on dataset updates
- **Snapshot safety** - Invalidates on snapshot changes
- **Config safety** - Invalidates on config changes

```python
from libs.factors.cache import DiskExpressionCache

cache = DiskExpressionCache(Path("data/cache"), ttl_days=7)

df, was_cached = cache.get_or_compute(
    factor_name="momentum_12m",
    as_of_date=date(2024, 1, 15),
    snapshot_id="snap_abc123",
    version_ids={"crsp": "v1.2.3"},
    config_hash="cfg_def456",
    compute_fn=compute_momentum
)
```

## Common Pitfalls

### 1. Forgetting to Register After Training

**Symptom:** You train a new model, save it to disk, but forget to register it.

**Solution:**
```python
from libs.models import (
    ModelRegistry,
    ModelMetadata,
    ModelType,
    capture_environment,
    compute_config_hash,
    generate_model_id,
)
from datetime import datetime, UTC

registry = ModelRegistry(Path("data/models"))

# Create metadata with full provenance
env = capture_environment(created_by="training_pipeline")
config = {"learning_rate": 0.01, "epochs": 100}

metadata = ModelMetadata(
    model_id=generate_model_id(),
    model_type=ModelType.risk_model,
    version="v1.0.0",
    created_at=datetime.now(UTC),
    dataset_version_ids={"crsp": "v1.2.3", "compustat": "v1.0.1"},
    snapshot_id="snap_20240101",
    factor_list=["momentum", "value", "size"],
    parameters={
        "factor_list": ["momentum", "value", "size"],
        "halflife_days": 60,
        "shrinkage_intensity": 0.5,
    },
    checksum_sha256="",  # Computed during registration
    metrics={"ic": 0.05, "sharpe": 1.2},
    env=env,
    config=config,
    config_hash=compute_config_hash(config),
)

# Register the model
model_id = registry.register_model(risk_model, metadata)
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
import psycopg

with psycopg.connect(DATABASE_URL) as conn:
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
with psycopg.connect(DATABASE_URL) as conn:
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
with psycopg.connect(DATABASE_URL) as conn:
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
with psycopg.connect(DATABASE_URL) as conn:
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

## CLI Commands

```bash
# Register a new model
python scripts/model_cli.py register risk_model path/to/model.pkl --version v1.0.0

# List models by status
python scripts/model_cli.py list risk_model --status staged

# Promote to production (enforces gates)
python scripts/model_cli.py promote risk_model v1.0.0

# Rollback to previous version
python scripts/model_cli.py rollback risk_model

# Validate model integrity
python scripts/model_cli.py validate risk_model v1.0.0

# Run garbage collection
python scripts/model_cli.py gc --dry-run
```

## API Endpoints

| Endpoint | Method | Scope | Description |
|----------|--------|-------|-------------|
| `/api/v1/models/{type}/current` | GET | model:read | Current production version |
| `/api/v1/models/{type}/{version}` | GET | model:read | Full metadata |
| `/api/v1/models/{type}/{version}/validate` | POST | model:write | Validate artifact |
| `/api/v1/models/{type}` | GET | model:read | List all versions |

## Further Reading

### Internal Documentation
- [ADR-0023-model-deployment.md](../ADRs/ADR-0023-model-deployment.md) - Architecture decisions
- [model-registry-dr.md](../RUNBOOKS/model-registry-dr.md) - Disaster recovery procedures
- [hot-reload.md](./hot-reload.md) - How hot reload works with model registry
- [feature-parity.md](./feature-parity.md) - Ensuring research models match production

### Academic & Industry References
- [MLOps: Model Registry Best Practices (Google)](https://cloud.google.com/architecture/mlops-continuous-delivery-and-automation-pipelines-in-machine-learning)
- [MLflow Model Registry Documentation](https://mlflow.org/docs/latest/model-registry.html)
- [Uber Michelangelo: ML Platform at Scale](https://www.uber.com/blog/michelangelo-machine-learning-platform/)

### Trading-Specific Considerations
- [Quantitative Trading Model Deployment (QuantConnect)](https://www.quantconnect.com/docs/)
- [Model Risk Management (Federal Reserve)](https://www.federalreserve.gov/supervisionreg/topics/model-risk-management.htm)
- [Algorithmic Trading Compliance (SEC)](https://www.sec.gov/rules/concept/2010/34-61358.pdf)

## Summary

Our T2.8 model registry provides production-ready ML model management:

| Capability | Implementation |
|------------|----------------|
| **Version control** | DuckDB with immutable semantic versions |
| **Audit trail** | promotion_history table with timestamps |
| **Hot deployment** | ProductionModelLoader with 60s polling |
| **Rollback capability** | Single CLI command, <1 min recovery |
| **Performance tracking** | IC, Sharpe, metrics stored with each version |
| **Disaster recovery** | manifest.json + daily backups |
| **Data provenance** | Linked to P4T1 dataset versions |
| **Promotion gates** | IC > 0.02, Sharpe > 0.5, 24h paper trade |
| **Cache safety** | DiskExpressionCache with 5-component keys |

**Key files:**
- `libs/models/` - Core registry implementation
- `libs/factors/cache.py` - DiskExpressionCache
- `apps/model_registry/` - FastAPI endpoints
- `scripts/model_cli.py` - CLI tool

**See also:**
- [ADR-0023](../ADRs/ADR-0023-model-deployment.md) - Architecture decisions
- [DR Runbook](../RUNBOOKS/model-registry-dr.md) - Recovery procedures
