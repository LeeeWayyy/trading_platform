"""
Tests for signal generator.

Tests cover:
- SignalGenerator initialization
- Signal generation logic
- Portfolio weight computation
- Weight validation
- Error handling

Usage:
    # Run all tests
    pytest apps/signal_service/tests/test_signal_generator.py -v

    # Run only unit tests
    pytest apps/signal_service/tests/test_signal_generator.py -v -k "not integration"
"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from apps.signal_service.model_registry import ModelRegistry
from apps.signal_service.signal_generator import SignalGenerator


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

    def test_initialization_with_negative_top_n_raises_error(self, test_db_url, temp_dir):
        """Initialize with negative top_n raises ValueError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(ValueError) as exc_info:
            SignalGenerator(registry, temp_dir, top_n=-1, bottom_n=3)

        assert "must be >= 0" in str(exc_info.value)

    def test_initialization_with_negative_bottom_n_raises_error(self, test_db_url, temp_dir):
        """Initialize with negative bottom_n raises ValueError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(ValueError) as exc_info:
            SignalGenerator(registry, temp_dir, top_n=3, bottom_n=-1)

        assert "must be >= 0" in str(exc_info.value)

    def test_initialization_with_nonexistent_dir_raises_error(self, test_db_url):
        """Initialize with nonexistent data_dir raises FileNotFoundError."""
        registry = ModelRegistry(test_db_url)

        with pytest.raises(FileNotFoundError) as exc_info:
            SignalGenerator(registry, Path("/nonexistent/dir"))

        assert "Data directory not found" in str(exc_info.value)


class TestSignalGeneration:
    """Tests for signal generation logic."""

    def test_generate_signals_without_model_raises_error(self, test_db_url, temp_dir):
        """Generating signals without loaded model raises RuntimeError."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir)

        with pytest.raises(RuntimeError) as exc_info:
            generator.generate_signals(["AAPL"])

        assert "Model not loaded" in str(exc_info.value)

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires T1 data and trained model")
    def test_generate_signals_success(self, test_db_url, alpha_baseline_model_path):
        """Generate signals successfully with real model and data."""
        # Setup registry with real model
        registry = ModelRegistry(test_db_url)
        # Would need database setup to reload model
        # registry.reload_if_changed("alpha_baseline")

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

        # Validate structure
        assert isinstance(signals, pd.DataFrame)
        assert list(signals.columns) == ["symbol", "predicted_return", "rank", "target_weight"]
        assert len(signals) == 5

        # Validate data types
        assert signals["symbol"].dtype == object
        assert signals["predicted_return"].dtype in [np.float64, np.float32]
        assert signals["rank"].dtype in [np.int64, np.int32]
        assert signals["target_weight"].dtype in [np.float64, np.float32]

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires T1 data and trained model")
    def test_generate_signals_weights_sum_correctly(self, test_db_url):
        """Portfolio weights sum to 1.0 (long) and -1.0 (short)."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(
            registry,
            Path("data/adjusted"),
            top_n=3,
            bottom_n=3,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            as_of_date=datetime(2024, 1, 15),
        )

        # Check weight sums
        long_weights = signals[signals["target_weight"] > 0]["target_weight"]
        short_weights = signals[signals["target_weight"] < 0]["target_weight"]

        assert np.isclose(long_weights.sum(), 1.0, atol=1e-6)
        assert np.isclose(short_weights.sum(), -1.0, atol=1e-6)

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires T1 data and trained model")
    def test_generate_signals_correct_position_counts(self, test_db_url):
        """Correct number of long and short positions."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(
            registry,
            Path("data/adjusted"),
            top_n=2,
            bottom_n=2,
        )

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            as_of_date=datetime(2024, 1, 15),
        )

        long_count = (signals["target_weight"] > 0).sum()
        short_count = (signals["target_weight"] < 0).sum()
        neutral_count = (signals["target_weight"] == 0).sum()

        assert long_count == 2
        assert short_count == 2
        assert neutral_count == 1  # 5 total - 2 long - 2 short

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires T1 data and trained model")
    def test_generate_signals_ranks_are_consecutive(self, test_db_url):
        """Ranks are consecutive integers starting from 1."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, Path("data/adjusted"))

        signals = generator.generate_signals(
            symbols=["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            as_of_date=datetime(2024, 1, 15),
        )

        ranks = sorted(signals["rank"].tolist())
        expected_ranks = list(range(1, 6))  # [1, 2, 3, 4, 5]

        assert ranks == expected_ranks


class TestWeightValidation:
    """Tests for weight validation logic."""

    def test_validate_weights_correct_portfolio(self, test_db_url, temp_dir):
        """Validate weights passes for correct portfolio."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        # Create mock signals with correct weights
        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
                "predicted_return": [0.02, 0.01, 0.0, -0.01, -0.02],
                "rank": [1, 2, 3, 4, 5],
                "target_weight": [0.5, 0.5, 0.0, -0.5, -0.5],  # 2 long, 2 short
            }
        )

        assert generator.validate_weights(signals) is True

    def test_validate_weights_incorrect_long_sum(self, test_db_url, temp_dir):
        """Validate weights fails for incorrect long sum."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        # Incorrect long sum (0.9 instead of 1.0)
        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
                "predicted_return": [0.02, 0.01, 0.0, -0.01, -0.02],
                "rank": [1, 2, 3, 4, 5],
                "target_weight": [0.4, 0.5, 0.0, -0.5, -0.5],  # Sum = 0.9
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_weights_incorrect_position_count(self, test_db_url, temp_dir):
        """Validate weights fails for incorrect position count."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        # Only 1 long position instead of 2
        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
                "predicted_return": [0.02, 0.01, 0.0, -0.01, -0.02],
                "rank": [1, 2, 3, 4, 5],
                "target_weight": [1.0, 0.0, 0.0, -0.5, -0.5],  # Only 1 long
            }
        )

        assert generator.validate_weights(signals) is False

    def test_validate_weights_out_of_bounds(self, test_db_url, temp_dir):
        """Validate weights fails for weights outside [-1, 1]."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=2, bottom_n=2)

        # Weight > 1.0 (out of bounds)
        signals = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
                "predicted_return": [0.02, 0.01, 0.0, -0.01, -0.02],
                "rank": [1, 2, 3, 4, 5],
                "target_weight": [1.5, -0.5, 0.0, -0.5, -0.5],  # 1.5 > 1.0
            }
        )

        assert generator.validate_weights(signals) is False


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_long_only_strategy(self, test_db_url, temp_dir):
        """Long-only strategy (bottom_n=0) works correctly."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=3, bottom_n=0)

        assert generator.top_n == 3
        assert generator.bottom_n == 0

    def test_zero_positions(self, test_db_url, temp_dir):
        """Strategy with top_n=0 and bottom_n=0 is allowed."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, temp_dir, top_n=0, bottom_n=0)

        assert generator.top_n == 0
        assert generator.bottom_n == 0

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires T1 data and trained model")
    def test_generate_signals_with_missing_data(self, test_db_url):
        """Generating signals with missing data raises ValueError."""
        registry = ModelRegistry(test_db_url)
        generator = SignalGenerator(registry, Path("data/adjusted"))

        # Date far in the future (no data)
        with pytest.raises(ValueError) as exc_info:
            generator.generate_signals(
                symbols=["AAPL"],
                as_of_date=datetime(2099, 1, 1),
            )

        assert "No features available" in str(exc_info.value)
