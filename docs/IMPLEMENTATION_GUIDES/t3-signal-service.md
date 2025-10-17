# T3 Implementation Guide: Signal Service with Model Registry

**Status:** Ready for Implementation
**Estimated Time:** 2-3 days (16-24 hours)
**Dependencies:** T1 (Data ETL), T2 (Baseline Strategy)
**Related ADR:** ADR-0004 (Signal Service Architecture)

---

## Overview

This guide walks through implementing T3: Signal Service, a FastAPI microservice that:
- Loads trained ML models from a database registry
- Generates real-time trading signals (target portfolio weights)
- Maintains feature parity with research code
- Supports hot reloading of new model versions

**Key Deliverables:**
1. ✅ Postgres model registry table
2. ✅ Model registry client (Python)
3. ✅ Signal generator with feature parity
4. ✅ FastAPI service with REST API
5. ✅ Hot reload mechanism
6. ✅ Comprehensive tests (80%+ coverage)
7. ✅ API documentation

---

## Prerequisites

Before starting, ensure:

1. **T1 Data ETL is complete** - Adjusted Parquet data in `data/adjusted/`
2. **T2 Baseline Strategy is complete** - Trained model in `artifacts/models/`
3. **MLflow is configured** - Experiments tracked in `artifacts/mlruns/`
4. **Postgres is installed** - Local instance or Docker container
5. **Dependencies installed** - `pip install fastapi uvicorn psycopg2-binary pydantic-settings`

---

## Phase 1: Database Setup (2 hours)

### 1.1 Create Database Schema

Create migration file for model registry table.

**File:** `migrations/001_create_model_registry.sql`

```sql
-- Model Registry Table
CREATE TABLE IF NOT EXISTS model_registry (
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

-- Comments
COMMENT ON TABLE model_registry IS 'Registry of trained ML models for signal generation';
COMMENT ON COLUMN model_registry.status IS 'active: serving traffic, inactive: retired, testing: validation, failed: load error';
COMMENT ON COLUMN model_registry.model_path IS 'Absolute path to model file or MLflow URI (e.g., runs:/<run_id>/model)';
```

### 1.2 Run Migration

```bash
# Connect to Postgres
psql -U postgres -d trading_platform

# Run migration
\i migrations/001_create_model_registry.sql

# Verify table created
\d model_registry
```

**Expected Output:**
```
                                        Table "public.model_registry"
        Column         |            Type             | Collation | Nullable |                   Default
-----------------------+-----------------------------+-----------+----------+---------------------------------------------
 id                    | integer                     |           | not null | nextval('model_registry_id_seq'::regclass)
 strategy_name         | text                        |           | not null |
 version               | text                        |           | not null |
 mlflow_run_id         | text                        |           |          |
 ...
```

### 1.3 Insert Test Model Record

Register the T2 baseline model.

```bash
# Find the trained model from T2
ls -lh artifacts/models/

# Expected: alpha_baseline.txt (LightGBM model)
```

```sql
-- Insert baseline model
INSERT INTO model_registry (
    strategy_name,
    version,
    mlflow_run_id,
    model_path,
    status,
    performance_metrics,
    config,
    notes
) VALUES (
    'alpha_baseline',
    'v1.0.0',
    'REPLACE_WITH_MLFLOW_RUN_ID',  -- Get from MLflow UI or artifacts/mlruns
    'artifacts/models/alpha_baseline.txt',  -- Absolute path
    'active',
    '{"ic": 0.082, "sharpe": 1.45, "max_drawdown": -0.12, "win_rate": 0.55}',
    '{"num_boost_round": 100, "learning_rate": 0.05, "max_depth": 6}',
    'Initial baseline model from T2 implementation'
);

-- Verify insertion
SELECT id, strategy_name, version, status, activated_at
FROM model_registry;
```

### 1.4 Create Helper Functions

```sql
-- Function to activate a model (deactivates others)
CREATE OR REPLACE FUNCTION activate_model(p_strategy TEXT, p_version TEXT)
RETURNS VOID AS $$
BEGIN
    -- Deactivate all models for this strategy
    UPDATE model_registry
    SET status = 'inactive',
        deactivated_at = NOW()
    WHERE strategy_name = p_strategy
      AND status = 'active';

    -- Activate the specified model
    UPDATE model_registry
    SET status = 'active',
        activated_at = NOW()
    WHERE strategy_name = p_strategy
      AND version = p_version;
END;
$$ LANGUAGE plpgsql;

-- Test function
SELECT activate_model('alpha_baseline', 'v1.0.0');
```

**Checkpoint:** ✅ Database table created, test record inserted

---

## Phase 2: Model Registry Client (4 hours)

### 2.1 Create Directory Structure

```bash
mkdir -p apps/signal_service/tests
touch apps/signal_service/__init__.py
touch apps/signal_service/main.py
touch apps/signal_service/model_registry.py
touch apps/signal_service/signal_generator.py
touch apps/signal_service/config.py
touch apps/signal_service/models.py
touch apps/signal_service/dependencies.py
touch apps/signal_service/tests/__init__.py
touch apps/signal_service/tests/conftest.py
touch apps/signal_service/tests/test_model_registry.py
```

### 2.2 Configuration

**File:** `apps/signal_service/config.py`

```python
"""Configuration for Signal Service."""

from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Signal service configuration."""

    # Service
    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = False

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/trading_platform"

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

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()
```

### 2.3 Model Registry Client

**File:** `apps/signal_service/model_registry.py`

