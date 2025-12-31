"""Tests for AlphaExplorerService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from apps.web_console.services.alpha_explorer_service import AlphaExplorerService
from libs.models.types import EnvironmentMetadata, ModelMetadata, ModelType


@pytest.fixture()
def mock_registry() -> MagicMock:
    """Mock registry returning ModelMetadata with correct fields."""
    registry = MagicMock()
    registry.list_models.return_value = [
        ModelMetadata(
            model_id="momentum_alpha_v1",
            model_type=ModelType.alpha_weights,
            version="v1.0.0",
            created_at=datetime.now(UTC),
            dataset_version_ids={"crsp": "v1.0.0"},
            snapshot_id="snap-001",
            checksum_sha256="abc123",
            env=EnvironmentMetadata(
                python_version="3.11.5",
                dependencies_hash="sha256abc123",
                platform="linux-x86_64",
                created_by="test_user",
                numpy_version="1.26.0",
                polars_version="0.20.0",
            ),
            config={},
            config_hash="cfg123",
            parameters={
                "name": "momentum_alpha",
                "backtest_job_id": "job-123",
                "alpha_names": [],
                "combination_method": "zscore",
                "ic_threshold": 0.02,
            },
            metrics={"mean_ic": 0.05, "icir": 1.2},
        ),
    ]
    return registry


def test_list_signals_returns_summaries(mock_registry: MagicMock) -> None:
    service = AlphaExplorerService(mock_registry, None)

    signals, total = service.list_signals()

    assert total == 1
    assert len(signals) == 1
    assert signals[0].display_name == "momentum_alpha"
    assert signals[0].mean_ic == 0.05


def test_list_signals_filters_by_ic_range(mock_registry: MagicMock) -> None:
    service = AlphaExplorerService(mock_registry, None)

    signals, total = service.list_signals(min_ic=0.06)

    assert total == 0
    assert signals == []
