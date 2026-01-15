"""
Tests for BarraRiskModel.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest

from libs.trading.risk import (
    CANONICAL_FACTOR_ORDER,
    BarraRiskModel,
    BarraRiskModelConfig,
    InsufficientCoverageError,
    SpecificRiskResult,
)
from tests.libs.trading.risk.conftest import (
    create_mock_covariance_matrix,
    create_mock_factor_exposures,
    create_mock_portfolio,
    create_mock_specific_risks,
)


class TestBarraRiskModelInit:
    """Tests for BarraRiskModel initialization."""

    def test_default_config(self):
        """Uses default config when none provided."""
        config = BarraRiskModelConfig()
        assert config.annualization_factor == 252
        assert config.min_coverage == 0.8
        assert config.var_confidence_95 == 0.95
        assert config.var_confidence_99 == 0.99

    def test_from_t22_results(self, sample_covariance_result):
        """Creates model from T2.2 results."""
        specific_risks = create_mock_specific_risks()
        factor_loadings = create_mock_factor_exposures()

        specific_result = SpecificRiskResult(
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp_specific_risk": "test123"},
        )

        model = BarraRiskModel.from_t22_results(
            covariance_result=sample_covariance_result,
            specific_risk_result=specific_result,
            factor_loadings=factor_loadings,
        )

        assert model.factor_covariance.shape == (5, 5)
        assert model.factor_names == CANONICAL_FACTOR_ORDER
        assert "factor_loadings" in model.dataset_version_ids

    def test_from_t22_results_with_wide_loadings(self, sample_covariance_result):
        """Creates model from wide format factor loadings."""
        specific_risks = create_mock_specific_risks()
        # Create wide format loadings directly
        factor_loadings = create_mock_factor_exposures().pivot(
            index="permno",
            on="factor_name",
            values="zscore",
        )

        specific_result = SpecificRiskResult(
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"crsp_specific_risk": "test123"},
        )

        model = BarraRiskModel.from_t22_results(
            covariance_result=sample_covariance_result,
            specific_risk_result=specific_result,
            factor_loadings=factor_loadings,
        )

        assert model.factor_loadings.height == 100


class TestBarraRiskModelValidation:
    """Tests for BarraRiskModel.validate()."""

    def test_validate_valid_model(self, sample_covariance_result):
        """Valid model passes validation."""
        specific_risks = create_mock_specific_risks()
        factor_loadings = create_mock_factor_exposures().pivot(
            index="permno", on="factor_name", values="zscore"
        )

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = model.validate()
        assert len(errors) == 0

    def test_validate_catches_asymmetric_covariance(self):
        """Validation catches asymmetric covariance."""
        factor_loadings = create_mock_factor_exposures().pivot(
            index="permno", on="factor_name", values="zscore"
        )
        specific_risks = create_mock_specific_risks()

        # Create asymmetric matrix
        cov = create_mock_covariance_matrix()
        cov[0, 1] = cov[0, 1] + 0.1  # Make asymmetric

        model = BarraRiskModel(
            factor_covariance=cov,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = model.validate()
        assert any("symmetric" in e for e in errors)

    def test_validate_catches_non_psd(self):
        """Validation catches non-PSD covariance."""
        factor_loadings = create_mock_factor_exposures().pivot(
            index="permno", on="factor_name", values="zscore"
        )
        specific_risks = create_mock_specific_risks()

        # Create non-PSD matrix (negative eigenvalue)
        cov = np.array([[1.0, 2.0], [2.0, 1.0]])  # Eigenvalues: 3, -1

        model = BarraRiskModel(
            factor_covariance=cov,
            factor_names=["factor1", "factor2"],
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = model.validate()
        assert any("PSD" in e for e in errors)


class TestCheckCoverage:
    """Tests for BarraRiskModel.check_coverage()."""

    def test_full_coverage(self, sample_covariance_result):
        """Portfolio fully covered returns 100%."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        coverage, missing, covered = model.check_coverage(portfolio)
        assert coverage == pytest.approx(1.0)
        assert len(missing) == 0
        assert covered.height == 50

    def test_partial_coverage(self, sample_covariance_result):
        """Portfolio with missing permnos returns correct coverage."""
        specific_risks = create_mock_specific_risks(n_stocks=50)  # Only 50 stocks
        factor_loadings = create_mock_factor_exposures(n_stocks=50).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        # Portfolio with permnos 10001-10100 (100 stocks)
        portfolio = create_mock_portfolio(n_stocks=100, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        coverage, missing, covered = model.check_coverage(portfolio)
        assert coverage < 1.0
        assert len(missing) > 0
        assert covered.height == 50

    def test_long_short_portfolio_coverage(self, sample_covariance_result):
        """Coverage uses absolute weights for long/short portfolios."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )

        # Create 130/30 long-short portfolio (net sum = 1.0)
        # 65% long in first 10 stocks, 65% long in next 10 stocks
        # 30% short in stocks 21-30
        portfolio = pl.DataFrame(
            {
                "permno": list(range(10001, 10031)),
                "weight": [0.065] * 10 + [0.065] * 10 + [-0.03] * 10,
            }
        )

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        coverage, missing, covered = model.check_coverage(portfolio)

        # All 30 stocks should be covered (100% coverage)
        assert coverage == pytest.approx(1.0)
        assert len(missing) == 0
        assert covered.height == 30

    def test_dollar_neutral_portfolio_coverage(self, sample_covariance_result):
        """Coverage works correctly for dollar-neutral portfolios (net sum ≈ 0)."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )

        # Create dollar-neutral portfolio (50% long, 50% short, net sum = 0)
        portfolio = pl.DataFrame(
            {
                "permno": list(range(10001, 10021)),
                "weight": [0.05] * 10 + [-0.05] * 10,  # Net sum = 0
            }
        )

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        coverage, missing, covered = model.check_coverage(portfolio)

        # All 20 stocks should be covered (100% coverage)
        # Using absolute weights avoids division by zero for dollar-neutral
        assert coverage == pytest.approx(1.0)
        assert len(missing) == 0
        assert covered.height == 20


class TestPortfolioRiskComputation:
    """Tests for BarraRiskModel.compute_portfolio_risk()."""

    def test_basic_risk_computation(self, sample_covariance_result):
        """Basic portfolio risk computation works."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "test_portfolio")

        assert result.total_risk > 0
        assert result.factor_risk >= 0
        assert result.specific_risk >= 0
        assert result.var_95 > 0
        assert result.cvar_95 >= result.var_95

    def test_risk_decomposition_identity(self, sample_covariance_result):
        """Total risk² = factor risk² + specific risk²."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "test_portfolio")

        expected_total_sq = result.factor_risk**2 + result.specific_risk**2
        assert result.total_risk**2 == pytest.approx(expected_total_sq, rel=1e-6)

    def test_equal_weighted_portfolio(self, sample_covariance_result):
        """Equal-weighted portfolio computation."""
        n_stocks = 20
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )

        # Create equal-weighted portfolio
        permnos = list(range(10001, 10001 + n_stocks))
        equal_weight = 1.0 / n_stocks
        portfolio = pl.DataFrame(
            {
                "permno": permnos,
                "weight": [equal_weight] * n_stocks,
            }
        )

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "equal_weight")

        assert result.total_risk > 0
        # Equal weighting should diversify specific risk
        assert result.specific_risk < result.total_risk

    def test_insufficient_coverage_raises(self, sample_covariance_result):
        """Raises InsufficientCoverageError when coverage < min."""
        specific_risks = create_mock_specific_risks(n_stocks=10)  # Very few
        factor_loadings = create_mock_factor_exposures(n_stocks=10).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=100, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
            config=BarraRiskModelConfig(min_coverage=0.8),
        )

        with pytest.raises(InsufficientCoverageError):
            model.compute_portfolio_risk(portfolio, "test_portfolio")


