"""Tests for TCA schemas in apps/execution_gateway/schemas/tca.py.

Tests model validation, serialization, and edge cases for all TCA-related
Pydantic models.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from apps.execution_gateway.schemas import (
    TCAAnalysisRequest,
    TCAAnalysisSummary,
    TCABenchmarkPoint,
    TCABenchmarkResponse,
    TCAMetricValue,
    TCAOrderDetail,
    TCASummaryResponse,
)


class TestTCAAnalysisRequest:
    """Tests for TCAAnalysisRequest model."""

    def test_valid_request_minimal(self) -> None:
        """Valid request with only required fields."""
        req = TCAAnalysisRequest(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        assert req.start_date == date(2024, 1, 1)
        assert req.end_date == date(2024, 1, 31)
        assert req.symbol is None
        assert req.strategy_id is None
        assert req.side is None

    def test_valid_request_all_fields(self) -> None:
        """Valid request with all optional fields."""
        req = TCAAnalysisRequest(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            symbol="AAPL",
            strategy_id="alpha_baseline",
            side="buy",
        )
        assert req.symbol == "AAPL"
        assert req.strategy_id == "alpha_baseline"
        assert req.side == "buy"

    def test_valid_side_sell(self) -> None:
        """Valid request with sell side."""
        req = TCAAnalysisRequest(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            side="sell",
        )
        assert req.side == "sell"

    def test_invalid_side(self) -> None:
        """Invalid side value raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TCAAnalysisRequest(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                side="invalid",  # type: ignore[arg-type]
            )
        assert "side" in str(exc_info.value)

    def test_missing_required_field(self) -> None:
        """Missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            TCAAnalysisRequest(start_date=date(2024, 1, 1))  # type: ignore[call-arg]


class TestTCAMetricValue:
    """Tests for TCAMetricValue model."""

    def test_valid_metric(self) -> None:
        """Valid metric with all fields."""
        metric = TCAMetricValue(
            value=2.5,
            label="Implementation Shortfall",
            is_good=True,
            description="Total cost of execution",
        )
        assert metric.value == 2.5
        assert metric.label == "Implementation Shortfall"
        assert metric.is_good is True
        assert metric.description == "Total cost of execution"

    def test_default_values(self) -> None:
        """Metric with default values."""
        metric = TCAMetricValue(value=-1.2, label="VWAP Slippage")
        assert metric.is_good is True
        assert metric.description is None

    def test_negative_value(self) -> None:
        """Negative values are allowed (favorable execution)."""
        metric = TCAMetricValue(value=-3.5, label="Price Improvement")
        assert metric.value == -3.5


class TestTCAAnalysisSummary:
    """Tests for TCAAnalysisSummary model."""

    @pytest.fixture
    def valid_summary_data(self) -> dict:
        """Fixture with valid summary data."""
        return {
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 1, 31),
            "computation_timestamp": datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
            "total_orders": 100,
            "total_fills": 250,
            "total_notional": 5000000.0,
            "total_shares": 50000,
            "avg_fill_rate": 0.95,
            "avg_implementation_shortfall_bps": 2.5,
            "avg_price_shortfall_bps": 1.5,
            "avg_vwap_slippage_bps": 1.0,
            "avg_fee_cost_bps": 0.5,
            "avg_opportunity_cost_bps": 0.5,
            "avg_market_impact_bps": 1.0,
            "avg_timing_cost_bps": 0.5,
        }

    def test_valid_summary(self, valid_summary_data: dict) -> None:
        """Valid summary with all required fields."""
        summary = TCAAnalysisSummary(**valid_summary_data)
        assert summary.total_orders == 100
        assert summary.avg_fill_rate == 0.95
        assert summary.warnings == []

    def test_summary_with_warnings(self, valid_summary_data: dict) -> None:
        """Summary with data quality warnings."""
        valid_summary_data["warnings"] = ["Incomplete data for 2024-01-15"]
        summary = TCAAnalysisSummary(**valid_summary_data)
        assert len(summary.warnings) == 1

    def test_invalid_fill_rate_above_one(self, valid_summary_data: dict) -> None:
        """Fill rate above 1.0 raises ValidationError."""
        valid_summary_data["avg_fill_rate"] = 1.5
        with pytest.raises(ValidationError) as exc_info:
            TCAAnalysisSummary(**valid_summary_data)
        assert "avg_fill_rate" in str(exc_info.value)

    def test_invalid_fill_rate_negative(self, valid_summary_data: dict) -> None:
        """Negative fill rate raises ValidationError."""
        valid_summary_data["avg_fill_rate"] = -0.1
        with pytest.raises(ValidationError) as exc_info:
            TCAAnalysisSummary(**valid_summary_data)
        assert "avg_fill_rate" in str(exc_info.value)

    def test_invalid_negative_orders(self, valid_summary_data: dict) -> None:
        """Negative order count raises ValidationError."""
        valid_summary_data["total_orders"] = -1
        with pytest.raises(ValidationError) as exc_info:
            TCAAnalysisSummary(**valid_summary_data)
        assert "total_orders" in str(exc_info.value)


class TestTCAOrderDetail:
    """Tests for TCAOrderDetail model."""

    @pytest.fixture
    def valid_order_data(self) -> dict:
        """Fixture with valid order detail data."""
        return {
            "client_order_id": "order-123",
            "symbol": "AAPL",
            "side": "buy",
            "strategy_id": "alpha_baseline",
            "execution_date": date(2024, 1, 15),
            "arrival_price": 150.00,
            "execution_price": 150.25,
            "vwap_benchmark": 150.20,
            "twap_benchmark": 150.18,
            "target_qty": 1000,
            "filled_qty": 950,
            "fill_rate": 0.95,
            "total_notional": 142737.50,
            "implementation_shortfall_bps": 1.67,
            "price_shortfall_bps": 1.00,
            "vwap_slippage_bps": 0.33,
            "fee_cost_bps": 0.10,
            "opportunity_cost_bps": 0.50,
            "market_impact_bps": 0.80,
            "timing_cost_bps": 0.20,
            "num_fills": 5,
            "execution_duration_seconds": 120.5,
            "total_fees": 14.27,
        }

    def test_valid_order_detail(self, valid_order_data: dict) -> None:
        """Valid order detail with all fields."""
        order = TCAOrderDetail(**valid_order_data)
        assert order.client_order_id == "order-123"
        assert order.symbol == "AAPL"
        assert order.side == "buy"
        assert order.fill_rate == 0.95
        assert order.vwap_coverage_pct == 100.0  # default

    def test_order_without_strategy(self, valid_order_data: dict) -> None:
        """Order without strategy_id is valid."""
        valid_order_data["strategy_id"] = None
        order = TCAOrderDetail(**valid_order_data)
        assert order.strategy_id is None

    def test_order_with_warnings(self, valid_order_data: dict) -> None:
        """Order with data quality warnings."""
        valid_order_data["warnings"] = ["Low VWAP coverage"]
        valid_order_data["vwap_coverage_pct"] = 75.0
        order = TCAOrderDetail(**valid_order_data)
        assert len(order.warnings) == 1
        assert order.vwap_coverage_pct == 75.0

    def test_invalid_side(self, valid_order_data: dict) -> None:
        """Invalid side value raises ValidationError."""
        valid_order_data["side"] = "short"  # not buy/sell
        with pytest.raises(ValidationError):
            TCAOrderDetail(**valid_order_data)

    def test_invalid_fill_rate(self, valid_order_data: dict) -> None:
        """Fill rate > 1 raises ValidationError."""
        valid_order_data["fill_rate"] = 1.05
        with pytest.raises(ValidationError):
            TCAOrderDetail(**valid_order_data)

    def test_invalid_vwap_coverage(self, valid_order_data: dict) -> None:
        """VWAP coverage > 100 raises ValidationError."""
        valid_order_data["vwap_coverage_pct"] = 105.0
        with pytest.raises(ValidationError):
            TCAOrderDetail(**valid_order_data)


class TestTCABenchmarkPoint:
    """Tests for TCABenchmarkPoint model."""

    def test_valid_point(self) -> None:
        """Valid benchmark point."""
        point = TCABenchmarkPoint(
            timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            execution_price=150.25,
            benchmark_price=150.20,
            benchmark_type="vwap",
            slippage_bps=0.33,
            cumulative_qty=500,
        )
        assert point.execution_price == 150.25
        assert point.benchmark_type == "vwap"
        assert point.cumulative_qty == 500

    def test_all_benchmark_types(self) -> None:
        """All valid benchmark types."""
        for btype in ["vwap", "twap", "arrival"]:
            point = TCABenchmarkPoint(
                timestamp=datetime.now(UTC),
                execution_price=100.0,
                benchmark_price=100.0,
                benchmark_type=btype,  # type: ignore[arg-type]
                slippage_bps=0.0,
                cumulative_qty=100,
            )
            assert point.benchmark_type == btype

    def test_invalid_benchmark_type(self) -> None:
        """Invalid benchmark type raises ValidationError."""
        with pytest.raises(ValidationError):
            TCABenchmarkPoint(
                timestamp=datetime.now(UTC),
                execution_price=100.0,
                benchmark_price=100.0,
                benchmark_type="invalid",  # type: ignore[arg-type]
                slippage_bps=0.0,
                cumulative_qty=100,
            )

    def test_negative_slippage_allowed(self) -> None:
        """Negative slippage (favorable) is allowed."""
        point = TCABenchmarkPoint(
            timestamp=datetime.now(UTC),
            execution_price=100.0,
            benchmark_price=100.5,
            benchmark_type="vwap",
            slippage_bps=-5.0,  # Bought below benchmark
            cumulative_qty=100,
        )
        assert point.slippage_bps == -5.0


class TestTCABenchmarkResponse:
    """Tests for TCABenchmarkResponse model."""

    def test_valid_response_minimal(self) -> None:
        """Valid response with minimal data."""
        resp = TCABenchmarkResponse(
            client_order_id="order-123",
            symbol="AAPL",
            side="buy",
            benchmark_type="vwap",
        )
        assert resp.client_order_id == "order-123"
        assert resp.points == []
        assert resp.summary is None

    def test_valid_response_with_points(self) -> None:
        """Valid response with time series points."""
        points = [
            TCABenchmarkPoint(
                timestamp=datetime(2024, 1, 15, 10, i, 0, tzinfo=UTC),
                execution_price=150.0 + i * 0.01,
                benchmark_price=150.0,
                benchmark_type="vwap",
                slippage_bps=i * 0.1,
                cumulative_qty=i * 100,
            )
            for i in range(5)
        ]
        resp = TCABenchmarkResponse(
            client_order_id="order-123",
            symbol="AAPL",
            side="sell",
            benchmark_type="twap",
            points=points,
        )
        assert len(resp.points) == 5
        assert resp.side == "sell"
        assert resp.benchmark_type == "twap"


class TestTCASummaryResponse:
    """Tests for TCASummaryResponse model."""

    def test_valid_response(self) -> None:
        """Valid summary response."""
        summary = TCAAnalysisSummary(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            computation_timestamp=datetime.now(UTC),
            total_orders=10,
            total_fills=25,
            total_notional=500000.0,
            total_shares=5000,
            avg_fill_rate=0.98,
            avg_implementation_shortfall_bps=1.5,
            avg_price_shortfall_bps=1.0,
            avg_vwap_slippage_bps=0.5,
            avg_fee_cost_bps=0.3,
            avg_opportunity_cost_bps=0.2,
            avg_market_impact_bps=0.7,
            avg_timing_cost_bps=0.3,
        )
        resp = TCASummaryResponse(summary=summary)
        assert resp.summary.total_orders == 10
        assert resp.orders == []

    def test_response_with_orders(self) -> None:
        """Summary response with order details."""
        summary = TCAAnalysisSummary(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            computation_timestamp=datetime.now(UTC),
            total_orders=1,
            total_fills=3,
            total_notional=15000.0,
            total_shares=100,
            avg_fill_rate=1.0,
            avg_implementation_shortfall_bps=1.0,
            avg_price_shortfall_bps=0.5,
            avg_vwap_slippage_bps=0.3,
            avg_fee_cost_bps=0.1,
            avg_opportunity_cost_bps=0.0,
            avg_market_impact_bps=0.5,
            avg_timing_cost_bps=0.2,
        )
        order = TCAOrderDetail(
            client_order_id="order-1",
            symbol="MSFT",
            side="buy",
            execution_date=date(2024, 1, 15),
            arrival_price=300.0,
            execution_price=300.10,
            vwap_benchmark=300.05,
            twap_benchmark=300.03,
            target_qty=100,
            filled_qty=100,
            fill_rate=1.0,
            total_notional=30010.0,
            implementation_shortfall_bps=3.33,
            price_shortfall_bps=2.0,
            vwap_slippage_bps=1.67,
            fee_cost_bps=0.1,
            opportunity_cost_bps=0.0,
            market_impact_bps=1.5,
            timing_cost_bps=0.5,
            num_fills=3,
            execution_duration_seconds=60.0,
            total_fees=3.00,
        )
        resp = TCASummaryResponse(summary=summary, orders=[order])
        assert len(resp.orders) == 1
        assert resp.orders[0].symbol == "MSFT"
