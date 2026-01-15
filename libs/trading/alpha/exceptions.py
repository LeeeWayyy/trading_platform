"""
Custom exceptions for Alpha Research Framework.

Provides fail-fast error handling for PIT violations and data issues.
"""


class AlphaResearchError(Exception):
    """Base exception for alpha research framework."""

    pass


class PITViolationError(AlphaResearchError):
    """Raised when point-in-time data contract is violated.

    This includes:
    - Attempting to access data beyond snapshot date range
    - Using live providers during backtest (should use snapshot-locked data)
    - Any operation that could introduce look-ahead bias
    """

    pass


class MissingForwardReturnError(AlphaResearchError):
    """Raised when forward return horizon exceeds snapshot (FAIL-FAST).

    This is a critical error that prevents IC/decay calculation from
    silently returning NaN. Users must either:
    - Reduce backtest end_date
    - Reduce horizon parameter
    - Use a newer snapshot with more data
    """

    pass


class InsufficientDataError(AlphaResearchError):
    """Raised when there's not enough data for reliable computation.

    This includes:
    - Too few observations for IC calculation (<30 stocks)
    - Too short time series for decay analysis
    - Missing sector mappings for grouped IC
    """

    pass


class AlphaValidationError(AlphaResearchError):
    """Raised when alpha signal fails validation checks.

    This includes:
    - NaN/inf values in signal
    - Signal values outside expected range
    - Missing required columns in output DataFrame
    """

    pass


class JobCancelled(AlphaResearchError):
    """Raised when a backtest job is cancelled cooperatively."""

    pass
