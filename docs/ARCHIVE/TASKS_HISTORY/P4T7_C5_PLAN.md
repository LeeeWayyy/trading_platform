# P4T7 C5: Tax Lot Reporter - Core - Component Plan

**Component:** C5 - T9.5 Tax Lot Reporter - Core
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING
**Estimated Effort:** 2-3 days
**Dependencies:** C0 (Prep & Validation)

---

## Overview

Implement T9.5 Tax Lot Reporter - Core that provides cost basis tracking, realized gains/losses reporting, and tax export functionality.

## Acceptance Criteria (from P4T7_TASK.md)

- [ ] Cost basis tracking with FIFO, LIFO, and Specific ID methods
- [ ] Method selection per account or global default
- [ ] Realized gains/losses report by tax year
- [ ] Short-term vs long-term capital gains classification (1-year holding period)
- [ ] Year-end tax summary with totals by category
- [ ] Export for tax software (TurboTax TXF format, CSV, PDF)
- [ ] Position lot viewer: see individual lots with purchase date, cost, current value
- [ ] RBAC: VIEW_TAX_REPORTS permission required
- [ ] Audit trail for cost basis method changes

---

## Architecture

### Data Model

```
┌─────────────────────────────────────────────────────────────────┐
│                        Tax Lot Tracking                          │
│                                                                  │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │    tax_lots     │───▶│ tax_lot_        │                     │
│  │                 │    │ dispositions    │                     │
│  │ - id            │    │                 │                     │
│  │ - user_id       │    │ - lot_id        │                     │
│  │ - symbol        │    │ - quantity      │                     │
│  │ - quantity      │    │ - proceeds      │                     │
│  │ - cost_per_share│    │ - disposed_at   │                     │
│  │ - acquired_at   │    │ - realized_gain │                     │
│  │ - remaining_qty │    │ - holding_period│                     │
│  └─────────────────┘    └─────────────────┘                     │
│                                                                  │
│  ┌─────────────────┐                                            │
│  │  tax_settings   │                                            │
│  │                 │                                            │
│  │ - user_id       │                                            │
│  │ - cost_basis_   │                                            │
│  │   method        │                                            │
│  └─────────────────┘                                            │
└─────────────────────────────────────────────────────────────────┘
```

### File Structure

```
apps/web_console/
├── pages/
│   └── tax_lots.py              # Tax lot viewer page
├── components/
│   ├── lot_table.py             # Position lots table
│   ├── gains_report.py          # Gains/losses report
│   └── tax_export.py            # Export controls

libs/tax/
├── __init__.py
├── cost_basis.py                # Cost basis calculator
├── models.py                    # Pydantic models
├── export.py                    # Export formatters (TXF, CSV, PDF)

db/migrations/
└── 0019_create_tax_lots.sql

tests/libs/tax/
├── test_cost_basis.py
├── test_export.py
└── fixtures/
    └── sample_trades.json       # Test data

docs/CONCEPTS/
└── tax-lot-accounting.md
```

---

## Implementation Details

### 1. Database Schema

```sql
-- db/migrations/0019_create_tax_lots.sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Tax lots (positions acquired)
-- NOTE: source_order_id is TEXT to match existing client_order_id/broker_order_id columns
-- NOTE: user_id VARCHAR matches existing RBAC tables (0006_create_rbac_tables.sql)
CREATE TABLE IF NOT EXISTS tax_lots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,  -- Matches RBAC user_id type
    symbol VARCHAR(20) NOT NULL,
    quantity DECIMAL(18, 8) NOT NULL,
    cost_per_share DECIMAL(18, 8) NOT NULL,
    total_cost DECIMAL(18, 4) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    acquisition_type VARCHAR(20) NOT NULL,  -- buy, transfer_in, dividend_reinvest
    source_order_id TEXT,  -- TEXT to match existing order ID types
    remaining_quantity DECIMAL(18, 8) NOT NULL,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tax lot dispositions (sales from lots)
-- NOTE: destination_order_id is TEXT to match existing order ID types
-- NOTE: wash_sale_disallowed is total for this disposition; per-lot detail in junction table
CREATE TABLE IF NOT EXISTS tax_lot_dispositions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lot_id UUID NOT NULL REFERENCES tax_lots(id),
    quantity DECIMAL(18, 8) NOT NULL,
    cost_basis DECIMAL(18, 4) NOT NULL,  -- Total cost basis for disposed shares
    proceeds_per_share DECIMAL(18, 8) NOT NULL,
    total_proceeds DECIMAL(18, 4) NOT NULL,
    disposed_at TIMESTAMPTZ NOT NULL,
    disposition_type VARCHAR(20) NOT NULL,  -- sell, transfer_out
    destination_order_id TEXT,  -- TEXT to match existing order ID types (legacy/reference)
    idempotency_key TEXT NOT NULL,  -- REQUIRED: prevents double-decrement on retry
    realized_gain_loss DECIMAL(18, 4) NOT NULL,
    holding_period VARCHAR(10) NOT NULL,  -- short_term, long_term
    wash_sale_disallowed DECIMAL(18, 4) DEFAULT 0,  -- Total disallowed for this disposition
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_disposition_idempotency UNIQUE (idempotency_key)
);

-- Idempotency key format:
-- For order-driven: "order:{destination_order_id}"
-- For manual/transfer: client-provided key (e.g., "manual:{user_id}:{timestamp}")

-- Wash sale adjustments junction table
-- Tracks per-replacement-lot attribution for multi-lot wash sales
-- Required for accurate IRS Form 8949 detail
CREATE TABLE IF NOT EXISTS tax_wash_sale_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    disposition_id UUID NOT NULL REFERENCES tax_lot_dispositions(id),
    replacement_lot_id UUID NOT NULL REFERENCES tax_lots(id),
    matching_shares DECIMAL(18, 8) NOT NULL,  -- Shares matched to this replacement lot
    disallowed_loss DECIMAL(18, 4) NOT NULL,  -- Loss disallowed for these shares
    -- Holding period adjustment for wash sales (IRS rule: extend by original holding period)
    holding_period_adjustment_days INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(disposition_id, replacement_lot_id)  -- One adjustment per disposition-lot pair
);

CREATE INDEX idx_wash_sale_adjustments_disposition
    ON tax_wash_sale_adjustments(disposition_id);
CREATE INDEX idx_wash_sale_adjustments_replacement
    ON tax_wash_sale_adjustments(replacement_lot_id);

-- Tax settings per user
CREATE TABLE IF NOT EXISTS tax_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL UNIQUE,  -- Matches RBAC user_id type
    cost_basis_method VARCHAR(20) NOT NULL DEFAULT 'fifo',
    wash_sale_tracking BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Audit log for method changes
CREATE TABLE IF NOT EXISTS tax_settings_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,  -- Matches RBAC user_id type
    old_method VARCHAR(20),
    new_method VARCHAR(20) NOT NULL,
    changed_by VARCHAR(255) NOT NULL,  -- Matches RBAC user_id type
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    reason TEXT
);

-- Indexes for performance
CREATE INDEX idx_tax_lots_user_symbol ON tax_lots(user_id, symbol);
CREATE INDEX idx_tax_lots_acquired ON tax_lots(acquired_at);
CREATE INDEX idx_tax_lots_remaining ON tax_lots(user_id, symbol, remaining_quantity) WHERE remaining_quantity > 0;
CREATE INDEX idx_tax_lot_dispositions_disposed ON tax_lot_dispositions(disposed_at);
CREATE INDEX idx_tax_lot_dispositions_tax_year ON tax_lot_dispositions(date_trunc('year', disposed_at));

-- Partial unique indexes for idempotency (Postgres doesn't allow WHERE in inline UNIQUE)
-- These prevent duplicate lots/dispositions from webhook retries
CREATE UNIQUE INDEX idx_tax_lots_source_order_id
    ON tax_lots(user_id, source_order_id)
    WHERE source_order_id IS NOT NULL;

CREATE UNIQUE INDEX idx_tax_lot_dispositions_dest_order_id
    ON tax_lot_dispositions(lot_id, destination_order_id)
    WHERE destination_order_id IS NOT NULL;
```

