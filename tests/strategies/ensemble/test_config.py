"""
Tests for ensemble configuration.

This module tests configuration validation and parameter handling.
"""

import pytest

from strategies.ensemble.combiner import CombinationMethod
from strategies.ensemble.config import (
    DEFAULT_ADAPTIVE_CONFIG,
    DEFAULT_ENSEMBLE_CONFIG,
    AdaptiveWeightConfig,
    EnsembleConfig,
)


class TestEnsembleConfig:
    """Test EnsembleConfig validation and defaults."""

    def test_default_config(self) -> None:
        """Test that default config has valid defaults."""
        config = EnsembleConfig()

        assert config.combination_method == CombinationMethod.WEIGHTED_AVERAGE
        assert config.min_confidence == 0.6
        assert config.signal_threshold == 0.3
        assert config.min_strategies == 2
        assert config.require_agreement is False

    def test_valid_config(self) -> None:
        """Test that valid config passes validation."""
        config = EnsembleConfig(
            combination_method=CombinationMethod.MAJORITY_VOTE,
            strategy_weights={"mean_reversion": 0.4, "momentum": 0.6},
            min_confidence=0.7,
            signal_threshold=0.4,
        )

        # Should not raise
        config.validate()

    def test_weights_sum_validation(self) -> None:
        """Test that weights must sum to 1.0."""
        config = EnsembleConfig(
            strategy_weights={"mean_reversion": 0.4, "momentum": 0.4}  # Sum = 0.8
        )

        with pytest.raises(ValueError, match="must sum to 1.0"):
            config.validate()

    def test_negative_weights_validation(self) -> None:
        """Test that negative weights are rejected."""
        config = EnsembleConfig(
            strategy_weights={"mean_reversion": 1.2, "momentum": -0.2}
        )

        with pytest.raises(ValueError, match="non-negative"):
            config.validate()

    def test_min_confidence_range(self) -> None:
        """Test that min_confidence must be in [0, 1]."""
        config = EnsembleConfig(min_confidence=1.5)

        with pytest.raises(ValueError, match="min_confidence must be in"):
            config.validate()

        config2 = EnsembleConfig(min_confidence=-0.1)

        with pytest.raises(ValueError, match="min_confidence must be in"):
            config2.validate()

    def test_signal_threshold_range(self) -> None:
        """Test that signal_threshold must be in [0, 1]."""
        config = EnsembleConfig(signal_threshold=1.5)

        with pytest.raises(ValueError, match="signal_threshold must be in"):
            config.validate()

    def test_min_strategies_positive(self) -> None:
        """Test that min_strategies must be >= 1."""
        config = EnsembleConfig(min_strategies=0)

        with pytest.raises(ValueError, match="min_strategies must be"):
            config.validate()

    def test_custom_weights(self) -> None:
        """Test custom strategy weights."""
        config = EnsembleConfig(
            strategy_weights={
                "mean_reversion": 0.3,
                "momentum": 0.5,
                "breakout": 0.2,
            }
        )

        config.validate()  # Should pass
        assert sum(config.strategy_weights.values()) == pytest.approx(1.0)

    def test_empty_weights(self) -> None:
        """Test that empty weights dictionary is allowed."""
        config = EnsembleConfig(strategy_weights={})

        # Empty dict should pass (no weights to validate)
        config.validate()


