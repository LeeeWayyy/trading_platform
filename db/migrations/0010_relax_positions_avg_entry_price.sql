-- Migration: Allow avg_entry_price = 0 only for flat positions
-- Created: 2025-12-17

DO $$
DECLARE
    old_constraint_name text;
    has_new_constraint boolean;
BEGIN
    SELECT c.conname INTO old_constraint_name
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    WHERE t.relname = 'positions'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%avg_entry_price%';

    IF old_constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE positions DROP CONSTRAINT %I', old_constraint_name);
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'positions'
          AND c.contype = 'c'
          AND c.conname = 'positions_avg_entry_price_nonnegative'
    ) INTO has_new_constraint;

    IF NOT has_new_constraint THEN
        EXECUTE 'ALTER TABLE positions ADD CONSTRAINT positions_avg_entry_price_nonnegative CHECK (avg_entry_price > 0 OR qty = 0)';
    END IF;
END $$;
