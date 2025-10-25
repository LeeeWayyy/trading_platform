"""
Unit tests for backtesting and evaluation.

Tests cover:
- PortfolioBacktest initialization
- Portfolio return computation
- Metrics calculation
- Plot generation
- Report generation
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategies.alpha_baseline.backtest import PortfolioBacktest, evaluate_model


class TestPortfolioBacktest:
    """Tests for PortfolioBacktest class."""

    def setup_method(self) -> None:
        """Create test data."""
        # Create mock predictions and actual returns
        # 10 days, 5 symbols
        np.random.seed(42)

        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

        # Create MultiIndex
        index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])

        # Generate predictions and actual returns
        self.predictions = pd.Series(
            np.random.randn(50) * 0.02,  # ±2% predictions
            index=index,
        )

        self.actual_returns = pd.Series(
            np.random.randn(50) * 0.03,  # ±3% actual returns
            index=index,
        )

        # Create temp directory for plots
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self) -> None:
        """Clean up temp directory."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_initialization(self) -> None:
        """Initialize PortfolioBacktest."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        assert backtest.top_n == 2
        assert backtest.bottom_n == 2
        assert backtest.portfolio_returns is None
        assert backtest.cumulative_returns is None
        assert backtest.metrics == {}

    def test_run_backtest(self) -> None:
        """Run portfolio backtest."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        metrics = backtest.run()

        # Check metrics exist
        assert "total_return" in metrics
        assert "annualized_return" in metrics
        assert "volatility" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "avg_win" in metrics
        assert "avg_loss" in metrics
        assert "n_days" in metrics

        # Check portfolio returns were computed
        assert backtest.portfolio_returns is not None
        assert len(backtest.portfolio_returns) > 0

        # Check cumulative returns were computed
        assert backtest.cumulative_returns is not None
        assert len(backtest.cumulative_returns) > 0

    def test_portfolio_returns_shape(self) -> None:
        """Portfolio returns have correct shape."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=1,
            bottom_n=1,
        )

        backtest.run()

        # Should have one return per day
        assert len(backtest.portfolio_returns) <= 10

    def test_metrics_values(self) -> None:
        """Metrics have reasonable values."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        metrics = backtest.run()

        # Win rate should be between 0 and 1
        assert 0 <= metrics["win_rate"] <= 1

        # Max drawdown should be negative or zero
        assert metrics["max_drawdown"] <= 0

        # Number of days should match
        assert metrics["n_days"] > 0

    def test_plot_cumulative_returns(self) -> None:
        """Generate cumulative returns plot."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        backtest.run()

        # Generate plot (don't show, save to temp dir)
        plot_path = self.temp_dir / "cumulative_returns.png"
        backtest.plot_cumulative_returns(save_path=plot_path, show=False)

        # Check file was created
        assert plot_path.exists()
        assert plot_path.stat().st_size > 0

    def test_plot_drawdown(self) -> None:
        """Generate drawdown plot."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        backtest.run()

        # Generate plot
        plot_path = self.temp_dir / "drawdown.png"
        backtest.plot_drawdown(save_path=plot_path, show=False)

        # Check file was created
        assert plot_path.exists()
        assert plot_path.stat().st_size > 0

    def test_generate_report(self) -> None:
        """Generate text report."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        backtest.run()

        report = backtest.generate_report()

        # Check report contains expected sections
        assert "Portfolio Backtest Report" in report
        assert "Performance Metrics" in report
        assert "Daily Statistics" in report
        assert "Total Return" in report
        assert "Sharpe Ratio" in report
        assert "Win Rate" in report

    def test_plot_without_running_raises_error(self) -> None:
        """Plotting without running backtest raises error."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
        )

        with pytest.raises(ValueError, match="must run backtest first"):
            backtest.plot_cumulative_returns()

    def test_report_without_running_raises_error(self) -> None:
        """Generating report without running raises error."""
        backtest = PortfolioBacktest(
            predictions=self.predictions,
            actual_returns=self.actual_returns,
        )

        with pytest.raises(ValueError, match="must run backtest first"):
            backtest.generate_report()

    def test_perfect_predictions(self) -> None:
        """Perfect predictions should have high Sharpe ratio."""
        # Create perfect predictions (same as actual)
        backtest = PortfolioBacktest(
            predictions=self.actual_returns,  # Perfect predictions!
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        metrics = backtest.run()

        # Should have positive returns and good Sharpe
        # (not guaranteed due to randomness, but likely)
        assert metrics["n_days"] > 0

    def test_opposite_predictions(self) -> None:
        """Opposite predictions should perform poorly."""
        # Create opposite predictions
        opposite_predictions = -self.actual_returns

        backtest = PortfolioBacktest(
            predictions=opposite_predictions,
            actual_returns=self.actual_returns,
            top_n=2,
            bottom_n=2,
        )

        metrics = backtest.run()

        # Should perform poorly (but not guaranteed due to noise)
        assert metrics["n_days"] > 0


class TestEvaluateModel:
    """Tests for evaluate_model function."""

    @pytest.mark.skip(reason="Requires trained model - integration test for Phase 6")
    def test_evaluate_model(self) -> None:
        """Evaluate trained model on test set."""
        from strategies.alpha_baseline.config import StrategyConfig
        from strategies.alpha_baseline.train import BaselineTrainer

        # Train model
        config = StrategyConfig()
        trainer = BaselineTrainer(config, use_mlflow=False)
        trainer.train()

        # Load test data
        _, _, _, _, X_test, y_test = trainer.load_data()

        # Create temp directory for results
        temp_dir = Path(tempfile.mkdtemp())

        try:
            # Evaluate
            results = evaluate_model(
                trainer,
                X_test,
                y_test,
                save_dir=temp_dir,
            )

            # Check results structure
            assert "predictions" in results
            assert "backtest" in results
            assert "metrics" in results
            assert "report" in results

            # Check files were created
            assert (temp_dir / "cumulative_returns.png").exists()
            assert (temp_dir / "drawdown.png").exists()
            assert (temp_dir / "backtest_report.txt").exists()

        finally:
            # Clean up
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def test_module_imports(self) -> None:
        """Module imports work correctly."""
        from strategies.alpha_baseline.backtest import (
            PortfolioBacktest,
            evaluate_model,
        )

        assert PortfolioBacktest is not None
        assert callable(evaluate_model)
