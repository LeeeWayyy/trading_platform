-- Migration: Create workspace_state table for workspace persistence (P6T1)
--
-- Purpose:
-- Persist per-user workspace state (grid column/order/sort/filter).
-- Enables state roaming across devices with schema versioning.
--
-- Created: 2026-01-13
-- Author: Codex CLI

CREATE TABLE IF NOT EXISTS workspace_state (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_key TEXT NOT NULL,        -- e.g., 'grid.positions_grid'
    state_json JSONB NOT NULL,          -- Grid state (columns, sort, filter)
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT workspace_state_user_key_unique UNIQUE (user_id, workspace_key),
    CONSTRAINT workspace_state_size_limit CHECK (octet_length(state_json::text) <= 65536)
);

-- Index for user lookups
CREATE INDEX IF NOT EXISTS idx_workspace_state_user_id ON workspace_state(user_id);

-- Trigger for updated_at
CREATE OR REPLACE FUNCTION update_workspace_state_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workspace_state_updated_at
    BEFORE UPDATE ON workspace_state
    FOR EACH ROW
    EXECUTE FUNCTION update_workspace_state_timestamp();
