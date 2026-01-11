-- Migration: Create trades view for Trade Journal (P5T10)
--
-- Purpose:
-- The Trade Journal UI queries a `trades` relation. We don't currently
-- persist trades as a standalone table in local dev, so create a view
-- backed by the orders table to avoid missing-relation errors while
-- providing basic trade history for filled orders.
--
-- Created: 2026-01-10
-- Author: Codex CLI

CREATE OR REPLACE VIEW trades AS
SELECT
  o.client_order_id AS trade_id,
  o.strategy_id,
  o.symbol,
  o.side,
  COALESCE(o.filled_qty, o.qty) AS qty,
  o.filled_avg_price AS price,
  o.filled_at AS executed_at,
  0::numeric AS realized_pnl
FROM orders o
WHERE o.status IN ('filled', 'partially_filled')
  AND COALESCE(o.filled_qty, 0) > 0;
