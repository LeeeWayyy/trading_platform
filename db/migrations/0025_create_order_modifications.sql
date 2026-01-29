-- Migration: Create order_modifications audit table (P6T6)
-- Date: 2026-01-28

-- Extension guard for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS order_modifications (
    modification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_client_order_id TEXT NOT NULL,
    new_client_order_id TEXT NOT NULL,
    original_broker_order_id TEXT,
    new_broker_order_id TEXT,
    modification_seq INTEGER NOT NULL,
    idempotency_key UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    modified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_by TEXT NOT NULL,
    reason TEXT,
    changes JSONB NOT NULL,
    FOREIGN KEY (original_client_order_id) REFERENCES orders(client_order_id) ON DELETE RESTRICT,
    CONSTRAINT uq_order_modifications_seq UNIQUE (original_client_order_id, modification_seq),
    CONSTRAINT uq_order_modifications_idempotency UNIQUE (original_client_order_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_order_modifications_original
  ON order_modifications(original_client_order_id);

CREATE INDEX IF NOT EXISTS idx_order_modifications_new
  ON order_modifications(new_client_order_id);

CREATE INDEX IF NOT EXISTS idx_order_modifications_modified_at
  ON order_modifications(modified_at DESC);

CREATE INDEX IF NOT EXISTS idx_order_modifications_modified_by
  ON order_modifications(modified_by);

CREATE INDEX IF NOT EXISTS idx_order_modifications_pending_stale
  ON order_modifications(modified_at)
  WHERE status = 'pending';

COMMENT ON TABLE order_modifications IS
  'Audit trail for order modifications linking original and replacement orders.';
