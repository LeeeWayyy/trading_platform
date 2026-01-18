"""
Comprehensive tests for apps/signal_service/main.py - CRITICAL PATH COVERAGE.

This test module focuses on HIGH-VALUE, LOW-COVERAGE areas of main.py:
1. Application lifespan (startup/shutdown sequences)
2. Generator cache (H8 fix - LRU caching for top_n/bottom_n combinations)
3. Readiness checks vs health checks
4. Precompute features endpoint
5. CORS and middleware configuration
6. Error handling in lifespan initialization
7. Settings accessor edge cases

Coverage targets:
- Lifespan startup paths (lines 481-749)
- Lifespan shutdown paths (lines 791-831)
- Generator cache logic (lines 1686-1721)
- Readiness endpoint (lines 1530-1544)
- Precompute endpoint (lines 1925-2005)
- CORS config (lines 850-887)

See Also:
    - test_main_endpoints.py: Core endpoint tests (generate_signals, model info, reload)
    - test_main_background_tasks.py: Background task tests (reload, hydration)
    - test_main_helpers.py: Helper function tests
    - test_main_integration.py: Golden master integration test
"""

from collections import OrderedDict
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from apps.signal_service.main import (
    _MAX_GENERATOR_CACHE_SIZE,
    PrecomputeRequest,
    app,
    lifespan,
)
from libs.core.redis_client import RedisConnectionError

pytestmark = pytest.mark.asyncio


# ==============================================================================
# Lifespan Tests (Startup/Shutdown Sequences)
# ==============================================================================


