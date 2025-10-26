"""
Ensemble framework for combining multiple trading strategies.

This module provides a flexible framework for combining signals from multiple
trading strategies (e.g., mean reversion and momentum) to generate more robust
trading decisions.

Ensemble methods help reduce false signals and improve overall strategy performance
by leveraging the strengths of different approaches and reducing individual strategy
weaknesses.

Key Benefits:
- Diversification: Reduce dependency on single strategy performance
- Risk Reduction: Conflicting signals can cancel out, avoiding bad trades
- Improved Sharpe Ratio: More consistent returns through signal combination
- Adaptive: Can weight strategies based on recent performance

Components:
- combiner: Core ensemble logic for combining multiple strategy signals
- config: Ensemble configuration (weights, combination methods)
- weighting: Dynamic strategy weighting based on performance

Combination Methods:
- weighted_average: Combine signals using fixed or adaptive weights
- majority_vote: Take action when majority of strategies agree
- unanimous: Only trade when all strategies agree (conservative)
- confidence_weighted: Weight by individual strategy confidence scores
"""

__version__ = "0.1.0"
