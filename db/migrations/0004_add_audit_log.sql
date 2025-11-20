-- Migration: Add audit_log table for web console
-- Purpose: Track all manual actions performed via web console (orders, kill switch, strategy toggles)
-- Author: Claude Code (P2T3)
-- Date: 2024-11-17

-- Create audit_log table
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL,  -- Username from auth
    action TEXT NOT NULL,   -- Action type: 'manual_order', 'kill_switch_engage', 'kill_switch_disengage', 'strategy_toggle'
    details JSONB NOT NULL, -- Action-specific data (order params, strategy name, etc.)
    reason TEXT,            -- User-provided justification
    ip_address TEXT,        -- Client IP for security audit
    session_id TEXT         -- Session tracking
);

-- Create indexes for common query patterns
CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX idx_audit_log_user ON audit_log(user_id);
CREATE INDEX idx_audit_log_action ON audit_log(action);

-- Add comment for documentation
COMMENT ON TABLE audit_log IS 'Audit trail for all manual actions performed via web console';
COMMENT ON COLUMN audit_log.user_id IS 'Username from authentication system';
COMMENT ON COLUMN audit_log.action IS 'Action type (manual_order, kill_switch_engage, kill_switch_disengage, strategy_toggle)';
COMMENT ON COLUMN audit_log.details IS 'JSON object with action-specific details (order params, strategy name, etc.)';
COMMENT ON COLUMN audit_log.reason IS 'User-provided justification for action (required for audit compliance)';
COMMENT ON COLUMN audit_log.ip_address IS 'Client IP address for security audit';
COMMENT ON COLUMN audit_log.session_id IS 'Session ID for tracking user sessions';
