"""Unit tests for orchestrator schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from apps.orchestrator.schemas import (
    ConfigResponse,
    HealthResponse,
    OrchestrationRequest,
    OrchestrationResult,
    OrderRequest,
    SignalOrderMapping,
)


class TestOrchestrationRequest:
    def test_symbols_required_min_length(self) -> None:
        with pytest.raises(ValidationError):
            OrchestrationRequest(symbols=[])


class TestOrderRequest:
    def test_default_time_in_force(self) -> None:
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        assert order.time_in_force == "day"
        assert order.limit_price is None


class TestTimestampSerialization:
    def test_health_response_serializes_timestamp_with_z(self) -> None:
        response = HealthResponse(
            status="healthy",
            service="orchestrator",
            version="0.1.0",
            timestamp=datetime(2024, 12, 31, 0, 0, tzinfo=UTC),
            signal_service_url="http://signals",
            execution_gateway_url="http://exec",
            signal_service_healthy=True,
            execution_gateway_healthy=True,
            database_connected=True,
        )

        payload = response.model_dump_json()
        assert "2024-12-31T00:00:00Z" in payload

    def test_config_response_serializes_timestamp_with_z(self) -> None:
        response = ConfigResponse(
            service="orchestrator",
            version="0.1.0",
            environment="staging",
            dry_run=True,
            alpaca_paper=True,
            circuit_breaker_enabled=True,
            timestamp=datetime(2024, 12, 31, 0, 0, tzinfo=UTC),
        )

        payload = response.model_dump_json()
        assert "2024-12-31T00:00:00Z" in payload


class TestOrchestrationResult:
    def test_from_attributes_support(self) -> None:
        mapping = SignalOrderMapping(
            symbol="AAPL",
            predicted_return=0.1,
            rank=1,
            target_weight=0.5,
        )

        obj = SimpleNamespace(
            run_id=UUID("12345678-1234-5678-1234-567812345678"),
            status="completed",
            strategy_id="alpha",
            as_of_date="2024-12-31",
            symbols=["AAPL"],
            capital=Decimal("1000"),
            num_signals=1,
            signal_metadata=None,
            num_orders_submitted=1,
            num_orders_accepted=1,
            num_orders_rejected=0,
            num_orders_filled=None,
            mappings=[mapping],
            started_at=datetime(2024, 12, 31, 12, 0, tzinfo=UTC),
            completed_at=None,
            duration_seconds=None,
            error_message=None,
        )

        result = OrchestrationResult.model_validate(obj)

        assert result.status == "completed"
        assert result.mappings[0].symbol == "AAPL"
