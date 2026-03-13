-- Migration 0028: Add 'researcher' to user_roles CHECK constraint
-- Purpose: Allow RESEARCHER role assignment via admin UI and ops tooling
-- Prerequisite for T16.2 (User Management / RBAC Admin)

-- Drop and re-create the CHECK constraint idempotently
ALTER TABLE user_roles DROP CONSTRAINT IF EXISTS valid_role;
ALTER TABLE user_roles ADD CONSTRAINT valid_role CHECK (role IN ('viewer', 'researcher', 'operator', 'admin'));
