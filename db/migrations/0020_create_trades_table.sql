-- Migration: Create trades table for Trade Journal (P5T10)
--
-- Purpose:
-- Persist per-fill trade records derived from Alpaca webhooks or
-- reconciliation backfills. This enables the Trade Journal and
-- recent activity to query a single source of truth.
--
-- Created: 2026-01-11
-- Author: Codex CLI

-- Replace any legacy view to ensure table creation succeeds.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_views
    WHERE schemaname = 'public'
      AND viewname = 'trades'
  ) THEN
    EXECUTE 'DROP VIEW trades';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
  broker_order_id TEXT,
  strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  qty NUMERIC NOT NULL CHECK (qty > 0),
  price NUMERIC NOT NULL CHECK (price > 0),
  executed_at TIMESTAMPTZ NOT NULL,
  realized_pnl NUMERIC NOT NULL DEFAULT 0,
  source TEXT,
  synthetic BOOLEAN NOT NULL DEFAULT false,
  superseded BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_strategy_executed
  ON trades(strategy_id, executed_at DESC);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_executed
  ON trades(symbol, executed_at DESC);
