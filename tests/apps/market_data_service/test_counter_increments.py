"""
Tests for Prometheus counter increment wiring (issue #169).

Verifies that the counters defined in ``apps/market_data_service/main.py`` are
actually incremented at the expected lifecycle points:

- ``market_data_websocket_messages_received_total`` on each received message
- ``market_data_reconnect_attempts_total`` on each WebSocket reconnect attempt
- ``market_data_position_syncs_total`` on each position-sync cycle

These replace the "counter is defined" assertions in test_metrics.py with
"counter actually moves" assertions, so the alerts in
``infra/prometheus/alerts.yml`` have real signal to fire on.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from prometheus_client import CollectorRegistry, Counter

from apps.market_data_service.position_sync import PositionBasedSubscription
from libs.data.market_data.alpaca_stream import AlpacaMarketDataStream


def _counter_value(counter: Counter, **labels: str) -> float:
    """Return the current value of a Prometheus counter (label-aware)."""
    if labels:
        return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]
    # Unlabelled counters expose the value directly via ``_value``.
    return counter._value.get()  # type: ignore[attr-defined]


@pytest.fixture()
def messages_counter() -> Counter:
    """Fresh counter in an isolated registry (mirrors main.py definition)."""
    registry = CollectorRegistry()
    return Counter(
        "market_data_websocket_messages_received_total",
        "Total number of WebSocket messages received",
        ["message_type"],
        registry=registry,
    )


@pytest.fixture()
def reconnect_counter() -> Counter:
    """Fresh counter in an isolated registry (mirrors main.py definition)."""
    registry = CollectorRegistry()
    return Counter(
        "market_data_reconnect_attempts_total",
        "Total number of WebSocket reconnection attempts",
        registry=registry,
    )


@pytest.fixture()
def syncs_counter() -> Counter:
    """Fresh counter in an isolated registry (mirrors main.py definition)."""
    registry = CollectorRegistry()
    return Counter(
        "market_data_position_syncs_total",
        "Total number of position-based subscription syncs",
        ["status"],
        registry=registry,
    )


@pytest.fixture()
def mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.set = MagicMock()
    return redis


@pytest.fixture()
def mock_publisher() -> MagicMock:
    publisher = MagicMock()
    publisher.publish = MagicMock()
    return publisher


@pytest.fixture()
def stream(
    mock_redis: MagicMock,
    mock_publisher: MagicMock,
    messages_counter: Counter,
    reconnect_counter: Counter,
) -> AlpacaMarketDataStream:
    with patch("libs.data.market_data.alpaca_stream.StockDataStream") as stream_cls:
        stream_cls.return_value = MagicMock()
        s = AlpacaMarketDataStream(
            api_key="k",
            secret_key="s",
            redis_client=mock_redis,
            event_publisher=mock_publisher,
            price_ttl=300,
            messages_received_counter=messages_counter,
            reconnect_attempts_counter=reconnect_counter,
        )
        s.stream = stream_cls.return_value
        yield s


def _make_quote(symbol: str = "AAPL") -> Mock:
    q = Mock()
    q.symbol = symbol
    q.bid_price = 150.00
    q.ask_price = 150.10
    q.bid_size = 100
    q.ask_size = 200
    q.timestamp = datetime.now(UTC)
    q.ask_exchange = "NASDAQ"
    return q


class TestWebsocketMessagesReceivedCounter:
    """Each received WebSocket message increments the counter."""

    @pytest.mark.asyncio()
    async def test_valid_quote_increments_counter(
        self, stream: AlpacaMarketDataStream, messages_counter: Counter
    ) -> None:
        assert _counter_value(messages_counter, message_type="quote") == 0.0

        await stream._handle_quote(_make_quote())

        assert _counter_value(messages_counter, message_type="quote") == 1.0

    @pytest.mark.asyncio()
    async def test_counter_increments_per_message(
        self, stream: AlpacaMarketDataStream, messages_counter: Counter
    ) -> None:
        for _ in range(3):
            await stream._handle_quote(_make_quote())

        assert _counter_value(messages_counter, message_type="quote") == 3.0

    @pytest.mark.asyncio()
    async def test_invalid_quote_still_counted(
        self, stream: AlpacaMarketDataStream, messages_counter: Counter
    ) -> None:
        """Even malformed messages count toward inbound volume."""
        bad = Mock()
        bad.symbol = "AAPL"
        bad.bid_price = "not-a-number"
        bad.ask_price = 150.00
        bad.bid_size = 100
        bad.ask_size = 200
        bad.timestamp = datetime.now(UTC)
        bad.ask_exchange = "NASDAQ"

        # Should not raise (stream resilience) and should still increment.
        await stream._handle_quote(bad)

        assert _counter_value(messages_counter, message_type="quote") == 1.0


class TestReconnectAttemptsCounter:
    """Each WebSocket reconnect attempt increments the counter."""

    @pytest.mark.asyncio()
    async def test_reconnect_increments_counter(
        self, stream: AlpacaMarketDataStream, reconnect_counter: Counter
    ) -> None:
        assert _counter_value(reconnect_counter) == 0.0

        # Fail twice, then bail by raising so we exit the retry loop quickly.
        call_count = {"n": 0}

        def fake_run() -> None:
            call_count["n"] += 1
            raise RuntimeError("boom")

        stream.stream.run = fake_run
        stream._max_reconnect_attempts = 2  # bail after 2 attempts

        # Patch sleep to avoid the exponential backoff in tests.
        with patch("libs.data.market_data.alpaca_stream.asyncio.sleep", new=AsyncMock()):
            from libs.data.market_data.exceptions import ConnectionError as MDConnErr

            with pytest.raises(MDConnErr):
                await stream.start()

        assert _counter_value(reconnect_counter) == 2.0


class TestReconnectAttemptsCleanReturn:
    """Clean-return reconnect cycles (stream.run() returns while _running is True)
    must also be counted; otherwise flapping connections silently undercount."""

    @pytest.mark.asyncio()
    async def test_clean_return_while_running_increments_counter(
        self, stream: AlpacaMarketDataStream, reconnect_counter: Counter
    ) -> None:
        assert _counter_value(reconnect_counter) == 0.0

        call_count = {"n": 0}

        def fake_run() -> None:
            call_count["n"] += 1
            # First two cycles: return normally (clean close). After that, raise
            # to let the ConnectionError bail us out of the retry loop.
            if call_count["n"] >= 3:
                raise RuntimeError("stop")
            # Return normally with _running still True -> flapping reconnect.

        stream.stream.run = fake_run
        stream._max_reconnect_attempts = 1  # first exception will bail

        with patch("libs.data.market_data.alpaca_stream.asyncio.sleep", new=AsyncMock()):
            from libs.data.market_data.exceptions import ConnectionError as MDConnErr

            with pytest.raises(MDConnErr):
                await stream.start()

        # Two clean-return flaps + one exception-path reconnect = 3 increments.
        assert _counter_value(reconnect_counter) == 3.0


class TestPositionSyncsCounter:
    """Each position-sync cycle increments the counter with a status label."""

    @pytest.fixture()
    def mock_stream(self) -> AsyncMock:
        s = AsyncMock(spec=AlpacaMarketDataStream)
        s.get_subscribed_symbols.return_value = []
        s.subscribe_symbols = AsyncMock()
        s.unsubscribe_symbols = AsyncMock()
        return s

    @pytest.mark.asyncio()
    async def test_successful_sync_increments_success(
        self, mock_stream: AsyncMock, syncs_counter: Counter
    ) -> None:
        mgr = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://gw",
            sync_interval=1,
            initial_sync=False,
            syncs_counter=syncs_counter,
        )

        with patch.object(mgr, "_fetch_position_symbols", new=AsyncMock(return_value={"AAPL"})):
            await mgr._sync_subscriptions()

        assert _counter_value(syncs_counter, status="success") == 1.0
        assert _counter_value(syncs_counter, status="error") == 0.0

    @pytest.mark.asyncio()
    async def test_failed_fetch_increments_error(
        self, mock_stream: AsyncMock, syncs_counter: Counter
    ) -> None:
        mgr = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://gw",
            sync_interval=1,
            initial_sync=False,
            syncs_counter=syncs_counter,
        )

        # _fetch_position_symbols returns None on failure (documented contract).
        with patch.object(mgr, "_fetch_position_symbols", new=AsyncMock(return_value=None)):
            await mgr._sync_subscriptions()

        assert _counter_value(syncs_counter, status="error") == 1.0
        assert _counter_value(syncs_counter, status="success") == 0.0

    @pytest.mark.asyncio()
    async def test_exception_increments_error(
        self, mock_stream: AsyncMock, syncs_counter: Counter
    ) -> None:
        mgr = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://gw",
            sync_interval=1,
            initial_sync=False,
            syncs_counter=syncs_counter,
        )

        with patch.object(
            mgr,
            "_fetch_position_symbols",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            # _sync_subscriptions swallows unexpected errors internally.
            await mgr._sync_subscriptions()

        assert _counter_value(syncs_counter, status="error") == 1.0

    @pytest.mark.asyncio()
    async def test_subscribe_failure_increments_error(self, syncs_counter: Counter) -> None:
        """Inner SubscriptionError on subscribe_symbols must flip status to error."""
        from libs.data.market_data.exceptions import SubscriptionError

        mock_stream = AsyncMock(spec=AlpacaMarketDataStream)
        mock_stream.get_subscribed_symbols.return_value = []
        mock_stream.subscribe_symbols = AsyncMock(side_effect=SubscriptionError("bad"))
        mock_stream.unsubscribe_symbols = AsyncMock()

        mgr = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://gw",
            sync_interval=1,
            initial_sync=False,
            syncs_counter=syncs_counter,
        )

        with patch.object(mgr, "_fetch_position_symbols", new=AsyncMock(return_value={"AAPL"})):
            await mgr._sync_subscriptions()

        assert _counter_value(syncs_counter, status="error") == 1.0
        assert _counter_value(syncs_counter, status="success") == 0.0

    @pytest.mark.asyncio()
    async def test_unsubscribe_failure_increments_error(self, syncs_counter: Counter) -> None:
        """Inner SubscriptionError on unsubscribe_symbols must flip status to error."""
        from libs.data.market_data.exceptions import SubscriptionError

        mock_stream = AsyncMock(spec=AlpacaMarketDataStream)
        # Pretend MSFT was previously subscribed so closed_symbols is non-empty.
        mock_stream.get_subscribed_symbols.return_value = ["MSFT"]
        mock_stream.subscribe_symbols = AsyncMock()
        mock_stream.unsubscribe_symbols = AsyncMock(side_effect=SubscriptionError("bad"))

        mgr = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://gw",
            sync_interval=1,
            initial_sync=False,
            syncs_counter=syncs_counter,
        )
        # Seed last_position_symbols so MSFT shows up as "closed" this cycle.
        mgr._last_position_symbols = {"MSFT"}

        with patch.object(mgr, "_fetch_position_symbols", new=AsyncMock(return_value=set())):
            await mgr._sync_subscriptions()

        assert _counter_value(syncs_counter, status="error") == 1.0
        assert _counter_value(syncs_counter, status="success") == 0.0

    @pytest.mark.asyncio()
    async def test_multiple_syncs_accumulate(
        self, mock_stream: AsyncMock, syncs_counter: Counter
    ) -> None:
        mgr = PositionBasedSubscription(
            stream=mock_stream,
            execution_gateway_url="http://gw",
            sync_interval=1,
            initial_sync=False,
            syncs_counter=syncs_counter,
        )

        with patch.object(mgr, "_fetch_position_symbols", new=AsyncMock(return_value=set())):
            for _ in range(3):
                await mgr._sync_subscriptions()

        assert _counter_value(syncs_counter, status="success") == 3.0


# Sanity check: importing main.py wires the counters into the long-lived
# stream/subscription-manager objects. We can't easily run the lifespan here,
# but we can assert that the Counter singletons exist and match expected labels.
class TestMainModuleWiring:
    def test_main_counters_declare_expected_labels(self) -> None:
        from apps.market_data_service import main as md_main

        # websocket_messages_received_total uses `message_type` label.
        md_main.websocket_messages_received_total.labels(message_type="quote")
        # position_syncs_total uses `status` label.
        md_main.position_syncs_total.labels(status="success")
        md_main.position_syncs_total.labels(status="error")
        # reconnect_attempts_total is unlabelled.
        md_main.reconnect_attempts_total.inc(0)  # no-op
        # Also sanity-check instance types.
        assert isinstance(md_main.websocket_messages_received_total, Counter)
        assert isinstance(md_main.position_syncs_total, Counter)
        assert isinstance(md_main.reconnect_attempts_total, Counter)


# Suppress unused-import warning for asyncio (used implicitly via pytest-asyncio).
_ = asyncio
