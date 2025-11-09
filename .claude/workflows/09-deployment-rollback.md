# Deployment & Rollback Workflow

**Purpose:** Deploy code to staging/production and rollback if issues occur
**When:** After PR merged to main, emergency hotfixes
**Prerequisites:** Code merged, tests passing, ADRs created
**Expected Outcome:** Code deployed successfully or rolled back to stable state

---

## Quick Reference

**Testing:** See [Test Commands Reference](./_common/test-commands.md)
**Git:** See [Git Commands Reference](./_common/git-commands.md)

---

## When to Use This Workflow

**Deploy to staging:**
- After PR merged to main
- Before every production deployment

**Deploy to production:**
- After successful staging validation (24h+ soak)
- During designated deployment windows
- Emergency hotfixes (with approval)

**Rollback:**
- Error rate >5%
- Circuit breaker tripped after deployment
- Data integrity issues
- >50% latency increase

---

## Step-by-Step Process

### 1. Pre-Deployment Checklist

```bash
# Check CI status
gh pr view <PR-number> --json statusCheckRollup

# Verify tests passed
make test && make lint

# Review changelog
git log --oneline $(git describe --tags --abbrev=0)..HEAD
```

**Required checks:**
- [ ] All tests passing in CI
- [ ] Code reviewed and approved
- [ ] ADRs created for architectural changes
- [ ] Database migrations tested
- [ ] Secrets/config updated
- [ ] Rollback plan documented
- [ ] Stakeholders notified (for production)

### 2. Deploy to Staging

```bash
# Pull latest code
git checkout main && git pull

# Build and deploy
docker-compose -f docker-compose.staging.yml build
docker-compose -f docker-compose.staging.yml up -d

# Verify deployment
docker-compose -f docker-compose.staging.yml ps
docker-compose -f docker-compose.staging.yml logs --tail=100
```

**Verify staging health:**
```bash
curl https://staging.example.com/health
pytest tests/smoke/ --env=staging
```

### 3. Staging Validation (24h Soak)

**Monitor for 24+ hours:**
- Error rates (<1%)
- Latency (<500ms p95)
- Circuit breaker states (OPEN)
- Reconciliation status
- Grafana dashboards

**If issues found:** Fix, redeploy to staging, restart 24h timer

### 4. Deploy to Production

**⚠️ Production deployment requires:**
- Successful staging validation (24h+ soak)
- Deployment window approval
- On-call engineer available
- Rollback plan ready

```bash
# Tag release
git tag -a v1.2.3 -m "Release v1.2.3: Add feature X"
git push origin v1.2.3

# Pull on production server
ssh prod-server
cd /opt/trading_platform
git fetch && git checkout v1.2.3

# Backup current state
docker-compose exec postgres pg_dump -U postgres trading_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Run migrations (if applicable)
make db-migrate

# Deploy
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d

# Verify deployment
docker-compose -f docker-compose.prod.yml ps
```

### 5. Post-Deployment Verification

**Immediate checks (0-5 min):**
```bash
curl https://api.example.com/health
docker-compose ps | grep Up
docker-compose logs --tail=200 | grep -i error
```

**Short-term monitoring (5-30 min):**
- Error rate <1%
- Latency within 10% of baseline
- No circuit breaker trips
- Reconciliation successful

**Medium-term monitoring (30 min - 4h):**
- Monitor Grafana dashboards
- Check Sentry for new errors
- Verify paper trading functioning

### 6. Rollback Procedure

**When to rollback:**
- Error rate >5%
- Circuit breaker tripped
- Data integrity issues
- Critical functionality broken

```bash
# 1. Stop services
docker-compose -f docker-compose.prod.yml down

# 2. Revert to previous tag
git checkout v1.2.2  # Previous stable

# 3. Restore database (if migrations ran)
psql -U postgres trading_db < backup_20251020_140000.sql

# 4. Redeploy previous version
docker-compose -f docker-compose.prod.yml up -d

# 5. Verify rollback
curl https://api.example.com/health
docker-compose logs --tail=100

# 6. Notify stakeholders
# Send alert: "Production rolled back to v1.2.2 due to [reason]"
```

**Post-rollback:**
- Document what went wrong
- Create postmortem (see LESSONS_LEARNED/)
- Fix issue in new branch
- Redeploy to staging for validation

---

## Decision Points

### Deploy During Market Hours?

**Deploy during market hours ONLY IF:**
- Emergency hotfix for critical bug
- Approved by stakeholders
- Rollback plan tested

**Prefer off-hours deployment:**
- After market close (4pm ET - 9:30am ET)
- Weekends (Saturday/Sunday)
- Avoid Monday mornings

### Rollback or Fix Forward?

**Rollback when:**
- Issue affects >50% of users/trades
- Data integrity at risk
- Unknown root cause
- Fix will take >1 hour

**Fix forward when:**
- Issue affects <10% of users
- Root cause clearly identified
- Fix available and tested

**When in doubt: ROLLBACK**

### Staging Soak Period?

**Minimum: 24 hours** for standard releases

**Extended soak (48-72h) for:**
- Major version upgrades
- Database schema changes
- Circuit breaker logic changes

**Shortened soak (4-8h) ONLY for:**
- Hotfix for critical production bug
- Documentation-only changes
- Config-only changes

---

## Common Issues

### Docker Container Won't Start

```bash
# Check logs
docker-compose logs service-name

# Check if port in use
lsof -i :8000

# Verify environment variables
docker-compose config
```

**Common causes:** Missing env vars, port conflict, database not ready

### Circuit Breaker Tripped After Deployment

```bash
# Check breaker state
redis-cli GET cb:state

# Check why it tripped
docker-compose logs risk_manager | grep -i "trip"

# If false alarm, reset
redis-cli SET cb:state OPEN
```

---

## Validation

**How to verify deployment succeeded:**
- [ ] All services running (`docker-compose ps`)
- [ ] Health endpoints returning 200 OK
- [ ] Error rate <1%
- [ ] Latency within 10% of baseline
- [ ] Circuit breaker state is OPEN
- [ ] Reconciliation completed
- [ ] No critical errors in logs

---

## Related Workflows

- [01-git.md](./01-git.md) - Commit before deployment
- [01-git.md](./01-git.md) - Create PR before merge
- [05-testing.md](./05-testing.md) - Run tests before deployment
- [10-ci-triage.md](./10-ci-triage.md) - Handle CI failures

---

## References

- [/docs/RUNBOOKS/ops.md](../../docs/RUNBOOKS/ops.md) - Operational procedures
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090
- docker-compose: https://docs.docker.com/compose/
