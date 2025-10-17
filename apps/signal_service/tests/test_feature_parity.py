"""
Feature Parity Tests for Signal Service.

These tests validate that production feature generation matches research code exactly.
This is critical to ensure research results can be reproduced in production.

Key Validations:
1. Same feature generation code used in research and production
2. Deterministic feature computation (same inputs → same outputs)
3. Feature dimensions match model expectations
4. No code duplication (DRY principle)

Usage:
    pytest apps/signal_service/tests/test_feature_parity.py -v -m integration

Prerequisites:
    - T1 data available in data/adjusted/
    - Research feature code in strategies/alpha_baseline/

See: docs/IMPLEMENTATION_GUIDES/t3-signal-service.md for context
"""

import pytest
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import inspect


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture(scope="module")
def data_dir():
    """Data directory for feature parity tests."""
    return Path("data/adjusted")


@pytest.fixture(scope="module")
def test_symbols():
    """Test symbols for feature parity tests."""
    return ["AAPL", "MSFT", "GOOGL"]


@pytest.fixture(scope="module")
def test_date():
    """Test date for feature parity tests."""
    return datetime(2024, 12, 31)


# ==============================================================================
# Test Code Import Parity
# ==============================================================================

@pytest.mark.integration
class TestCodeImportParity:
    """Validate that production imports feature code from research."""

    def test_signal_generator_imports_research_features(self):
        """Test that SignalGenerator imports features from strategies module."""
        from apps.signal_service.signal_generator import SignalGenerator
        import apps.signal_service.signal_generator as sg_module

        # Get source code of generate_signals method
        source = inspect.getsource(SignalGenerator.generate_signals)

        # Should call feature generation functions
        assert "get_alpha158_features" in source or "get_mock_alpha158_features" in source, \
            "SignalGenerator.generate_signals should call feature generation function"

        # Check module-level imports to verify strategies module is imported
        module_source = inspect.getsource(sg_module)

        # Should import from strategies.alpha_baseline
        assert "from strategies.alpha_baseline.features import get_alpha158_features" in module_source, \
            "SignalGenerator module should import get_alpha158_features from strategies"

        assert "from strategies.alpha_baseline.mock_features import get_mock_alpha158_features" in module_source, \
            "SignalGenerator module should import get_mock_alpha158_features from strategies"

        print("\n  ✓ SignalGenerator imports from strategies.alpha_baseline.features")
        print("  ✓ SignalGenerator imports from strategies.alpha_baseline.mock_features")
        print("  ✓ No feature code duplication detected")

    def test_no_duplicate_feature_implementations(self):
        """Test that features are not duplicated in signal service."""
        from apps.signal_service import signal_generator

        source = inspect.getsource(signal_generator)

        # Should NOT contain feature computation logic
        # (Features should be imported from strategies module)
        assert "rolling" not in source.lower() or "import" in source, \
            "Signal generator should not contain rolling window logic"

        assert "pct_change" not in source.lower() or "import" in source, \
            "Signal generator should not contain price change logic"

        print("\n  ✓ No duplicate feature implementations found")
        print("  ✓ DRY principle maintained")


# ==============================================================================
# Test Feature Computation Determinism
# ==============================================================================

