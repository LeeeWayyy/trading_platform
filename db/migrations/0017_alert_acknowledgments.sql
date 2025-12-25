-- Migration: 0017_alert_acknowledgments.sql
-- Purpose: Acknowledgment audit for data quality alerts (P4T6)
-- Date: 2025-12-24

-- DEPENDS ON: 0016_anomaly_alerts.sql (FK reference)
CREATE TABLE data_quality_alert_acknowledgments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES data_anomaly_alerts(id),  -- FK to anomaly alerts
    dataset VARCHAR(100) NOT NULL,
    metric VARCHAR(100) NOT NULL,           -- row_drop, null_spike, date_gap
    severity VARCHAR(20) NOT NULL,          -- error, warning
    acknowledged_by VARCHAR(255) NOT NULL,  -- User ID
    acknowledged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    reason TEXT NOT NULL,
    original_alert JSONB NOT NULL,          -- Full AnomalyAlert for audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_alert_ack_alert_id ON data_quality_alert_acknowledgments(alert_id);
CREATE INDEX idx_alert_ack_dataset ON data_quality_alert_acknowledgments(dataset);
CREATE INDEX idx_alert_ack_acknowledged_at ON data_quality_alert_acknowledgments(acknowledged_at);
