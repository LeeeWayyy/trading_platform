"""
Unit tests for risk management exceptions.
"""

import pytest

from libs.trading.risk_management.exceptions import (
    CircuitBreakerError,
    CircuitBreakerTripped,
    RiskViolation,
)


@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (RiskViolation, "Position limit exceeded: 1100 > 1000"),
        (CircuitBreakerTripped, "Circuit breaker TRIPPED: DAILY_LOSS_EXCEEDED"),
        (CircuitBreakerError, "Failed to reset breaker"),
    ],
)
def test_exceptions_raise_and_message(exc_type, message):
    """Ensure exceptions can be raised and preserve messages."""
    with pytest.raises(exc_type) as exc_info:
        raise exc_type(message)

    assert str(exc_info.value) == message


@pytest.mark.parametrize(
    "exc_type",
    [RiskViolation, CircuitBreakerTripped, CircuitBreakerError],
)
def test_exceptions_are_exception_subclasses(exc_type):
    """Ensure custom exceptions are proper Exception subclasses."""
    assert issubclass(exc_type, Exception)
