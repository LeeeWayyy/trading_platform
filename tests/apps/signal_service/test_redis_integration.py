"""
Integration tests for Signal Service Redis integration (T1.2).

Tests cover:
- SignalGenerator with feature caching
- Cache hits and misses
- Graceful degradation without Redis
- Health endpoint Redis status
"""

from datetime import datetime
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from apps.signal_service.model_registry import ModelRegistry
from apps.signal_service.signal_generator import SignalGenerator
from libs.core.redis_client import FeatureCache


@pytest.fixture()
def mock_model_registry():
    """Mock ModelRegistry with loaded model."""
    registry = Mock(spec=ModelRegistry)
    registry.is_loaded = True

    # Mock metadata
    metadata = Mock()
    metadata.strategy_name = "alpha_baseline"
    metadata.version = "v1.0.0"
    metadata.activated_at = datetime(2024, 1, 1)
    registry.current_metadata = metadata

    # Mock model - returns predictions matching input length
    model = Mock()

    def predict_side_effect(features):
        # Return predictions matching the number of samples
        n_samples = features.shape[0] if hasattr(features, "shape") else len(features)
        return np.random.randn(n_samples) * 0.02  # Random predictions ~2% std

    model.predict = Mock(side_effect=predict_side_effect)
    registry.current_model = model

    return registry


@pytest.fixture()
def mock_feature_cache():
    """Mock FeatureCache for testing."""
    cache = Mock(spec=FeatureCache)
    cache.get = Mock(return_value=None)  # Default to cache miss
    cache.set = Mock()
    return cache


@pytest.fixture()
def test_data_dir(tmp_path):
    """Create temporary data directory with test data."""
    data_dir = tmp_path / "data" / "adjusted"
    data_dir.mkdir(parents=True)
    return data_dir


class TestSignalGeneratorCaching:
    """Tests for SignalGenerator feature caching."""

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_initialization_without_cache(self, mock_features, mock_model_registry, test_data_dir):
        """Test SignalGenerator initializes without feature cache."""
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=2,
            bottom_n=2,
            feature_cache=None,
        )

        assert generator.feature_cache is None
        assert generator.top_n == 2
        assert generator.bottom_n == 2

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_initialization_with_cache(
        self, mock_features, mock_model_registry, test_data_dir, mock_feature_cache
    ):
        """Test SignalGenerator initializes with feature cache."""
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=2,
            bottom_n=2,
            feature_cache=mock_feature_cache,
        )

        assert generator.feature_cache is mock_feature_cache
        assert generator.top_n == 2
        assert generator.bottom_n == 2

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_cache_miss_generates_and_caches_features(
        self, mock_get_features, mock_model_registry, test_data_dir, mock_feature_cache
    ):
        """Test cache miss generates features and caches them."""
        # Setup mock feature generation
        mock_features = pd.DataFrame(
            np.random.randn(5, 158),
            index=pd.MultiIndex.from_tuples(
                [
                    ("2024-01-15", "AAPL"),
                    ("2024-01-15", "MSFT"),
                    ("2024-01-15", "GOOGL"),
                    ("2024-01-15", "AMZN"),
                    ("2024-01-15", "TSLA"),
                ],
                names=["datetime", "instrument"],
            ),
            columns=[f"feature_{i}" for i in range(158)],
        )
        mock_get_features.return_value = mock_features

        # Cache returns None (cache miss)
        mock_feature_cache.get.return_value = None

        # Generate signals
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=2,
            bottom_n=2,
            feature_cache=mock_feature_cache,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"], as_of_date=datetime(2024, 1, 15)
        )

        # Verify features were generated
        assert mock_get_features.called

        # Verify features were cached (once per symbol)
        assert mock_feature_cache.set.call_count == 5

        # Verify signals returned
        assert len(signals) == 5
        assert "symbol" in signals.columns
        assert "predicted_return" in signals.columns
        assert "target_weight" in signals.columns

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_cache_hit_skips_feature_generation(
        self, mock_get_features, mock_model_registry, test_data_dir, mock_feature_cache
    ):
        """Test cache hit uses cached features without generation."""
        # Setup cached features
        cached_features = {f"feature_{i}": np.random.randn() for i in range(158)}

        mock_feature_cache.get.return_value = cached_features

        # Generate signals
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=2,
            bottom_n=2,
            feature_cache=mock_feature_cache,
        )

        signals = generator.generate_signals(symbols=["AAPL"], as_of_date=datetime(2024, 1, 15))

        # Verify features were NOT generated (cache hit)
        assert not mock_get_features.called

        # Verify cache was checked
        mock_feature_cache.get.assert_called_once_with("AAPL", "2024-01-15")

        # Verify signals returned
        assert len(signals) == 1
        assert signals.iloc[0]["symbol"] == "AAPL"

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_mixed_cache_hits_and_misses(
        self, mock_get_features, mock_model_registry, test_data_dir, mock_feature_cache
    ):
        """Test mixed cache hits and misses."""

        # Setup: AAPL and MSFT cached, GOOGL not cached
        def get_side_effect(symbol, date):
            if symbol in ["AAPL", "MSFT"]:
                return {f"feature_{i}": np.random.randn() for i in range(158)}
            return None

        mock_feature_cache.get.side_effect = get_side_effect

        # Mock feature generation for cache misses
        mock_features = pd.DataFrame(
            np.random.randn(1, 158),
            index=pd.MultiIndex.from_tuples(
                [
                    ("2024-01-15", "GOOGL"),
                ],
                names=["datetime", "instrument"],
            ),
            columns=[f"feature_{i}" for i in range(158)],
        )
        mock_get_features.return_value = mock_features

        # Generate signals
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=1,
            bottom_n=1,
            feature_cache=mock_feature_cache,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"], as_of_date=datetime(2024, 1, 15)
        )

        # Verify cache checked for all symbols
        assert mock_feature_cache.get.call_count == 3

        # Verify features generated only for cache miss (GOOGL)
        mock_get_features.assert_called_once()
        call_args = mock_get_features.call_args
        assert "GOOGL" in call_args[1]["symbols"]
        assert "AAPL" not in call_args[1]["symbols"]  # Cache hit
        assert "MSFT" not in call_args[1]["symbols"]  # Cache hit

        # Verify signals returned for all symbols
        assert len(signals) == 3

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_no_cache_generates_all_features(
        self, mock_get_features, mock_model_registry, test_data_dir
    ):
        """Test without cache, all features are generated."""
        # Mock feature generation
        mock_features = pd.DataFrame(
            np.random.randn(3, 158),
            index=pd.MultiIndex.from_tuples(
                [
                    ("2024-01-15", "AAPL"),
                    ("2024-01-15", "MSFT"),
                    ("2024-01-15", "GOOGL"),
                ],
                names=["datetime", "instrument"],
            ),
            columns=[f"feature_{i}" for i in range(158)],
        )
        mock_get_features.return_value = mock_features

        # Generate signals WITHOUT cache
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=1,
            bottom_n=1,
            feature_cache=None,  # No cache
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"], as_of_date=datetime(2024, 1, 15)
        )

        # Verify features generated for all symbols
        mock_get_features.assert_called_once()
        call_args = mock_get_features.call_args
        assert set(call_args[1]["symbols"]) == {"AAPL", "MSFT", "GOOGL"}

        # Verify signals returned
        assert len(signals) == 3


