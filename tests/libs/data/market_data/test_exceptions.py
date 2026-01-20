import pytest

from libs.data.market_data.exceptions import (
    ConnectionError,
    MarketDataError,
    QuoteHandlingError,
    SubscriptionError,
)


def test_exception_hierarchy() -> None:
    assert issubclass(ConnectionError, MarketDataError)
    assert issubclass(SubscriptionError, MarketDataError)
    assert issubclass(QuoteHandlingError, MarketDataError)


def test_exceptions_can_be_raised() -> None:
    with pytest.raises(MarketDataError, match="no connection") as exc_info:
        raise ConnectionError("no connection")
    assert isinstance(exc_info.value, ConnectionError)
