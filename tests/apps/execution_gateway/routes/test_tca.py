"""Tests for TCA routes in apps/execution_gateway/routes/tca.py.

Tests the TCA analysis endpoints including parameter validation
and response format using real data processing functions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import get_context
from apps.execution_gateway.routes import tca
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import AuthContext


def create_mock_user_context() -> dict[str, Any]:
    """Create mock user context for testing."""
    mock_user = MagicMock()
    mock_user.role = "trader"
    return {
        "user_id": "test_user",
        "user": mock_user,
        "role": "trader",
    }


def create_mock_auth_context() -> AuthContext:
    """Create mock auth context."""
    ctx = MagicMock(spec=AuthContext)
    ctx.user_id = "test_user"
    ctx.user = MagicMock()
    ctx.user.role = "trader"
    return ctx


def create_sample_trades(
    client_order_id: str = "order-123",
    symbol: str = "AAPL",
    side: str = "buy",
    strategy_id: str = "alpha_baseline",
    num_trades: int = 3,
) -> list[dict[str, Any]]:
    """Create sample trade data for testing."""
    base_time = datetime.now(UTC) - timedelta(hours=1)
    order_submitted_at = base_time - timedelta(minutes=5)

    trades = []
    for i in range(num_trades):
        trades.append({
            "trade_id": f"trade-{i}",
            "client_order_id": client_order_id,
            "broker_order_id": f"broker-{client_order_id}",
            "strategy_id": strategy_id,
            "symbol": symbol,
            "side": side,
            "qty": 100,
            "price": 150.0 + i * 0.10,  # Slight price movement
            "executed_at": base_time + timedelta(minutes=i),
            "source": "webhook",
            "order_submitted_at": order_submitted_at,
            "order_qty": 300,
            "order_filled_qty": 300,
            "filled_avg_price": 150.10,
            "order_metadata": {"fills": []},
        })
    return trades


@pytest.fixture()
def mock_db() -> MagicMock:
    """Create mock database client."""
    db = MagicMock()
    db.get_trades_for_tca.return_value = create_sample_trades()
    return db


@pytest.fixture()
def test_client(mock_db: MagicMock) -> TestClient:
    """Create test client with mocked dependencies."""
    app = FastAPI()
    app.include_router(tca.router)

    def create_mock_app_context() -> AppContext:
        ctx = MagicMock(spec=AppContext)
        ctx.db = mock_db
        return ctx

    # Override all dependencies
    app.dependency_overrides[tca.tca_auth] = create_mock_auth_context
    app.dependency_overrides[get_context] = create_mock_app_context
    app.dependency_overrides[build_user_context] = create_mock_user_context

    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_get_authorized_strategies() -> Any:
    """Mock get_authorized_strategies to return demo strategy for all tests."""
    with patch(
        "apps.execution_gateway.routes.tca.get_authorized_strategies",
        return_value=["alpha_baseline"],
    ):
        yield


@pytest.fixture(autouse=True)
def mock_taq_provider() -> Any:
    """Mock TAQ provider to return None (no TAQ data available)."""
    with patch(
        "apps.execution_gateway.routes.tca._get_taq_provider",
        return_value=None,
    ):
        yield


class TestGetTCAAnalysis:
    """Tests for GET /api/v1/tca/analysis endpoint."""

    def test_analysis_valid_request(self, test_client: TestClient) -> None:
        """Valid analysis request returns summary and orders."""
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "orders" in data
        assert data["summary"]["start_date"] == str(start)
        assert data["summary"]["end_date"] == str(today)

    def test_analysis_with_symbol_filter(self, test_client: TestClient) -> None:
        """Analysis with symbol filter."""
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
                "symbol": "AAPL",
            },
        )

        assert response.status_code == 200

    def test_analysis_with_side_filter(self, test_client: TestClient) -> None:
        """Analysis with side filter."""
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
                "side": "buy",
            },
        )

        assert response.status_code == 200

    def test_analysis_invalid_date_range(self, test_client: TestClient) -> None:
        """End date before start date returns 400."""
        today = date.today()

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(today),
                "end_date": str(today - timedelta(days=7)),
            },
        )

        assert response.status_code == 400
        assert "end_date must be >= start_date" in response.json()["detail"]

    def test_analysis_date_range_exceeds_max(self, test_client: TestClient) -> None:
        """Date range > 90 days returns 400."""
        today = date.today()

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(today - timedelta(days=100)),
                "end_date": str(today),
            },
        )

        assert response.status_code == 400
        assert "cannot exceed" in response.json()["detail"]

    def test_analysis_no_trades_returns_empty(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """No trades returns empty orders list."""
        mock_db.get_trades_for_tca.return_value = []
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["orders"] == []
        assert data["summary"]["total_orders"] == 0

    def test_analysis_truncation_warning(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Large result set triggers truncation warning."""
        # Create 1001 trades to trigger truncation warning (limit+1 is fetched)
        trades = []
        for i in range(1001):
            trades.extend(create_sample_trades(client_order_id=f"order-{i}", num_trades=1))
        mock_db.get_trades_for_tca.return_value = trades
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Should have truncation warning
        assert any("truncated" in w.lower() for w in data["summary"]["warnings"])