```python
"""Model registry client for loading and managing models."""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import lightgbm as lgb
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


@dataclass
class ModelMetadata:
    """Metadata for a registered model."""

    id: int
    strategy_name: str
    version: str
    mlflow_run_id: Optional[str]
    mlflow_experiment_id: Optional[str]
    model_path: str
    status: str
    performance_metrics: Dict[str, Any]
    config: Dict[str, Any]
    created_at: datetime
    activated_at: Optional[datetime]


class ModelRegistry:
    """Manages model loading from database registry."""

    def __init__(self, db_conn_string: str):
        """
        Initialize model registry client.

        Args:
            db_conn_string: Postgres connection string
        """
        self.db_conn_string = db_conn_string
        self._current_model: Optional[lgb.Booster] = None
        self._current_metadata: Optional[ModelMetadata] = None
        self._last_check: Optional[datetime] = None

    def get_active_model_metadata(self, strategy: str = "alpha_baseline") -> ModelMetadata:
        """
        Fetch active model metadata from database.

        Args:
            strategy: Strategy name (e.g., "alpha_baseline")

        Returns:
            ModelMetadata for active model

        Raises:
            ValueError: If no active model found
        """
        with psycopg2.connect(self.db_conn_string) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, strategy_name, version, mlflow_run_id, mlflow_experiment_id,
                           model_path, status, performance_metrics, config, created_at, activated_at
                    FROM model_registry
                    WHERE strategy_name = %s AND status = 'active'
                    ORDER BY activated_at DESC
                    LIMIT 1
                    """,
                    (strategy,),
                )
                row = cur.fetchone()

                if not row:
                    raise ValueError(f"No active model for strategy: {strategy}")

                return ModelMetadata(**row)

    def load_model_from_file(self, model_path: str) -> lgb.Booster:
        """
        Load LightGBM model from file.

        Args:
            model_path: Path to model file

        Returns:
            Loaded LightGBM model

        Raises:
            FileNotFoundError: If model file doesn't exist
        """
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        logger.info(f"Loading model from: {model_path}")
        return lgb.Booster(model_file=str(path))

    def reload_if_changed(self, strategy: str = "alpha_baseline") -> bool:
        """
        Check if active model changed and reload if needed.

        Args:
            strategy: Strategy name

        Returns:
            True if model was reloaded, False otherwise

        Raises:
            ValueError: If no active model found
            FileNotFoundError: If model file doesn't exist
        """
        try:
            # Fetch latest active model metadata
            new_metadata = self.get_active_model_metadata(strategy)

            # Check if version changed
            if (
                self._current_metadata is None
                or new_metadata.version != self._current_metadata.version
            ):
                logger.info(
                    f"Model version changed: {self._current_metadata.version if self._current_metadata else 'None'} "
                    f"-> {new_metadata.version}"
                )

                # Load new model
                new_model = self.load_model_from_file(new_metadata.model_path)

                # Update state
                self._current_model = new_model
                self._current_metadata = new_metadata
                self._last_check = datetime.now()

                logger.info(
                    f"Model reloaded successfully: {new_metadata.strategy_name} v{new_metadata.version}"
                )
                return True

            # No change
            self._last_check = datetime.now()
            return False

        except Exception as e:
            logger.error(f"Failed to reload model: {e}")
            # Keep current model if reload fails (graceful degradation)
            if self._current_model is not None:
                logger.warning("Keeping current model after failed reload")
                return False
            raise

    @property
    def current_model(self) -> Optional[lgb.Booster]:
        """Get currently loaded model."""
        return self._current_model

    @property
    def current_metadata(self) -> Optional[ModelMetadata]:
        """Get metadata for currently loaded model."""
        return self._current_metadata

    @property
    def is_loaded(self) -> bool:
        """Check if a model is currently loaded."""
        return self._current_model is not None
```

### 2.4 Tests for Model Registry

**File:** `apps/signal_service/tests/conftest.py`

```python
"""Pytest fixtures for signal service tests."""

import pytest
import tempfile
import shutil
from pathlib import Path
import psycopg2
import lightgbm as lgb
from sklearn.datasets import make_regression
from sklearn.model_selection import train_test_split


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    shutil.rmtree(tmp)


@pytest.fixture
def mock_model(temp_dir):
    """Create a mock LightGBM model for testing."""
    # Generate synthetic data
    X, y = make_regression(n_samples=100, n_features=10, random_state=42)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # Train simple model
    train_data = lgb.Dataset(X_train, label=y_train)
    params = {"objective": "regression", "verbose": -1, "seed": 42}
    model = lgb.train(params, train_data, num_boost_round=10)

    # Save model
    model_path = temp_dir / "test_model.txt"
    model.save_model(str(model_path))

    return model_path


@pytest.fixture
def test_db_url():
    """Return test database URL."""
    return "postgresql://postgres:postgres@localhost:5432/trading_platform_test"


@pytest.fixture
def db_connection(test_db_url):
    """Create test database connection."""
    conn = psycopg2.connect(test_db_url)
    yield conn
    conn.close()
```

**File:** `apps/signal_service/tests/test_model_registry.py`