### 2. Cost Basis Calculator

```python
# libs/tax/cost_basis.py
"""Cost basis calculation with FIFO, LIFO, and Specific ID methods."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class AsyncConnection(Protocol):
    """Protocol for async database connection (psycopg-compatible)."""

    def cursor(self) -> Any:
        """Return cursor context manager."""
        ...


class AsyncConnectionPool(Protocol):
    """Protocol for async connection pool (psycopg-compatible).

    This protocol decouples libs/tax from apps.web_console.utils.db_pool,
    allowing libs to define the interface it needs while apps provide
    the implementation.

    Compatible with: apps.web_console.utils.db_pool.AsyncConnectionAdapter
    """

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Return async context manager for connection."""
        ...

# Long-term holding period (IRS: more than 1 year = more than 365 days)
# Per IRS rules, holding period starts day AFTER acquisition
# Long-term = held MORE than one year (> 365 days, so >= 366 days from acquisition)
LONG_TERM_THRESHOLD_DAYS = 365  # If held > 365 days, it's long-term


class CostBasisMethod(str, Enum):
    FIFO = "fifo"  # First In, First Out
    LIFO = "lifo"  # Last In, First Out
    SPECIFIC_ID = "specific_id"  # User-specified lots


@dataclass
class TaxLot:
    """A tax lot representing shares acquired at a specific time and price."""
    id: UUID
    user_id: str  # VARCHAR(255) to match RBAC user_id type
    symbol: str
    quantity: Decimal
    cost_per_share: Decimal
    total_cost: Decimal
    acquired_at: datetime
    acquisition_type: str
    remaining_quantity: Decimal


@dataclass
class Disposition:
    """Result of disposing shares from tax lots."""
    lot_id: UUID
    quantity: Decimal
    cost_basis: Decimal
    proceeds: Decimal
    realized_gain_loss: Decimal
    holding_period: str  # "short_term" or "long_term"
    disposed_at: datetime


@dataclass
class GainsSummary:
    """Summary of gains/losses for a period."""
    short_term_gains: Decimal
    short_term_losses: Decimal
    long_term_gains: Decimal
    long_term_losses: Decimal

    @property
    def net_short_term(self) -> Decimal:
        return self.short_term_gains + self.short_term_losses

    @property
    def net_long_term(self) -> Decimal:
        return self.long_term_gains + self.long_term_losses

    @property
    def total_net(self) -> Decimal:
        return self.net_short_term + self.net_long_term


class CostBasisCalculator:
    """Calculates cost basis and tracks tax lots.

    Uses AsyncConnectionPool protocol for database access, decoupling from
    specific implementations (e.g., apps.web_console.utils.db_pool).

    IMPORTANT: This class assumes dict-style row access (row["column"]).
    The database adapter MUST use row_factory=dict_row or equivalent.
    AsyncConnectionAdapter from apps.web_console.utils.db_pool is configured
    correctly by default.
    """

    def __init__(self, db_adapter: AsyncConnectionPool):
        """Initialize with database adapter.

        Args:
            db_adapter: Any object implementing AsyncConnectionPool protocol.
                        Compatible with apps.web_console.utils.db_pool.AsyncConnectionAdapter.

        IMPORTANT: The adapter MUST use row_factory=dict_row (or equivalent)
        so that row["column"] access works. The default AsyncConnectionAdapter
        is configured correctly. If using a custom adapter, ensure dict_row.
        """
        self._db = db_adapter

    @staticmethod
    def _ensure_dict(row) -> dict:
        """Ensure row is a dict for dict-style access.

        Handles both dict_row (already dict) and tuple rows.
        For tuple rows, this requires column name knowledge - callers
        should prefer using row_factory=dict_row instead.
        """
        if isinstance(row, dict):
            return row
        # If we get here with a non-dict row, it means row_factory wasn't set
        raise TypeError(
            "Row is not a dict - ensure db_adapter uses row_factory=dict_row. "
            "See apps.web_console.utils.db_pool.AsyncConnectionAdapter for example."
        )

    async def get_user_method(self, user_id: str) -> CostBasisMethod:
        """Get cost basis method for user.

        Uses psycopg cursor pattern with %s placeholders.
        user_id is VARCHAR(255) to match RBAC tables.
        """
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT cost_basis_method FROM tax_settings WHERE user_id = %s",
                    (user_id,),
                )
                row = await cur.fetchone()
                if row:
                    # psycopg with dict_row returns dicts
                    return CostBasisMethod(row["cost_basis_method"])
                return CostBasisMethod.FIFO

    async def set_user_method(
        self,
        user_id: str,
        method: CostBasisMethod,
        changed_by: str,
        reason: str | None = None,
    ) -> None:
        """Set cost basis method for account with audit.

        Uses psycopg cursor pattern with %s placeholders.
        """
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                # Get old method
                await cur.execute(
                    "SELECT cost_basis_method FROM tax_settings WHERE user_id = %s",
                    (user_id,),
                )
                old_row = await cur.fetchone()
                # psycopg with dict_row returns dicts
                old_method = old_row["cost_basis_method"] if old_row else None

                # Upsert settings
                await cur.execute(
                    """
                    INSERT INTO tax_settings (user_id, cost_basis_method, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        cost_basis_method = EXCLUDED.cost_basis_method,
                        updated_at = NOW()
                    """,
                    (user_id, method.value),
                )

                # Audit log
                await cur.execute(
                    """
                    INSERT INTO tax_settings_audit
                    (user_id, old_method, new_method, changed_by, reason)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (user_id, old_method, method.value, changed_by, reason),
                )
            # psycopg3 requires explicit commit (autocommit=False by default)
            await conn.commit()

        logger.info(
            "Cost basis method changed",
            extra={
                "user_id": user_id,
                "old_method": old_method,
                "new_method": method.value,
                "changed_by": changed_by,
            },
        )

    async def record_acquisition(
        self,
        user_id: str,
        symbol: str,
        quantity: Decimal,
        cost_per_share: Decimal,
        acquired_at: datetime,
        acquisition_type: str = "buy",
        source_order_id: str | None = None,  # TEXT to match existing order ID types
    ) -> TaxLot:
        """Record a new tax lot from share acquisition.

        Uses psycopg cursor pattern with %s placeholders.
        Idempotent: ON CONFLICT handles duplicate source_order_id.
        """
        total_cost = quantity * cost_per_share

        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO tax_lots
                    (user_id, symbol, quantity, cost_per_share, total_cost,
                     acquired_at, acquisition_type, source_order_id, remaining_quantity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, source_order_id)
                    WHERE source_order_id IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    (user_id, symbol, quantity, cost_per_share, total_cost,
                     acquired_at, acquisition_type, source_order_id, quantity),
                )
                row = await cur.fetchone()

                # If conflict (duplicate order), fetch existing lot
                if row is None and source_order_id:
                    await cur.execute(
                        "SELECT id FROM tax_lots WHERE user_id = %s AND source_order_id = %s",
                        (user_id, source_order_id),
                    )
                    row = await cur.fetchone()

                lot_id = row["id"]  # psycopg with dict_row returns dicts

            # psycopg3 requires explicit commit (autocommit=False by default)
            await conn.commit()

            return TaxLot(
                id=lot_id,
                user_id=user_id,
                symbol=symbol,
                quantity=quantity,
                cost_per_share=cost_per_share,
                total_cost=total_cost,
                acquired_at=acquired_at,
                acquisition_type=acquisition_type,
                remaining_quantity=quantity,
            )

    async def record_disposition(
        self,
        user_id: str,
        symbol: str,
        quantity: Decimal,
        proceeds_per_share: Decimal,
        disposed_at: datetime,
        disposition_type: str = "sell",
        destination_order_id: str | None = None,  # TEXT to match existing order ID types
        idempotency_key: str | None = None,  # REQUIRED for non-order dispositions
        specific_lot_ids: list[UUID] | None = None,
    ) -> list[Disposition]:
        """Record share disposition and calculate gains/losses.

        Uses psycopg cursor pattern with %s placeholders.

        IDEMPOTENCY: Every disposition requires an idempotency key to prevent
        double-decrement on retry. For order-driven sells, use destination_order_id.
        For manual/transfer dispositions, caller must provide idempotency_key.

        Args:
            user_id: User selling shares (VARCHAR to match RBAC)
            symbol: Symbol being sold
            quantity: Number of shares to sell
            proceeds_per_share: Price per share received
            disposed_at: DateTime of sale
            disposition_type: Type of disposition (sell, transfer_out)
            destination_order_id: Order ID for order-driven sells (webhook retries)
            idempotency_key: Required if destination_order_id is None.
                            Client-provided unique key for manual/transfer dispositions.
            specific_lot_ids: For SPECIFIC_ID method, the lots to use

        Raises:
            ValueError: If neither destination_order_id nor idempotency_key provided

        Returns:
            List of Dispositions showing which lots were used
        """
        # IDEMPOTENCY: Require a key for ALL dispositions to prevent double-decrement
        # Either destination_order_id (for broker sells) or explicit idempotency_key
        if destination_order_id:
            # Order-driven: prefix with "order:" for namespace separation
            effective_idem_key = f"order:{destination_order_id}"
        elif idempotency_key:
            # Manual/transfer: use client-provided key directly
            effective_idem_key = idempotency_key
        else:
            raise ValueError(
                "Idempotency key required: provide destination_order_id for order-driven "
                "dispositions or idempotency_key for manual/transfer dispositions"
            )

        method = await self.get_user_method(user_id)
        total_proceeds = quantity * proceeds_per_share

        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                # IDEMPOTENCY CHECK: Return existing dispositions if already processed
                # This ensures retries return the same allocation as the original
                # Uses idempotency_key column with unique constraint
                await cur.execute(
                    """
                    SELECT d.id, d.lot_id, d.quantity, d.cost_basis,
                           d.proceeds_per_share, d.total_proceeds, d.disposed_at,
                           d.disposition_type, d.realized_gain_loss, d.holding_period
                    FROM tax_lot_dispositions d
                    JOIN tax_lots t ON t.id = d.lot_id
                    WHERE d.idempotency_key = %s
                      AND t.user_id = %s
                      AND t.symbol = %s
                    ORDER BY d.created_at
                    """,
                    (effective_idem_key, user_id, symbol),
                )
                existing = await cur.fetchall()

                if existing:
                    # Already processed - return stored allocations
                    logger.info(
                        "Idempotent disposition return",
                        extra={
                            "idempotency_key": effective_idem_key,
                            "existing_count": len(existing),
                        },
                    )
                    # Reconstruct Disposition from stored data using actual stored values
                    return [
                        Disposition(
                            lot_id=row["lot_id"],
                            quantity=row["quantity"],
                            cost_basis=row["cost_basis"],  # Use stored cost_basis
                            proceeds=row["total_proceeds"],
                            realized_gain_loss=row["realized_gain_loss"],
                            holding_period=row["holding_period"],
                            disposed_at=row["disposed_at"],
                        )
                        for row in existing
                    ]

                # Get available lots (with user/symbol scoping for security)
                # VALIDATION: Specific ID method requires lot IDs - never fall back silently
                if method == CostBasisMethod.SPECIFIC_ID:
                    if not specific_lot_ids:
                        raise ValueError(
                            "SPECIFIC_ID method requires specific_lot_ids parameter. "
                            "Cannot fall back to FIFO/LIFO as this would dispose wrong lots."
                        )
                    lots = await self._get_specific_lots(cur, specific_lot_ids, user_id, symbol)
                else:
                    lots = await self._get_available_lots(cur, user_id, symbol, method)

                # Allocate shares to lots
                dispositions = []
                remaining_to_sell = quantity

                for lot in lots:
                    if remaining_to_sell <= 0:
                        break

                    # How many to take from this lot
                    from_lot = min(lot.remaining_quantity, remaining_to_sell)
                    if from_lot <= 0:
                        continue

                    # Calculate gain/loss
                    cost_basis = from_lot * lot.cost_per_share
                    lot_proceeds = from_lot * proceeds_per_share
                    gain_loss = lot_proceeds - cost_basis

                    # Determine holding period (IRS: > 1 year = long-term)
                    holding_days = (disposed_at - lot.acquired_at).days
                    holding_period = "long_term" if holding_days > LONG_TERM_THRESHOLD_DAYS else "short_term"

                    # Record disposition with idempotency (psycopg cursor pattern)
                    # Uses unique constraint on idempotency_key to prevent duplicates
                    # RETURNING id detects if insert succeeded (vs conflict)
                    await cur.execute(
                        """
                        INSERT INTO tax_lot_dispositions
                        (lot_id, quantity, cost_basis, proceeds_per_share, total_proceeds,
                         disposed_at, disposition_type, destination_order_id, idempotency_key,
                         realized_gain_loss, holding_period)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (idempotency_key) DO NOTHING
                        RETURNING id
                        """,
                        (lot.id, from_lot, cost_basis, proceeds_per_share, lot_proceeds,
                         disposed_at, disposition_type, destination_order_id, effective_idem_key,
                         gain_loss, holding_period),
                    )
                    inserted_row = await cur.fetchone()

                    # Only update lot if insert succeeded (not a duplicate)
                    # This prevents double-decrement on webhook retries
                    if inserted_row is not None:
                        new_remaining = lot.remaining_quantity - from_lot
                        await cur.execute(
                            """
                            UPDATE tax_lots
                            SET remaining_quantity = %s,
                                closed_at = CASE WHEN %s = 0 THEN NOW() ELSE NULL END
                            WHERE id = %s
                            """,
                            (new_remaining, new_remaining, lot.id),
                        )
                    else:
                        # Duplicate disposition - skip lot update but still add to results
                        # (idempotent: return same result as original call)
                        new_remaining = lot.remaining_quantity  # No change

                    dispositions.append(Disposition(
                        lot_id=lot.id,
                        quantity=from_lot,
                        cost_basis=cost_basis,
                        proceeds=lot_proceeds,
                        realized_gain_loss=gain_loss,
                        holding_period=holding_period,
                        disposed_at=disposed_at,
                    ))

                    remaining_to_sell -= from_lot

                if remaining_to_sell > 0:
                    raise ValueError(
                        f"Insufficient shares: trying to sell {quantity} but only "
                        f"{quantity - remaining_to_sell} available"
                    )

            # psycopg3 requires explicit commit (autocommit=False by default)
            await conn.commit()

            return dispositions

    async def get_unrealized_lots(
        self,
        user_id: str,
        symbol: str | None = None,
    ) -> list[TaxLot]:
        """Get open tax lots with remaining shares.

        Uses psycopg cursor pattern with %s placeholders.
        """
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                if symbol:
                    await cur.execute(
                        """
                        SELECT * FROM tax_lots
                        WHERE user_id = %s AND symbol = %s AND remaining_quantity > 0
                        ORDER BY acquired_at
                        """,
                        (user_id, symbol),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT * FROM tax_lots
                        WHERE user_id = %s AND remaining_quantity > 0
                        ORDER BY symbol, acquired_at
                        """,
                        (user_id,),
                    )
                rows = await cur.fetchall()
                return [self._row_to_lot(row) for row in rows]

    async def get_gains_summary(
        self,
        user_id: str,
        tax_year: int,
    ) -> GainsSummary:
        """Get gains/losses summary for tax year.

        Uses psycopg cursor pattern with %s placeholders.
        """
        async with self._db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        d.holding_period,
                        SUM(CASE WHEN d.realized_gain_loss > 0 THEN d.realized_gain_loss ELSE 0 END) as gains,
                        SUM(CASE WHEN d.realized_gain_loss < 0 THEN d.realized_gain_loss ELSE 0 END) as losses
                    FROM tax_lot_dispositions d
                    JOIN tax_lots l ON d.lot_id = l.id
                    WHERE l.user_id = %s
                      AND date_trunc('year', d.disposed_at) = date_trunc('year', %s::date)
                    GROUP BY d.holding_period
                    """,
                    (user_id, date(tax_year, 1, 1)),
                )
                rows = await cur.fetchall()

            summary = GainsSummary(
                short_term_gains=Decimal(0),
                short_term_losses=Decimal(0),
                long_term_gains=Decimal(0),
                long_term_losses=Decimal(0),
            )

            for row in rows:
                if row["holding_period"] == "short_term":
                    summary.short_term_gains = row["gains"] or Decimal(0)
                    summary.short_term_losses = row["losses"] or Decimal(0)
                else:
                    summary.long_term_gains = row["gains"] or Decimal(0)
                    summary.long_term_losses = row["losses"] or Decimal(0)

            return summary

    async def _get_available_lots(
        self,
        cur,  # psycopg cursor from parent context
        user_id: str,
        symbol: str,
        method: CostBasisMethod,
    ) -> list[TaxLot]:
        """Get available lots sorted by method with row locking.

        Uses psycopg cursor pattern with %s placeholders.
        Note: cur is the cursor from the parent record_disposition context.

        CONCURRENCY: Uses FOR UPDATE to lock rows during disposition,
        preventing concurrent sales from double-allocating the same lots.
        """
        order = "ASC" if method == CostBasisMethod.FIFO else "DESC"
        await cur.execute(
            f"""
            SELECT * FROM tax_lots
            WHERE user_id = %s AND symbol = %s AND remaining_quantity > 0
            ORDER BY acquired_at {order}
            FOR UPDATE
            """,
            (user_id, symbol),
        )
        rows = await cur.fetchall()
        return [self._row_to_lot(row) for row in rows]

    async def _get_specific_lots(
        self, cur, lot_ids: list[UUID], user_id: str, symbol: str
    ) -> list[TaxLot]:
        """Get specific lots by ID with ownership validation and row locking.

        SECURITY: Validates user_id and symbol match to prevent cross-tenant
        access. Only returns lots owned by the specified user for the symbol.

        CONCURRENCY: Uses FOR UPDATE to lock rows during disposition,
        preventing concurrent sales from double-allocating the same lots.

        Uses psycopg cursor pattern with %s placeholders.
        Note: cur is the cursor from the parent record_disposition context.
        """
        await cur.execute(
            """
            SELECT * FROM tax_lots
            WHERE id = ANY(%s)
              AND user_id = %s
              AND symbol = %s
              AND remaining_quantity > 0
            ORDER BY acquired_at
            FOR UPDATE
            """,
            (lot_ids, user_id, symbol),
        )
        rows = await cur.fetchall()
        return [self._row_to_lot(row) for row in rows]

    def _row_to_lot(self, row) -> TaxLot:
        """Convert database row to TaxLot."""
        return TaxLot(
            id=row["id"],
            user_id=row["user_id"],
            symbol=row["symbol"],
            quantity=row["quantity"],
            cost_per_share=row["cost_per_share"],
            total_cost=row["total_cost"],
            acquired_at=row["acquired_at"],
            acquisition_type=row["acquisition_type"],
            remaining_quantity=row["remaining_quantity"],
        )
```

