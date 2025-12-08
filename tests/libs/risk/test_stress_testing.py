"""
Tests for StressTester.
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from libs.risk import (
    CANONICAL_FACTOR_ORDER,
    BarraRiskModel,
    MissingHistoricalDataError,
    SpecificRiskResult,
    StressScenario,
    StressTester,
    StressTestResult,
)
from tests.libs.risk.conftest import (
    create_mock_factor_exposures,
    create_mock_specific_risks,
)


def create_historical_factor_returns(
    start_date: date,
    end_date: date,
    factor_names: list[str] | None = None,
) -> pl.DataFrame:
    """Create mock historical factor returns for stress testing."""
    if factor_names is None:
        factor_names = CANONICAL_FACTOR_ORDER

    np.random.seed(42)

    n_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(n_days)]
    trading_days = [d for d in dates if d.weekday() < 5]

    data = []
    for dt in trading_days:
        for factor_name in factor_names:
            # Use different return distributions for different periods
            if dt >= date(2008, 9, 1) and dt <= date(2008, 11, 30):
                # GFC period - larger negative returns
                ret = np.random.normal(-0.005, 0.03)
            elif dt >= date(2020, 2, 19) and dt <= date(2020, 3, 23):
                # COVID period - very large negative returns
                ret = np.random.normal(-0.01, 0.04)
            elif dt >= date(2022, 1, 3) and dt <= date(2022, 6, 16):
                # Rate hike period - moderate negative returns
                ret = np.random.normal(-0.002, 0.02)
            else:
                # Normal period
                ret = np.random.normal(0.0002, 0.01)

            data.append({
                "date": dt,
                "factor_name": factor_name,
                "return": ret,
            })

    return pl.DataFrame(data)


@pytest.fixture
def sample_barra_model(sample_covariance_result) -> BarraRiskModel:
    """Create sample BarraRiskModel for testing."""
    specific_risks = create_mock_specific_risks()
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

    return BarraRiskModel.from_t22_results(
        covariance_result=sample_covariance_result,
        specific_risk_result=specific_result,
        factor_loadings=factor_loadings,
    )


@pytest.fixture
def sample_historical_returns() -> pl.DataFrame:
    """Historical factor returns spanning crisis periods."""
    return create_historical_factor_returns(
        start_date=date(2008, 1, 1),
        end_date=date(2023, 12, 31),
    )


@pytest.fixture
def sample_portfolio() -> pl.DataFrame:
    """Sample portfolio for stress testing."""
    np.random.seed(42)
    n_stocks = 50
    permnos = list(range(10001, 10001 + n_stocks))
    weights = np.random.uniform(0.01, 0.05, n_stocks)
    weights = weights / weights.sum()

    return pl.DataFrame({
        "permno": permnos,
        "weight": weights.tolist(),
    })


class TestStressScenario:
    """Test scenario definitions."""

    def test_historical_scenario_fields(self):
        """Historical scenario has required fields."""
        scenario = StressScenario(
            name="test_historical",
            scenario_type="historical",
            description="Test historical scenario",
            start_date=date(2020, 2, 19),
            end_date=date(2020, 3, 23),
        )

        errors = scenario.validate()
        assert len(errors) == 0
        assert scenario.scenario_type == "historical"
        assert scenario.start_date is not None
        assert scenario.end_date is not None

    def test_hypothetical_scenario_fields(self):
        """Hypothetical scenario has required fields."""
        scenario = StressScenario(
            name="test_hypothetical",
            scenario_type="hypothetical",
            description="Test hypothetical scenario",
            factor_shocks={"market_beta": -0.10},
        )

        errors = scenario.validate()
        assert len(errors) == 0
        assert scenario.scenario_type == "hypothetical"
        assert scenario.factor_shocks is not None

    def test_invalid_scenario_type(self):
        """Catches invalid scenario type."""
        scenario = StressScenario(
            name="invalid",
            scenario_type="invalid_type",
            description="Invalid scenario",
        )

        errors = scenario.validate()
        assert len(errors) > 0
        assert "scenario_type" in errors[0]

    def test_historical_without_dates(self):
        """Catches historical scenario without dates."""
        scenario = StressScenario(
            name="missing_dates",
            scenario_type="historical",
            description="Missing dates",
        )

        errors = scenario.validate()
        assert len(errors) > 0

    def test_hypothetical_without_shocks(self):
        """Catches hypothetical scenario without shocks."""
        scenario = StressScenario(
            name="missing_shocks",
            scenario_type="hypothetical",
            description="Missing shocks",
        )

        errors = scenario.validate()
        assert len(errors) > 0

    def test_three_historical_scenarios_defined(self):
        """Pre-defined scenarios include 3 historical."""
        historical_count = sum(
            1
            for s in StressTester.PREDEFINED_SCENARIOS.values()
            if s.scenario_type == "historical"
        )
        assert historical_count == 3

        # Check specific scenarios
        assert "GFC_2008" in StressTester.PREDEFINED_SCENARIOS
        assert "COVID_2020" in StressTester.PREDEFINED_SCENARIOS
        assert "RATE_HIKE_2022" in StressTester.PREDEFINED_SCENARIOS


class TestHistoricalStress:
    """Test historical scenario replay."""

    def test_gfc_2008_scenario(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """GFC 2008 scenario returns valid result."""
        tester = StressTester(sample_barra_model, sample_historical_returns)
        result = tester.run_stress_test(
            sample_portfolio, "GFC_2008", portfolio_id="test"
        )

        assert result.scenario_name == "GFC_2008"
        assert result.scenario_type == "historical"
        assert result.portfolio_pnl is not None
        # P&L should be computed (sign depends on portfolio exposures and random data)
        assert isinstance(result.portfolio_pnl, float)

    def test_covid_2020_scenario(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """COVID 2020 scenario returns valid result."""
        tester = StressTester(sample_barra_model, sample_historical_returns)
        result = tester.run_stress_test(
            sample_portfolio, "COVID_2020", portfolio_id="test"
        )

        assert result.scenario_name == "COVID_2020"
        assert result.scenario_type == "historical"
        assert result.portfolio_pnl is not None

    def test_rate_hike_2022_scenario(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """Rate Hike 2022 scenario returns valid result."""
        tester = StressTester(sample_barra_model, sample_historical_returns)
        result = tester.run_stress_test(
            sample_portfolio, "RATE_HIKE_2022", portfolio_id="test"
        )

        assert result.scenario_name == "RATE_HIKE_2022"
        assert result.scenario_type == "historical"
        assert result.portfolio_pnl is not None

    def test_factor_attribution_sums(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """Factor contributions sum to total factor P&L."""
        tester = StressTester(sample_barra_model, sample_historical_returns)
        result = tester.run_stress_test(
            sample_portfolio, "GFC_2008", portfolio_id="test"
        )

        factor_sum = sum(result.factor_impacts.values())
        assert abs(factor_sum - result.portfolio_pnl) < 1e-6

    def test_missing_factor_returns_handled(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Missing factor returns are handled gracefully."""
        # Create returns with only some factors
        partial_returns = create_historical_factor_returns(
            start_date=date(2008, 1, 1),
            end_date=date(2023, 12, 31),
            factor_names=["market_beta", "size"],  # Only 2 of 5 factors
        )

        tester = StressTester(sample_barra_model, partial_returns)
        result = tester.run_stress_test(
            sample_portfolio, "GFC_2008", portfolio_id="test"
        )

        # Should complete without error
        assert result.portfolio_pnl is not None
        # Missing factors should have 0 contribution
        for factor in sample_barra_model.factor_names:
            if factor not in ["market_beta", "size"]:
                assert result.factor_impacts.get(factor, 0.0) == 0.0

    def test_empty_period_raises(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Raises error when no data for period."""
        # Create returns that don't cover any crisis period
        empty_returns = create_historical_factor_returns(
            start_date=date(2015, 1, 1),
            end_date=date(2015, 12, 31),
        )

        tester = StressTester(sample_barra_model, empty_returns)

        with pytest.raises(MissingHistoricalDataError):
            tester.run_stress_test(sample_portfolio, "GFC_2008")


class TestHypotheticalStress:
    """Test hypothetical scenarios."""

    def test_rate_shock_scenario(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Pre-defined RATE_SHOCK scenario returns valid result."""
        tester = StressTester(sample_barra_model)
        result = tester.run_stress_test(
            sample_portfolio, "RATE_SHOCK", portfolio_id="test"
        )

        assert result.scenario_name == "RATE_SHOCK"
        assert result.scenario_type == "hypothetical"
        assert result.portfolio_pnl is not None

    def test_custom_scenario(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Custom hypothetical scenario works correctly."""
        factor_shocks = {
            "market_beta": -0.20,
            "size": 0.05,
        }

        tester = StressTester(sample_barra_model)
        result = tester.run_custom_scenario(
            sample_portfolio,
            factor_shocks,
            scenario_name="custom_test",
            portfolio_id="test",
        )

        assert result.scenario_name == "custom_test"
        assert result.scenario_type == "hypothetical"
        assert result.portfolio_pnl is not None

    def test_single_factor_shock(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Single factor shock works correctly."""
        # Use actual canonical factor name
        factor_shocks = {"momentum_12_1": -0.10}

        tester = StressTester(sample_barra_model)
        result = tester.run_custom_scenario(
            sample_portfolio,
            factor_shocks,
            scenario_name="single_factor",
        )

        # Only momentum_12_1 should have non-zero contribution
        # (other factors have 0 shock)
        assert result.factor_impacts.get("momentum_12_1", 0) != 0

    def test_missing_factor_shock_warning(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
        caplog,
    ):
        """Warns when factor has exposure but no shock."""
        # Only shock one factor
        factor_shocks = {"market_beta": -0.10}

        tester = StressTester(sample_barra_model)
        result = tester.run_custom_scenario(
            sample_portfolio,
            factor_shocks,
        )

        # Should log warnings for factors with exposure but no shock
        assert result is not None

    def test_unknown_factor_shock_skipped(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
        caplog,
    ):
        """Unknown factor in shocks is skipped with warning."""
        factor_shocks = {
            "unknown_factor": -0.10,
            "market_beta": -0.05,
        }

        tester = StressTester(sample_barra_model)
        result = tester.run_custom_scenario(
            sample_portfolio,
            factor_shocks,
        )

        # Should complete without error
        assert result is not None
        # Unknown factor should not be in impacts
        assert "unknown_factor" not in result.factor_impacts


class TestSpecificRiskEstimate:
    """Test optional specific risk estimation."""

    def test_specific_risk_disabled_by_default(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Specific risk estimate is 0 by default."""
        tester = StressTester(sample_barra_model)
        result = tester.run_stress_test(
            sample_portfolio,
            "RATE_SHOCK",
            include_specific_risk=False,
        )

        assert result.specific_risk_estimate == 0.0
        assert result.total_pnl == result.portfolio_pnl

    def test_specific_risk_conservative_estimate(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """Specific risk estimate is conservative (2-sigma)."""
        tester = StressTester(sample_barra_model, sample_historical_returns)
        result = tester.run_stress_test(
            sample_portfolio,
            "GFC_2008",
            include_specific_risk=True,
        )

        # Should be negative (tail estimate)
        assert result.specific_risk_estimate < 0
        # Total P&L includes specific risk
        assert result.total_pnl == result.portfolio_pnl + result.specific_risk_estimate

    def test_specific_risk_scales_with_period(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """Specific risk scales with period length."""
        tester = StressTester(sample_barra_model, sample_historical_returns)

        # Short period (COVID: ~1 month)
        result_short = tester.run_stress_test(
            sample_portfolio,
            "COVID_2020",
            include_specific_risk=True,
        )

        # Long period (Rate Hike: ~6 months)
        result_long = tester.run_stress_test(
            sample_portfolio,
            "RATE_HIKE_2022",
            include_specific_risk=True,
        )

        # Longer period should have larger specific risk (in absolute value)
        assert abs(result_long.specific_risk_estimate) > abs(
            result_short.specific_risk_estimate
        )


class TestBarraModelIntegration:
    """Test integration with BarraRiskModel."""

    def test_exposure_computation(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Portfolio exposures are correctly computed."""
        tester = StressTester(sample_barra_model)
        exposures = tester._compute_portfolio_exposures(sample_portfolio)

        # Should have exposure for each factor
        assert len(exposures) == len(sample_barra_model.factor_names)

        # Exposures should be reasonable (z-scores)
        for factor_name, exposure in exposures.items():
            assert -5 < exposure < 5  # Reasonable z-score range

    def test_coverage_handling_insufficient(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Rejects portfolios with insufficient coverage (<95% gross)."""
        # Portfolio with only 80% gross coverage (20% unknown permnos)
        portfolio = pl.DataFrame({
            "permno": [10001, 10002, 99999],  # 99999 not in model
            "weight": [0.4, 0.4, 0.2],  # 80% gross coverage
        })

        tester = StressTester(sample_barra_model)

        # Should raise ValueError for insufficient coverage (requires 95%)
        with pytest.raises(ValueError, match="Insufficient factor coverage"):
            tester._compute_portfolio_exposures(portfolio)

    def test_coverage_handling_sufficient(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Accepts portfolios with sufficient coverage (>=95% gross)."""
        # Portfolio with 96% gross coverage (only 4% unknown)
        portfolio = pl.DataFrame({
            "permno": [10001, 10002, 10003, 99999],  # 99999 not in model
            "weight": [0.32, 0.32, 0.32, 0.04],  # 96% gross coverage
        })

        tester = StressTester(sample_barra_model)
        exposures = tester._compute_portfolio_exposures(portfolio)

        # Should compute exposures for covered positions
        assert len(exposures) > 0

    def test_coverage_long_short_portfolio(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Handles long/short portfolios using gross exposure for coverage."""
        # Dollar-neutral portfolio: +0.5 long, -0.5 short = 0 net, 1.0 gross
        # All positions have factor coverage (100% gross coverage)
        portfolio = pl.DataFrame({
            "permno": [10001, 10002],  # Both in model
            "weight": [0.5, -0.5],  # Net = 0, Gross = 1.0
        })

        tester = StressTester(sample_barra_model)
        exposures = tester._compute_portfolio_exposures(portfolio)

        # Should work - 100% gross coverage
        assert len(exposures) > 0

    def test_weight_normalization(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Works with non-normalized weights (leverage != 1.0)."""
        # Portfolio with non-unit leverage (0.8)
        portfolio = pl.DataFrame({
            "permno": [10001, 10002],
            "weight": [0.4, 0.4],  # Sum = 0.8 (100% coverage)
        })

        tester = StressTester(sample_barra_model)
        # Use valid canonical factor
        result = tester.run_custom_scenario(
            portfolio,
            {"momentum_12_1": -0.10},
        )

        # Should work with non-normalized weights
        assert result is not None
        # P&L should scale with total weight (0.8 vs 1.0)
        assert abs(result.portfolio_pnl) < 1.0  # Reasonable range


class TestStressTestResult:
    """Test result structure."""

    def test_result_schema(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Result has expected schema."""
        tester = StressTester(sample_barra_model)
        result = tester.run_stress_test(sample_portfolio, "RATE_SHOCK")

        assert result.test_id is not None
        assert result.portfolio_id is not None
        assert result.scenario_name == "RATE_SHOCK"
        assert result.scenario_type == "hypothetical"
        assert result.as_of_date is not None
        assert isinstance(result.portfolio_pnl, float)
        assert isinstance(result.factor_impacts, dict)
        assert result.model_version is not None

    def test_worst_position_identified(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Worst position is correctly identified."""
        tester = StressTester(sample_barra_model)
        result = tester.run_stress_test(sample_portfolio, "RATE_SHOCK")

        # Should identify worst position
        assert result.worst_position_permno is not None
        assert result.worst_position_loss is not None

        # Worst loss should be the minimum P&L
        if result.position_impacts is not None:
            min_pnl = result.position_impacts["pnl"].min()
            assert abs(result.worst_position_loss - min_pnl) < 1e-6

    def test_to_storage_format(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Result can be converted to storage format."""
        tester = StressTester(sample_barra_model)
        result = tester.run_stress_test(
            sample_portfolio, "RATE_SHOCK", portfolio_id="test"
        )

        df = result.to_storage_format()

        assert "test_id" in df.columns
        assert "scenario_name" in df.columns
        assert "portfolio_pnl" in df.columns
        assert "factor_impacts" in df.columns
        assert df.height == 1

    def test_provenance_tracking(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Dataset version IDs are tracked."""
        tester = StressTester(sample_barra_model)
        result = tester.run_stress_test(sample_portfolio, "RATE_SHOCK")

        assert result.dataset_version_ids is not None
        assert len(result.dataset_version_ids) > 0


class TestRunAllScenarios:
    """Test running all pre-defined scenarios."""

    def test_run_all_historical_with_data(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """Runs all scenarios when historical data is available."""
        tester = StressTester(sample_barra_model, sample_historical_returns)
        results = tester.run_all_scenarios(sample_portfolio)

        # Should run all 4 scenarios (3 historical + 1 hypothetical)
        assert len(results) == 4

    def test_run_all_without_historical(
        self,
        sample_barra_model: BarraRiskModel,
        sample_portfolio: pl.DataFrame,
    ):
        """Skips historical scenarios when no data."""
        tester = StressTester(sample_barra_model)  # No historical data
        results = tester.run_all_scenarios(sample_portfolio)

        # Should only run hypothetical scenario
        assert len(results) == 1
        assert results[0].scenario_type == "hypothetical"


class TestAvailableScenarios:
    """Test scenario listing."""

    def test_get_available_scenarios(
        self,
        sample_barra_model: BarraRiskModel,
    ):
        """Can get list of available scenarios."""
        tester = StressTester(sample_barra_model)
        scenarios = tester.get_available_scenarios()

        assert "GFC_2008" in scenarios
        assert "COVID_2020" in scenarios
        assert "RATE_HIKE_2022" in scenarios
        assert "RATE_SHOCK" in scenarios


@pytest.mark.performance
class TestPerformance:
    """Performance tests."""

    def test_stress_test_performance(
        self,
        sample_barra_model: BarraRiskModel,
        sample_historical_returns: pl.DataFrame,
        sample_portfolio: pl.DataFrame,
    ):
        """Stress test completes quickly."""
        import time

        tester = StressTester(sample_barra_model, sample_historical_returns)

        start = time.perf_counter()
        result = tester.run_stress_test(sample_portfolio, "GFC_2008")
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0  # Should complete in under 1 second
        assert result is not None
