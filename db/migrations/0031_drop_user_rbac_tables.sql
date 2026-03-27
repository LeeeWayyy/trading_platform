-- P6T19: Drop RBAC tables — single-admin model
-- Migration order is critical: triggers → function → FK → tables
-- Re-runnable: uses DO $$ blocks for triggers, IF EXISTS for everything else.
--
-- KEPT: strategies (used by StrategyService, signal_service)
-- KEPT: audit_log (still needed for action logging)
-- KEPT: api_keys (table preserved, only FK to user_roles dropped)

-- Step 1: Drop triggers on user_strategy_access
-- DROP TRIGGER IF EXISTS ... ON <table> errors if table doesn't exist,
-- so we guard with a pg_tables check.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'user_strategy_access') THEN
        DROP TRIGGER IF EXISTS trg_strategy_access_insert_bump ON user_strategy_access;
        DROP TRIGGER IF EXISTS trg_strategy_access_delete_bump ON user_strategy_access;
        DROP TRIGGER IF EXISTS trg_strategy_access_update_bump ON user_strategy_access;
    END IF;
END
$$;

-- Step 2: Drop the trigger function
DROP FUNCTION IF EXISTS bump_session_version_on_strategy_change();

-- Step 3: Drop FK constraint on api_keys.user_id → user_roles(user_id)
-- api_keys table itself is KEPT
ALTER TABLE IF EXISTS api_keys DROP CONSTRAINT IF EXISTS fk_api_keys_user;

-- Step 4: Drop user_strategy_access (has FKs to user_roles and strategies)
DROP TABLE IF EXISTS user_strategy_access;

-- Step 5: Drop user_roles
DROP TABLE IF EXISTS user_roles;

-- Verification: strategies and audit_log should still exist
-- (no action needed — they are not touched by this migration)
