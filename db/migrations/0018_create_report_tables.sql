-- Migration: 0018_create_report_tables.sql
-- Purpose: Reporting schedules, runs, and archives (P4T7)
-- Date: 2025-12-29

-- Extension guard for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE report_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    template_type VARCHAR(50) NOT NULL,
    schedule_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    recipients JSONB NOT NULL DEFAULT '[]'::JSONB,
    strategies JSONB NOT NULL DEFAULT '[]'::JSONB,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE report_schedule_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID NOT NULL REFERENCES report_schedules(id) ON DELETE CASCADE,
    run_key VARCHAR(100) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE TABLE report_archives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id UUID REFERENCES report_schedules(id) ON DELETE SET NULL,
    user_id VARCHAR(255) NOT NULL,
    idempotency_key VARCHAR(100) NOT NULL UNIQUE,
    generated_at TIMESTAMPTZ NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_format VARCHAR(20) NOT NULL,
    file_size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_report_schedules_user ON report_schedules(user_id);
CREATE INDEX idx_report_schedules_template_type ON report_schedules(template_type);
CREATE INDEX idx_report_schedules_next_run ON report_schedules(next_run_at) WHERE enabled = TRUE;

CREATE INDEX idx_report_schedule_runs_schedule ON report_schedule_runs(schedule_id);
CREATE INDEX idx_report_schedule_runs_status ON report_schedule_runs(status);

CREATE INDEX idx_report_archives_user ON report_archives(user_id);
CREATE INDEX idx_report_archives_schedule ON report_archives(schedule_id);
CREATE INDEX idx_report_archives_generated ON report_archives(generated_at);
