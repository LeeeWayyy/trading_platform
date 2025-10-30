"""
Unit tests for TradingOrchestrator business logic.

Tests cover:
- Orchestrator initialization
- Complete orchestration workflow (run method)
- Signal fetching from Signal Service
- Signal-to-order mapping with position sizing
- Order submission to Execution Gateway
- Price fetching and caching
- Position sizing calculations
- Error handling and edge cases
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from apps.orchestrator.orchestrator import TradingOrchestrator, calculate_position_size
from apps.orchestrator.schemas import (
    OrderSubmission,
    Signal,
    SignalMetadata,
    SignalServiceResponse,
)


class TestTradingOrchestratorInitialization:
    """Tests for TradingOrchestrator initialization."""

    def test_initialization_with_required_params(self):
        """Test initialization with required parameters."""
        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
        )

        assert orchestrator.signal_client is not None
        assert orchestrator.execution_client is not None
        assert orchestrator.capital == Decimal("100000")
        assert orchestrator.max_position_size == Decimal("10000")
        assert orchestrator.price_cache == {}

    def test_initialization_with_price_cache(self):
        """Test initialization with custom price cache."""
        price_cache = {"AAPL": Decimal("150.00"), "MSFT": Decimal("300.00")}

        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache=price_cache,
        )

        assert orchestrator.price_cache == price_cache

    def test_initialization_inverse_vol_not_supported(self):
        """Test that inverse_vol allocation method raises clear error."""
        with pytest.raises(ValueError, match="inverse_vol.*not yet supported"):
            TradingOrchestrator(
                signal_service_url="http://localhost:8001",
                execution_gateway_url="http://localhost:8002",
                capital=Decimal("100000"),
                max_position_size=Decimal("10000"),
                allocation_method="inverse_vol",
            )


class TestTradingOrchestratorRun:
    """Tests for main orchestration workflow."""

    @pytest.fixture()
    def orchestrator(self):
        """Create TradingOrchestrator with mocked clients."""
        orch = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("20000"),
            price_cache={"AAPL": Decimal("150.00"), "MSFT": Decimal("300.00")},
        )

        # Mock the clients
        orch.signal_client = Mock()
        orch.execution_client = Mock()

        return orch

    @pytest.mark.asyncio()
    async def test_successful_run_with_all_orders_accepted(self, orchestrator):
        """Test successful orchestration run with all orders accepted."""
        # Mock signal response
        signal_response = SignalServiceResponse(
            signals=[
                Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.10),
                Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.05),
            ],
            metadata=SignalMetadata(
                as_of_date="2024-10-19",
                model_version="v1.0",
                strategy="alpha_baseline",
                num_signals=2,
                generated_at="2024-10-19T12:00:00Z",
                top_n=1,
                bottom_n=0,
            ),
        )
        orchestrator.signal_client.fetch_signals = AsyncMock(return_value=signal_response)

        # Mock order submissions
        orchestrator.execution_client.submit_order = AsyncMock(
            side_effect=[
                OrderSubmission(
                    client_order_id="order1",
                    status="pending_new",
                    broker_order_id="broker1",
                    symbol="AAPL",
                    side="buy",
                    qty=66,
                    order_type="market",
                    created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                    message="Order submitted",
                ),
                OrderSubmission(
                    client_order_id="order2",
                    status="pending_new",
                    broker_order_id="broker2",
                    symbol="MSFT",
                    side="buy",
                    qty=16,
                    order_type="market",
                    created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                    message="Order submitted",
                ),
            ]
        )

        result = await orchestrator.run(symbols=["AAPL", "MSFT"], strategy_id="alpha_baseline")

        assert result.status == "completed"
        assert result.num_signals == 2
        assert result.num_orders_submitted == 2
        assert result.num_orders_accepted == 2
        assert result.num_orders_rejected == 0
        assert len(result.mappings) == 2

    @pytest.mark.asyncio()
    async def test_partial_success_with_some_rejected(self, orchestrator):
        """Test orchestration run with partial success (some orders rejected)."""
        # Mock signal response
        signal_response = SignalServiceResponse(
            signals=[
                Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.10),
                Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.05),
            ],
            metadata=SignalMetadata(
                as_of_date="2024-10-19",
                model_version="v1.0",
                strategy="alpha_baseline",
                num_signals=2,
                generated_at="2024-10-19T12:00:00Z",
                top_n=1,
                bottom_n=0,
            ),
        )
        orchestrator.signal_client.fetch_signals = AsyncMock(return_value=signal_response)

        # Mock order submissions (first succeeds, second fails)
        orchestrator.execution_client.submit_order = AsyncMock(
            side_effect=[
                OrderSubmission(
                    client_order_id="order1",
                    status="pending_new",
                    broker_order_id="broker1",
                    symbol="AAPL",
                    side="buy",
                    qty=66,
                    order_type="market",
                    created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                    message="Order submitted",
                ),
                httpx.HTTPStatusError(
                    "Order rejected",
                    request=Mock(),
                    response=Mock(status_code=400, text="Insufficient buying power"),
                ),
            ]
        )

        result = await orchestrator.run(symbols=["AAPL", "MSFT"], strategy_id="alpha_baseline")

        assert result.status == "partial"
        assert result.num_orders_submitted == 1  # Only first order got client_order_id
        assert result.num_orders_accepted == 1
        assert result.num_orders_rejected == 1

    @pytest.mark.asyncio()
    async def test_failed_run_all_orders_rejected(self, orchestrator):
        """Test orchestration run where all orders are rejected."""
        # Mock signal response
        signal_response = SignalServiceResponse(
            signals=[
                Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.10),
            ],
            metadata=SignalMetadata(
                as_of_date="2024-10-19",
                model_version="v1.0",
                strategy="alpha_baseline",
                num_signals=1,
                generated_at="2024-10-19T12:00:00Z",
                top_n=1,
                bottom_n=0,
            ),
        )
        orchestrator.signal_client.fetch_signals = AsyncMock(return_value=signal_response)

        # Mock order submission failure
        orchestrator.execution_client.submit_order = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Order rejected",
                request=Mock(),
                response=Mock(status_code=400, text="Invalid order"),
            )
        )

        result = await orchestrator.run(symbols=["AAPL"], strategy_id="alpha_baseline")

        assert result.status == "failed"
        assert result.num_orders_submitted == 0  # No client_order_id when submission fails
        assert result.num_orders_accepted == 0
        assert result.num_orders_rejected == 1

    @pytest.mark.asyncio()
    async def test_error_during_signal_fetching(self, orchestrator):
        """Test orchestration run fails gracefully when signal fetching fails."""
        # Mock signal client to raise error
        orchestrator.signal_client.fetch_signals = AsyncMock(
            side_effect=httpx.HTTPError("Signal service unavailable")
        )

        result = await orchestrator.run(symbols=["AAPL"], strategy_id="alpha_baseline")

        assert result.status == "failed"
        assert result.num_signals == 0
        assert result.num_orders_submitted == 0
        assert "Signal service unavailable" in result.error_message


class TestFetchSignals:
    """Tests for signal fetching."""

    @pytest.mark.asyncio()
    async def test_fetch_signals_success(self):
        """Test successful signal fetching."""
        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
        )

        signal_response = SignalServiceResponse(
            signals=[
                Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.10),
                Signal(symbol="MSFT", predicted_return=-0.02, rank=2, target_weight=-0.05),
            ],
            metadata=SignalMetadata(
                as_of_date="2024-10-19",
                model_version="v1.0",
                strategy="alpha_baseline",
                num_signals=2,
                generated_at="2024-10-19T12:00:00Z",
                top_n=1,
                bottom_n=1,
            ),
        )

        orchestrator.signal_client = Mock()
        orchestrator.signal_client.fetch_signals = AsyncMock(return_value=signal_response)

        result = await orchestrator._fetch_signals(
            symbols=["AAPL", "MSFT"], as_of_date=date(2024, 10, 19)
        )

        assert len(result.signals) == 2
        assert result.metadata.num_signals == 2
        assert result.metadata.top_n == 1
        assert result.metadata.bottom_n == 1


class TestMapSignalsToOrders:
    """Tests for signal-to-order mapping with position sizing."""

    @pytest.fixture()
    def orchestrator(self):
        """Create orchestrator with price cache."""
        return TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("20000"),
            price_cache={"AAPL": Decimal("150.00"), "MSFT": Decimal("300.00")},
        )

    @pytest.mark.asyncio()
    async def test_map_long_signal_to_buy_order(self, orchestrator):
        """Test mapping long signal (positive weight) to buy order."""
        signals = [Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.10)]

        mappings = await orchestrator._map_signals_to_orders(signals)

        assert len(mappings) == 1
        assert mappings[0].symbol == "AAPL"
        assert mappings[0].order_side == "buy"
        assert mappings[0].order_qty == 66  # floor(100000 * 0.10 / 150)
        assert mappings[0].skip_reason is None

    @pytest.mark.asyncio()
    async def test_map_short_signal_to_sell_order(self, orchestrator):
        """Test mapping short signal (negative weight) to sell order."""
        signals = [Signal(symbol="MSFT", predicted_return=-0.02, rank=1, target_weight=-0.05)]

        mappings = await orchestrator._map_signals_to_orders(signals)

        assert len(mappings) == 1
        assert mappings[0].symbol == "MSFT"
        assert mappings[0].order_side == "sell"
        assert mappings[0].order_qty == 16  # floor(100000 * 0.05 / 300)

    @pytest.mark.asyncio()
    async def test_skip_zero_weight_signal(self, orchestrator):
        """Test skipping signal with zero target weight."""
        signals = [Signal(symbol="AAPL", predicted_return=0.0, rank=1, target_weight=0.0)]

        mappings = await orchestrator._map_signals_to_orders(signals)

        assert len(mappings) == 1
        assert mappings[0].skip_reason == "zero_weight"
        assert mappings[0].order_qty is None

    @pytest.mark.asyncio()
    async def test_cap_position_at_max_size(self, orchestrator):
        """Test capping position size when exceeding max."""
        # Signal with 50% weight = $50k, but max is $20k
        signals = [Signal(symbol="AAPL", predicted_return=0.10, rank=1, target_weight=0.50)]

        mappings = await orchestrator._map_signals_to_orders(signals)

        assert len(mappings) == 1
        # Should use max_position_size ($20k) instead of calculated ($50k)
        assert mappings[0].order_qty == 133  # floor(20000 / 150)

    @pytest.mark.asyncio()
    async def test_skip_when_qty_less_than_one_share(self, orchestrator):
        """Test skipping signal when position size < 1 share."""
        # Very small weight results in qty < 1
        signals = [Signal(symbol="MSFT", predicted_return=0.001, rank=1, target_weight=0.0001)]

        mappings = await orchestrator._map_signals_to_orders(signals)

        assert len(mappings) == 1
        assert mappings[0].skip_reason == "qty_less_than_one_share"
        assert mappings[0].order_qty is None

    @pytest.mark.asyncio()
    async def test_skip_when_price_fetch_fails(self):
        """Test skipping signal when price cannot be fetched."""
        # No price cache, price fetch will fail for unknown symbols
        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("20000"),
            price_cache={},
        )

        # Patch _get_current_price to raise error
        with patch.object(
            orchestrator, "_get_current_price", side_effect=Exception("Price API unavailable")
        ):
            signals = [Signal(symbol="UNKNOWN", predicted_return=0.05, rank=1, target_weight=0.10)]
            mappings = await orchestrator._map_signals_to_orders(signals)

            assert len(mappings) == 1
            assert "price_fetch_failed" in mappings[0].skip_reason


class TestSubmitOrders:
    """Tests for order submission."""

    @pytest.fixture()
    def orchestrator(self):
        """Create orchestrator with mocked execution client."""
        orch = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
        )
        orch.execution_client = Mock()
        return orch

    @pytest.mark.asyncio()
    async def test_submit_orders_success(self, orchestrator):
        """Test successful order submission updates mappings."""
        from apps.orchestrator.schemas import SignalOrderMapping

        mappings = [
            SignalOrderMapping(
                symbol="AAPL",
                predicted_return=0.05,
                rank=1,
                target_weight=0.10,
                order_qty=100,
                order_side="buy",
            ),
        ]

        orchestrator.execution_client.submit_order = AsyncMock(
            return_value=OrderSubmission(
                client_order_id="order123",
                status="pending_new",
                broker_order_id="broker123",
                symbol="AAPL",
                side="buy",
                qty=100,
                order_type="market",
                created_at=datetime(2024, 10, 19, 12, 0, 0, tzinfo=UTC),
                message="Order submitted",
            )
        )

        await orchestrator._submit_orders(mappings)

        assert mappings[0].client_order_id == "order123"
        assert mappings[0].broker_order_id == "broker123"
        assert mappings[0].order_status == "pending_new"

    @pytest.mark.asyncio()
    async def test_submit_orders_http_error(self, orchestrator):
        """Test order submission with HTTP error marks order as rejected."""
        from apps.orchestrator.schemas import SignalOrderMapping

        mappings = [
            SignalOrderMapping(
                symbol="AAPL",
                predicted_return=0.05,
                rank=1,
                target_weight=0.10,
                order_qty=100,
                order_side="buy",
            ),
        ]

        orchestrator.execution_client.submit_order = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Bad request",
                request=Mock(),
                response=Mock(status_code=400, text="Invalid order"),
            )
        )

        await orchestrator._submit_orders(mappings)

        assert mappings[0].order_status == "rejected"
        assert "submission_failed: 400" in mappings[0].skip_reason

    @pytest.mark.asyncio()
    async def test_submit_orders_unexpected_error(self, orchestrator):
        """Test order submission with unexpected error marks order as rejected."""
        from apps.orchestrator.schemas import SignalOrderMapping

        mappings = [
            SignalOrderMapping(
                symbol="AAPL",
                predicted_return=0.05,
                rank=1,
                target_weight=0.10,
                order_qty=100,
                order_side="buy",
            ),
        ]

        orchestrator.execution_client.submit_order = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        await orchestrator._submit_orders(mappings)

        assert mappings[0].order_status == "rejected"
        assert "unexpected_error" in mappings[0].skip_reason


class TestGetCurrentPrice:
    """Tests for price fetching."""

    @pytest.mark.asyncio()
    async def test_get_price_from_cache(self):
        """Test getting price from cache."""
        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache={"AAPL": Decimal("150.50")},
        )

        price = await orchestrator._get_current_price("AAPL")

        assert price == Decimal("150.50")

    @pytest.mark.asyncio()
    async def test_get_price_default_when_not_in_cache(self):
        """Test getting default price when symbol not in cache."""
        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache={},
        )

        price = await orchestrator._get_current_price("UNKNOWN")

        assert price == Decimal("100.00")  # Default price


class TestCalculatePositionSize:
    """Tests for position size calculation utility."""

    def test_calculate_position_size_normal(self):
        """Test normal position size calculation."""
        qty, dollar_amount = calculate_position_size(
            target_weight=0.10,
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("50000"),
        )

        assert qty == 66  # floor(10000 / 150)
        assert dollar_amount == Decimal("10000.00")

    def test_calculate_position_size_with_max_cap(self):
        """Test position size calculation with max cap applied."""
        qty, dollar_amount = calculate_position_size(
            target_weight=0.50,  # 50% = $50k
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("20000"),  # Cap at $20k
        )

        assert qty == 133  # floor(20000 / 150)
        assert dollar_amount == Decimal("20000.00")

    def test_calculate_position_size_negative_weight(self):
        """Test position size calculation with negative weight (short)."""
        qty, dollar_amount = calculate_position_size(
            target_weight=-0.05,  # -5% short position
            capital=Decimal("100000"),
            price=Decimal("300.00"),
            max_position_size=Decimal("50000"),
        )

        assert qty == 16  # floor(5000 / 300)
        assert dollar_amount == Decimal("5000.00")

    def test_calculate_position_size_zero_weight(self):
        """Test position size calculation with zero weight."""
        qty, dollar_amount = calculate_position_size(
            target_weight=0.0,
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("50000"),
        )

        assert qty == 0
        assert dollar_amount == Decimal("0.00")

    def test_calculate_position_size_very_small_weight(self):
        """Test position size calculation resulting in < 1 share."""
        qty, dollar_amount = calculate_position_size(
            target_weight=0.0001,  # 0.01% = $10
            capital=Decimal("100000"),
            price=Decimal("150.00"),
            max_position_size=Decimal("50000"),
        )

        assert qty == 0  # floor(10 / 150) = 0
        assert dollar_amount == Decimal("10.00")