### 3. Export Formatters

```python
# libs/tax/export.py
"""Tax report export formatters."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.platform.tax.cost_basis import Disposition, GainsSummary


@dataclass
class TaxReportRow:
    """Row for tax report export."""
    symbol: str
    acquired_date: date
    disposed_date: date
    quantity: Decimal
    cost_basis: Decimal
    proceeds: Decimal
    gain_loss: Decimal
    holding_period: str
    wash_sale_adjustment: Decimal = Decimal(0)


class TXFExporter:
    """Export to TurboTax TXF format."""

    def export(self, rows: list[TaxReportRow], tax_year: int) -> str:
        """Export to TXF format string."""
        lines = [
            "V042",  # TXF version
            "ATax Lot Reporter",  # Application name
            f"D{tax_year}1231",  # Tax year end date
            "^",
        ]

        for row in rows:
            # Form 8949 record type
            record_type = "321" if row.holding_period == "short_term" else "323"
            lines.append(f"TD")
            lines.append(f"N{record_type}")
            lines.append(f"C1")
            lines.append(f"L1")
            lines.append(f"P{row.symbol}")
            lines.append(f"D{row.acquired_date.strftime('%m/%d/%Y')}")
            lines.append(f"D{row.disposed_date.strftime('%m/%d/%Y')}")
            lines.append(f"${row.proceeds:.2f}")
            lines.append(f"${row.cost_basis:.2f}")
            if row.wash_sale_adjustment:
                lines.append(f"${row.wash_sale_adjustment:.2f}")
            lines.append(f"${row.gain_loss:.2f}")
            lines.append("^")

        return "\n".join(lines)


class CSVExporter:
    """Export to CSV format."""

    def export(self, rows: list[TaxReportRow]) -> str:
        """Export to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Symbol",
            "Date Acquired",
            "Date Sold",
            "Quantity",
            "Cost Basis",
            "Proceeds",
            "Gain/Loss",
            "Holding Period",
            "Wash Sale Adj",
        ])

        for row in rows:
            writer.writerow([
                row.symbol,
                row.acquired_date.isoformat(),
                row.disposed_date.isoformat(),
                str(row.quantity),
                f"{row.cost_basis:.2f}",
                f"{row.proceeds:.2f}",
                f"{row.gain_loss:.2f}",
                row.holding_period,
                f"{row.wash_sale_adjustment:.2f}" if row.wash_sale_adjustment else "",
            ])

        return output.getvalue()


class PDFExporter:
    """Export to PDF format."""

    def export(self, rows: list[TaxReportRow], summary: GainsSummary, tax_year: int) -> bytes:
        """Export to PDF bytes."""
        from libs.reporting.html_generator import HTMLGenerator
        from libs.reporting.pdf_generator import PDFGenerator

        # Generate HTML then convert to PDF
        html = self._generate_html(rows, summary, tax_year)

        pdf_gen = PDFGenerator()
        # Note: This is sync, would need to be wrapped for async use
        return pdf_gen._generate_pdf(html)

    def _generate_html(
        self,
        rows: list[TaxReportRow],
        summary: GainsSummary,
        tax_year: int,
    ) -> str:
        """Generate HTML for PDF conversion."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Tax Report {tax_year}</title>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: right; }}
                th {{ background-color: #f4f4f4; }}
                .symbol {{ text-align: left; }}
                .gain {{ color: green; }}
                .loss {{ color: red; }}
            </style>
        </head>
        <body>
            <h1>Capital Gains Report - Tax Year {tax_year}</h1>

            <h2>Summary</h2>
            <table>
                <tr><th>Category</th><th>Gains</th><th>Losses</th><th>Net</th></tr>
                <tr>
                    <td class="symbol">Short-Term</td>
                    <td class="gain">${summary.short_term_gains:,.2f}</td>
                    <td class="loss">${summary.short_term_losses:,.2f}</td>
                    <td>${summary.net_short_term:,.2f}</td>
                </tr>
                <tr>
                    <td class="symbol">Long-Term</td>
                    <td class="gain">${summary.long_term_gains:,.2f}</td>
                    <td class="loss">${summary.long_term_losses:,.2f}</td>
                    <td>${summary.net_long_term:,.2f}</td>
                </tr>
                <tr>
                    <td class="symbol"><strong>Total</strong></td>
                    <td></td>
                    <td></td>
                    <td><strong>${summary.total_net:,.2f}</strong></td>
                </tr>
            </table>

            <h2>Transactions</h2>
            <table>
                <tr>
                    <th class="symbol">Symbol</th>
                    <th>Acquired</th>
                    <th>Sold</th>
                    <th>Qty</th>
                    <th>Cost Basis</th>
                    <th>Proceeds</th>
                    <th>Gain/Loss</th>
                    <th>Period</th>
                </tr>
                {"".join(self._row_to_html(row) for row in rows)}
            </table>
        </body>
        </html>
        """

    def _row_to_html(self, row: TaxReportRow) -> str:
        gain_class = "gain" if row.gain_loss >= 0 else "loss"
        return f"""
        <tr>
            <td class="symbol">{row.symbol}</td>
            <td>{row.acquired_date}</td>
            <td>{row.disposed_date}</td>
            <td>{row.quantity}</td>
            <td>${row.cost_basis:,.2f}</td>
            <td>${row.proceeds:,.2f}</td>
            <td class="{gain_class}">${row.gain_loss:,.2f}</td>
            <td>{row.holding_period.replace('_', ' ').title()}</td>
        </tr>
        """
```

