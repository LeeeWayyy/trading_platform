-- Migration 0029: Add PagerDuty to alert deliveries channel constraint
-- Purpose: Support PagerDuty notification channel (P6T17.3)
-- Date: 2026-03-12

-- Drop and re-create the channel check constraint to include 'pagerduty'
ALTER TABLE alert_deliveries
    DROP CONSTRAINT IF EXISTS alert_deliveries_channel_check;

ALTER TABLE alert_deliveries
    ADD CONSTRAINT alert_deliveries_channel_check
    CHECK (channel IN ('email', 'slack', 'sms', 'pagerduty'));