```python
"""Tests for model registry client."""

import pytest
from datetime import datetime
from apps.signal_service.model_registry import ModelRegistry, ModelMetadata


class TestModelRegistry:
    """Tests for ModelRegistry class."""

    def test_initialization(self, test_db_url):
        """Initialize ModelRegistry."""
        registry = ModelRegistry(test_db_url)
        assert registry.db_conn_string == test_db_url
        assert registry.current_model is None
        assert registry.current_metadata is None

    def test_load_model_from_file(self, test_db_url, mock_model):
        """Load LightGBM model from file."""
        registry = ModelRegistry(test_db_url)
        model = registry.load_model_from_file(str(mock_model))

        assert model is not None
        assert model.num_trees() > 0

    def test_load_model_nonexistent_file_raises_error(self, test_db_url):
        """Loading nonexistent model raises FileNotFoundError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(FileNotFoundError):
            registry.load_model_from_file("/nonexistent/model.txt")

    @pytest.mark.skip(reason="Requires test database setup")
    def test_get_active_model_metadata(self, test_db_url, db_connection, mock_model):
        """Fetch active model metadata from database."""
        # Insert test record
        with db_connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    "test_strategy",
                    "v1.0.0",
                    str(mock_model),
                    "active",
                    '{"ic": 0.05}',
                    '{"num_boost_round": 10}',
                ),
            )
            db_connection.commit()

        # Fetch metadata
        registry = ModelRegistry(test_db_url)
        metadata = registry.get_active_model_metadata("test_strategy")

        assert metadata.strategy_name == "test_strategy"
        assert metadata.version == "v1.0.0"
        assert metadata.status == "active"

    @pytest.mark.skip(reason="Requires test database setup")
    def test_reload_if_changed(self, test_db_url, db_connection, mock_model):
        """Reload model when version changes."""
        # Insert initial model
        with db_connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry
                (strategy_name, version, model_path, status, performance_metrics, config)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("test_strategy", "v1.0.0", str(mock_model), "active", '{}', '{}'),
            )
            db_connection.commit()

        # Initial load
        registry = ModelRegistry(test_db_url)
        reloaded = registry.reload_if_changed("test_strategy")

        assert reloaded is True
        assert registry.is_loaded
        assert registry.current_metadata.version == "v1.0.0"

        # No change - should not reload
        reloaded = registry.reload_if_changed("test_strategy")
        assert reloaded is False

    def test_is_loaded_property(self, test_db_url):
        """Check is_loaded property."""
        registry = ModelRegistry(test_db_url)
        assert registry.is_loaded is False

        # After loading
        # (Would need to mock database and load model)
```

**Checkpoint:** ✅ Model registry client implemented and tested

---

## Phase 3: Signal Generator (4 hours)

### 3.1 Signal Generator Implementation

**File:** `apps/signal_service/signal_generator.py`

```python
"""Signal generator for producing trading signals from model predictions."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
import numpy as np

from strategies.alpha_baseline.data_loader import T1DataProvider
from strategies.alpha_baseline.features import get_alpha158_features
from .model_registry import ModelRegistry

logger = logging.getLogger(__name__)


class SignalGenerator:
    """Generates trading signals from ML model predictions."""

    def __init__(
        self,
        model_registry: ModelRegistry,
        data_dir: Path,
        top_n: int = 3,
        bottom_n: int = 3,
    ):
        """
        Initialize signal generator.

        Args:
            model_registry: Model registry for loading models
            data_dir: Directory containing T1 adjusted data
            top_n: Number of long positions
            bottom_n: Number of short positions
        """
        self.model_registry = model_registry
        self.data_provider = T1DataProvider(data_dir)
        self.top_n = top_n
        self.bottom_n = bottom_n

    def generate_signals(
        self,
        symbols: List[str],
        as_of_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Generate target portfolio weights for given symbols.

        Args:
            symbols: List of symbols to generate signals for
            as_of_date: Date to generate signals for (default: latest available)

        Returns:
            DataFrame with columns:
                - symbol: Stock symbol
                - predicted_return: Model's predicted next-day return
                - rank: Rank by predicted return (1 = highest)
                - target_weight: Portfolio weight (-1 to 1, 0 = no position)

        Raises:
            RuntimeError: If model not loaded
            ValueError: If no features available for given date
        """
        # 1. Validate model is loaded
        if not self.model_registry.is_loaded:
            raise RuntimeError("Model not loaded. Call reload_if_changed() first.")

        # 2. Default to latest available date
        if as_of_date is None:
            as_of_date = datetime.now()

        # Convert to date string for Qlib
        date_str = as_of_date.strftime("%Y-%m-%d")

        # 3. Generate features using same code as research (FEATURE PARITY!)
        logger.info(f"Generating features for {len(symbols)} symbols on {date_str}")

        try:
            features = get_alpha158_features(
                symbols=symbols,
                start_date=date_str,
                end_date=date_str,
                fit_start_date=date_str,  # Use same date (already fitted in training)
                fit_end_date=date_str,
                data_dir=self.data_provider.data_dir,
            )
        except Exception as e:
            logger.error(f"Failed to generate features: {e}")
            raise ValueError(f"No features available for {date_str}: {e}")

        if features.empty:
            raise ValueError(f"No features generated for {date_str}")

        # 4. Generate predictions
        logger.info(f"Generating predictions with model {self.model_registry.current_metadata.version}")

        try:
            predictions = self.model_registry.current_model.predict(features.values)
        except Exception as e:
            logger.error(f"Model prediction failed: {e}")
            raise

        # 5. Create results DataFrame
        results = pd.DataFrame(
            {
                "symbol": features.index.get_level_values("instrument").tolist(),
                "predicted_return": predictions,
            }
        )

        # 6. Rank symbols by predicted return (1 = highest predicted return)
        results["rank"] = results["predicted_return"].rank(ascending=False, method="dense").astype(int)

        # 7. Compute target weights (Top-N Long / Bottom-N Short)
        results["target_weight"] = 0.0

        # Long positions (top N by predicted return)
        top_symbols = results.nsmallest(self.top_n, "rank")
        if not top_symbols.empty:
            results.loc[top_symbols.index, "target_weight"] = 1.0 / self.top_n

        # Short positions (bottom N by predicted return)
        bottom_symbols = results.nlargest(self.bottom_n, "rank")
        if not bottom_symbols.empty:
            results.loc[bottom_symbols.index, "target_weight"] = -1.0 / self.bottom_n

        # 8. Sort by rank and return
        results = results.sort_values("rank").reset_index(drop=True)

        logger.info(
            f"Generated {len(results)} signals: "
            f"{(results['target_weight'] > 0).sum()} long, "
            f"{(results['target_weight'] < 0).sum()} short, "
            f"{(results['target_weight'] == 0).sum()} neutral"
        )

        return results
```

