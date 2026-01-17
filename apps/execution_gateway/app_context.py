"""Application context for dependency injection in Execution Gateway.

This module defines the AppContext dataclass that holds all application dependencies,
enabling clean dependency injection and easy mocking in tests.

Design Rationale:
    - Replaces module-level globals with explicit dependency container
    - Enables easy testing by allowing mock injection
    - Makes dependencies explicit at function/route level
    - Supports clean shutdown by having all resources in one place

Usage:
    # In route handlers
    async def my_route(ctx: AppContext = Depends(get_context)):
        await ctx.db.execute(...)
        ctx.metrics.orders_submitted.inc()

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 0 for design decisions.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.liquidity_service import LiquidityService
from apps.execution_gateway.order_slicer import TWAPSlicer
from libs.trading.risk_management import RiskConfig

if TYPE_CHECKING:
    from datetime import date, datetime
    from decimal import Decimal
    from typing import Literal

    from apps.execution_gateway.schemas import OrderDetail, OrderRequest, Position, SliceDetail
    from libs.trading.risk_management.position_reservation import ReleaseResult, ReservationResult


class DatabaseClientProtocol(Protocol):
    """Protocol for database operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete DatabaseClient implementation.
    """

    def transaction(self) -> AbstractContextManager[Any]:
        """Start a database transaction context manager."""
        ...

    def check_connection(self) -> bool:
        """Check database connectivity."""
        ...

    def create_order(
        self,
        client_order_id: str,
        strategy_id: str,
        order_request: OrderRequest,
        status: str,
        broker_order_id: str | None = None,
        error_message: str | None = None,
    ) -> OrderDetail:
        """Create a new order record."""
        ...

    def create_parent_order(
        self,
        client_order_id: str,
        strategy_id: str,
        order_request: OrderRequest,
        total_slices: int,
        status: str = "pending_new",
        metadata: dict[str, Any] | None = None,
        conn: Any | None = None,
    ) -> OrderDetail:
        """Create a TWAP parent order record."""
        ...

    def create_child_slice(
        self,
        client_order_id: str,
        parent_order_id: str,
        slice_num: int,
        strategy_id: str,
        order_request: OrderRequest,
        scheduled_time: datetime,
        status: str = "pending_new",
        conn: Any | None = None,
    ) -> OrderDetail:
        """Create a TWAP child slice record."""
        ...

    def get_order_by_client_id(self, client_order_id: str) -> OrderDetail | None:
        """Fetch an order by client_order_id."""
        ...

    def get_order_for_update(self, client_order_id: str, conn: Any) -> OrderDetail | None:
        """Fetch order for update within a transaction."""
        ...

    def get_slices_by_parent_id(self, parent_order_id: str) -> list[OrderDetail]:
        """Fetch all child slices for a parent order."""
        ...

    def get_all_positions(self) -> list[Position]:
        """Fetch all positions."""
        ...

    def get_positions_for_strategies(self, strategy_ids: list[str]) -> list[Position]:
        """Fetch positions filtered by strategy IDs."""
        ...

    def get_position_by_symbol(self, symbol: str) -> int:
        """Get current position quantity for a symbol."""
        ...

    def get_position_for_update(self, symbol: str, conn: Any) -> Position | None:
        """Fetch position for update within a transaction."""
        ...

    def get_daily_pnl_history(
        self, start_date: date, end_date: date, strategy_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch daily realized P&L history."""
        ...

    def get_data_availability_date(self) -> date | None:
        """Get earliest date with available P&L data."""
        ...

    def get_all_strategy_ids(self, filter_ids: list[str] | None = None) -> list[str]:
        """Fetch all strategy IDs."""
        ...

    def get_bulk_strategy_status(self, strategy_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch status metadata for multiple strategies."""
        ...

    def get_strategy_status(self, strategy_id: str) -> dict[str, Any] | None:
        """Fetch status metadata for a strategy."""
        ...

    def update_order_status(
        self,
        client_order_id: str,
        status: str,
        broker_order_id: str | None = None,
        filled_qty: Decimal | None = None,
        filled_avg_price: Decimal | None = None,
        error_message: str | None = None,
    ) -> OrderDetail | None:
        """Update order status."""
        ...

    def update_order_status_cas(
        self,
        client_order_id: str,
        status: str,
        broker_updated_at: datetime,
        status_rank: int,
        source_priority: int,
        filled_qty: Decimal | None = None,
        filled_avg_price: Decimal | None = None,
        filled_at: datetime | None = None,
        broker_order_id: str | None = None,
        broker_event_id: str | None = None,
        error_message: str | None = None,
        conn: Any | None = None,
    ) -> OrderDetail | None:
        """Update order status with conflict resolution."""
        ...

    def update_order_status_with_conn(
        self,
        client_order_id: str,
        status: str,
        filled_qty: Decimal | None,
        filled_avg_price: Decimal,
        filled_at: datetime | None,
        conn: Any,
        broker_order_id: str | None = None,
        broker_updated_at: datetime | None = None,
        status_rank: int | None = None,
        source_priority: int | None = None,
        broker_event_id: str | None = None,
    ) -> OrderDetail | None:
        """Update order status using an existing transaction connection."""
        ...

    def update_position_on_fill_with_conn(
        self,
        symbol: str,
        fill_qty: int,
        fill_price: Decimal,
        side: str,
        conn: Any,
    ) -> Position:
        """Update position in a transaction after a fill."""
        ...

    def append_fill_to_order_metadata(
        self, client_order_id: str, fill_data: dict[str, Any], conn: Any
    ) -> OrderDetail | None:
        """Append fill metadata to an order."""
        ...

    def cancel_pending_slices(self, parent_order_id: str) -> int:
        """Cancel pending slice orders for a parent."""
        ...


class RedisClientProtocol(Protocol):
    """Protocol for Redis operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete RedisClient implementation.
    """

    def get(self, key: str) -> str | None:
        """Get value for key."""
        ...

    def set(
        self,
        key: str,
        value: str,
        ttl: int | None = None,
        ex: int | None = None,
    ) -> None:
        """Set key to value with optional expiration."""
        ...

    def mget(self, keys: list[str]) -> list[str | None]:
        """Get multiple values for keys."""
        ...

    def health_check(self) -> bool:
        """Check Redis connectivity."""
        ...

    def pipeline(self) -> Any:
        """Create Redis pipeline."""
        ...

    def sscan_iter(self, key: str) -> Any:
        """Iterate set members without blocking."""
        ...

    def delete(self, *keys: str) -> int:
        """Delete keys."""
        ...


class AlpacaClientProtocol(Protocol):
    """Protocol for Alpaca broker operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete AlpacaExecutor implementation.

    Note: Methods are synchronous to match the actual AlpacaExecutor
    implementation which uses synchronous httpx/alpaca-py calls.
    """

    def submit_order(
        self,
        order: OrderRequest,
        client_order_id: str,
    ) -> dict[str, Any]:
        """Submit an order to Alpaca.

        Args:
            order: OrderRequest with symbol, side, qty, order_type, etc.
            client_order_id: Deterministic client order ID for idempotency

        Returns:
            Order response dict with 'id' (broker_order_id), 'status', etc.
        """
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by broker order_id.

        Args:
            order_id: Alpaca broker order ID (not client_order_id)

        Returns:
            True if cancelled successfully
        """
        ...

    def get_all_positions(self) -> list[dict[str, Any]]:
        """Get all open positions from Alpaca."""
        ...

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        """Get open position for a specific symbol from Alpaca.

        Args:
            symbol: Stock symbol (e.g., "AAPL")

        Returns:
            Position dict with qty, side, etc., or None if flat (no position)
        """
        ...

    def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        """Get order by client_order_id."""
        ...

    def get_account_info(self) -> dict[str, Any] | None:
        """Get Alpaca account info."""
        ...

    def check_connection(self) -> bool:
        """Check if connection to Alpaca is healthy."""
        ...


class ReconciliationServiceProtocol(Protocol):
    """Protocol for reconciliation service operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete ReconciliationService implementation.
    """

    def is_startup_complete(self) -> bool:
        """Check if startup reconciliation is complete."""
        ...

    def startup_elapsed_seconds(self) -> float:
        """Get elapsed time since startup reconciliation began."""
        ...

    def startup_timed_out(self) -> bool:
        """Check if startup reconciliation has timed out."""
        ...

    def override_active(self) -> bool:
        """Check if operator override is active."""
        ...

    def override_context(self) -> dict[str, object]:
        """Get operator override context."""
        ...

    def mark_startup_complete(self, forced: bool, user_id: str | None, reason: str | None) -> None:
        """Mark startup reconciliation as complete (operator override)."""
        ...

    async def run_reconciliation_once(self, trigger: str) -> None:
        """Run reconciliation once."""
        ...

    async def run_fills_backfill_once(
        self,
        *,
        lookback_hours: int | None = None,
        recalc_all_trades: bool = False,
    ) -> dict[str, object]:
        """Run fills backfill once."""
        ...


class RecoveryManagerProtocol(Protocol):
    """Protocol for recovery manager operations (safety components).

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete RecoveryManager implementation.
    """

    @property
    def kill_switch(self) -> KillSwitchProtocol | None:
        """Get the kill switch instance."""
        ...

    @property
    def circuit_breaker(self) -> CircuitBreakerProtocol | None:
        """Get the circuit breaker instance."""
        ...

    @property
    def position_reservation(self) -> PositionReservationProtocol | None:
        """Get the position reservation instance."""
        ...

    @property
    def slice_scheduler(self) -> SliceSchedulerProtocol | None:
        """Get the slice scheduler instance."""
        ...

    def is_kill_switch_unavailable(self) -> bool:
        """Check if kill switch is unavailable (Redis down)."""
        ...

    def is_circuit_breaker_unavailable(self) -> bool:
        """Check if circuit breaker is unavailable (Redis down)."""
        ...

    def is_position_reservation_unavailable(self) -> bool:
        """Check if position reservation is unavailable (Redis down)."""
        ...

    def set_kill_switch_unavailable(self, unavailable: bool) -> None:
        """Set kill switch unavailability flag."""
        ...

    def set_circuit_breaker_unavailable(self, unavailable: bool) -> None:
        """Set circuit breaker unavailability flag."""
        ...

    def set_position_reservation_unavailable(self, unavailable: bool) -> None:
        """Set position reservation unavailability flag."""
        ...

    def needs_recovery(self) -> bool:
        """Check if any safety component needs recovery."""
        ...

    def attempt_recovery(
        self,
        kill_switch_factory: Any | None = None,
        circuit_breaker_factory: Any | None = None,
        position_reservation_factory: Any | None = None,
        slice_scheduler_factory: Any | None = None,
    ) -> dict[str, bool]:
        """Attempt to recover unavailable safety components."""
        ...


class KillSwitchProtocol(Protocol):
    """Protocol for kill switch operations used by routes."""

    def is_engaged(self) -> bool:
        """Return whether trading is halted."""
        ...

    def engage(self, reason: str, operator: str, details: dict[str, Any] | None = None) -> None:
        """Engage the kill switch."""
        ...

    def disengage(self, operator: str, notes: str | None = None) -> None:
        """Disengage the kill switch."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Get kill switch status."""
        ...


class CircuitBreakerProtocol(Protocol):
    """Protocol for circuit breaker operations used by routes."""

    def is_tripped(self) -> bool:
        """Return whether the circuit breaker is tripped."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Get circuit breaker status."""
        ...


class PositionReservationProtocol(Protocol):
    """Protocol for position reservation operations used by routes."""

    def reserve(
        self,
        symbol: str,
        side: str,
        qty: int,
        max_limit: int,
        current_position: int = 0,
    ) -> ReservationResult:
        """Reserve a position delta."""
        ...

    def release(self, symbol: str, token: str) -> ReleaseResult:
        """Release a reservation."""
        ...

    def confirm(self, symbol: str, token: str) -> ReleaseResult:
        """Confirm a reservation."""
        ...


class SliceSchedulerProtocol(Protocol):
    """Protocol for slice scheduler operations used by routes."""

    def schedule_slices(
        self,
        parent_order_id: str,
        slices: list[SliceDetail],
        symbol: str,
        side: Literal["buy", "sell"],
        order_type: Literal["market", "limit", "stop", "stop_limit"],
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        time_in_force: Literal["day", "gtc", "ioc", "fok"],
    ) -> list[str]:
        """Schedule slice execution jobs."""
        ...

    def cancel_remaining_slices(self, parent_order_id: str) -> tuple[int, int]:
        """Cancel remaining slices and return scheduler/db counts."""
        ...


@dataclass
class AppContext:
    """Central context for all application dependencies.

    This dataclass holds all application dependencies, replacing module-level
    globals with explicit dependency injection. It enables:
        - Easy mocking in tests (inject test doubles)
        - Clear dependency requirements at function signatures
        - Clean shutdown (all resources in one place)
        - Type safety via protocols

    Attributes:
        db: Database client for persistent storage
        redis: Redis client for caching and real-time features (optional)
        alpaca: Alpaca broker client for order execution (optional in dry-run)
        liquidity_service: Service for ADV lookups (optional)
        reconciliation_service: Service for broker state sync (optional)
        recovery_manager: Coordinator for safety components (kill-switch, CB)
        risk_config: Risk limits configuration
        fat_finger_validator: Order size validation
        twap_slicer: TWAP order slicing logic (stateless)
        webhook_secret: Secret for webhook signature verification

    Note:
        Optional dependencies (Redis, Alpaca, etc.) can be None to support:
        - Graceful degradation when services are unavailable
        - Dry-run mode without broker connection
        - Testing without external dependencies
    """

    db: DatabaseClientProtocol
    redis: RedisClientProtocol | None
    alpaca: AlpacaClientProtocol | None
    liquidity_service: LiquidityService | None
    reconciliation_service: ReconciliationServiceProtocol | None
    recovery_manager: RecoveryManagerProtocol
    risk_config: RiskConfig
    fat_finger_validator: FatFingerValidator
    twap_slicer: TWAPSlicer
    webhook_secret: str
    # Position tracking state (for Prometheus metrics)
    position_metrics_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tracked_position_symbols: set[str] = field(default_factory=set)
