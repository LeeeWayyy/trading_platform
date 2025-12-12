-- Migration: Create RBAC tables for web console
-- Purpose: Persist user roles, strategies, and strategy-level grants
-- Author: Codex (T6.1a)
-- Date: 2025-12-10

-- user_roles: authoritative source of role + session invalidation version
CREATE TABLE IF NOT EXISTS user_roles (
    user_id VARCHAR(255) PRIMARY KEY,
    role VARCHAR(20) NOT NULL DEFAULT 'viewer',
    session_version INTEGER NOT NULL DEFAULT 1,
    updated_by VARCHAR(255),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT valid_role CHECK (role IN ('viewer', 'operator', 'admin'))
);

-- strategies catalog
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- per-user strategy access grants
CREATE TABLE IF NOT EXISTS user_strategy_access (
    user_id VARCHAR(255) NOT NULL,
    strategy_id VARCHAR(50) NOT NULL,
    granted_by VARCHAR(255) NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, strategy_id),
    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES user_roles(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_strategy FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id) ON DELETE CASCADE
);

-- indexes for common lookups
CREATE INDEX IF NOT EXISTS idx_user_strategy_user ON user_strategy_access (user_id);
CREATE INDEX IF NOT EXISTS idx_user_strategy_strategy ON user_strategy_access (strategy_id);
