"""
Comprehensive tests for signal_service/main.py API endpoints.

Tests coverage for:
- generate_signals() endpoint (lines 1635-1885)
- get_model_info() endpoint (lines 2048-2062)
- reload_model() endpoint (lines 2132-2233)
- health_check() edge cases (lines 1460-1509)
- root() endpoint (line 1403)
- Global exception handler (lines 1369-1371)
- Request validators (lines 1252-1262)
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.signal_service.main import (
    SignalRequest,
    get_settings,
)


class TestRootEndpoint:
    """Test the root (/) endpoint."""

    def test_root_returns_service_info(self, client: TestClient) -> None:
        """Test root endpoint returns service information."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()

        assert data["service"] == "Signal Service"
        assert data["version"] == "1.0.0"
        assert data["docs"] == "/docs"
        assert data["health"] == "/health"
        assert data["api"] == "/api/v1"


class TestHealthCheckEdgeCases:
    """Test health_check() edge cases for testing mode."""

    def test_health_check_testing_mode_no_model(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check in testing mode when model not loaded."""
        from apps.signal_service import main

        # Override settings for this test
        mock_settings = Mock()
        mock_settings.testing = True
        mock_settings.redis_enabled = False
        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", None)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["model_loaded"] is False
        assert data["model_info"] is None
        assert data["redis_status"] == "disabled"
        assert data["feature_cache_enabled"] is False

    def test_health_check_testing_mode_model_not_loaded(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check in testing mode when model registry exists but not loaded."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = True
        mock_settings.redis_enabled = True  # Redis enabled but disconnected

        mock_registry = Mock()
        mock_registry.is_loaded = False

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "redis_client", None)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["model_loaded"] is False
        assert data["model_info"] is None
        assert data["redis_status"] == "disconnected"

    def test_health_check_testing_mode_metadata_null(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check in testing mode when metadata is None despite is_loaded=True."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = True
        mock_settings.redis_enabled = False

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_registry.current_metadata = None  # Edge case: loaded but no metadata

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["model_loaded"] is False
        assert data["model_info"] is None

    def test_health_check_production_mode_no_model_fails(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check in production mode fails when model not loaded."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", None)

        response = client.get("/health")
        assert response.status_code == 503
        assert "Model not loaded" in response.json()["detail"]

    def test_health_check_production_mode_metadata_null_fails(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test health check in production mode fails when metadata is None."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_registry.current_metadata = None  # Edge case

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/health")
        assert response.status_code == 503
        assert "metadata not available" in response.json()["detail"]

    def test_health_check_redis_disconnected(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_model_registry: Mock,
    ) -> None:
        """Test health check when Redis is enabled but disconnected."""
        from apps.signal_service import main

        mock_settings = Mock()
        mock_settings.testing = False
        mock_settings.redis_enabled = True

        mock_redis = Mock()
        mock_redis.health_check.return_value = False  # Disconnected

        monkeypatch.setattr(main, "settings", mock_settings)
        monkeypatch.setattr(main, "redis_client", mock_redis)
        monkeypatch.setattr(main, "model_registry", mock_model_registry)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["redis_status"] == "disconnected"


class TestSignalRequestValidators:
    """Test SignalRequest validators."""

    def test_validate_date_invalid_format(self) -> None:
        """Test date validator rejects invalid format."""
        with pytest.raises(ValidationError) as exc_info:
            SignalRequest(symbols=["AAPL"], as_of_date="invalid-date")

        errors = exc_info.value.errors()
        assert any("as_of_date" in str(e) for e in errors)

    def test_validate_date_valid_format(self) -> None:
        """Test date validator accepts valid ISO format."""
        request = SignalRequest(symbols=["AAPL"], as_of_date="2024-12-31")
        assert request.as_of_date == "2024-12-31"

    def test_validate_symbols_uppercase_conversion(self) -> None:
        """Test symbols validator converts to uppercase."""
        request = SignalRequest(symbols=["aapl", "msft"])
        assert request.symbols == ["AAPL", "MSFT"]


class TestGenerateSignalsEndpoint:
    """Comprehensive tests for generate_signals() endpoint."""

    def test_generate_signals_success(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test successful signal generation."""
        # Create mock signal generator
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

        # Create mock model registry
        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.0"
        mock_metadata.strategy_name = "alpha_baseline"
        mock_registry.current_metadata = mock_metadata

        with patch("apps.signal_service.main.signal_generator", mock_generator):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
                    response = client.post(
                        "/api/v1/signals/generate",
                        json={"symbols": ["AAPL", "MSFT"]},
                    )

        assert response.status_code == 200
        data = response.json()
        assert len(data["signals"]) == 2
        assert data["metadata"]["model_version"] == "v1.0.0"
        assert data["metadata"]["strategy"] == "alpha_baseline"

    def test_generate_signals_signal_generator_not_initialized(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals fails when signal_generator is None."""
        with patch("apps.signal_service.main.signal_generator", None):
            response = client.post(
                "/api/v1/signals/generate",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 503
        assert "Signal generator not initialized" in response.json()["detail"]

    def test_generate_signals_model_registry_not_initialized(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals fails when model_registry is None."""
        mock_generator = Mock()

        with patch("apps.signal_service.main.signal_generator", mock_generator):
            with patch("apps.signal_service.main.model_registry", None):
                response = client.post(
                    "/api/v1/signals/generate",
                    json={"symbols": ["AAPL"]},
                )

        assert response.status_code == 503
        assert "Model registry not initialized" in response.json()["detail"]

    def test_generate_signals_model_not_loaded(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals fails when model not loaded."""
        mock_generator = Mock()
        mock_registry = Mock()
        mock_registry.is_loaded = False

        with patch("apps.signal_service.main.signal_generator", mock_generator):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                response = client.post(
                    "/api/v1/signals/generate",
                    json={"symbols": ["AAPL"]},
                )

        assert response.status_code == 503
        assert "Model not loaded" in response.json()["detail"]

    def test_generate_signals_invalid_date_format(
        self,
        client: TestClient,
        mock_model_registry: Mock,
        mock_signal_generator: Mock,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals rejects invalid date format."""
        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL"], "as_of_date": "invalid-date"},
        )

        # Pydantic validation error from request model
        assert response.status_code == 422

    def test_generate_signals_too_many_positions_requested(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals fails when top_n + bottom_n > num_symbols."""
        mock_generator = Mock()
        mock_generator.top_n = 5
        mock_generator.bottom_n = 5

        mock_registry = Mock()
        mock_registry.is_loaded = True

        with patch("apps.signal_service.main.signal_generator", mock_generator):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                response = client.post(
                    "/api/v1/signals/generate",
                    json={"symbols": ["AAPL", "MSFT"]},  # Only 2 symbols, need 10
                )

        assert response.status_code == 400
        assert "Cannot select" in response.json()["detail"]

    def test_generate_signals_data_not_found(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals returns 404 when data file not found."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.generate_signals.side_effect = FileNotFoundError("Data file not found")
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},  # Enough symbols
        )

        assert response.status_code == 404
        assert "Data not found" in response.json()["detail"]

    def test_generate_signals_value_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals returns 400 on ValueError."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.generate_signals.side_effect = ValueError("Invalid symbol")
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},  # Enough symbols
        )

        assert response.status_code == 400
        assert "Invalid" in response.json()["detail"]

    def test_generate_signals_key_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals returns 500 on KeyError."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.generate_signals.side_effect = KeyError("missing_column")
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
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},  # Enough symbols
        )

        assert response.status_code == 500
        assert "Signal generation failed" in response.json()["detail"]

    def test_generate_signals_os_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals returns 500 on OSError."""
        from apps.signal_service import main

        mock_generator = Mock()
        mock_generator.generate_signals.side_effect = OSError("Disk error")
        mock_generator.top_n = 2
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post(
            "/api/v1/signals/generate",
            json={"symbols": ["AAPL", "MSFT", "GOOGL"]},  # Enough symbols
        )

        assert response.status_code == 500
        assert "Signal generation failed" in response.json()["detail"]

    def test_generate_signals_with_override_top_n_bottom_n(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals with overridden top_n/bottom_n parameters."""
        mock_generator = Mock()
        mock_signals = pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "predicted_return": [0.023],
                "rank": [1],
                "target_weight": [1.0],
            }
        )
        mock_generator.generate_signals.return_value = mock_signals
        mock_generator.top_n = 3  # Default
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        with patch("apps.signal_service.main.signal_generator", mock_generator):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
                    response = client.post(
                        "/api/v1/signals/generate",
                        json={
                            "symbols": ["AAPL", "MSFT", "GOOGL"],
                            "top_n": 1,  # Override
                            "bottom_n": 0,
                        },
                    )

        assert response.status_code == 200

    def test_generate_signals_non_string_dict_keys(
        self,
        client: TestClient,
        mock_auth_context: Mock,
    ) -> None:
        """Test generate_signals handles non-string dict keys error."""
        mock_generator = Mock()
        # Simulate DataFrame with non-string column names (edge case)
        bad_signals = pd.DataFrame(
            {
                1: ["AAPL"],  # Integer column name
                "predicted_return": [0.023],
            }
        )
        mock_generator.generate_signals.return_value = bad_signals
        mock_generator.top_n = 1
        mock_generator.bottom_n = 0

        mock_registry = Mock()
        mock_registry.is_loaded = True

        with patch("apps.signal_service.main.signal_generator", mock_generator):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with patch("apps.signal_service.main._publish_signal_event_with_fallback"):
                    response = client.post(
                        "/api/v1/signals/generate",
                        json={"symbols": ["AAPL"]},
                    )

        assert response.status_code == 500
        assert "non-string keys" in response.json()["detail"]


class TestGetModelInfoEndpoint:
    """Test get_model_info() endpoint."""

    def test_get_model_info_success(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test get_model_info returns model metadata."""

        from apps.signal_service import main
        from apps.signal_service.model_registry import ModelMetadata

        mock_registry = Mock()
        mock_registry.is_loaded = True
        # Use real ModelMetadata for proper serialization
        mock_metadata = ModelMetadata(
            id=1,
            strategy_name="alpha_baseline",
            version="v1.0.0",
            mlflow_run_id="test_run_123",
            mlflow_experiment_id="test_exp_456",
            status="active",
            model_path="/path/to/model.txt",
            activated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            performance_metrics={"ic": 0.082},
            config={},
        )
        mock_registry.current_metadata = mock_metadata

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/api/v1/model/info")

        assert response.status_code == 200
        data = response.json()
        assert data["strategy_name"] == "alpha_baseline"
        assert data["version"] == "v1.0.0"
        assert data["status"] == "active"

    def test_get_model_info_model_not_loaded(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test get_model_info fails when model not loaded."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.is_loaded = False

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/api/v1/model/info")

        assert response.status_code == 503
        assert "Model not loaded" in response.json()["detail"]

    def test_get_model_info_metadata_null(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test get_model_info fails when metadata is None."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_registry.current_metadata = None

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/api/v1/model/info")

        assert response.status_code == 503
        assert "metadata not available" in response.json()["detail"]


class TestReloadModelEndpoint:
    """Test reload_model() endpoint."""

    def test_reload_model_success_reloaded(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model when model version changed."""

        from apps.signal_service import main
        from apps.signal_service.model_registry import ModelMetadata

        mock_registry = Mock()
        mock_registry.reload_if_changed.return_value = True
        # Use real ModelMetadata for proper serialization
        mock_metadata_new = ModelMetadata(
            id=2,
            strategy_name="alpha_baseline",
            version="v1.0.1",
            mlflow_run_id="test_run_456",
            mlflow_experiment_id="test_exp_456",
            status="active",
            model_path="/path/to/model_new.txt",
            activated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            performance_metrics={"ic": 0.085},
            config={},
        )
        mock_registry.current_metadata = mock_metadata_new
        mock_registry.pending_validation = False

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 200
        data = response.json()
        assert data["reloaded"] is True
        assert data["version"] == "v1.0.1"
        assert "reloaded successfully" in data["message"]

    def test_reload_model_no_changes(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model when no changes detected."""

        from apps.signal_service import main
        from apps.signal_service.model_registry import ModelMetadata

        mock_registry = Mock()
        mock_registry.reload_if_changed.return_value = False
        # Provide real metadata for current version
        mock_metadata = ModelMetadata(
            id=1,
            strategy_name="alpha_baseline",
            version="v1.0.0",
            mlflow_run_id="test_run_123",
            mlflow_experiment_id="test_exp_456",
            status="active",
            model_path="/path/to/model.txt",
            activated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            performance_metrics={},
            config={},
        )
        mock_registry.current_metadata = mock_metadata
        mock_registry.pending_validation = False

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 200
        data = response.json()
        assert data["reloaded"] is False
        assert "already up to date" in data["message"]

    def test_reload_model_pending_validation(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model during shadow validation."""

        from apps.signal_service import main
        from apps.signal_service.model_registry import ModelMetadata

        mock_registry = Mock()
        mock_registry.reload_if_changed.return_value = False
        # Provide real metadata
        mock_metadata = ModelMetadata(
            id=1,
            strategy_name="alpha_baseline",
            version="v1.0.0",
            mlflow_run_id="test_run_123",
            mlflow_experiment_id="test_exp_456",
            status="active",
            model_path="/path/to/model.txt",
            activated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            performance_metrics={},
            config={},
        )
        mock_registry.current_metadata = mock_metadata
        mock_registry.pending_validation = True

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 200
        data = response.json()
        assert data["pending_validation"] is True
        assert "Shadow validation in progress" in data["message"]

    def test_reload_model_registry_not_initialized(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model fails when registry not initialized."""
        from apps.signal_service import main

        monkeypatch.setattr(main, "model_registry", None)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 503
        assert "Model registry not initialized" in response.json()["detail"]

    def test_reload_model_value_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model handles ValueError."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.reload_if_changed.side_effect = ValueError("Invalid model data")

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 500
        assert "Model reload failed" in response.json()["detail"]

    def test_reload_model_file_not_found_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model handles FileNotFoundError."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.reload_if_changed.side_effect = FileNotFoundError("Model file missing")

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 500
        assert "Model reload failed" in response.json()["detail"]

    def test_reload_model_os_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test reload_model handles OSError."""
        from apps.signal_service import main

        mock_registry = Mock()
        mock_registry.reload_if_changed.side_effect = OSError("Disk error")

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.post("/api/v1/model/reload")

        assert response.status_code == 500
        assert "Model reload failed" in response.json()["detail"]


class TestGlobalExceptionHandler:
    """Test global_exception_handler for unexpected errors."""

    def test_global_exception_handler(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test global exception handler catches unhandled exceptions."""
        from apps.signal_service import main

        # Create a registry that raises RuntimeError when accessed
        def raise_error(*args, **kwargs):
            raise RuntimeError("Unexpected error")

        mock_registry = Mock()
        mock_registry.is_loaded = property(raise_error)

        monkeypatch.setattr(main, "model_registry", mock_registry)

        response = client.get("/api/v1/model/info")

        assert response.status_code == 500
        data = response.json()
        assert "Internal server error" in data["error"] or "Unexpected error" in str(data)


class TestGetSettingsAccessor:
    """Test get_settings() accessor function."""

    def test_get_settings_before_lifespan_raises_error(self) -> None:
        """Test get_settings raises RuntimeError when called before lifespan."""
        with patch("apps.signal_service.main.settings", None):
            with pytest.raises(RuntimeError) as exc_info:
                get_settings()

            assert "Settings not initialized" in str(exc_info.value)

    def test_get_settings_after_lifespan_returns_settings(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test get_settings returns settings after lifespan initialization."""
        with patch("apps.signal_service.main.settings", mock_settings):
            result = get_settings()
            assert result == mock_settings
