"""
Risk management library for trading platform.

This library provides:
- Risk limit configuration (position limits, loss limits, exposure limits)
- Circuit breaker state machine (automatic trading halts)
- Pre-trade risk validation
- Post-trade monitoring

Example:
    >>> from libs.risk_management import RiskConfig, CircuitBreaker, RiskChecker
    >>> from libs.redis_client import RedisClient
    >>>
    >>> # Initialize
    >>> config = RiskConfig()
    >>> redis = RedisClient(host="localhost", port=6379)
    >>> breaker = CircuitBreaker(redis_client=redis)
    >>> checker = RiskChecker(config=config, breaker=breaker)
    >>>
    >>> # Pre-trade check
    >>> is_valid, reason = checker.validate_order(
    ...     symbol="AAPL",
    ...     side="buy",
    ...     qty=100,
    ...     current_position=0
    ... )
    >>> if not is_valid:
    ...     raise RiskViolation(reason)

See Also:
    - docs/CONCEPTS/risk-management.md - Educational guide
    - docs/ADRs/0011-risk-management-system.md - Architecture decisions
"""

from libs.risk_management.breaker import CircuitBreaker, CircuitBreakerState, TripReason
from libs.risk_management.checker import RiskChecker
from libs.risk_management.config import (
    LossLimits,
    PortfolioLimits,
    PositionLimits,
    RiskConfig,
)
from libs.risk_management.exceptions import (
    CircuitBreakerError,
    CircuitBreakerTripped,
    RiskViolation,
)

__all__ = [
    # Configuration
    "RiskConfig",
    "PositionLimits",
    "PortfolioLimits",
    "LossLimits",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerState",
    "TripReason",
    # Risk Checker
    "RiskChecker",
    # Exceptions
    "RiskViolation",
    "CircuitBreakerTripped",
    "CircuitBreakerError",
]