class TestAdaptiveWeightConfig:
    """Test AdaptiveWeightConfig validation and defaults."""

    def test_default_config(self) -> None:
        """Test that default config has valid defaults."""
        config = AdaptiveWeightConfig()

        assert config.enabled is False
        assert config.lookback_days == 30
        assert config.update_frequency == "daily"
        assert config.min_trades == 10
        assert config.performance_metric == "sharpe"
        assert config.smoothing_factor == 0.2

    def test_valid_config(self) -> None:
        """Test that valid config passes validation."""
        config = AdaptiveWeightConfig(
            enabled=True,
            lookback_days=20,
            update_frequency="weekly",
            min_trades=5,
            performance_metric="win_rate",
            smoothing_factor=0.3,
        )

        # Should not raise
        config.validate()

    def test_lookback_days_positive(self) -> None:
        """Test that lookback_days must be >= 1."""
        config = AdaptiveWeightConfig(lookback_days=0)

        with pytest.raises(ValueError, match="lookback_days must be"):
            config.validate()

    def test_min_trades_positive(self) -> None:
        """Test that min_trades must be >= 1."""
        config = AdaptiveWeightConfig(min_trades=0)

        with pytest.raises(ValueError, match="min_trades must be"):
            config.validate()

    def test_smoothing_factor_range(self) -> None:
        """Test that smoothing_factor must be in (0, 1]."""
        config = AdaptiveWeightConfig(smoothing_factor=0.0)

        with pytest.raises(ValueError, match="smoothing_factor must be in"):
            config.validate()

        config2 = AdaptiveWeightConfig(smoothing_factor=1.5)

        with pytest.raises(ValueError, match="smoothing_factor must be in"):
            config2.validate()

    def test_valid_update_frequencies(self) -> None:
        """Test valid update frequencies."""
        for freq in ["intraday", "daily", "weekly"]:
            config = AdaptiveWeightConfig(update_frequency=freq)
            config.validate()  # Should not raise

    def test_invalid_update_frequency(self) -> None:
        """Test invalid update frequency raises error."""
        config = AdaptiveWeightConfig(update_frequency="monthly")

        with pytest.raises(ValueError, match="update_frequency must be one of"):
            config.validate()

    def test_valid_performance_metrics(self) -> None:
        """Test valid performance metrics."""
        for metric in ["sharpe", "returns", "win_rate", "profit_factor"]:
            config = AdaptiveWeightConfig(performance_metric=metric)
            config.validate()  # Should not raise

    def test_invalid_performance_metric(self) -> None:
        """Test invalid performance metric raises error."""
        config = AdaptiveWeightConfig(performance_metric="max_drawdown")

        with pytest.raises(ValueError, match="performance_metric must be one of"):
            config.validate()


class TestDefaultConfigs:
    """Test default configuration instances."""

    def test_default_ensemble_config_valid(self) -> None:
        """Test that DEFAULT_ENSEMBLE_CONFIG is valid."""
        DEFAULT_ENSEMBLE_CONFIG.validate()  # Should not raise

    def test_default_adaptive_config_valid(self) -> None:
        """Test that DEFAULT_ADAPTIVE_CONFIG is valid."""
        DEFAULT_ADAPTIVE_CONFIG.validate()  # Should not raise

    def test_defaults_are_immutable_instances(self) -> None:
        """Test that modifying defaults doesn't affect new instances."""
        # Get default values
        original_method = DEFAULT_ENSEMBLE_CONFIG.combination_method
        original_enabled = DEFAULT_ADAPTIVE_CONFIG.enabled

        # Modify defaults
        DEFAULT_ENSEMBLE_CONFIG.combination_method = CombinationMethod.UNANIMOUS
        DEFAULT_ADAPTIVE_CONFIG.enabled = True

        # Create new instances
        new_ensemble = EnsembleConfig()
        new_adaptive = AdaptiveWeightConfig()

        # New instances should have original defaults, not modified values
        # (This behavior depends on dataclass implementation)
        # In Python dataclasses with default_factory, new instances get the original defaults
        assert new_ensemble.combination_method == CombinationMethod.WEIGHTED_AVERAGE
        assert new_adaptive.enabled is False

        # Reset to avoid affecting other tests
        DEFAULT_ENSEMBLE_CONFIG.combination_method = original_method
        DEFAULT_ADAPTIVE_CONFIG.enabled = original_enabled

    def test_version_tracking(self) -> None:
        """Test that config includes version for tracking."""
        config = EnsembleConfig()
        assert hasattr(config, "version")
        assert config.version == "0.1.0"
