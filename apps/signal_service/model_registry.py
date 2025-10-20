"""
Model registry client for loading and managing ML models.

This module provides the ModelRegistry class which handles:
- Loading model metadata from database
- Loading LightGBM models from disk
- Hot reloading when model version changes
- Graceful degradation when model load fails

The registry pattern ensures production services always know which model
version is active and can automatically update when new models are deployed.

Example:
    >>> from apps.signal_service.model_registry import ModelRegistry
    >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
    >>>
    >>> # Load initial model
    >>> registry.reload_if_changed("alpha_baseline")
    True  # Model loaded
    >>>
    >>> # Check current model
    >>> registry.current_metadata.version
    'v1.0.0'
    >>>
    >>> # Make prediction
    >>> predictions = registry.current_model.predict(features)

See Also:
    - /docs/CONCEPTS/model-registry.md for concept explanation
    - /docs/ADRs/0004-signal-service-architecture.md for architecture
    - migrations/001_create_model_registry.sql for database schema
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import psycopg
from psycopg import DatabaseError, OperationalError
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


@dataclass
class ModelMetadata:
    """
    Metadata for a registered model.

    This dataclass holds all information about a model from the registry,
    including where it's stored, its performance metrics, and deployment status.

    Attributes:
        id: Database primary key
        strategy_name: Strategy identifier (e.g., "alpha_baseline")
        version: Model version (e.g., "v1.0.0", "20250117")
        mlflow_run_id: MLflow run ID for traceability (optional)
        mlflow_experiment_id: MLflow experiment ID (optional)
        model_path: Absolute path to model file
        status: Deployment status (active, inactive, testing, failed)
        performance_metrics: Backtest metrics (IC, Sharpe, drawdown, etc.)
        config: Model hyperparameters and training config
        created_at: When model was registered
        activated_at: When model was activated (if ever)

    Example:
        >>> metadata = ModelMetadata(
        ...     id=1,
        ...     strategy_name="alpha_baseline",
        ...     version="v1.0.0",
        ...     model_path="/path/to/model.txt",
        ...     status="active",
        ...     performance_metrics={"ic": 0.082, "sharpe": 1.45},
        ...     config={"learning_rate": 0.05},
        ...     created_at=datetime.now(),
        ...     activated_at=datetime.now()
        ... )
    """

    id: int
    strategy_name: str
    version: str
    mlflow_run_id: str | None
    mlflow_experiment_id: str | None
    model_path: str
    status: str
    performance_metrics: dict[str, Any]
    config: dict[str, Any]
    created_at: datetime
    activated_at: datetime | None


class ModelRegistry:
    """
    Client for loading and managing models from database registry.

    The registry provides:
    - Loading model metadata from Postgres
    - Loading LightGBM models from disk
    - Hot reload when model version changes (no restart needed)
    - Graceful degradation (keeps old model if new one fails)

    Args:
        db_conn_string: Postgres connection string
            Format: postgresql://user:pass@host:port/dbname

    Example:
        >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
        >>>
        >>> # Load model for first time
        >>> reloaded = registry.reload_if_changed("alpha_baseline")
        >>> reloaded
        True
        >>>
        >>> # Check currently loaded model
        >>> registry.is_loaded
        True
        >>> registry.current_metadata.version
        'v1.0.0'
        >>>
        >>> # Later, when model is updated in database...
        >>> reloaded = registry.reload_if_changed("alpha_baseline")
        >>> reloaded
        True  # New model loaded
        >>> registry.current_metadata.version
        'v2.0.0'  # Version changed

    Notes:
        - Thread-safe for single-reader (multiple predictions OK)
        - NOT thread-safe for concurrent reload_if_changed() calls
        - Keeps previous model if new model fails to load (graceful degradation)
        - Logs all model load events for auditing

    See Also:
        - /docs/CONCEPTS/model-registry.md for concept explanation
        - /docs/CONCEPTS/hot-reload.md for reload mechanism
    """

    def __init__(self, db_conn_string: str):
        """
        Initialize model registry client.

        Args:
            db_conn_string: Postgres connection string
                Format: postgresql://[user[:password]@][host][:port][/dbname]
                Example: postgresql://postgres:postgres@localhost:5432/trading_platform

        Raises:
            ValueError: If connection string is empty or invalid format

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
        """
        if not db_conn_string:
            raise ValueError("db_conn_string cannot be empty")

        self.db_conn_string = db_conn_string
        self._current_model: lgb.Booster | None = None
        self._current_metadata: ModelMetadata | None = None
        self._last_check: datetime | None = None

        logger.info(
            "ModelRegistry initialized",
            extra={"db": db_conn_string.split("@")[1] if "@" in db_conn_string else "local"},
        )

    def get_active_model_metadata(self, strategy: str = "alpha_baseline") -> ModelMetadata:
        """
        Fetch active model metadata from database.

        Queries the model_registry table for the currently active model for a given
        strategy. There should be at most one active model per strategy.

        Args:
            strategy: Strategy name (e.g., "alpha_baseline", "alpha_v2")

        Returns:
            ModelMetadata for the active model

        Raises:
            ValueError: If no active model found for strategy
            OperationalError: If database connection fails
            DatabaseError: If query fails (table missing, etc.)

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> metadata = registry.get_active_model_metadata("alpha_baseline")
            >>> metadata.version
            'v1.0.0'
            >>> metadata.status
            'active'
            >>> metadata.performance_metrics
            {'ic': 0.082, 'sharpe': 1.45, 'max_drawdown': -0.12}

        Notes:
            - Opens new database connection for each call (connection pooling handled by psycopg)
            - Returns most recently activated model if multiple active (shouldn't happen)
            - Reads from model_registry table (see migrations/001_create_model_registry.sql)

        See Also:
            - migrations/001_create_model_registry.sql for table schema
            - /docs/CONCEPTS/model-registry.md for registry concept
        """
        try:
            with psycopg.connect(self.db_conn_string) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Query for active model
                    # ORDER BY activated_at DESC ensures we get most recent if multiple active
                    cur.execute(
                        """
                        SELECT
                            id, strategy_name, version,
                            mlflow_run_id, mlflow_experiment_id,
                            model_path, status,
                            performance_metrics, config,
                            created_at, activated_at
                        FROM model_registry
                        WHERE strategy_name = %s AND status = 'active'
                        ORDER BY activated_at DESC
                        LIMIT 1
                        """,
                        (strategy,),
                    )
                    row = cur.fetchone()

                    if not row:
                        raise ValueError(
                            f"No active model found for strategy: {strategy}. "
                            f"Check model_registry table: SELECT * FROM model_registry WHERE strategy_name='{strategy}';"
                        )

                    # Convert database row to ModelMetadata dataclass
                    return ModelMetadata(**row)

        except (OperationalError, DatabaseError) as e:
            logger.error(
                f"Database error fetching model metadata: {e}", extra={"strategy": strategy}
            )
            raise

    def load_model_from_file(self, model_path: str) -> lgb.Booster:
        """
        Load LightGBM model from file.

        Loads a LightGBM Booster model from the specified file path. Validates
        that the file exists and is readable before loading.

        Args:
            model_path: Absolute or relative path to model file
                Examples:
                    - "artifacts/models/alpha_baseline.txt"
                    - "/absolute/path/to/model.txt"

        Returns:
            Loaded LightGBM Booster model ready for predictions

        Raises:
            FileNotFoundError: If model file doesn't exist
            ValueError: If file exists but isn't a valid LightGBM model
            OSError: If file permissions prevent reading

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> model = registry.load_model_from_file("artifacts/models/alpha_baseline.txt")
            >>> model.num_trees()
            100
            >>> # Make prediction
            >>> predictions = model.predict([[0.1, 0.2, ...]])  # 158 features

        Notes:
            - LightGBM models are typically 1-10 MB in size
            - Loading takes ~100-500ms depending on model size
            - Model is loaded into memory (not memory-mapped)
            - Loaded model is immutable (cannot be modified)

        See Also:
            - LightGBM Booster documentation: https://lightgbm.readthedocs.io/en/latest/pythonapi/lightgbm.Booster.html
        """
        path = Path(model_path)

        # Validate file exists before attempting load
        if not path.exists():
            raise FileNotFoundError(
                f"Model file not found: {model_path}\n"
                f"Absolute path: {path.resolve()}\n"
                f"Check model_registry.model_path is correct."
            )

        logger.info(f"Loading model from file: {model_path}")

        try:
            # Load model using LightGBM native format
            # This is faster than pickle and more portable
            model = lgb.Booster(model_file=str(path))

            logger.info(
                "Model loaded successfully",
                extra={
                    "path": str(path),
                    "num_trees": model.num_trees(),
                    "num_features": model.num_feature(),
                },
            )

            return model

        except Exception as e:
            logger.error(f"Failed to load model from {model_path}: {e}")
            raise ValueError(f"Invalid LightGBM model file: {model_path}") from e

    def reload_if_changed(self, strategy: str = "alpha_baseline") -> bool:
        """
        Check if active model changed and reload if needed.

        This method:
        1. Fetches latest active model metadata from database
        2. Compares version with currently loaded model
        3. If version changed, loads new model
        4. Updates internal state with new model and metadata
        5. On failure, keeps old model (graceful degradation)

        Args:
            strategy: Strategy name to check for updates

        Returns:
            True if model was reloaded (version changed), False if no change

        Raises:
            ValueError: If no active model found and no model currently loaded
            FileNotFoundError: If new model file doesn't exist
            (Keeps old model on any error if one is loaded)

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>>
            >>> # First call - no model loaded yet
            >>> reloaded = registry.reload_if_changed("alpha_baseline")
            >>> reloaded
            True  # v1.0.0 loaded
            >>>
            >>> # Second call - no change in database
            >>> reloaded = registry.reload_if_changed("alpha_baseline")
            >>> reloaded
            False  # Still v1.0.0, no reload needed
            >>>
            >>> # ... time passes, new model activated in database ...
            >>>
            >>> # Third call - version changed to v2.0.0
            >>> reloaded = registry.reload_if_changed("alpha_baseline")
            >>> reloaded
            True  # v2.0.0 loaded, replaced v1.0.0

        Notes:
            - Call this method periodically (e.g., every 5 minutes) for hot reload
            - Safe to call frequently (only reloads if version changed)
            - Graceful degradation: keeps old model if new model fails to load
            - Thread-safe for single writer (don't call concurrently)
            - Updates _last_check timestamp on every call

        See Also:
            - /docs/CONCEPTS/hot-reload.md for reload mechanism
            - /docs/CONCEPTS/model-registry.md for registry pattern
        """
        try:
            # Fetch latest active model metadata from database
            new_metadata = self.get_active_model_metadata(strategy)

            # Check if model version changed
            version_changed = (
                self._current_metadata is None
                or new_metadata.version != self._current_metadata.version
            )

            if version_changed:
                # Log version change
                old_version = self._current_metadata.version if self._current_metadata else "None"
                logger.info(
                    f"Model version changed: {old_version} -> {new_metadata.version}",
                    extra={
                        "strategy": strategy,
                        "old_version": old_version,
                        "new_version": new_metadata.version,
                    },
                )

                # Load new model from file
                # This may raise FileNotFoundError or ValueError
                new_model = self.load_model_from_file(new_metadata.model_path)

                # Validate model is usable (test prediction)
                # Use dummy input with correct number of features
                try:
                    _ = new_model.predict([[0.0] * new_model.num_feature()])
                except Exception as e:
                    raise ValueError(f"Model prediction test failed: {e}") from e

                # Update state (only after successful load and validation)
                self._current_model = new_model
                self._current_metadata = new_metadata
                self._last_check = datetime.now()

                logger.info(
                    f"Model reloaded successfully: {new_metadata.strategy_name} v{new_metadata.version}",
                    extra={
                        "version": new_metadata.version,
                        "model_path": new_metadata.model_path,
                        "performance_metrics": new_metadata.performance_metrics,
                    },
                )

                return True

            # No version change - update last check time and return False
            self._last_check = datetime.now()
            return False

        except Exception as e:
            logger.error(
                f"Failed to reload model: {e}", extra={"strategy": strategy}, exc_info=True
            )

            # Graceful degradation: keep current model if one is loaded
            if self._current_model is not None and self._current_metadata is not None:
                logger.warning(
                    "Keeping current model after failed reload",
                    extra={"current_version": self._current_metadata.version, "error": str(e)},
                )
                return False

            # No model loaded and reload failed - propagate error
            raise

    @property
    def current_model(self) -> lgb.Booster | None:
        """
        Get currently loaded model.

        Returns:
            LightGBM Booster model if loaded, None otherwise

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> registry.current_model
            None  # No model loaded yet
            >>>
            >>> registry.reload_if_changed("alpha_baseline")
            True
            >>> registry.current_model
            <lightgbm.basic.Booster object at 0x...>
            >>>
            >>> # Make prediction
            >>> predictions = registry.current_model.predict(features)
        """
        return self._current_model

    @property
    def current_metadata(self) -> ModelMetadata | None:
        """
        Get metadata for currently loaded model.

        Returns:
            ModelMetadata if model is loaded, None otherwise

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> registry.reload_if_changed("alpha_baseline")
            True
            >>> registry.current_metadata.version
            'v1.0.0'
            >>> registry.current_metadata.performance_metrics
            {'ic': 0.082, 'sharpe': 1.45, 'max_drawdown': -0.12}
        """
        return self._current_metadata

    @property
    def is_loaded(self) -> bool:
        """
        Check if a model is currently loaded.

        Returns:
            True if model is loaded and ready for predictions, False otherwise

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> registry.is_loaded
            False
            >>> registry.reload_if_changed("alpha_baseline")
            True
            >>> registry.is_loaded
            True
        """
        return self._current_model is not None

    @property
    def last_check(self) -> datetime | None:
        """
        Get timestamp of last reload check.

        Returns:
            Datetime of last reload_if_changed() call, None if never checked

        Example:
            >>> registry = ModelRegistry("postgresql://localhost/trading_platform")
            >>> registry.reload_if_changed("alpha_baseline")
            True
            >>> registry.last_check
            datetime.datetime(2025, 1, 17, 14, 30, 0)

        Notes:
            - Useful for monitoring health (alert if last_check too old)
            - Updated on every reload_if_changed() call (success or failure)
        """
        return self._last_check
