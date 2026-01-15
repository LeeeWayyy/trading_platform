# Database Migrations

## Overview

This directory contains database schema migrations using **Alembic** for schema versioning and management.

## Structure

```
db/
├── migrations/              # Current Alembic migrations (active)
│   ├── 0001_extend_orders_for_slicing.sql
│   ├── 0004_add_audit_log.sql
│   ├── 0005_update_audit_log_schema.sql
│   ├── 0006_create_rbac_tables.sql
│   ├── ...
│   └── README               # Alembic auto-generated README
├── legacy/                  # Archived pre-Alembic migrations (reference only)
│   └── migrations_pre_alembic/
│       ├── 001_create_model_registry.sql
│       ├── 002_create_execution_tables.sql
│       └── 003_create_orchestration_tables.sql
└── README.md               # This file
```

## Migration History

### Current System: Alembic (Active)

**Since:** 2025-01-08
**Location:** `db/migrations/`
**Tool:** Alembic
**Numbering:** 0001, 0004, 0005, ... (sequential with gaps)

**Run migrations:**
```bash
# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Show current version
alembic current

# Generate new migration
alembic revision -m "description"
```

### Legacy System: Manual SQL Scripts (Archived)

**Prior to:** 2025-01-08
**Location:** `db/legacy/migrations_pre_alembic/` (archived, read-only)
**Tool:** Manual SQL execution
**Numbering:** 001, 002, 003

**Why archived?**
- No version tracking (no history of which migrations were applied)
- No rollback capability
- Manual execution prone to errors
- Migrated to Alembic for better schema management

**Reference only:** These migrations have been applied to all environments and should not be re-run.

## Creating New Migrations

### Step 1: Generate Migration Template
```bash
# Auto-generate from model changes (recommended)
alembic revision --autogenerate -m "add user preferences table"

# Or create empty template (manual)
alembic revision -m "add custom index"
```

### Step 2: Edit Migration File
- Review auto-generated SQL (if using --autogenerate)
- Add data migrations if needed (not just schema)
- Test rollback logic (downgrade function)

### Step 3: Test Migration
```bash
# Apply migration locally
alembic upgrade head

# Verify schema changes
psql -U trader -d trading_db -c "\dt"

# Test rollback
alembic downgrade -1

# Verify rollback worked
psql -U trader -d trading_db -c "\dt"

# Re-apply for final verification
alembic upgrade head
```

### Step 4: Commit and Deploy
```bash
# Commit migration file
git add db/migrations/0XXX_*.sql
git commit -m "db: Add migration for user preferences table"

# CI will apply migration in staging
# Manually apply in production after testing
```

## Best Practices

### Schema Changes
- ✅ **Always generate migrations for schema changes** (never modify production DB directly)
- ✅ **Make migrations reversible** (implement both upgrade and downgrade)
- ✅ **Test rollbacks** before deploying to production
- ✅ **Use transactions** for DDL operations (PostgreSQL supports this)

### Data Migrations
- ✅ **Separate data migrations from schema migrations** (easier to rollback)
- ✅ **Handle large tables carefully** (batch updates, avoid table locks)
- ✅ **Backfill data after schema changes** (add column → backfill → add constraint)

### Migration Naming
- ✅ **Use descriptive names**: `add_user_preferences_table` (good)
- ❌ **Avoid generic names**: `update_schema` (bad)

### Production Deployment
- ✅ **Apply migrations during maintenance windows** (if downtime expected)
- ✅ **Monitor migration progress** (use `pg_stat_activity` for long-running queries)
- ✅ **Have rollback plan** (test rollback before deploying)

## Troubleshooting

### Issue: "Can't locate revision identified by 'XXXX'"
**Cause:** Alembic version table out of sync with migration files
**Fix:**
```bash
# Check current version
alembic current

# Stamp database to specific version (if known)
alembic stamp head

# Or manually fix alembic_version table
psql -U trader -d trading_db -c "UPDATE alembic_version SET version_num='XXXX';"
```

### Issue: Migration fails midway
**Cause:** SQL error, constraint violation, or data inconsistency
**Fix:**
```bash
# Check error message in Alembic output
# Fix underlying issue (add missing FK, fix data, etc.)

# Rollback to previous version
alembic downgrade -1

# Fix migration SQL
# Re-apply
alembic upgrade head
```

### Issue: Need to skip a migration
**Cause:** Migration already manually applied or not needed
**Fix:**
```bash
# Mark migration as applied without running it
alembic stamp +1

# Verify current version
alembic current
```

## Related Documents

- [Alembic Documentation](https://alembic.sqlalchemy.org/) - Official Alembic docs
- [docs/RUNBOOKS/ops.md](../docs/RUNBOOKS/ops.md) - Operations runbook

---

**Last Updated:** 2026-01-14
**Migration System:** Alembic
**Current Version:** Run `alembic current` to check
