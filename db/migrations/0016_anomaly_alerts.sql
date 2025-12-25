-- Migration: 0016_anomaly_alerts.sql
-- Purpose: Anomaly alert storage for data quality monitoring (P4T6)
-- Date: 2025-12-24

-- MUST run before 0017_alert_acknowledgments.sql due to FK dependency
CREATE TABLE data_anomaly_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset VARCHAR(100) NOT NULL,
    metric VARCHAR(100) NOT NULL,            -- row_drop, null_spike, date_gap
    severity VARCHAR(20) NOT NULL,           -- error, warning
    current_value DOUBLE PRECISION NOT NULL,
    expected_value DOUBLE PRECISION NOT NULL,
    deviation_pct DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    sync_run_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_anomaly_alerts_dataset ON data_anomaly_alerts(dataset);
CREATE INDEX idx_anomaly_alerts_created ON data_anomaly_alerts(created_at DESC);
CREATE INDEX idx_anomaly_alerts_severity ON data_anomaly_alerts(severity);
