-- Migration 0034: Persist source and issue scope on quality alert acknowledgments
-- Purpose: Phase 5 acceptance criterion — "Quality acknowledgments are persisted
--          server-side with actor, time, source, and issue scope" (docs/TASKS/2026-05-03-data-page-ui-optimization-plan.md)
-- Date: 2026-05-12
--
-- Changes:
--   * Relax alert_id to TEXT (and drop the FK to data_anomaly_alerts) so
--     manifest-derived signal ids ("alpaca-sip-manifest-pairing", etc.) and
--     mock string ids can be acknowledged today, before alert rows are
--     materialised in a dedicated table.
--   * Add explicit source and issue_scope columns so the persisted record
--     carries the full set required by the plan AC.
--   * Make original_alert default to an empty object so callers that do not
--     have a full alert payload (e.g. manifest-derived signals) can still
--     persist acknowledgments.

ALTER TABLE data_quality_alert_acknowledgments
    DROP CONSTRAINT IF EXISTS data_quality_alert_acknowledgments_alert_id_fkey;

ALTER TABLE data_quality_alert_acknowledgments
    ALTER COLUMN alert_id TYPE TEXT USING alert_id::text;

ALTER TABLE data_quality_alert_acknowledgments
    ADD COLUMN IF NOT EXISTS source VARCHAR(64) NOT NULL DEFAULT 'unknown';

ALTER TABLE data_quality_alert_acknowledgments
    ADD COLUMN IF NOT EXISTS issue_scope JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE data_quality_alert_acknowledgments
    ALTER COLUMN original_alert SET DEFAULT '{}'::jsonb;
