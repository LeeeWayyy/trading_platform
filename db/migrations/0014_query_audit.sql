-- Migration: 0014_query_audit.sql
-- Purpose: Query execution audit log for web console data management (P4T6)
-- Date: 2025-12-24

CREATE TABLE data_query_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    dataset VARCHAR(100) NOT NULL,
    query_fingerprint VARCHAR(64) NOT NULL,  -- SHA-256 of normalized query
    query_text TEXT NOT NULL,                 -- Full query (encrypted at application level)
    row_count INTEGER,
    duration_ms INTEGER NOT NULL,
    client_ip VARCHAR(45),
    result VARCHAR(20) NOT NULL,              -- success, error, timeout, rate_limited
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_query_audit_user ON data_query_audit(user_id);
CREATE INDEX idx_query_audit_dataset ON data_query_audit(dataset);
CREATE INDEX idx_query_audit_created ON data_query_audit(created_at DESC);

-- Retention: 90 days, cleaned by scheduled job
-- Encryption: query_text encrypted using Fernet symmetric encryption
--   Key source: QUERY_AUDIT_ENCRYPTION_KEY from secrets manager (libs/secrets)
--   Encrypt: fernet.encrypt(query_text.encode()) before INSERT
--   Decrypt: fernet.decrypt(query_text) on SELECT for authorized admin access
--   Key rotation: Quarterly via secrets rotation procedure (see docs/RUNBOOKS/secrets-migration.md)
