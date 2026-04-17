-- Add composite index to speed up symbol->strategy resolution queries used by dashboard context.
-- Query shape uses DISTINCT ON(strategy_id) + latest created_at ordering.
-- Use CONCURRENTLY to avoid blocking writes on large orders tables.
-- NOTE: PostgreSQL requires these statements to run outside an explicit transaction.
DROP INDEX CONCURRENTLY IF EXISTS idx_orders_symbol_strategy;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_symbol_strategy
  ON orders(symbol, strategy_id, created_at DESC)
  WHERE strategy_id IS NOT NULL;
