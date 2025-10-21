# Deployment & Rollback Workflow

**Purpose:** Deploy code to staging/production and rollback if issues occur
**Prerequisites:** Code merged to main, tests passing, ADRs created for architectural changes
**Expected Outcome:** Code deployed successfully or rolled back to stable state
**Owner:** @devops-team + @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Deploy to staging:**
- After PR merged to main
- Before every production deployment (staging validation required)
- Weekly deployment schedule (if applicable)

**Deploy to production:**
- After successful staging validation (24h+ soak time)
- During designated deployment windows
- Emergency hotfixes (with approval)

**Rollback:**
- Production errors detected (>5% error rate)
- Circuit breaker tripped after deployment
- Data integrity issues
- Performance degradation (>50% latency increase)
- User-reported critical bugs

---

## Step-by-Step Process

### 1. Pre-Deployment Checklist

**Verify readiness:**
```bash
# Check CI status
gh pr view <PR-number> --json statusCheckRollup

# Verify tests passed
make test && make lint

# Check ADRs for architectural changes
ls docs/ADRs/ | grep -E "$(git log --oneline main..HEAD)"

# Review changelog/release notes
git log --oneline $(git describe --tags --abbrev=0)..HEAD
```

**Required checks:**
- [ ] All tests passing in CI
- [ ] Code reviewed and approved
- [ ] ADRs created for architectural changes
- [ ] Database migrations tested (if applicable)
- [ ] Secrets/config updated in target environment
- [ ] Rollback plan documented
- [ ] Stakeholders notified (for production)

### 2. Deploy to Staging

**Using docker-compose:**
```bash
# Pull latest code
git checkout main && git pull

# Build images
docker-compose -f docker-compose.staging.yml build

# Deploy
docker-compose -f docker-compose.staging.yml up -d

# Verify deployment
docker-compose -f docker-compose.staging.yml ps
docker-compose -f docker-compose.staging.yml logs --tail=100
```

**Verify staging health:**
```bash
# Check service health
curl https://staging.example.com/health

# Check metrics
open http://staging-grafana.example.com

# Run smoke tests
pytest tests/smoke/ --env=staging
```

### 3. Staging Validation (24h Soak)

**Monitor for 24+ hours:**
- Check error rates (target: <1%)
- Monitor latency (target: <500ms p95)
- Verify circuit breaker states (should be OPEN)
- Check reconciliation status (no drift)
- Review Grafana dashboards

**If issues found:**
- Investigate immediately
- Fix and redeploy to staging
- Restart 24h soak timer
- DO NOT proceed to production

### 4. Deploy to Production

**⚠️ Production deployment requires:**
- Successful staging validation (24h+ soak)
- Deployment window approval
- On-call engineer available
- Rollback plan ready

**Deployment steps:**
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
docker-compose -f docker-compose.prod.yml logs --tail=100
```

### 5. Post-Deployment Verification

**Immediate checks (0-5 min):**
```bash
# Health endpoints
curl https://api.example.com/health

# Check all services running
docker-compose ps | grep Up

# Review logs for errors
docker-compose logs --tail=200 | grep -i error
```

**Short-term monitoring (5-30 min):**
- Error rate <1%
- Latency within 10% of baseline
- No circuit breaker trips
- Reconciliation successful
- Order placement working

**Medium-term monitoring (30 min - 4h):**
- Monitor Grafana dashboards
- Check Sentry for new errors
- Verify paper trading functioning
- Review position reconciliation

### 6. Rollback Procedure

**When to rollback:**
- Error rate >5%
- Circuit breaker tripped
- Data integrity issues
- Critical functionality broken
- Performance degradation >50%

**Rollback steps:**
```bash
# 1. Stop new deployments
docker-compose -f docker-compose.prod.yml down

# 2. Revert to previous tag
git checkout v1.2.2  # Previous stable version

# 3. Restore database (if migrations ran)
psql -U postgres trading_db < backup_20251020_140000.sql

# 4. Redeploy previous version
docker-compose -f docker-compose.prod.yml up -d

# 5. Verify rollback successful
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
- DO NOT redeploy to prod until validated

---

## Decision Points

### Should I deploy to production during market hours?

**Deploy during market hours ONLY IF:**
- Emergency hotfix for critical bug
- Approved by stakeholders
- Rollback plan tested
- On-call engineer monitoring

**Prefer off-hours deployment:**
- Less impact if issues occur
- Market closed = no live trading affected
- Time to validate before market opens
- Easier rollback without trading interruption

**Best deployment windows:**
- Weekends (Saturday/Sunday)
- After market close (4pm ET - 9:30am ET next day)
- Avoid Monday mornings (highest trading volume)

### Should I rollback or try to fix forward?

**Rollback when:**
- Issue affects >50% of users/trades
- Data integrity at risk
- Unknown root cause
- Fix will take >1 hour
- Multiple components affected

**Fix forward when:**
- Issue affects <10% of users
- Root cause clearly identified
- Fix available and tested
- Rollback would cause data loss
- Issue isolated to non-critical feature

**When in doubt: ROLLBACK** (safer to rollback and fix properly)

### How long should staging soak period be?

**Minimum: 24 hours** for standard releases

**Extended soak (48-72h) for:**
- Major version upgrades
- Database schema changes
- New trading strategies
- Circuit breaker logic changes
- Position reconciliation changes

**Shortened soak (4-8h) ONLY for:**
- Hotfix for critical production bug
- Documentation-only changes
- UI-only changes (no backend)
- Config-only changes (reviewed)

---

## Common Issues & Solutions

### Issue: Docker Container Won't Start

**Symptom:**
```bash
$ docker-compose up -d
ERROR: Container exited with code 1
```

