-- Backtest jobs metadata table (T5.2 Backtest Result Storage)
-- Depends on: 0007_strategy_session_version_triggers.sql

-- Extension guard: required for gen_random_uuid() on some PG installations
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE backtest_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id VARCHAR(32) UNIQUE NOT NULL,  -- Idempotency key (SHA256[:32])
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CONSTRAINT status_vocabulary CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),

    -- Configuration
    alpha_name VARCHAR(255) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    weight_method VARCHAR(50) NOT NULL,
    config_json JSONB NOT NULL,

    -- Execution
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    worker_id VARCHAR(255),
    progress_pct SMALLINT DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),
    job_timeout INTEGER NOT NULL DEFAULT 3600
        CONSTRAINT timeout_bounds CHECK (job_timeout BETWEEN 300 AND 14400),

    -- Results (summary only; bulk data stored in Parquet)
    result_path VARCHAR(512),
    mean_ic FLOAT,
    icir FLOAT,
    hit_rate FLOAT,
    coverage FLOAT,
    long_short_spread FLOAT,
    average_turnover FLOAT,
    decay_half_life FLOAT,

    -- Reproducibility
    snapshot_id VARCHAR(255),
    dataset_version_ids JSONB,

    -- Error handling
    error_message TEXT,
    retry_count SMALLINT DEFAULT 0,

    created_by VARCHAR(255) NOT NULL
);

CREATE INDEX idx_backtest_jobs_status ON backtest_jobs(status);
CREATE INDEX idx_backtest_jobs_created_at ON backtest_jobs(created_at);
CREATE INDEX idx_backtest_jobs_alpha_name ON backtest_jobs(alpha_name);
CREATE INDEX idx_backtest_jobs_created_by ON backtest_jobs(created_by);
CREATE INDEX idx_backtest_jobs_snapshot_id ON backtest_jobs(snapshot_id);
CREATE INDEX idx_backtest_jobs_user_status ON backtest_jobs(created_by, status, created_at DESC);