### 4. Tax Lots Page

```python
# apps/web_console/pages/tax_lots.py
"""Tax Lot Reporter page."""

from __future__ import annotations

import os
from datetime import date

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.utils.db_pool import get_db_pool
from libs.platform.tax.cost_basis import CostBasisCalculator, CostBasisMethod

FEATURE_TAX_LOTS = os.getenv("FEATURE_TAX_LOTS", "false").lower() in {
    "1", "true", "yes", "on",
}


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Tax Lot Reporter", page_icon="📋", layout="wide")
    st.title("Tax Lot Reporter")

    if not FEATURE_TAX_LOTS:
        st.info("Feature not available.")
        return

    user = get_current_user()
    if not has_permission(user, Permission.VIEW_TAX_REPORTS):
        st.error("Permission denied: VIEW_TAX_REPORTS required.")
        st.stop()

    user_id = user.get("user_id")  # VARCHAR to match RBAC
    db_pool = get_db_pool()
    calculator = CostBasisCalculator(db_pool)

    tab1, tab2, tab3, tab4 = st.tabs([
        "Open Positions",
        "Gains/Losses",
        "Export",
        "Settings",
    ])

    with tab1:
        _render_open_positions(calculator, user_id)

    with tab2:
        _render_gains_losses(calculator, user_id)

    with tab3:
        _render_export(calculator, user_id)

    with tab4:
        _render_settings(calculator, user_id, user)


def _render_open_positions(calculator: CostBasisCalculator, user_id: str) -> None:
    """Render open tax lots."""
    st.subheader("Open Tax Lots")

    symbol_filter = st.text_input("Filter by Symbol").upper().strip() or None

    # This would need to be async in practice
    # lots = await calculator.get_unrealized_lots(user_id, symbol_filter)
    lots = []  # Placeholder

    if not lots:
        st.info("No open positions.")
        return

    # Display lots table
    for lot in lots:
        with st.expander(f"{lot.symbol} - {lot.remaining_quantity} shares"):
            col1, col2, col3 = st.columns(3)
            col1.metric("Cost Basis", f"${lot.total_cost:,.2f}")
            col2.metric("Per Share", f"${lot.cost_per_share:,.2f}")
            col3.metric("Acquired", lot.acquired_at.strftime("%Y-%m-%d"))


def _render_gains_losses(calculator: CostBasisCalculator, user_id: str) -> None:
    """Render gains/losses summary."""
    st.subheader("Capital Gains Summary")

    tax_year = st.selectbox(
        "Tax Year",
        list(range(date.today().year, 2020, -1)),
    )

    # summary = await calculator.get_gains_summary(user_id, tax_year)
    summary = None  # Placeholder

    if summary:
        col1, col2 = st.columns(2)

        with col1:
            st.write("**Short-Term**")
            st.metric("Gains", f"${summary.short_term_gains:,.2f}")
            st.metric("Losses", f"${summary.short_term_losses:,.2f}")
            st.metric("Net", f"${summary.net_short_term:,.2f}")

        with col2:
            st.write("**Long-Term**")
            st.metric("Gains", f"${summary.long_term_gains:,.2f}")
            st.metric("Losses", f"${summary.long_term_losses:,.2f}")
            st.metric("Net", f"${summary.net_long_term:,.2f}")

        st.divider()
        st.metric("Total Net Gain/Loss", f"${summary.total_net:,.2f}")


def _render_export(calculator: CostBasisCalculator, user_id: str) -> None:
    """Render export controls."""
    st.subheader("Export Tax Reports")

    tax_year = st.selectbox(
        "Tax Year",
        list(range(date.today().year, 2020, -1)),
        key="export_year",
    )

    format_choice = st.radio(
        "Format",
        ["CSV", "TXF (TurboTax)", "PDF"],
        horizontal=True,
    )

    if st.button("Generate Export"):
        st.info(f"Export for {tax_year} in {format_choice} format would be generated here.")


def _render_settings(calculator: CostBasisCalculator, user_id: str, user) -> None:
    """Render cost basis settings."""
    st.subheader("Cost Basis Settings")

    # current_method = await calculator.get_user_method(user_id)
    current_method = CostBasisMethod.FIFO  # Placeholder

    new_method = st.selectbox(
        "Cost Basis Method",
        [m.value for m in CostBasisMethod],
        index=[m.value for m in CostBasisMethod].index(current_method.value),
    )

    st.caption("""
    **FIFO (First In, First Out):** Sells oldest shares first.
    **LIFO (Last In, First Out):** Sells newest shares first.
    **Specific ID:** You choose which shares to sell.
    """)

    if new_method != current_method.value:
        reason = st.text_area("Reason for change (required for audit)")

        if st.button("Save Method"):
            if not reason:
                st.error("Please provide a reason for the change.")
            else:
                st.success(f"Cost basis method changed to {new_method}")


if __name__ == "__main__":
    main()
```