### 3.2 Tests for Signal Generator

**File:** `apps/signal_service/tests/test_signal_generator.py`

```python
"""Tests for signal generator."""

import pytest
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np

from apps.signal_service.signal_generator import SignalGenerator
from apps.signal_service.model_registry import ModelRegistry


class TestSignalGenerator:
    """Tests for SignalGenerator class."""

    def test_initialization(self, test_db_url, temp_dir):
        """Initialize SignalGenerator."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(
            model_registry=registry,
            data_dir=temp_dir,
            top_n=2,
            bottom_n=2,
        )

        assert generator.top_n == 2
        assert generator.bottom_n == 2
        assert generator.model_registry == registry

    @pytest.mark.skip(reason="Requires T1 data and trained model")
    def test_generate_signals(self, test_db_url, temp_dir, mock_model):
        """Generate signals for given symbols."""
        # Setup
        registry = ModelRegistry(test_db_url)
        registry.reload_if_changed("alpha_baseline")

        generator = SignalGenerator(
            model_registry=registry,
            data_dir=Path("data/adjusted"),
            top_n=2,
            bottom_n=2,
        )

        # Generate signals
        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            as_of_date=datetime(2024, 1, 15),
        )

        # Validate structure
        assert isinstance(signals, pd.DataFrame)
        assert list(signals.columns) == ["symbol", "predicted_return", "rank", "target_weight"]
        assert len(signals) == 5

        # Validate weights
        long_positions = signals[signals["target_weight"] > 0]
        short_positions = signals[signals["target_weight"] < 0]

        assert len(long_positions) == 2
        assert len(short_positions) == 2
        assert np.isclose(long_positions["target_weight"].sum(), 1.0)
        assert np.isclose(short_positions["target_weight"].sum(), -1.0)

    def test_generate_signals_without_model_raises_error(self, test_db_url, temp_dir):
        """Generating signals without loaded model raises error."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir)

        with pytest.raises(RuntimeError) as exc_info:
            generator.generate_signals(["AAPL"])

        assert "model not loaded" in str(exc_info.value).lower()
```

**Checkpoint:** ✅ Signal generator implemented with feature parity

---

## Phase 4: FastAPI Application (4 hours)

### 4.1 Pydantic Models

**File:** `apps/signal_service/models.py`

```python
"""Pydantic models for API request/response."""

from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class SignalItem(BaseModel):
    """Individual signal for a symbol."""

    symbol: str = Field(..., description="Stock symbol")
    predicted_return: float = Field(..., description="Model's predicted next-day return")
    rank: int = Field(..., description="Rank by predicted return (1 = highest)")
    target_weight: float = Field(..., description="Target portfolio weight (-1 to 1)")


class SignalMetadata(BaseModel):
    """Metadata about signal generation."""

    strategy: str = Field(..., description="Strategy name")
    model_version: str = Field(..., description="Model version")
    generated_at: datetime = Field(..., description="When signals were generated")
    data_as_of: datetime = Field(..., description="Data cutoff date")
    num_symbols: int = Field(..., description="Number of symbols processed")


class SignalResponse(BaseModel):
    """Response from /signals endpoint."""

    signals: List[SignalItem] = Field(..., description="List of signals")
    metadata: SignalMetadata = Field(..., description="Generation metadata")


class HealthResponse(BaseModel):
    """Response from /health endpoint."""

    status: str = Field(..., description="Service status (healthy/degraded)")
    model_loaded: bool = Field(..., description="Whether a model is loaded")
    model_version: Optional[str] = Field(None, description="Current model version")


class ModelInfoResponse(BaseModel):
    """Response from /model/info endpoint."""

    strategy_name: str
    version: str
    mlflow_run_id: Optional[str]
    status: str
    performance_metrics: Dict[str, Any]
    activated_at: Optional[datetime]


class ReloadResponse(BaseModel):
    """Response from /model/reload endpoint."""

    reloaded: bool = Field(..., description="Whether model was reloaded")
    version: str = Field(..., description="Current model version")
```

### 4.2 FastAPI Application

**File:** `apps/signal_service/main.py`

