-- Migration: 0012_sync_logs.sql
-- Purpose: Sync log storage for web console data management (P4T6)
-- Date: 2025-12-24

CREATE TABLE data_sync_logs (
    id BIGSERIAL PRIMARY KEY,
    dataset VARCHAR(100) NOT NULL,
    level VARCHAR(20) NOT NULL,        -- INFO, WARN, ERROR
    message TEXT NOT NULL,
    extra JSONB,                         -- Additional structured data
    sync_run_id UUID,                    -- Links to specific sync run
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sync_logs_dataset_created ON data_sync_logs(dataset, created_at DESC);
CREATE INDEX idx_sync_logs_level ON data_sync_logs(level);
CREATE INDEX idx_sync_logs_created ON data_sync_logs(created_at DESC);

-- Retention: 30 days, cleaned by scheduled job
-- Query pattern: SELECT * FROM data_sync_logs WHERE dataset = ? ORDER BY created_at DESC LIMIT 100

-- Ingestion: SyncManager writes logs via PostgreSQLLogHandler
-- Handler configured in libs/data_providers/sync_manager.py during sync operations
