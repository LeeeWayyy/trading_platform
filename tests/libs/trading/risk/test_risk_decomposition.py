"""
Tests for risk decomposition module.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest
from scipy import stats

from libs.trading.risk import (
    CANONICAL_FACTOR_ORDER,
    BarraRiskModel,
    PortfolioRiskResult,
    RiskDecomposer,
    compute_cvar_parametric,
    compute_var_parametric,
)
from tests.libs.trading.risk.conftest import (
    create_mock_factor_exposures,
    create_mock_portfolio,
    create_mock_specific_risks,
)


class TestPortfolioRiskResultValidation:
    """Tests for PortfolioRiskResult.validate()."""

    def test_validate_valid_result(self):
        """Valid result passes validation."""
        result = PortfolioRiskResult(
            analysis_id="test-123",
            portfolio_id="portfolio-1",
            as_of_date=date(2023, 6, 30),
            total_risk=0.15,  # 15% annual vol
            factor_risk=0.12,
            specific_risk=0.09,
            var_95=0.0164,  # ~1.64% daily VaR
            var_99=0.0233,
            cvar_95=0.0206,  # CVaR > VaR
            model_version="barra_v1.0",
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = result.validate()
        assert len(errors) == 0

    def test_validate_catches_total_less_than_factor(self):
        """Validation catches total risk < factor risk."""
        result = PortfolioRiskResult(
            analysis_id="test-123",
            portfolio_id="portfolio-1",
            as_of_date=date(2023, 6, 30),
            total_risk=0.10,  # Less than factor!
            factor_risk=0.12,
            specific_risk=0.09,
            var_95=0.0164,
            var_99=0.0233,
            cvar_95=0.0206,
            model_version="barra_v1.0",
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = result.validate()
        assert any("factor" in e.lower() for e in errors)

    def test_validate_catches_negative_var(self):
        """Validation catches negative VaR."""
        result = PortfolioRiskResult(
            analysis_id="test-123",
            portfolio_id="portfolio-1",
            as_of_date=date(2023, 6, 30),
            total_risk=0.15,
            factor_risk=0.12,
            specific_risk=0.09,
            var_95=-0.01,  # Negative!
            var_99=0.0233,
            cvar_95=0.0206,
            model_version="barra_v1.0",
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = result.validate()
        assert any("negative" in e.lower() for e in errors)

    def test_validate_catches_cvar_less_than_var(self):
        """Validation catches CVaR < VaR."""
        result = PortfolioRiskResult(
            analysis_id="test-123",
            portfolio_id="portfolio-1",
            as_of_date=date(2023, 6, 30),
            total_risk=0.15,
            factor_risk=0.12,
            specific_risk=0.09,
            var_95=0.0200,
            var_99=0.0233,
            cvar_95=0.0100,  # Less than VaR!
            model_version="barra_v1.0",
            dataset_version_ids={"test": "v1.0.0"},
        )

        errors = result.validate()
        assert any("cvar" in e.lower() for e in errors)


class TestPortfolioRiskResultStorageFormat:
    """Tests for PortfolioRiskResult.to_storage_format()."""

    def test_to_storage_format_schema(self):
        """Storage format matches expected schema."""
        factor_contributions = pl.DataFrame(
            {
                "factor_name": ["factor1", "factor2"],
                "marginal_contribution": [0.05, 0.03],
                "component_contribution": [0.02, 0.01],
                "percent_contribution": [0.4, 0.2],
            }
        )

        result = PortfolioRiskResult(
            analysis_id="test-123",
            portfolio_id="portfolio-1",
            as_of_date=date(2023, 6, 30),
            total_risk=0.15,
            factor_risk=0.12,
            specific_risk=0.09,
            var_95=0.0164,
            var_99=0.0233,
            cvar_95=0.0206,
            model_version="barra_v1.0",
            dataset_version_ids={"crsp": "v1.0.0", "compustat": "v1.0.0"},
            factor_contributions=factor_contributions,
        )

        portfolio_df, factor_df = result.to_storage_format()

        # Check portfolio schema
        required_columns = [
            "analysis_id",
            "portfolio_id",
            "as_of_date",
            "total_risk",
            "factor_risk",
            "specific_risk",
            "var_95",
            "var_99",
            "cvar_95",
            "model_version",
            "dataset_version_id",
            "computation_timestamp",
        ]
        for col in required_columns:
            assert col in portfolio_df.columns, f"Missing column: {col}"

        # Check factor contributions schema
        assert factor_df is not None
        factor_cols = [
            "analysis_id",
            "factor_name",
            "marginal_contribution",
            "component_contribution",
            "percent_contribution",
        ]
        for col in factor_cols:
            assert col in factor_df.columns, f"Missing column: {col}"

    def test_storage_analysis_id_links(self):
        """Analysis ID links portfolio and factor tables."""
        factor_contributions = pl.DataFrame(
            {
                "factor_name": ["factor1"],
                "marginal_contribution": [0.05],
                "component_contribution": [0.02],
                "percent_contribution": [0.4],
            }
        )

        result = PortfolioRiskResult(
            analysis_id="test-123",
            portfolio_id="portfolio-1",
            as_of_date=date(2023, 6, 30),
            total_risk=0.15,
            factor_risk=0.12,
            specific_risk=0.09,
            var_95=0.0164,
            var_99=0.0233,
            cvar_95=0.0206,
            model_version="barra_v1.0",
            dataset_version_ids={"test": "v1.0.0"},
            factor_contributions=factor_contributions,
        )

        portfolio_df, factor_df = result.to_storage_format()

        # Analysis IDs should match
        assert portfolio_df["analysis_id"][0] == "test-123"
        assert factor_df["analysis_id"][0] == "test-123"


class TestVaRParametric:
    """Tests for compute_var_parametric()."""

    def test_var_95_known_value(self):
        """VaR 95% matches known z-score."""
        daily_sigma = 0.01  # 1% daily vol
        z_95 = stats.norm.ppf(0.95)  # ~1.645
        expected_var = daily_sigma * z_95

        var = compute_var_parametric(daily_sigma, confidence=0.95)

        assert var == pytest.approx(expected_var, rel=1e-6)

    def test_var_99_known_value(self):
        """VaR 99% matches known z-score."""
        daily_sigma = 0.01  # 1% daily vol
        z_99 = stats.norm.ppf(0.99)  # ~2.326
        expected_var = daily_sigma * z_99

        var = compute_var_parametric(daily_sigma, confidence=0.99)

        assert var == pytest.approx(expected_var, rel=1e-6)

    def test_var_scales_with_holding_period(self):
        """VaR scales with sqrt of holding period."""
        daily_sigma = 0.01
        var_1d = compute_var_parametric(daily_sigma, holding_period_days=1)
        var_10d = compute_var_parametric(daily_sigma, holding_period_days=10)

        assert var_10d == pytest.approx(var_1d * np.sqrt(10), rel=1e-6)

    def test_var_with_expected_return(self):
        """VaR adjusts for expected return."""
        daily_sigma = 0.01
        expected_return = 0.001  # 0.1% daily

        var_zero_mu = compute_var_parametric(daily_sigma, expected_return=0.0)
        var_pos_mu = compute_var_parametric(daily_sigma, expected_return=expected_return)

        # Positive expected return reduces VaR
        assert var_pos_mu < var_zero_mu


class TestCVaRParametric:
    """Tests for compute_cvar_parametric()."""

    def test_cvar_95_known_value(self):
        """CVaR 95% matches expected formula."""
        daily_sigma = 0.01
        z_95 = stats.norm.ppf(0.95)
        pdf_at_z = stats.norm.pdf(z_95)
        expected_cvar = daily_sigma * pdf_at_z / (1 - 0.95)

        cvar = compute_cvar_parametric(daily_sigma, confidence=0.95)

        assert cvar == pytest.approx(expected_cvar, rel=1e-6)

    def test_cvar_greater_than_var(self):
        """CVaR >= VaR always."""
        daily_sigma = 0.01

        var = compute_var_parametric(daily_sigma, confidence=0.95)
        cvar = compute_cvar_parametric(daily_sigma, confidence=0.95)

        assert cvar >= var

    def test_cvar_99_greater_than_cvar_95(self):
        """CVaR 99% > CVaR 95%."""
        daily_sigma = 0.01

        cvar_95 = compute_cvar_parametric(daily_sigma, confidence=0.95)
        cvar_99 = compute_cvar_parametric(daily_sigma, confidence=0.99)

        assert cvar_99 > cvar_95

    def test_cvar_scales_with_holding_period(self):
        """CVaR scales with sqrt of holding period."""
        daily_sigma = 0.01
        cvar_1d = compute_cvar_parametric(daily_sigma, holding_period_days=1)
        cvar_10d = compute_cvar_parametric(daily_sigma, holding_period_days=10)

        assert cvar_10d == pytest.approx(cvar_1d * np.sqrt(10), rel=1e-6)


class TestRiskDecomposer:
    """Tests for RiskDecomposer class."""

    def test_decompose_returns_result(self, sample_covariance_result):
        """Decompose returns PortfolioRiskResult."""
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

        decomposer = RiskDecomposer(model)
        result = decomposer.decompose(portfolio, "test_portfolio")

        assert isinstance(result, PortfolioRiskResult)
        assert result.portfolio_id == "test_portfolio"
        assert result.total_risk > 0

    def test_get_factor_contributions(self, sample_covariance_result):
        """Get factor contributions returns DataFrame."""
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

        decomposer = RiskDecomposer(model)
        contributions = decomposer.get_factor_contributions(portfolio)

        assert contributions.height == 5  # 5 factors

    def test_check_portfolio_coverage(self, sample_covariance_result):
        """Check coverage returns ratio and missing permnos."""
        specific_risks = create_mock_specific_risks(n_stocks=50)
        factor_loadings = create_mock_factor_exposures(n_stocks=50).pivot(
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
        )

        decomposer = RiskDecomposer(model)
        coverage, missing = decomposer.check_portfolio_coverage(portfolio)

        assert coverage < 1.0
        assert len(missing) > 0


class TestRoundTrip:
    """Tests for storage round-trip."""

    def test_storage_round_trip(self, sample_covariance_result):
        """Compute -> store -> verify works."""
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
        portfolio_df, factor_df = result.to_storage_format()

        # Verify values preserved
        assert portfolio_df["total_risk"][0] == pytest.approx(result.total_risk)
        assert portfolio_df["factor_risk"][0] == pytest.approx(result.factor_risk)
        assert portfolio_df["specific_risk"][0] == pytest.approx(result.specific_risk)
        assert portfolio_df["var_95"][0] == pytest.approx(result.var_95)
        assert portfolio_df["cvar_95"][0] == pytest.approx(result.cvar_95)
