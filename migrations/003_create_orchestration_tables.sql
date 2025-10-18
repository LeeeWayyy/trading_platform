-- Migration 003: Orchestration Tables
-- Creates tables for orchestrator service (T5)
--
-- Tables:
-- 1. orchestration_runs - Main orchestration run tracking
-- 2. signal_order_mappings - Maps signals to orders for each run
--
-- Author: T5 Implementation
-- Date: 2024-10-17

-- =============================================================================
-- orchestration_runs table
-- =============================================================================

CREATE TABLE IF NOT EXISTS orchestration_runs (
    id SERIAL PRIMARY KEY,
    run_id UUID UNIQUE NOT NULL,
    strategy_id VARCHAR(100) NOT NULL,
    as_of_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'partial')),

    -- Input parameters
    symbols TEXT[] NOT NULL,
    capital NUMERIC(15, 2) NOT NULL CHECK (capital > 0),
    max_position_size NUMERIC(15, 2),

    -- Signal metrics
    num_signals INTEGER DEFAULT 0 CHECK (num_signals >= 0),
    model_version VARCHAR(50),

    -- Order metrics
    num_orders_submitted INTEGER DEFAULT 0 CHECK (num_orders_submitted >= 0),
    num_orders_accepted INTEGER DEFAULT 0 CHECK (num_orders_accepted >= 0),
    num_orders_rejected INTEGER DEFAULT 0 CHECK (num_orders_rejected >= 0),
    num_orders_filled INTEGER DEFAULT 0 CHECK (num_orders_filled >= 0),

    -- Timing
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC(10, 3),

    -- Error tracking
    error_message TEXT,

    -- Metadata (JSONB for flexibility)
    signal_service_response JSONB,
    execution_gateway_responses JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for orchestration_runs
CREATE INDEX IF NOT EXISTS idx_orchestration_runs_run_id ON orchestration_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_orchestration_runs_status ON orchestration_runs(status);
CREATE INDEX IF NOT EXISTS idx_orchestration_runs_as_of_date ON orchestration_runs(as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_orchestration_runs_strategy_id ON orchestration_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_orchestration_runs_started_at ON orchestration_runs(started_at DESC);

-- Trigger to update updated_at
CREATE OR REPLACE FUNCTION update_orchestration_runs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_orchestration_runs_updated_at
    BEFORE UPDATE ON orchestration_runs
    FOR EACH ROW
    EXECUTE FUNCTION update_orchestration_runs_updated_at();


-- =============================================================================
-- signal_order_mappings table
-- =============================================================================

CREATE TABLE IF NOT EXISTS signal_order_mappings (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL,

    -- Signal information
    symbol VARCHAR(10) NOT NULL,
    predicted_return NUMERIC(10, 6),
    rank INTEGER,
    target_weight NUMERIC(5, 4),

    -- Order information
    client_order_id TEXT,
    order_qty INTEGER,
    order_side VARCHAR(10) CHECK (order_side IN ('buy', 'sell')),

    -- Execution information
    broker_order_id TEXT,
    order_status VARCHAR(20),
    filled_qty NUMERIC(15, 4),
    filled_avg_price NUMERIC(15, 4),

    -- Skip reason (if order not created)
    skip_reason TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Foreign key constraint
    CONSTRAINT fk_signal_order_mappings_run_id
        FOREIGN KEY (run_id)
        REFERENCES orchestration_runs(run_id)
        ON DELETE CASCADE
);

-- Indexes for signal_order_mappings
CREATE INDEX IF NOT EXISTS idx_signal_order_mappings_run_id ON signal_order_mappings(run_id);
CREATE INDEX IF NOT EXISTS idx_signal_order_mappings_symbol ON signal_order_mappings(symbol);
CREATE INDEX IF NOT EXISTS idx_signal_order_mappings_client_order_id ON signal_order_mappings(client_order_id);

-- Trigger to update updated_at
CREATE OR REPLACE FUNCTION update_signal_order_mappings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_signal_order_mappings_updated_at
    BEFORE UPDATE ON signal_order_mappings
    FOR EACH ROW
    EXECUTE FUNCTION update_signal_order_mappings_updated_at();


-- =============================================================================
-- Comments for documentation
-- =============================================================================

COMMENT ON TABLE orchestration_runs IS 'Tracks orchestration runs that coordinate signal generation and order execution';
COMMENT ON COLUMN orchestration_runs.run_id IS 'Unique identifier for this orchestration run (UUID)';
COMMENT ON COLUMN orchestration_runs.status IS 'Status: running, completed, failed, partial';
COMMENT ON COLUMN orchestration_runs.symbols IS 'Array of symbols included in this run';
COMMENT ON COLUMN orchestration_runs.capital IS 'Total capital allocated for this run';
COMMENT ON COLUMN orchestration_runs.num_signals IS 'Number of signals received from Signal Service';
COMMENT ON COLUMN orchestration_runs.num_orders_submitted IS 'Number of orders submitted to Execution Gateway';
COMMENT ON COLUMN orchestration_runs.num_orders_accepted IS 'Number of orders accepted by broker';
COMMENT ON COLUMN orchestration_runs.num_orders_rejected IS 'Number of orders rejected by broker';
COMMENT ON COLUMN orchestration_runs.duration_seconds IS 'Total duration of orchestration run in seconds';

COMMENT ON TABLE signal_order_mappings IS 'Maps trading signals to orders for each orchestration run';
COMMENT ON COLUMN signal_order_mappings.predicted_return IS 'Predicted return from ML model';
COMMENT ON COLUMN signal_order_mappings.target_weight IS 'Target portfolio weight from signal';
COMMENT ON COLUMN signal_order_mappings.skip_reason IS 'Reason order was not created (e.g., qty < 1 share)';
