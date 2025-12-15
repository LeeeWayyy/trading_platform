"""Pytest fixtures for backtest regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

GOLDEN_RESULTS_DIR = Path(__file__).parent / "golden_results"
METRIC_TOLERANCE = 0.001  # 0.1% tolerance for floating point


@pytest.fixture()
def golden_results_dir() -> Path:
    """Return path to golden results directory."""
    return GOLDEN_RESULTS_DIR


def load_golden_result(filename: str) -> dict[str, Any]:
    """Load golden result from fixture file."""
    path = GOLDEN_RESULTS_DIR / filename
    with open(path, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


def load_golden_config(filename: str) -> dict[str, Any]:
    """Load golden config from fixture file."""
    path = GOLDEN_RESULTS_DIR / filename
    with open(path, encoding="utf-8") as f:
        config: dict[str, Any] = json.load(f)
        return config