```python
"""FastAPI application for signal service."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .config import settings
from .model_registry import ModelRegistry
from .signal_generator import SignalGenerator
from .models import (
    HealthResponse,
    SignalResponse,
    SignalItem,
    SignalMetadata,
    ModelInfoResponse,
    ReloadResponse,
)

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global state
model_registry: Optional[ModelRegistry] = None
signal_generator: Optional[SignalGenerator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    global model_registry, signal_generator

    logger.info("Starting Signal Service...")

    try:
        # Initialize model registry
        model_registry = ModelRegistry(settings.database_url)
        logger.info("Model registry initialized")

        # Load initial model
        reloaded = model_registry.reload_if_changed(strategy=settings.default_strategy)
        if reloaded:
            logger.info(
                f"Initial model loaded: {model_registry.current_metadata.strategy_name} "
                f"v{model_registry.current_metadata.version}"
            )
        else:
            logger.warning("No model loaded on startup")

        # Initialize signal generator
        signal_generator = SignalGenerator(
            model_registry=model_registry,
            data_dir=settings.data_dir,
            top_n=settings.top_n,
            bottom_n=settings.bottom_n,
        )
        logger.info("Signal generator initialized")

        logger.info("Signal Service started successfully")

    except Exception as e:
        logger.error(f"Failed to start Signal Service: {e}")
        raise

    yield

    # Shutdown
    logger.info("Shutting down Signal Service...")


# Create FastAPI app
app = FastAPI(
    title="Signal Service",
    description="Generates trading signals from ML models",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.

    Returns service status and model loading state.
    """
    return HealthResponse(
        status="healthy" if model_registry.is_loaded else "degraded",
        model_loaded=model_registry.is_loaded,
        model_version=model_registry.current_metadata.version if model_registry.is_loaded else None,
    )


@app.get("/signals", response_model=SignalResponse)
async def get_signals(
    symbols: Optional[List[str]] = Query(None, description="Filter signals for specific symbols"),
    strategy: str = Query(settings.default_strategy, description="Strategy name"),
    as_of_date: Optional[datetime] = Query(None, description="Data cutoff date (ISO format)"),
):
    """
    Generate trading signals for given symbols.

    Returns target portfolio weights based on model predictions.
    """
    # Validate model is loaded
    if not model_registry.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # Default to all tradable symbols
    if symbols is None:
        symbols = settings.tradable_symbols

    # Default to current date
    if as_of_date is None:
        as_of_date = datetime.now()

    # Generate signals
    try:
        logger.info(f"Generating signals for {len(symbols)} symbols as of {as_of_date}")
        signals_df = signal_generator.generate_signals(symbols, as_of_date)

        # Convert to response format
        signals = [
            SignalItem(
                symbol=row["symbol"],
                predicted_return=float(row["predicted_return"]),
                rank=int(row["rank"]),
                target_weight=float(row["target_weight"]),
            )
            for _, row in signals_df.iterrows()
        ]

        metadata = SignalMetadata(
            strategy=strategy,
            model_version=model_registry.current_metadata.version,
            generated_at=datetime.now(),
            data_as_of=as_of_date,
            num_symbols=len(signals),
        )

        return SignalResponse(signals=signals, metadata=metadata)

    except Exception as e:
        logger.error(f"Failed to generate signals: {e}")
        raise HTTPException(status_code=500, detail=f"Signal generation failed: {str(e)}")


@app.get("/model/info", response_model=ModelInfoResponse)
async def get_model_info():
    """
    Get current model metadata.

    Returns information about the currently loaded model.
    """
    if not model_registry.is_loaded:
        raise HTTPException(status_code=404, detail="No model loaded")

    metadata = model_registry.current_metadata
    return ModelInfoResponse(
        strategy_name=metadata.strategy_name,
        version=metadata.version,
        mlflow_run_id=metadata.mlflow_run_id,
        status=metadata.status,
        performance_metrics=metadata.performance_metrics,
        activated_at=metadata.activated_at,
    )


@app.post("/model/reload", response_model=ReloadResponse)
async def reload_model(strategy: str = Query(settings.default_strategy)):
    """
    Force model reload from registry.

    Checks for new model version and reloads if changed.
    """
    try:
        reloaded = model_registry.reload_if_changed(strategy=strategy)
        return ReloadResponse(
            reloaded=reloaded,
            version=model_registry.current_metadata.version if model_registry.is_loaded else "none",
        )
    except Exception as e:
        logger.error(f"Failed to reload model: {e}")
        raise HTTPException(status_code=500, detail=f"Model reload failed: {str(e)}")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.signal_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
```

### 4.3 API Tests

**File:** `apps/signal_service/tests/test_api.py`

```python
"""Tests for FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from apps.signal_service.main import app


client = TestClient(app)


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_check_returns_200(self):
        """Health check returns 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_check_has_required_fields(self):
        """Health check response has required fields."""
        response = client.get("/health")
        data = response.json()

        assert "status" in data
        assert "model_loaded" in data
        assert "model_version" in data
        assert data["status"] in ["healthy", "degraded"]
        assert isinstance(data["model_loaded"], bool)


@pytest.mark.skip(reason="Requires database and model setup")
class TestSignalsEndpoint:
    """Tests for /signals endpoint."""

    def test_get_signals_with_default_symbols(self):
        """Get signals with default tradable symbols."""
        response = client.get("/signals")
        assert response.status_code == 200

        data = response.json()
        assert "signals" in data
        assert "metadata" in data
        assert len(data["signals"]) > 0

    def test_get_signals_with_specific_symbols(self):
        """Get signals for specific symbols."""
        response = client.get("/signals?symbols=AAPL&symbols=MSFT")
        assert response.status_code == 200

        data = response.json()
        signals = data["signals"]
        symbols = [s["symbol"] for s in signals]

        assert "AAPL" in symbols
        assert "MSFT" in symbols

    def test_signals_have_required_fields(self):
        """Signal items have all required fields."""
        response = client.get("/signals?symbols=AAPL")
        data = response.json()
        signal = data["signals"][0]

        assert "symbol" in signal
        assert "predicted_return" in signal
        assert "rank" in signal
        assert "target_weight" in signal


@pytest.mark.skip(reason="Requires database and model setup")
class TestModelInfoEndpoint:
    """Tests for /model/info endpoint."""

    def test_get_model_info(self):
        """Get current model information."""
        response = client.get("/model/info")
        assert response.status_code == 200

        data = response.json()
        assert "strategy_name" in data
        assert "version" in data
        assert "status" in data
        assert "performance_metrics" in data


@pytest.mark.skip(reason="Requires database and model setup")
class TestReloadEndpoint:
    """Tests for /model/reload endpoint."""

    def test_reload_model(self):
        """Reload model from registry."""
        response = client.post("/model/reload")
        assert response.status_code == 200

        data = response.json()
        assert "reloaded" in data
        assert "version" in data
        assert isinstance(data["reloaded"], bool)
```