class TestHealthEndpointRedisStatus:
    """Tests for health endpoint Redis status."""

    def test_health_response_redis_disabled(self):
        """Test health response when Redis is disabled."""
        from apps.signal_service.main import HealthResponse

        response = HealthResponse(
            status="healthy",
            model_loaded=True,
            model_info={"strategy": "alpha_baseline", "version": "v1.0.0"},
            redis_status="disabled",
            feature_cache_enabled=False,
            timestamp="2024-01-15T10:30:00Z",
        )

        assert response.status == "healthy"
        assert response.redis_status == "disabled"
        assert response.feature_cache_enabled is False

    def test_health_response_redis_connected(self):
        """Test health response when Redis is connected."""
        from apps.signal_service.main import HealthResponse

        response = HealthResponse(
            status="healthy",
            model_loaded=True,
            model_info={"strategy": "alpha_baseline", "version": "v1.0.0"},
            redis_status="connected",
            feature_cache_enabled=True,
            timestamp="2024-01-15T10:30:00Z",
        )

        assert response.status == "healthy"
        assert response.redis_status == "connected"
        assert response.feature_cache_enabled is True

    def test_health_response_redis_disconnected(self):
        """Test health response when Redis is disconnected."""
        from apps.signal_service.main import HealthResponse

        response = HealthResponse(
            status="healthy",
            model_loaded=True,
            model_info={"strategy": "alpha_baseline", "version": "v1.0.0"},
            redis_status="disconnected",
            feature_cache_enabled=False,
            timestamp="2024-01-15T10:30:00Z",
        )

        assert response.status == "healthy"
        assert response.redis_status == "disconnected"
        assert response.feature_cache_enabled is False


class TestGracefulDegradation:
    """Tests for graceful degradation when Redis fails."""

    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_cache_error_falls_back_to_generation(
        self, mock_get_features, mock_model_registry, test_data_dir, mock_feature_cache
    ):
        """Test cache error falls back to feature generation."""
        # Setup: cache.get raises exception
        from redis.exceptions import RedisError

        mock_feature_cache.get.side_effect = RedisError("Connection lost")

        # Mock feature generation
        mock_features = pd.DataFrame(
            np.random.randn(1, 158),
            index=pd.MultiIndex.from_tuples(
                [
                    ("2024-01-15", "AAPL"),
                ],
                names=["datetime", "instrument"],
            ),
            columns=[f"feature_{i}" for i in range(158)],
        )
        mock_get_features.return_value = mock_features

        # Generate signals
        generator = SignalGenerator(
            model_registry=mock_model_registry,
            data_dir=test_data_dir,
            top_n=1,
            bottom_n=1,
            feature_cache=mock_feature_cache,
        )

        # Should not raise exception (graceful degradation)
        signals = generator.generate_signals(symbols=["AAPL"], as_of_date=datetime(2024, 1, 15))

        # Verify features were generated (fallback)
        assert mock_get_features.called

        # Verify signals returned
        assert len(signals) == 1
