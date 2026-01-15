"""
Shared fixtures for risk module tests.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from libs.trading.risk import CANONICAL_FACTOR_ORDER, CovarianceConfig, CovarianceResult


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


class MockCRSPProvider:
    """Mock CRSP provider for testing."""

    def __init__(self, data: pl.DataFrame | None = None):
        self._data = data

    def get_daily_data(
        self,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        if self._data is not None:
            return self._data.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))
        return create_mock_crsp_data(start_date, end_date)

    def get_daily_prices(
        self,
        start_date: date,
        end_date: date,
        as_of_date: date | None = None,
    ) -> pl.DataFrame:
        return self.get_daily_data(start_date, end_date)


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


def create_mock_crsp_data(
    start_date: date | None = None,
    end_date: date | None = None,
    n_stocks: int = 100,
) -> pl.DataFrame:
    """Create mock CRSP data with realistic returns."""
    if start_date is None:
        start_date = date(2022, 1, 1)
    if end_date is None:
        end_date = date(2023, 12, 31)

    np.random.seed(42)

    permnos = list(range(10001, 10001 + n_stocks))
    n_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(n_days)]

    data = []
    for permno in permnos:
        base_price = np.random.uniform(10, 200)
        shrout = np.random.uniform(1000, 50000)

        for i, dt in enumerate(dates):
            # Skip weekends
            if dt.weekday() >= 5:
                continue

            # Random return with some factor structure
            market_factor = np.random.normal(0.0003, 0.01)
            idio_return = np.random.normal(0, 0.015)
            ret = market_factor + idio_return

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


def create_mock_fundamentals(n_stocks: int = 100) -> pl.DataFrame:
    """Create mock Compustat fundamental data."""
    np.random.seed(42)

    permnos = list(range(10001, 10001 + n_stocks))

    data = []
    for permno in permnos:
        for year in [2021, 2022, 2023]:
            data.append(
                {
                    "permno": permno,
                    "datadate": date(year, 12, 31),
                    "ceq": np.random.uniform(100, 10000),
                    "ni": np.random.uniform(-50, 500),
                    "gsector": str(np.random.choice([10, 15, 20, 25, 30, 35, 40, 45, 50, 55])),
                }
            )

    return pl.DataFrame(data)


def create_mock_factor_returns(
    start_date: date,
    end_date: date,
    factor_names: list[str] | None = None,
) -> pl.DataFrame:
    """Create mock daily factor returns."""
    if factor_names is None:
        factor_names = CANONICAL_FACTOR_ORDER

    np.random.seed(42)

    n_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(n_days)]
    trading_days = [d for d in dates if d.weekday() < 5]

    data = []
    for dt in trading_days:
        for factor_name in factor_names:
            data.append(
                {
                    "date": dt,
                    "factor_name": factor_name,
                    "daily_return": np.random.normal(0, 0.01),
                    "t_statistic": np.random.normal(0, 2),
                    "r_squared": np.random.uniform(0.01, 0.1),
                }
            )

    return pl.DataFrame(data)


def create_mock_factor_exposures(
    n_stocks: int = 100,
    as_of_date: date | None = None,
    factor_names: list[str] | None = None,
) -> pl.DataFrame:
    """Create mock factor exposures (loadings)."""
    if as_of_date is None:
        as_of_date = date(2023, 6, 30)
    if factor_names is None:
        factor_names = CANONICAL_FACTOR_ORDER

    np.random.seed(42)

    permnos = list(range(10001, 10001 + n_stocks))

    data = []
    for permno in permnos:
        for factor_name in factor_names:
            data.append(
                {
                    "permno": permno,
                    "date": as_of_date,
                    "factor_name": factor_name,
                    "zscore": np.random.normal(0, 1),
                    "raw_value": np.random.normal(0, 0.1),
                    "percentile": np.random.uniform(0, 1),
                }
            )

    return pl.DataFrame(data)


def create_mock_covariance_matrix(n_factors: int = 5) -> np.ndarray:
    """Create a valid PSD covariance matrix."""
    np.random.seed(42)

    # Generate random correlation structure
    A = np.random.randn(n_factors, n_factors)
    cov = A @ A.T / n_factors

    # Scale to reasonable variance (~1% daily vol)
    daily_vol = 0.01
    cov = cov * (daily_vol**2)

    return cov


@pytest.fixture()
def mock_crsp_data() -> pl.DataFrame:
    """Fixture for mock CRSP data."""
    return create_mock_crsp_data()


@pytest.fixture()
def mock_fundamentals() -> pl.DataFrame:
    """Fixture for mock fundamental data."""
    return create_mock_fundamentals()


@pytest.fixture()
def mock_crsp_provider(mock_crsp_data: pl.DataFrame) -> MockCRSPProvider:
    """Fixture for mock CRSP provider."""
    return MockCRSPProvider(mock_crsp_data)


@pytest.fixture()
def mock_compustat_provider(mock_fundamentals: pl.DataFrame) -> MockCompustatProvider:
    """Fixture for mock Compustat provider."""
    return MockCompustatProvider(mock_fundamentals)


@pytest.fixture()
def mock_manifest_manager() -> MockManifestManager:
    """Fixture for mock manifest manager."""
    return MockManifestManager()


@pytest.fixture()
def mock_factor_builder(
    mock_crsp_provider: MockCRSPProvider,
    mock_compustat_provider: MockCompustatProvider,
    mock_manifest_manager: MockManifestManager,
):
    """Fixture for mock FactorBuilder."""
    from libs.models.factors import FactorBuilder

    return FactorBuilder(
        crsp_provider=mock_crsp_provider,
        compustat_provider=mock_compustat_provider,
        manifest_manager=mock_manifest_manager,
    )


@pytest.fixture()
def covariance_config() -> CovarianceConfig:
    """Fixture for default covariance config."""
    return CovarianceConfig()


@pytest.fixture()
def mock_factor_returns() -> pl.DataFrame:
    """Fixture for mock factor returns."""
    return create_mock_factor_returns(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 30),
    )


@pytest.fixture()
def mock_factor_exposures() -> pl.DataFrame:
    """Fixture for mock factor exposures."""
    return create_mock_factor_exposures()


@pytest.fixture()
def mock_covariance_matrix() -> np.ndarray:
    """Fixture for a valid covariance matrix."""
    return create_mock_covariance_matrix()


@pytest.fixture()
def sample_covariance_result(mock_factor_returns: pl.DataFrame) -> CovarianceResult:
    """Fixture for a sample CovarianceResult."""
    return CovarianceResult(
        factor_covariance=create_mock_covariance_matrix(),
        factor_names=CANONICAL_FACTOR_ORDER.copy(),
        factor_returns=mock_factor_returns,
        as_of_date=date(2023, 6, 30),
        dataset_version_ids={"crsp": "v1.0.0", "compustat": "v1.0.0"},
        shrinkage_intensity=0.2,
        effective_observations=126,
        reproducibility_hash="test123",
    )


def create_mock_specific_risks(
    n_stocks: int = 100,
    as_of_date: date | None = None,
) -> pl.DataFrame:
    """Create mock specific risk data."""
    if as_of_date is None:
        as_of_date = date(2023, 6, 30)

    np.random.seed(42)

    permnos = list(range(10001, 10001 + n_stocks))

    data = []
    for permno in permnos:
        # Daily specific variance (~1-5% daily idiosyncratic vol)
        daily_vol = np.random.uniform(0.01, 0.05)
        specific_variance = daily_vol**2
        specific_vol = daily_vol * np.sqrt(252)  # Annualized

        data.append(
            {
                "permno": permno,
                "specific_variance": specific_variance,
                "specific_vol": specific_vol,
            }
        )

    return pl.DataFrame(data)


def create_mock_portfolio(
    n_stocks: int = 50,
    start_permno: int = 10001,
) -> pl.DataFrame:
    """Create mock portfolio with weights summing to 1."""
    np.random.seed(42)

    permnos = list(range(start_permno, start_permno + n_stocks))
    weights = np.random.uniform(0.01, 0.05, n_stocks)
    weights = weights / weights.sum()  # Normalize to sum to 1

    return pl.DataFrame(
        {
            "permno": permnos,
            "weight": weights.tolist(),
        }
    )


@pytest.fixture()
def mock_specific_risks() -> pl.DataFrame:
    """Fixture for mock specific risk data."""
    return create_mock_specific_risks()


@pytest.fixture()
def mock_portfolio() -> pl.DataFrame:
    """Fixture for mock portfolio."""
    return create_mock_portfolio()


@pytest.fixture()
def mock_factor_loadings_wide() -> pl.DataFrame:
    """Fixture for factor loadings in wide format."""
    exposures = create_mock_factor_exposures()
    return exposures.pivot(
        index="permno",
        on="factor_name",
        values="zscore",
    )
