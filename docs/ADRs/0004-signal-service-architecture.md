# ADR-0004: Signal Service Architecture

**Status:** Accepted
**Date:** 2025-10-17
**Deciders:** Trading Platform Team
**Tags:** microservices, fastapi, model-registry, signals, production-ml

## Context and Problem Statement

After implementing the T2 Baseline Strategy (ADR-0003) with Qlib and MLflow, we have trained models that can predict next-day returns. Now we need a production service that:

1. **Loads trained models** from MLflow or file storage
2. **Generates real-time signals** (target portfolio weights) for tradable symbols
3. **Maintains feature parity** with research code (zero train-serve skew)
4. **Supports hot reloading** when new model versions are deployed
5. **Provides API interface** for downstream services (Risk Manager, Execution Gateway)
6. **Tracks model metadata** (version, performance, deployment status)

**Key Requirements:**
- FastAPI microservice for signal generation
- Model registry (database table) for version control
- Feature parity validation between research and production
- Sub-second latency for signal generation
- Support for multiple strategies (extensible architecture)
- Graceful degradation if model fails to load

## Decision Drivers

1. **Feature Parity** - Research features must exactly match production (critical for model performance)
2. **Low Latency** - Signal generation must complete in < 100ms for timely trading
3. **Hot Reload** - Deploy new models without service restart (zero downtime)
4. **Observability** - Track which model version generated which signals
5. **Extensibility** - Support multiple strategies (alpha_baseline, alpha_v2, etc.)
6. **Reliability** - Graceful degradation, fallback to previous model if new one fails
7. **Simplicity** - Avoid over-engineering; start simple, iterate

## Considered Options

### Option 1: Direct MLflow Model Serving
Use MLflow's built-in model serving with `mlflow models serve`.

**Pros:**
- Zero custom code for model loading
- Built-in model registry integration
- REST API out of the box
- Model versioning handled by MLflow

**Cons:**
- No control over feature computation
- Can't enforce feature parity with research
- Limited customization (hard to add business logic)
- Not integrated with our microservices
- Separate deployment from our stack

### Option 2: Custom FastAPI Service with Model Registry ✅ (CHOSEN)
Build FastAPI service that loads models from registry and generates signals.

**Pros:**
- Full control over feature computation (feature parity)
- Custom API endpoints tailored to our needs
- Integrated with microservices architecture
- Can add business logic (position limits, symbol filtering)
- Hot reload with polling mechanism
- Model registry in our database (single source of truth)
- Easy to test and extend

**Cons:**
- More code to write and maintain
- Need to implement model loading logic
- Need to implement registry polling
- Responsible for error handling

### Option 3: Serverless Lambda + S3 Models
Deploy signal generation as AWS Lambda, load models from S3.

**Pros:**
- Scales automatically
- Pay per request (cost-effective)
- No infrastructure to manage

**Cons:**
- Cold start latency (100-500ms)
- Limited to 15-minute execution
- Complex local testing
- Not aligned with microservices architecture
- Vendor lock-in

## Decision Outcome

**Chosen option:** Option 2 - Custom FastAPI Service with Model Registry

**Rationale:**
- Full control ensures feature parity (most critical requirement)
- Integrated microservices architecture
- Low latency (no cold starts)
- Simple to test locally
- Extensible for future strategies
- Model registry in Postgres (already using)

## Implementation Details

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Signal Service                           │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                     FastAPI App                           │  │
│  │  GET /health, GET /signals, POST /reload, GET /model/info│  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│  ┌───────────────────────────┴───────────────────────────────┐  │
│  │              Signal Generator                              │  │
│  │  - Load model from registry                                │  │
│  │  - Fetch latest market data (T1DataProvider)               │  │
│  │  - Compute Alpha158 features (same as research)            │  │
│  │  - Generate predictions → target weights                   │  │
│  └────────────────────┬───────────────────────────────────────┘  │
│                       │                                           │
│  ┌────────────────────┴──────────────────────────────────────┐  │
│  │              Model Registry                                │  │
│  │  - Poll database for active model                          │  │
│  │  - Load model from MLflow or file path                     │  │
│  │  - Cache model in memory                                   │  │
│  │  - Hot reload on version change                            │  │
│  └────────────────────┬──────────────────────────────────────┘  │
│                       │                                           │
└───────────────────────┼───────────────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
    ┌───▼────┐                   ┌──────▼────────┐
    │ MLflow │                   │   Postgres    │
    │Models  │                   │model_registry │
    └────────┘                   └───────────────┘