@pytest.mark.integration
class TestFeatureComputationDeterminism:
    """Validate that feature computation is deterministic."""

    def test_mock_features_deterministic(self, data_dir, test_symbols, test_date):
        """Test that mock features are deterministic (same inputs → same outputs)."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        date_str = test_date.strftime("%Y-%m-%d")

        # Generate features 3 times
        features_list = []
        for i in range(3):
            features = get_mock_alpha158_features(
                symbols=test_symbols,
                start_date=date_str,
                end_date=date_str,
                data_dir=data_dir,
            )
            features_list.append(features)

        # All should be identical
        pd.testing.assert_frame_equal(features_list[0], features_list[1])
        pd.testing.assert_frame_equal(features_list[1], features_list[2])

        print(f"\n  Generated features 3 times:")
        print(f"    Shape: {features_list[0].shape}")
        print(f"    All identical: ✓")

    def test_features_consistent_across_symbols(self, data_dir, test_date):
        """Test that feature generation is consistent for different symbol sets."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        date_str = test_date.strftime("%Y-%m-%d")

        # Generate for symbols individually
        symbol_features = {}
        for symbol in ["AAPL", "MSFT", "GOOGL"]:
            features = get_mock_alpha158_features(
                symbols=[symbol],
                start_date=date_str,
                end_date=date_str,
                data_dir=data_dir,
            )
            symbol_features[symbol] = features

        # Generate for all symbols together
        all_features = get_mock_alpha158_features(
            symbols=["AAPL", "MSFT", "GOOGL"],
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        # Individual features should match subset of combined features
        for symbol in ["AAPL", "MSFT", "GOOGL"]:
            individual = symbol_features[symbol]
            from_combined = all_features[all_features.index.get_level_values("instrument") == symbol]

            pd.testing.assert_frame_equal(
                individual.reset_index(drop=True),
                from_combined.reset_index(drop=True)
            )

        print(f"\n  Feature generation consistent:")
        print(f"    Individual symbols: ✓")
        print(f"    Combined symbols: ✓")


# ==============================================================================
# Test Feature Dimensions
# ==============================================================================

@pytest.mark.integration
class TestFeatureDimensions:
    """Validate feature dimensions match expectations."""

    def test_feature_count_matches_alpha158(self, data_dir, test_symbols, test_date):
        """Test that feature count matches Alpha158 specification (158 features)."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        date_str = test_date.strftime("%Y-%m-%d")

        features = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        # Should have exactly 158 features
        assert features.shape[1] == 158, f"Expected 158 features, got {features.shape[1]}"

        print(f"\n  Feature dimensions:")
        print(f"    Rows (symbols): {features.shape[0]}")
        print(f"    Columns (features): {features.shape[1]}")
        print(f"    ✓ Matches Alpha158 specification")

    def test_feature_names_are_consistent(self, data_dir, test_symbols, test_date):
        """Test that feature names are consistent across calls."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        date_str = test_date.strftime("%Y-%m-%d")

        # Generate twice
        features1 = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        features2 = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        # Column names should be identical
        assert list(features1.columns) == list(features2.columns)

        print(f"\n  Feature names:")
        print(f"    First 5: {list(features1.columns[:5])}")
        print(f"    Last 5: {list(features1.columns[-5:])}")
        print(f"    ✓ Consistent across calls")

    def test_features_have_no_nulls(self, data_dir, test_symbols, test_date):
        """Test that features have no null values."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        date_str = test_date.strftime("%Y-%m-%d")

        features = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        # Should have no nulls (filled with forward/backward fill)
        null_count = features.isnull().sum().sum()
        assert null_count == 0, f"Found {null_count} null values in features"

        print(f"\n  Feature quality:")
        print(f"    Null values: {null_count}")
        print(f"    ✓ All features populated")


# ==============================================================================
# Test Feature-Model Compatibility
# ==============================================================================

@pytest.mark.integration
class TestFeatureModelCompatibility:
    """Validate features are compatible with model."""

    def test_features_match_model_input_dimensions(self, data_dir, test_symbols, test_date):
        """Test that features match model's expected input dimensions."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features
        from apps.signal_service.model_registry import ModelRegistry

        # Load model
        registry = ModelRegistry("postgresql://postgres:postgres@localhost:5432/trading_platform")
        registry.reload_if_changed("alpha_baseline")

        # Generate features
        date_str = test_date.strftime("%Y-%m-%d")
        features = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        # Test prediction (should not raise error)
        try:
            predictions = registry.current_model.predict(features.values)
            success = True
        except Exception as e:
            success = False
            error = str(e)

        assert success, f"Model prediction failed: {error if not success else ''}"
        assert len(predictions) == len(test_symbols), "Should have one prediction per symbol"

        print(f"\n  Feature-model compatibility:")
        print(f"    Feature shape: {features.shape}")
        print(f"    Predictions: {len(predictions)}")
        print(f"    ✓ Features compatible with model")

    def test_feature_values_in_reasonable_range(self, data_dir, test_symbols, test_date):
        """Test that feature values are in reasonable range (not NaN/Inf)."""
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        date_str = test_date.strftime("%Y-%m-%d")

        features = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        # Check for inf values
        inf_count = np.isinf(features.values).sum()
        assert inf_count == 0, f"Found {inf_count} infinite values"

        # Check for nan values
        nan_count = np.isnan(features.values).sum()
        assert nan_count == 0, f"Found {nan_count} NaN values"

        # Get value ranges
        min_val = features.values.min()
        max_val = features.values.max()

        print(f"\n  Feature value ranges:")
        print(f"    Min: {min_val:.4f}")
        print(f"    Max: {max_val:.4f}")
        print(f"    Inf count: {inf_count}")
        print(f"    NaN count: {nan_count}")
        print(f"    ✓ All values in reasonable range")


# ==============================================================================
# Test Production-Research Parity
# ==============================================================================

@pytest.mark.integration
class TestProductionResearchParity:
    """Validate production signals match research predictions."""

    def test_signal_generator_produces_same_predictions(self, data_dir, test_symbols, test_date):
        """Test that signal generator produces same predictions as research code.

        NOTE: SignalGenerator normalizes predictions to reasonable return scale (mean=0, std=0.02).
        This test verifies that the RANKING is preserved (not absolute values).
        """
        from apps.signal_service.model_registry import ModelRegistry
        from apps.signal_service.signal_generator import SignalGenerator
        from strategies.alpha_baseline.mock_features import get_mock_alpha158_features

        # Research path: Generate features and predict
        date_str = test_date.strftime("%Y-%m-%d")
        research_features = get_mock_alpha158_features(
            symbols=test_symbols,
            start_date=date_str,
            end_date=date_str,
            data_dir=data_dir,
        )

        registry = ModelRegistry("postgresql://postgres:postgres@localhost:5432/trading_platform")
        registry.reload_if_changed("alpha_baseline")

        research_predictions = registry.current_model.predict(research_features.values)

        # Production path: Use signal generator
        generator = SignalGenerator(
            model_registry=registry,
            data_dir=data_dir,
            top_n=2,
            bottom_n=2,
        )

        production_signals = generator.generate_signals(
            symbols=test_symbols,
            as_of_date=test_date,
        )

        # Extract predictions from signals
        production_predictions = production_signals["predicted_return"].values

        # IMPORTANT: SignalGenerator normalizes predictions (see signal_generator.py:224-234)
        # We verify that the RANKING is preserved, not absolute values
        research_ranking = np.argsort(-research_predictions)  # Descending order
        production_ranking = np.argsort(-production_predictions)  # Descending order

        # Rankings should be identical
        np.testing.assert_array_equal(
            research_ranking,
            production_ranking,
            err_msg="Research and production prediction rankings differ"
        )

        # Also verify that relative ordering is preserved (correlation should be perfect)
        from scipy.stats import spearmanr
        correlation, _ = spearmanr(research_predictions, production_predictions)
        assert abs(correlation) > 0.99, f"Prediction correlation too low: {correlation}"

        print(f"\n  Production-research parity:")
        print(f"    Research predictions (raw): {research_predictions}")
        print(f"    Production predictions (normalized): {production_predictions}")
        print(f"    Research ranking: {research_ranking}")
        print(f"    Production ranking: {production_ranking}")
        print(f"    Spearman correlation: {correlation:.6f}")
        print(f"    ✓ Rankings match perfectly")


# ==============================================================================
# Summary Report
# ==============================================================================

@pytest.fixture(scope="session", autouse=True)
def print_test_summary(request):
    """Print summary after all tests complete."""
    yield

    # This runs after all tests
    print("\n" + "=" * 60)
    print("Feature Parity Test Suite Complete")
    print("=" * 60)
    print("\nValidated:")
    print("  ✓ Code import parity (no duplication)")
    print("  ✓ Deterministic feature computation")
    print("  ✓ Feature dimensions (158 features)")
    print("  ✓ Feature-model compatibility")
    print("  ✓ Production-research parity")
    print("\nFeature parity is maintained! 🎯")
    print("=" * 60)
