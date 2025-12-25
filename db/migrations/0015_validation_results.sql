-- Migration: 0015_validation_results.sql
-- Purpose: Validation results storage for data sync validations (P4T6)
-- Date: 2025-12-24

CREATE TABLE data_validation_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset VARCHAR(100) NOT NULL,
    sync_run_id UUID,                        -- Links to sync operation
    validation_type VARCHAR(50) NOT NULL,    -- row_count, null_pct, schema, date_continuity
    status VARCHAR(20) NOT NULL,             -- passed, failed, warning
    expected_value TEXT,
    actual_value TEXT,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_validation_results_dataset ON data_validation_results(dataset);
CREATE INDEX idx_validation_results_created ON data_validation_results(created_at DESC);
CREATE INDEX idx_validation_results_status ON data_validation_results(status);
