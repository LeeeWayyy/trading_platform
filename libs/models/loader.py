"""
Production Model Loader for signal_service integration.

This module provides:
- ProductionModelLoader: Load models from registry for signal_service
- Version polling with configurable interval
- Atomic model swap with fallback
- Circuit breaker integration on failures

Key design decisions:
- In-memory cache with 24h TTL for resilience
- Fallback to last-good version on load failure
- Circuit breaker triggers on 3 consecutive failures
- Version polling with 60s default interval
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from libs.models.compatibility import VersionCompatibilityChecker
from libs.models.serialization import (
    ChecksumMismatchError,
    compute_checksum,
    deserialize_model,
)
from libs.models.types import ModelMetadata, ModelType

if TYPE_CHECKING:
    from libs.models.registry import ModelRegistry

logger = logging.getLogger(__name__)


class LoadFailure(Exception):
    """Raised when model loading fails."""

    def __init__(self, model_type: str, version: str | None, cause: str) -> None:
        self.model_type = model_type
        self.version = version
        self.cause = cause
        super().__init__(f"Failed to load {model_type}/{version}: {cause}")


class CircuitBreakerTripped(Exception):
    """Raised when circuit breaker is tripped due to consecutive failures."""

    def __init__(self, model_type: str, failure_count: int) -> None:
        self.model_type = model_type
        self.failure_count = failure_count
        super().__init__(
            f"Circuit breaker tripped for {model_type} after {failure_count} failures"
        )


class CachedModel:
    """In-memory cached model with TTL."""

    def __init__(
        self,
        model: Any,
        metadata: ModelMetadata,
        loaded_at: datetime,
        ttl_hours: int = 24,
    ) -> None:
        self.model = model
        self.metadata = metadata
        self.loaded_at = loaded_at
        self.ttl = timedelta(hours=ttl_hours)

    @property
    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        return datetime.now(UTC) > self.loaded_at + self.ttl


class ProductionModelLoader:
    """Load models from registry for signal_service.

    Features:
    - Version polling with configurable interval
    - Atomic model swap (load -> validate -> swap)
    - Fallback to last-good version on load failure
    - Circuit breaker on consecutive failures (3 failures -> trigger)
    - In-memory cache with 24h TTL

    Example:
        loader = ProductionModelLoader(registry)
        risk_model = loader.get_risk_model()
        alpha_weights = loader.get_alpha_weights()

        # Check compatibility before loading
        is_compatible, warnings = loader.check_compatibility(
            model_id="...",
            current_versions={"crsp": "v1.2.3"},
        )
    """

    DEFAULT_POLL_INTERVAL_SECONDS = 60
    CIRCUIT_BREAKER_THRESHOLD = 3
    CACHE_TTL_HOURS = 24

    def __init__(
        self,
        registry: ModelRegistry,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        compatibility_checker: VersionCompatibilityChecker | None = None,
        on_circuit_breaker_trip: Callable[[str], None] | None = None,
        current_dataset_versions: dict[str, str] | None = None,
        require_version_check: bool = True,
    ) -> None:
        """Initialize loader.

        Args:
            registry: ModelRegistry instance.
            poll_interval_seconds: Interval for version polling.
            compatibility_checker: Optional compatibility checker.
            on_circuit_breaker_trip: Callback when circuit breaker trips.
            current_dataset_versions: Current dataset versions for compatibility checks.
            require_version_check: If True (default), raise error when current_dataset_versions
                is not provided. Set to False only in development/testing environments.

        Raises:
            ValueError: If require_version_check=True and current_dataset_versions is empty.
        """
        self.registry = registry
        self.poll_interval = poll_interval_seconds
        self.compatibility_checker = compatibility_checker or VersionCompatibilityChecker()
        self.on_circuit_breaker_trip = on_circuit_breaker_trip
        self._current_dataset_versions = current_dataset_versions or {}
        self._require_version_check = require_version_check

        # Enforce version check requirement in production
        if not self._current_dataset_versions:
            if require_version_check:
                raise ValueError(
                    "ProductionModelLoader requires current_dataset_versions for compatibility "
                    "checking. Provide dataset versions or set require_version_check=False "
                    "(NOT recommended for production)."
                )
            else:
                logger.warning(
                    "ProductionModelLoader initialized without current_dataset_versions. "
                    "Dataset compatibility checks will be SKIPPED during model loading. "
                    "This is NOT recommended for production use.",
                )

        # In-memory cache {model_type: CachedModel}
        self._cache: dict[str, CachedModel] = {}
        self._cache_lock = threading.RLock()

        # Last-known good versions {model_type: (model, metadata)}
        self._last_good: dict[str, tuple[Any, ModelMetadata]] = {}

        # Failure tracking {model_type: consecutive_failures}
        self._failures: dict[str, int] = {}

        # Polling thread
        self._polling = False
        self._poll_thread: threading.Thread | None = None

    def set_current_dataset_versions(self, versions: dict[str, str]) -> None:
        """Update current dataset versions for compatibility checking.

        Args:
            versions: Current dataset versions dict.
        """
        self._current_dataset_versions = versions
        logger.info(
            "Updated current dataset versions",
            extra={"versions": versions},
        )

    # =========================================================================
    # Model Getters
    # =========================================================================

    def get_risk_model(self, version: str | None = None) -> Any:
        """Get risk model.

        Args:
            version: Specific version or None for production.

        Returns:
            Risk model object.

        Raises:
            LoadFailure: If loading fails.
            CircuitBreakerTripped: If circuit breaker is tripped.
        """
        return self._get_model(ModelType.risk_model.value, version)

    def get_alpha_weights(self, version: str | None = None) -> dict[str, float]:
        """Get alpha weights.

        Args:
            version: Specific version or None for production.

        Returns:
            Alpha weights dict.

        Raises:
            LoadFailure: If loading fails.
            CircuitBreakerTripped: If circuit breaker is tripped.
        """
        result: dict[str, float] = self._get_model(ModelType.alpha_weights.value, version)
        return result

    def get_factor_definitions(self, version: str | None = None) -> dict[str, Any]:
        """Get factor definitions.

        Args:
            version: Specific version or None for production.

        Returns:
            Factor definitions dict.

        Raises:
            LoadFailure: If loading fails.
            CircuitBreakerTripped: If circuit breaker is tripped.
        """
        result: dict[str, Any] = self._get_model(ModelType.factor_definitions.value, version)
        return result

    def get_current_version(self, model_type: ModelType) -> str | None:
        """Get current production version.

        Args:
            model_type: Type of model.

        Returns:
            Version string or None if no production version.
        """
        metadata = self.registry.get_current_production(model_type.value)
        return metadata.version if metadata else None

    # =========================================================================
    # Compatibility
    # =========================================================================

    def check_compatibility(
        self,
        model_id: str,
        current_versions: dict[str, str],
    ) -> tuple[bool, list[str]]:
        """Check if model is compatible with current dataset versions.

        Args:
            model_id: Model ID to check.
            current_versions: Current dataset versions.

        Returns:
            Tuple of (compatible, drift_warnings).
        """
        metadata = self.registry.get_model_by_id(model_id)
        if metadata is None:
            return False, [f"Model {model_id} not found"]

        if self.compatibility_checker is None:
            # No checker configured, allow with warning
            return True, ["No compatibility checker configured"]

        result = self.compatibility_checker.check_compatibility(
            model_versions=metadata.dataset_version_ids,
            current_versions=current_versions,
        )
        return result.compatible, result.warnings

    # =========================================================================
    # Internal Loading
    # =========================================================================

    def _get_model(self, model_type: str, version: str | None) -> Any:
        """Internal model loading with caching and fallback.

        Args:
            model_type: Type of model.
            version: Specific version or None for production.

        Returns:
            Model object.

        Raises:
            LoadFailure: If loading fails.
            CircuitBreakerTripped: If circuit breaker is tripped.
        """
        # Check circuit breaker (read under lock for consistency with mutations)
        with self._cache_lock:
            failure_count = self._failures.get(model_type, 0)
        if failure_count >= self.CIRCUIT_BREAKER_THRESHOLD:
            raise CircuitBreakerTripped(model_type, failure_count)

        # Get version to load
        if version is None:
            metadata = self.registry.get_current_production(model_type)
            if metadata is None:
                raise LoadFailure(model_type, None, "No production version available")
            version = metadata.version

        # Check cache
        cache_key = f"{model_type}/{version}"
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and not cached.is_expired:
                return cached.model

        # Load from registry
        try:
            model, metadata = self._load_from_registry(model_type, version)
            self._record_success(model_type, model, metadata, cache_key)
            return model
        except Exception as e:
            return self._handle_load_failure(model_type, version, e)

    def _load_from_registry(
        self, model_type: str, version: str
    ) -> tuple[Any, ModelMetadata]:
        """Load model from registry with checksum verification and compatibility check.

        Args:
            model_type: Type of model.
            version: Version to load.

        Returns:
            Tuple of (model, metadata).

        Raises:
            FileNotFoundError: If artifact not found.
            ChecksumMismatchError: If checksum fails.
            DeserializationError: If deserialization fails.
            LoadFailure: If version compatibility check fails in strict mode.
        """
        # First, verify metadata against registry DB to prevent serving tampered artifacts
        metadata = self.registry.get_model_metadata(model_type, version)
        if metadata is None:
            raise FileNotFoundError(f"Metadata not found for {model_type}/{version}")

        artifact_path = self.registry.get_artifact_path(model_type, version)
        if artifact_path is None:
            raise FileNotFoundError(f"Artifact path not found for {model_type}/{version}")

        # Validate model file checksum using DB-verified metadata before loading
        model_path = artifact_path / (
            "model.json" if metadata.model_type == ModelType.alpha_weights else "model.pkl"
        )
        if not model_path.exists():
            raise FileNotFoundError(f"Model artifact not found: {model_path}")

        actual_checksum = compute_checksum(model_path)
        if actual_checksum != metadata.checksum_sha256:
            raise ChecksumMismatchError(model_path, metadata.checksum_sha256, actual_checksum)

        # Load model after checksum verification (metadata already validated against DB)
        loaded_model, loaded_metadata = deserialize_model(artifact_path, verify=False)

        # Fail fast if metadata changed between verification and load (TOCTOU guard)
        if loaded_metadata.model_id != metadata.model_id:
            raise LoadFailure(
                model_type,
                version,
                "Metadata changed between verification and load; possible concurrent modification",
            )
        metadata = loaded_metadata

        # Enforce version compatibility check if current versions are set
        if self._current_dataset_versions:
            result = self.compatibility_checker.check_compatibility(
                model_versions=metadata.dataset_version_ids,
                current_versions=self._current_dataset_versions,
            )

            if not result.compatible:
                # Block load on incompatibility
                raise LoadFailure(
                    model_type,
                    version,
                    f"Version compatibility check failed: {'; '.join(result.warnings)}",
                )
            elif result.warnings:
                # Log warnings for drift in non-strict mode
                logger.warning(
                    "Model loaded with version drift warnings",
                    extra={
                        "model_type": model_type,
                        "version": version,
                        "warnings": result.warnings,
                    },
                )

        logger.info(
            "Loaded model from registry",
            extra={
                "model_type": model_type,
                "version": version,
                "model_id": metadata.model_id,
            },
        )

        return model, metadata

    def _record_success(
        self,
        model_type: str,
        model: Any,
        metadata: ModelMetadata,
        cache_key: str,
    ) -> None:
        """Record successful load."""
        now = datetime.now(UTC)

        # All state mutations must be protected by the lock
        # This prevents race conditions between polling thread and request threads
        with self._cache_lock:
            self._cache[cache_key] = CachedModel(
                model=model,
                metadata=metadata,
                loaded_at=now,
                ttl_hours=self.CACHE_TTL_HOURS,
            )

            # Update last-known good
            self._last_good[model_type] = (model, metadata)

            # Reset failure counter
            self._failures[model_type] = 0

    def _handle_load_failure(
        self, model_type: str, version: str | None, error: Exception
    ) -> Any:
        """Handle load failure with fallback.

        Args:
            model_type: Type of model.
            version: Version attempted.
            error: Exception that occurred.

        Returns:
            Fallback model if available.

        Raises:
            LoadFailure: If no fallback available.
            CircuitBreakerTripped: If circuit breaker trips.
        """
        # All state access must be protected by the lock
        # This prevents race conditions between polling thread and request threads
        with self._cache_lock:
            # Increment failure counter
            self._failures[model_type] = self._failures.get(model_type, 0) + 1
            failure_count = self._failures[model_type]

            logger.warning(
                "Model load failed",
                extra={
                    "model_type": model_type,
                    "version": version,
                    "error": str(error),
                    "consecutive_failures": failure_count,
                },
            )

            # Check if circuit breaker should trip
            if failure_count >= self.CIRCUIT_BREAKER_THRESHOLD:
                if self.on_circuit_breaker_trip:
                    self.on_circuit_breaker_trip(model_type)
                raise CircuitBreakerTripped(model_type, failure_count)

            # Try fallback to last-known good
            if model_type in self._last_good:
                model, metadata = self._last_good[model_type]
                logger.info(
                    "Falling back to last-known good model",
                    extra={
                        "model_type": model_type,
                        "fallback_version": metadata.version,
                    },
                )
                return model

        raise LoadFailure(model_type, version, str(error))

    def on_load_failure(self, model_id: str, error: Exception) -> None:
        """Handle load failure callback.

        Args:
            model_id: Model that failed to load.
            error: Exception that occurred.
        """
        logger.error(
            "Model load failure callback",
            extra={"model_id": model_id, "error": str(error)},
        )

    # =========================================================================
    # Polling
    # =========================================================================

    def start_polling(self, model_types: list[ModelType] | None = None) -> None:
        """Start version polling thread.

        Args:
            model_types: Types to poll (defaults to all).
        """
        if self._polling:
            return

        self._polling = True
        types = model_types or list(ModelType)
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(types,),
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Started model polling", extra={"interval": self.poll_interval})

    def stop_polling(self) -> None:
        """Stop version polling thread."""
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
            self._poll_thread = None
        logger.info("Stopped model polling")

    def _poll_loop(self, model_types: list[ModelType]) -> None:
        """Polling loop for version updates."""
        while self._polling:
            for model_type in model_types:
                try:
                    self._check_for_update(model_type)
                except Exception as e:
                    logger.warning(
                        "Polling check failed",
                        extra={"model_type": model_type.value, "error": str(e)},
                    )
            time.sleep(self.poll_interval)

    def _check_for_update(self, model_type: ModelType) -> None:
        """Check if production version has changed.

        Args:
            model_type: Type to check.
        """
        current_prod = self.registry.get_current_production(model_type.value)
        if current_prod is None:
            return

        cache_key = f"{model_type.value}/{current_prod.version}"

        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                # New version available, preload
                logger.info(
                    "New production version detected, preloading",
                    extra={
                        "model_type": model_type.value,
                        "version": current_prod.version,
                    },
                )
                try:
                    self._get_model(model_type.value, current_prod.version)
                except Exception as e:
                    logger.warning(
                        "Preload failed",
                        extra={
                            "model_type": model_type.value,
                            "version": current_prod.version,
                            "error": str(e),
                        },
                    )

    # =========================================================================
    # Cache Management
    # =========================================================================

    def clear_cache(self, model_type: str | None = None) -> int:
        """Clear cached models.

        Args:
            model_type: Type to clear or None for all.

        Returns:
            Number of entries cleared.
        """
        with self._cache_lock:
            if model_type is None:
                count = len(self._cache)
                self._cache.clear()
            else:
                count = 0
                keys_to_remove = [
                    k for k in self._cache if k.startswith(f"{model_type}/")
                ]
                for key in keys_to_remove:
                    del self._cache[key]
                    count += 1

        logger.info("Cleared cache", extra={"model_type": model_type, "count": count})
        return count

    def reset_circuit_breaker(self, model_type: str) -> None:
        """Reset circuit breaker for model type.

        Args:
            model_type: Type to reset.
        """
        # Protect mutation with lock for thread safety
        with self._cache_lock:
            self._failures[model_type] = 0
        logger.info("Reset circuit breaker", extra={"model_type": model_type})
