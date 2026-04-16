-- Add composite index to speed up symbol->strategy resolution queries used by dashboard context.
-- Query shape uses DISTINCT ON(strategy_id) + latest created_at ordering.
DROP INDEX IF EXISTS idx_orders_symbol_strategy;
CREATE INDEX IF NOT EXISTS idx_orders_symbol_strategy
  ON orders(symbol, strategy_id, created_at DESC)
  WHERE strategy_id IS NOT NULL;