---

## Testing Strategy

### Unit Tests

```python
# tests/libs/tax/test_cost_basis.py

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from libs.platform.tax.cost_basis import CostBasisCalculator, CostBasisMethod, TaxLot


@pytest.fixture
def sample_lots():
    """Create sample tax lots for testing.

    user_id is a string (VARCHAR) to match RBAC tables.
    """
    base_date = datetime(2023, 1, 15)
    return [
        TaxLot(
            id=uuid4(),
            user_id="test_user_123",  # VARCHAR to match RBAC
            symbol="AAPL",
            quantity=Decimal(100),
            cost_per_share=Decimal("150.00"),
            total_cost=Decimal("15000.00"),
            acquired_at=base_date,
            acquisition_type="buy",
            remaining_quantity=Decimal(100),
        ),
        TaxLot(
            id=uuid4(),
            user_id="test_user_123",  # Same user for both lots
            symbol="AAPL",
            quantity=Decimal(50),
            cost_per_share=Decimal("160.00"),
            total_cost=Decimal("8000.00"),
            acquired_at=base_date + timedelta(days=30),
            acquisition_type="buy",
            remaining_quantity=Decimal(50),
        ),
    ]


def test_fifo_uses_oldest_first(sample_lots):
    """FIFO should sell oldest shares first."""
    # Selling 75 shares should use all 100 from lot 1, then none from lot 2
    # Wait, that's wrong - 75 < 100, so only lot 1 is used
    # Actually should use 75 from first lot
    pass


def test_lifo_uses_newest_first(sample_lots):
    """LIFO should sell newest shares first."""
    pass


def test_holding_period_classification():
    """Verify short-term vs long-term classification per IRS rules."""
    # Shares held <= 365 days = short-term
    # Shares held > 365 days (more than 1 year) = long-term
    # Edge case: exactly 365 days = short-term
    # Edge case: 366 days = long-term
    pass


def test_insufficient_shares_raises_error():
    """Cannot sell more shares than available."""
    pass
```

