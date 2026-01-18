"""
Unit tests for libs.core.common.exceptions.

Tests cover:
- Exception hierarchy structure
- TradingPlatformError base exception
- DataQualityError and its subclasses (StalenessError, OutlierError)
- ConfigurationError
- Exception message handling
- Inheritance relationships
- Exception catching behavior
"""

from __future__ import annotations

import pytest

from libs.core.common.exceptions import (
    ConfigurationError,
    DataQualityError,
    OutlierError,
    StalenessError,
    TradingPlatformError,
)


class TestTradingPlatformError:
    """Tests for TradingPlatformError base exception."""

    def test_trading_platform_error_can_be_raised(self):
        """Test TradingPlatformError can be raised."""
        with pytest.raises(TradingPlatformError):
            raise TradingPlatformError("test error")

    def test_trading_platform_error_message_preserved(self):
        """Test TradingPlatformError preserves error message."""
        error = TradingPlatformError("specific message")

        assert str(error) == "specific message"

    def test_trading_platform_error_inherits_from_exception(self):
        """Test TradingPlatformError inherits from Exception."""
        error = TradingPlatformError("test")

        assert isinstance(error, Exception)

    def test_trading_platform_error_with_empty_message(self):
        """Test TradingPlatformError with empty message."""
        error = TradingPlatformError("")

        assert str(error) == ""

    def test_trading_platform_error_without_message(self):
        """Test TradingPlatformError without any message."""
        error = TradingPlatformError()

        assert str(error) == ""

    def test_trading_platform_error_catches_all_platform_errors(self):
        """Test TradingPlatformError catches all derived exceptions."""
        exceptions_to_test = [
            DataQualityError("data error"),
            StalenessError("stale error"),
            OutlierError("outlier error"),
            ConfigurationError("config error"),
        ]

        for exc in exceptions_to_test:
            with pytest.raises(TradingPlatformError):
                raise exc


class TestDataQualityError:
    """Tests for DataQualityError exception."""

    def test_data_quality_error_can_be_raised(self):
        """Test DataQualityError can be raised."""
        with pytest.raises(DataQualityError):
            raise DataQualityError("quality check failed")

    def test_data_quality_error_message_preserved(self):
        """Test DataQualityError preserves error message."""
        error = DataQualityError("Price change 0.55 exceeds 50%")

        assert str(error) == "Price change 0.55 exceeds 50%"

    def test_data_quality_error_inherits_from_trading_platform_error(self):
        """Test DataQualityError inherits from TradingPlatformError."""
        error = DataQualityError("test")

        assert isinstance(error, TradingPlatformError)
        assert isinstance(error, Exception)

    def test_data_quality_error_caught_by_base_class(self):
        """Test DataQualityError can be caught as TradingPlatformError."""
        with pytest.raises(TradingPlatformError, match="data issue"):
            raise DataQualityError("data issue")


class TestStalenessError:
    """Tests for StalenessError exception."""

    def test_staleness_error_can_be_raised(self):
        """Test StalenessError can be raised."""
        with pytest.raises(StalenessError):
            raise StalenessError("Data is 45.5m old, exceeds 30m")

    def test_staleness_error_message_preserved(self):
        """Test StalenessError preserves error message."""
        error = StalenessError("Data is 45.5m old, exceeds 30m")

        assert str(error) == "Data is 45.5m old, exceeds 30m"

    def test_staleness_error_inherits_from_data_quality_error(self):
        """Test StalenessError inherits from DataQualityError."""
        error = StalenessError("test")

        assert isinstance(error, DataQualityError)
        assert isinstance(error, TradingPlatformError)
        assert isinstance(error, Exception)

    def test_staleness_error_caught_by_data_quality_error(self):
        """Test StalenessError can be caught as DataQualityError."""
        with pytest.raises(DataQualityError, match="stale data"):
            raise StalenessError("stale data")

    def test_staleness_error_caught_by_base_class(self):
        """Test StalenessError can be caught as TradingPlatformError."""
        with pytest.raises(TradingPlatformError, match="stale"):
            raise StalenessError("stale")


class TestOutlierError:
    """Tests for OutlierError exception."""

    def test_outlier_error_can_be_raised(self):
        """Test OutlierError can be raised."""
        with pytest.raises(OutlierError):
            raise OutlierError("Abnormal return 0.35 for AAPL")

    def test_outlier_error_message_preserved(self):
        """Test OutlierError preserves error message."""
        error = OutlierError("Abnormal return 35.00% for AAPL")

        assert str(error) == "Abnormal return 35.00% for AAPL"

    def test_outlier_error_inherits_from_data_quality_error(self):
        """Test OutlierError inherits from DataQualityError."""
        error = OutlierError("test")

        assert isinstance(error, DataQualityError)
        assert isinstance(error, TradingPlatformError)
        assert isinstance(error, Exception)

    def test_outlier_error_caught_by_data_quality_error(self):
        """Test OutlierError can be caught as DataQualityError."""
        with pytest.raises(DataQualityError, match="outlier detected"):
            raise OutlierError("outlier detected")

    def test_outlier_error_caught_by_base_class(self):
        """Test OutlierError can be caught as TradingPlatformError."""
        with pytest.raises(TradingPlatformError, match="outlier"):
            raise OutlierError("outlier")


