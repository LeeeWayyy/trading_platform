"""
Tests for M5: Feature Pre-computation.

M5 Fix: Enables cache warming at day start to avoid blocking signal
generation requests with disk I/O.

Contract:
- precompute_features() method caches features without model prediction
- Precompute endpoint returns cache statistics
- Cached features are used by subsequent signal generation
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


class TestPrecomputeFeaturesMethod:
    """Test SignalGenerator.precompute_features() method."""

    def test_precompute_no_cache_returns_skipped(self) -> None:
        """When feature_cache is None, all symbols should be skipped."""
        from apps.signal_service.signal_generator import SignalGenerator

        mock_registry = MagicMock()
        mock_registry.is_loaded = True

        with patch.object(SignalGenerator, "__init__", lambda self, **kw: None):
            generator = SignalGenerator()
            generator.feature_cache = None

            result = generator.precompute_features(
                symbols=["AAPL", "MSFT", "GOOGL"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

            assert result["cached_count"] == 0
            assert result["skipped_count"] == 3
            assert result["symbols_cached"] == []
            assert set(result["symbols_skipped"]) == {"AAPL", "MSFT", "GOOGL"}

    def test_precompute_with_cache_returns_stats(self) -> None:
        """When feature_cache is available, it should cache features."""
        from apps.signal_service.signal_generator import SignalGenerator

        mock_registry = MagicMock()
        mock_registry.is_loaded = True
        mock_cache = MagicMock()
        # mget returns dict with None values (not cached) - uses batch MGET
        mock_cache.mget.return_value = {"AAPL": None}

        with patch.object(SignalGenerator, "__init__", lambda self, **kw: None):
            generator = SignalGenerator()
            generator.feature_cache = mock_cache
            generator.data_provider = MagicMock()
            generator.data_provider.data_dir = "/fake/path"

            # Mock feature generation to return empty DataFrame
            with patch(
                "apps.signal_service.signal_generator.get_alpha158_features"
            ) as mock_features:
                import pandas as pd

                mock_features.return_value = pd.DataFrame()

                generator.precompute_features(
                    symbols=["AAPL"],
                    as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
                )

                # Should have tried to generate features
                mock_features.assert_called_once()

    def test_precompute_already_cached_skips(self) -> None:
        """When symbols are already cached, they should be skipped."""
        from apps.signal_service.signal_generator import SignalGenerator

        mock_cache = MagicMock()
        # Return dict with non-None values to indicate cached - uses batch MGET
        mock_cache.mget.return_value = {
            "AAPL": {"feature1": 1.0},
            "MSFT": {"feature1": 2.0},
        }

        with patch.object(SignalGenerator, "__init__", lambda self, **kw: None):
            generator = SignalGenerator()
            generator.feature_cache = mock_cache

            result = generator.precompute_features(
                symbols=["AAPL", "MSFT"],
                as_of_date=datetime(2024, 1, 15, tzinfo=UTC),
            )

            # All already cached, nothing to generate
            assert result["cached_count"] == 0
            assert result["skipped_count"] == 2


class TestPrecomputeEndpoint:
    """Test /api/v1/features/precompute endpoint."""

    def test_precompute_endpoint_returns_stats(self) -> None:
        """Precompute endpoint should return cache statistics."""
        from apps.signal_service.main import app

        # Mock the signal_generator
        with patch("apps.signal_service.main.signal_generator") as mock_gen:
            mock_gen.precompute_features.return_value = {
                "cached_count": 2,
                "skipped_count": 1,
                "symbols_cached": ["AAPL", "MSFT"],
                "symbols_skipped": ["GOOGL"],
            }

            client = TestClient(app)
            response = client.post(
                "/api/v1/features/precompute",
                json={"symbols": ["AAPL", "MSFT", "GOOGL"]},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["cached_count"] == 2
            assert data["skipped_count"] == 1
            assert "AAPL" in data["symbols_cached"]

    def test_precompute_endpoint_validates_symbols(self) -> None:
        """Endpoint should reject empty symbols list."""
        from apps.signal_service.main import app

        with patch("apps.signal_service.main.signal_generator"):
            client = TestClient(app)
            response = client.post(
                "/api/v1/features/precompute",
                json={"symbols": []},  # Empty list should fail
            )

            # Pydantic validation error
            assert response.status_code == 422

    def test_precompute_endpoint_invalid_date(self) -> None:
        """Endpoint should reject invalid date format."""
        from apps.signal_service.main import app

        with patch("apps.signal_service.main.signal_generator"):
            client = TestClient(app)
            response = client.post(
                "/api/v1/features/precompute",
                json={"symbols": ["AAPL"], "as_of_date": "not-a-date"},
            )

            assert response.status_code == 400
            assert "Invalid date format" in response.json()["detail"]

    def test_precompute_endpoint_503_without_generator(self) -> None:
        """Endpoint should return 503 if signal_generator not initialized."""
        from apps.signal_service.main import app

        with patch("apps.signal_service.main.signal_generator", None):
            client = TestClient(app)
            response = client.post(
                "/api/v1/features/precompute",
                json={"symbols": ["AAPL"]},
            )

            assert response.status_code == 503
