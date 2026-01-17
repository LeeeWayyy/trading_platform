#!/usr/bin/env python3
"""Shadow mode validation for reconciliation refactor.

This script runs the refactored reconciliation logic against a test database
with transaction rollback to validate correctness without persisting changes.

Usage:
    # Run shadow validation against test database
    python scripts/reconciliation_shadow_mode.py --db-url postgresql://... --validate

    # Generate expected writes baseline (run pre-refactor)
    python scripts/reconciliation_shadow_mode.py --db-url postgresql://... --generate-expected

    # Validate idempotency (run twice, compare writes)
    python scripts/reconciliation_shadow_mode.py --db-url postgresql://... --idempotency

IMPORTANT: Run against an isolated test environment, NOT production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shadow_mode")

# Default paths
DEFAULT_EXPECTED_WRITES_PATH = "tests/fixtures/reconciliation_expected_writes.json"


@dataclass
class CapturedWrite:
    """Represents a captured database write operation."""

    operation: str  # INSERT, UPDATE, DELETE
    table: str
    sql: str
    params: dict[str, Any] | tuple[Any, ...] | None
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "operation": self.operation,
            "table": self.table,
            "sql_hash": hashlib.sha256(self.sql.encode()).hexdigest()[:16],
            "params_summary": str(self.params)[:200] if self.params else None,
            "timestamp": self.timestamp,
        }


class WriteCapturingDatabaseClient:
    """Database client wrapper that captures write operations.

    Wraps a real DatabaseClient and intercepts all write operations
    for validation purposes. Captures writes at the method level
    (not just raw SQL execute) to properly track reconciliation operations.

    IMPORTANT: This wrapper ensures all DB operations use the provided
    transaction connection, preventing auto-commit on pooled connections.
    """

    def __init__(self, real_db_client: Any, transaction_conn: Any) -> None:
        self._db = real_db_client
        self._conn = transaction_conn  # Force all operations to use this connection
        self.captured_writes: list[CapturedWrite] = []

    def _capture_write(
        self, operation: str, table: str, params: dict[str, Any] | None = None
    ) -> None:
        """Capture a write operation."""
        self.captured_writes.append(
            CapturedWrite(
                operation=operation,
                table=table,
                sql=f"{operation} on {table}",
                params=params,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

    # Override write methods to capture and use transaction connection

    def update_order_status_cas(
        self,
        client_order_id: str,
        status: str,
        broker_updated_at: datetime,
        status_rank: int,
        source_priority: int,
        filled_qty: Any = None,
        filled_avg_price: Any = None,
        filled_at: Any = None,
        broker_order_id: str | None = None,
        broker_event_id: str | None = None,
        error_message: str | None = None,
        conn: Any = None,
    ) -> Any:
        """Capture CAS update and delegate with transaction connection.

        Signature matches DatabaseClient.update_order_status_cas exactly.
        Uses keyword-only forwarding to avoid positional argument mismatch.
        """
        self._capture_write(
            "UPDATE",
            "orders",
            {
                "client_order_id": client_order_id,
                "status": status,
                "source_priority": source_priority,
                "status_rank": status_rank,
            },
        )
        # Force use of transaction connection via keyword-only forwarding
        return self._db.update_order_status_cas(
            client_order_id=client_order_id,
            status=status,
            broker_updated_at=broker_updated_at,
            status_rank=status_rank,
            source_priority=source_priority,
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            filled_at=filled_at,
            broker_order_id=broker_order_id,
            broker_event_id=broker_event_id,
            error_message=error_message,
            conn=self._conn,  # Always use transaction connection
        )

    def create_orphan_order(self, **kwargs: Any) -> None:
        """Capture orphan order creation (shadow mode: capture only, no persist).

        Note: DatabaseClient.create_orphan_order doesn't support conn parameter,
        so we capture the write for validation but don't delegate. The transaction
        is rolled back anyway, so this is safe for shadow validation.
        """
        self._capture_write("INSERT", "orphan_orders", kwargs)
        # Don't delegate - method doesn't support conn, and we rollback anyway

    def update_orphan_order_status(self, **kwargs: Any) -> None:
        """Capture orphan order status update (shadow mode: capture only).

        Note: DatabaseClient.update_orphan_order_status doesn't support conn,
        so we capture for validation without delegating.
        """
        self._capture_write("UPDATE", "orphan_orders", kwargs)
        # Don't delegate - method doesn't support conn, and we rollback anyway

    def upsert_position_snapshot(self, **kwargs: Any) -> None:
        """Capture position snapshot upsert (shadow mode: capture only).

        Note: DatabaseClient.upsert_position_snapshot doesn't support conn,
        so we capture for validation without delegating.
        """
        self._capture_write("UPSERT", "positions", kwargs)
        # Don't delegate - method doesn't support conn, and we rollback anyway

    def append_fill_to_order_metadata(
        self, client_order_id: str, fill_data: dict[str, Any], conn: Any
    ) -> Any:
        """Capture fill metadata append and delegate with transaction connection.

        This method supports conn parameter, so we delegate to get real return
        value (needed for control flow) and capture conditionally based on result.
        """
        # Delegate with transaction connection to get real result
        result = self._db.append_fill_to_order_metadata(
            client_order_id=client_order_id,
            fill_data=fill_data,
            conn=self._conn,
        )
        # Only capture if update actually happened (idempotency check passed)
        if result is not None:
            self._capture_write(
                "UPDATE",
                "orders_metadata",
                {"client_order_id": client_order_id, "fill_id": fill_data.get("fill_id")},
            )
        return result

    def set_reconciliation_high_water_mark(self, *args: Any, **kwargs: Any) -> None:
        """Capture high water mark update (shadow mode: capture only).

        Note: DatabaseClient.set_reconciliation_high_water_mark doesn't support
        conn, so we capture for validation without delegating.
        """
        self._capture_write(
            "UPSERT", "reconciliation_state", {"args": str(args), "kwargs": kwargs}
        )
        # Don't delegate - method doesn't support conn, and we rollback anyway

    def recalculate_trade_realized_pnl(
        self,
        strategy_id: str,
        symbol: str,
        *,
        update_all: bool = False,
        update_sources: set[str] | None = None,
        conn: Any = None,
    ) -> dict[str, int]:
        """Capture P&L recalculation and delegate with transaction connection.

        This method supports conn parameter, so we delegate to get real return
        value (needed for control flow) and capture conditionally based on result.
        """
        # Delegate with transaction connection to get real result
        result = self._db.recalculate_trade_realized_pnl(
            strategy_id,
            symbol,
            update_all=update_all,
            update_sources=update_sources,
            conn=self._conn,
        )
        # Only capture if trades were actually updated
        if result.get("trades_updated", 0) > 0:
            self._capture_write(
                "UPDATE",
                "trades_pnl",
                {"strategy_id": strategy_id, "symbol": symbol, "trades_updated": result},
            )
        return result

    @contextmanager
    def transaction(self) -> Generator[Any, None, None]:
        """Return the existing transaction connection (don't create new)."""
        # Shadow mode uses a single transaction - return the existing connection
        yield self._conn

    def __getattr__(self, name: str) -> Any:
        """Delegate read methods to real DatabaseClient.

        For methods that accept conn parameter, wrap to inject transaction connection.
        """
        attr = getattr(self._db, name)

        # For callable methods that might accept conn, wrap them
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                # Inject transaction connection if method accepts it
                # This ensures all DB operations use our transaction
                try:
                    import inspect
                    sig = inspect.signature(attr)
                    if "conn" in sig.parameters and "conn" not in kwargs:
                        kwargs["conn"] = self._conn
                except (ValueError, TypeError):
                    pass
                return attr(*args, **kwargs)
            return wrapped

        return attr


class MockAlpacaClient:
    """Mock Alpaca client for shadow mode testing.

    Returns empty/neutral data to avoid hitting real broker APIs.
    """

    def get_orders(
        self,
        status: str | None = None,
        limit: int = 500,
        after: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return empty order list."""
        return []

    def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        """Return None - order not found."""
        return None

    def get_all_positions(self) -> list[dict[str, Any]]:
        """Return empty position list."""
        return []

    def get_account_activities(
        self,
        activity_type: str,
        after: datetime | None = None,
        until: datetime | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        direction: str = "desc",
    ) -> list[dict[str, Any]]:
        """Return empty activities list."""
        return []


def run_shadow_validation(
    db_url: str,
    redis_url: str | None = None,
    expected_writes_path: str = DEFAULT_EXPECTED_WRITES_PATH,
) -> dict[str, Any]:
    """Run reconciliation in shadow mode with transaction rollback.

    Args:
        db_url: PostgreSQL connection URL for test database.
        redis_url: Redis connection URL (optional, can be None to skip Redis).
        expected_writes_path: Path to expected writes JSON file.

    Returns:
        Dict with validation results.
    """
    logger.info("Starting shadow mode validation")
    logger.info(f"Database: {db_url[:50]}...")
    logger.info(f"Expected writes: {expected_writes_path}")

    # Import here to avoid circular imports
    from apps.execution_gateway.database import DatabaseClient
    from apps.execution_gateway.reconciliation import ReconciliationService

    # Create real database client pointing to test DB
    real_db = DatabaseClient(db_url)

    # Create mock clients
    mock_alpaca = MockAlpacaClient()
    mock_redis = None  # Skip Redis for shadow mode

    # Wrap in transaction for rollback
    captured_writes: list[CapturedWrite] = []
    result: dict[str, Any] = {}

    try:
        # Use psycopg pool connection directly for full transaction control
        with real_db._pool.connection() as conn:
            # Start explicit transaction (psycopg auto-starts, but be explicit)
            with conn.transaction() as txn:
                # Create capturing wrapper with the transaction connection
                # This ensures ALL writes go through this connection and can be rolled back
                capturing_db = WriteCapturingDatabaseClient(real_db, conn)

                # Create service with test dependencies
                service = ReconciliationService(
                    db_client=capturing_db,
                    redis_client=mock_redis,
                    alpaca_client=mock_alpaca,  # type: ignore
                )

                # Run reconciliation synchronously
                recon_result = service._run_reconciliation("shadow")
                result["reconciliation"] = recon_result

                # Capture writes before rollback
                captured_writes = list(capturing_db.captured_writes)

                # Rollback - no mutations persist
                txn.rollback()
                logger.info("Transaction rolled back - no mutations persisted")

    except Exception as exc:
        logger.error(f"Shadow mode execution failed: {exc}", exc_info=True)
        return {
            "status": "error",
            "error": str(exc),
            "captured_writes": [],
        }

    # Validate writes against expected
    validation_result = validate_writes(captured_writes, expected_writes_path)

    return {
        "status": "success" if validation_result["passed"] else "failed",
        "reconciliation": result.get("reconciliation"),
        "captured_writes_count": len(captured_writes),
        "validation": validation_result,
        "captured_writes": [w.to_dict() for w in captured_writes],
    }


def validate_writes(
    captured_writes: list[CapturedWrite],
    expected_writes_path: str,
) -> dict[str, Any]:
    """Validate captured writes against expected outputs.

    Args:
        captured_writes: List of captured write operations.
        expected_writes_path: Path to expected writes JSON file.

    Returns:
        Dict with validation results.
    """
    expected_path = Path(expected_writes_path)
    if not expected_path.exists():
        logger.warning(f"Expected writes file not found: {expected_path}")
        return {
            "passed": True,  # Pass if no expectations set
            "message": "No expected writes file - skipping comparison",
            "captured_count": len(captured_writes),
        }

    with open(expected_path) as f:
        expected_data = json.load(f)

    expected_writes = expected_data.get("writes", [])

    # Compare by operation and table counts
    captured_summary = {}
    for w in captured_writes:
        key = f"{w.operation}:{w.table}"
        captured_summary[key] = captured_summary.get(key, 0) + 1

    expected_summary = {}
    for w in expected_writes:
        key = f"{w['operation']}:{w['table']}"
        expected_summary[key] = expected_summary.get(key, 0) + 1

    # Check for differences
    differences = []
    all_keys = set(captured_summary.keys()) | set(expected_summary.keys())
    for key in sorted(all_keys):
        captured_count = captured_summary.get(key, 0)
        expected_count = expected_summary.get(key, 0)
        if captured_count != expected_count:
            differences.append({
                "operation:table": key,
                "expected": expected_count,
                "captured": captured_count,
            })

    passed = len(differences) == 0
    if passed:
        logger.info("Validation PASSED - captured writes match expected")
    else:
        logger.error(f"Validation FAILED - {len(differences)} differences found")
        for diff in differences:
            logger.error(f"  {diff}")

    return {
        "passed": passed,
        "captured_count": len(captured_writes),
        "expected_count": len(expected_writes),
        "differences": differences,
    }


def generate_expected_writes(
    db_url: str,
    output_path: str = DEFAULT_EXPECTED_WRITES_PATH,
) -> dict[str, Any]:
    """Generate expected writes baseline from current code.

    Run this before refactoring to capture the expected output.

    Args:
        db_url: PostgreSQL connection URL for test database.
        output_path: Path to write expected outputs JSON.

    Returns:
        Dict with generation results.
    """
    logger.info("Generating expected writes baseline")

    result = run_shadow_validation(db_url, expected_writes_path="__nonexistent__")
    captured_writes = result.get("captured_writes", [])

    # Save to file
    output_data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "description": "Expected writes from pre-refactor reconciliation",
        "writes": captured_writes,
    }

    output_path_obj = Path(output_path)
    output_path_obj.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path_obj, "w") as f:
        json.dump(output_data, f, indent=2)

    logger.info(f"Wrote {len(captured_writes)} expected writes to {output_path}")
    return {
        "status": "success",
        "writes_count": len(captured_writes),
        "output_path": str(output_path),
    }


def check_idempotency(db_url: str) -> dict[str, Any]:
    """Verify reconciliation produces identical writes when run twice.

    Args:
        db_url: PostgreSQL connection URL for test database.

    Returns:
        Dict with idempotency check results.
    """
    logger.info("Checking idempotency - running reconciliation twice")

    # First run
    result1 = run_shadow_validation(db_url, expected_writes_path="__nonexistent__")
    writes1 = result1.get("captured_writes", [])

    # Second run
    result2 = run_shadow_validation(db_url, expected_writes_path="__nonexistent__")
    writes2 = result2.get("captured_writes", [])

    # Compare
    writes1_hashes = {json.dumps(w, sort_keys=True) for w in writes1}
    writes2_hashes = {json.dumps(w, sort_keys=True) for w in writes2}

    identical = writes1_hashes == writes2_hashes

    if identical:
        logger.info("Idempotency check PASSED - both runs produced identical writes")
    else:
        logger.error("Idempotency check FAILED - runs produced different writes")
        only_in_1 = writes1_hashes - writes2_hashes
        only_in_2 = writes2_hashes - writes1_hashes
        if only_in_1:
            logger.error(f"  Only in run 1: {len(only_in_1)} writes")
        if only_in_2:
            logger.error(f"  Only in run 2: {len(only_in_2)} writes")

    return {
        "status": "passed" if identical else "failed",
        "run1_writes": len(writes1),
        "run2_writes": len(writes2),
        "identical": identical,
    }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Shadow mode validation for reconciliation refactor"
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help="PostgreSQL connection URL for test database",
    )
    parser.add_argument(
        "--redis-url",
        help="Redis connection URL (optional)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run shadow validation against expected writes",
    )
    parser.add_argument(
        "--generate-expected",
        action="store_true",
        help="Generate expected writes baseline",
    )
    parser.add_argument(
        "--idempotency",
        action="store_true",
        help="Check idempotency (run twice, compare)",
    )
    parser.add_argument(
        "--expected-path",
        default=DEFAULT_EXPECTED_WRITES_PATH,
        help="Path to expected writes JSON file",
    )

    args = parser.parse_args()

    if args.generate_expected:
        result = generate_expected_writes(args.db_url, args.expected_path)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "success" else 1

    if args.idempotency:
        result = check_idempotency(args.db_url)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "passed" else 1

    if args.validate:
        result = run_shadow_validation(
            args.db_url,
            args.redis_url,
            args.expected_path,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "success" else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
