"""
Risk management exceptions.

This module defines custom exceptions for risk violations and circuit breaker events.

Example:
    >>> from libs.risk_management.exceptions import RiskViolation, CircuitBreakerTripped
    >>>
    >>> # Raise risk violation
    >>> if position_too_large:
    ...     raise RiskViolation("Position limit exceeded: 1100 > 1000")
    >>>
    >>> # Raise circuit breaker error
    >>> if breaker.is_tripped():
    ...     raise CircuitBreakerTripped(f"Circuit breaker TRIPPED: {breaker.get_trip_reason()}")

See Also:
    - docs/CONCEPTS/risk-management.md for risk concepts
"""


class RiskViolation(Exception):
    """
    Raised when a risk limit is violated.

    Used for pre-trade checks that fail (position limits, loss limits, blacklist, etc.).

    Example:
        >>> if new_position > max_position:
        ...     raise RiskViolation(f"Position limit exceeded: {new_position} > {max_position}")
    """

    pass


class CircuitBreakerTripped(Exception):
    """
    Raised when attempting to trade while circuit breaker is TRIPPED.

    Example:
        >>> if breaker.is_tripped():
        ...     raise CircuitBreakerTripped(f"Circuit breaker TRIPPED: {breaker.get_trip_reason()}")
    """

    pass


class CircuitBreakerError(Exception):
    """
    Raised when circuit breaker operation fails.

    Example:
        >>> try:
        ...     breaker.reset()  # Fails if not TRIPPED
        ... except CircuitBreakerError as e:
        ...     logger.error(f"Failed to reset breaker: {e}")
    """

    pass
