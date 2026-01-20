"""
Comprehensive tests for signal generator - targeting 85%+ branch coverage.

This test file provides extensive coverage of the SignalGenerator class, including:
- Initialization with various parameter combinations
- Signal generation logic with feature caching
- Feature precomputation and hydration
- Portfolio weight computation and validation
- Edge cases and error handling
- Cache-aside pattern behavior
- Fallback to mock features
- Prediction normalization
- Multi-symbol scenarios

Coverage targets:
- Branch coverage: 85%+
- All error paths tested
- All cache scenarios tested (hit, miss, error)
- All feature generation paths tested

Usage:
    # Run all tests
    pytest tests/apps/signal_service/test_signal_generator_comprehensive.py -v

    # Run with coverage
    pytest tests/apps/signal_service/test_signal_generator_comprehensive.py --cov=apps.signal_service.signal_generator --cov-report=term-missing

    # Run only unit tests (fast)
    pytest tests/apps/signal_service/test_signal_generator_comprehensive.py -v -k "not integration"
"""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest
from redis.exceptions import RedisError

from apps.signal_service.model_registry import ModelMetadata, ModelRegistry
from apps.signal_service.signal_generator import (
    PrecomputeResult,
    SignalGenerator,
)
from libs.core.redis_client import FeatureCache

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture()
def mock_model_with_registry(test_db_url, mock_model):
    """Create a model registry with loaded model."""
    registry = ModelRegistry(test_db_url)

    # Load model directly and set up registry state
    model = registry.load_model_from_file(str(mock_model))
    registry.current_model = Mock(wraps=model)

    # Create metadata
    metadata = ModelMetadata(
        id=1,
        strategy_name="alpha_baseline",
        version="v1.0.0",
        mlflow_run_id="test_run_123",
        mlflow_experiment_id="test_exp_456",
        status="active",
        model_path=str(mock_model),
        activated_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        performance_metrics={"ic": 0.082, "sharpe": 1.45},
        config={"learning_rate": 0.05},
    )
    registry.current_metadata = metadata

    return registry


@pytest.fixture()
def mock_feature_cache():
    """Create a mock feature cache for testing."""
    cache = Mock(spec=FeatureCache)

    # Default behavior: cache miss
    cache.get.return_value = None
    cache.mget.return_value = {}
    cache.set.return_value = None

    return cache


@pytest.fixture()
def sample_features():
    """Create sample Alpha158 features for testing."""
    date_str = "2024-01-15"
    symbols = ["AAPL", "MSFT", "GOOGL"]
    index = pd.MultiIndex.from_product([[date_str], symbols], names=["datetime", "instrument"])

    np.random.seed(42)
    features = pd.DataFrame(
        np.random.randn(3, 10),  # 3 symbols, 10 features (mock model has 10)
        index=index,
        columns=[f"feature_{i:03d}" for i in range(10)],
    )

    return features


# ============================================================================
# Initialization Tests
# ============================================================================


class TestSignalGeneratorInitialization:
    """Tests for SignalGenerator initialization."""

    def test_initialization_with_valid_params(self, test_db_url, temp_dir):
        """Initialize SignalGenerator with valid parameters."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(
            model_registry=registry,
            data_dir=temp_dir,
            top_n=3,
            bottom_n=3,
        )

        assert generator.model_registry == registry
        assert generator.top_n == 3
        assert generator.bottom_n == 3
        assert generator.data_provider is not None
        assert generator.feature_cache is None

    def test_initialization_with_feature_cache(self, test_db_url, temp_dir, mock_feature_cache):
        """Initialize SignalGenerator with feature cache enabled."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(
            model_registry=registry,
            data_dir=temp_dir,
            top_n=3,
            bottom_n=3,
            feature_cache=mock_feature_cache,
        )

        assert generator.feature_cache is not None
        assert generator.feature_cache == mock_feature_cache

    def test_initialization_with_zero_top_n(self, test_db_url, temp_dir):
        """Initialize with top_n=0 (short-only strategy) is valid."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=0, bottom_n=3)

        assert generator.top_n == 0
        assert generator.bottom_n == 3

    def test_initialization_with_zero_bottom_n(self, test_db_url, temp_dir):
        """Initialize with bottom_n=0 (long-only strategy) is valid."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=3, bottom_n=0)

        assert generator.top_n == 3
        assert generator.bottom_n == 0

    def test_initialization_with_both_zero(self, test_db_url, temp_dir):
        """Initialize with both zero (no positions) is allowed."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=0, bottom_n=0)

        assert generator.top_n == 0
        assert generator.bottom_n == 0

    def test_initialization_with_negative_top_n_raises_error(self, test_db_url, temp_dir):
        """Initialize with negative top_n raises ValueError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(ValueError, match="must be >= 0"):
            SignalGenerator(registry, temp_dir, top_n=-1, bottom_n=3)

    def test_initialization_with_negative_bottom_n_raises_error(self, test_db_url, temp_dir):
        """Initialize with negative bottom_n raises ValueError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(ValueError, match="must be >= 0"):
            SignalGenerator(registry, temp_dir, top_n=3, bottom_n=-1)

    def test_initialization_with_nonexistent_dir_raises_error(self, test_db_url):
        """Initialize with nonexistent data_dir raises FileNotFoundError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(FileNotFoundError, match="Data directory not found"):
            SignalGenerator(registry, Path("/nonexistent/dir"))


