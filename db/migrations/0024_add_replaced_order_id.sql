-- Migration: Add replaced_order_id to orders for order modification lineage (P6T6)
-- Date: 2026-01-28

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS replaced_order_id TEXT REFERENCES orders(client_order_id);

CREATE INDEX IF NOT EXISTS idx_orders_replaced
  ON orders(replaced_order_id)
  WHERE replaced_order_id IS NOT NULL;

COMMENT ON COLUMN orders.replaced_order_id IS
  'Replacement order only: points to original client_order_id that was replaced';
