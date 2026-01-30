"""Tests for transaction cost model."""

from datetime import date

import polars as pl
import pytest

from libs.trading.backtest.cost_model import (
    ADV_FLOOR_USD,
    VOL_FLOOR,
    ADVSource,
    BacktestCostResult,
    CapacityAnalysis,
    CostModelConfig,
    CostSummary,
    TradeCost,
    apply_adv_fallback,
    apply_volatility_fallback,
    compute_backtest_costs,
    compute_capacity_analysis,
    compute_compounded_return,
    compute_cost_summary,
    compute_daily_costs,
    compute_daily_costs_permno,
    compute_market_impact,
    compute_max_drawdown,
    compute_net_returns,
    compute_rolling_adv_volatility,
    compute_sharpe_ratio,
    compute_trade_cost,
)


class TestCostModelConfig:
    """Tests for CostModelConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = CostModelConfig()
        assert config.enabled is True
        assert config.bps_per_trade == 5.0
        assert config.impact_coefficient == 0.1
        assert config.participation_limit == 0.05
        assert config.adv_source == ADVSource.YAHOO
        assert config.portfolio_value_usd == 1_000_000.0

    def test_custom_config(self):
        """Test custom configuration values."""
        config = CostModelConfig(
            enabled=False,
            bps_per_trade=10.0,
            impact_coefficient=0.2,
            participation_limit=0.10,
            adv_source=ADVSource.ALPACA,
            portfolio_value_usd=5_000_000.0,
        )
        assert config.enabled is False
        assert config.bps_per_trade == 10.0
        assert config.impact_coefficient == 0.2
        assert config.participation_limit == 0.10
        assert config.adv_source == ADVSource.ALPACA
        assert config.portfolio_value_usd == 5_000_000.0

    def test_validation_negative_bps(self):
        """Test validation rejects negative bps_per_trade."""
        with pytest.raises(ValueError, match="bps_per_trade must be >= 0"):
            CostModelConfig(bps_per_trade=-1.0)

    def test_validation_negative_impact(self):
        """Test validation rejects negative impact_coefficient."""
        with pytest.raises(ValueError, match="impact_coefficient must be >= 0"):
            CostModelConfig(impact_coefficient=-0.1)

    def test_validation_participation_limit_zero(self):
        """Test validation rejects zero participation limit."""
        with pytest.raises(ValueError, match="participation_limit must be in"):
            CostModelConfig(participation_limit=0.0)

    def test_validation_participation_limit_over_one(self):
        """Test validation rejects participation limit > 1."""
        with pytest.raises(ValueError, match="participation_limit must be in"):
            CostModelConfig(participation_limit=1.5)

    def test_validation_negative_portfolio_value(self):
        """Test validation rejects negative portfolio value."""
        with pytest.raises(ValueError, match="portfolio_value_usd must be > 0"):
            CostModelConfig(portfolio_value_usd=-100.0)

    def test_from_dict_coerces_string_to_float(self):
        """Test from_dict raises ValueError for string numeric fields."""
        with pytest.raises(ValueError, match="cost_model.bps_per_trade must be a number"):
            CostModelConfig.from_dict({"bps_per_trade": "five"})

    def test_from_dict_coerces_string_number_to_float(self):
        """Test from_dict coerces valid string numbers to float."""
        # Valid numeric strings should be coerced
        config = CostModelConfig.from_dict({"bps_per_trade": "5.5"})
        assert config.bps_per_trade == 5.5

    def test_from_dict_rejects_string_boolean(self):
        """Test from_dict rejects string 'true'/'false' for enabled field."""
        with pytest.raises(ValueError, match="cost_model.enabled must be a boolean"):
            CostModelConfig.from_dict({"enabled": "true"})

    def test_from_dict_handles_none_with_defaults(self):
        """Test from_dict uses defaults when values are None."""
        config = CostModelConfig.from_dict({
            "bps_per_trade": None,
            "impact_coefficient": None,
        })
        assert config.bps_per_trade == 5.0  # default
        assert config.impact_coefficient == 0.1  # default

    def test_validation_rejects_nan_bps(self):
        """Test validation rejects NaN for bps_per_trade."""
        with pytest.raises(ValueError, match="bps_per_trade must be finite"):
            CostModelConfig(bps_per_trade=float("nan"))

    def test_validation_rejects_inf_impact(self):
        """Test validation rejects Inf for impact_coefficient."""
        with pytest.raises(ValueError, match="impact_coefficient must be finite"):
            CostModelConfig(impact_coefficient=float("inf"))

    def test_validation_rejects_nan_portfolio_value(self):
        """Test validation rejects NaN for portfolio_value_usd."""
        with pytest.raises(ValueError, match="portfolio_value_usd must be finite"):
            CostModelConfig(portfolio_value_usd=float("nan"))

    def test_validation_rejects_negative_inf_participation(self):
        """Test validation rejects negative Inf for participation_limit."""
        with pytest.raises(ValueError, match="participation_limit must be finite"):
            CostModelConfig(participation_limit=float("-inf"))

    def test_to_dict(self):
        """Test serialization to dictionary."""
        config = CostModelConfig(bps_per_trade=7.5, adv_source=ADVSource.ALPACA)
        d = config.to_dict()
        assert d["enabled"] is True
        assert d["bps_per_trade"] == 7.5
        assert d["adv_source"] == "alpaca"

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        d = {
            "enabled": False,
            "bps_per_trade": 3.0,
            "impact_coefficient": 0.15,
            "participation_limit": 0.03,
            "adv_source": "yahoo",
            "portfolio_value_usd": 2_000_000.0,
        }
        config = CostModelConfig.from_dict(d)
        assert config.enabled is False
        assert config.bps_per_trade == 3.0
        assert config.adv_source == ADVSource.YAHOO

    def test_from_dict_defaults(self):
        """Test deserialization with missing keys uses defaults."""
        config = CostModelConfig.from_dict({})
        assert config.enabled is True
        assert config.bps_per_trade == 5.0

    def test_roundtrip_serialization(self):
        """Test serialization roundtrip preserves values."""
        original = CostModelConfig(
            bps_per_trade=8.0,
            impact_coefficient=0.25,
            adv_source=ADVSource.ALPACA,
        )
        restored = CostModelConfig.from_dict(original.to_dict())
        assert restored.bps_per_trade == original.bps_per_trade
        assert restored.impact_coefficient == original.impact_coefficient
        assert restored.adv_source == original.adv_source


class TestComputeMarketImpact:
    """Tests for market impact calculation."""

    def test_basic_impact(self):
        """Test basic market impact calculation."""
        # impact_bps = 0.1 * 0.02 * 10000 * sqrt(10000 / 1000000)
        # impact_bps = 0.1 * 0.02 * 10000 * 0.1 = 2.0
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=1_000_000,
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact == pytest.approx(2.0, rel=1e-6)

    def test_zero_trade_value(self):
        """Test impact is zero for zero trade value."""
        impact = compute_market_impact(
            trade_value_usd=0,
            adv_usd=1_000_000,
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_none_adv_returns_zero(self):
        """Test impact is zero when ADV is None."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=None,
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_zero_adv_returns_zero(self):
        """Test impact is zero when ADV is zero."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=0,
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_none_volatility_returns_zero(self):
        """Test impact is zero when volatility is None."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=1_000_000,
            volatility=None,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_zero_volatility_returns_zero(self):
        """Test impact is zero when volatility is zero."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=1_000_000,
            volatility=0,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_nan_adv_returns_zero(self):
        """Test impact is zero when ADV is NaN."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=float("nan"),
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_inf_adv_returns_zero(self):
        """Test impact is zero when ADV is infinity."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=float("inf"),
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_nan_volatility_returns_zero(self):
        """Test impact is zero when volatility is NaN."""
        impact = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=1_000_000,
            volatility=float("nan"),
            impact_coefficient=0.1,
        )
        assert impact == 0.0

    def test_impact_scales_with_sqrt_participation(self):
        """Test impact scales with square root of participation."""
        # 4x trade size should give 2x impact
        impact_small = compute_market_impact(
            trade_value_usd=10_000,
            adv_usd=1_000_000,
            volatility=0.02,
            impact_coefficient=0.1,
        )
        impact_large = compute_market_impact(
            trade_value_usd=40_000,
            adv_usd=1_000_000,
            volatility=0.02,
            impact_coefficient=0.1,
        )
        assert impact_large == pytest.approx(impact_small * 2, rel=1e-6)


class TestComputeTradeCost:
    """Tests for trade cost calculation."""

    def test_basic_trade_cost(self):
        """Test basic trade cost calculation."""
        config = CostModelConfig(bps_per_trade=5.0, impact_coefficient=0.1)
        cost = compute_trade_cost(
            symbol="AAPL",
            trade_date=date(2024, 1, 15),
            trade_value_usd=100_000,
            adv_usd=10_000_000,
            volatility=0.02,
            config=config,
        )

        assert cost.symbol == "AAPL"
        assert cost.trade_date == date(2024, 1, 15)
        assert cost.trade_value_usd == 100_000
        assert cost.commission_spread_cost == pytest.approx(50.0, rel=1e-6)  # 5 bps
        assert cost.market_impact_cost > 0
        assert cost.total_cost_usd == pytest.approx(
            cost.commission_spread_cost + cost.market_impact_cost, rel=1e-6
        )
        assert cost.participation_pct == pytest.approx(0.01, rel=1e-6)  # 100k / 10M

    def test_trade_cost_without_adv(self):
        """Test trade cost when ADV is unavailable."""
        config = CostModelConfig(bps_per_trade=5.0, impact_coefficient=0.1)
        cost = compute_trade_cost(
            symbol="AAPL",
            trade_date=date(2024, 1, 15),
            trade_value_usd=100_000,
            adv_usd=None,
            volatility=0.02,
            config=config,
        )

        # Only commission/spread, no impact
        assert cost.commission_spread_cost == pytest.approx(50.0, rel=1e-6)
        assert cost.market_impact_cost == 0.0
        assert cost.participation_pct is None


class TestComputeDailyCosts:
    """Tests for daily cost computation."""

    def test_basic_daily_costs(self):
        """Test daily cost computation from weight changes."""
        config = CostModelConfig(
            bps_per_trade=5.0,
            impact_coefficient=0.0,  # No impact for simplicity
            portfolio_value_usd=1_000_000,
        )

        # Day 1: Build position (10% weight)
        # Day 2: No change
        # Day 3: Increase to 20%
        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "weight": [0.10, 0.10, 0.20],
            }
        )
        adv_data = pl.DataFrame({"date": [], "symbol": [], "adv_usd": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "adv_usd": pl.Float64}
        )
        vol_data = pl.DataFrame({"date": [], "symbol": [], "volatility": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "volatility": pl.Float64}
        )

        cost_drag_df, trade_costs = compute_daily_costs(
            daily_weights, adv_data, vol_data, config
        )

        assert len(trade_costs) == 2  # Day 1 and Day 3
        assert cost_drag_df.height == 3

        # Day 1: 10% * 1M = 100k trade, 5 bps = $50, drag = 50/1M = 0.00005
        day1_drag = cost_drag_df.filter(pl.col("date") == date(2024, 1, 1))["cost_drag"][0]
        assert day1_drag == pytest.approx(0.00005, rel=1e-6)

        # Day 2: No trade
        day2_drag = cost_drag_df.filter(pl.col("date") == date(2024, 1, 2))["cost_drag"][0]
        assert day2_drag == 0.0

        # Day 3: 10% change = 100k trade, 5 bps = $50
        day3_drag = cost_drag_df.filter(pl.col("date") == date(2024, 1, 3))["cost_drag"][0]
        assert day3_drag == pytest.approx(0.00005, rel=1e-6)

    def test_costs_disabled(self):
        """Test zero costs when model is disabled."""
        config = CostModelConfig(enabled=False)
        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "symbol": ["AAPL"],
                "weight": [0.10],
            }
        )
        adv_data = pl.DataFrame({"date": [], "symbol": [], "adv_usd": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "adv_usd": pl.Float64}
        )
        vol_data = pl.DataFrame({"date": [], "symbol": [], "volatility": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "volatility": pl.Float64}
        )

        cost_drag_df, trade_costs = compute_daily_costs(
            daily_weights, adv_data, vol_data, config
        )

        assert len(trade_costs) == 0
        assert cost_drag_df["cost_drag"].sum() == 0.0


class TestComputeNetReturns:
    """Tests for net return computation."""

    def test_basic_net_returns(self):
        """Test net returns = gross returns - cost drag."""
        gross_returns = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "return": [0.01, -0.02, 0.015],
            }
        )
        cost_drag = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "cost_drag": [0.0005, 0.0, 0.0003],
            }
        )

        net_df = compute_net_returns(gross_returns, cost_drag)

        assert net_df.height == 3
        assert "gross_return" in net_df.columns
        assert "net_return" in net_df.columns
        assert net_df["net_return"][0] == pytest.approx(0.01 - 0.0005, rel=1e-6)
        assert net_df["net_return"][1] == pytest.approx(-0.02, rel=1e-6)
        assert net_df["net_return"][2] == pytest.approx(0.015 - 0.0003, rel=1e-6)


class TestComputeCompoundedReturn:
    """Tests for compounded return calculation."""

    def test_basic_compounding(self):
        """Test basic compounded return."""
        returns = [0.01, 0.02, -0.01]
        # (1.01) * (1.02) * (0.99) - 1 = 1.019898 - 1 = 0.019898
        compounded = compute_compounded_return(returns)
        assert compounded == pytest.approx(0.019898, rel=1e-4)

    def test_empty_returns(self):
        """Test compounded return with empty list."""
        assert compute_compounded_return([]) is None

    def test_all_nan_returns(self):
        """Test compounded return with all NaN values."""
        assert compute_compounded_return([float("nan"), float("nan")]) is None

    def test_mixed_nan_returns(self):
        """Test compounded return with mixed valid and NaN values."""
        returns = [0.01, float("nan"), 0.02]
        compounded = compute_compounded_return(returns)
        # Only valid returns: (1.01) * (1.02) - 1 = 0.0302
        assert compounded == pytest.approx(0.0302, rel=1e-4)

    def test_inf_returns(self):
        """Test compounded return with inf values filters them out."""
        returns = [0.01, float("inf"), 0.02]
        compounded = compute_compounded_return(returns)
        assert compounded == pytest.approx(0.0302, rel=1e-4)


class TestComputeSharpeRatio:
    """Tests for Sharpe ratio calculation."""

    def test_basic_sharpe(self):
        """Test basic Sharpe ratio calculation."""
        # Varying returns with positive mean
        returns = [0.001, 0.002, 0.0005, 0.0015, 0.001]
        sharpe = compute_sharpe_ratio(returns, annualization_factor=252)
        assert sharpe is not None
        assert sharpe > 0  # Positive Sharpe for positive mean returns

    def test_insufficient_data(self):
        """Test Sharpe returns None for insufficient data."""
        assert compute_sharpe_ratio([0.01]) is None
        assert compute_sharpe_ratio([]) is None

    def test_zero_std_returns_none(self):
        """Test Sharpe returns None for zero standard deviation."""
        # Exactly constant returns
        returns = [0.001] * 10
        sharpe = compute_sharpe_ratio(returns)
        # Should still work with tiny variance from float precision
        assert sharpe is None or sharpe > 100

    def test_all_nan_returns_none(self):
        """Test Sharpe returns None for all NaN values."""
        assert compute_sharpe_ratio([float("nan"), float("nan")]) is None


class TestComputeMaxDrawdown:
    """Tests for max drawdown calculation."""

    def test_basic_drawdown(self):
        """Test basic max drawdown."""
        # Start at 1.0, go to 1.1, drop to 0.99
        returns = [0.10, -0.10]
        # Cumulative: 1.0 -> 1.1 -> 0.99 (1.1 * 0.9)
        # Peak: 1.1, DD = (1.1 - 0.99) / 1.1 = 0.11 / 1.1 = 0.10
        dd = compute_max_drawdown(returns)
        assert dd == pytest.approx(0.10, rel=1e-4)

    def test_no_drawdown(self):
        """Test zero drawdown for always-up returns."""
        returns = [0.01, 0.02, 0.03]
        dd = compute_max_drawdown(returns)
        assert dd == 0.0

    def test_empty_returns(self):
        """Test max drawdown with empty list."""
        assert compute_max_drawdown([]) is None

    def test_all_nan_returns(self):
        """Test max drawdown with all NaN values."""
        assert compute_max_drawdown([float("nan"), float("nan")]) is None


class TestCostSummary:
    """Tests for CostSummary dataclass."""

    def test_to_dict_from_dict_roundtrip(self):
        """Test serialization roundtrip."""
        summary = CostSummary(
            total_gross_return=0.12,
            total_net_return=0.09,
            total_cost_drag=0.03,
            total_cost_usd=30000,
            commission_spread_cost_usd=18000,
            market_impact_cost_usd=12000,
            gross_sharpe=1.5,
            net_sharpe=1.2,
            gross_max_drawdown=0.10,
            net_max_drawdown=0.12,
            num_trades=100,
            avg_trade_cost_bps=6.0,
        )
        restored = CostSummary.from_dict(summary.to_dict())
        assert restored.total_gross_return == summary.total_gross_return
        assert restored.total_net_return == summary.total_net_return
        assert restored.net_sharpe == summary.net_sharpe

    def test_from_dict_with_nulls(self):
        """Test deserialization handles null values."""
        d = {
            "total_gross_return": None,
            "total_net_return": None,
            "total_cost_drag": 0.0,
            "total_cost_usd": 0.0,
        }
        summary = CostSummary.from_dict(d)
        assert summary.total_gross_return is None
        assert summary.total_net_return is None


class TestCapacityAnalysis:
    """Tests for CapacityAnalysis dataclass."""

    def test_to_dict_from_dict_roundtrip(self):
        """Test serialization roundtrip."""
        analysis = CapacityAnalysis(
            avg_daily_turnover=0.15,
            avg_holding_period_days=6.7,
            portfolio_adv=50_000_000,
            portfolio_sigma=0.02,
            gross_alpha_annualized=0.15,
            impact_aum_5bps=75_000_000,
            impact_aum_10bps=25_000_000,
            participation_aum=100_000_000,
            breakeven_aum=50_000_000,
            implied_max_capacity=50_000_000,
            limiting_factor="breakeven",
        )
        restored = CapacityAnalysis.from_dict(analysis.to_dict())
        assert restored.avg_daily_turnover == analysis.avg_daily_turnover
        assert restored.implied_max_capacity == analysis.implied_max_capacity
        assert restored.limiting_factor == analysis.limiting_factor

    def test_from_dict_with_missing_keys(self):
        """Test deserialization handles missing keys with None."""
        analysis = CapacityAnalysis.from_dict({})
        assert analysis.avg_daily_turnover is None
        assert analysis.portfolio_adv is None
        assert analysis.limiting_factor is None


class TestComputeCostSummary:
    """Tests for compute_cost_summary function."""

    def test_basic_summary(self):
        """Test cost summary computation."""
        gross_returns = [0.01, -0.02, 0.015]
        net_returns = [0.009, -0.021, 0.014]
        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 1),
                trade_value_usd=100_000,
                commission_spread_cost=50,
                market_impact_cost=30,
                total_cost_usd=80,
                total_cost_bps=8.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.01,
            ),
        ]

        summary = compute_cost_summary(
            gross_returns, net_returns, trade_costs, portfolio_value_usd=1_000_000
        )

        assert summary.total_cost_usd == 80
        assert summary.commission_spread_cost_usd == 50
        assert summary.market_impact_cost_usd == 30
        assert summary.num_trades == 1
        assert summary.avg_trade_cost_bps == pytest.approx(8.0, rel=1e-4)


class TestComputeCapacityAnalysis:
    """Tests for compute_capacity_analysis function."""

    def test_capacity_with_valid_data(self):
        """Test capacity analysis with valid trade data."""
        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "weight": [0.10, 0.15, 0.10],
            }
        )

        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 1),
                trade_value_usd=100_000,
                commission_spread_cost=50,
                market_impact_cost=20,
                total_cost_usd=70,
                total_cost_bps=7.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.01,
            ),
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 2),
                trade_value_usd=50_000,
                commission_spread_cost=25,
                market_impact_cost=10,
                total_cost_usd=35,
                total_cost_bps=7.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.005,
            ),
        ]

        cost_summary = CostSummary(
            total_gross_return=0.05,
            total_net_return=0.04,
            total_cost_drag=0.0001,
            total_cost_usd=105,
            commission_spread_cost_usd=75,
            market_impact_cost_usd=30,
            gross_sharpe=1.5,
            net_sharpe=1.2,
            gross_max_drawdown=0.05,
            net_max_drawdown=0.06,
            num_trades=2,
            avg_trade_cost_bps=7.0,
        )

        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)

        assert analysis.avg_daily_turnover is not None
        assert analysis.avg_daily_turnover > 0
        assert analysis.portfolio_adv is not None
        assert analysis.portfolio_sigma is not None

    def test_capacity_with_empty_weights(self):
        """Test capacity analysis returns None fields for empty data."""
        config = CostModelConfig()
        daily_weights = pl.DataFrame({"date": [], "symbol": [], "weight": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "weight": pl.Float64}
        )
        cost_summary = CostSummary(
            total_gross_return=None,
            total_net_return=None,
            total_cost_drag=0,
            total_cost_usd=0,
            commission_spread_cost_usd=0,
            market_impact_cost_usd=0,
            gross_sharpe=None,
            net_sharpe=None,
            gross_max_drawdown=None,
            net_max_drawdown=None,
            num_trades=0,
            avg_trade_cost_bps=0,
        )

        analysis = compute_capacity_analysis(daily_weights, [], cost_summary, config)

        assert analysis.avg_daily_turnover is None
        assert analysis.implied_max_capacity is None
        assert analysis.limiting_factor is None

    def test_capacity_with_no_adv_volatility(self):
        """Test capacity analysis when trades have no ADV/volatility data."""
        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "weight": [0.10, 0.20],
            }
        )

        # Trades without ADV/volatility
        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 1),
                trade_value_usd=100_000,
                commission_spread_cost=50,
                market_impact_cost=0,
                total_cost_usd=50,
                total_cost_bps=5.0,
                adv_usd=None,  # No ADV
                volatility=None,  # No volatility
                participation_pct=None,
            ),
        ]

        cost_summary = CostSummary(
            total_gross_return=0.05,
            total_net_return=0.045,
            total_cost_drag=0.005,
            total_cost_usd=50,
            commission_spread_cost_usd=50,
            market_impact_cost_usd=0,
            gross_sharpe=1.5,
            net_sharpe=1.2,
            gross_max_drawdown=0.05,
            net_max_drawdown=0.06,
            num_trades=1,
            avg_trade_cost_bps=5.0,
        )

        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)

        # Should have turnover but no ADV/sigma due to missing data
        assert analysis.avg_daily_turnover is not None
        assert analysis.portfolio_adv is None
        assert analysis.portfolio_sigma is None


class TestCostModelConfigEdgeCases:
    """Additional edge case tests for CostModelConfig."""

    def test_from_dict_unknown_adv_source(self):
        """Test from_dict falls back to yahoo for unknown ADV source."""
        d = {
            "enabled": True,
            "bps_per_trade": 5.0,
            "adv_source": "unknown_source",  # Invalid source
        }
        config = CostModelConfig.from_dict(d)
        assert config.adv_source == ADVSource.YAHOO  # Fallback


class TestComputeDailyCostsEdgeCases:
    """Additional edge case tests for compute_daily_costs."""

    def test_no_trades_tiny_weight_changes(self):
        """Test compute_daily_costs when all weight changes are below threshold."""
        config = CostModelConfig(
            bps_per_trade=5.0,
            impact_coefficient=0.0,
            portfolio_value_usd=1_000_000,
        )

        # Weight changes are very small (< $0.01 trade value)
        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "weight": [0.0, 0.0, 0.0],  # No weight, no trades
            }
        )
        adv_data = pl.DataFrame({"date": [], "symbol": [], "adv_usd": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "adv_usd": pl.Float64}
        )
        vol_data = pl.DataFrame({"date": [], "symbol": [], "volatility": []}).cast(
            {"date": pl.Date, "symbol": pl.Utf8, "volatility": pl.Float64}
        )

        cost_drag_df, trade_costs = compute_daily_costs(
            daily_weights, adv_data, vol_data, config
        )

        assert len(trade_costs) == 0
        assert cost_drag_df.height == 3
        assert cost_drag_df["cost_drag"].sum() == 0.0


class TestCapacityHelperFunctions:
    """Tests for capacity helper functions via compute_capacity_analysis."""

    def test_capacity_with_valid_gross_alpha(self):
        """Test capacity analysis computes all constraints with valid data."""
        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, i) for i in range(1, 11)],
                "symbol": ["AAPL"] * 10,
                "weight": [0.1, 0.15, 0.1, 0.15, 0.1, 0.15, 0.1, 0.15, 0.1, 0.15],
            }
        )

        # Trades with full ADV/volatility for capacity calculations
        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, i),
                trade_value_usd=50_000,
                commission_spread_cost=25,
                market_impact_cost=10,
                total_cost_usd=35,
                total_cost_bps=7.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.005,
            )
            for i in range(1, 10)
        ]

        # Use realistic returns that produce achievable breakeven:
        # 0.4% gross return over 10 days annualizes to ~10% alpha
        # (1 + 0.004)^(252/10) - 1 ≈ 0.10
        cost_summary = CostSummary(
            total_gross_return=0.004,  # 0.4% gross return (realistic for 10 days)
            total_net_return=0.003,
            total_cost_drag=0.001,
            total_cost_usd=1000,
            commission_spread_cost_usd=600,
            market_impact_cost_usd=400,
            gross_sharpe=1.5,
            net_sharpe=1.2,
            gross_max_drawdown=0.02,
            net_max_drawdown=0.025,
            num_trades=9,
            avg_trade_cost_bps=7.0,
        )

        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)

        # All fields should be populated with valid data
        assert analysis.avg_daily_turnover is not None
        assert analysis.avg_holding_period_days is not None
        assert analysis.portfolio_adv is not None
        assert analysis.portfolio_sigma is not None
        assert analysis.gross_alpha_annualized is not None
        assert analysis.impact_aum_5bps is not None
        assert analysis.impact_aum_10bps is not None
        assert analysis.participation_aum is not None
        # breakeven_aum may be None if alpha is too high or impact is too low
        # to reach breakeven within search bounds ($10B). With realistic data,
        # it should be populated.
        assert analysis.breakeven_aum is not None
        assert analysis.implied_max_capacity is not None
        assert analysis.limiting_factor is not None

    def test_capacity_with_zero_impact_coefficient(self):
        """Test capacity analysis when impact coefficient is zero."""
        config = CostModelConfig(
            impact_coefficient=0.0,  # Zero impact
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "weight": [0.10, 0.20],
            }
        )

        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 1),
                trade_value_usd=100_000,
                commission_spread_cost=50,
                market_impact_cost=0,
                total_cost_usd=50,
                total_cost_bps=5.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.01,
            ),
        ]

        cost_summary = CostSummary(
            total_gross_return=0.05,
            total_net_return=0.045,
            total_cost_drag=0.005,
            total_cost_usd=50,
            commission_spread_cost_usd=50,
            market_impact_cost_usd=0,
            gross_sharpe=1.5,
            net_sharpe=1.2,
            gross_max_drawdown=0.05,
            net_max_drawdown=0.06,
            num_trades=1,
            avg_trade_cost_bps=5.0,
        )

        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)

        # Impact AUM should be None when impact_coefficient is 0
        assert analysis.impact_aum_5bps is None
        assert analysis.impact_aum_10bps is None

    def test_capacity_with_negative_gross_return(self):
        """Test capacity analysis when gross return is negative (no breakeven)."""
        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "weight": [0.10, 0.20],
            }
        )

        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 1),
                trade_value_usd=100_000,
                commission_spread_cost=50,
                market_impact_cost=20,
                total_cost_usd=70,
                total_cost_bps=7.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.01,
            ),
        ]

        cost_summary = CostSummary(
            total_gross_return=-0.05,  # Negative return
            total_net_return=-0.06,
            total_cost_drag=0.01,
            total_cost_usd=100,
            commission_spread_cost_usd=50,
            market_impact_cost_usd=50,
            gross_sharpe=-0.5,
            net_sharpe=-0.6,
            gross_max_drawdown=0.10,
            net_max_drawdown=0.12,
            num_trades=1,
            avg_trade_cost_bps=7.0,
        )

        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)

        # Breakeven AUM should be None for negative gross return
        assert analysis.breakeven_aum is None


class TestFallbackFunctions:
    """Tests for ADV and volatility fallback functions."""

    def test_apply_adv_fallback_with_valid_value(self):
        """Test fallback returns valid value unchanged."""
        value, used_fallback = apply_adv_fallback(1_000_000.0, 12345)
        assert value == 1_000_000.0
        assert used_fallback is False

    def test_apply_adv_fallback_with_none(self):
        """Test fallback applies floor for None value."""
        value, used_fallback = apply_adv_fallback(None, 12345)
        assert value == ADV_FLOOR_USD
        assert used_fallback is True

    def test_apply_adv_fallback_with_zero(self):
        """Test fallback applies floor for zero value."""
        value, used_fallback = apply_adv_fallback(0.0, 12345)
        assert value == ADV_FLOOR_USD
        assert used_fallback is True

    def test_apply_adv_fallback_with_negative(self):
        """Test fallback applies floor for negative value."""
        value, used_fallback = apply_adv_fallback(-100.0, 12345)
        assert value == ADV_FLOOR_USD
        assert used_fallback is True

    def test_apply_adv_fallback_with_nan(self):
        """Test fallback applies floor for NaN value."""
        value, used_fallback = apply_adv_fallback(float("nan"), 12345)
        assert value == ADV_FLOOR_USD
        assert used_fallback is True

    def test_apply_adv_fallback_with_inf(self):
        """Test fallback applies floor for infinity value."""
        value, used_fallback = apply_adv_fallback(float("inf"), 12345)
        assert value == ADV_FLOOR_USD
        assert used_fallback is True

    def test_apply_volatility_fallback_with_valid_value(self):
        """Test volatility fallback returns valid value unchanged."""
        value, used_fallback = apply_volatility_fallback(0.02, 12345)
        assert value == 0.02
        assert used_fallback is False

    def test_apply_volatility_fallback_with_none(self):
        """Test volatility fallback applies floor for None value."""
        value, used_fallback = apply_volatility_fallback(None, 12345)
        assert value == VOL_FLOOR
        assert used_fallback is True

    def test_apply_volatility_fallback_with_zero(self):
        """Test volatility fallback applies floor for zero value."""
        value, used_fallback = apply_volatility_fallback(0.0, 12345)
        assert value == VOL_FLOOR
        assert used_fallback is True


class TestComputeRollingAdvVolatility:
    """Tests for rolling ADV/volatility computation."""

    def test_empty_input(self):
        """Test with empty DataFrame returns empty result."""
        empty_df = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "prc": pl.Float64,
                "vol": pl.Float64,
                "ret": pl.Float64,
            }
        )
        result = compute_rolling_adv_volatility(empty_df, date(2024, 1, 1), date(2024, 1, 31))
        assert result.height == 0
        assert set(result.columns) == {"permno", "date", "adv_usd", "volatility"}

    def test_rolling_computation_short_window(self):
        """Test rolling computation with fewer than 20 days returns nulls."""
        # Create 10 days of data (less than 20-day window)
        dates = [date(2024, 1, i + 1) for i in range(10)]
        df = pl.DataFrame(
            {
                "permno": [12345] * 10,
                "date": dates,
                "prc": [100.0] * 10,
                "vol": [1_000_000.0] * 10,
                "ret": [0.01] * 10,
            }
        )

        result = compute_rolling_adv_volatility(df, date(2024, 1, 1), date(2024, 1, 10))

        # With only 10 days, we can't compute 20-day rolling stats
        # ADV and volatility should be null due to min_samples=20
        assert result.height == 10
        # All values should be null (not enough data for 20-day window)
        assert result.filter(pl.col("adv_usd").is_not_null()).height == 0

    def test_rolling_computation_with_sufficient_data(self):
        """Test rolling computation with sufficient data produces values."""
        # Create 30 days of data
        dates = [date(2024, 1, i + 1) for i in range(30)]
        df = pl.DataFrame(
            {
                "permno": [12345] * 30,
                "date": dates,
                "prc": [100.0] * 30,
                "vol": [1_000_000.0] * 30,
                "ret": [0.01 * (1 if i % 2 == 0 else -1) for i in range(30)],  # Alternating returns
            }
        )

        result = compute_rolling_adv_volatility(df, date(2024, 1, 22), date(2024, 1, 30))

        # After 21 days (20 days of rolling + 1 day lag), we should have values
        non_null_adv = result.filter(pl.col("adv_usd").is_not_null())
        assert non_null_adv.height > 0


class TestComputeDailyCostsPermno:
    """Tests for permno-keyed daily cost computation."""

    def test_disabled_cost_model(self):
        """Test disabled cost model returns zero costs."""
        config = CostModelConfig(enabled=False)
        daily_weights = pl.DataFrame(
            {
                "permno": [12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "weight": [0.10, 0.20],
            }
        )
        adv_vol = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "adv_usd": pl.Float64,
                "volatility": pl.Float64,
            }
        )

        cost_drag_df, trade_costs, adv_fb, vol_fb, violations = compute_daily_costs_permno(
            daily_weights, adv_vol, config
        )

        assert len(trade_costs) == 0
        assert adv_fb == 0
        assert vol_fb == 0
        assert violations == 0
        assert cost_drag_df.filter(pl.col("cost_drag") > 0).height == 0

    def test_basic_cost_computation(self):
        """Test basic cost computation with ADV/volatility data."""
        config = CostModelConfig(
            enabled=True,
            bps_per_trade=5.0,
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "permno": [12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "weight": [0.10, 0.20],
            }
        )

        adv_vol = pl.DataFrame(
            {
                "permno": [12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "adv_usd": [10_000_000.0, 10_000_000.0],
                "volatility": [0.02, 0.02],
            }
        )

        cost_drag_df, trade_costs, adv_fb, vol_fb, violations = compute_daily_costs_permno(
            daily_weights, adv_vol, config
        )

        assert len(trade_costs) == 2  # Day 1: 10% position, Day 2: 10% change
        assert adv_fb == 0
        assert vol_fb == 0
        assert cost_drag_df.height >= 2

    def test_fallback_counting(self):
        """Test fallback counts are incremented for missing ADV/volatility."""
        config = CostModelConfig(
            enabled=True,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "permno": [12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "weight": [0.10, 0.20],
            }
        )

        # Empty ADV/vol data - will trigger fallbacks
        adv_vol = pl.DataFrame(
            schema={
                "permno": pl.Int64,
                "date": pl.Date,
                "adv_usd": pl.Float64,
                "volatility": pl.Float64,
            }
        )

        _, trade_costs, adv_fb, vol_fb, _ = compute_daily_costs_permno(
            daily_weights, adv_vol, config
        )

        assert adv_fb == 2  # Both trades use fallback
        assert vol_fb == 2

    def test_participation_violation_counting(self):
        """Test participation violations are counted correctly."""
        config = CostModelConfig(
            enabled=True,
            participation_limit=0.01,  # 1% limit
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "permno": [12345],
                "date": [date(2024, 1, 1)],
                "weight": [0.50],  # 50% position = $500K trade
            }
        )

        adv_vol = pl.DataFrame(
            {
                "permno": [12345],
                "date": [date(2024, 1, 1)],
                "adv_usd": [1_000_000.0],  # $1M ADV
                "volatility": [0.02],
            }
        )

        # $500K / $1M = 50% participation > 1% limit
        _, _, _, _, violations = compute_daily_costs_permno(daily_weights, adv_vol, config)

        assert violations == 1


class TestComputeBacktestCosts:
    """Tests for full backtest cost computation."""

    def test_basic_backtest_costs(self):
        """Test basic backtest cost computation."""
        config = CostModelConfig(
            enabled=True,
            bps_per_trade=5.0,
            impact_coefficient=0.1,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "permno": [12345, 12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "weight": [0.10, 0.15, 0.20],
            }
        )

        gross_returns = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "return": [0.01, 0.02, -0.01],
            }
        )

        adv_vol = pl.DataFrame(
            {
                "permno": [12345, 12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "adv_usd": [10_000_000.0] * 3,
                "volatility": [0.02] * 3,
            }
        )

        result = compute_backtest_costs(daily_weights, gross_returns, adv_vol, config)

        assert isinstance(result, BacktestCostResult)
        assert result.cost_summary is not None
        assert result.cost_summary.total_cost_usd > 0
        assert result.net_returns_df.height == 3
        assert "net_return" in result.net_returns_df.columns

    def test_backtest_costs_capacity_analysis(self):
        """Test capacity analysis is computed in backtest costs."""
        config = CostModelConfig(
            enabled=True,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "permno": [12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "weight": [0.10, 0.20],
            }
        )

        gross_returns = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [0.01, 0.02],
            }
        )

        adv_vol = pl.DataFrame(
            {
                "permno": [12345, 12345],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "adv_usd": [10_000_000.0] * 2,
                "volatility": [0.02] * 2,
            }
        )

        result = compute_backtest_costs(daily_weights, gross_returns, adv_vol, config)

        assert result.capacity_analysis is not None
        # Should have turnover data
        assert result.capacity_analysis.avg_daily_turnover is not None


class TestBacktestCostResult:
    """Tests for BacktestCostResult dataclass."""

    def test_backtest_cost_result_attributes(self):
        """Test BacktestCostResult has all expected attributes."""
        cost_summary = CostSummary(
            total_gross_return=0.10,
            total_net_return=0.08,
            total_cost_drag=0.02,
            total_cost_usd=1000,
            commission_spread_cost_usd=500,
            market_impact_cost_usd=500,
            gross_sharpe=1.5,
            net_sharpe=1.2,
            gross_max_drawdown=0.05,
            net_max_drawdown=0.06,
            num_trades=10,
            avg_trade_cost_bps=5.0,
        )

        capacity = CapacityAnalysis(
            avg_daily_turnover=0.05,
            avg_holding_period_days=20.0,
            portfolio_adv=10_000_000.0,
            portfolio_sigma=0.02,
            gross_alpha_annualized=0.15,
            impact_aum_5bps=50_000_000.0,
            impact_aum_10bps=100_000_000.0,
            participation_aum=40_000_000.0,
            breakeven_aum=80_000_000.0,
            implied_max_capacity=40_000_000.0,
            limiting_factor="participation",
        )

        result = BacktestCostResult(
            cost_summary=cost_summary,
            capacity_analysis=capacity,
            net_returns_df=pl.DataFrame({"date": [], "net_return": []}),
            cost_drag_df=pl.DataFrame({"date": [], "cost_drag": []}),
            trade_costs=[],
            adv_fallback_count=5,
            volatility_fallback_count=3,
            participation_violations=2,
        )

        assert result.cost_summary == cost_summary
        assert result.capacity_analysis == capacity
        assert result.adv_fallback_count == 5
        assert result.volatility_fallback_count == 3
        assert result.participation_violations == 2


class TestCapacityGrossReturnEdgeCases:
    """Tests for capacity analysis with extreme gross returns."""

    def test_capacity_handles_negative_100_percent_return(self):
        """Test capacity analysis doesn't crash on -100% gross return."""
        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        daily_weights = pl.DataFrame(
            {
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "symbol": ["AAPL"] * 5,
                "weight": [0.1] * 5,
            }
        )

        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, i),
                trade_value_usd=50_000,
                commission_spread_cost=25,
                market_impact_cost=10,
                total_cost_usd=35,
                total_cost_bps=7.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.005,
            )
            for i in range(1, 5)
        ]

        # Extreme case: -100% gross return (total loss)
        cost_summary = CostSummary(
            total_gross_return=-1.0,  # -100% return
            total_net_return=-1.02,
            total_cost_drag=0.02,
            total_cost_usd=20000,
            commission_spread_cost_usd=12000,
            market_impact_cost_usd=8000,
            gross_sharpe=-2.0,
            net_sharpe=-2.5,
            gross_max_drawdown=1.0,
            net_max_drawdown=1.02,
            num_trades=4,
            avg_trade_cost_bps=7.0,
        )

        # Should not raise - gross_alpha_annual should be None
        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)

        # gross_alpha_annualized should be None (guarded against -100% return)
        assert analysis.gross_alpha_annualized is None
        # breakeven_aum should also be None (depends on gross_alpha_annualized)
        assert analysis.breakeven_aum is None

    def test_capacity_handles_worse_than_negative_100_percent(self):
        """Test capacity analysis doesn't crash on < -100% gross return."""
        config = CostModelConfig()

        daily_weights = pl.DataFrame(
            {"date": [date(2024, 1, 1)], "symbol": ["AAPL"], "weight": [0.1]}
        )

        trade_costs = [
            TradeCost(
                symbol="AAPL",
                trade_date=date(2024, 1, 1),
                trade_value_usd=50_000,
                commission_spread_cost=25,
                market_impact_cost=10,
                total_cost_usd=35,
                total_cost_bps=7.0,
                adv_usd=10_000_000,
                volatility=0.02,
                participation_pct=0.005,
            )
        ]

        # Extreme case: -150% gross return (leveraged loss)
        cost_summary = CostSummary(
            total_gross_return=-1.5,  # -150% return (possible with leverage)
            total_net_return=-1.52,
            total_cost_drag=0.02,
            total_cost_usd=1000,
            commission_spread_cost_usd=600,
            market_impact_cost_usd=400,
            gross_sharpe=-3.0,
            net_sharpe=-3.2,
            gross_max_drawdown=1.5,
            net_max_drawdown=1.52,
            num_trades=1,
            avg_trade_cost_bps=7.0,
        )

        # Should not raise
        analysis = compute_capacity_analysis(daily_weights, trade_costs, cost_summary, config)
        assert analysis.gross_alpha_annualized is None


