"""Tests for transaction cost model."""

from datetime import date

import polars as pl
import pytest

from libs.trading.backtest.cost_model import (
    ADVSource,
    CapacityAnalysis,
    CostModelConfig,
    CostSummary,
    TradeCost,
    compute_capacity_analysis,
    compute_compounded_return,
    compute_cost_summary,
    compute_daily_costs,
    compute_market_impact,
    compute_max_drawdown,
    compute_net_returns,
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
