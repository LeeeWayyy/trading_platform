# Operations Workflows (Deployment, Rollback, CI Triage)

**Purpose:** Production operations, deployment management, and CI/CD troubleshooting
**When:** Production deployments, rollbacks, CI failures
**Tools:** docker-compose, gh CLI, pytest, CI logs

---

## Quick Reference

| Workflow | When | Urgency | See Section |
|----------|------|---------|-------------|
| **Deployment** | After PR merge | Normal | §1 |
| **Rollback** | Production issues detected | CRITICAL | §2 |
| **CI Triage** | GitHub Actions failing | HIGH | §3 |

---

## §1: Deployment Workflow

### Pre-Deployment Checklist

- [ ] PR approved and merged to master
- [ ] CI passing on master branch
- [ ] Deep review completed
- [ ] Database migrations tested locally
- [ ] Rollback plan documented

### Deployment Process

```bash
# 1. Pull latest master
git checkout master && git pull

# 2. Run migrations (if any)
cd db && alembic upgrade head

# 3. Restart services
docker-compose down
docker-compose up -d

# 4. Verify health
curl http://localhost:8001/health  # Signal service
curl http://localhost:8002/health  # Execution gateway
curl http://localhost:8003/health  # Orchestrator

# 5. Monitor logs
docker-compose logs -f --tail=100
```

### Post-Deployment Verification

```bash
# Check service status
docker-compose ps

# Run smoke tests
pytest tests/e2e/test_smoke.py

# Monitor metrics
open http://localhost:3000  # Grafana

# Check circuit breaker state
redis-cli GET cb:state  # Should be OPEN (normal)
```

---

## §2: Rollback Workflow

**CRITICAL: Act quickly to minimize impact**

### When to Rollback

**Immediate rollback if:**
- Service crashes repeatedly
- Data corruption detected
- Critical bug in production
- Circuit breaker tripped unexpectedly
- Performance degradation >50%

**Monitor and defer if:**
- Minor UI issues
- Non-critical warnings in logs
- Performance degradation <20%

### Rollback Process

```bash
# 1. Identify last known good commit
git log --oneline -10

# 2. Checkout previous version
git checkout <last-good-commit>

# 3. Rollback database (if migrations ran)
cd db && alembic downgrade -1

# 4. Restart services
docker-compose down
docker-compose up -d

# 5. Verify rollback successful
curl http://localhost:8001/health
pytest tests/e2e/test_smoke.py

# 6. Document incident
vim docs/INCIDENTS/YYYY-MM-DD-incident.md
```

### Rollback Database Migrations

```bash
# Check current revision
alembic current

# Downgrade one revision
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade abc123

# Verify data integrity after rollback
pytest tests/integration/test_db_integrity.py
```

### Post-Rollback Actions

1. **Create incident report** in `docs/INCIDENTS/`
2. **Notify team** of rollback
3. **Create hotfix branch** to address issue
4. **Test hotfix thoroughly** before redeployment
5. **Update runbooks** if new failure mode discovered

---

## §3: CI Triage Workflow

**When GitHub Actions fail**

### Quick Triage Steps

```bash
# 1. Check CI logs in GitHub
gh run view <run-id>
gh run view <run-id> --log-failed

# 2. Identify failure type (see table below)

# 3. Reproduce locally
make ci-local

# 4. Fix and push
git add <files>
git commit -m "fix: Address CI failure in..."
git push
```

### CI Failure Types

| Failure | Likely Cause | Fix |
|---------|--------------|-----|
| **Mypy type errors** | Missing type hints, incorrect types | Add/fix type annotations |
| **Ruff lint errors** | Code style, unused imports | Run `make fmt`, fix issues |
| **Test failures** | Broken tests, flaky tests | Fix tests, check for race conditions |
| **Import errors** | Missing dependencies, wrong paths | Check `pyproject.toml`, verify imports |
| **Timeout** | Slow tests, infinite loops | Optimize tests, check for deadlocks |
| **Coverage drop** | New code without tests | Add tests for uncovered code |

### Common CI Issues

**Issue: Mypy errors only in CI**
```bash
# CI uses --strict, local might not
poetry run mypy libs/ apps/ strategies/ --strict

# Fix common issues:
# - Add return type hints
# - Use Optional[T] for nullable
# - Use | None instead of Optional in Python 3.10+
```

**Issue: Tests pass locally, fail in CI**
```bash
# Possible causes:
# 1. Test order dependency
pytest --random-order tests/

# 2. Missing test fixture cleanup
# Add proper teardown in conftest.py

# 3. Timezone differences
# Use UTC explicitly: datetime.now(timezone.utc)
```

**Issue: Flaky tests**
```bash
# Run test 10 times to reproduce
pytest tests/path/to/test.py::test_name --count=10

# Common causes:
# - Async race conditions → Add proper awaits
# - Time-dependent tests → Mock datetime
# - External service calls → Mock HTTP requests
```

