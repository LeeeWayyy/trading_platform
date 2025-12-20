-- Migration: 0011_create_api_keys.sql
-- Purpose: API keys, user last_active_ip, system configuration
-- Author: Codex (P4T5-C5)
-- Date: 2025-12-20

-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Add last_active_ip to user_roles (T7.4 requirement)
ALTER TABLE user_roles
    ADD COLUMN IF NOT EXISTS last_active_ip INET,
    ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_user_roles_last_active ON user_roles(last_active_at DESC);

-- 2. API Keys table with security constraints
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(255) NOT NULL,
    key_salt VARCHAR(64) NOT NULL,
    key_prefix VARCHAR(20) NOT NULL,
    scopes JSONB NOT NULL DEFAULT '[]',
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id)
        REFERENCES user_roles(user_id) ON DELETE CASCADE,
    CONSTRAINT uq_api_keys_prefix UNIQUE (key_prefix),
    CONSTRAINT chk_key_prefix_format CHECK (key_prefix ~ '^tp_live_[a-zA-Z0-9]{8}$')
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(user_id) WHERE revoked_at IS NULL;

COMMENT ON TABLE api_keys IS 'API keys for programmatic access with salted SHA-256 hashing';
COMMENT ON COLUMN api_keys.key_hash IS 'SHA-256 hash of key with per-key salt';
COMMENT ON COLUMN api_keys.key_salt IS '16-byte hex salt for this key';
COMMENT ON COLUMN api_keys.key_prefix IS 'tp_live_{first8chars} for identification';

-- 3. System Configuration table (canonical store)
CREATE TABLE IF NOT EXISTS system_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value JSONB NOT NULL,
    config_type VARCHAR(50) NOT NULL,
    updated_by VARCHAR(255) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_config_type CHECK (config_type IN ('trading_hours', 'position_limits', 'system_defaults'))
);

CREATE INDEX IF NOT EXISTS idx_system_config_key ON system_config(config_key);
CREATE INDEX IF NOT EXISTS idx_system_config_type ON system_config(config_type);

COMMENT ON TABLE system_config IS 'Admin-editable system configuration with audit trail';
