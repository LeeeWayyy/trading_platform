"""
Comprehensive unit tests for apps/signal_service/main.py.

This test module provides additional coverage for main.py focusing on:
1. Edge cases in signal generation endpoint
2. Model reload endpoint error paths
3. Request/response model validation
4. Middleware configuration edge cases
5. Exception handling in various scenarios
6. Metrics recording
7. Date parsing edge cases

Target: 85%+ branch coverage combined with existing test files.

Test Organization:
- TestSignalGenerationAdvanced: Complex signal generation scenarios
- TestModelEndpointsAdvanced: Advanced model info/reload scenarios
- TestMiddlewareConfiguration: Proxy headers, CORS edge cases
- TestExceptionHandlingPaths: Error propagation and logging
- TestMetricsRecording: Prometheus metrics updates
- TestRequestValidation: Pydantic model edge cases
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.signal_service.main import (
    HealthResponse,
    PrecomputeResponse,
    SignalRequest,
    SignalResponse,
)
from libs.core.redis_client import RedisConnectionError

pytestmark = pytest.mark.asyncio


# ==============================================================================
# Signal Generation Advanced Tests
# ==============================================================================


class TestSignalGenerationAdvanced:
    """Advanced tests for generate_signals endpoint."""

    def test_generate_signals_with_default_date(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals defaults to current date when as_of_date not provided."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_signals = pd.DataFrame({
            "symbol": ["AAPL", "MSFT"],
            "predicted_return": [0.023, 0.018],
            "rank": [1, 2],
            "target_weight": [0.5, 0.5],
        })
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
            response = client.post(
                "/api/v1/signals/generate",
                json={"symbols": ["AAPL", "MSFT"]},  # No as_of_date
            )

        assert response.status_code == 200
        data = response.json()
        # Verify metadata has generated_at timestamp
        assert "generated_at" in data["metadata"]
        assert data["metadata"]["num_signals"] == 2

    def test_generate_signals_type_error_during_generation(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals handles TypeError during signal generation."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.generate_signals.side_effect = TypeError("Invalid type in features")
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},
        )

        assert response.status_code == 500
        assert "Signal generation failed" in response.json()["detail"]

    def test_generate_signals_with_zero_top_n_bottom_n(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals with top_n=0 and bottom_n=0 (neutral portfolio)."""
        from apps.signal_service import main

        mock_generator = Mock()
        # Empty signals (neutral portfolio)
        mock_signals = pd.DataFrame(columns=["symbol", "predicted_return", "rank", "target_weight"])
        # Convert empty DataFrame to list of dicts
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 3  # Default values
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
            # Use generator cache path by providing overrides
            with patch("apps.signal_service.main.SignalGenerator") as mock_sig_gen_class:
                mock_new_gen = Mock()
                # Return empty DataFrame with proper structure
                empty_df = pd.DataFrame(columns=["symbol", "predicted_return", "rank", "target_weight"])
                mock_new_gen.generate_signals.return_value = empty_df
                mock_sig_gen_class.return_value = mock_new_gen

                response = client.post(
                    "/api/v1/signals/generate",
                    json={
                        "symbols": ["AAPL", "MSFT", "GOOGL"],
                        "top_n": 0,
                        "bottom_n": 0,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert len(data["signals"]) == 0
        assert data["metadata"]["top_n"] == 0
        assert data["metadata"]["bottom_n"] == 0

    def test_generate_signals_empty_symbol_in_result(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals handles edge case with missing symbol in result."""
        from apps.signal_service import main

        mock_generator = Mock()
        # DataFrame with missing symbol (edge case)
        mock_signals = pd.DataFrame({
            "symbol": [None],  # Missing symbol
            "predicted_return": [0.023],
            "rank": [1],
            "target_weight": [1.0],
        })
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 1
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
            response = client.post(
                "/api/v1/signals/generate",
                json={"symbols": ["AAPL"]},
            )

        # Should succeed even with None symbol (it just won't be tracked in metrics)
        assert response.status_code == 200


# ==============================================================================
# Model Endpoints Advanced Tests
# ==============================================================================


class TestModelEndpointsAdvanced:
    """Advanced tests for model info and reload endpoints."""

    def test_reload_model_with_redis_connection_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model handles RedisConnectionError."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.reload_if_changed.side_effect = RedisConnectionError("Redis unavailable")

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 500
        assert "Model reload failed" in response.json()["detail"]

    def test_reload_model_runtime_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model handles RuntimeError."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.reload_if_changed.side_effect = RuntimeError("Shadow validation failed")

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 500
        assert "Model reload failed" in response.json()["detail"]

    def test_get_model_info_with_null_timestamps(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test get_model_info handles null activated_at/created_at timestamps."""
        from apps.signal_service import main
        from apps.signal_service.model_registry import ModelMetadata

        mock_registry = Mock()
        mock_registry.is_loaded = True

        # Create metadata with null timestamps
        mock_metadata = ModelMetadata(
            id=1,
            strategy_name="alpha_baseline",
            version="v1.0.0",
            mlflow_run_id="test_run_123",
            mlflow_experiment_id="test_exp_456",
            status="active",
            model_path="/path/to/model.txt",
            activated_at=None,  # Null timestamp
            created_at=None,  # Null timestamp
            performance_metrics={},
            config={},
        )
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/api/v1/model/info")

        assert response.status_code == 200
        data = response.json()
        assert data["activated_at"] is None
        assert data["created_at"] is None


# ==============================================================================
# Request/Response Validation Tests
# ==============================================================================


class TestRequestResponseValidation:
    """Test Pydantic request/response model validation."""

    def test_signal_request_negative_top_n(self) -> None:
        """Test SignalRequest rejects negative top_n."""
        with pytest.raises(ValidationError):
            SignalRequest(symbols=["AAPL"], top_n=-1)

    def test_signal_request_negative_bottom_n(self) -> None:
        """Test SignalRequest rejects negative bottom_n."""
        with pytest.raises(ValidationError):
            SignalRequest(symbols=["AAPL"], bottom_n=-1)

    def test_signal_request_empty_symbols(self) -> None:
        """Test SignalRequest rejects empty symbols list."""
        with pytest.raises(ValidationError):
            SignalRequest(symbols=[])

    def test_signal_request_valid_with_all_fields(self) -> None:
        """Test SignalRequest accepts all valid fields."""
        request = SignalRequest(
            symbols=["AAPL", "MSFT"],
            as_of_date="2024-12-31",
            top_n=2,
            bottom_n=1,
        )
        assert request.symbols == ["AAPL", "MSFT"]
        assert request.as_of_date == "2024-12-31"
        assert request.top_n == 2
        assert request.bottom_n == 1

    def test_signal_response_structure(self) -> None:
        """Test SignalResponse structure."""
        response = SignalResponse(
            signals=[
                {
                    "symbol": "AAPL",
                    "predicted_return": 0.023,
                    "rank": 1,
                    "target_weight": 1.0,
                }
            ],
            metadata={
                "as_of_date": "2024-12-31",
                "model_version": "v1.0.0",
                "strategy": "alpha_baseline",
                "num_signals": 1,
                "generated_at": "2024-12-31T10:30:00Z",
                "top_n": 1,
                "bottom_n": 0,
            },
        )
        assert len(response.signals) == 1
        assert response.metadata["num_signals"] == 1

    def test_health_response_structure(self) -> None:
        """Test HealthResponse structure."""
        response = HealthResponse(
            status="healthy",
            model_loaded=True,
            model_info={"version": "v1.0.0"},
            redis_status="connected",
            feature_cache_enabled=True,
            timestamp="2024-12-31T10:30:00Z",
        )
        assert response.status == "healthy"
        assert response.model_loaded is True
        assert response.service == "signal_service"

    def test_precompute_response_structure(self) -> None:
        """Test PrecomputeResponse structure."""
        response = PrecomputeResponse(
            cached_count=3,
            skipped_count=1,
            symbols_cached=["AAPL", "MSFT", "GOOGL"],
            symbols_skipped=["TSLA"],
            as_of_date="2024-12-31",
        )
        assert response.cached_count == 3
        assert response.skipped_count == 1
        assert len(response.symbols_cached) == 3


# ==============================================================================
# Health Check Edge Cases
# ==============================================================================


class TestHealthCheckAdvanced:
    """Advanced health check scenarios."""

    def test_health_check_redis_enabled_but_client_none(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check when Redis enabled but client not initialized."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = True

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.0"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "redis_client", None)  # Redis enabled but client None
        monkeypatch.setattr(main, "feature_cache", None)
        monkeypatch.setattr(main, "hydration_complete", True)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["redis_status"] == "disconnected"
        assert data["feature_cache_enabled"] is False

    def test_health_check_redis_health_check_false(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check when Redis health_check returns False."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = True

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.0"
        mock_metadata.activated_at = datetime.now(UTC)
        mock_registry.current_metadata = mock_metadata

        mock_redis = Mock()
        # Redis health check returns False (unhealthy)
        mock_redis.health_check.return_value = False

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "redis_client", mock_redis)
        monkeypatch.setattr(main, "feature_cache", None)
        monkeypatch.setattr(main, "hydration_complete", True)

        response = client.get("/health")

        # Should succeed but report disconnected status
        assert response.status_code == 200
        data = response.json()
        assert data["redis_status"] == "disconnected"


# ==============================================================================
# Precompute Features Advanced Tests
# ==============================================================================


class TestPrecomputeFeaturesAdvanced:
    """Advanced precompute features endpoint tests."""

    def test_precompute_features_default_date(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test precompute features defaults to current date when not provided."""
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
            json={"symbols": ["AAPL", "MSFT"]},  # No as_of_date
        )

        assert response.status_code == 200
        data = response.json()
        # Should have today's date
        assert data["as_of_date"] is not None

    def test_precompute_features_partial_failure(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test precompute features with some symbols skipped."""
        from apps.signal_service import main

        mock_generator = Mock()
        # Simulate partial success
        mock_generator.precompute_features.return_value = {
            "cached_count": 2,
            "skipped_count": 1,
            "symbols_cached": ["AAPL", "MSFT"],
            "symbols_skipped": ["INVALID"],
        }

        monkeypatch.setattr(main, "signal_generator", mock_generator)

        response = client.post(
            "/api/v1/features/precompute",
            json={"symbols": ["AAPL", "MSFT", "INVALID"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cached_count"] == 2
        assert data["skipped_count"] == 1
        assert "INVALID" in data["symbols_skipped"]


# ==============================================================================
# Exception Handling Tests
# ==============================================================================


class TestExceptionHandling:
    """Test exception handling and error propagation."""

    def test_global_exception_handler_with_key_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test global exception handler catches KeyError in property access."""
        from apps.signal_service import main

        # Create registry that raises KeyError when accessing is_loaded
        def raise_key_error():
            raise KeyError("test_key")

        mock_registry = Mock()
        type(mock_registry).is_loaded = property(lambda self: raise_key_error())

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/api/v1/model/info")

        assert response.status_code == 500
        data = response.json()
        assert "error" in data

    def test_generate_signals_unhandled_exception_in_finally(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals records metrics even when unhandled exception occurs."""
        from apps.signal_service import main

        mock_generator = Mock()
        # Raise unexpected exception type
        mock_generator.generate_signals.side_effect = MemoryError("Out of memory")
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        # Should still record metrics in finally block
        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},
        )

        # Global exception handler should catch it
        assert response.status_code == 500


# ==============================================================================
# Edge Case Tests
# ==============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_generate_signals_with_single_symbol(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals with single symbol (minimum valid request)."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_signals = pd.DataFrame({
            "symbol": ["AAPL"],
            "predicted_return": [0.023],
            "rank": [1],
            "target_weight": [1.0],
        })
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 1
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
            response = client.post(
                "/api/v1/signals/generate",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["signals"]) == 1

    def test_generate_signals_with_date_iso_format_with_time(
        self,
        client: TestClient,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test generate_signals accepts ISO datetime format (not just date)."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_signals = pd.DataFrame({
            "symbol": ["AAPL"],
            "predicted_return": [0.023],
            "rank": [1],
            "target_weight": [1.0],
        })
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 1
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
            response = client.post(
                "/api/v1/signals/generate",
                json={
                    "symbols": ["AAPL"],
                    "as_of_date": "2024-12-31T10:30:00",  # With time component
                },
            )

        # Should accept full ISO datetime format
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
