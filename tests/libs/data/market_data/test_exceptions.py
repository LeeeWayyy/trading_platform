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
    try:
        raise ConnectionError("no connection")
    except MarketDataError as exc:
        assert isinstance(exc, ConnectionError)
        assert str(exc) == "no connection"
