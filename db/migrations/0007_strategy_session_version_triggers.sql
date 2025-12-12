-- Migration: Keep sessions in sync with strategy access changes
-- Purpose: Increment user_roles.session_version whenever user_strategy_access changes
-- Date: 2025-12-10

-- Bump session_version on inserts/deletes/updates to user_strategy_access so
-- existing sessions are invalidated in the same way role changes already are.

CREATE OR REPLACE FUNCTION bump_session_version_on_strategy_change() RETURNS TRIGGER AS $$
BEGIN
    UPDATE user_roles
    SET session_version = session_version + 1,
        updated_at = NOW()
    WHERE user_id = COALESCE(NEW.user_id, OLD.user_id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Ensure clean recreation for idempotency in dev environments
DROP TRIGGER IF EXISTS trg_strategy_access_insert_bump ON user_strategy_access;
DROP TRIGGER IF EXISTS trg_strategy_access_delete_bump ON user_strategy_access;
DROP TRIGGER IF EXISTS trg_strategy_access_update_bump ON user_strategy_access;

CREATE TRIGGER trg_strategy_access_insert_bump
AFTER INSERT ON user_strategy_access
FOR EACH ROW EXECUTE FUNCTION bump_session_version_on_strategy_change();

CREATE TRIGGER trg_strategy_access_delete_bump
AFTER DELETE ON user_strategy_access
FOR EACH ROW EXECUTE FUNCTION bump_session_version_on_strategy_change();

CREATE TRIGGER trg_strategy_access_update_bump
AFTER UPDATE ON user_strategy_access
FOR EACH ROW EXECUTE FUNCTION bump_session_version_on_strategy_change();