---

## Tax Lot Ingestion Pipeline

Tax lots can be created via two paths:

### 1. Real-Time Trade Ingestion (Primary Path)

New trades are automatically converted to tax lots via order fill webhooks:

```
Order Fill (Alpaca Webhook)
        │
        ▼
ExecutionGateway.handle_fill()
        │
        ▼
TaxLotService.on_order_filled(order)  # <-- New integration point
        │
        ├── If BUY: CostBasisCalculator.record_acquisition()
        │
        └── If SELL: CostBasisCalculator.record_disposition()
```

**Implementation Note:** Add a TaxLotService that listens to order fill events (via Redis pub/sub or direct call from ExecutionGateway) and creates/updates tax lots in real-time.

### 2. Historical Backfill (One-Time Migration)

For existing accounts with trade history but no tax lots, use the backfill utility:

## Tax Lot Backfill Utility

**Purpose:** For existing accounts with trade history but no tax lots, replay all historical trades through the CostBasisCalculator to establish the current tax lot state.

```python
# scripts/backfill_tax_lots.py
"""Backfill tax lots from historical trade data.

NOTE: Scripts (not libs) can import from apps. The libs/tax module uses a
Protocol interface (AsyncConnectionPool) to avoid importing from apps.
Scripts inject the concrete implementation from apps.web_console.utils.db_pool.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID

# Scripts can import from apps (unlike libs which use Protocol interfaces)
from apps.web_console.utils.db_pool import get_db_pool
from libs.platform.tax.cost_basis import CostBasisCalculator, AsyncConnectionPool

logger = logging.getLogger(__name__)


async def backfill_tax_lots(
    db_adapter: AsyncConnectionPool,
    user_id: str,
    strategy_id: str,
    dry_run: bool = True,
) -> dict:
    """Backfill tax lots from order history.

    The orders table uses strategy_id (not user_id), so both are required:
    - user_id: Used for tax lot ownership (matches RBAC user_id VARCHAR)
    - strategy_id: Used to query orders (e.g., "alpha_baseline")

    Args:
        db_adapter: Any AsyncConnectionPool implementation (e.g., from get_db_pool)
        user_id: User who owns the tax lots (VARCHAR to match RBAC)
        strategy_id: Strategy ID to fetch orders from (e.g., "alpha_baseline")
        dry_run: If True, don't commit changes

    Returns:
        Summary of lots created and dispositions recorded
    """
    calculator = CostBasisCalculator(db_adapter)

    # Fetch all executed orders for strategy, ordered by timestamp
    # Uses psycopg cursor pattern with %s placeholders
    # NOTE: orders table uses strategy_id, not user_id. The caller must map
    # user_id to their strategy_id(s). A single user may have multiple strategies.
    # IMPORTANT: strategy_id parameter is passed, not user_id - caller resolves mapping.
    async with db_adapter.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    client_order_id, symbol, side, filled_qty, filled_avg_price,
                    filled_at, order_type
                FROM orders
                WHERE strategy_id = %s
                  AND status = 'filled'
                  AND filled_at IS NOT NULL
                ORDER BY filled_at ASC
                """,
                (strategy_id,),
            )
            orders = await cur.fetchall()

    stats = {
        "lots_created": 0,
        "dispositions_recorded": 0,
        "errors": [],
    }

    for order in orders:
        try:
            if order["side"] == "buy":
                # Record acquisition
                if not dry_run:
                    await calculator.record_acquisition(
                        user_id=user_id,
                        symbol=order["symbol"],
                        quantity=Decimal(str(order["filled_qty"])),
                        cost_per_share=Decimal(str(order["filled_avg_price"])),
                        acquired_at=order["filled_at"],
                        acquisition_type="buy",
                        source_order_id=order["client_order_id"],
                    )
                stats["lots_created"] += 1

            elif order["side"] == "sell":
                # Record disposition with order_id for idempotency
                # destination_order_id ensures re-running backfill won't double-dispose
                if not dry_run:
                    await calculator.record_disposition(
                        user_id=user_id,
                        symbol=order["symbol"],
                        quantity=Decimal(str(order["filled_qty"])),
                        proceeds_per_share=Decimal(str(order["filled_avg_price"])),
                        disposed_at=order["filled_at"],
                        disposition_type="sell",
                        destination_order_id=order["client_order_id"],  # Idempotency key
                    )
                stats["dispositions_recorded"] += 1

        except Exception as e:
            stats["errors"].append({
                "order_id": order["client_order_id"],
                "error": str(e),
            })
            logger.error(
                "Failed to process order in backfill",
                extra={"order_id": order["client_order_id"], "error": str(e)},
            )

    logger.info(
        "Tax lot backfill completed",
        extra={
            "user_id": user_id,
            "dry_run": dry_run,
            **stats,
        },
    )

    return stats


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backfill tax lots from trade history")
    parser.add_argument("--user-id", required=True, help="User ID for tax lot ownership (VARCHAR)")
    parser.add_argument("--strategy-id", required=True, help="Strategy ID to query orders (e.g., alpha_baseline)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without committing")
    args = parser.parse_args()

    # Use psycopg AsyncConnectionAdapter pattern (same as web console)
    # get_db_pool() returns the singleton pool - no await needed
    # NOTE: The adapter uses connection pooling, no explicit close() needed
    db_adapter = get_db_pool()

    stats = await backfill_tax_lots(
        db_adapter,
        args.user_id,  # VARCHAR string for tax lot ownership
        args.strategy_id,  # Strategy ID for orders query
        dry_run=args.dry_run,
    )

    print(f"\nBackfill {'preview' if args.dry_run else 'complete'}:")
    print(f"  Lots created: {stats['lots_created']}")
    print(f"  Dispositions: {stats['dispositions_recorded']}")
    print(f"  Errors: {len(stats['errors'])}")

    if stats["errors"]:
        print("\nErrors:")
        for err in stats["errors"][:10]:
            print(f"  - Order {err['order_id']}: {err['error']}")


if __name__ == "__main__":
    asyncio.run(main())
```