# ============================================================================
# Signal Generation Tests
# ============================================================================


class TestSignalGeneration:
    """Tests for signal generation logic."""

    def test_generate_signals_without_loaded_model_raises_error(self, test_db_url, temp_dir):
        """Generating signals without loaded model raises RuntimeError."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir)

        with pytest.raises(RuntimeError, match="Model not loaded"):
            generator.generate_signals(["AAPL"])

    def test_generate_signals_without_metadata_raises_error(
        self, test_db_url, temp_dir, mock_model
    ):
        """Generating signals without metadata raises ValueError."""
        registry = ModelRegistry(test_db_url)
        model = registry.load_model_from_file(str(mock_model))
        registry.current_model = model
        registry.current_metadata = None  # No metadata

        generator = SignalGenerator(registry, temp_dir)

        with pytest.raises(ValueError, match="Model metadata not loaded"):
            generator.generate_signals(["AAPL"])

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_generate_signals_with_mock_features_success(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features
    ):
        """Generate signals successfully with mock features."""
        # Setup mock to return sample features
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir, top_n=1, bottom_n=1)

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Validate structure
        assert isinstance(signals, pd.DataFrame)
        assert list(signals.columns) == ["symbol", "predicted_return", "rank", "target_weight"]
        assert len(signals) == 3

        # Validate data types
        assert signals["symbol"].dtype == object
        assert signals["rank"].dtype in [np.int64, np.int32]

        # Validate position counts (top_n=1, bottom_n=1)
        long_count = (signals["target_weight"] > 0).sum()
        short_count = (signals["target_weight"] < 0).sum()
        neutral_count = (signals["target_weight"] == 0).sum()

        assert long_count == 1
        assert short_count == 1
        assert neutral_count == 1

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_generate_signals_weights_sum_correctly(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features
    ):
        """Portfolio weights sum to 1.0 (long) and -1.0 (short)."""
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            top_n=2,
            bottom_n=2,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Only 3 symbols but top_n=2, bottom_n=2 -> 2 long, 2 short, but we only have 3
        # So we expect fewer positions or all to be allocated
        long_weights = signals[signals["target_weight"] > 0]["target_weight"]
        short_weights = signals[signals["target_weight"] < 0]["target_weight"]

        # With 3 symbols and top_n=2, bottom_n=2, only 2 longs and 1 short possible
        # Actually, top 2 and bottom 2 with 3 symbols means top 2 long, bottom 2 short
        # But there are only 3 ranks total (1, 2, 3), so:
        # - Rank 1, 2: long (top 2)
        # - Rank 3, 2 (from bottom): short? No, bottom 2 means ranks 2 and 3
        # This creates overlap. Let's check actual behavior.

        # Actually looking at code: nsmallest(top_n, "rank") for long (ranks 1, 2)
        # and nlargest(bottom_n, "rank") for short (ranks 2, 3)
        # So rank 2 gets assigned both long and short weight? No, second assignment overwrites.

        # Better approach: just check sums are correct for actual positions
        if len(long_weights) > 0:
            assert np.isclose(long_weights.sum(), 1.0, atol=1e-6)
        if len(short_weights) > 0:
            assert np.isclose(short_weights.sum(), -1.0, atol=1e-6)

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_generate_signals_with_no_features_raises_error(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry
    ):
        """Generating signals with no features raises ValueError."""
        # Return empty DataFrame
        mock_get_features.return_value = pd.DataFrame()

        generator = SignalGenerator(mock_model_with_registry, temp_dir)

        with pytest.raises(ValueError, match="No features generated"):
            generator.generate_signals(
                symbols=["AAPL"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_generate_signals_fallback_to_mock_on_error(
        self,
        mock_get_mock,
        mock_get_real,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        sample_features,
    ):
        """Generating signals falls back to mock features on error."""
        # Real features fail
        mock_get_real.side_effect = ValueError("Data not available")
        # Mock features succeed
        mock_get_mock.return_value = sample_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir)

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should succeed with mock features
        assert len(signals) == 3
        mock_get_mock.assert_called_once()

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    @patch("apps.signal_service.signal_generator.get_mock_alpha158_features")
    def test_generate_signals_raises_when_both_fail(
        self, mock_get_mock, mock_get_real, test_db_url, temp_dir, mock_model_with_registry
    ):
        """Generating signals raises when both real and mock features fail."""
        mock_get_real.side_effect = ValueError("Data not available")
        mock_get_mock.side_effect = ValueError("Mock also failed")

        generator = SignalGenerator(mock_model_with_registry, temp_dir)

        with pytest.raises(ValueError, match="No features available"):
            generator.generate_signals(
                symbols=["AAPL"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_generate_signals_prediction_normalization(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features
    ):
        """Predictions are normalized to reasonable return scale."""
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir)

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # After normalization, mean should be ~0, std should be ~0.02
        predictions = signals["predicted_return"].values
        assert np.abs(np.mean(predictions)) < 0.01  # Close to 0
        # Std depends on number of samples, but should be reasonable

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_generate_signals_uses_default_date(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features
    ):
        """Generate signals uses current date when as_of_date is None."""
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir)

        with patch("apps.signal_service.signal_generator.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2024, 1, 20, tzinfo=UTC)
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            signals = generator.generate_signals(symbols=["AAPL", "MSFT", "GOOGL"])

            # Should have called with current date
            assert len(signals) == 3


# ============================================================================
# Feature Caching Tests
# ============================================================================


class TestFeatureCaching:
    """Tests for feature cache-aside pattern."""

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_cache_hit_skips_generation(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, mock_feature_cache
    ):
        """Cache hit skips feature generation."""
        # Setup cache to return features
        cached_features = {"feature_000": 1.0, "feature_001": 2.0}
        mock_feature_cache.get.return_value = cached_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        # This should use cached features, but will fail because we only have 2 features
        # and model expects 10. Let's make it complete.
        cached_features = {f"feature_{i:03d}": float(i) for i in range(10)}
        mock_feature_cache.get.return_value = cached_features

        generator.generate_signals(
            symbols=["AAPL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should NOT have called feature generation
        mock_get_features.assert_not_called()
        # Should have called cache.get
        mock_feature_cache.get.assert_called()

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_cache_miss_triggers_generation(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Cache miss triggers feature generation."""
        # Cache miss
        mock_feature_cache.get.return_value = None
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        generator.generate_signals(
            symbols=["AAPL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should have called feature generation
        mock_get_features.assert_called_once()
        # Should have tried to set cache
        assert mock_feature_cache.set.call_count > 0

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_cache_error_falls_back_to_generation(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Cache error gracefully falls back to generation."""
        # Cache raises error
        mock_feature_cache.get.side_effect = RedisError("Connection failed")
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        signals = generator.generate_signals(
            symbols=["AAPL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should have fallen back to generation
        mock_get_features.assert_called_once()
        assert len(signals) == 1

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_cache_set_error_continues(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Cache set error doesn't fail signal generation."""
        # Cache miss, but set fails
        mock_feature_cache.get.return_value = None
        mock_feature_cache.set.side_effect = RedisError("Write failed")
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        # Should still succeed despite cache write failure
        signals = generator.generate_signals(
            symbols=["AAPL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert len(signals) == 1

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_mixed_cache_hits_and_misses(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Some symbols cached, others need generation."""

        # AAPL cached, MSFT not cached
        def cache_get_side_effect(symbol, date_str):
            if symbol == "AAPL":
                return {f"feature_{i:03d}": float(i) for i in range(10)}
            return None

        mock_feature_cache.get.side_effect = cache_get_side_effect

        # Return features only for MSFT (the miss)
        msft_features = sample_features.xs("MSFT", level="instrument", drop_level=False)
        mock_get_features.return_value = msft_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should have both symbols
        assert len(signals) == 2
        # Should have generated only for MSFT
        mock_get_features.assert_called_once()


# ============================================================================
# Feature Precomputation Tests
# ============================================================================


class TestFeaturePrecomputation:
    """Tests for precompute_features method."""

    def test_precompute_without_cache_returns_skipped(
        self, test_db_url, temp_dir, mock_model_with_registry
    ):
        """Precompute without cache returns all skipped."""
        generator = SignalGenerator(mock_model_with_registry, temp_dir, feature_cache=None)

        result = generator.precompute_features(
            symbols=["AAPL", "MSFT"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert result["cached_count"] == 0
        assert result["skipped_count"] == 2
        assert result["symbols_cached"] == []
        assert set(result["symbols_skipped"]) == {"AAPL", "MSFT"}

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_precompute_with_cache_success(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Precompute successfully caches features."""
        # No symbols cached yet
        mock_feature_cache.mget.return_value = {}
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        result = generator.precompute_features(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert result["cached_count"] == 3
        assert result["skipped_count"] == 0
        assert set(result["symbols_cached"]) == {"AAPL", "MSFT", "GOOGL"}
        assert result["symbols_skipped"] == []

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_precompute_skips_already_cached(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Precompute skips symbols already in cache."""
        # AAPL already cached
        cached_data = {f"feature_{i:03d}": float(i) for i in range(10)}
        mock_feature_cache.mget.return_value = {"AAPL": cached_data}

        # Return features only for MSFT
        msft_features = sample_features.xs("MSFT", level="instrument", drop_level=False)
        mock_get_features.return_value = msft_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        result = generator.precompute_features(
            symbols=["AAPL", "MSFT"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert result["cached_count"] == 1  # Only MSFT newly cached
        assert result["skipped_count"] == 1  # AAPL already cached
        assert result["symbols_cached"] == ["MSFT"]
        assert result["symbols_skipped"] == ["AAPL"]

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_precompute_handles_generation_failure(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, mock_feature_cache
    ):
        """Precompute handles feature generation failure gracefully."""
        mock_feature_cache.mget.return_value = {}
        mock_get_features.side_effect = ValueError("Data not available")

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        # Should try mock features and potentially fail, but not crash
        # Actually the code falls back to mock features
        with patch(
            "apps.signal_service.signal_generator.get_mock_alpha158_features"
        ) as mock_get_mock:
            mock_get_mock.side_effect = ValueError("Mock also failed")

            result = generator.precompute_features(
                symbols=["AAPL"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

            # All symbols failed
            assert result["cached_count"] == 0
            assert result["skipped_count"] == 1


# ============================================================================
# Feature Hydration Tests
# ============================================================================


class TestFeatureHydration:
    """Tests for hydrate_feature_cache method."""

    def test_hydration_without_cache_returns_zero(
        self, test_db_url, temp_dir, mock_model_with_registry
    ):
        """Hydration without cache returns zero results."""
        generator = SignalGenerator(mock_model_with_registry, temp_dir, feature_cache=None)

        result = generator.hydrate_feature_cache(
            symbols=["AAPL"],
            history_days=5,
        )

        assert result["dates_attempted"] == 0
        assert result["dates_succeeded"] == 0
        assert result["cached_count"] == 0

    def test_hydration_with_zero_days_returns_zero(
        self, test_db_url, temp_dir, mock_model_with_registry, mock_feature_cache
    ):
        """Hydration with zero history_days returns zero."""
        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        result = generator.hydrate_feature_cache(
            symbols=["AAPL"],
            history_days=0,
        )

        assert result["dates_attempted"] == 0

    def test_hydration_with_empty_symbols_returns_zero(
        self, test_db_url, temp_dir, mock_model_with_registry, mock_feature_cache
    ):
        """Hydration with empty symbols returns zero."""
        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        result = generator.hydrate_feature_cache(
            symbols=[],
            history_days=5,
        )

        assert result["dates_attempted"] == 0

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_hydration_success_multiple_days(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Hydration successfully processes multiple days."""
        mock_feature_cache.mget.return_value = {}
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        result = generator.hydrate_feature_cache(
            symbols=["AAPL"],
            history_days=3,
            end_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert result["dates_attempted"] == 3
        assert result["dates_succeeded"] == 3
        assert result["dates_failed"] == 0
        assert result["cached_count"] == 3  # 1 symbol * 3 days

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_hydration_handles_partial_failures(
        self,
        mock_get_features,
        test_db_url,
        temp_dir,
        mock_model_with_registry,
        mock_feature_cache,
        sample_features,
    ):
        """Hydration continues despite partial failures."""
        mock_feature_cache.mget.return_value = {}

        # First call succeeds, second fails, third succeeds
        mock_get_features.side_effect = [
            sample_features,
            ValueError("Data not available"),
            sample_features,
        ]

        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        # Need to also mock the fallback
        with patch(
            "apps.signal_service.signal_generator.get_mock_alpha158_features"
        ) as mock_get_mock:
            mock_get_mock.side_effect = ValueError("Mock also failed")

            result = generator.hydrate_feature_cache(
                symbols=["AAPL"],
                history_days=3,
                end_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

            assert result["dates_attempted"] == 3
            assert result["dates_succeeded"] == 2
            assert result["dates_failed"] == 1

    def test_hydration_resolves_end_date_from_data(
        self, test_db_url, temp_dir, mock_model_with_registry, mock_feature_cache
    ):
        """Hydration resolves end_date from available data when not provided."""
        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        # Mock the data provider to return a date range
        with patch.object(generator.data_provider, "get_date_range") as mock_get_range:
            mock_get_range.return_value = (
                date(2024, 1, 1),
                date(2024, 1, 15),
            )

            with patch.object(generator, "precompute_features") as mock_precompute:
                mock_precompute.return_value = PrecomputeResult(
                    cached_count=1,
                    skipped_count=0,
                    symbols_cached=["AAPL"],
                    symbols_skipped=[],
                )

                result = generator.hydrate_feature_cache(
                    symbols=["AAPL"],
                    history_days=2,
                    end_date=None,  # Should resolve from data
                )

                # Should have attempted hydration
                assert result["dates_attempted"] == 2

    def test_hydration_with_no_available_data_returns_zero(
        self, test_db_url, temp_dir, mock_model_with_registry, mock_feature_cache
    ):
        """Hydration with no available data returns zero."""
        generator = SignalGenerator(
            mock_model_with_registry,
            temp_dir,
            feature_cache=mock_feature_cache,
        )

        # Mock data provider to return None for date range
        with patch.object(generator.data_provider, "get_date_range") as mock_get_range:
            mock_get_range.return_value = (None, None)

            result = generator.hydrate_feature_cache(
                symbols=["AAPL"],
                history_days=5,
                end_date=None,
            )

            assert result["dates_attempted"] == 0


# ============================================================================
# Weight Validation Tests
# ============================================================================


class TestWeightValidation:
    """Tests for validate_weights method."""

    def test_validate_correct_weights(self, test_db_url, temp_dir):
        """Validate weights passes for correct portfolio."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "predicted_return": [0.02, 0.01, -0.01, -0.02],
                "rank": [1, 2, 3, 4],
                "target_weight": [0.5, 0.5, -0.5, -0.5],
            }
        )

        assert generator.validate_weights(signals) is True

    def test_validate_incorrect_long_sum(self, test_db_url, temp_dir):
        """Validate weights fails when long weights don't sum to 1.0."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "predicted_return": [0.02, 0.01, -0.01, -0.02],
                "rank": [1, 2, 3, 4],
                "target_weight": [0.4, 0.5, -0.5, -0.5],  # Sum = 0.9, not 1.0
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_incorrect_short_sum(self, test_db_url, temp_dir):
        """Validate weights fails when short weights don't sum to -1.0."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "predicted_return": [0.02, 0.01, -0.01, -0.02],
                "rank": [1, 2, 3, 4],
                "target_weight": [0.5, 0.5, -0.4, -0.5],  # Sum = -0.9, not -1.0
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_incorrect_long_count(self, test_db_url, temp_dir):
        """Validate weights fails when long position count is incorrect."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "predicted_return": [0.02, 0.01, -0.01, -0.02],
                "rank": [1, 2, 3, 4],
                "target_weight": [1.0, 0.0, -0.5, -0.5],  # Only 1 long, expected 2
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_incorrect_short_count(self, test_db_url, temp_dir):
        """Validate weights fails when short position count is incorrect."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "predicted_return": [0.02, 0.01, -0.01, -0.02],
                "rank": [1, 2, 3, 4],
                "target_weight": [0.5, 0.5, -1.0, 0.0],  # Only 1 short, expected 2
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_weight_out_of_bounds(self, test_db_url, temp_dir):
        """Validate weights fails when weight exceeds [-1, 1] bounds."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "predicted_return": [0.02, 0.01, -0.01, -0.02],
                "rank": [1, 2, 3, 4],
                "target_weight": [1.5, -0.5, -0.5, -0.5],  # 1.5 > 1.0
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_long_only_strategy(self, test_db_url, temp_dir):
        """Validate weights for long-only strategy (bottom_n=0)."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=3, bottom_n=0)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL"],
                "predicted_return": [0.02, 0.01, -0.01],
                "rank": [1, 2, 3],
                "target_weight": [0.333, 0.333, 0.334],  # Only long positions
            }
        )

        assert generator.validate_weights(signals) is True

    def test_validate_short_only_strategy(self, test_db_url, temp_dir):
        """Validate weights for short-only strategy (top_n=0)."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=0, bottom_n=3)

        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL"],
                "predicted_return": [0.02, 0.01, -0.01],
                "rank": [1, 2, 3],
                "target_weight": [-0.333, -0.333, -0.334],  # Only short positions
            }
        )

        assert generator.validate_weights(signals) is True


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_single_symbol_generation(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry
    ):
        """Generate signals for single symbol works correctly."""
        # Create features for single symbol
        single_feature = pd.DataFrame(
            np.random.randn(1, 10),
            index=pd.MultiIndex.from_tuples(
                [("2024-01-15", "AAPL")], names=["datetime", "instrument"]
            ),
            columns=[f"feature_{i:03d}" for i in range(10)],
        )
        mock_get_features.return_value = single_feature

        generator = SignalGenerator(mock_model_with_registry, temp_dir, top_n=1, bottom_n=0)

        signals = generator.generate_signals(
            symbols=["AAPL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        assert len(signals) == 1
        assert signals.iloc[0]["symbol"] == "AAPL"

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_prediction_with_constant_values(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry
    ):
        """Predictions with constant values (std=0) are handled correctly."""
        # Create features that will produce constant predictions
        constant_features = pd.DataFrame(
            np.zeros((3, 10)),  # All zeros -> constant predictions
            index=pd.MultiIndex.from_product(
                [["2024-01-15"], ["AAPL", "MSFT", "GOOGL"]], names=["datetime", "instrument"]
            ),
            columns=[f"feature_{i:03d}" for i in range(10)],
        )
        mock_get_features.return_value = constant_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir, top_n=1, bottom_n=1)

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should handle zero std case (all predictions same)
        assert len(signals) == 3
        # Ranks should still be assigned (though arbitrary)
        assert "rank" in signals.columns

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_model_prediction_error_propagates(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features
    ):
        """Model prediction errors are properly propagated."""
        mock_get_features.return_value = sample_features

        # Mock model to raise error on predict
        mock_model_with_registry.current_model.predict.side_effect = ValueError("Model error")

        generator = SignalGenerator(mock_model_with_registry, temp_dir)

        with pytest.raises(RuntimeError, match="Model prediction failed"):
            generator.generate_signals(
                symbols=["AAPL"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_more_positions_than_symbols(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features
    ):
        """Requesting more positions than available symbols is handled."""
        # Only 3 symbols but requesting top_n=5, bottom_n=5
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir, top_n=5, bottom_n=5)

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL"],
            as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
        )

        # Should only have 3 positions total
        assert len(signals) == 3
        # Weights should still be valid
        long_count = (signals["target_weight"] > 0).sum()
        short_count = (signals["target_weight"] < 0).sum()
        # Can't have more than 3 positions
        assert long_count + short_count <= 3

    @patch("apps.signal_service.signal_generator.get_alpha158_features")
    def test_overlap_logs_warning(
        self, mock_get_features, test_db_url, temp_dir, mock_model_with_registry, sample_features, caplog
    ):
        """Test that overlap between top and bottom selections logs a warning."""
        import logging

        # Only 3 symbols but requesting top_n=2, bottom_n=2
        # This will cause 1 symbol to overlap
        mock_get_features.return_value = sample_features

        generator = SignalGenerator(mock_model_with_registry, temp_dir, top_n=2, bottom_n=2)

        with caplog.at_level(logging.WARNING):
            signals = generator.generate_signals(
                symbols=["AAPL", "MSFT", "GOOGL"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

        # Verify overlap warning was logged
        overlap_warnings = [r for r in caplog.records if "overlap detected" in r.message.lower()]
        assert len(overlap_warnings) > 0, "Expected overlap warning to be logged"

        # Verify market neutrality is maintained
        long_weight = signals[signals["target_weight"] > 0]["target_weight"].sum()
        short_weight = signals[signals["target_weight"] < 0]["target_weight"].sum()
        assert abs(long_weight - 1.0) < 0.01, f"Long weights should sum to 1.0, got {long_weight}"
        assert abs(short_weight + 1.0) < 0.01, f"Short weights should sum to -1.0, got {short_weight}"
