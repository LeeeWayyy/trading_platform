-- Migration 0028: Add active status and metadata to strategies table
-- Purpose: Support strategy enable/disable from web console (P6T17.1)
-- Date: 2026-03-12

ALTER TABLE strategies
    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_by VARCHAR(255);

-- Seed default strategy to prevent fail-closed signal service check from blocking
-- on fresh deployments or environments that lack ad hoc strategy rows.
-- The signal service's default_strategy is 'alpha_baseline' (apps/signal_service/config.py:156),
-- but this is configurable via env var. Deployments using a different default_strategy
-- must ensure that strategy exists in this table with active=true, or the fail-closed
-- check will block signal generation. The seed below covers the default case only.
INSERT INTO strategies (strategy_id, name, description)
VALUES ('alpha_baseline', 'Alpha Baseline', 'Default alpha signal strategy')
ON CONFLICT (strategy_id) DO NOTHING;

-- Index for fast lookup of active strategies
CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies (active) WHERE active = true;

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_strategies_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_strategies_updated_at ON strategies;
CREATE TRIGGER trg_strategies_updated_at
    BEFORE UPDATE ON strategies
    FOR EACH ROW EXECUTE FUNCTION update_strategies_timestamp();