class TestConfigurationError:
    """Tests for ConfigurationError exception."""

    def test_configuration_error_can_be_raised(self):
        """Test ConfigurationError can be raised."""
        with pytest.raises(ConfigurationError):
            raise ConfigurationError("TWILIO_ACCOUNT_SID not configured")

    def test_configuration_error_message_preserved(self):
        """Test ConfigurationError preserves error message."""
        error = ConfigurationError("API_KEY missing")

        assert str(error) == "API_KEY missing"

    def test_configuration_error_inherits_from_trading_platform_error(self):
        """Test ConfigurationError inherits from TradingPlatformError."""
        error = ConfigurationError("test")

        assert isinstance(error, TradingPlatformError)
        assert isinstance(error, Exception)

    def test_configuration_error_not_data_quality_error(self):
        """Test ConfigurationError is not a DataQualityError."""
        error = ConfigurationError("test")

        assert not isinstance(error, DataQualityError)

    def test_configuration_error_caught_by_base_class(self):
        """Test ConfigurationError can be caught as TradingPlatformError."""
        with pytest.raises(TradingPlatformError, match="missing config"):
            raise ConfigurationError("missing config")


class TestExceptionHierarchy:
    """Tests for the overall exception hierarchy structure."""

    def test_all_exceptions_derive_from_trading_platform_error(self):
        """Test all custom exceptions derive from TradingPlatformError."""
        exceptions = [
            DataQualityError,
            StalenessError,
            OutlierError,
            ConfigurationError,
        ]

        for exc_class in exceptions:
            assert issubclass(exc_class, TradingPlatformError)

    def test_staleness_and_outlier_are_siblings(self):
        """Test StalenessError and OutlierError are both DataQualityError subclasses."""
        assert issubclass(StalenessError, DataQualityError)
        assert issubclass(OutlierError, DataQualityError)

        # But they are not subclasses of each other
        assert not issubclass(StalenessError, OutlierError)
        assert not issubclass(OutlierError, StalenessError)

    def test_configuration_error_is_independent_of_data_quality(self):
        """Test ConfigurationError is not related to DataQualityError."""
        assert not issubclass(ConfigurationError, DataQualityError)
        assert not issubclass(DataQualityError, ConfigurationError)

    def test_exception_hierarchy_depth(self):
        """Test exception hierarchy has expected depth."""
        # TradingPlatformError -> Exception
        assert TradingPlatformError.__bases__ == (Exception,)

        # DataQualityError -> TradingPlatformError
        assert DataQualityError.__bases__ == (TradingPlatformError,)

        # StalenessError -> DataQualityError
        assert StalenessError.__bases__ == (DataQualityError,)

        # OutlierError -> DataQualityError
        assert OutlierError.__bases__ == (DataQualityError,)

        # ConfigurationError -> TradingPlatformError
        assert ConfigurationError.__bases__ == (TradingPlatformError,)


class TestExceptionCatchingBehavior:
    """Tests for exception catching behavior in try/except blocks."""

    def test_catch_specific_before_general(self):
        """Test specific exceptions can be caught before general ones."""
        caught_type = None

        try:
            raise StalenessError("stale data")
        except StalenessError:
            caught_type = "staleness"
        except DataQualityError:
            caught_type = "data_quality"
        except TradingPlatformError:
            caught_type = "platform"

        assert caught_type == "staleness"

    def test_general_catches_specific_if_not_handled(self):
        """Test general exception catches specific if not explicitly handled."""
        caught_type = None

        try:
            raise OutlierError("outlier")
        except StalenessError:
            caught_type = "staleness"
        except DataQualityError:
            caught_type = "data_quality"

        assert caught_type == "data_quality"

    def test_base_catches_all_platform_exceptions(self):
        """Test TradingPlatformError catches all derived exceptions."""
        exceptions_and_messages = [
            (DataQualityError("data"), "data"),
            (StalenessError("stale"), "stale"),
            (OutlierError("outlier"), "outlier"),
            (ConfigurationError("config"), "config"),
        ]

        for exc, expected_msg in exceptions_and_messages:
            with pytest.raises(TradingPlatformError, match=expected_msg):
                raise exc

    def test_standard_exception_does_not_catch_platform_errors(self):
        """Test standard exceptions (like ValueError) don't catch platform errors."""
        caught = False

        try:
            try:
                raise DataQualityError("test")
            except ValueError:
                caught = True
        except DataQualityError:
            pass  # Expected

        assert not caught


class TestExceptionWithFormatting:
    """Tests for exceptions with formatted messages."""

    def test_staleness_error_with_formatted_age(self):
        """Test StalenessError with formatted age value."""
        age_minutes = 45.678
        error = StalenessError(f"Data is {age_minutes:.1f}m old, exceeds 30m")

        assert str(error) == "Data is 45.7m old, exceeds 30m"

    def test_outlier_error_with_formatted_return(self):
        """Test OutlierError with formatted return percentage."""
        daily_return = 0.35
        symbol = "AAPL"
        error = OutlierError(f"Abnormal return {daily_return:.2%} for {symbol}")

        assert str(error) == "Abnormal return 35.00% for AAPL"

    def test_data_quality_error_with_price_change(self):
        """Test DataQualityError with price change value."""
        price_change = 0.55
        error = DataQualityError(f"Price change {price_change} exceeds 50%")

        assert str(error) == "Price change 0.55 exceeds 50%"

    def test_configuration_error_with_env_var_name(self):
        """Test ConfigurationError with environment variable name."""
        env_var = "ALPACA_API_KEY"
        error = ConfigurationError(f"{env_var} not configured")

        assert str(error) == "ALPACA_API_KEY not configured"