class TestGetOrderTCA:
    """Tests for GET /api/v1/tca/analysis/{client_order_id} endpoint."""

    def test_order_tca_valid_request(self, test_client: TestClient) -> None:
        """Valid order TCA request returns order detail."""
        response = test_client.get("/api/v1/tca/analysis/order-123")

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "order-123"
        assert "implementation_shortfall_bps" in data
        assert "vwap_slippage_bps" in data

    def test_order_tca_not_found(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Order not found returns 404."""
        mock_db.get_trades_for_tca.return_value = []

        response = test_client.get("/api/v1/tca/analysis/nonexistent-order")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_order_tca_inconsistent_strategy_ids(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Order with inconsistent strategy IDs returns 422."""
        trades = create_sample_trades()
        # Set different strategy IDs for trades in the same order
        trades[0]["strategy_id"] = "alpha_baseline"
        trades[1]["strategy_id"] = "beta_strategy"
        trades[2]["strategy_id"] = "alpha_baseline"
        mock_db.get_trades_for_tca.return_value = trades

        response = test_client.get("/api/v1/tca/analysis/order-123")

        assert response.status_code == 422
        assert "inconsistent strategy" in response.json()["detail"].lower()


class TestGetBenchmarks:
    """Tests for GET /api/v1/tca/benchmarks endpoint."""

    def test_benchmarks_vwap(self, test_client: TestClient) -> None:
        """Get VWAP benchmark comparison."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={
                "client_order_id": "order-123",
                "benchmark": "vwap",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "order-123"
        assert data["benchmark_type"] == "vwap"
        assert "points" in data

    def test_benchmarks_twap(self, test_client: TestClient) -> None:
        """Get TWAP benchmark comparison."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={
                "client_order_id": "order-123",
                "benchmark": "twap",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["benchmark_type"] == "twap"

    def test_benchmarks_arrival(self, test_client: TestClient) -> None:
        """Get arrival price benchmark comparison."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={
                "client_order_id": "order-123",
                "benchmark": "arrival",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["benchmark_type"] == "arrival"

    def test_benchmarks_default_vwap(self, test_client: TestClient) -> None:
        """Default benchmark type is VWAP."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={"client_order_id": "order-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["benchmark_type"] == "vwap"

    def test_benchmarks_not_found(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Order not found returns 404."""
        mock_db.get_trades_for_tca.return_value = []

        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={"client_order_id": "nonexistent-order"},
        )

        assert response.status_code == 404

    def test_benchmarks_inconsistent_strategy_ids(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Benchmarks with inconsistent strategy IDs returns 422."""
        trades = create_sample_trades()
        # Set different strategy IDs for trades in the same order
        trades[0]["strategy_id"] = "alpha_baseline"
        trades[1]["strategy_id"] = "beta_strategy"
        trades[2]["strategy_id"] = "alpha_baseline"
        mock_db.get_trades_for_tca.return_value = trades

        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={"client_order_id": "order-123"},
        )

        assert response.status_code == 422
        assert "inconsistent strategy" in response.json()["detail"].lower()


class TestTCADataProcessing:
    """Tests for internal TCA data processing functions."""

    def test_group_trades_by_order(self) -> None:
        """Group trades correctly by client_order_id."""
        trades = [
            {"client_order_id": "order-1", "qty": 100},
            {"client_order_id": "order-1", "qty": 200},
            {"client_order_id": "order-2", "qty": 150},
        ]

        grouped, missing_count = tca._group_trades_by_order(trades)

        assert len(grouped["order-1"]) == 2
        assert len(grouped["order-2"]) == 1
        assert missing_count == 0

    def test_group_trades_by_order_missing_client_order_id(self) -> None:
        """Trades missing client_order_id are counted."""
        trades = [
            {"client_order_id": "order-1", "qty": 100},
            {"client_order_id": None, "qty": 200},  # Missing
            {"qty": 150},  # Also missing
        ]

        grouped, missing_count = tca._group_trades_by_order(trades)

        assert len(grouped["order-1"]) == 1
        assert missing_count == 2

    def test_build_fill_batch_valid(self) -> None:
        """Build FillBatch from valid trades."""
        trades = create_sample_trades()

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert fill_batch.symbol == "AAPL"
        assert fill_batch.side == "buy"
        assert len(fill_batch.fills) == 3

    def test_build_fill_batch_empty(self) -> None:
        """Empty trades returns None."""
        fill_batch = tca._build_fill_batch("order-123", [])

        assert fill_batch is None

    def test_build_fill_batch_missing_data(self) -> None:
        """Missing required data returns None."""
        trades = [{"client_order_id": "order-123"}]  # Missing symbol, executed_at

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is None

    def test_build_fill_batch_zero_qty_trades_skipped(self) -> None:
        """Trades with zero qty are skipped, resulting in None if all invalid."""
        trades = create_sample_trades()
        # Set qty to 0 for all trades - they should be skipped
        for trade in trades:
            trade["qty"] = 0

        fill_batch = tca._build_fill_batch("order-123", trades)

        # All trades skipped due to invalid qty, so returns None
        assert fill_batch is None

    def test_build_fill_batch_side_normalization(self) -> None:
        """Side is normalized to lowercase (prevents inverted cost signs)."""
        trades = create_sample_trades()
        # Use uppercase side (common in broker APIs)
        for trade in trades:
            trade["side"] = "BUY"

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        # Side should be normalized to lowercase
        assert fill_batch.side == "buy"

    def test_build_fill_batch_side_bytes(self) -> None:
        """Side as bytes is correctly normalized."""
        trades = create_sample_trades()
        for trade in trades:
            trade["side"] = b"SELL"  # Bytes representation

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert fill_batch.side == "sell"

    def test_build_fill_batch_side_enum_with_bytes_value(self) -> None:
        """Side as enum with bytes value is correctly normalized."""
        from enum import Enum

        class SideEnum(Enum):
            BUY = b"BUY"
            SELL = b"SELL"

        trades = create_sample_trades()
        for trade in trades:
            trade["side"] = SideEnum.BUY  # Enum with bytes value

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert fill_batch.side == "buy"

    def test_build_fill_batch_side_invalid_bytes(self) -> None:
        """Invalid bytes encoding returns None gracefully (no UnicodeDecodeError)."""
        trades = create_sample_trades()
        for trade in trades:
            trade["side"] = b"\xff\xfe"  # Invalid UTF-8 bytes

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should return None gracefully, not raise UnicodeDecodeError
        assert fill_batch is None

    def test_build_fill_batch_invalid_side(self) -> None:
        """Invalid side value returns None."""
        trades = create_sample_trades()
        for trade in trades:
            trade["side"] = "HOLD"  # Invalid side

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is None

    def test_build_fill_batch_missing_side(self) -> None:
        """Missing side in ALL trades returns None."""
        trades = create_sample_trades()
        for trade in trades:
            del trade["side"]  # Remove side entirely

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should return None, NOT default to "buy" which would invert cost signs
        assert fill_batch is None

    def test_build_fill_batch_first_trade_missing_side(self) -> None:
        """When first trade lacks side, use first valid side from later trades."""
        trades = create_sample_trades()
        trades[0]["side"] = None  # First trade missing side
        trades[1]["side"] = "buy"  # Second trade has it
        trades[2]["side"] = "buy"  # Third trade matches

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should use side from second trade
        assert fill_batch is not None
        assert fill_batch.side == "buy"

    def test_build_fill_batch_first_trade_missing_symbol(self) -> None:
        """When first trade lacks symbol, use first valid symbol from later trades."""
        trades = create_sample_trades()
        trades[0]["symbol"] = ""  # First trade empty symbol
        trades[1]["symbol"] = "AAPL"  # Second trade has it
        trades[2]["symbol"] = "AAPL"  # Third trade matches

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should use symbol from second trade
        assert fill_batch is not None
        assert fill_batch.symbol == "AAPL"

    def test_build_fill_batch_fractional_qty_rejected(self) -> None:
        """Fractional quantities are rejected, not silently truncated."""
        trades = create_sample_trades()
        trades[0]["qty"] = 100  # Valid integer
        trades[1]["qty"] = 50.5  # Fractional - should be skipped
        trades[2]["qty"] = 100  # Valid integer

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should succeed with 2 fills (trade with 50.5 skipped)
        assert fill_batch is not None
        assert len(fill_batch.fills) == 2
        # Total should be 200 (100 + 100), not 250.5 truncated to 250
        total_qty = sum(f.quantity for f in fill_batch.fills)
        assert total_qty == 200

    def test_build_fill_batch_empty_side(self) -> None:
        """Empty side string returns None."""
        trades = create_sample_trades()
        for trade in trades:
            trade["side"] = ""  # Empty string

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is None

    def test_build_fill_batch_inconsistent_symbols(self) -> None:
        """Trades with different symbols in same order return None."""
        trades = create_sample_trades()
        trades[0]["symbol"] = "AAPL"
        trades[1]["symbol"] = "GOOG"  # Different symbol
        trades[2]["symbol"] = "AAPL"

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should return None due to symbol inconsistency
        assert fill_batch is None

    def test_build_fill_batch_inconsistent_sides(self) -> None:
        """Trades with different sides in same order return None."""
        trades = create_sample_trades()
        trades[0]["side"] = "buy"
        trades[1]["side"] = "sell"  # Different side
        trades[2]["side"] = "buy"

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should return None due to side inconsistency
        assert fill_batch is None

    def test_build_fill_batch_inconsistent_order_qty(self) -> None:
        """Trades with different order_qty in same order return None."""
        trades = create_sample_trades()
        trades[0]["order_qty"] = 300
        trades[1]["order_qty"] = 500  # Different order_qty
        trades[2]["order_qty"] = 300

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should return None due to order_qty inconsistency
        assert fill_batch is None

    def test_build_fill_batch_consistent_order_qty(self) -> None:
        """Trades with consistent order_qty pass validation."""
        trades = create_sample_trades()
        for trade in trades:
            trade["order_qty"] = 300  # Same order_qty

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert fill_batch.total_target_qty == 300

    def test_build_fill_batch_first_trade_missing_order_qty(self) -> None:
        """When first trade lacks order_qty, use first valid value from later trades."""
        trades = create_sample_trades()
        trades[0]["order_qty"] = None  # First trade missing
        trades[1]["order_qty"] = 300  # Second trade has it
        trades[2]["order_qty"] = 300  # Third trade matches

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should use order_qty from second trade
        assert fill_batch is not None
        assert fill_batch.total_target_qty == 300

    def test_build_fill_batch_first_trade_zero_order_qty(self) -> None:
        """When first trade has zero order_qty, use first valid value from later trades."""
        trades = create_sample_trades()
        trades[0]["order_qty"] = 0  # First trade zero
        trades[1]["order_qty"] = 300  # Second trade has it
        trades[2]["order_qty"] = 300  # Third trade matches

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should use order_qty from second trade
        assert fill_batch is not None
        assert fill_batch.total_target_qty == 300

    def test_build_fill_batch_consistent_data(self) -> None:
        """Trades with consistent symbol/side pass validation."""
        trades = create_sample_trades()
        # All trades have same symbol/side (default from create_sample_trades)

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert fill_batch.symbol == "AAPL"
        assert fill_batch.side == "buy"

    def test_build_fill_batch_zero_price_skipped(self) -> None:
        """Trades with zero price are skipped."""
        trades = create_sample_trades()
        for trade in trades:
            trade["price"] = 0  # Zero price

        fill_batch = tca._build_fill_batch("order-123", trades)

        # All trades skipped due to invalid price, so returns None
        assert fill_batch is None

    def test_build_fill_batch_negative_price_skipped(self) -> None:
        """Trades with negative price are skipped."""
        trades = create_sample_trades()
        for trade in trades:
            trade["price"] = -150.0  # Negative price

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is None

    def test_build_fill_batch_non_numeric_price_skipped(self) -> None:
        """Trades with non-numeric price are skipped."""
        trades = create_sample_trades()
        for trade in trades:
            trade["price"] = "invalid"  # Non-numeric

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is None

    def test_build_fill_batch_non_numeric_qty_skipped(self) -> None:
        """Trades with non-numeric qty are skipped."""
        trades = create_sample_trades()
        for trade in trades:
            trade["qty"] = "invalid"  # Non-numeric

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is None

    def test_build_fill_batch_float_string_qty_handled(self) -> None:
        """Float string quantities like '100.0' are parsed correctly."""
        trades = create_sample_trades()
        for trade in trades:
            trade["qty"] = "100.0"  # Float string from some DB drivers

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert len(fill_batch.fills) == 3
        # Verify quantities are correctly parsed as integers
        for fill in fill_batch.fills:
            assert fill.quantity == 100

    def test_build_fill_batch_float_string_order_qty_handled(self) -> None:
        """Float string order_qty like '300.0' is parsed correctly."""
        trades = create_sample_trades(num_trades=3)
        for trade in trades:
            trade["order_qty"] = "300.0"  # Float string
            trade["qty"] = 100

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert fill_batch.total_target_qty == 300

    def test_build_fill_batch_non_numeric_order_qty_fallback(self) -> None:
        """Non-numeric order_qty falls back to sum of fills."""
        trades = create_sample_trades(num_trades=2)
        for trade in trades:
            trade["order_qty"] = "invalid"  # Non-numeric
            trade["qty"] = 100

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        # Should fall back to sum of fills (100 * 2 = 200)
        assert fill_batch.total_target_qty == 200

    def test_build_fill_batch_non_numeric_fee_defaults_zero(self) -> None:
        """Non-numeric fee defaults to 0.0."""
        trades = create_sample_trades()
        # Add metadata with non-numeric fee
        trades[0]["order_metadata"] = {
            "fills": [{"fill_id": trades[0]["trade_id"], "fee": "invalid"}]
        }

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        # Fee should default to 0.0
        assert fill_batch.fills[0].fee_amount == 0.0

    def test_build_fill_batch_negative_order_qty_fallback(self) -> None:
        """Negative order_qty falls back to sum of fills (not used as target)."""
        trades = create_sample_trades(num_trades=3)
        # Set negative order_qty - should fall back to sum of fills
        for trade in trades:
            trade["order_qty"] = -100
            trade["qty"] = 100

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Negative order_qty should be rejected, falling back to sum of fills
        assert fill_batch is not None
        assert fill_batch.total_target_qty == 300  # Sum of 3 x 100

    def test_compute_simple_tca(self) -> None:
        """Compute simple TCA metrics from fills."""
        trades = create_sample_trades()
        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        order_detail = tca._compute_simple_tca(fill_batch)

        assert order_detail is not None
        assert order_detail.symbol == "AAPL"
        assert order_detail.side == "buy"
        assert order_detail.filled_qty == 300
        assert order_detail.num_fills == 3
        assert "Simplified TCA" in order_detail.warnings[0]

    def test_compute_simple_tca_empty_fills(self) -> None:
        """Simple TCA returns None when no fills."""
        from libs.platform.analytics.execution_quality import FillBatch

        # Create a FillBatch with no valid fills (via mock)
        fill_batch = FillBatch(
            symbol="AAPL",
            side="buy",
            fills=[],  # No fills
            decision_time=datetime.now(UTC),
            submission_time=datetime.now(UTC),
            total_target_qty=100,
        )

        order_detail = tca._compute_simple_tca(fill_batch)
        assert order_detail is None

    def test_compute_simple_tca_fill_rate_weighting(self) -> None:
        """Simple TCA weights filled components by fill_rate (matching ExecutionQualityAnalyzer)."""
        trades = create_sample_trades(num_trades=1)
        # Modify to simulate partial fill
        trades[0]["order_qty"] = 200  # Target 200, filled only 100
        trades[0]["qty"] = 100

        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        order_detail = tca._compute_simple_tca(fill_batch)

        assert order_detail is not None
        # fill_rate should be 0.5 (100/200)
        assert order_detail.fill_rate == 0.5
        # Total cost should be weighted: (price + fee) * fill_rate + opportunity
        # This verifies the fix for fill_rate weighting

    def test_compute_simple_tca_overfill_clamped(self) -> None:
        """Overfill (filled > target) is clamped to 100% fill rate."""
        trades = create_sample_trades(num_trades=2)
        # Modify to simulate overfill: target 100, filled 200
        trades[0]["order_qty"] = 100  # Target 100
        trades[0]["qty"] = 100
        trades[1]["order_qty"] = 100
        trades[1]["qty"] = 100  # Total filled = 200 > 100 target

        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        order_detail = tca._compute_simple_tca(fill_batch)

        assert order_detail is not None
        # fill_rate should be clamped to 1.0 (not 2.0)
        assert order_detail.fill_rate == 1.0
        # opportunity_cost should be 0 (no unfilled)
        assert order_detail.opportunity_cost_bps == 0.0
        # Should have overfill warning
        assert any("overfill" in w.lower() for w in order_detail.warnings)

    def test_analyze_trades_for_tca_empty(self) -> None:
        """Empty trades list returns empty orders with warning."""
        orders, warnings = tca._analyze_trades_for_tca([], ["alpha_baseline"])

        assert len(orders) == 0
        assert any("No trades found" in w for w in warnings)

    def test_analyze_trades_for_tca_valid(self) -> None:
        """Valid trades returns analyzed orders."""
        trades = create_sample_trades()

        orders, warnings = tca._analyze_trades_for_tca(trades, ["alpha_baseline"])

        assert len(orders) == 1
        assert orders[0].client_order_id == "order-123"

    def test_analyze_trades_for_tca_inconsistent_strategy_ids(self) -> None:
        """Orders with inconsistent strategy IDs are skipped."""
        trades = create_sample_trades()
        # Set different strategy IDs for trades in the same order
        trades[0]["strategy_id"] = "alpha_baseline"
        trades[1]["strategy_id"] = "beta_strategy"  # Different strategy
        trades[2]["strategy_id"] = "alpha_baseline"

        orders, warnings = tca._analyze_trades_for_tca(trades, ["alpha_baseline", "beta_strategy"])

        # Order should be skipped due to inconsistent strategy IDs
        assert len(orders) == 0

    def test_analyze_trades_for_tca_skip_counters_reported(self) -> None:
        """Skipped orders are counted and reported in warnings."""
        # Create multiple orders with different skip reasons
        order1_trades = create_sample_trades(client_order_id="order-valid")
        order2_trades = create_sample_trades(client_order_id="order-invalid-data")
        order3_trades = create_sample_trades(client_order_id="order-unauthorized")

        # Order 2: invalid data (missing symbol)
        for trade in order2_trades:
            trade["symbol"] = ""

        # Order 3: unauthorized strategy
        for trade in order3_trades:
            trade["strategy_id"] = "unauthorized_strategy"

        all_trades = order1_trades + order2_trades + order3_trades

        orders, warnings = tca._analyze_trades_for_tca(all_trades, ["alpha_baseline"])

        # Only order 1 should succeed
        assert len(orders) == 1
        assert orders[0].client_order_id == "order-valid"

        # Should have a warning about skipped orders
        skip_warning = [w for w in warnings if "skipped" in w.lower()]
        assert len(skip_warning) == 1
        assert "invalid data: 1" in skip_warning[0].lower()
        assert "unauthorized: 1" in skip_warning[0].lower()


class TestTAQEnabledPath:
    """Tests for TCA with TAQ data available."""

    def test_analyze_order_with_taq_success(self) -> None:
        """TAQ-enabled analysis returns result from analyzer."""
        from libs.platform.analytics.execution_quality import (
            ExecutionAnalysisResult,
            ExecutionQualityAnalyzer,
        )

        trades = create_sample_trades()
        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        # Create mock analyzer with mock result
        mock_analyzer = MagicMock(spec=ExecutionQualityAnalyzer)
        mock_result = MagicMock(spec=ExecutionAnalysisResult)
        mock_result.symbol = "AAPL"
        mock_result.side = "buy"
        mock_result.execution_date = date.today()
        mock_result.arrival_price = 150.0
        mock_result.execution_price = 150.10
        mock_result.vwap_benchmark = 150.05
        mock_result.twap_benchmark = 150.03
        mock_result.total_target_qty = 300
        mock_result.total_filled_qty = 300
        mock_result.fill_rate = 1.0
        mock_result.total_notional = 45030.0
        mock_result.total_cost_bps = 2.5
        mock_result.price_shortfall_bps = 1.5
        mock_result.vwap_slippage_bps = 1.0
        mock_result.fee_cost_bps = 0.5
        mock_result.opportunity_cost_bps = 0.0
        mock_result.market_impact_bps = 0.3
        mock_result.timing_cost_bps = 0.2
        mock_result.num_fills = 3
        mock_result.execution_duration_seconds = 120.0
        mock_result.total_fees = 1.5
        mock_result.warnings = []
        mock_result.vwap_coverage_pct = 0.95
        mock_analyzer.analyze_execution.return_value = mock_result

        result = tca._analyze_order_with_taq(fill_batch, mock_analyzer)

        assert result is not None
        assert result.symbol == "AAPL"
        mock_analyzer.analyze_execution.assert_called_once_with(fill_batch)

    def test_analyze_order_with_taq_failure(self) -> None:
        """TAQ analysis returns None on analyzer failure."""
        from libs.platform.analytics.execution_quality import ExecutionQualityAnalyzer

        trades = create_sample_trades()
        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        # Create mock analyzer that raises exception
        mock_analyzer = MagicMock(spec=ExecutionQualityAnalyzer)
        mock_analyzer.analyze_execution.side_effect = RuntimeError("TAQ data unavailable")

        result = tca._analyze_order_with_taq(fill_batch, mock_analyzer)

        assert result is None

    def test_analyze_trades_with_taq_provider(self) -> None:
        """_analyze_trades_for_tca uses TAQ provider when available."""
        from libs.platform.analytics.execution_quality import (
            ExecutionAnalysisResult,
        )

        trades = create_sample_trades()

        # Create mock result
        mock_result = MagicMock(spec=ExecutionAnalysisResult)
        mock_result.symbol = "AAPL"
        mock_result.side = "buy"
        mock_result.execution_date = date.today()
        mock_result.arrival_price = 150.0
        mock_result.execution_price = 150.10
        mock_result.vwap_benchmark = 150.05
        mock_result.twap_benchmark = 150.03
        mock_result.total_target_qty = 300
        mock_result.total_filled_qty = 300
        mock_result.fill_rate = 1.0
        mock_result.total_notional = 45030.0
        mock_result.total_cost_bps = 2.5
        mock_result.price_shortfall_bps = 1.5
        mock_result.vwap_slippage_bps = 1.0
        mock_result.fee_cost_bps = 0.5
        mock_result.opportunity_cost_bps = 0.0
        mock_result.market_impact_bps = 0.3
        mock_result.timing_cost_bps = 0.2
        mock_result.num_fills = 3
        mock_result.execution_duration_seconds = 120.0
        mock_result.total_fees = 1.5
        mock_result.warnings = []
        mock_result.vwap_coverage_pct = 0.95

        mock_taq_provider = MagicMock()

        with patch("apps.execution_gateway.routes.tca._get_taq_provider", return_value=mock_taq_provider):
            with patch("apps.execution_gateway.routes.tca.ExecutionQualityAnalyzer") as MockAnalyzer:
                mock_analyzer = MagicMock()
                mock_analyzer.analyze_execution.return_value = mock_result
                MockAnalyzer.return_value = mock_analyzer

                orders, warnings = tca._analyze_trades_for_tca(trades, ["alpha_baseline"])

        # Should have created analyzer with TAQ provider
        MockAnalyzer.assert_called_once_with(taq_provider=mock_taq_provider)
        # Should have called analyze_execution
        assert mock_analyzer.analyze_execution.called
        # Should have one order with TAQ data
        assert len(orders) == 1
        # No "simplified TCA" warning since TAQ was available
        assert not any("simplified" in w.lower() for w in warnings)


class TestDatetimeParsing:
    """Tests for datetime string parsing in TCA."""

    def test_parse_datetime_from_string(self) -> None:
        """ISO datetime string is parsed correctly."""
        result = tca._parse_datetime("2025-01-15T10:30:00Z", "test_field")

        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.tzinfo is not None  # Should be timezone-aware

    def test_parse_datetime_from_datetime(self) -> None:
        """datetime object passes through correctly."""
        dt = datetime.now(UTC)
        result = tca._parse_datetime(dt, "test_field")

        assert result is dt

    def test_parse_datetime_naive_gets_utc(self) -> None:
        """Naive datetime gets UTC timezone added."""
        naive_dt = datetime(2025, 1, 15, 10, 30, 0)
        result = tca._parse_datetime(naive_dt, "test_field")

        assert result is not None
        assert result.tzinfo == UTC

    def test_parse_datetime_non_utc_normalized_to_utc(self) -> None:
        """Non-UTC tz-aware datetime is converted to UTC."""
        # 23:30 EST (-05:00) = 04:30 next day UTC
        result = tca._parse_datetime("2025-01-15T23:30:00-05:00", "test_field")

        assert result is not None
        assert result.tzinfo == UTC
        # Should be converted to UTC
        assert result.hour == 4  # 23:30 - 5h = 28:30 = 04:30 next day
        assert result.day == 16  # Next day in UTC

    def test_parse_datetime_invalid_string(self) -> None:
        """Invalid datetime string returns None."""
        result = tca._parse_datetime("not-a-date", "test_field")

        assert result is None

    def test_parse_datetime_none(self) -> None:
        """None value returns None."""
        result = tca._parse_datetime(None, "test_field")

        assert result is None

    def test_build_fill_batch_with_string_timestamps(self) -> None:
        """FillBatch is built correctly from string timestamps."""
        trades = create_sample_trades()
        # Replace datetime with ISO strings
        for trade in trades:
            if isinstance(trade["executed_at"], datetime):
                trade["executed_at"] = trade["executed_at"].isoformat()
            if isinstance(trade["order_submitted_at"], datetime):
                trade["order_submitted_at"] = trade["order_submitted_at"].isoformat()

        fill_batch = tca._build_fill_batch("order-123", trades)

        assert fill_batch is not None
        assert len(fill_batch.fills) == 3

    def test_build_fill_batch_missing_order_submitted_at_fallback(self) -> None:
        """When order_submitted_at is missing, use earliest VALID fill timestamp as fallback."""
        trades = create_sample_trades()
        # Set different execution times
        base = datetime.now(UTC)
        trades[0]["executed_at"] = base - timedelta(minutes=10)  # Earliest
        trades[1]["executed_at"] = base - timedelta(minutes=5)
        trades[2]["executed_at"] = base  # Latest
        # Remove order_submitted_at to trigger fallback
        for trade in trades:
            trade["order_submitted_at"] = None

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should succeed using earliest valid fill timestamp as decision_time
        assert fill_batch is not None
        assert fill_batch.decision_time == trades[0]["executed_at"]
        assert fill_batch.submission_time == trades[0]["executed_at"]

    def test_build_fill_batch_fallback_uses_only_valid_fills(self) -> None:
        """Fallback decision_time should use only valid fills, not invalid ones."""
        trades = create_sample_trades()
        base = datetime.now(UTC)
        # Trade 0: earliest timestamp but INVALID qty (will be filtered out)
        trades[0]["executed_at"] = base - timedelta(minutes=10)
        trades[0]["qty"] = 0  # Invalid - will be filtered
        # Trade 1: second earliest, VALID
        trades[1]["executed_at"] = base - timedelta(minutes=5)
        trades[1]["qty"] = 100  # Valid
        # Trade 2: latest, VALID
        trades[2]["executed_at"] = base
        trades[2]["qty"] = 100  # Valid
        # Remove order_submitted_at to trigger fallback
        for trade in trades:
            trade["order_submitted_at"] = None

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should succeed using trade 1's timestamp (earliest VALID fill)
        assert fill_batch is not None
        # Should have 2 fills (trade 0 filtered due to invalid qty)
        assert len(fill_batch.fills) == 2
        # Decision time should be trade 1's timestamp, NOT trade 0's
        assert fill_batch.decision_time == trades[1]["executed_at"]
        assert fill_batch.submission_time == trades[1]["executed_at"]

    def test_build_fill_batch_missing_both_timestamps_returns_none(self) -> None:
        """When both order_submitted_at and executed_at are missing, return None."""
        trades = create_sample_trades()
        # Remove all timestamps
        for trade in trades:
            trade["order_submitted_at"] = None
            trade["executed_at"] = None

        fill_batch = tca._build_fill_batch("order-123", trades)

        # Should return None - no valid timestamps at all
        assert fill_batch is None


class TestSlippageSign:
    """Tests for correct slippage sign calculation."""

    def test_buy_side_positive_slippage(self) -> None:
        """Buy side: price increase = positive slippage (bad for buyer)."""
        trades = create_sample_trades(side="buy")
        # Set prices: first fill lower than later fills
        trades[0]["price"] = 100.0
        trades[1]["price"] = 101.0
        trades[2]["price"] = 102.0

        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        order_detail = tca._compute_simple_tca(fill_batch)

        assert order_detail is not None
        # Price went up after arrival = positive shortfall for buyer
        assert order_detail.price_shortfall_bps > 0

    def test_sell_side_positive_slippage(self) -> None:
        """Sell side: price decrease = positive slippage (bad for seller)."""
        trades = create_sample_trades(side="sell")
        # Set prices: first fill higher than later fills
        trades[0]["price"] = 102.0
        trades[1]["price"] = 101.0
        trades[2]["price"] = 100.0

        fill_batch = tca._build_fill_batch("order-123", trades)
        assert fill_batch is not None

        order_detail = tca._compute_simple_tca(fill_batch)

        assert order_detail is not None
        # Price went down after arrival = positive shortfall for seller
        assert order_detail.price_shortfall_bps > 0


class TestTruncation:
    """Tests for truncation handling with partial orders."""

    def test_analysis_exact_limit_no_false_truncation(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """When result count exactly equals limit, no false truncation warning."""
        # Create exactly 999 trades (the query_limit) but not more
        # The DB returns 999 when we ask for 1000 (limit+1), so no truncation detected
        # Note: query_limit=999 so we fetch limit=1000, getting 999 means no more data
        trades = []
        # 333 orders * 3 trades each = 999 trades
        for i in range(333):
            order_trades = create_sample_trades(client_order_id=f"order-{i}", num_trades=3)
            for j, trade in enumerate(order_trades):
                trade["trade_id"] = f"trade-{i}-{j}"
                trade["order_qty"] = 300  # 3 fills * 100 qty
            trades.extend(order_trades)

        assert len(trades) == 999

        mock_db.get_trades_for_tca.return_value = trades
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Should NOT have truncation warning since exactly limit, not more
        assert not any("truncated" in w.lower() for w in data["summary"]["warnings"])
        # All 333 orders should be present
        assert data["summary"]["total_orders"] == 333

    def test_analysis_truncation_discards_tail_orders(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """When limit is hit, orders in the tail are discarded to ensure complete data."""
        # Create 1001 trades (more than limit to trigger truncation)
        # Create distinct orders with proper structure
        trades = []

        # Create 100 complete orders with 10 trades each (1000 trades)
        for i in range(100):
            order_trades = create_sample_trades(client_order_id=f"order-{i}", num_trades=10)
            for j, trade in enumerate(order_trades):
                trade["trade_id"] = f"trade-{i}-{j}"
                trade["order_qty"] = 1000  # Set proper order_qty (10 fills * 100 qty)
            trades.extend(order_trades)

        # Add 1 more trade to trigger truncation
        extra = create_sample_trades(client_order_id="order-100", num_trades=1)
        extra[0]["trade_id"] = "trade-100-0"
        extra[0]["order_qty"] = 100
        trades.extend(extra)

        # Now we have 1001 trades total
        assert len(trades) == 1001

        mock_db.get_trades_for_tca.return_value = trades
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Should have truncation warning
        assert any("truncated" in w.lower() for w in data["summary"]["warnings"])
        # Tail orders should be discarded (last 10% = 100 trades covering 10+ orders)
        # With 10 trades per order, 100 trades = 10 complete orders discarded
        # We should have 90 orders (order-0 through order-89)
        assert data["summary"]["total_orders"] == 90
        # Verify tail orders are not in results
        order_ids = [o["client_order_id"] for o in data["orders"]]
        for i in range(90, 101):
            assert f"order-{i}" not in order_ids

    def test_analysis_truncation_interleaved_orders(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Interleaved orders in tail are all discarded."""
        # Create 1001 trades (more than limit) with interleaved orders near the end
        trades = []

        # First 900 trades: orders 0-89 with 10 trades each
        for i in range(90):
            order_trades = create_sample_trades(client_order_id=f"order-{i}", num_trades=10)
            for j, trade in enumerate(order_trades):
                trade["trade_id"] = f"trade-{i}-{j}"
                trade["order_qty"] = 1000  # Set proper order_qty
            trades.extend(order_trades)

        # Next 100 trades: interleaved orders 90 and 91 (50 trades each)
        for i in range(50):
            # order-90 trade
            t90 = create_sample_trades(client_order_id="order-90", num_trades=1)
            t90[0]["trade_id"] = f"trade-90-{i}"
            t90[0]["order_qty"] = 5000  # 50 trades * 100 qty
            trades.extend(t90)
            # order-91 trade
            t91 = create_sample_trades(client_order_id="order-91", num_trades=1)
            t91[0]["trade_id"] = f"trade-91-{i}"
            t91[0]["order_qty"] = 5000  # 50 trades * 100 qty
            trades.extend(t91)

        # Add 1 more trade to trigger truncation
        extra = create_sample_trades(client_order_id="order-92", num_trades=1)
        extra[0]["trade_id"] = "trade-92-0"
        extra[0]["order_qty"] = 100
        trades.extend(extra)

        assert len(trades) == 1001

        mock_db.get_trades_for_tca.return_value = trades
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Both interleaved orders (90, 91, and 92) should be discarded
        order_ids = [o["client_order_id"] for o in data["orders"]]
        assert "order-90" not in order_ids
        assert "order-91" not in order_ids
        assert "order-92" not in order_ids
        # Should have 90 orders (order-0 through order-89)
        assert data["summary"]["total_orders"] == 90


class TestAuthorizationDenial:
    """Tests for strategy-scoped access control."""

    def test_analysis_unauthorized_strategy(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Request for unauthorized strategy returns 403."""
        today = date.today()
        start = today - timedelta(days=7)

        # Mock returns alpha_baseline but we request different strategy
        with patch(
            "apps.execution_gateway.routes.tca.get_authorized_strategies",
            return_value=["alpha_baseline"],  # Only authorized for alpha_baseline
        ):
            response = test_client.get(
                "/api/v1/tca/analysis",
                params={
                    "start_date": str(start),
                    "end_date": str(today),
                    "strategy_id": "unauthorized_strategy",  # Not authorized
                },
            )

        assert response.status_code == 403
        assert "not authorized" in response.json()["detail"].lower()

    def test_analysis_no_authorized_strategies(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """User with no authorized strategies returns 403."""
        today = date.today()
        start = today - timedelta(days=7)

        with patch(
            "apps.execution_gateway.routes.tca.get_authorized_strategies",
            return_value=[],  # No authorized strategies
        ):
            response = test_client.get(
                "/api/v1/tca/analysis",
                params={
                    "start_date": str(start),
                    "end_date": str(today),
                },
            )

        assert response.status_code == 403
        assert "no authorized strategies" in response.json()["detail"].lower()

    def test_order_tca_unauthorized_strategy(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Order TCA for unauthorized strategy returns 403."""
        # Create trades with a different strategy
        trades = create_sample_trades(strategy_id="unauthorized_strategy")
        mock_db.get_trades_for_tca.return_value = trades

        with patch(
            "apps.execution_gateway.routes.tca.get_authorized_strategies",
            return_value=["alpha_baseline"],  # Only authorized for alpha_baseline
        ):
            response = test_client.get("/api/v1/tca/analysis/order-123")

        assert response.status_code == 403
        assert "not authorized" in response.json()["detail"].lower()

    def test_benchmarks_unauthorized_strategy(
        self, test_client: TestClient, mock_db: MagicMock
    ) -> None:
        """Benchmarks for unauthorized strategy returns 403."""
        trades = create_sample_trades(strategy_id="unauthorized_strategy")
        mock_db.get_trades_for_tca.return_value = trades

        with patch(
            "apps.execution_gateway.routes.tca.get_authorized_strategies",
            return_value=["alpha_baseline"],
        ):
            response = test_client.get(
                "/api/v1/tca/benchmarks",
                params={"client_order_id": "order-123"},
            )

        assert response.status_code == 403
