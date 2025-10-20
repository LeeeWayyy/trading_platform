"""
Backtesting and evaluation for baseline strategy.

This module implements portfolio simulation and performance evaluation:
1. Generate predictions on test set
2. Simulate portfolio with top-N long/short strategy
3. Compute performance metrics (returns, Sharpe, drawdown, etc.)
4. Generate evaluation reports and plots

See /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md for details.
"""

from pathlib import Path
from typing import Any, cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from strategies.alpha_baseline.config import StrategyConfig
from strategies.alpha_baseline.train import BaselineTrainer


class PortfolioBacktest:
    """
    Backtest portfolio performance using model predictions.

    This class simulates a simple long-short portfolio strategy:
    - Long top-N stocks with highest predicted returns
    - Short bottom-N stocks with lowest predicted returns
    - Equal weight within each group
    - Daily rebalancing

    Attributes:
        predictions: DataFrame with predicted returns
        actual_returns: DataFrame with actual returns
        top_n: Number of stocks to long
        bottom_n: Number of stocks to short
        portfolio_returns: Daily portfolio returns
        cumulative_returns: Cumulative portfolio returns
        metrics: Performance metrics

    Example:
        >>> backtest = PortfolioBacktest(predictions, actual_returns, top_n=5, bottom_n=5)
        >>> backtest.run()
        >>> print(backtest.metrics)
        {'sharpe': 1.23, 'max_drawdown': -0.15, ...}

    See Also:
        - /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md
        - /docs/CONCEPTS/portfolio-backtesting.md (to be created)
    """

    def __init__(
        self,
        predictions: pd.Series,
        actual_returns: pd.Series,
        top_n: int = 3,
        bottom_n: int = 3,
    ) -> None:
        """
        Initialize portfolio backtester.

        Args:
            predictions: Predicted returns with (date, symbol) MultiIndex
            actual_returns: Actual returns with (date, symbol) MultiIndex
            top_n: Number of top stocks to long
            bottom_n: Number of bottom stocks to short

        Notes:
            - predictions and actual_returns must have same index
            - NaN values are automatically excluded
        """
        self.predictions = predictions
        self.actual_returns = actual_returns
        self.top_n = top_n
        self.bottom_n = bottom_n

        self.portfolio_returns: pd.Series | None = None
        self.cumulative_returns: pd.Series | None = None
        self.metrics: dict[str, float] = {}

    def run(self) -> dict[str, float]:
        """
        Run portfolio backtest.

        This method:
        1. Ranks stocks by predicted return each day
        2. Longs top-N, shorts bottom-N
        3. Computes daily portfolio return
        4. Calculates performance metrics

        Returns:
            Dictionary of performance metrics

        Example:
            >>> backtest = PortfolioBacktest(predictions, actual_returns)
            >>> metrics = backtest.run()
            >>> print(f"Sharpe Ratio: {metrics['sharpe']:.2f}")
            Sharpe Ratio: 1.23

        Notes:
            - Uses equal weighting within long/short groups
            - Assumes daily rebalancing (no transaction costs)
            - Returns are NOT annualized (use daily returns)
        """
        # Get daily portfolio returns
        self.portfolio_returns = self._compute_portfolio_returns()

        # Compute cumulative returns
        self.cumulative_returns = (1 + self.portfolio_returns).cumprod() - 1

        # Compute metrics
        self.metrics = self._compute_metrics()

        return self.metrics

    def _compute_portfolio_returns(self) -> pd.Series:
        """
        Compute daily portfolio returns.

        For each day:
        1. Rank stocks by predicted return
        2. Long top-N with equal weight (1/top_N each)
        3. Short bottom-N with equal weight (-1/bottom_N each)
        4. Portfolio return = sum of (weight × actual_return)

        Returns:
            Series of daily portfolio returns

        Example:
            Day 1: Predict AAPL=+2%, MSFT=+1%, GOOGL=-1%
                   Long AAPL (weight=0.5), MSFT (weight=0.5)
                   Short GOOGL (weight=-1.0)

                   Actual returns: AAPL=+3%, MSFT=+2%, GOOGL=-0.5%
                   Portfolio return = 0.5*3% + 0.5*2% + (-1.0)*(-0.5%)
                                    = 1.5% + 1.0% + 0.5% = 3.0%
        """
        daily_returns = []

        # Get unique dates
        dates = self.predictions.index.get_level_values(0).unique()

        for date in dates:
            # Get predictions and actual returns for this date
            try:
                day_pred = self.predictions.loc[date]
                day_actual = self.actual_returns.loc[date]
            except KeyError:
                # Date not in index (skip)
                continue

            # Convert to Series if not already (single symbol case)
            if not isinstance(day_pred, pd.Series):
                continue  # Need at least 2 symbols

            if not isinstance(day_actual, pd.Series):
                continue

            # Drop NaN values
            valid_mask = ~(day_pred.isna() | day_actual.isna())
            day_pred = day_pred[valid_mask]
            day_actual = day_actual[valid_mask]

            if len(day_pred) < (self.top_n + self.bottom_n):
                # Not enough stocks for strategy
                continue

            # Rank by predicted return
            ranks = day_pred.rank(ascending=False)

            # Long top-N
            long_mask = ranks <= self.top_n
            long_weight = 1.0 / self.top_n
            long_return = (day_actual[long_mask] * long_weight).sum()

            # Short bottom-N
            short_mask = ranks > (len(ranks) - self.bottom_n)
            short_weight = -1.0 / self.bottom_n
            short_return = (day_actual[short_mask] * short_weight).sum()

            # Total portfolio return
            portfolio_return = long_return + short_return

            daily_returns.append(portfolio_return)

        return cast(pd.Series, pd.Series(daily_returns, index=dates[: len(daily_returns)]))

    def _compute_metrics(self) -> dict[str, float]:
        """
        Compute portfolio performance metrics.

        Metrics:
        - total_return: Total return over backtest period
        - annualized_return: Annualized return (252 trading days)
        - volatility: Annualized volatility (std of daily returns)
        - sharpe_ratio: Sharpe ratio (return / volatility)
        - max_drawdown: Maximum peak-to-trough decline
        - win_rate: Fraction of days with positive returns
        - avg_win: Average return on winning days
        - avg_loss: Average return on losing days

        Returns:
            Dictionary of metrics

        Notes:
            - Assumes 252 trading days per year
            - Sharpe ratio assumes 0% risk-free rate
            - All returns are in decimal form (0.01 = 1%)
        """
        if self.portfolio_returns is None or len(self.portfolio_returns) == 0:
            return {}

        returns = self.portfolio_returns
        assert (
            self.cumulative_returns is not None
        ), "cumulative_returns must be set after portfolio_returns"

        # Total return
        total_return = self.cumulative_returns.iloc[-1]

        # Annualized return
        n_days = len(returns)
        annualized_return = (1 + total_return) ** (252 / n_days) - 1

        # Volatility (annualized)
        volatility = returns.std() * np.sqrt(252)

        # Sharpe ratio (assuming 0% risk-free rate)
        sharpe_ratio = annualized_return / volatility if volatility > 0 else 0.0

        # Max drawdown
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        # Win rate
        wins = returns > 0
        win_rate = wins.mean()

        # Average win/loss
        avg_win = returns[wins].mean() if wins.sum() > 0 else 0.0
        avg_loss = returns[~wins].mean() if (~wins).sum() > 0 else 0.0

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "n_days": n_days,
        }

    def plot_cumulative_returns(self, save_path: Path | None = None, show: bool = True) -> None:
        """
        Plot cumulative returns over time.

        Args:
            save_path: Path to save plot (None = don't save)
            show: Whether to display plot

        Example:
            >>> backtest = PortfolioBacktest(predictions, actual_returns)
            >>> backtest.run()
            >>> backtest.plot_cumulative_returns(save_path=Path("returns.png"))
        """
        if self.cumulative_returns is None:
            raise ValueError("Must run backtest first (call .run())")

        plt.figure(figsize=(12, 6))
        plt.plot(self.cumulative_returns.index, self.cumulative_returns.values * 100)  # type: ignore[operator]
        plt.title("Cumulative Portfolio Returns", fontsize=14, fontweight="bold")
        plt.xlabel("Date")
        plt.ylabel("Cumulative Return (%)")
        plt.grid(True, alpha=0.3)

        # Add horizontal line at 0
        plt.axhline(y=0, color="r", linestyle="--", alpha=0.3)

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")

        if show:
            plt.show()
        else:
            plt.close()

    def plot_drawdown(self, save_path: Path | None = None, show: bool = True) -> None:
        """
        Plot drawdown over time.

        Drawdown shows peak-to-current decline in portfolio value.

        Args:
            save_path: Path to save plot (None = don't save)
            show: Whether to display plot
        """
        if self.portfolio_returns is None:
            raise ValueError("Must run backtest first (call .run())")

        # Compute drawdown
        cumulative = (1 + self.portfolio_returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max

        plt.figure(figsize=(12, 6))
        plt.fill_between(
            drawdown.index, drawdown.values * 100, 0, alpha=0.3, color="red"  # type: ignore[operator]
        )
        plt.plot(drawdown.index, drawdown.values * 100, color="red")  # type: ignore[operator]
        plt.title("Portfolio Drawdown", fontsize=14, fontweight="bold")
        plt.xlabel("Date")
        plt.ylabel("Drawdown (%)")
        plt.grid(True, alpha=0.3)

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")

        if show:
            plt.show()
        else:
            plt.close()

    def generate_report(self) -> str:
        """
        Generate text report of backtest results.

        Returns:
            Formatted string with metrics

        Example:
            >>> backtest = PortfolioBacktest(predictions, actual_returns)
            >>> backtest.run()
            >>> print(backtest.generate_report())
            ===== Portfolio Backtest Report =====
            Total Return: 15.23%
            Annualized Return: 24.56%
            ...
        """
        if not self.metrics:
            raise ValueError("Must run backtest first (call .run())")

        assert self.cumulative_returns is not None, "cumulative_returns must be set after run()"

        report = "=" * 50 + "\n"
        report += "Portfolio Backtest Report\n"
        report += "=" * 50 + "\n\n"

        report += f"Strategy: Top-{self.top_n} Long / Bottom-{self.bottom_n} Short\n"
        report += f"Backtest Period: {self.cumulative_returns.index[0]} to {self.cumulative_returns.index[-1]}\n"
        report += f"Number of Trading Days: {self.metrics['n_days']}\n\n"

        report += "-" * 50 + "\n"
        report += "Performance Metrics\n"
        report += "-" * 50 + "\n"
        report += f"Total Return:        {self.metrics['total_return']*100:>10.2f}%\n"
        report += f"Annualized Return:   {self.metrics['annualized_return']*100:>10.2f}%\n"
        report += f"Volatility (Annual): {self.metrics['volatility']*100:>10.2f}%\n"
        report += f"Sharpe Ratio:        {self.metrics['sharpe_ratio']:>10.2f}\n"
        report += f"Max Drawdown:        {self.metrics['max_drawdown']*100:>10.2f}%\n\n"

        report += "-" * 50 + "\n"
        report += "Daily Statistics\n"
        report += "-" * 50 + "\n"
        report += f"Win Rate:            {self.metrics['win_rate']*100:>10.2f}%\n"
        report += f"Avg Win:             {self.metrics['avg_win']*100:>10.2f}%\n"
        report += f"Avg Loss:            {self.metrics['avg_loss']*100:>10.2f}%\n\n"

        report += "=" * 50 + "\n"

        return report


def evaluate_model(
    trainer: BaselineTrainer,
    X_test: pd.DataFrame,
    y_test: pd.DataFrame,
    top_n: int = 3,
    bottom_n: int = 3,
    save_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Evaluate trained model on test set.

    This function:
    1. Generates predictions on test set
    2. Runs portfolio backtest
    3. Computes metrics
    4. Generates plots and report
    5. Returns all results

    Args:
        trainer: Trained BaselineTrainer instance
        X_test: Test features
        y_test: Test labels (actual returns)
        top_n: Number of top stocks to long
        bottom_n: Number of bottom stocks to short
        save_dir: Directory to save plots and report (None = don't save)

    Returns:
        Dictionary with:
            - predictions: Model predictions
            - backtest: PortfolioBacktest instance
            - metrics: Performance metrics
            - report: Text report

    Example:
        >>> trainer = BaselineTrainer()
        >>> trainer.train()
        >>> _, _, _, _, X_test, y_test = trainer.load_data()
        >>> results = evaluate_model(trainer, X_test, y_test, save_dir=Path("results"))
        >>> print(results['report'])

    Notes:
        - Saves cumulative returns plot
        - Saves drawdown plot
        - Saves text report
        - All files saved to save_dir if provided
    """
    print("\n" + "=" * 50)
    print("Evaluating Model on Test Set")
    print("=" * 50)

    # Generate predictions
    print("\nGenerating predictions...")
    predictions = trainer.predict(X_test)

    # Convert to Series with same index as y_test
    predictions_series = pd.Series(predictions, index=y_test.index)
    actual_series = y_test.iloc[:, 0]  # Extract first column as Series

    # Run backtest
    print(f"\nRunning portfolio backtest (Top-{top_n} Long / Bottom-{bottom_n} Short)...")
    backtest = PortfolioBacktest(
        predictions=predictions_series,
        actual_returns=actual_series,
        top_n=top_n,
        bottom_n=bottom_n,
    )

    metrics = backtest.run()

    # Generate report
    report = backtest.generate_report()
    print("\n" + report)

    # Save plots and report if save_dir provided
    if save_dir:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        # Save plots
        print(f"\nSaving plots to {save_dir}...")
        backtest.plot_cumulative_returns(
            save_path=save_dir / "cumulative_returns.png",
            show=False,
        )
        backtest.plot_drawdown(
            save_path=save_dir / "drawdown.png",
            show=False,
        )

        # Save report
        report_path = save_dir / "backtest_report.txt"
        with open(report_path, "w") as f:
            f.write(report)
        print(f"Report saved to {report_path}")

    return {
        "predictions": predictions_series,
        "backtest": backtest,
        "metrics": metrics,
        "report": report,
    }


if __name__ == "__main__":
    # Example usage
    print("Training baseline model and evaluating on test set...")

    from strategies.alpha_baseline.config import StrategyConfig
    from strategies.alpha_baseline.train import train_baseline_model

    # Train model
    config = StrategyConfig()
    trainer = train_baseline_model(config)

    # Load test data
    _, _, _, _, X_test, y_test = trainer.load_data()

    # Evaluate
    results = evaluate_model(
        trainer,
        X_test,
        y_test,
        save_dir=Path("artifacts/backtest_results"),
    )

    print("\nEvaluation complete!")
    print(f"Sharpe Ratio: {results['metrics']['sharpe_ratio']:.2f}")
    print(f"Max Drawdown: {results['metrics']['max_drawdown']*100:.2f}%")
