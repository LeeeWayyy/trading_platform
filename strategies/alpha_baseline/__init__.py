"""
Baseline quantitative trading strategy using Qlib and Alpha158 features.

This module implements a baseline strategy that serves as the foundation
for the trading platform. It uses Microsoft's Qlib framework with Alpha158
feature engineering and LightGBM for predictions.

Components:
- data_loader: Custom Qlib provider for T1 adjusted data
- features: Alpha158 feature engineering (shared with Signal Service)
- model: LightGBM model configuration
- train: Training pipeline with MLflow tracking
- backtest: Backtesting and evaluation
- config: Strategy configuration and hyperparameters
"""

__version__ = "0.1.0"