class TestLifespanStartup:
    """Test application lifespan startup sequence."""

    async def test_lifespan_success_production_with_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test successful startup in production with model loaded."""
        # Mock all the imports and dependencies
        mock_settings_class = Mock()
        mock_settings = Mock()
        mock_settings.redis_enabled = True
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_db = 0
        mock_settings.redis_ttl = 3600
        mock_settings.default_strategy = "alpha_baseline"
        mock_settings.data_dir = "data/adjusted"
        mock_settings.top_n = 2
        mock_settings.bottom_n = 2
        mock_settings.feature_hydration_enabled = False
        mock_settings.shadow_validation_enabled = False
        mock_settings.testing = False
        mock_settings_class.return_value = mock_settings

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata
        mock_registry.reload_if_changed.return_value = True

        mock_generator = Mock()

        mock_redis = Mock()
        mock_redis.health_check.return_value = True

        # Mock environment
        monkeypatch.setenv("ENVIRONMENT", "production")

        with patch("apps.signal_service.main.Settings", mock_settings_class):
            with patch(
                "apps.signal_service.main.get_required_secret", return_value="postgresql://..."
            ):
                with patch("apps.signal_service.main.validate_required_secrets"):
                    with patch(
                        "apps.signal_service.main.ModelRegistry", return_value=mock_registry
                    ):
                        with patch(
                            "apps.signal_service.main.SignalGenerator", return_value=mock_generator
                        ):
                            with patch(
                                "apps.signal_service.main.get_optional_secret_or_none",
                                return_value=None,
                            ):
                                with patch(
                                    "apps.signal_service.main.RedisClient", return_value=mock_redis
                                ):
                                    with patch("apps.signal_service.main.EventPublisher"):
                                        with patch("apps.signal_service.main.FallbackBuffer"):
                                            with patch("apps.signal_service.main.FeatureCache"):
                                                with patch(
                                                    "asyncio.create_task"
                                                ) as mock_create_task:
                                                    # Mock tasks
                                                    mock_reload_task = AsyncMock()
                                                    mock_redis_task = AsyncMock()
                                                    mock_create_task.side_effect = [
                                                        mock_reload_task,
                                                        mock_redis_task,
                                                    ]

                                                    async with lifespan(app):
                                                        # Verify startup completed
                                                        pass

                                                    # Verify cleanup
                                                    mock_reload_task.cancel.assert_called_once()
                                                    mock_redis_task.cancel.assert_called_once()

    async def test_lifespan_testing_mode_no_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test startup in testing mode when model fails to load."""
        mock_settings_class = Mock()
        mock_settings = Mock()
        mock_settings.redis_enabled = False
        mock_settings.default_strategy = "alpha_baseline"
        mock_settings.data_dir = "data/adjusted"
        mock_settings.feature_hydration_enabled = False
        mock_settings.shadow_validation_enabled = False
        mock_settings.testing = True
        mock_settings_class.return_value = mock_settings

        mock_registry = Mock()
        mock_registry.is_loaded = False
        mock_registry.current_metadata = None
        mock_registry.reload_if_changed.return_value = False

        monkeypatch.setenv("ENVIRONMENT", "test")

        with patch("apps.signal_service.main.Settings", mock_settings_class):
            with patch("apps.signal_service.main.get_optional_secret", return_value=""):
                with patch("apps.signal_service.main.ModelRegistry", return_value=mock_registry):
                    with patch("asyncio.create_task") as mock_create_task:
                        mock_reload_task = AsyncMock()
                        mock_create_task.return_value = mock_reload_task

                        async with lifespan(app):
                            # Verify startup completed in testing mode without model
                            pass

                        mock_reload_task.cancel.assert_called_once()

    async def test_lifespan_production_mode_model_load_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test startup fails in production when model load fails."""
        mock_settings_class = Mock()
        mock_settings = Mock()
        mock_settings.default_strategy = "alpha_baseline"
        mock_settings.redis_enabled = False
        mock_settings.feature_hydration_enabled = False
        mock_settings.shadow_validation_enabled = False
        mock_settings.testing = False
        mock_settings_class.return_value = mock_settings

        mock_registry = Mock()
        mock_registry.is_loaded = False
        mock_registry.reload_if_changed.return_value = False

        monkeypatch.setenv("ENVIRONMENT", "production")

        with patch("apps.signal_service.main.Settings", mock_settings_class):
            with patch(
                "apps.signal_service.main.get_required_secret", return_value="postgresql://..."
            ):
                with patch("apps.signal_service.main.validate_required_secrets"):
                    with patch(
                        "apps.signal_service.main.ModelRegistry", return_value=mock_registry
                    ):
                        with pytest.raises(RuntimeError, match="Failed to load model"):
                            async with lifespan(app):
                                pass

    async def test_lifespan_model_value_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test startup handles ValueError during model load."""
        mock_settings_class = Mock()
        mock_settings = Mock()
        mock_settings.default_strategy = "alpha_baseline"
        mock_settings.redis_enabled = False
        mock_settings.feature_hydration_enabled = False
        mock_settings.shadow_validation_enabled = False
        mock_settings.testing = False
        mock_settings_class.return_value = mock_settings

        mock_registry = Mock()
        mock_registry.reload_if_changed.side_effect = ValueError("Invalid model data")

        monkeypatch.setenv("ENVIRONMENT", "production")

        with patch("apps.signal_service.main.Settings", mock_settings_class):
            with patch(
                "apps.signal_service.main.get_required_secret", return_value="postgresql://..."
            ):
                with patch("apps.signal_service.main.validate_required_secrets"):
                    with patch(
                        "apps.signal_service.main.ModelRegistry", return_value=mock_registry
                    ):
                        with pytest.raises(RuntimeError, match="Failed to load model"):
                            async with lifespan(app):
                                pass

    async def test_lifespan_shutdown_cleanup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test lifespan shutdown cleans up resources."""
        mock_settings_class = Mock()
        mock_settings = Mock()
        mock_settings.redis_enabled = True
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_db = 0
        mock_settings.redis_ttl = 3600
        mock_settings.default_strategy = "alpha_baseline"
        mock_settings.data_dir = "data/adjusted"
        mock_settings.top_n = 2
        mock_settings.bottom_n = 2
        mock_settings.feature_hydration_enabled = True
        mock_settings.shadow_validation_enabled = False
        mock_settings.testing = False
        mock_settings.tradable_symbols = ["AAPL", "MSFT"]
        mock_settings_class.return_value = mock_settings

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata
        mock_registry.reload_if_changed.return_value = True
        mock_registry.close = Mock()

        mock_generator = Mock()

        mock_redis = Mock()
        mock_redis.health_check.return_value = True
        mock_redis.close = Mock()

        monkeypatch.setenv("ENVIRONMENT", "production")

        with patch("apps.signal_service.main.Settings", mock_settings_class):
            with patch(
                "apps.signal_service.main.get_required_secret", return_value="postgresql://..."
            ):
                with patch("apps.signal_service.main.validate_required_secrets"):
                    with patch(
                        "apps.signal_service.main.ModelRegistry", return_value=mock_registry
                    ):
                        with patch(
                            "apps.signal_service.main.SignalGenerator", return_value=mock_generator
                        ):
                            with patch(
                                "apps.signal_service.main.get_optional_secret_or_none",
                                return_value=None,
                            ):
                                with patch(
                                    "apps.signal_service.main.RedisClient", return_value=mock_redis
                                ):
                                    with patch("apps.signal_service.main.EventPublisher"):
                                        with patch("apps.signal_service.main.FallbackBuffer"):
                                            with patch("apps.signal_service.main.FeatureCache"):
                                                with patch(
                                                    "asyncio.create_task"
                                                ) as mock_create_task:
                                                    with patch(
                                                        "apps.signal_service.main.close_secret_manager"
                                                    ) as mock_close_secrets:
                                                        # Mock tasks
                                                        mock_reload_task = AsyncMock()
                                                        mock_redis_task = AsyncMock()
                                                        mock_hydration_task = AsyncMock()
                                                        mock_create_task.side_effect = [
                                                            mock_hydration_task,
                                                            mock_reload_task,
                                                            mock_redis_task,
                                                        ]

                                                        async with lifespan(app):
                                                            pass

                                                        # Verify cleanup
                                                        mock_reload_task.cancel.assert_called_once()
                                                        mock_redis_task.cancel.assert_called_once()
                                                        mock_hydration_task.cancel.assert_called_once()
                                                        mock_redis.close.assert_called_once()
                                                        mock_registry.close.assert_called_once()
                                                        mock_close_secrets.assert_called_once()


# ==============================================================================
# Generator Cache Tests (H8 Fix - LRU Cache)
# ==============================================================================


class TestGeneratorCache:
    """Test SignalGenerator cache for top_n/bottom_n combinations (H8 fix)."""

    async def test_generator_cache_hit(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generator cache hit when using same top_n/bottom_n."""
        from apps.signal_service import main

        # Create cached generator
        cached_gen = Mock()
        cached_gen.generate_signals.return_value = pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "predicted_return": [0.023],
                "rank": [1],
                "target_weight": [1.0],
            }
        )

        # Pre-populate cache
        cache_key = (1, 0)
        test_cache = OrderedDict()
        test_cache[cache_key] = cached_gen

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "_generator_cache", test_cache)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "signal_generator", Mock(top_n=2, bottom_n=0))

        with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
            response = client.post(
                "/api/v1/signals/generate",
                json={
                    "symbols": ["AAPL"],
                    "top_n": 1,
                    "bottom_n": 0,
                },
            )

        assert response.status_code == 200
        cached_gen.generate_signals.assert_called_once()

    async def test_generator_cache_miss_and_create(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generator cache miss creates new cached generator."""
        from apps.signal_service import main

        test_cache = OrderedDict()

        mock_generator = Mock()
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0
        mock_generator.data_provider = Mock()
        mock_generator.data_provider.data_dir = "data/adjusted"
        mock_generator.generate_signals.return_value = pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "predicted_return": [0.023],
                "rank": [1],
                "target_weight": [1.0],
            }
        )

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "_generator_cache", test_cache)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "signal_generator", mock_generator)

        with patch("apps.signal_service.main.SignalGenerator") as mock_sig_gen_class:
            mock_new_gen = Mock()
            mock_new_gen.generate_signals.return_value = pd.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "predicted_return": [0.023],
                    "rank": [1],
                    "target_weight": [1.0],
                }
            )
            mock_sig_gen_class.return_value = mock_new_gen

            with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
                response = client.post(
                    "/api/v1/signals/generate",
                    json={
                        "symbols": ["AAPL"],
                        "top_n": 1,
                        "bottom_n": 0,
                    },
                )

        assert response.status_code == 200
        # Verify cache was populated
        assert (1, 0) in test_cache

    async def test_generator_cache_lru_eviction(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generator cache evicts LRU entry when full."""
        from apps.signal_service import main

        # Fill cache to capacity
        test_cache = OrderedDict()
        for i in range(_MAX_GENERATOR_CACHE_SIZE):
            test_cache[(i, 0)] = Mock()

        mock_generator = Mock()
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0
        mock_generator.data_provider = Mock()
        mock_generator.data_provider.data_dir = "data/adjusted"

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "_generator_cache", test_cache)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "signal_generator", mock_generator)

        # Verify cache is full
        assert len(test_cache) == _MAX_GENERATOR_CACHE_SIZE

        with patch("apps.signal_service.main.SignalGenerator") as mock_sig_gen_class:
            mock_new_gen = Mock()
            mock_new_gen.generate_signals.return_value = pd.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "predicted_return": [0.023],
                    "rank": [1],
                    "target_weight": [1.0],
                }
            )
            mock_sig_gen_class.return_value = mock_new_gen

            with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
                response = client.post(
                    "/api/v1/signals/generate",
                    json={
                        "symbols": ["AAPL"],
                        "top_n": 99,  # New key not in cache
                        "bottom_n": 0,
                    },
                )

        assert response.status_code == 200
        # Verify oldest entry was evicted
        assert (0, 0) not in test_cache
        # Verify new entry was added
        assert (99, 0) in test_cache
        # Verify cache size maintained
        assert len(test_cache) == _MAX_GENERATOR_CACHE_SIZE


