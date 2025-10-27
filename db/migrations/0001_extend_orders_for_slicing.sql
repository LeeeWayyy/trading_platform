-- Migration: Extend orders table for TWAP order slicing (P2T0)
--
-- Adds support for parent-child order relationships to enable TWAP
-- (Time-Weighted Average Price) order slicing. Large parent orders
-- can be split into smaller child slices executed over time.
--
-- Schema Changes:
-- - parent_order_id: Links child slice to parent order (NULL for parent orders)
-- - slice_num: Sequential slice number (0-indexed, NULL for parent orders)
-- - total_slices: Total number of slices planned (set on parent, NULL on children)
-- - scheduled_time: UTC timestamp for slice execution (NULL for parent, set on children)
--
-- Created: 2025-10-26
-- Author: Claude Code (P2T0 implementation)

-- Add parent-child relationship columns with validation constraints
ALTER TABLE orders
  ADD COLUMN parent_order_id TEXT REFERENCES orders(client_order_id),
  ADD COLUMN slice_num INTEGER CHECK (slice_num IS NULL OR slice_num >= 0),
  ADD COLUMN total_slices INTEGER CHECK (total_slices IS NULL OR total_slices > 0),
  ADD COLUMN scheduled_time TIMESTAMPTZ;

-- Add unique constraint to prevent duplicate child slices
-- Ensures each (parent_order_id, slice_num) combination is unique
-- Prevents scheduler bugs from duplicate slice executions or gaps
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_parent_slice_unique
  ON orders(parent_order_id, slice_num)
  WHERE parent_order_id IS NOT NULL;

-- Add index for efficient child slice queries by parent
CREATE INDEX IF NOT EXISTS idx_orders_parent_id
  ON orders(parent_order_id)
  WHERE parent_order_id IS NOT NULL;

-- Add index for scheduled time queries (scheduler optimization)
-- Filter uses 'pending_new' to match actual order status vocabulary
CREATE INDEX IF NOT EXISTS idx_orders_scheduled_time
  ON orders(scheduled_time)
  WHERE scheduled_time IS NOT NULL AND status = 'pending_new';

-- Validate schema change
-- This comment documents expected behavior:
-- - Parent orders: parent_order_id=NULL, total_slices=N, slice_num=NULL, scheduled_time=NULL
-- - Child slices: parent_order_id=<parent_id>, total_slices=NULL, slice_num=0..N-1, scheduled_time=<timestamp>
