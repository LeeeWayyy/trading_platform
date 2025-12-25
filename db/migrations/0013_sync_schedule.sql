-- Migration: 0013_sync_schedule.sql
-- Purpose: Sync schedule configuration for web console data management (P4T6)
-- Date: 2025-12-24

CREATE TABLE data_sync_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset VARCHAR(100) NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT true,
    cron_expression VARCHAR(100) NOT NULL DEFAULT '0 2 * * *',  -- 2 AM daily
    last_scheduled_run TIMESTAMP WITH TIME ZONE,
    next_scheduled_run TIMESTAMP WITH TIME ZONE,
    updated_by VARCHAR(255),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1  -- For optimistic locking
);

CREATE UNIQUE INDEX idx_sync_schedule_dataset ON data_sync_schedule(dataset);

-- Optimistic locking: UPDATE ... WHERE version = expected_version
-- On conflict: return current version to client, client must retry with fresh data
