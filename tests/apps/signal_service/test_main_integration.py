"""
Golden Master Integration Test for signal_service/main.py.

This test ensures the "happy path" of signal generation is covered:
- HTTP request parsing (Pydantic validation)
- Service initialization checks
- Signal generation execution (mocked for simplicity)
- Response serialization (JSON)
- Event publishing (mocked)

This is a "smoke test" to verify the pipes are connected end-to-end,
not exhaustive coverage of feature generation internals.

Rationale (from Gemini code review):
    "You covered the *guards* (validators) but not the *action*. If a library
    upgrade or a subtle code change breaks the actual calculation pipeline,
    your tests will still pass, but the service will fail to deliver value."

See Also:
    - test_main_endpoints.py for endpoint unit tests (validators, error paths)
    - test_main_background_tasks.py for background task tests
"""

import pytest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from apps.signal_service.signal_generator import SignalGenerator


class TestSignalGenerationGoldenMaster:
    """Golden master integration test for full signal generation pipeline."""

    def test_generate_signals_endpoint_golden_master(
        self,
        client,
        mock_auth_context: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Golden master test: /api/v1/signals/generate endpoint works end-to-end.

        This test exercises the COMPLETE endpoint flow:
        1. HTTP request parsing (Pydantic validation)
        2. Service initialization checks (model loaded, etc.)
        3. Signal generation (mocked to return known output)
        4. Response serialization (DataFrame → JSON)
        5. Event publishing (mocked)

        This is NOT testing the internal feature generation logic (unit tests
        cover that). This is testing the HTTP → business logic → HTTP flow.

        Args:
            client: FastAPI test client
            mock_auth_context: Auth bypass fixture
            monkeypatch: pytest monkeypatch

        Expected Behavior:
            - 200 OK response
            - JSON response with signals and metadata
            - Signals have expected structure
            - Event publishing is called
        """
        from apps.signal_service import main
        from apps.signal_service.model_registry import ModelMetadata

        # Setup model registry (simple mock with metadata)
        mock_registry = Mock()
        mock_registry.is_loaded = True
        mock_metadata = ModelMetadata(
            id=1,
            strategy_name="alpha_baseline",
            version="v1.0.0-golden",
            mlflow_run_id="golden_run",
            mlflow_experiment_id="golden_exp",
            status="active",
            model_path="/fake/model.txt",
            activated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            performance_metrics={"ic": 0.08},
            config={},
        )
        mock_registry.current_metadata = mock_metadata

        # Setup signal generator (mock generate_signals to return known output)
        mock_generator = Mock(spec=SignalGenerator)
        mock_generator.top_n = 2
        mock_generator.bottom_n = 1

        # Create known output (this is the "golden master" - known good output structure)
        golden_signals = pd.DataFrame({
            "symbol": ["AAPL", "MSFT", "GOOGL"],
            "predicted_return": [0.023, 0.018, -0.015],
            "rank": [1, 2, 3],
            "target_weight": [0.5, 0.5, -1.0],
        })
        mock_generator.generate_signals.return_value = golden_signals

        # Mock event publisher
        mock_publish = Mock()

        # Patch globals
        monkeypatch.setattr(main, "model_registry", mock_registry)
        monkeypatch.setattr(main, "signal_generator", mock_generator)
        monkeypatch.setattr(main, "_publish_signal_event_with_fallback", mock_publish)

        # Make request (this is the end-to-end flow)
        response = client.post(
            "/api/v1/signals/generate",
            json={
                "symbols": ["AAPL", "MSFT", "GOOGL"],
                "as_of_date": "2024-01-15",
            },
        )

        # Assert response structure (golden master verification)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Verify top-level response structure
        assert "signals" in data, "Response should have 'signals' field"
        assert "metadata" in data, "Response should have 'metadata' field"
        # Note: timestamp is in metadata.generated_at, not top-level

        # Verify signals structure (golden master comparison)
        signals = data["signals"]
        assert len(signals) == 3, f"Expected 3 signals, got {len(signals)}"

        # Verify each signal has required fields
        required_fields = {"symbol", "target_weight", "predicted_return", "rank"}
        for signal in signals:
            assert required_fields.issubset(set(signal.keys())), \
                f"Signal missing fields: {required_fields - set(signal.keys())}"

        # Verify specific values (golden master)
        symbols = [s["symbol"] for s in signals]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        assert "GOOGL" in symbols

        # Verify metadata
        assert data["metadata"]["model_version"] == "v1.0.0-golden"
        assert data["metadata"]["strategy"] == "alpha_baseline"
        assert "generated_at" in data["metadata"], "Metadata should have generated_at timestamp"

        # Verify signal generation was called with correct params
        mock_generator.generate_signals.assert_called_once()
        call_kwargs = mock_generator.generate_signals.call_args.kwargs
        assert call_kwargs["symbols"] == ["AAPL", "MSFT", "GOOGL"]

        # Verify event publishing was called
        mock_publish.assert_called_once()

