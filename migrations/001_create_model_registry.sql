-- Migration 001: Create Model Registry Table
-- Purpose: Track trained ML models for signal generation
-- Date: 2025-01-17
-- Related: ADR-0004 (Signal Service Architecture)

-- ============================================================================
-- Model Registry Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_registry (
    -- Primary key
    id SERIAL PRIMARY KEY,

    -- Model identification
    strategy_name TEXT NOT NULL,            -- e.g., "alpha_baseline", "alpha_v2"
    version TEXT NOT NULL,                  -- e.g., "v1.0.0", "20250117-143022"

    -- MLflow integration (optional, for traceability)
    mlflow_run_id TEXT,                     -- MLflow run ID for full experiment details
    mlflow_experiment_id TEXT,              -- MLflow experiment ID

    -- Model storage
    model_path TEXT NOT NULL,               -- Absolute path to model file or MLflow URI
                                            -- Examples:
                                            --   "/path/to/artifacts/models/alpha_baseline.txt"
                                            --   "runs:/<run_id>/model"

    -- Deployment status
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'testing', 'failed')),
                                            -- active: Currently serving traffic
                                            -- inactive: Retired or superseded
                                            -- testing: Under validation (paper trading)
                                            -- failed: Failed to load or validation

    -- Model metadata
    performance_metrics JSONB,              -- Backtest metrics: IC, Sharpe, drawdown, etc.
                                            -- Example: {"ic": 0.082, "sharpe": 1.45, "max_drawdown": -0.12}

    config JSONB,                           -- Model hyperparameters and training config
                                            -- Example: {"learning_rate": 0.05, "max_depth": 6, ...}

    -- Audit trail
    created_at TIMESTAMP DEFAULT NOW(),     -- When model was registered
    activated_at TIMESTAMP,                 -- When model was activated (if ever)
    deactivated_at TIMESTAMP,               -- When model was deactivated (if applicable)
    created_by TEXT DEFAULT 'system',       -- Who/what registered the model

    -- Additional notes
    notes TEXT,                             -- Free-form deployment notes
                                            -- Example: "Trained on 5 years data, validated on 6 months"

    -- Constraints
    UNIQUE(strategy_name, version)          -- Prevent duplicate versions for same strategy
);

-- ============================================================================
-- Indexes for Performance
-- ============================================================================

-- Fast lookup of active model for a strategy
CREATE INDEX idx_model_registry_active
    ON model_registry(strategy_name, status)
    WHERE status = 'active';

-- Fast lookup of models by activation time (for rollback queries)
CREATE INDEX idx_model_registry_activated_at
    ON model_registry(strategy_name, activated_at DESC NULLS LAST);

-- Fast lookup by MLflow run ID (for traceability)
CREATE INDEX idx_model_registry_mlflow_run
    ON model_registry(mlflow_run_id)
    WHERE mlflow_run_id IS NOT NULL;

-- ============================================================================
-- Helper Function: Activate Model
-- ============================================================================

-- Atomically activate a model (deactivates all others for same strategy)
CREATE OR REPLACE FUNCTION activate_model(
    p_strategy_name TEXT,
    p_version TEXT
)
RETURNS VOID AS $$
BEGIN
    -- Deactivate all currently active models for this strategy
    UPDATE model_registry
    SET status = 'inactive',
        deactivated_at = NOW()
    WHERE strategy_name = p_strategy_name
      AND status = 'active';

    -- Activate the specified model
    UPDATE model_registry
    SET status = 'active',
        activated_at = NOW(),
        deactivated_at = NULL  -- Clear deactivation timestamp
    WHERE strategy_name = p_strategy_name
      AND version = p_version;

    -- Verify exactly one row was updated
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Model not found: strategy=%, version=%', p_strategy_name, p_version;
    END IF;

    -- Log the activation
    RAISE NOTICE 'Activated model: strategy=%, version=%', p_strategy_name, p_version;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Helper Function: Get Active Model
-- ============================================================================

-- Get metadata for currently active model
CREATE OR REPLACE FUNCTION get_active_model(
    p_strategy_name TEXT
)
RETURNS TABLE (
    id INT,
    version TEXT,
    model_path TEXT,
    performance_metrics JSONB,
    activated_at TIMESTAMP
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        mr.id,
        mr.version,
        mr.model_path,
        mr.performance_metrics,
        mr.activated_at
    FROM model_registry mr
    WHERE mr.strategy_name = p_strategy_name
      AND mr.status = 'active'
    ORDER BY mr.activated_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Helper Function: Get Model History
-- ============================================================================

-- Get deployment history for a strategy (for rollback)
CREATE OR REPLACE FUNCTION get_model_history(
    p_strategy_name TEXT,
    p_limit INT DEFAULT 10
)
RETURNS TABLE (
    version TEXT,
    status TEXT,
    performance_metrics JSONB,
    activated_at TIMESTAMP,
    deactivated_at TIMESTAMP
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        mr.version,
        mr.status,
        mr.performance_metrics,
        mr.activated_at,
        mr.deactivated_at
    FROM model_registry mr
    WHERE mr.strategy_name = p_strategy_name
    ORDER BY
        CASE
            WHEN mr.status = 'active' THEN 1
            WHEN mr.activated_at IS NOT NULL THEN 2
            ELSE 3
        END,
        mr.activated_at DESC NULLS LAST,
        mr.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Table Comments (Documentation)
-- ============================================================================

COMMENT ON TABLE model_registry IS
'Registry of trained ML models for signal generation. Tracks model versions, deployment status, and performance metrics.';

COMMENT ON COLUMN model_registry.strategy_name IS
'Strategy identifier (e.g., "alpha_baseline"). Each strategy can have multiple versions.';

COMMENT ON COLUMN model_registry.version IS
'Model version (e.g., "v1.0.0", "20250117"). Must be unique within a strategy.';

COMMENT ON COLUMN model_registry.model_path IS
'Absolute path to model file or MLflow URI. Service loads model from this location.';

COMMENT ON COLUMN model_registry.status IS
'Deployment status: active (serving traffic), inactive (retired), testing (validation), failed (load error).';

COMMENT ON COLUMN model_registry.performance_metrics IS
'Backtest metrics in JSON format. Example: {"ic": 0.082, "sharpe": 1.45, "max_drawdown": -0.12}.';

COMMENT ON COLUMN model_registry.config IS
'Model hyperparameters and training config in JSON format.';

COMMENT ON FUNCTION activate_model(TEXT, TEXT) IS
'Atomically activate a model for a strategy. Deactivates all other models for same strategy.';

COMMENT ON FUNCTION get_active_model(TEXT) IS
'Get metadata for currently active model for a strategy.';

COMMENT ON FUNCTION get_model_history(TEXT, INT) IS
'Get deployment history for a strategy, ordered by activation time. Useful for rollbacks.';

-- ============================================================================
-- Migration Complete
-- ============================================================================

-- Verify table was created
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'model_registry'
    ) THEN
        RAISE NOTICE 'Migration 001 completed successfully: model_registry table created';
    ELSE
        RAISE EXCEPTION 'Migration 001 failed: model_registry table not found';
    END IF;
END $$;
