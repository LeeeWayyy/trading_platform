-- Migration: Add export_audit table and audit_log indexes for P6T8
-- Purpose: Support execution analytics with export audit tracking and efficient audit queries
-- Author: Claude Code (P6T8)
-- Date: 2026-01-31

-- Add index for audit trail queries (resource_type + resource_id + timestamp)
-- Used by GET /api/v1/orders/{client_order_id}/audit endpoint
-- Index sort order matches query ORDER BY (timestamp ASC, id ASC) for optimal performance
CREATE INDEX IF NOT EXISTS idx_audit_log_resource
    ON audit_log (resource_type, resource_id, timestamp ASC, id ASC);

-- Create export_audit table for tracking data exports
-- Separate from audit_log because exports have lifecycle (pending -> completed/failed/expired)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS export_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    export_type VARCHAR(20) NOT NULL CHECK (export_type IN ('csv', 'excel', 'clipboard')),
    grid_name VARCHAR(100) NOT NULL,
    filter_params JSONB,
    visible_columns JSONB,
    sort_model JSONB,
    strategy_ids JSONB,
    export_scope VARCHAR(20) NOT NULL DEFAULT 'visible' CHECK (export_scope IN ('visible', 'full')),
    estimated_row_count INTEGER,
    actual_row_count INTEGER,
    reported_by VARCHAR(10) CHECK (reported_by IN ('client', 'server')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'downloading', 'completed', 'failed', 'expired')),
    error_message TEXT,
    ip_address TEXT,
    session_id TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Indexes for export_audit queries
CREATE INDEX IF NOT EXISTS idx_export_audit_user_created
    ON export_audit (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_export_audit_status_created
    ON export_audit (status, created_at)
    WHERE status = 'pending';

-- Comments for documentation
COMMENT ON TABLE export_audit IS 'Audit trail for data exports (CSV, Excel, clipboard) with lifecycle tracking';
COMMENT ON COLUMN export_audit.export_type IS 'Type of export: csv, excel, or clipboard';
COMMENT ON COLUMN export_audit.grid_name IS 'Name of the grid being exported (positions, orders, fills, audit, tca)';
COMMENT ON COLUMN export_audit.filter_params IS 'AG Grid filter model applied during export';
COMMENT ON COLUMN export_audit.visible_columns IS 'List of columns included in export';
COMMENT ON COLUMN export_audit.strategy_ids IS 'Server-injected strategy scope for compliance';
COMMENT ON COLUMN export_audit.export_scope IS 'visible = current page, full = all filtered rows';
COMMENT ON COLUMN export_audit.estimated_row_count IS 'Server-computed row count at audit creation';
COMMENT ON COLUMN export_audit.actual_row_count IS 'Actual rows exported (client-reported for CSV/clipboard, server-computed for Excel)';
COMMENT ON COLUMN export_audit.reported_by IS 'Who reported actual_row_count: client (CSV/clipboard) or server (Excel)';
COMMENT ON COLUMN export_audit.status IS 'Export lifecycle: pending -> completed/failed/expired';
COMMENT ON COLUMN export_audit.error_message IS 'Error details if status=failed';
