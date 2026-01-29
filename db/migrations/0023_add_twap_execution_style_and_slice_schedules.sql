-- Migration: Add TWAP execution_style column and slice_schedules table (T6.0.1)
-- Purpose: Support TWAP order submission metadata and optional slice scheduling storage
-- Author: Codex CLI (T6.0.1)
-- Date: 2026-01-28

-- Add execution_style to orders (default to 'instant' for existing rows)
ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS execution_style TEXT DEFAULT 'instant';

-- Add index to help query TWAP parents/children by style
CREATE INDEX IF NOT EXISTS idx_orders_execution_style
  ON orders(execution_style);

-- Slice schedules table (if not already present)
CREATE TABLE IF NOT EXISTS slice_schedules (
    parent_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
    slice_index INTEGER NOT NULL CHECK (slice_index >= 0),
    scheduled_at TIMESTAMPTZ NOT NULL,
    qty NUMERIC NOT NULL CHECK (qty > 0),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (parent_order_id, slice_index)
);

-- Index for due-slice queries
CREATE INDEX IF NOT EXISTS idx_slice_schedules_due
  ON slice_schedules(scheduled_at)
  WHERE status = 'pending';

-- Trigger to update updated_at on modification
CREATE OR REPLACE FUNCTION update_slice_schedules_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_slice_schedules_updated_at ON slice_schedules;
CREATE TRIGGER trigger_update_slice_schedules_updated_at
    BEFORE UPDATE ON slice_schedules
    FOR EACH ROW
    EXECUTE FUNCTION update_slice_schedules_updated_at();

-- Comments for documentation
COMMENT ON TABLE slice_schedules IS 'TWAP slice scheduling metadata keyed by parent order';
COMMENT ON COLUMN slice_schedules.parent_order_id IS 'Parent order client_order_id';
COMMENT ON COLUMN slice_schedules.slice_index IS 'Slice index (0-based)';
COMMENT ON COLUMN slice_schedules.scheduled_at IS 'Scheduled execution time (UTC)';
COMMENT ON COLUMN slice_schedules.qty IS 'Slice quantity';
COMMENT ON COLUMN slice_schedules.status IS 'Slice status (pending, scheduled, submitted, filled, canceled, failed)';
