"""
Risk management library for trading platform.

This library provides:
- Risk limit configuration (position limits, loss limits, exposure limits)
- Circuit breaker state machine (automatic trading halts)
- Kill-switch (operator-controlled emergency halt)
- Pre-trade risk validation
- Post-trade monitoring

Example:
    >>> from libs.trading.risk_management import RiskConfig, CircuitBreaker, KillSwitch, RiskChecker
    >>> from libs.core.redis_client import RedisClient
    >>>
    >>> # Initialize
    >>> config = RiskConfig()
    >>> redis = RedisClient(host="localhost", port=6379)
    >>> breaker = CircuitBreaker(redis_client=redis)
    >>> kill_switch = KillSwitch(redis_client=redis)
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

from libs.trading.risk_management.breaker import CircuitBreaker, CircuitBreakerState, TripReason
from libs.trading.risk_management.checker import RiskChecker
from libs.trading.risk_management.config import (
    LossLimits,
    PortfolioLimits,
    PositionLimits,
    RiskConfig,
)
from libs.trading.risk_management.exceptions import (
    CircuitBreakerError,
    CircuitBreakerTripped,
    RiskViolation,
)
from libs.trading.risk_management.kill_switch import KillSwitch, KillSwitchEngaged, KillSwitchState
from libs.trading.risk_management.position_reservation import (
    PositionReservation,
    ReleaseResult,
    ReservationResult,
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
    # Kill-Switch
    "KillSwitch",
    "KillSwitchState",
    "KillSwitchEngaged",
    # Risk Checker
    "RiskChecker",
    # Exceptions
    "RiskViolation",
    "CircuitBreakerTripped",
    "CircuitBreakerError",
    # Position Reservation
    "PositionReservation",
    "ReservationResult",
    "ReleaseResult",
]
