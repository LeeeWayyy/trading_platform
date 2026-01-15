"""
Multi-alpha capital allocation library.

Provides risk-aware capital allocation across multiple trading strategies
with correlation monitoring and concentration limits.
"""

from libs.trading.allocation.multi_alpha import AllocMethod, MultiAlphaAllocator

__all__ = ["MultiAlphaAllocator", "AllocMethod"]
