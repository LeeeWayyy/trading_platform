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

from dataclasses import dataclass
from typing import Protocol

from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.liquidity_service import LiquidityService
from apps.execution_gateway.order_slicer import TWAPSlicer
from libs.trading.risk_management import RiskConfig


class DatabaseClientProtocol(Protocol):
    """Protocol for database operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete DatabaseClient implementation.
    """

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> list[dict[str, object]]:
        """Execute a query and return results."""
        ...

    def transaction(self) -> object:
        """Start a database transaction context manager."""
        ...


class RedisClientProtocol(Protocol):
    """Protocol for Redis operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete RedisClient implementation.
    """

    def get(self, key: str) -> bytes | None:
        """Get value for key."""
        ...

    def set(self, key: str, value: str | bytes, ex: int | None = None) -> bool:
        """Set key to value with optional expiration."""
        ...


class AlpacaClientProtocol(Protocol):
    """Protocol for Alpaca broker operations.

    This protocol enables dependency injection and mocking in tests
    without depending on the concrete AlpacaExecutor implementation.
    """

    async def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str,
        time_in_force: str,
        client_order_id: str,
        limit_price: float | None = None,
    ) -> dict[str, object]:
        """Submit an order to Alpaca."""
        ...

    async def cancel_order(self, client_order_id: str) -> dict[str, object]:
        """Cancel an order."""
        ...

    async def get_positions(self) -> list[dict[str, object]]:
        """Get all positions."""
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

    async def run_reconciliation_once(self, trigger: str) -> dict[str, object]:
        """Run reconciliation once."""
        ...

    async def run_fills_backfill_once(
        self,
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

    def kill_switch_engaged(self) -> bool:
        """Check if kill switch is engaged."""
        ...

    def circuit_breaker_tripped(self) -> bool:
        """Check if circuit breaker is tripped."""
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