**Checkpoint:** ✅ FastAPI application with all endpoints implemented

---

## Phase 5: Hot Reload Mechanism (2 hours)

### 5.1 Background Polling Task

Add to `main.py`:

```python
import asyncio

async def model_reload_task():
    """Background task to poll registry and reload model if changed."""
    logger.info(f"Starting model reload task (interval: {settings.model_reload_interval_seconds}s)")

    while True:
        try:
            await asyncio.sleep(settings.model_reload_interval_seconds)

            logger.debug("Checking for model updates...")
            reloaded = model_registry.reload_if_changed(strategy=settings.default_strategy)

            if reloaded:
                logger.info(
                    f"Model auto-reloaded: {model_registry.current_metadata.strategy_name} "
                    f"v{model_registry.current_metadata.version}"
                )

        except Exception as e:
            logger.error(f"Model reload task failed: {e}")
            # Continue polling even if one check fails


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # ... existing startup code ...

    # Start background reload task
    reload_task = asyncio.create_task(model_reload_task())
    logger.info("Background model reload task started")

    yield

    # Shutdown
    reload_task.cancel()
    logger.info("Background model reload task stopped")
    logger.info("Shutting down Signal Service...")
```

### 5.2 Manual Reload Script

**File:** `scripts/reload_signal_service.sh`

```bash
#!/bin/bash
# Trigger manual model reload via API

SIGNAL_SERVICE_URL="${SIGNAL_SERVICE_URL:-http://localhost:8001}"

echo "Triggering model reload..."
curl -X POST "$SIGNAL_SERVICE_URL/model/reload" | jq

echo ""
echo "Checking model info..."
curl -X GET "$SIGNAL_SERVICE_URL/model/info" | jq
```

Make executable:
```bash
chmod +x scripts/reload_signal_service.sh
```

**Checkpoint:** ✅ Hot reload mechanism implemented

---

## Phase 6: Integration Tests (4 hours)

### 6.1 Integration Test Suite

**File:** `apps/signal_service/tests/test_integration.py`

```python
"""Integration tests for signal service (requires database and T1 data)."""

import pytest
from datetime import datetime
from pathlib import Path
import psycopg2

from apps.signal_service.model_registry import ModelRegistry
from apps.signal_service.signal_generator import SignalGenerator


@pytest.mark.integration
@pytest.mark.skip(reason="Requires database and T1 data setup")
class TestSignalServiceIntegration:
    """Full integration tests."""

    def test_end_to_end_signal_generation(self):
        """Test complete signal generation workflow."""
        # 1. Setup
        db_url = "postgresql://postgres:postgres@localhost:5432/trading_platform"
        registry = ModelRegistry(db_url)

        # 2. Load model
        reloaded = registry.reload_if_changed("alpha_baseline")
        assert reloaded is True
        assert registry.is_loaded

        # 3. Generate signals
        generator = SignalGenerator(
            model_registry=registry,
            data_dir=Path("data/adjusted"),
            top_n=3,
            bottom_n=3,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            as_of_date=datetime(2024, 1, 15),
        )

        # 4. Validate results
        assert len(signals) == 5
        assert (signals["target_weight"] != 0).sum() == 6  # 3 long + 3 short

    def test_feature_parity_validation(self):
        """Validate production features match research features."""
        from strategies.alpha_baseline.features import get_alpha158_features

        # Generate features using research code
        symbols = ["AAPL", "MSFT"]
        date = "2024-01-15"

        research_features = get_alpha158_features(
            symbols=symbols,
            start_date=date,
            end_date=date,
            fit_start_date=date,
            fit_end_date=date,
            data_dir=Path("data/adjusted"),
        )

        # Generate features using production signal generator
        # (SignalGenerator uses same get_alpha158_features internally)
        # This test validates the import and usage are identical

        assert research_features.shape[1] == 158  # 158 features
        assert not research_features.empty
```

### 6.2 Feature Parity Test

**File:** `apps/signal_service/tests/test_feature_parity.py`

```python
"""Tests to validate feature parity between research and production."""

import pytest
from pathlib import Path
from datetime import datetime
import pandas as pd

from strategies.alpha_baseline.features import get_alpha158_features


@pytest.mark.integration
@pytest.mark.skip(reason="Requires T1 data")
class TestFeatureParity:
    """Validate production features match research features exactly."""

    def test_feature_computation_is_deterministic(self):
        """Features computed twice should be identical."""
        symbols = ["AAPL"]
        date = "2024-01-15"
        data_dir = Path("data/adjusted")

        # Compute features twice
        features1 = get_alpha158_features(
            symbols=symbols,
            start_date=date,
            end_date=date,
            fit_start_date=date,
            fit_end_date=date,
            data_dir=data_dir,
        )

        features2 = get_alpha158_features(
            symbols=symbols,
            start_date=date,
            end_date=date,
            fit_start_date=date,
            fit_end_date=date,
            data_dir=data_dir,
        )

        # Should be exactly identical
        pd.testing.assert_frame_equal(features1, features2)

    def test_signal_generator_uses_same_features(self):
        """Signal generator uses same feature code as research."""
        # This is validated by inspection - SignalGenerator imports
        # get_alpha158_features from strategies.alpha_baseline.features
        #
        # Key: NO feature code duplication!
        from apps.signal_service.signal_generator import SignalGenerator
        import inspect

        source = inspect.getsource(SignalGenerator.generate_signals)
        assert "get_alpha158_features" in source
        assert "strategies.alpha_baseline.features" in source
```