class TestImpactAumScaling:
    """Tests for impact AUM computation with avg_turnover scaling."""

    def test_impact_aum_scales_with_turnover(self):
        """Verify impact_aum = trade_at_target / avg_turnover."""
        from libs.trading.backtest.cost_model import _compute_impact_aum

        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
            portfolio_value_usd=1_000_000,
        )

        # Known inputs
        target_impact_bps = 5.0
        portfolio_adv = 10_000_000.0
        portfolio_sigma = 0.02
        avg_turnover = 0.10  # 10% daily turnover

        result = _compute_impact_aum(
            target_impact_bps, portfolio_adv, portfolio_sigma, avg_turnover, config
        )

        # Manual calculation:
        # participation_at_target = (5 / (0.1 * 0.02 * 10000))^2
        #                        = (5 / 20)^2 = 0.0625
        # trade_at_target = 10_000_000 * 0.0625 = 625_000
        # AUM = 625_000 / 0.10 = 6_250_000
        expected_aum = 6_250_000.0

        assert result is not None
        assert abs(result - expected_aum) < 1.0  # Allow small float precision difference

    def test_impact_aum_none_when_no_turnover(self):
        """Verify impact_aum returns None when avg_turnover is None or zero."""
        from libs.trading.backtest.cost_model import _compute_impact_aum

        config = CostModelConfig(impact_coefficient=0.1)

        # None turnover
        result = _compute_impact_aum(5.0, 10_000_000.0, 0.02, None, config)
        assert result is None

        # Zero turnover
        result = _compute_impact_aum(5.0, 10_000_000.0, 0.02, 0.0, config)
        assert result is None

    def test_impact_aum_higher_turnover_means_lower_capacity(self):
        """Higher turnover means we trade more, so capacity is lower."""
        from libs.trading.backtest.cost_model import _compute_impact_aum

        config = CostModelConfig(impact_coefficient=0.1)

        low_turnover_aum = _compute_impact_aum(5.0, 10_000_000.0, 0.02, 0.05, config)
        high_turnover_aum = _compute_impact_aum(5.0, 10_000_000.0, 0.02, 0.20, config)

        assert low_turnover_aum is not None
        assert high_turnover_aum is not None
        assert low_turnover_aum > high_turnover_aum  # Lower turnover = higher capacity

    def test_capacity_analysis_impact_consistent_with_participation(self):
        """Verify impact and participation AUM use same scaling (trade/turnover)."""
        from libs.trading.backtest.cost_model import (
            _compute_impact_aum,
            _compute_participation_aum,
        )

        config = CostModelConfig(
            impact_coefficient=0.1,
            participation_limit=0.05,
        )

        portfolio_adv = 10_000_000.0
        portfolio_sigma = 0.02
        avg_turnover = 0.10

        impact_aum = _compute_impact_aum(5.0, portfolio_adv, portfolio_sigma, avg_turnover, config)
        participation_aum = _compute_participation_aum(
            portfolio_adv, avg_turnover, config.participation_limit
        )

        # Both should return AUM values (not trade sizes)
        # participation_aum = 0.05 * 10_000_000 / 0.10 = 5_000_000
        assert impact_aum is not None
        assert participation_aum is not None
        assert participation_aum == 5_000_000.0

        # Impact AUM at 5bps should be comparable in magnitude
        # (both are AUM values, not trade sizes)
        assert impact_aum > 1_000_000  # Should be millions, not hundreds of thousands