### Debugging CI Failures

**View logs:**
```bash
# Latest run
gh run list --limit 5
gh run view <run-id> --log

# Failed jobs only
gh run view <run-id> --log-failed

# Specific job
gh run view <run-id> --job <job-id> --log
```

**Re-run failed jobs:**
```bash
# Re-run all failed jobs
gh run rerun <run-id> --failed

# Re-run entire workflow
gh run rerun <run-id>
```

**Download artifacts:**
```bash
# Download coverage report, test results
gh run download <run-id>
```

---

## Common Scenarios

### Scenario: Deployment Causes Production Issue

```bash
# 1. IMMEDIATE: Rollback (§2)
git checkout <last-good-commit>
docker-compose down && docker-compose up -d

# 2. Verify rollback successful
curl http://localhost:8001/health
pytest tests/e2e/test_smoke.py

# 3. Create incident report
vim docs/INCIDENTS/2025-11-08-order-placement-failure.md

# 4. Create hotfix branch
git checkout -b hotfix/order-placement-bug
# Fix issue, test thoroughly
make ci-local
git push

# 5. Fast-track hotfix PR
gh pr create --title "hotfix: Fix order placement..."
# After approval: deploy hotfix
```

### Scenario: CI Failing on PR

```bash
# 1. Check CI logs
gh pr view <pr-number> --web
# Click on failing check

# 2. Reproduce locally
make ci-local

# 3. Fix issues
# mypy errors → add types
# ruff errors → make fmt
# test failures → fix tests

# 4. Push fix
git add <files>
git commit -m "fix(ci): Address type errors..."
git push

# 5. Wait for CI to pass
gh pr checks <pr-number> --watch
```

### Scenario: Database Migration Rollback

```bash
# After failed migration
alembic current  # Check current version

# Downgrade migration
alembic downgrade -1

# Verify data integrity
pytest tests/integration/test_db_schema.py

# Document migration issue
vim docs/INCIDENTS/migration-rollback.md

# Fix migration script
vim db/versions/<migration_file>.py

# Test migration locally
alembic upgrade head
alembic downgrade -1  # Test rollback
alembic upgrade head   # Re-apply
```

---

## Runbooks

### Kill Switch Activation

**CRITICAL: Use only for emergencies**

**NOTE:** `make kill-switch` not yet implemented. Use manual procedure:

```bash
# 1. Set kill switch flag in Redis
redis-cli SET killswitch:active true

# 2. Verify activation
redis-cli GET killswitch:active  # Should be "true"

# 3. Manually cancel open orders via broker dashboard or API
# (automated cancellation pending implementation - see P1 backlog)

# 4. Trip circuit breaker to block new signals
redis-cli SET cb:state TRIPPED
redis-cli SET cb:trip_reason "Manual kill switch activation"

# 5. Check status
docker-compose logs -f | grep -i "kill\|circuit"

# 6. Deactivate (after issue resolved)
redis-cli SET killswitch:active false
redis-cli SET cb:state OPEN
```

### Circuit Breaker Trip

```bash
# Check breaker state
redis-cli GET cb:state  # OPEN (normal) or TRIPPED

# If TRIPPED, check reason
redis-cli GET cb:trip_reason

# Review conditions before recovery
# 1. Drawdown normalized?
# 2. Broker API healthy?
# 3. Data feeds current?

# Reset breaker (manual approval required)
redis-cli SET cb:state OPEN
redis-cli SET cb:last_reset "$(date -Iseconds)"
```

### Service Health Check

```bash
# Check all services
docker-compose ps

# Check individual service logs
docker-compose logs signal_service --tail=50
docker-compose logs execution_gateway --tail=50

# Restart unhealthy service
docker-compose restart signal_service

# Full restart
docker-compose down && docker-compose up -d
```

---

## Validation Checklists

**Deployment complete:**
- [ ] All services healthy
- [ ] Smoke tests passing
- [ ] Logs show no errors
- [ ] Metrics normal in Grafana
- [ ] Circuit breaker state OPEN

**Rollback complete:**
- [ ] Rollback version deployed
- [ ] Database downgraded (if needed)
- [ ] Services healthy
- [ ] Incident report created
- [ ] Hotfix plan documented

**CI issue resolved:**
- [ ] Issue reproduced locally
- [ ] Fix implemented and tested
- [ ] CI passing on PR
- [ ] No new issues introduced

---

## See Also

- [01-git.md](./01-git.md) - Creating PRs
- [04-development.md](./04-development.md) - Testing and debugging
- [ops.md](../../RUNBOOKS/ops.md) - Operational procedures
- [INCIDENTS/](../../INCIDENTS/) - Past incidents for reference