**Usage:**
```bash
# Preview what would be created for user "alice" from strategy "alpha_baseline"
python scripts/backfill_tax_lots.py --user-id alice --strategy-id alpha_baseline --dry-run

# Execute backfill
python scripts/backfill_tax_lots.py --user-id alice --strategy-id alpha_baseline
```

---

## Deliverables

1. **CostBasisCalculator:** FIFO/LIFO/Specific ID implementation
2. **Export Formatters:** TXF, CSV, PDF export
3. **Tax Lots Page:** Streamlit UI
4. **Database Migration:** 0019_create_tax_lots.sql
5. **Backfill Utility:** `scripts/backfill_tax_lots.py`
6. **Tests:** Unit tests for cost basis logic
7. **Documentation:** `docs/CONCEPTS/tax-lot-accounting.md`

---

## Verification Checklist

- [ ] FIFO correctly uses oldest shares first
- [ ] LIFO correctly uses newest shares first
- [ ] Specific ID allows lot selection
- [ ] Short-term/long-term classification correct (365 day rule)
- [ ] Gains summary calculates correctly
- [ ] TXF export validates with TurboTax
- [ ] CSV export has correct format
- [ ] PDF export readable
- [ ] Method change audited
- [ ] Backfill utility tested with sample data
- [ ] Backfill handles edge cases (partial lots, splits)
- [ ] RBAC enforcement tested
- [ ] All tests pass
