"""
Pytest fixtures for signal service tests.

This module provides reusable test fixtures for:
- Temporary directories and files
- Mock LightGBM models
- Database connections (test database)
- Mock data (Parquet files, features, etc.)

Example:
    def test_something(temp_dir, mock_model):
        # temp_dir and mock_model are automatically provided
        model = lgb.Booster(model_file=str(mock_model))
        assert model.num_trees() > 0

See Also:
    - pytest fixtures documentation: https://docs.pytest.org/en/stable/fixture.html
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg
import pytest
from sklearn.datasets import make_regression  # type: ignore[import-untyped]
from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]

# ============================================================================
# Directory and File Fixtures
# ============================================================================


@pytest.fixture()
def temp_dir():
    """
    Create temporary directory for tests.

    Directory is automatically cleaned up after test completes.

    Yields:
        Path: Temporary directory path

    Example:
        def test_file_creation(temp_dir):
            file_path = temp_dir / "test.txt"
            file_path.write_text("hello")
            assert file_path.exists()
    """
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    if tmp.exists():
        shutil.rmtree(tmp)


# ============================================================================
# Model Fixtures
# ============================================================================


@pytest.fixture()
def mock_model(temp_dir):
    """
    Create a mock LightGBM model for testing.

    Trains a simple regression model on synthetic data and saves to disk.
    Model has 10 features and 10 trees (small for fast tests).

    Args:
        temp_dir: Temporary directory fixture

    Yields:
        Path: Path to saved model file

    Example:
        def test_model_loading(mock_model):
            model = lgb.Booster(model_file=str(mock_model))
            assert model.num_trees() == 10
            assert model.num_feature() == 10
    """
    # Generate synthetic regression data
    # 100 samples, 10 features (instead of 158 for speed)
    X, y = make_regression(
        n_samples=100, n_features=10, n_informative=8, noise=10.0, random_state=42
    )
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # Train simple LightGBM model
    train_data = lgb.Dataset(X_train, label=y_train)
    params = {
        "objective": "regression",
        "metric": "mae",
        "num_leaves": 7,
        "learning_rate": 0.1,
        "verbose": -1,
        "seed": 42,
    }
    model = lgb.train(params, train_data, num_boost_round=10)

    # Save model to temporary directory
    model_path = temp_dir / "test_model.txt"
    model.save_model(str(model_path))

    return model_path

    # Cleanup handled by temp_dir fixture


@pytest.fixture()
def alpha_baseline_model_path():
    """
    Path to actual alpha_baseline model (if exists).

    Returns None if model doesn't exist (for integration tests).

    Yields:
        Path | None: Path to alpha_baseline model or None

    Example:
        @pytest.mark.skipif(not alpha_baseline_model_path(), reason="Model not found")
        def test_with_real_model(alpha_baseline_model_path):
            model = lgb.Booster(model_file=str(alpha_baseline_model_path))
            assert model.num_feature() == 158  # Alpha158
    """
    model_path = Path("artifacts/models/alpha_baseline.txt")
    if model_path.exists():
        yield model_path
    else:
        yield None


# ============================================================================
# Database Fixtures
# ============================================================================


@pytest.fixture()
def test_db_url():
    """
    Test database connection string.

    Returns connection string for test database (separate from production).

    Returns:
        str: Postgres connection string

    Example:
        def test_database(test_db_url):
            conn = psycopg.connect(test_db_url)
            assert conn is not None

    Notes:
        Reads from DATABASE_URL environment variable (set by CI) or falls back to default.
    """
    import os

    return os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform_test"
    )


@pytest.fixture()
def db_connection(test_db_url):
    """
    Create test database connection.

    Connection is automatically closed after test completes.

    Args:
        test_db_url: Test database URL fixture

    Yields:
        psycopg.connection: Database connection

    Example:
        def test_query(db_connection):
            with db_connection.cursor() as cur:
                cur.execute("SELECT 1")
                result = cur.fetchone()
                assert result[0] == 1
    """
    conn = psycopg.connect(test_db_url)
    yield conn
    conn.close()


@pytest.fixture()
def setup_model_registry_table(db_connection):
    """
    Create model_registry table in test database.

    Runs migration SQL to create table and helper functions.

    Args:
        db_connection: Database connection fixture

    Yields:
        psycopg.connection: Database connection with table created

    Example:
        def test_model_registry(setup_model_registry_table):
            # Table is ready to use
            with setup_model_registry_table.cursor() as cur:
                cur.execute("SELECT * FROM model_registry")
    """
    # Read migration SQL
    migration_path = Path("migrations/001_create_model_registry.sql")
    if not migration_path.exists():
        pytest.skip("Migration file not found")

    migration_sql = migration_path.read_text()

    # Execute migration
    with db_connection.cursor() as cur:
        cur.execute(migration_sql)
        db_connection.commit()

    yield db_connection

    # Cleanup: drop table
    with db_connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS model_registry CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS activate_model CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS get_active_model CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS get_model_history CASCADE")
        db_connection.commit()


# ============================================================================
# Mock Data Fixtures
# ============================================================================


@pytest.fixture()
def mock_t1_data(temp_dir):
    """
    Create mock T1 adjusted data (Parquet files).

    Creates directory structure with Parquet files for testing:
        temp_dir/2024-01-15/
        ├── AAPL.parquet
        ├── MSFT.parquet
        └── GOOGL.parquet

    Each file contains 30 days of OHLCV data.

    Args:
        temp_dir: Temporary directory fixture

    Yields:
        Path: Path to mock data directory

    Example:
        def test_data_loading(mock_t1_data):
            data_dir = mock_t1_data
            assert (data_dir / "2024-01-15" / "AAPL.parquet").exists()
    """
    # Create date directory
    date_dir = temp_dir / "2024-01-15"
    date_dir.mkdir(parents=True)

    # Generate mock OHLCV data for 3 symbols
    symbols = ["AAPL", "MSFT", "GOOGL"]
    dates = pd.date_range(start="2023-12-01", end="2024-01-15", freq="D")

    for symbol in symbols:
        # Generate realistic-looking price data
        np.random.seed(hash(symbol) % (2**32))  # Different seed per symbol
        base_price = 100.0 + np.random.rand() * 100.0  # Random base 100-200

        # Random walk with drift
        returns = np.random.randn(len(dates)) * 0.02  # 2% daily volatility
        prices = base_price * np.exp(np.cumsum(returns))

        # Create OHLCV data
        df = pd.DataFrame(
            {
                "date": dates,
                "symbol": symbol,
                "open": prices * (1 + np.random.randn(len(dates)) * 0.005),
                "high": prices * (1 + np.abs(np.random.randn(len(dates))) * 0.01),
                "low": prices * (1 - np.abs(np.random.randn(len(dates))) * 0.01),
                "close": prices,
                "volume": np.random.randint(1_000_000, 10_000_000, len(dates)),
            }
        )

        # Save to Parquet
        file_path = date_dir / f"{symbol}.parquet"
        df.to_parquet(file_path, index=False)

    return temp_dir


# ============================================================================
# Model Registry Fixtures
# ============================================================================


@pytest.fixture()
def sample_model_metadata():
    """
    Sample model metadata for testing.

    Returns:
        dict: Model metadata dictionary

    Example:
        def test_metadata(sample_model_metadata):
            assert sample_model_metadata["version"] == "v1.0.0"
            assert sample_model_metadata["status"] == "active"
    """
    return {
        "id": 1,
        "strategy_name": "alpha_baseline",
        "version": "v1.0.0",
        "mlflow_run_id": "abc123",
        "mlflow_experiment_id": "exp456",
        "model_path": "/path/to/model.txt",
        "status": "active",
        "performance_metrics": {
            "ic": 0.082,
            "sharpe": 1.45,
            "max_drawdown": -0.12,
            "win_rate": 0.55,
        },
        "config": {
            "learning_rate": 0.05,
            "max_depth": 6,
            "num_boost_round": 100,
        },
        "created_at": datetime.now(),
        "activated_at": datetime.now(),
    }


# ============================================================================
# Feature Fixtures
# ============================================================================


@pytest.fixture()
def mock_alpha158_features():
    """
    Mock Alpha158 features DataFrame.

    Creates DataFrame with 5 symbols, 158 features (random data).
    Structure matches get_alpha158_features() output.

    Returns:
        pd.DataFrame: Features with (date, symbol) MultiIndex

    Example:
        def test_features(mock_alpha158_features):
            assert mock_alpha158_features.shape[1] == 158
            assert len(mock_alpha158_features.index.names) == 2
            assert mock_alpha158_features.index.names == ["datetime", "instrument"]
    """
    # Create MultiIndex
    date = pd.Timestamp("2024-01-15")
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
    index = pd.MultiIndex.from_product([[date], symbols], names=["datetime", "instrument"])

    # Generate random features (158 columns)
    np.random.seed(42)
    features = pd.DataFrame(
        np.random.randn(5, 158),  # 5 symbols, 158 features
        index=index,
        columns=[f"feature_{i:03d}" for i in range(158)],
    )

    return features


# ============================================================================
# Configuration Fixtures
# ============================================================================


@pytest.fixture()
def test_config():
    """
    Test configuration dictionary.

    Returns:
        dict: Configuration for testing

    Example:
        def test_with_config(test_config):
            assert test_config["top_n"] == 2
            assert test_config["bottom_n"] == 2

    Notes:
        Reads database_url from DATABASE_URL environment variable (set by CI) or falls back to default.
    """
    import os

    return {
        "database_url": os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform_test"
        ),
        "data_dir": "data/adjusted",
        "default_strategy": "alpha_baseline",
        "tradable_symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
        "top_n": 2,
        "bottom_n": 2,
        "model_reload_interval_seconds": 300,
        "log_level": "DEBUG",
    }


# ============================================================================
# Skip Markers
# ============================================================================

skip_if_no_database = pytest.mark.skipif(
    True,  # Always skip by default
    reason="Requires test database setup. Run manually: pytest -m integration",
)

skip_if_no_model = pytest.mark.skipif(
    not Path("artifacts/models/alpha_baseline.txt").exists(),
    reason="Requires trained alpha_baseline model from T2",
)

skip_if_no_t1_data = pytest.mark.skipif(
    not Path("data/adjusted").exists(), reason="Requires T1 adjusted data"
)


# ============================================================================
# FastAPI Test Client Fixtures
# ============================================================================


@pytest.fixture()
def client(monkeypatch, mock_settings, mock_model_registry, mock_signal_generator):
    """
    FastAPI test client for endpoint testing with mocked globals.

    Args:
        monkeypatch: pytest monkeypatch fixture
        mock_settings: Mocked settings
        mock_model_registry: Mocked model registry
        mock_signal_generator: Mocked signal generator

    Returns:
        TestClient: Configured test client

    Example:
        def test_endpoint(client):
            response = client.get("/health")
            assert response.status_code == 200
    """
    from fastapi.testclient import TestClient

    from apps.signal_service import main

    # Patch all global variables
    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "model_registry", mock_model_registry)
    monkeypatch.setattr(main, "signal_generator", mock_signal_generator)
    monkeypatch.setattr(main, "redis_client", None)
    monkeypatch.setattr(main, "feature_cache", None)
    monkeypatch.setattr(main, "event_publisher", None)
    monkeypatch.setattr(main, "fallback_buffer", None)
    monkeypatch.setattr(main, "shadow_validator", None)

    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture()
def mock_settings():
    """
    Mock Settings object for testing.

    Returns:
        Mock: Settings mock with default values

    Example:
        def test_with_settings(mock_settings):
            mock_settings.redis_enabled = True
            assert mock_settings.redis_enabled
    """
    from unittest.mock import Mock

    settings = Mock()
    settings.testing = False
    settings.redis_enabled = True
    settings.redis_host = "localhost"
    settings.redis_port = 6379
    settings.redis_db = 0
    settings.redis_ttl = 3600
    settings.default_strategy = "alpha_baseline"
    settings.model_reload_interval_seconds = 300
    settings.feature_hydration_enabled = True
    settings.feature_hydration_timeout_seconds = 300
    settings.shadow_validation_enabled = True
    settings.skip_shadow_validation = False
    settings.redis_fallback_replay_interval_seconds = 30
    settings.redis_fallback_buffer_max_size = 1000
    settings.redis_fallback_buffer_path = "/tmp/fallback_buffer.json"
    settings.tradable_symbols = ["AAPL", "MSFT", "GOOGL"]
    settings.shadow_sample_count = 100
    settings.data_dir = "data/adjusted"
    settings.top_n = 2
    settings.bottom_n = 2

    return settings


@pytest.fixture()
def mock_model_registry():
    """
    Mock ModelRegistry for testing.

    Returns:
        Mock: ModelRegistry mock with default behavior

    Example:
        def test_with_registry(mock_model_registry):
            assert mock_model_registry.is_loaded
    """
    from datetime import UTC, datetime
    from unittest.mock import Mock

    from apps.signal_service.model_registry import ModelMetadata

    registry = Mock()
    registry.is_loaded = True
    registry.pending_validation = False

    # Create REAL metadata object (not Mock) for proper serialization
    metadata = ModelMetadata(
        id=1,
        strategy_name="alpha_baseline",
        version="v1.0.0",
        mlflow_run_id="test_run_123",
        mlflow_experiment_id="test_exp_456",
        status="active",
        model_path="/path/to/model.txt",
        activated_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        performance_metrics={"ic": 0.082, "sharpe": 1.45},
        config={"learning_rate": 0.05},
    )

    registry.current_metadata = metadata
    registry.pending_metadata = None

    return registry


@pytest.fixture()
def mock_signal_generator():
    """
    Mock SignalGenerator for testing.

    Returns:
        Mock: SignalGenerator mock with default behavior

    Example:
        def test_with_generator(mock_signal_generator):
            mock_signal_generator.generate_signals.return_value = pd.DataFrame(...)
    """
    from unittest.mock import Mock

    generator = Mock()
    generator.top_n = 2
    generator.bottom_n = 2
    generator.data_provider = Mock()
    generator.data_provider.data_dir = "data/adjusted"

    return generator


@pytest.fixture()
def mock_auth_context(monkeypatch):
    """
    Mock authentication and rate limiting for API endpoints.

    This fixture patches both auth and rate limit dependencies to bypass them.

    Args:
        monkeypatch: pytest monkeypatch fixture

    Example:
        def test_protected_endpoint(client, mock_auth_context):
            response = client.post("/api/v1/signals/generate", json={...})
            # Auth and rate limiting are bypassed
    """
    from unittest.mock import Mock

    from apps.signal_service.main import app, signal_generate_auth, signal_generate_rl

    # Create mock auth context
    auth_ctx = Mock()
    auth_ctx.user_id = "test-service"
    auth_ctx.service_id = "test-service"
    auth_ctx.roles = set()
    auth_ctx.permissions = set()

    # Override auth dependency
    def mock_auth_dependency():
        return auth_ctx

    # Override rate limit dependency (return remaining count)
    def mock_rate_limit_dependency():
        return 100  # Plenty remaining

    # Patch the dependencies
    app.dependency_overrides[signal_generate_auth] = mock_auth_dependency
    app.dependency_overrides[signal_generate_rl] = mock_rate_limit_dependency

    yield auth_ctx

    # Cleanup
    app.dependency_overrides.pop(signal_generate_auth, None)
    app.dependency_overrides.pop(signal_generate_rl, None)