**Checkpoint:** ✅ Integration tests and feature parity validation

---

## Phase 7: Documentation & Deployment (2 hours)

### 7.1 API Documentation (OpenAPI)

**File:** `docs/API/signal_service.openapi.yaml`

```yaml
openapi: 3.0.3
info:
  title: Signal Service API
  version: 0.1.0
  description: |
    Generates trading signals (target portfolio weights) from ML models.

    ## Features
    - Load models from database registry
    - Generate signals with feature parity to research
    - Hot reload on model version changes
    - Real-time signal generation (< 100ms)

servers:
  - url: http://localhost:8001
    description: Local development

paths:
  /health:
    get:
      summary: Health check
      tags: [Monitoring]
      responses:
        '200':
          description: Service is healthy
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HealthResponse'

  /signals:
    get:
      summary: Generate trading signals
      tags: [Signals]
      parameters:
        - name: symbols
          in: query
          required: false
          schema:
            type: array
            items:
              type: string
          description: Filter signals for specific symbols (default: all tradable)
        - name: strategy
          in: query
          required: false
          schema:
            type: string
            default: alpha_baseline
        - name: as_of_date
          in: query
          required: false
          schema:
            type: string
            format: date-time
          description: Data cutoff date (default: now)
      responses:
        '200':
          description: Target portfolio weights
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SignalResponse'
        '503':
          description: Model not loaded

  /model/info:
    get:
      summary: Get current model metadata
      tags: [Model]
      responses:
        '200':
          description: Model information
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ModelInfoResponse'
        '404':
          description: No model loaded

  /model/reload:
    post:
      summary: Force model reload
      tags: [Model]
      parameters:
        - name: strategy
          in: query
          required: false
          schema:
            type: string
            default: alpha_baseline
      responses:
        '200':
          description: Reload completed
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ReloadResponse'
        '500':
          description: Reload failed

components:
  schemas:
    HealthResponse:
      type: object
      required: [status, model_loaded]
      properties:
        status:
          type: string
          enum: [healthy, degraded]
        model_loaded:
          type: boolean
        model_version:
          type: string
          nullable: true

    SignalItem:
      type: object
      required: [symbol, predicted_return, rank, target_weight]
      properties:
        symbol:
          type: string
          example: AAPL
        predicted_return:
          type: number
          format: float
          example: 0.015
        rank:
          type: integer
          example: 1
        target_weight:
          type: number
          format: float
          example: 0.333
          description: Portfolio weight (-1 to 1)

    SignalMetadata:
      type: object
      required: [strategy, model_version, generated_at, data_as_of, num_symbols]
      properties:
        strategy:
          type: string
        model_version:
          type: string
        generated_at:
          type: string
          format: date-time
        data_as_of:
          type: string
          format: date-time
        num_symbols:
          type: integer

    SignalResponse:
      type: object
      required: [signals, metadata]
      properties:
        signals:
          type: array
          items:
            $ref: '#/components/schemas/SignalItem'
        metadata:
          $ref: '#/components/schemas/SignalMetadata'

    ModelInfoResponse:
      type: object
      required: [strategy_name, version, status, performance_metrics]
      properties:
        strategy_name:
          type: string
        version:
          type: string
        mlflow_run_id:
          type: string
          nullable: true
        status:
          type: string
          enum: [active, inactive, testing, failed]
        performance_metrics:
          type: object
        activated_at:
          type: string
          format: date-time
          nullable: true

    ReloadResponse:
      type: object
      required: [reloaded, version]
      properties:
        reloaded:
          type: boolean
        version:
          type: string
```

### 7.2 Deployment Guide

**File:** `docs/DEPLOYMENT/signal-service.md`

```markdown
# Signal Service Deployment Guide

## Prerequisites

1. **Database Setup**
   ```bash
   # Run migrations
   psql -U postgres -d trading_platform -f migrations/001_create_model_registry.sql

   # Register initial model
   psql -U postgres -d trading_platform -c "
   INSERT INTO model_registry (strategy_name, version, model_path, status, performance_metrics, config)
   VALUES ('alpha_baseline', 'v1.0.0', 'artifacts/models/alpha_baseline.txt', 'active', '{\"ic\": 0.082}', '{}');
   "
   ```

2. **Environment Variables**
   ```bash
   # .env file
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_platform
   DATA_DIR=data/adjusted
   DEFAULT_STRATEGY=alpha_baseline
   TOP_N=3
   BOTTOM_N=3
   MODEL_RELOAD_INTERVAL_SECONDS=300
   LOG_LEVEL=INFO
   ```

3. **Install Dependencies**
   ```bash
   pip install fastapi uvicorn psycopg2-binary pydantic-settings
   ```

## Running Locally

### Development Mode (with auto-reload)
```bash
cd apps/signal_service
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

### Production Mode
```bash
cd apps/signal_service
uvicorn main:app --host 0.0.0.0 --port 8001 --workers 4
```

## Testing

### Unit Tests
```bash
pytest apps/signal_service/tests/ -v
```

### Integration Tests (requires database + data)
```bash
pytest apps/signal_service/tests/ -v -m integration
```

## API Usage Examples

### Health Check
```bash
curl http://localhost:8001/health | jq
```

### Get Signals
```bash
# All tradable symbols
curl "http://localhost:8001/signals" | jq

# Specific symbols
curl "http://localhost:8001/signals?symbols=AAPL&symbols=MSFT" | jq

# Specific date
curl "http://localhost:8001/signals?as_of_date=2024-01-15T00:00:00" | jq
```

