"""
Exception hierarchy for the trading platform.

This module defines all custom exceptions used throughout the platform,
organized in a hierarchy for precise error handling.

See ADR-0002 for rationale.
"""


class TradingPlatformError(Exception):
    """
    Base exception for all trading platform errors.

    All custom exceptions in the platform inherit from this class,
    allowing for catch-all error handling when needed.

    Example:
        >>> try:
        ...     # trading platform code
        ...     pass
        ... except TradingPlatformError as e:
        ...     logger.error(f"Platform error: {e}")
    """

    pass


class DataQualityError(TradingPlatformError):
    """
    Raised when data fails quality checks.

    This includes outliers, schema validation failures, missing data,
    or any other data integrity issues.

    Example:
        >>> if price_change > 0.50:
        ...     raise DataQualityError(f"Price change {price_change} exceeds 50%")
    """

    pass


class StalenessError(DataQualityError):
    """
    Raised when data is too old to be used for trading decisions.

    The freshness threshold is configured via DATA_FRESHNESS_MINUTES
    environment variable (default: 30 minutes).

    Example:
        >>> age_minutes = (now - data_timestamp).total_seconds() / 60
        >>> if age_minutes > 30:
        ...     raise StalenessError(f"Data is {age_minutes:.1f}m old, exceeds 30m")
    """

    pass


class OutlierError(DataQualityError):
    """
    Raised when price movements are abnormal without corporate actions.

    Outliers are defined as daily price changes exceeding the configured
    threshold (default: 30%) when no split or dividend occurred.

    Example:
        >>> if abs(daily_return) > 0.30 and not has_corporate_action:
        ...     raise OutlierError(f"Abnormal return {daily_return:.2%} for {symbol}")
    """

    pass


class ConfigurationError(TradingPlatformError):
    """
    Raised when required configuration or secrets are missing.

    This is used for services that require external configuration
    (API keys, credentials, etc.) that may not be available in all
    deployment environments.

    Example:
        >>> if not twilio_account_sid:
        ...     raise ConfigurationError("TWILIO_ACCOUNT_SID not configured")
    """

    pass