# ==============================================================================
# Readiness Check Tests
# ==============================================================================


class TestReadinessCheck:
    """Test /ready endpoint (differs from /health during hydration)."""

    def test_readiness_check_healthy(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test readiness check returns 200 when fully healthy."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = False

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.0"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "hydration_complete", True)

        response = client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_readiness_check_degraded_hydration(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test readiness check returns 503 when hydration incomplete."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = True
        mock_settings.feature_hydration_enabled = True

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.0"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata

        mock_feature_cache = Mock()
        mock_generator = Mock()

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "feature_cache", mock_feature_cache)
        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "hydration_complete", False)

        response = client.get("/ready")

        assert response.status_code == 503
        assert "not ready" in response.json()["detail"]


# ==============================================================================
# Precompute Features Endpoint Tests
# ==============================================================================


class TestPrecomputeFeaturesEndpoint:
    """Test /api/v1/features/precompute endpoint (M5 fix)."""

    def test_precompute_features_success(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test precompute endpoint successfully caches features."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.precompute_features.return_value = {
            "cached_count": 3,
            "skipped_count": 0,
            "symbols_cached": ["AAPL", "MSFT", "GOOGL"],
            "symbols_skipped": [],
        }

        monkeypatch.setattr(main, "signal_generator", mock_generator)

        response = client.post(
            "/api/v1/features/precompute",
            json={
                "symbols": ["AAPL", "MSFT", "GOOGL"],
                "as_of_date": "2024-12-31",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cached_count"] == 3
        assert data["skipped_count"] == 0
        assert "AAPL" in data["symbols_cached"]

    def test_precompute_features_signal_generator_not_initialized(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test precompute fails when signal_generator is None."""
        from apps.signal_service import main

        monkeypatch.setattr(main, "signal_generator", None)

        response = client.post(
            "/api/v1/features/precompute",
            json={
                "symbols": ["AAPL"],
            },
        )

        assert response.status_code == 503
        assert "Signal generator not initialized" in response.json()["detail"]

    def test_precompute_features_invalid_date(
        self,
        client: TestClient,
        mock_signal_generator: Mock,
    ) -> None:
        """Test precompute rejects invalid date format."""
        response = client.post(
            "/api/v1/features/precompute",
            json={
                "symbols": ["AAPL"],
                "as_of_date": "invalid-date",
            },
        )

        assert response.status_code == 400
        assert "Invalid date format" in response.json()["detail"]

    def test_precompute_features_uppercase_symbols(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test precompute normalizes symbols to uppercase."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.precompute_features.return_value = {
            "cached_count": 2,
            "skipped_count": 0,
            "symbols_cached": ["AAPL", "MSFT"],
            "symbols_skipped": [],
        }

        monkeypatch.setattr(main, "signal_generator", mock_generator)

        response = client.post(
            "/api/v1/features/precompute",
            json={
                "symbols": ["aapl", "msft"],  # Lowercase
            },
        )

        assert response.status_code == 200
        # Verify precompute_features was called with uppercase
        call_kwargs = mock_generator.precompute_features.call_args.kwargs
        assert call_kwargs["symbols"] == ["AAPL", "MSFT"]


# ==============================================================================
# CORS Configuration Tests
# ==============================================================================


class TestCORSConfiguration:
    """Test CORS middleware configuration (C6 fix)."""

    def test_cors_wildcard_rejected_in_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test CORS rejects wildcard '*' in ALLOWED_ORIGINS."""
        monkeypatch.setenv("ALLOWED_ORIGINS", "*")

        # Need to reload the module to re-evaluate CORS config
        # Import will trigger CORS middleware setup
        import importlib

        from apps.signal_service import main

        with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS cannot contain wildcard"):
            importlib.reload(main)

    def test_cors_production_requires_explicit_origins(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test CORS requires explicit ALLOWED_ORIGINS in production."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

        import importlib

        from apps.signal_service import main

        with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must be set for production"):
            importlib.reload(main)


# ==============================================================================
# Health Check Edge Cases (Hydration Status)
# ==============================================================================


class TestHealthCheckHydration:
    """Test health check degraded status during feature hydration."""

    def test_health_check_degraded_during_hydration(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check returns degraded during feature hydration."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = True
        mock_settings.feature_hydration_enabled = True

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.0"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata

        mock_feature_cache = Mock()
        mock_generator = Mock()

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "feature_cache", mock_feature_cache)
        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "hydration_complete", False)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"

    def test_health_check_healthy_after_hydration(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check returns healthy after hydration completes."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = True
        mock_settings.feature_hydration_enabled = True

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.0"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata

        mock_feature_cache = Mock()
        mock_generator = Mock()

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "feature_cache", mock_feature_cache)
        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "hydration_complete", True)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


# ==============================================================================
# Request Model Validation Tests
# ==============================================================================


class TestPrecomputeRequestValidation:
    """Test PrecomputeRequest validation."""

    def test_precompute_request_valid(self) -> None:
        """Test valid PrecomputeRequest."""
        request = PrecomputeRequest(
            symbols=["AAPL", "MSFT"],
            as_of_date="2024-12-31",
        )
        assert request.symbols == ["AAPL", "MSFT"]
        assert request.as_of_date == "2024-12-31"

    def test_precompute_request_min_length_validation(self) -> None:
        """Test PrecomputeRequest rejects empty symbols list."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PrecomputeRequest(symbols=[])


# ==============================================================================
# Generate Signals with Redis Fallback Tests
# ==============================================================================


class TestGenerateSignalsRedisHandling:
    """Test generate_signals handles Redis connection errors gracefully."""

    def test_generate_signals_redis_error_during_generation(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals handles RedisConnectionError."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.generate_signals.side_effect = RedisConnectionError("Redis down")
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},
        )

        assert response.status_code == 500
        assert "Signal generation failed" in response.json()["detail"]


# ==============================================================================
# Settings Initialization Tests
# ==============================================================================


class TestSettingsInitialization:
    """Test Settings initialization in different environments."""

    async def test_lifespan_dev_mode_fallback_to_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test lifespan uses Settings defaults in dev mode when secret unavailable."""
        mock_settings_class = Mock()
        mock_settings = Mock()
        mock_settings.default_strategy = "alpha_baseline"
        mock_settings.redis_enabled = False
        mock_settings.feature_hydration_enabled = False
        mock_settings.shadow_validation_enabled = False
        mock_settings.testing = False
        mock_settings_class.return_value = mock_settings

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata
        mock_registry.reload_if_changed.return_value = True

        monkeypatch.setenv("ENVIRONMENT", "dev")

        with patch("apps.signal_service.main.Settings", mock_settings_class):
            with patch("apps.signal_service.main.get_optional_secret", return_value=""):
                with patch("apps.signal_service.main.ModelRegistry", return_value=mock_registry):
                    with patch("asyncio.create_task") as mock_create_task:
                        mock_reload_task = AsyncMock()
                        mock_create_task.return_value = mock_reload_task

                        async with lifespan(app):
                            # Verify startup completed with defaults
                            pass

                        mock_reload_task.cancel.assert_called_once()


# ==============================================================================
# Payload Publishing Tests
# ==============================================================================


class TestSignalEventPublishing:
    """Test signal event publishing with fallback."""

    def test_generate_signals_publishes_event(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals publishes signal event."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "predicted_return": [0.023, 0.018],
                "rank": [1, 2],
                "target_weight": [0.5, 0.5],
            }
        )
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        mock_publish = Mock()

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "_publish_signal_event_with_fallback", mock_publish)

        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT"]},
        )

        assert response.status_code == 200
        # Verify event was published
        mock_publish.assert_called_once()
        published_event = mock_publish.call_args[0][0]
        assert published_event.strategy_id == "alpha_baseline"
        assert set(published_event.symbols) == {"AAPL", "MSFT"}
        assert published_event.num_signals == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
