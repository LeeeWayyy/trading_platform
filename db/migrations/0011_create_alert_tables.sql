-- Migration: Create alert tables for alert delivery service (C3.1)
-- Created: 2025-12-20

-- Extension guard for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Catalog of alert rules
CREATE TABLE IF NOT EXISTS alert_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    condition_type VARCHAR(50) NOT NULL,
    threshold_value NUMERIC NOT NULL,
    comparison VARCHAR(10) NOT NULL,
    channels JSONB NOT NULL DEFAULT '[]'::JSONB,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Alert instances (triggered events)
CREATE TABLE IF NOT EXISTS alert_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_value NUMERIC,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(255),
    acknowledged_note TEXT,
    routed_channels JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_events_rule_id ON alert_events (rule_id);
CREATE INDEX IF NOT EXISTS idx_alert_events_triggered_at ON alert_events (triggered_at);

-- Delivery attempts per channel
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES alert_events(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL,
    recipient TEXT NOT NULL,
    dedup_key VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    poison_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT alert_deliveries_channel_check CHECK (channel IN ('email', 'slack', 'sms')),
    CONSTRAINT alert_deliveries_status_check CHECK (status IN ('pending', 'delivered', 'failed', 'poison')),
    CONSTRAINT alert_deliveries_attempts_check CHECK (attempts >= 0 AND attempts <= 3)
);

CREATE INDEX IF NOT EXISTS idx_alert_deliveries_alert_id ON alert_deliveries (alert_id);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_status ON alert_deliveries (status);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_poison ON alert_deliveries (poison_at) WHERE status = 'poison';
