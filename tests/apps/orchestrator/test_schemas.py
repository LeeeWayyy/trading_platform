"""Unit tests for orchestrator schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from apps.orchestrator.schemas import (
    ConfigResponse,
    HealthResponse,
    KillSwitchDisengageRequest,
    KillSwitchEngageRequest,
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationRunsResponse,
    OrchestrationRunSummary,
    OrderRequest,
    OrderSubmission,
    Signal,
    SignalMetadata,
    SignalOrderMapping,
    SignalServiceResponse,
)

# ==============================================================================
# Signal Service Models Tests
# ==============================================================================


class TestSignal:
    """Tests for Signal schema."""

    def test_valid_signal_creation(self) -> None:
        """Verify Signal schema accepts valid data."""
        signal = Signal(
            symbol="AAPL",
            predicted_return=0.05,
            rank=1,
            target_weight=0.15,
        )

        assert signal.symbol == "AAPL"
        assert signal.predicted_return == 0.05
        assert signal.rank == 1
        assert signal.target_weight == 0.15

    def test_signal_negative_return(self) -> None:
        """Verify Signal accepts negative predicted returns (short signals)."""
        signal = Signal(
            symbol="TSLA",
            predicted_return=-0.03,
            rank=50,
            target_weight=-0.10,
        )

        assert signal.predicted_return == -0.03
        assert signal.target_weight == -0.10

    def test_signal_missing_required_fields(self) -> None:
        """Verify Signal validation fails when required fields are missing."""
        with pytest.raises(ValidationError) as exc_info:
            Signal(symbol="AAPL", predicted_return=0.05)  # type: ignore[call-arg]

        errors = exc_info.value.errors()
        missing_fields = {err["loc"][0] for err in errors}
        assert "rank" in missing_fields
        assert "target_weight" in missing_fields

    def test_signal_serialization(self) -> None:
        """Verify Signal serializes correctly to dict and JSON."""
        signal = Signal(
            symbol="GOOGL",
            predicted_return=0.08,
            rank=3,
            target_weight=0.20,
        )

        dumped = signal.model_dump()
        assert dumped == {
            "symbol": "GOOGL",
            "predicted_return": 0.08,
            "rank": 3,
            "target_weight": 0.20,
        }

        json_str = signal.model_dump_json()
        assert "GOOGL" in json_str
        assert "0.08" in json_str


class TestSignalMetadata:
    """Tests for SignalMetadata schema."""

    def test_valid_metadata_creation(self) -> None:
        """Verify SignalMetadata accepts valid metadata."""
        metadata = SignalMetadata(
            as_of_date="2025-01-15",
            model_version="v1.2.3",
            strategy="alpha_baseline",
            num_signals=50,
            generated_at="2025-01-15T09:30:00Z",
            top_n=25,
            bottom_n=25,
        )

        assert metadata.as_of_date == "2025-01-15"
        assert metadata.model_version == "v1.2.3"
        assert metadata.strategy == "alpha_baseline"
        assert metadata.num_signals == 50
        assert metadata.top_n == 25
        assert metadata.bottom_n == 25

    def test_metadata_missing_fields(self) -> None:
        """Verify SignalMetadata validation fails when fields are missing."""
        with pytest.raises(ValidationError) as exc_info:
            SignalMetadata(  # type: ignore[call-arg]
                as_of_date="2025-01-15",
                model_version="v1.0.0",
            )

        errors = exc_info.value.errors()
        assert len(errors) >= 4  # Missing strategy, num_signals, generated_at, top_n, bottom_n


class TestSignalServiceResponse:
    """Tests for SignalServiceResponse schema."""

    def test_valid_signal_service_response(self) -> None:
        """Verify SignalServiceResponse accepts valid response data."""
        signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.15),
            Signal(symbol="GOOGL", predicted_return=0.03, rank=2, target_weight=0.10),
        ]
        metadata = SignalMetadata(
            as_of_date="2025-01-15",
            model_version="v1.0.0",
            strategy="alpha_baseline",
            num_signals=2,
            generated_at="2025-01-15T09:30:00Z",
            top_n=1,
            bottom_n=1,
        )

        response = SignalServiceResponse(signals=signals, metadata=metadata)

        assert len(response.signals) == 2
        assert response.signals[0].symbol == "AAPL"
        assert response.metadata.num_signals == 2

    def test_empty_signals_list(self) -> None:
        """Verify SignalServiceResponse accepts empty signals list."""
        metadata = SignalMetadata(
            as_of_date="2025-01-15",
            model_version="v1.0.0",
            strategy="alpha_baseline",
            num_signals=0,
            generated_at="2025-01-15T09:30:00Z",
            top_n=0,
            bottom_n=0,
        )

        response = SignalServiceResponse(signals=[], metadata=metadata)

        assert len(response.signals) == 0
        assert response.metadata.num_signals == 0


# ==============================================================================
# Execution Gateway Models Tests
# ==============================================================================


class TestOrderRequest:
    """Tests for OrderRequest schema."""

    def test_default_time_in_force(self) -> None:
        """Verify OrderRequest uses 'day' as default time_in_force."""
        order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")

        assert order.time_in_force == "day"
        assert order.limit_price is None

    def test_market_order(self) -> None:
        """Verify market order creation without prices."""
        order = OrderRequest(
            symbol="MSFT",
            side="sell",
            qty=50,
            order_type="market",
        )

        assert order.symbol == "MSFT"
        assert order.side == "sell"
        assert order.qty == 50
        assert order.order_type == "market"
        assert order.limit_price is None
        assert order.stop_price is None

    def test_limit_order_with_price(self) -> None:
        """Verify limit order creation with limit_price."""
        order = OrderRequest(
            symbol="TSLA",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("250.50"),
        )

        assert order.order_type == "limit"
        assert order.limit_price == Decimal("250.50")
        assert order.stop_price is None

    def test_stop_order_with_price(self) -> None:
        """Verify stop order creation with stop_price."""
        order = OrderRequest(
            symbol="NVDA",
            side="sell",
            qty=25,
            order_type="stop",
            stop_price=Decimal("500.00"),
        )

        assert order.order_type == "stop"
        assert order.stop_price == Decimal("500.00")
        assert order.limit_price is None

    def test_custom_time_in_force(self) -> None:
        """Verify OrderRequest accepts custom time_in_force values."""
        order = OrderRequest(
            symbol="AAPL",
            side="buy",
            qty=10,
            order_type="limit",
            limit_price=Decimal("150.00"),
            time_in_force="gtc",
        )

        assert order.time_in_force == "gtc"

    def test_missing_required_fields(self) -> None:
        """Verify OrderRequest validation fails without required fields."""
        with pytest.raises(ValidationError):
            OrderRequest(side="buy", qty=10, order_type="market")  # type: ignore[call-arg]


class TestOrderSubmission:
    """Tests for OrderSubmission schema."""

    def test_successful_submission(self) -> None:
        """Verify OrderSubmission for successful order."""
        submission = OrderSubmission(
            client_order_id="CLT123456789",
            status="accepted",
            broker_order_id="BRK987654321",
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            created_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            message="Order accepted",
        )

        assert submission.client_order_id == "CLT123456789"
        assert submission.status == "accepted"
        assert submission.broker_order_id == "BRK987654321"
        assert submission.message == "Order accepted"

    def test_rejected_submission_without_broker_id(self) -> None:
        """Verify OrderSubmission for rejected order (no broker_order_id)."""
        submission = OrderSubmission(
            client_order_id="CLT123456789",
            status="rejected",
            broker_order_id=None,
            symbol="AAPL",
            side="buy",
            qty=100,
            order_type="market",
            created_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            message="Insufficient buying power",
        )

        assert submission.status == "rejected"
        assert submission.broker_order_id is None
        assert "Insufficient buying power" in submission.message

    def test_limit_order_submission(self) -> None:
        """Verify OrderSubmission includes limit_price for limit orders."""
        submission = OrderSubmission(
            client_order_id="CLT111222333",
            status="accepted",
            broker_order_id="BRK444555666",
            symbol="GOOGL",
            side="sell",
            qty=50,
            order_type="limit",
            limit_price=Decimal("2500.75"),
            created_at=datetime(2025, 1, 15, 11, 30, 0, tzinfo=UTC),
            message="Limit order accepted",
        )

        assert submission.limit_price == Decimal("2500.75")


# ==============================================================================
# Orchestration Models Tests
# ==============================================================================


class TestOrchestrationRequest:
    """Tests for OrchestrationRequest schema."""

    def test_symbols_required_min_length(self) -> None:
        """Verify OrchestrationRequest requires at least one symbol."""
        with pytest.raises(ValidationError):
            OrchestrationRequest(symbols=[])

    def test_valid_request_with_defaults(self) -> None:
        """Verify OrchestrationRequest with only required fields."""
        request = OrchestrationRequest(symbols=["AAPL", "GOOGL", "MSFT"])

        assert request.symbols == ["AAPL", "GOOGL", "MSFT"]
        assert request.as_of_date is None
        assert request.capital is None
        assert request.max_position_size is None
        assert request.dry_run is None

    def test_request_with_all_fields(self) -> None:
        """Verify OrchestrationRequest with all optional fields."""
        request = OrchestrationRequest(
            symbols=["AAPL", "TSLA"],
            as_of_date="2025-01-15",
            capital=Decimal("100000.00"),
            max_position_size=Decimal("10000.00"),
            dry_run=True,
        )

        assert request.as_of_date == "2025-01-15"
        assert request.capital == Decimal("100000.00")
        assert request.max_position_size == Decimal("10000.00")
        assert request.dry_run is True

    def test_request_serialization(self) -> None:
        """Verify OrchestrationRequest serializes Decimal fields correctly."""
        request = OrchestrationRequest(
            symbols=["AAPL"],
            capital=Decimal("50000.50"),
        )

        dumped = request.model_dump()
        assert dumped["capital"] == Decimal("50000.50")

        json_str = request.model_dump_json()
        assert "50000.50" in json_str


class TestSignalOrderMapping:
    """Tests for SignalOrderMapping schema."""

    def test_signal_only_mapping(self) -> None:
        """Verify mapping with signal info only (no order created)."""
        mapping = SignalOrderMapping(
            symbol="AAPL",
            predicted_return=0.05,
            rank=1,
            target_weight=0.15,
            skip_reason="Insufficient capital",
        )

        assert mapping.symbol == "AAPL"
        assert mapping.client_order_id is None
        assert mapping.order_qty is None
        assert mapping.skip_reason == "Insufficient capital"

    def test_signal_with_order_mapping(self) -> None:
        """Verify mapping with both signal and order info."""
        mapping = SignalOrderMapping(
            symbol="GOOGL",
            predicted_return=0.03,
            rank=2,
            target_weight=0.10,
            client_order_id="CLT123456789",
            order_qty=50,
            order_side="buy",
            order_status="accepted",
        )

        assert mapping.client_order_id == "CLT123456789"
        assert mapping.order_qty == 50
        assert mapping.order_side == "buy"
        assert mapping.order_status == "accepted"
        assert mapping.skip_reason is None

    def test_filled_order_mapping(self) -> None:
        """Verify mapping with execution info (filled order)."""
        mapping = SignalOrderMapping(
            symbol="MSFT",
            predicted_return=0.04,
            rank=3,
            target_weight=0.12,
            client_order_id="CLT987654321",
            order_qty=100,
            order_side="buy",
            broker_order_id="BRK111222333",
            order_status="filled",
            filled_qty=Decimal("100"),
            filled_avg_price=Decimal("350.25"),
        )

        assert mapping.broker_order_id == "BRK111222333"
        assert mapping.order_status == "filled"
        assert mapping.filled_qty == Decimal("100")
        assert mapping.filled_avg_price == Decimal("350.25")


class TestOrchestrationResult:
    """Tests for OrchestrationResult schema."""

    def test_from_attributes_support(self) -> None:
        """Verify OrchestrationResult can be created from object attributes."""
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

    def test_completed_result(self) -> None:
        """Verify completed OrchestrationResult with all fields."""
        run_id = uuid4()
        started_at = datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC)
        completed_at = datetime(2025, 1, 15, 9, 35, 30, tzinfo=UTC)

        result = OrchestrationResult(
            run_id=run_id,
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            symbols=["AAPL", "GOOGL"],
            capital=Decimal("100000.00"),
            num_signals=2,
            signal_metadata={"model_version": "v1.0.0", "top_n": 1, "bottom_n": 1},
            num_orders_submitted=2,
            num_orders_accepted=2,
            num_orders_rejected=0,
            num_orders_filled=2,
            mappings=[
                SignalOrderMapping(
                    symbol="AAPL",
                    predicted_return=0.05,
                    rank=1,
                    target_weight=0.15,
                    client_order_id="CLT123",
                    order_qty=50,
                    order_side="buy",
                ),
            ],
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=Decimal("330.5"),
            error_message=None,
        )

        assert result.status == "completed"
        assert result.num_orders_submitted == 2
        assert result.num_orders_filled == 2
        assert result.duration_seconds == Decimal("330.5")
        assert result.error_message is None

    def test_failed_result_with_error(self) -> None:
        """Verify failed OrchestrationResult includes error message."""
        result = OrchestrationResult(
            run_id=uuid4(),
            status="failed",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            symbols=["AAPL"],
            capital=Decimal("100000.00"),
            num_signals=0,
            signal_metadata=None,
            num_orders_submitted=0,
            num_orders_accepted=0,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
            completed_at=datetime(2025, 1, 15, 9, 30, 5, tzinfo=UTC),
            duration_seconds=Decimal("5.0"),
            error_message="Signal service unavailable",
        )

        assert result.status == "failed"
        assert result.error_message == "Signal service unavailable"
        assert result.num_signals == 0

    def test_partial_result(self) -> None:
        """Verify partial OrchestrationResult (some orders rejected)."""
        result = OrchestrationResult(
            run_id=uuid4(),
            status="partial",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            symbols=["AAPL", "GOOGL", "MSFT"],
            capital=Decimal("100000.00"),
            num_signals=3,
            signal_metadata={},
            num_orders_submitted=3,
            num_orders_accepted=2,
            num_orders_rejected=1,
            mappings=[],
            started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
        )

        assert result.status == "partial"
        assert result.num_orders_accepted == 2
        assert result.num_orders_rejected == 1


class TestOrchestrationRunSummary:
    """Tests for OrchestrationRunSummary schema."""

    def test_summary_from_attributes(self) -> None:
        """Verify summary can be created from object attributes."""
        obj = SimpleNamespace(
            run_id=UUID("12345678-1234-5678-1234-567812345678"),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            num_signals=10,
            num_orders_submitted=8,
            num_orders_accepted=7,
            num_orders_rejected=1,
            started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
            completed_at=datetime(2025, 1, 15, 9, 35, 0, tzinfo=UTC),
            duration_seconds=Decimal("300.0"),
        )

        summary = OrchestrationRunSummary.model_validate(obj)

        assert summary.run_id == UUID("12345678-1234-5678-1234-567812345678")
        assert summary.status == "completed"
        assert summary.num_signals == 10
        assert summary.duration_seconds == Decimal("300.0")

    def test_running_summary_without_completion(self) -> None:
        """Verify summary for running orchestration (no completed_at)."""
        summary = OrchestrationRunSummary(
            run_id=uuid4(),
            status="running",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            num_signals=5,
            num_orders_submitted=3,
            num_orders_accepted=2,
            num_orders_rejected=0,
            started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
            completed_at=None,
            duration_seconds=None,
        )

        assert summary.status == "running"
        assert summary.completed_at is None
        assert summary.duration_seconds is None


class TestOrchestrationRunsResponse:
    """Tests for OrchestrationRunsResponse schema."""

    def test_empty_runs_list(self) -> None:
        """Verify response with no runs."""
        response = OrchestrationRunsResponse(
            runs=[],
            total=0,
            limit=10,
            offset=0,
        )

        assert len(response.runs) == 0
        assert response.total == 0

    def test_paginated_runs_response(self) -> None:
        """Verify response with pagination parameters."""
        summaries = [
            OrchestrationRunSummary(
                run_id=uuid4(),
                status="completed",
                strategy_id="alpha_baseline",
                as_of_date="2025-01-15",
                num_signals=10,
                num_orders_submitted=8,
                num_orders_accepted=8,
                num_orders_rejected=0,
                started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
                completed_at=datetime(2025, 1, 15, 9, 35, 0, tzinfo=UTC),
                duration_seconds=Decimal("300.0"),
            ),
        ]

        response = OrchestrationRunsResponse(
            runs=summaries,
            total=25,
            limit=10,
            offset=10,
        )

        assert len(response.runs) == 1
        assert response.total == 25
        assert response.limit == 10
        assert response.offset == 10


# ==============================================================================
# Health and Config Models Tests
# ==============================================================================


class TestHealthResponse:
    """Tests for HealthResponse schema."""

    def test_healthy_response(self) -> None:
        """Verify healthy HealthResponse with all services up."""
        response = HealthResponse(
            status="healthy",
            service="orchestrator",
            version="0.1.0",
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            signal_service_url="http://signal-service:8001",
            execution_gateway_url="http://execution-gateway:8002",
            signal_service_healthy=True,
            execution_gateway_healthy=True,
            database_connected=True,
        )

        assert response.status == "healthy"
        assert response.signal_service_healthy is True
        assert response.execution_gateway_healthy is True
        assert response.database_connected is True

    def test_degraded_response_with_details(self) -> None:
        """Verify degraded response includes details about issues."""
        response = HealthResponse(
            status="degraded",
            service="orchestrator",
            version="0.1.0",
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            signal_service_url="http://signal-service:8001",
            execution_gateway_url="http://execution-gateway:8002",
            signal_service_healthy=False,
            execution_gateway_healthy=True,
            database_connected=True,
            details={"signal_service_error": "Connection timeout"},
        )

        assert response.status == "degraded"
        assert response.signal_service_healthy is False
        assert response.details is not None
        assert "signal_service_error" in response.details

    def test_unhealthy_response(self) -> None:
        """Verify unhealthy response when critical services are down."""
        response = HealthResponse(
            status="unhealthy",
            service="orchestrator",
            version="0.1.0",
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            signal_service_url="http://signal-service:8001",
            execution_gateway_url="http://execution-gateway:8002",
            signal_service_healthy=False,
            execution_gateway_healthy=False,
            database_connected=False,
        )

        assert response.status == "unhealthy"
        assert response.database_connected is False


class TestTimestampSerialization:
    """Tests for TimestampSerializerMixin functionality."""

    def test_health_response_serializes_timestamp_with_z(self) -> None:
        """Verify HealthResponse serializes timestamp with Z suffix."""
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
        """Verify ConfigResponse serializes timestamp with Z suffix."""
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


class TestConfigResponse:
    """Tests for ConfigResponse schema."""

    def test_staging_config(self) -> None:
        """Verify staging configuration with safety flags enabled."""
        config = ConfigResponse(
            service="orchestrator",
            version="0.1.0",
            environment="staging",
            dry_run=True,
            alpaca_paper=True,
            circuit_breaker_enabled=True,
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
        )

        assert config.environment == "staging"
        assert config.dry_run is True
        assert config.alpaca_paper is True
        assert config.circuit_breaker_enabled is True

    def test_production_config(self) -> None:
        """Verify production configuration (live trading)."""
        config = ConfigResponse(
            service="orchestrator",
            version="0.2.0",
            environment="production",
            dry_run=False,
            alpaca_paper=False,
            circuit_breaker_enabled=True,
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
        )

        assert config.environment == "production"
        assert config.dry_run is False
        assert config.alpaca_paper is False
        # Circuit breaker should always be enabled
        assert config.circuit_breaker_enabled is True

    def test_config_serialization(self) -> None:
        """Verify ConfigResponse serializes all fields correctly."""
        config = ConfigResponse(
            service="orchestrator",
            version="0.1.0",
            environment="dev",
            dry_run=True,
            alpaca_paper=True,
            circuit_breaker_enabled=False,
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
        )

        dumped = config.model_dump()
        assert dumped["service"] == "orchestrator"
        assert dumped["dry_run"] is True
        assert dumped["circuit_breaker_enabled"] is False


# ==============================================================================
# Kill-Switch Models Tests
# ==============================================================================


class TestKillSwitchEngageRequest:
    """Tests for KillSwitchEngageRequest schema."""

    def test_valid_engage_request(self) -> None:
        """Verify kill-switch engagement with required fields."""
        request = KillSwitchEngageRequest(
            reason="Market anomaly detected",
            operator="ops_team",
        )

        assert request.reason == "Market anomaly detected"
        assert request.operator == "ops_team"
        assert request.details is None

    def test_engage_request_with_details(self) -> None:
        """Verify kill-switch engagement includes optional details."""
        request = KillSwitchEngageRequest(
            reason="Flash crash detected",
            operator="automated_monitor",
            details={
                "anomaly_type": "flash_crash",
                "severity": "high",
                "affected_symbols": ["AAPL", "GOOGL"],
            },
        )

        assert request.details is not None
        assert request.details["anomaly_type"] == "flash_crash"
        assert request.details["severity"] == "high"

    def test_engage_request_missing_required_fields(self) -> None:
        """Verify validation fails without required fields."""
        with pytest.raises(ValidationError) as exc_info:
            KillSwitchEngageRequest(reason="Some reason")  # type: ignore[call-arg]

        errors = exc_info.value.errors()
        missing_fields = {err["loc"][0] for err in errors}
        assert "operator" in missing_fields

    def test_engage_request_json_schema_example(self) -> None:
        """Verify schema example matches expected structure."""
        schema = KillSwitchEngageRequest.model_json_schema()
        examples = schema.get("examples", [])

        assert len(examples) > 0
        example = examples[0]
        assert "reason" in example
        assert "operator" in example
        assert "details" in example


class TestKillSwitchDisengageRequest:
    """Tests for KillSwitchDisengageRequest schema."""

    def test_valid_disengage_request(self) -> None:
        """Verify kill-switch disengagement with required fields."""
        request = KillSwitchDisengageRequest(
            operator="ops_team",
        )

        assert request.operator == "ops_team"
        assert request.notes is None

    def test_disengage_request_with_notes(self) -> None:
        """Verify kill-switch disengagement includes optional notes."""
        request = KillSwitchDisengageRequest(
            operator="ops_team",
            notes="Market conditions normalized, all systems healthy",
        )

        assert request.notes == "Market conditions normalized, all systems healthy"

    def test_disengage_request_missing_operator(self) -> None:
        """Verify validation fails without operator field."""
        with pytest.raises(ValidationError) as exc_info:
            KillSwitchDisengageRequest()  # type: ignore[call-arg]

        errors = exc_info.value.errors()
        missing_fields = {err["loc"][0] for err in errors}
        assert "operator" in missing_fields

    def test_disengage_request_json_schema_example(self) -> None:
        """Verify schema example matches expected structure."""
        schema = KillSwitchDisengageRequest.model_json_schema()
        examples = schema.get("examples", [])

        assert len(examples) > 0
        example = examples[0]
        assert "operator" in example
        assert "notes" in example


# ==============================================================================
# Edge Cases and Validation Tests
# ==============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_capital_request(self) -> None:
        """Verify OrchestrationRequest accepts zero capital."""
        request = OrchestrationRequest(
            symbols=["AAPL"],
            capital=Decimal("0.00"),
        )

        assert request.capital == Decimal("0.00")

    def test_negative_capital_request(self) -> None:
        """Verify OrchestrationRequest accepts negative capital (edge case)."""
        # Note: Business logic should validate this, but schema allows it
        request = OrchestrationRequest(
            symbols=["AAPL"],
            capital=Decimal("-1000.00"),
        )

        assert request.capital == Decimal("-1000.00")

    def test_large_number_of_symbols(self) -> None:
        """Verify OrchestrationRequest handles large symbol lists."""
        symbols = [f"SYM{i}" for i in range(500)]
        request = OrchestrationRequest(symbols=symbols)

        assert len(request.symbols) == 500

    def test_signal_with_zero_weight(self) -> None:
        """Verify Signal accepts zero target weight."""
        signal = Signal(
            symbol="AAPL",
            predicted_return=0.0,
            rank=50,
            target_weight=0.0,
        )

        assert signal.target_weight == 0.0

    def test_orchestration_result_with_zero_duration(self) -> None:
        """Verify OrchestrationResult accepts zero duration."""
        result = OrchestrationResult(
            run_id=uuid4(),
            status="completed",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            symbols=["AAPL"],
            capital=Decimal("100000.00"),
            num_signals=0,
            num_orders_submitted=0,
            num_orders_accepted=0,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
            completed_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
            duration_seconds=Decimal("0.0"),
        )

        assert result.duration_seconds == Decimal("0.0")

    def test_very_long_error_message(self) -> None:
        """Verify OrchestrationResult handles long error messages."""
        long_error = "X" * 10000
        result = OrchestrationResult(
            run_id=uuid4(),
            status="failed",
            strategy_id="alpha_baseline",
            as_of_date="2025-01-15",
            symbols=["AAPL"],
            capital=Decimal("100000.00"),
            num_signals=0,
            num_orders_submitted=0,
            num_orders_accepted=0,
            num_orders_rejected=0,
            mappings=[],
            started_at=datetime(2025, 1, 15, 9, 30, 0, tzinfo=UTC),
            error_message=long_error,
        )

        assert len(result.error_message) == 10000

    def test_special_characters_in_strings(self) -> None:
        """Verify schemas handle special characters in string fields."""
        request = KillSwitchEngageRequest(
            reason="Market 🔥💥 anomaly: 'flash-crash' detected @ 09:30:00",
            operator="ops_team_αβγ",
            details={"symbols": ["ABC&D", "XYZ|123"]},
        )

        assert "🔥💥" in request.reason
        assert "αβγ" in request.operator
        assert request.details is not None
        assert "ABC&D" in request.details["symbols"]
