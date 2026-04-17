-- Rebuild symbol->strategy index to support latest-order disambiguation queries.
-- Safe for environments where 0032 was already applied: DROP+CREATE is idempotent.
DROP INDEX IF EXISTS idx_orders_symbol_strategy;
CREATE INDEX IF NOT EXISTS idx_orders_symbol_strategy
  ON orders(symbol, strategy_id, created_at DESC)
  WHERE strategy_id IS NOT NULL;