### Get Model Info
```bash
curl http://localhost:8001/model/info | jq
```

### Reload Model
```bash
curl -X POST http://localhost:8001/model/reload | jq
```

## Monitoring

### Health Check
```bash
watch -n 5 "curl -s http://localhost:8001/health | jq"
```

### Logs
```bash
# View logs
tail -f signal_service.log

# Filter errors
tail -f signal_service.log | grep ERROR
```

## Troubleshooting

### Model Not Loading
1. Check model file exists: `ls -lh artifacts/models/alpha_baseline.txt`
2. Check database record: `SELECT * FROM model_registry WHERE status = 'active';`
3. Check logs: `grep "Failed to load model" signal_service.log`

### Feature Generation Failing
1. Check T1 data exists: `ls -lh data/adjusted/`
2. Validate data directory in config
3. Check Qlib initialization

### Database Connection Issues
1. Verify Postgres is running: `pg_isready`
2. Check connection string in .env
3. Test connection: `psql $DATABASE_URL -c "SELECT 1;"`
```

### 7.3 README

**File:** `apps/signal_service/README.md`

```markdown
# Signal Service

FastAPI microservice for generating trading signals from ML models.

## Features

- ✅ Load models from database registry
- ✅ Generate real-time signals (< 100ms)
- ✅ Feature parity with research code
- ✅ Hot reload on model updates
- ✅ REST API with automatic docs

## Quick Start

```bash
# Start service
uvicorn main:app --reload

# Visit API docs
open http://localhost:8001/docs

# Generate signals
curl http://localhost:8001/signals | jq
```

## Architecture

See [ADR-0004](../../docs/ADRs/0004-signal-service-architecture.md) for detailed architecture decisions.

## API Documentation

- **OpenAPI Spec:** `/docs/API/signal_service.openapi.yaml`
- **Interactive Docs:** `http://localhost:8001/docs` (Swagger UI)
- **ReDoc:** `http://localhost:8001/redoc`

## Testing

```bash
# Unit tests
pytest tests/ -v

# Integration tests (requires database)
pytest tests/ -v -m integration

# Coverage
pytest tests/ --cov=apps.signal_service --cov-report=html
```

## Configuration

See `config.py` for all settings. Override via environment variables:

```bash
export DATABASE_URL=postgresql://...
export DATA_DIR=data/adjusted
export TOP_N=5
export BOTTOM_N=5
```

## Deployment

See [Deployment Guide](../../docs/DEPLOYMENT/signal-service.md) for production deployment.
```

**Checkpoint:** ✅ Documentation complete

---

## Final Steps

### 1. Run Full Test Suite

```bash
# Activate venv
source .venv/bin/activate

# Run all unit tests
pytest apps/signal_service/tests/ -v --tb=short

# Expected: Most pass, some skipped (require database)
```

### 2. Start Service Locally

```bash
# Terminal 1: Start Postgres (if not running)
# (Docker or local)

# Terminal 2: Start Signal Service
cd apps/signal_service
uvicorn main:app --reload

# Terminal 3: Test endpoints
curl http://localhost:8001/health | jq
curl http://localhost:8001/docs  # Open in browser
```

### 3. Manual Testing

```bash
# 1. Health check
curl http://localhost:8001/health

# Expected: {"status": "healthy", "model_loaded": true, "model_version": "v1.0.0"}

# 2. Get signals
curl "http://localhost:8001/signals?symbols=AAPL&symbols=MSFT"

# Expected: JSON with signals array

# 3. Model info
curl http://localhost:8001/model/info

# Expected: Model metadata

# 4. Reload
curl -X POST http://localhost:8001/model/reload

# Expected: {"reloaded": false, "version": "v1.0.0"}  (no change)
```

---

## Success Criteria Checklist

- [ ] Database schema created and migrated
- [ ] Model registry table populated with T2 model
- [ ] Model registry client implemented
- [ ] Signal generator implemented with feature parity
- [ ] FastAPI app with all 4 endpoints
- [ ] Hot reload mechanism (background task)
- [ ] Unit tests (80%+ coverage)
- [ ] Integration tests (skipped by default)
- [ ] Feature parity validation
- [ ] API documentation (OpenAPI spec)
- [ ] Deployment guide
- [ ] Service runs locally
- [ ] Health check returns 200 OK
- [ ] Signals endpoint returns valid data
- [ ] Model info endpoint works
- [ ] Manual reload endpoint works

---

## Next Steps (T4)

After completing T3, proceed to **T4: Execution Gateway**:
- Idempotent order submission
- DRY_RUN mode
- Alpaca API integration
- Circuit breaker pattern

See: `docs/TASKS/P0_TICKETS.md`

---

## Troubleshooting

### Common Issues

**1. "Model not loaded" on startup**
- Check database contains active model record
- Verify model_path in database points to existing file
- Check logs for load errors

**2. "No features available for date"**
- Ensure T1 data exists for the date
- Check data_dir configuration
- Verify Qlib can read data

**3. "Database connection failed"**
- Start Postgres: `brew services start postgresql` (Mac)
- Check connection string in .env
- Test: `psql $DATABASE_URL -c "SELECT 1;"`

**4. "Feature parity test fails"**
- Ensure same Qlib version as research
- Check for code changes in features.py
- Validate data preprocessing is identical

---

## References

- **ADR-0004:** Signal Service Architecture
- **T2 Guide:** Baseline Strategy Implementation
- **FastAPI Docs:** https://fastapi.tiangolo.com
- **Uvicorn Docs:** https://www.uvicorn.org
