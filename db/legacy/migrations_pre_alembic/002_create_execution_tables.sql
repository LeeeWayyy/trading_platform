-- Migration 002: Create Execution Gateway Tables
-- Description: Creates orders and positions tables for T4 Execution Gateway
-- Author: Claude Code
-- Date: 2025-10-17

-- ============================================================================
-- Orders Table
-- ============================================================================
-- Tracks the full lifecycle of orders from submission to fill/cancel/reject
-- Primary key is deterministic client_order_id for idempotency

CREATE TABLE IF NOT EXISTS orders (
    -- Primary key: deterministic client_order_id
    -- Format: SHA256(symbol|side|qty|limit_price|strategy_id|date)[:24]
    client_order_id TEXT PRIMARY KEY,

    -- Order parameters
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT CHECK (side IN ('buy', 'sell')) NOT NULL,
    qty NUMERIC NOT NULL CHECK (qty > 0),
    order_type TEXT DEFAULT 'market' CHECK (order_type IN ('market', 'limit', 'stop', 'stop_limit')),
    limit_price NUMERIC CHECK (limit_price IS NULL OR limit_price > 0),
    stop_price NUMERIC CHECK (stop_price IS NULL OR stop_price > 0),
    time_in_force TEXT DEFAULT 'day' CHECK (time_in_force IN ('day', 'gtc', 'ioc', 'fok')),

    -- Status tracking
    -- Possible values: dry_run, pending_new, accepted, filled, partially_filled,
    --                  cancelled, rejected, expired
    status TEXT NOT NULL,
    broker_order_id TEXT UNIQUE,  -- Alpaca's order_id (null for dry_run orders)

    -- Error tracking
    error_message TEXT,
    retry_count INTEGER DEFAULT 0 CHECK (retry_count >= 0),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    submitted_at TIMESTAMPTZ,  -- When submitted to broker
    filled_at TIMESTAMPTZ,     -- When fully filled

    -- Fill details (populated by webhooks or API polling)
    filled_qty NUMERIC DEFAULT 0 CHECK (filled_qty >= 0),
    filled_avg_price NUMERIC CHECK (filled_avg_price IS NULL OR filled_avg_price > 0),

    -- Additional metadata
    metadata JSONB DEFAULT '{}'::jsonb  -- For storing additional order details
);

-- Indexes for efficient queries
CREATE INDEX idx_orders_strategy_created ON orders(strategy_id, created_at DESC);
CREATE INDEX idx_orders_symbol_created ON orders(symbol, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_broker_id ON orders(broker_order_id) WHERE broker_order_id IS NOT NULL;
CREATE INDEX idx_orders_created_at ON orders(created_at DESC);

-- Comment on table
COMMENT ON TABLE orders IS 'Tracks order lifecycle from submission to fill/cancel/reject. Uses deterministic client_order_id for idempotency.';

-- Comments on key columns
COMMENT ON COLUMN orders.client_order_id IS 'Deterministic ID: SHA256(symbol|side|qty|limit_price|strategy_id|date)[:24]';
COMMENT ON COLUMN orders.status IS 'Order status: dry_run, pending_new, accepted, filled, partially_filled, cancelled, rejected, expired';
COMMENT ON COLUMN orders.broker_order_id IS 'Alpaca order_id. NULL for dry_run orders.';
COMMENT ON COLUMN orders.retry_count IS 'Number of retry attempts for this order';

-- ============================================================================
-- Positions Table
-- ============================================================================
-- Tracks current positions from order fills
-- Updated via webhooks when orders are filled

CREATE TABLE IF NOT EXISTS positions (
    -- Primary key: symbol
    symbol TEXT PRIMARY KEY,

    -- Position details
    qty NUMERIC NOT NULL,  -- Can be negative for short positions
    avg_entry_price NUMERIC NOT NULL CHECK (avg_entry_price > 0),

    -- Current market data (updated periodically)
    current_price NUMERIC CHECK (current_price IS NULL OR current_price > 0),

    -- P&L tracking
    unrealized_pl NUMERIC,  -- (current_price - avg_entry_price) * qty
    realized_pl NUMERIC DEFAULT 0,  -- Cumulative realized P&L from closed positions

    -- Timestamps
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    last_trade_at TIMESTAMPTZ,  -- Last time position was modified by a trade

    -- Additional metadata
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes
CREATE INDEX idx_positions_updated ON positions(updated_at DESC);
CREATE INDEX idx_positions_qty ON positions(qty) WHERE qty != 0;

-- Comment on table
COMMENT ON TABLE positions IS 'Tracks current positions from order fills. Updated via webhooks.';

-- Comments on key columns
COMMENT ON COLUMN positions.qty IS 'Position quantity. Positive=long, negative=short, zero=flat';
COMMENT ON COLUMN positions.avg_entry_price IS 'Average entry price for current position';
COMMENT ON COLUMN positions.unrealized_pl IS 'Unrealized P&L: (current_price - avg_entry_price) * qty';
COMMENT ON COLUMN positions.realized_pl IS 'Cumulative realized P&L from closed positions';

-- ============================================================================
-- Triggers
-- ============================================================================
-- Auto-update updated_at timestamp on row modification

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for orders table
CREATE TRIGGER update_orders_updated_at
BEFORE UPDATE ON orders
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- Trigger for positions table
CREATE TRIGGER update_positions_updated_at
BEFORE UPDATE ON positions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Verification Queries
-- ============================================================================
-- Run these to verify migration succeeded

-- Check tables exist
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public' AND table_name IN ('orders', 'positions');

-- Check indexes
-- SELECT indexname FROM pg_indexes
-- WHERE tablename IN ('orders', 'positions');

-- Check triggers
-- SELECT trigger_name, event_manipulation, event_object_table
-- FROM information_schema.triggers
-- WHERE event_object_table IN ('orders', 'positions');