```

### Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Web Framework** | FastAPI | Async, fast, automatic API docs, type hints |
| **Model Registry** | Postgres Table | Single source of truth, ACID guarantees |
| **Model Storage** | MLflow Artifacts | Already using MLflow, version control built-in |
| **Data Provider** | T1DataProvider | Reuse existing T1 ETL data |
| **Features** | Alpha158 (from T2) | Feature parity with research code |
| **Model Format** | LightGBM Native | Fast loading, small file size |
| **Validation** | Pydantic | Request/response validation, type safety |
| **Testing** | Pytest + FastAPI TestClient | Standard testing stack |

### Database Schema

**Model Registry Table:**
```sql
CREATE TABLE model_registry (
    id SERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,            -- e.g., "alpha_baseline"
    version TEXT NOT NULL,                  -- e.g., "v1.0.0", "20250117-143022"
    mlflow_run_id TEXT,                     -- MLflow run ID for traceability
    mlflow_experiment_id TEXT,              -- MLflow experiment ID
    model_path TEXT NOT NULL,               -- File path or MLflow URI
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'testing', 'failed')),
    performance_metrics JSONB,              -- {"ic": 0.08, "sharpe": 1.5, ...}
    config JSONB,                           -- Model config (hyperparameters, etc.)
    created_at TIMESTAMP DEFAULT NOW(),
    activated_at TIMESTAMP,                 -- When this model was activated
    deactivated_at TIMESTAMP,               -- When this model was deactivated
    created_by TEXT DEFAULT 'system',
    notes TEXT,                             -- Deployment notes
    UNIQUE(strategy_name, version)
);

-- Index for fast lookups
CREATE INDEX idx_model_registry_active
    ON model_registry(strategy_name, status)
    WHERE status = 'active';
```

**Example Records:**
```sql
INSERT INTO model_registry (
    strategy_name, version, mlflow_run_id, model_path,
    status, performance_metrics
) VALUES (
    'alpha_baseline',
    'v1.0.0',
    'abc123def456',
    'artifacts/models/alpha_baseline_v1.txt',
    'active',
    '{"ic": 0.082, "sharpe": 1.45, "max_drawdown": -0.12}'
);
```

### API Specification

**OpenAPI Schema:**
```yaml
openapi: 3.0.3
info:
  title: Signal Service API
  version: 0.1.0
  description: Generates trading signals from trained ML models

paths:
  /health:
    get:
      summary: Health check
      responses:
        '200':
          description: Service is healthy
          content:
            application/json:
              schema:
                type: object
                properties:
                  status: { type: string, example: "healthy" }
                  model_loaded: { type: boolean }
                  model_version: { type: string }

  /signals:
    get:
      summary: Get current target portfolio weights
      parameters:
        - name: symbols
          in: query
          required: false
          schema:
            type: array
            items: { type: string }
          description: Filter signals for specific symbols (default: all tradable)
        - name: strategy
          in: query
          required: false
          schema:
            type: string
            default: alpha_baseline
      responses:
        '200':
          description: Target weights for each symbol
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SignalResponse'
        '503':
          description: Model not loaded or failed

  /model/info:
    get:
      summary: Get current model metadata
      responses:
        '200':
          description: Model information
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ModelInfo'

  /model/reload:
    post:
      summary: Force model reload from registry
      responses:
        '200':
          description: Model reloaded successfully
        '500':
          description: Failed to reload model

components:
  schemas:
    SignalResponse:
      type: object
      properties:
        signals:
          type: array
          items:
            type: object
            properties:
              symbol: { type: string }
              target_weight: { type: number }
              predicted_return: { type: number }
              rank: { type: integer }
        metadata:
          type: object
          properties:
            strategy: { type: string }
            model_version: { type: string }
            generated_at: { type: string, format: date-time }
            data_as_of: { type: string, format: date-time }
            num_symbols: { type: integer }

    ModelInfo:
      type: object
      properties:
        strategy_name: { type: string }
        version: { type: string }
        mlflow_run_id: { type: string }
        status: { type: string }
        performance_metrics: { type: object }
        loaded_at: { type: string, format: date-time }
        last_prediction_at: { type: string, format: date-time }
```

### Directory Structure

```
apps/signal_service/
├── __init__.py
├── main.py                    # FastAPI app, endpoints
├── signal_generator.py        # Core signal generation logic
├── model_registry.py          # Model registry client (DB + file loading)
├── config.py                  # Settings (Pydantic BaseSettings)
├── models.py                  # Pydantic models for API
├── dependencies.py            # FastAPI dependencies (DB, model manager)
└── tests/
    ├── __init__.py
    ├── test_api.py            # API endpoint tests
    ├── test_signal_generator.py
    ├── test_model_registry.py
    └── conftest.py            # Pytest fixtures
