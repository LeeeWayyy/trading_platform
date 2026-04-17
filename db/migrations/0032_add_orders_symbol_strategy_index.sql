-- Add composite index to speed up symbol->strategy resolution queries used by dashboard context.
-- Query shape: WHERE symbol = ? [AND strategy_id = ANY(?)] GROUP BY strategy_id LIMIT 2.
CREATE INDEX IF NOT EXISTS idx_orders_symbol_strategy
  ON orders(symbol, strategy_id)
  WHERE strategy_id IS NOT NULL;
