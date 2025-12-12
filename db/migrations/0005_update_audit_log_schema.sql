-- Migration: Update audit_log schema for RBAC and MFA events
-- Purpose: Add event metadata columns and indexes to support RBAC auditing
-- Author: Codex (T6.1a)
-- Date: 2025-12-10

-- Add new columns (nullable for backfill)
ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS event_type VARCHAR(20),
    ADD COLUMN IF NOT EXISTS resource_type VARCHAR(50),
    ADD COLUMN IF NOT EXISTS resource_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(20),
    ADD COLUMN IF NOT EXISTS amr_method VARCHAR(20);

-- Backfill existing rows with defaults
UPDATE audit_log SET event_type = 'action' WHERE event_type IS NULL;
UPDATE audit_log SET resource_type = 'system' WHERE resource_type IS NULL;
UPDATE audit_log SET outcome = 'success' WHERE outcome IS NULL;

-- Add NOT NULL constraints after backfill
ALTER TABLE audit_log ALTER COLUMN event_type SET NOT NULL;
ALTER TABLE audit_log ALTER COLUMN resource_type SET NOT NULL;
ALTER TABLE audit_log ALTER COLUMN outcome SET NOT NULL;

-- Create indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log (event_type, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_log (outcome) WHERE outcome <> 'success';