```

### Key Components

#### 1. Model Registry Client

```python
# apps/signal_service/model_registry.py

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import lightgbm as lgb
import psycopg2

@dataclass
class ModelMetadata:
    id: int
    strategy_name: str
    version: str
    mlflow_run_id: str
    model_path: str
    status: str
    performance_metrics: dict
    activated_at: str

class ModelRegistry:
    """Manages model loading from registry."""

    def __init__(self, db_conn_string: str):
        self.db_conn_string = db_conn_string
        self._current_model: Optional[lgb.Booster] = None
        self._current_metadata: Optional[ModelMetadata] = None

    def get_active_model(self, strategy: str = "alpha_baseline") -> ModelMetadata:
        """Fetch active model metadata from database."""
        with psycopg2.connect(self.db_conn_string) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, strategy_name, version, mlflow_run_id, model_path,
                           status, performance_metrics, activated_at
                    FROM model_registry
                    WHERE strategy_name = %s AND status = 'active'
                    ORDER BY activated_at DESC
                    LIMIT 1
                """, (strategy,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"No active model for strategy: {strategy}")
                return ModelMetadata(*row)

    def load_model(self, metadata: ModelMetadata) -> lgb.Booster:
        """Load LightGBM model from file."""
        model_path = Path(metadata.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        return lgb.Booster(model_file=str(model_path))

    def reload_if_changed(self, strategy: str = "alpha_baseline") -> bool:
        """Check if model changed and reload if needed. Returns True if reloaded."""
        new_metadata = self.get_active_model(strategy)

        # Check if version changed
        if (self._current_metadata is None or
            new_metadata.version != self._current_metadata.version):
            self._current_model = self.load_model(new_metadata)
            self._current_metadata = new_metadata
            return True
        return False

    @property
    def current_model(self) -> Optional[lgb.Booster]:
        return self._current_model

    @property
    def current_metadata(self) -> Optional[ModelMetadata]:
        return self._current_metadata
```

#### 2. Signal Generator

```python
# apps/signal_service/signal_generator.py

from datetime import datetime
from typing import List
import pandas as pd
import numpy as np

from strategies.alpha_baseline.data_loader import T1DataProvider
from strategies.alpha_baseline.features import get_alpha158_features
from .model_registry import ModelRegistry

class SignalGenerator:
    """Generates trading signals from model predictions."""

    def __init__(
        self,
        model_registry: ModelRegistry,
        data_provider: T1DataProvider,
        top_n: int = 3,
        bottom_n: int = 3,
    ):
        self.model_registry = model_registry
        self.data_provider = data_provider
        self.top_n = top_n
        self.bottom_n = bottom_n

    def generate_signals(
        self,
        symbols: List[str],
        as_of_date: datetime
    ) -> pd.DataFrame:
        """
        Generate target portfolio weights for given symbols.

        Returns:
            DataFrame with columns: symbol, predicted_return, rank, target_weight
        """
        # 1. Check model is loaded
        if self.model_registry.current_model is None:
            raise RuntimeError("Model not loaded")

        # 2. Fetch latest features (using same code as research)
        features = get_alpha158_features(
            symbols=symbols,
            start_date=as_of_date,
            end_date=as_of_date,
            data_dir=self.data_provider.data_dir,
        )

        if features.empty:
            raise ValueError(f"No features for date: {as_of_date}")

        # 3. Generate predictions
        predictions = self.model_registry.current_model.predict(features.values)

        # 4. Create results DataFrame
        results = pd.DataFrame({
            'symbol': features.index.get_level_values('instrument'),
            'predicted_return': predictions,
        })

        # 5. Rank symbols by predicted return
        results['rank'] = results['predicted_return'].rank(ascending=False)

        # 6. Compute target weights (top-N long, bottom-N short)
        results['target_weight'] = 0.0

        # Long positions (top N)
        top_symbols = results.nsmallest(self.top_n, 'rank')
        results.loc[top_symbols.index, 'target_weight'] = 1.0 / self.top_n

        # Short positions (bottom N)
        bottom_symbols = results.nlargest(self.bottom_n, 'rank')
        results.loc[bottom_symbols.index, 'target_weight'] = -1.0 / self.bottom_n

        return results.sort_values('rank')
```

#### 3. FastAPI Application

```python
# apps/signal_service/main.py

from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel

from .signal_generator import SignalGenerator
from .model_registry import ModelRegistry
from .config import Settings

app = FastAPI(title="Signal Service", version="0.1.0")
settings = Settings()

# Global state (initialized on startup)
model_registry: Optional[ModelRegistry] = None
signal_generator: Optional[SignalGenerator] = None

@app.on_event("startup")
async def startup_event():
    """Initialize model registry and load first model."""
    global model_registry, signal_generator

    model_registry = ModelRegistry(settings.database_url)
    model_registry.reload_if_changed(strategy=settings.default_strategy)

    signal_generator = SignalGenerator(
        model_registry=model_registry,
        data_provider=T1DataProvider(settings.data_dir),
        top_n=settings.top_n,
        bottom_n=settings.bottom_n,
    )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy" if model_registry.current_model else "degraded",
        "model_loaded": model_registry.current_model is not None,
        "model_version": model_registry.current_metadata.version
            if model_registry.current_metadata else None,
    }

@app.get("/signals")
async def get_signals(
    symbols: Optional[List[str]] = None,
    strategy: str = "alpha_baseline",
    as_of_date: Optional[datetime] = None,
):
    """Generate trading signals for given symbols."""
    if model_registry.current_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # Default to latest available date
    if as_of_date is None:
        as_of_date = datetime.now()

    # Default to all tradable symbols
    if symbols is None:
        symbols = settings.tradable_symbols

    # Generate signals
    try:
        signals_df = signal_generator.generate_signals(symbols, as_of_date)

        return {
            "signals": signals_df.to_dict(orient="records"),
            "metadata": {
                "strategy": strategy,
                "model_version": model_registry.current_metadata.version,
                "generated_at": datetime.now().isoformat(),
                "data_as_of": as_of_date.isoformat(),
                "num_symbols": len(signals_df),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/model/info")
async def get_model_info():
    """Get current model metadata."""
    if model_registry.current_metadata is None:
        raise HTTPException(status_code=404, detail="No model loaded")

    metadata = model_registry.current_metadata
    return {
        "strategy_name": metadata.strategy_name,
        "version": metadata.version,
        "mlflow_run_id": metadata.mlflow_run_id,
        "status": metadata.status,
        "performance_metrics": metadata.performance_metrics,
        "activated_at": metadata.activated_at,
    }

@app.post("/model/reload")
async def reload_model(background_tasks: BackgroundTasks):
    """Force model reload from registry."""
    try:
        reloaded = model_registry.reload_if_changed(
            strategy=settings.default_strategy
        )
        return {
            "reloaded": reloaded,
            "version": model_registry.current_metadata.version,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Feature Parity Validation

Critical to ensure production features match research:

```python
# apps/signal_service/tests/test_feature_parity.py

def test_feature_parity_with_research():
    """Validate that production features match research features."""
    from strategies.alpha_baseline.features import get_alpha158_features
    from apps.signal_service.signal_generator import SignalGenerator

    # Same input data
    symbols = ["AAPL", "MSFT"]
    date = "2024-01-15"

    # Generate features using research code
    research_features = get_alpha158_features(
        symbols=symbols,
        start_date=date,
        end_date=date,
        data_dir=Path("data/adjusted"),
    )

    # Generate features using production code
    signal_gen = SignalGenerator(...)
    production_features = signal_gen._get_features(symbols, date)

    # Validate exact match
    pd.testing.assert_frame_equal(research_features, production_features)
```

### Hot Reload Mechanism

**Polling Approach:**
```python
# Background task polls registry every N minutes
import asyncio

async def model_reload_task():
    """Background task to poll registry and reload model if changed."""
    while True:
        try:
            reloaded = model_registry.reload_if_changed()
            if reloaded:
                logger.info(f"Model reloaded: {model_registry.current_metadata.version}")
        except Exception as e:
            logger.error(f"Failed to reload model: {e}")

        await asyncio.sleep(settings.model_reload_interval_seconds)

@app.on_event("startup")
async def startup_event():
    # ... existing startup code ...
    asyncio.create_task(model_reload_task())
```

**Manual Trigger:**
```bash
# Trigger reload via API
curl -X POST http://localhost:8001/model/reload
```

### Configuration

```python
# apps/signal_service/config.py

from pathlib import Path
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    # Service
    host: str = "0.0.0.0"
    port: int = 8001

    # Database
    database_url: str = "postgresql://user:pass@localhost:5432/trading_platform"

    # Data
    data_dir: Path = Path("data/adjusted")

    # Strategy
    default_strategy: str = "alpha_baseline"
    tradable_symbols: List[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

    # Portfolio
    top_n: int = 3
    bottom_n: int = 3

    # Model reload
    model_reload_interval_seconds: int = 300  # 5 minutes

    class Config:
        env_file = ".env"
```

## Consequences

### Positive

1. **Feature Parity Guaranteed** - Same code for research and production eliminates train-serve skew
2. **Low Latency** - In-memory model, cached features, sub-100ms response time
3. **Hot Reload** - Deploy new models without downtime (zero-downtime deployments)
4. **Extensible** - Easy to add new strategies (just new registry entries)
5. **Observability** - Model version tracked with every signal
6. **Testable** - FastAPI TestClient, easy to write integration tests
7. **Integrated** - Fits cleanly into microservices architecture

### Negative

1. **Stateful Service** - Model cached in memory (not ideal for serverless)
2. **Database Dependency** - Service won't start without Postgres
3. **Manual Registry Updates** - Need scripts to register new models
4. **Single Strategy per Instance** - Can't serve multiple strategies simultaneously (mitigated by running multiple instances)
5. **Memory Usage** - Large models consume memory (mitigated by LightGBM's efficiency)

### Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Feature drift** | Critical | Medium | Automated tests validate feature parity |
| **Model load failure** | High | Low | Graceful degradation, keep previous model |
| **Database downtime** | High | Low | Cache registry entries, poll retries |
| **Stale data** | High | Medium | Validate data freshness before prediction |
| **Memory leak** | Medium | Low | Monitor memory, restart on leak detection |
| **Slow feature computation** | Medium | Medium | Profile, optimize, add caching |

## Success Metrics

**Performance:**
- ✅ Signal generation latency < 100ms (p95)
- ✅ Model reload time < 1 second
- ✅ Feature computation < 50ms (for 10 symbols)
- ✅ API response time < 100ms (p95)

**Reliability:**
- ✅ 99.9% uptime
- ✅ Zero failed model loads (graceful degradation)
- ✅ 100% feature parity with research

**Testing:**
- ✅ 80%+ code coverage
- ✅ Integration tests for all endpoints
- ✅ Feature parity validation tests

## Implementation Timeline

**Total: 2-3 days (16-24 hours)**

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| Phase 1: Database Schema | 2 hours | Model registry table, migrations |
| Phase 2: Model Registry Client | 4 hours | DB client, model loading, tests |
| Phase 3: Signal Generator | 4 hours | Feature generation, prediction logic |
| Phase 4: FastAPI App | 4 hours | Endpoints, dependencies, config |
| Phase 5: Hot Reload | 2 hours | Polling mechanism, manual trigger |
| Phase 6: Tests | 4 hours | Unit tests, integration tests, feature parity |
| Phase 7: Documentation | 2 hours | API docs, deployment guide |

## Alternatives Considered (Detailed)

### Alternative 1: Event-Driven Model Updates
Use pub/sub (Redis) to notify service of model changes.

**Pros:**
- Real-time updates (no polling delay)
- Lower database load

**Cons:**
- More complex (Redis dependency)
- Harder to debug
- Overkill for 5-minute reload interval

**Verdict:** Rejected - polling is simpler and sufficient

### Alternative 2: Model Registry in MLflow Only
Skip database, use MLflow registry directly.

**Pros:**
- Fewer components
- MLflow has built-in registry UI

**Cons:**
- Tight coupling to MLflow
- Harder to query (no SQL)
- No custom metadata fields
- Slower queries

**Verdict:** Rejected - database gives more control

### Alternative 3: Sidecar Container for Models
Deploy models in sidecar container, share via volume.

**Pros:**
- Clean separation
- Easy to swap models

**Cons:**
- Kubernetes-specific
- Complicates local dev
- Not needed for MVP

**Verdict:** Deferred - consider for production deployment

## Related Decisions

- **ADR-0001:** Data Pipeline Architecture (T1 data provider)
- **ADR-0003:** Baseline Strategy with Qlib and MLflow (model training)
- **Future ADR:** Risk Manager Architecture (consumes signals)
- **Future ADR:** Execution Gateway Architecture (consumes risk-adjusted signals)

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [MLflow Model Registry](https://mlflow.org/docs/latest/model-registry.html)
- [LightGBM Python API](https://lightgbm.readthedocs.io/en/latest/Python-API.html)
- [Microservices Patterns](https://microservices.io/patterns/index.html)

## Approval

**Approved by:** Trading Platform Team
**Date:** 2025-10-17
**Reviewers:** All team members

---

**Change Log:**
- 2025-10-17: Initial version (ADR-0004)