**Debug:**
```bash
# Check logs
docker-compose logs service-name

# Check if port already in use
lsof -i :8000

# Check environment variables
docker-compose config

# Verify image built correctly
docker images | grep trading_platform
```

**Common causes:**
- Missing environment variables
- Port conflict
- Database not ready
- Image not built

### Issue: Database Migration Failed

**Symptom:**
```
ERROR: Migration 0015 failed: relation already exists
```

**Solution:**
```bash
# Check migration status
make db-status

# Rollback migration
make db-rollback

# Fix migration script
# Re-run migration
make db-migrate

# If corrupted, restore from backup
psql -U postgres trading_db < backup_file.sql
```

### Issue: Deployment Successful But Circuit Breaker Tripped

**Symptom:**
- Deployment completed
- Services running
- Circuit breaker in TRIPPED state
- No trading occurring

**Debug:**
```bash
# Check breaker state
redis-cli GET cb:state

# Check why it tripped (logs)
docker-compose logs risk_manager | grep -i "trip"

# Check metrics
curl http://localhost:9090/metrics | grep drawdown
```

**Common causes:**
- Stale data triggered staleness check
- Initial position reconciliation failed
- Config mismatch (position limits)
- Redis state persisted from staging

**Solution:**
```bash
# If false alarm, manually reset
redis-cli SET cb:state OPEN

# If legitimate issue, investigate root cause
# Fix issue before resetting
```

---

## Examples

### Example 1: Standard Production Deployment

```bash
# Scenario: Deploy feature X to production after staging validation

# Step 1: Verify staging passed 24h soak
# (Checked Grafana, no errors, performance good)

# Step 2: Tag release
$ git tag -a v1.3.0 -m "Release v1.3.0: Add monitoring dashboards"
$ git push origin v1.3.0

# Step 3: Notify stakeholders
# Slack: "Deploying v1.3.0 to production at 6pm ET (after market close)"

# Step 4: SSH to production
$ ssh prod-server

# Step 5: Backup database
$ docker-compose exec postgres pg_dump -U postgres trading_db > backup_20251020_180000.sql

# Step 6: Deploy
$ git fetch && git checkout v1.3.0
$ docker-compose -f docker-compose.prod.yml build
$ docker-compose -f docker-compose.prod.yml up -d

# Step 7: Verify
$ curl https://api.example.com/health
{"status": "healthy", "version": "v1.3.0"}

$ docker-compose logs --tail=100
# No errors seen

# Step 8: Monitor
# Watch Grafana for 30 min
# Error rate: 0.1% ✅
# Latency: 200ms p95 ✅
# Circuit breaker: OPEN ✅

# Success! Deployment complete.
```

### Example 2: Emergency Rollback

```bash
# Scenario: Production deployment causing 10% error rate

# Step 1: Detect issue
$ curl https://api.example.com/metrics | grep error_rate
error_rate 10.5%  # ❌ Too high!

# Step 2: Decide to rollback
# Error rate >5% = immediate rollback

# Step 3: Stop services
$ ssh prod-server
$ docker-compose -f docker-compose.prod.yml down

# Step 4: Revert to previous version
$ git checkout v1.2.9  # Previous stable

# Step 5: Restore database (migrations ran)
$ psql -U postgres trading_db < backup_20251020_180000.sql

# Step 6: Redeploy
$ docker-compose -f docker-compose.prod.yml up -d

# Step 7: Verify rollback
$ curl https://api.example.com/health
{"status": "healthy", "version": "v1.2.9"}

$ curl https://api.example.com/metrics | grep error_rate
error_rate 0.2%  # ✅ Back to normal

# Step 8: Notify stakeholders
# Slack: "Rolled back v1.3.0 due to high error rate. Investigating root cause."

# Step 9: Create postmortem
$ vi docs/LESSONS_LEARNED/2025-10-20-v1.3.0-rollback.md

# Success! Production stable again.
```

---

## Validation

**How to verify deployment succeeded:**
- [ ] All services running (`docker-compose ps`)
- [ ] Health endpoints returning 200 OK
- [ ] Error rate <1%
- [ ] Latency within 10% of baseline
- [ ] Circuit breaker state is OPEN
- [ ] Reconciliation completed successfully
- [ ] Paper trading functioning
- [ ] Grafana dashboards showing healthy metrics
- [ ] No critical errors in logs

**What to check if deployment seems broken:**
- Check docker logs: `docker-compose logs --tail=200`
- Verify environment variables: `docker-compose config`
- Check database connectivity: `psql -U postgres -h localhost`
- Review recent commits: `git log -5 --oneline`
- Check disk space: `df -h`
- Verify ports available: `lsof -i :8000`

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Commit changes before deployment
- [02-git-pr.md](./02-git-pr.md) - Create PR before merge to main
- [05-testing.md](./05-testing.md) - Run tests before deployment
- [10-ci-triage.md](./10-ci-triage.md) - Handle CI failures before deploying

---

## References

**Deployment Standards:**
- [/docs/RUNBOOKS/ops.md](../../docs/RUNBOOKS/ops.md) - Operational procedures
- [/docs/GETTING_STARTED/SETUP.md](../../docs/GETTING_STARTED/SETUP.md) - Environment setup

**Monitoring:**
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090
- Sentry: (configure externally)

**Docker:**
- docker-compose: https://docs.docker.com/compose/
- Multi-stage builds: https://docs.docker.com/develop/develop-images/multistage-build/

---

**Maintenance Notes:**
- Update when deployment process changes
- Review after each incident/rollback
- Update soak time requirements based on experience
- Add new health checks as services evolve
