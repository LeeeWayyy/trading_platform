# Staging Deployment Runbook

This runbook covers staging environment deployment, credential management, and troubleshooting.

## Table of Contents
- [Overview](#overview)
- [Credential Management](#credential-management)
- [Deployment Process](#deployment-process)
- [Smoke Tests](#smoke-tests)
- [Troubleshooting](#troubleshooting)
- [Rollback Procedures](#rollback-procedures)

---

## Overview

**Staging environment characteristics:**
- **Purpose:** Pre-production testing with paper trading
- **Trading mode:** Paper trading ONLY (DRY_RUN=true, ALPACA_PAPER=true)
- **Credentials:** Alpaca paper trading API keys
- **Deployment:** Automated via GitHub Actions on merge to main/master
- **Monitoring:** Full observability stack (Prometheus, Grafana, Loki)

**Safety guarantees:**
- Live API keys are blocked by GitHub Environments
- DRY_RUN=true enforced at multiple levels
- Credential validation runs before deployment
- Smoke tests verify paper trading mode active

---

## Credential Management

### GitHub Environments Setup

**1. Create staging environment in GitHub:**

Go to: Repository → Settings → Environments → New environment

Name: `staging`

**2. Add environment secrets:**

Required secrets for staging environment:

| Secret Name | Value | Description |
|-------------|-------|-------------|
| `ALPACA_PAPER_API_KEY` | Your Alpaca paper API key | Paper trading API key |
| `ALPACA_PAPER_API_SECRET` | Your Alpaca paper API secret | Paper trading API secret |
| `STAGING_DB_PASSWORD` | Strong password | PostgreSQL password for staging |
| `GRAFANA_PASSWORD` | Strong password | Grafana admin password (optional) |

**3. Set environment variables:**

| Variable Name | Value | Description |
|---------------|-------|-------------|
| `DRY_RUN` | `true` | Enforces dry-run mode |
| `ENVIRONMENT` | `staging` | Environment identifier |

**4. Add protection rules (recommended):**

- ✅ Required reviewers: 1
- ✅ Wait timer: 0 minutes (staging can deploy immediately)
- ❌ Do NOT allow administrators to bypass

**5. CRITICAL: Never add these secrets to staging:**

- ❌ `ALPACA_LIVE_API_KEY` (blocked by workflow validation)
- ❌ `ALPACA_LIVE_API_SECRET` (blocked by workflow validation)
- ❌ Any production database credentials

### Credential Rotation

**Rotate paper API keys quarterly:**

1. Generate new paper API keys from Alpaca dashboard
2. Update `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET` in staging environment
3. Trigger manual deployment via GitHub Actions
4. Verify smoke tests pass
5. Revoke old API keys in Alpaca dashboard

**Rotation checklist:**

```bash
# 1. Update secrets in GitHub UI
# Settings → Environments → staging → Update secrets

# 2. Trigger deployment
# Actions → Deploy to Staging → Run workflow → main branch

# 3. Verify deployment
curl http://staging.example.com:8001/health
curl http://staging.example.com:8002/health
curl http://staging.example.com:8003/health

# 4. Check logs for any authentication errors
docker-compose -f docker-compose.staging.yml logs execution_gateway | grep -i "auth\|key"
```

---

## Deployment Process

### Automatic Deployment

Triggered automatically on:
- Merge to `main` or `master` branch
- Successful completion of CI tests

Workflow: `.github/workflows/deploy-staging.yml`

**Deployment steps:**

1. **Credential Validation** (Job 1)
   - Verify paper API credentials exist
   - Block live API keys
   - Validate DRY_RUN=true

2. **Deploy** (Job 2)
   - Pull latest Docker images from ghcr.io
   - Stop existing services gracefully
   - Start services with new images
   - Wait for health checks (120s timeout)
   - Run smoke tests
   - Capture deployment info

**View deployment status:**

```bash
# GitHub UI
https://github.com/YOUR_ORG/trading_platform/actions/workflows/deploy-staging.yml

# Check running services
docker-compose -f docker-compose.staging.yml ps

# View logs
docker-compose -f docker-compose.staging.yml logs -f
```

### Manual Deployment

**Trigger manual deployment:**

1. Go to: Actions → Deploy to Staging
2. Click "Run workflow"
3. Select branch: `main`
4. Click "Run workflow"

**Manual deployment use cases:**
- Testing configuration changes
- After credential rotation
- Rollback to previous version (select commit SHA)

---

## Smoke Tests

Smoke tests run automatically after deployment to verify system health.

**Test 1: Health Checks**

```bash
# All services must respond with 200 OK
curl -f http://localhost:8001/health  # signal_service
curl -f http://localhost:8002/health  # execution_gateway
curl -f http://localhost:8003/health  # orchestrator
```

**Test 2: Paper Trading Mode**

```bash
# Verify paper trading mode is active
curl http://localhost:8002/api/v1/config | jq '.dry_run' | grep -q "true"
curl http://localhost:8002/api/v1/config | jq '.alpaca_paper' | grep -q "true"
```

**Test 3: Service Communication**

```bash
# Verify orchestrator can reach dependencies
curl http://localhost:8003/api/v1/orchestration/status
```

**Manual smoke test checklist:**

```bash
# 1. Check all containers are running
docker-compose -f docker-compose.staging.yml ps

# 2. Verify health checks
for port in 8001 8002 8003; do
  curl -f http://localhost:$port/health && echo " ✅ Port $port" || echo " ❌ Port $port"
done

# 3. Check logs for errors
docker-compose -f docker-compose.staging.yml logs --tail=50 | grep -i "error\|critical"

# 4. Verify paper trading mode
docker-compose -f docker-compose.staging.yml exec execution_gateway env | grep -E "DRY_RUN|ALPACA_PAPER"

# Expected output:
# DRY_RUN=true
# ALPACA_PAPER=true
```

---

## Troubleshooting

### Issue: Services fail to start

**Symptoms:**
- Health checks timeout after 120s
- Containers restart repeatedly

**Diagnosis:**

```bash
# Check container status
docker-compose -f docker-compose.staging.yml ps

# View logs for failing service
docker-compose -f docker-compose.staging.yml logs signal_service
docker-compose -f docker-compose.staging.yml logs execution_gateway
docker-compose -f docker-compose.staging.yml logs orchestrator

# Check resource usage
docker stats

# Inspect specific container
docker inspect staging_signal_service
```

**Common causes:**
1. **Database connection failure:** Check `DB_PASSWORD` secret
2. **Redis connection failure:** Ensure redis container is healthy
3. **Missing API credentials:** Verify `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET`
4. **Port conflicts:** Check if ports 8001/8002/8003 are available

**Solutions:**

```bash
# Restart all services
docker-compose -f docker-compose.staging.yml restart

# Rebuild and restart specific service
docker-compose -f docker-compose.staging.yml up -d --force-recreate execution_gateway

# Check environment variables
docker-compose -f docker-compose.staging.yml config | grep -A 5 "execution_gateway:"
```

### Issue: Credential validation fails

**Symptoms:**
- Workflow fails at "validate-credentials" job
- Error: "ALPACA_PAPER_API_KEY not found"

**Solutions:**

1. Verify secrets exist in GitHub Environment:
   - Repository → Settings → Environments → staging → Secrets

2. Check secret names match exactly:
   - `ALPACA_PAPER_API_KEY` (not ALPACA_API_KEY)
   - `ALPACA_PAPER_API_SECRET` (not ALPACA_API_SECRET)

3. Ensure workflow uses correct environment:
   ```yaml
   environment: staging  # Must be present in job definition
   ```

### Issue: Live API key detected (blocked)

**Symptoms:**
- Workflow fails with error: "ALPACA_LIVE_API_KEY found in staging environment"

**This is EXPECTED BEHAVIOR - the workflow is preventing unsafe deployment.**

**Solutions:**

1. Remove live API keys from staging environment:
   - Repository → Settings → Environments → staging → Secrets
   - Delete `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_API_SECRET`

2. Ensure only paper API keys are present:
   - `ALPACA_PAPER_API_KEY`
   - `ALPACA_PAPER_API_SECRET`

3. Never add live credentials to staging environment

---

## Rollback Procedures

### Quick Rollback (Manual)

**1. Identify last good deployment:**

```bash
# Check GitHub Actions history
https://github.com/YOUR_ORG/trading_platform/actions/workflows/deploy-staging.yml

# Note commit SHA of last successful deployment
```

**2. Redeploy previous version:**

```bash
# Trigger manual workflow with previous commit
# Actions → Deploy to Staging → Run workflow → Select commit SHA
```

**3. Verify rollback:**

```bash
# Check deployment info
docker-compose -f docker-compose.staging.yml logs | grep "Commit SHA"

# Run smoke tests
curl -f http://localhost:8001/health
curl -f http://localhost:8002/health
curl -f http://localhost:8003/health
```

### Emergency Rollback (Stop services)

**If deployment causes critical issues:**

```bash
# Stop all services immediately
docker-compose -f docker-compose.staging.yml down

# Optional: preserve data volumes
docker-compose -f docker-compose.staging.yml down  # volumes preserved by default

# Optional: completely wipe environment
docker-compose -f docker-compose.staging.yml down -v  # WARNING: deletes all data
```

**Restart with previous images:**

```bash
# Pull specific version
docker pull ghcr.io/YOUR_ORG/trading-platform-signal_service:PREVIOUS_SHA
docker pull ghcr.io/YOUR_ORG/trading-platform-execution_gateway:PREVIOUS_SHA
docker pull ghcr.io/YOUR_ORG/trading-platform-orchestrator:PREVIOUS_SHA

# Tag as latest
docker tag ghcr.io/YOUR_ORG/trading-platform-signal_service:PREVIOUS_SHA \
  ghcr.io/YOUR_ORG/trading-platform-signal_service:latest

# Restart services
docker-compose -f docker-compose.staging.yml up -d
```

---

## Monitoring and Alerts

### Access Monitoring Dashboards

**Grafana:**
- URL: http://staging.example.com:3000
- Username: admin
- Password: (from `GRAFANA_PASSWORD` secret)

**Prometheus:**
- URL: http://staging.example.com:9090

**Loki (logs):**
- Access via Grafana → Explore → Loki datasource

### Key Metrics to Monitor

**Service health:**
- All services respond to `/health` with 200 OK
- Health check duration < 1 second

**Trading safety:**
- DRY_RUN=true in all logs
- ALPACA_PAPER=true confirmed
- No live API calls detected

**Error rates:**
- ERROR/CRITICAL log count < 10/hour
- HTTP 5xx responses < 1%

**Resource usage:**
- CPU < 70%
- Memory < 80%
- Disk < 85%

---

## Production Migration

**⚠️ WARNING: Production deployment requires separate workflow and environment**

**Before deploying to production:**

1. ✅ Staging tested for minimum 48 hours
2. ✅ All smoke tests passing
3. ✅ No critical errors in logs
4. ✅ Load testing completed
5. ✅ Security audit passed
6. ✅ ADR created for production deployment
7. ✅ Rollback plan documented
8. ✅ On-call engineer assigned

**Production deployment:**
- Must use separate `.github/workflows/deploy-production.yml`
- Must use `production` GitHub Environment
- Must require manual approval (2 reviewers)
- Must have 24-hour wait timer
- Must use live API credentials (separate secrets)
- Must have staged rollout plan
- Must have monitoring alerts configured

**See: `/docs/RUNBOOKS/production-deployment.md` (to be created)**

---

## Related Documentation

- [CI/CD Pipeline (P1T10)](../TASKS/P1T10_DONE.md)
- [Centralized Logging Guide](../GETTING_STARTED/LOGGING_GUIDE.md)
- [Logging Queries](./logging-queries.md)
- [Docker Build Pipeline Tests](../../tests/test_docker_build.py)

---

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2025-10-22 | Initial staging deployment runbook | Claude Code |
