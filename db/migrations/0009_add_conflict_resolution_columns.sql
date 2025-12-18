-- Migration: Add reconciliation conflict resolution columns + orphan tracking
--
-- Adds CAS-related columns to orders, plus reconciliation high-water mark
-- tracking and orphan order quarantine tables.
--
-- Created: 2025-12-17

-- ==========================================================================
-- Orders table: conflict resolution metadata
-- ==========================================================================
ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS last_updated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS is_terminal BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS source_priority INTEGER NOT NULL DEFAULT 2,
  ADD COLUMN IF NOT EXISTS status_rank INTEGER,
  ADD COLUMN IF NOT EXISTS broker_event_id TEXT;

-- Backfill terminal state for existing rows
UPDATE orders
SET is_terminal = TRUE
WHERE status IN (
    'filled',
    'canceled',
    'expired',
    'failed',
    'rejected',
    'replaced',
    'done_for_day',
    'blocked_kill_switch',
    'blocked_circuit_breaker'
);

-- ==========================================================================
-- Reconciliation high-water mark
-- ==========================================================================
CREATE TABLE IF NOT EXISTS reconciliation_high_water_mark (
    name TEXT PRIMARY KEY,
    last_check_time TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ==========================================================================
-- Orphan orders table (broker orders without DB entries)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS orphan_orders (
    id SERIAL PRIMARY KEY,
    broker_order_id TEXT NOT NULL UNIQUE,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL DEFAULT 'external',
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    estimated_notional DECIMAL(18,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'untracked',
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orphan_orders_symbol_strategy
  ON orphan_orders(symbol, strategy_id);

CREATE INDEX IF NOT EXISTS idx_orphan_orders_unresolved
  ON orphan_orders(resolved_at)
  WHERE resolved_at IS NULL;