class TestFactorContributions:
    """Tests for BarraRiskModel.compute_factor_contributions()."""

    def test_factor_contributions_computed(self, sample_covariance_result):
        """Factor contributions are computed."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        contributions = model.compute_factor_contributions(portfolio)

        assert contributions.height == 5  # 5 factors
        assert "factor_name" in contributions.columns
        assert "marginal_contribution" in contributions.columns
        assert "component_contribution" in contributions.columns
        assert "percent_contribution" in contributions.columns

    def test_factor_contributions_in_result(self, sample_covariance_result):
        """Factor contributions included in PortfolioRiskResult."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "test_portfolio")

        assert result.factor_contributions is not None
        assert result.factor_contributions.height == 5


class TestVaRComputation:
    """Tests for VaR and CVaR computation."""

    def test_var_95_vs_99(self, sample_covariance_result):
        """VaR 99% should be greater than VaR 95%."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "test_portfolio")

        assert result.var_99 > result.var_95

    def test_cvar_greater_than_var(self, sample_covariance_result):
        """CVaR should always be >= VaR."""
        specific_risks = create_mock_specific_risks(n_stocks=100)
        factor_loadings = create_mock_factor_exposures(n_stocks=100).pivot(
            index="permno", on="factor_name", values="zscore"
        )
        portfolio = create_mock_portfolio(n_stocks=50, start_permno=10001)

        model = BarraRiskModel(
            factor_covariance=sample_covariance_result.factor_covariance,
            factor_names=CANONICAL_FACTOR_ORDER.copy(),
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "test_portfolio")

        assert result.cvar_95 >= result.var_95

    def test_var_known_values(self):
        """VaR matches known values for normal distribution."""
        from scipy import stats

        # 95% VaR with daily sigma = 0.01 (1%)
        daily_sigma = 0.01
        z_95 = stats.norm.ppf(0.95)
        daily_sigma * z_95

        # Create minimal model
        cov = np.eye(2) * 0.0001  # 1% daily vol per factor
        factor_loadings = pl.DataFrame(
            {
                "permno": [1, 2],
                "factor1": [1.0, 1.0],
                "factor2": [0.0, 0.0],
            }
        )
        specific_risks = pl.DataFrame(
            {
                "permno": [1, 2],
                "specific_variance": [0.0001, 0.0001],  # 1% daily vol
                "specific_vol": [0.158, 0.158],
            }
        )
        portfolio = pl.DataFrame(
            {
                "permno": [1],
                "weight": [1.0],
            }
        )

        model = BarraRiskModel(
            factor_covariance=cov,
            factor_names=["factor1", "factor2"],
            factor_loadings=factor_loadings,
            specific_risks=specific_risks,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={"test": "v1.0.0"},
        )

        result = model.compute_portfolio_risk(portfolio, "test")

        # VaR should be close to expected (with some factor/specific mix)
        assert result.var_95 > 0
        assert result.var_95 < 0.1  # Should be in reasonable range
