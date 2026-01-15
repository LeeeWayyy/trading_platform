"""
Shared fixtures for factor module tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest

if TYPE_CHECKING:
    from libs.models.factors import FactorResult


@dataclass
class MockManifest:
    """Mock manifest for testing."""

    manifest_version: str = "1.0.0"
    dataset_name: str = "test"


class MockManifestManager:
    """Mock ManifestManager for testing."""

    def __init__(self, version: str = "1.0.0"):
        self.version = version

    def load_manifest(self, dataset_name: str) -> MockManifest:
        return MockManifest(manifest_version=self.version, dataset_name=dataset_name)

    def load_manifest_at_date(self, dataset_name: str, snapshot_date: date) -> MockManifest:
        # Different version for different dates (for PIT testing)
        if snapshot_date < date(2024, 1, 1):
            return MockManifest(manifest_version="0.9.0", dataset_name=dataset_name)
        return MockManifest(manifest_version=self.version, dataset_name=dataset_name)


class MockCRSPProvider:
    """Mock CRSP provider for testing."""

    def __init__(self, data: pl.DataFrame | None = None):
        self._data = data

    def get_daily_prices(
        self,
        start_date: date,
        end_date: date,
        as_of_date: date | None = None,
    ) -> pl.DataFrame:
        if self._data is not None:
            return self._data.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))
        return create_mock_prices(start_date, end_date)


class MockCompustatProvider:
    """Mock Compustat provider for testing."""

    def __init__(self, data: pl.DataFrame | None = None):
        self._data = data

    def get_annual_fundamentals(
        self,
        start_date: date,
        end_date: date,
        as_of_date: date | None = None,
    ) -> pl.DataFrame:
        if self._data is not None:
            return self._data
        return create_mock_fundamentals()


def create_mock_prices(
    start_date: date | None = None,
    end_date: date | None = None,
    n_stocks: int = 50,
    n_days: int = 400,
) -> pl.DataFrame:
    """Create mock CRSP price data."""
    if start_date is None:
        start_date = date(2022, 6, 1)  # Start earlier to cover 2023 with 252+ days
    if end_date is None:
        end_date = date(2023, 12, 31)  # Ensure we cover test dates

    np.random.seed(42)

    permnos = list(range(10001, 10001 + n_stocks))
    # Generate dates from start to end
    n_actual_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(n_actual_days)]

    # Generate daily data
    data = []
    for permno in permnos:
        base_price = np.random.uniform(10, 200)
        shrout = np.random.uniform(1000, 50000)  # In thousands

        for i, dt in enumerate(dates):
            # Skip weekends
            if dt.weekday() >= 5:
                continue

            # Random return
            ret = np.random.normal(0.0005, 0.02)
            price = base_price * (1 + ret) ** i

            data.append(
                {
                    "date": dt,
                    "permno": permno,
                    "prc": price,
                    "ret": ret,
                    "vol": np.random.uniform(10000, 1000000),
                    "shrout": shrout,
                }
            )

    return pl.DataFrame(data)


def create_mock_fundamentals(n_stocks: int = 50) -> pl.DataFrame:
    """Create mock Compustat fundamental data."""
    np.random.seed(42)

    permnos = list(range(10001, 10001 + n_stocks))

    data = []
    for permno in permnos:
        # Multiple years of data
        for year in [2021, 2022, 2023]:
            data.append(
                {
                    "permno": permno,
                    "datadate": date(year, 12, 31),
                    "ceq": np.random.uniform(100, 10000),  # Common equity in millions
                    "ni": np.random.uniform(-50, 500),  # Net income in millions
                    "gsector": str(np.random.choice([10, 15, 20, 25, 30, 35, 40, 45, 50, 55])),
                }
            )

    return pl.DataFrame(data)


@pytest.fixture()
def mock_prices() -> pl.DataFrame:
    """Fixture for mock price data."""
    return create_mock_prices()


@pytest.fixture()
def mock_fundamentals() -> pl.DataFrame:
    """Fixture for mock fundamental data."""
    return create_mock_fundamentals()


@pytest.fixture()
def mock_crsp_provider(mock_prices: pl.DataFrame) -> MockCRSPProvider:
    """Fixture for mock CRSP provider."""
    return MockCRSPProvider(mock_prices)


@pytest.fixture()
def mock_compustat_provider(mock_fundamentals: pl.DataFrame) -> MockCompustatProvider:
    """Fixture for mock Compustat provider."""
    return MockCompustatProvider(mock_fundamentals)


@pytest.fixture()
def mock_manifest_manager() -> MockManifestManager:
    """Fixture for mock manifest manager."""
    return MockManifestManager()


@pytest.fixture()
def factor_builder(
    mock_crsp_provider: MockCRSPProvider,
    mock_compustat_provider: MockCompustatProvider,
    mock_manifest_manager: MockManifestManager,
):
    """Fixture for FactorBuilder with mocked dependencies."""
    from libs.models.factors import FactorBuilder

    return FactorBuilder(
        crsp_provider=mock_crsp_provider,
        compustat_provider=mock_compustat_provider,
        manifest_manager=mock_manifest_manager,
    )


@pytest.fixture()
def sample_factor_result() -> FactorResult:
    """Fixture for a sample FactorResult."""
    from libs.models.factors import FactorResult

    df = pl.DataFrame(
        {
            "permno": [1, 2, 3, 4, 5],
            "date": [date(2023, 6, 30)] * 5,
            "factor_name": ["momentum_12_1"] * 5,
            "raw_value": [0.1, 0.2, -0.1, 0.05, -0.15],
            "zscore": [0.5, 1.2, -0.8, 0.2, -1.1],
            "percentile": [0.7, 0.9, 0.2, 0.6, 0.1],
        }
    )

    return FactorResult(
        exposures=df,
        as_of_date=date(2023, 6, 30),
        dataset_version_ids={"crsp": "v1.0.0", "compustat": "v1.0.0"},
        computation_timestamp=datetime.now(UTC),
        reproducibility_hash="abc123",
    )
