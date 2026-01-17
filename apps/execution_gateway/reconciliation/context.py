"""Dependency injection context for reconciliation.

This module provides a dataclass for injecting all dependencies needed
by reconciliation operations, enabling easy testing via mock injection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.execution_gateway.alpaca_client import AlpacaExecutor
    from apps.execution_gateway.database import DatabaseClient
    from libs.core.redis_client import RedisClient


@dataclass
class ReconciliationContext:
    """Context containing all dependencies for reconciliation operations.

    This dataclass provides a clean way to inject dependencies into
    reconciliation functions, enabling:
    - Easy mocking in unit tests
    - Deterministic time via now() injection
    - Clear dependency boundaries

    Example:
        >>> ctx = ReconciliationContext(
        ...     db_client=mock_db,
        ...     alpaca_client=mock_alpaca,
        ...     redis_client=mock_redis,
        ...     now=lambda: datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        ... )
        >>> # Now all reconciliation functions use the frozen time
    """

    db_client: DatabaseClient
    """Database client for order/position queries and updates."""

    alpaca_client: AlpacaExecutor
    """Alpaca API client for broker state queries."""

    redis_client: RedisClient | None
    """Redis client for quarantine state. May be None in dry-run mode."""

    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    """Injectable time provider for deterministic testing."""


@dataclass
class ReconciliationConfig:
    """Configuration for reconciliation behavior.

    Extracted from environment variables, this config controls
    timing, limits, and feature flags for reconciliation.
    """

    poll_interval_seconds: int = 300
    """Seconds between periodic reconciliation runs."""

    timeout_seconds: int = 300
    """Startup reconciliation timeout."""

    max_individual_lookups: int = 100
    """Max individual order lookups per reconciliation run."""

    overlap_seconds: int = 60
    """Time overlap for after_time window to catch edge cases."""

    submitted_unconfirmed_grace_seconds: int = 300
    """Grace period before marking submitted_unconfirmed as failed."""

    fills_backfill_enabled: bool = False
    """Whether to enable Alpaca fills backfill in periodic runs."""

    fills_backfill_initial_lookback_hours: int = 24
    """Initial lookback window for fills backfill."""

    fills_backfill_page_size: int = 100
    """Page size for fills API pagination."""

    fills_backfill_max_pages: int = 5
    """Max pages per fills backfill run."""

    dry_run: bool = False
    """If True, skip all reconciliation operations."""

    @classmethod
    def from_env(cls) -> ReconciliationConfig:
        """Create config from environment variables.

        Returns:
            ReconciliationConfig with values from env or defaults.
        """
        import os

        def _parse_bool(val: str) -> bool:
            return val.lower() in {"1", "true", "yes", "on"}

        return cls(
            poll_interval_seconds=int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "300")),
            timeout_seconds=int(os.getenv("RECONCILIATION_TIMEOUT_SECONDS", "300")),
            max_individual_lookups=int(os.getenv("RECONCILIATION_MAX_LOOKUPS", "100")),
            overlap_seconds=int(os.getenv("RECONCILIATION_OVERLAP_SECONDS", "60")),
            submitted_unconfirmed_grace_seconds=int(
                os.getenv("RECONCILIATION_SUBMITTED_UNCONFIRMED_GRACE_SECONDS", "300")
            ),
            fills_backfill_enabled=_parse_bool(
                os.getenv("ALPACA_FILLS_BACKFILL_ENABLED", "false")
            ),
            fills_backfill_initial_lookback_hours=int(
                os.getenv("ALPACA_FILLS_BACKFILL_INITIAL_LOOKBACK_HOURS", "24")
            ),
            fills_backfill_page_size=int(os.getenv("ALPACA_FILLS_BACKFILL_PAGE_SIZE", "100")),
            fills_backfill_max_pages=int(os.getenv("ALPACA_FILLS_BACKFILL_MAX_PAGES", "5")),
            dry_run=_parse_bool(os.getenv("DRY_RUN", "true")),
        )
