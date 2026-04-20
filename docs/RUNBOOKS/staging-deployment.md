# Staging Image Smoke Test Runbook

> **Scope note (issue #164):** The workflow `.github/workflows/deploy-staging.yml`
> (historical filename) is a **post-build image smoke test**, not a real
> deployment. It runs on an ephemeral `ubuntu-latest` GitHub runner: it pulls
> the latest images from GHCR, starts them with `docker-compose`, hits
> `/health` and `/api/v1/config` on `localhost`, then the runner is discarded.
> No remote host / cluster / self-hosted runner is involved. A real staging
> deployment would require additional infrastructure (SSH target, self-hosted
> runner, or `kubectl apply`) and is explicitly out of scope for this workflow.
> Treat passing runs as "images start cleanly and enforce paper-trading mode",
> **not** as "release to a persistent staging environment".

This runbook covers the staging-image smoke test workflow, credential
management for it, and troubleshooting.

## Table of Contents
- [Overview](#overview)
- [Credential Management](#credential-management)
- [Smoke Test Process](#smoke-test-process)
- [Smoke Tests](#smoke-tests)
- [Troubleshooting](#troubleshooting)
- [Testing Previous Image Versions](#testing-previous-image-versions)
- [Monitoring and Alerts](#monitoring-and-alerts)

---

## Overview

**Workflow characteristics:**
- **Purpose:** Post-build smoke test of the staging image set on an ephemeral
  GitHub runner. Catches image-level regressions and paper-trading-mode drift.
  NOT a release to a persistent staging environment.
- **Trading mode:** Paper trading ONLY (DRY_RUN=true, ALPACA_PAPER=true)
- **Credentials:** Alpaca paper trading API keys (paper-only; live keys blocked)
- **Trigger:** Runs via GitHub Actions on merge to main/master, or manually
- **Runtime:** `ubuntu-latest` ephemeral runner; compose stack discarded on
  runner teardown

**Safety guarantees:**
- Live API keys are blocked by GitHub Environments
- DRY_RUN=true enforced at multiple levels
- Credential validation runs before the smoke test
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
| `ALPACA_PAPER_API_KEY` | Your Alpaca paper API key | Paper trading API key (mapped to `ALPACA_API_KEY_ID` in containers) |
| `ALPACA_PAPER_API_SECRET` | Your Alpaca paper API secret | Paper trading API secret (mapped to `ALPACA_API_SECRET_KEY`) |
| `STAGING_DB_PASSWORD` | Strong password | PostgreSQL password for staging |
| `GRAFANA_PASSWORD` | Strong password | Grafana admin password (optional) |

**3. Set environment variables:**

| Variable Name | Value | Description |
|---------------|-------|-------------|
| `DRY_RUN` | `true` | Enforces dry-run mode |
| `ENVIRONMENT` | `staging` | Environment identifier |

**4. Add protection rules (recommended):**

- ✅ Required reviewers: 1
- ✅ Wait timer: 0 minutes (smoke tests can run immediately)
- ❌ Do NOT allow administrators to bypass

**5. CRITICAL: Never add these secrets to staging:**

- ❌ `ALPACA_LIVE_API_KEY` (blocked by workflow validation)
- ❌ `ALPACA_LIVE_API_SECRET` (blocked by workflow validation)
- ❌ Any production database credentials

### Credential Rotation

**Rotate paper API keys quarterly:**

1. Generate new paper API keys from Alpaca dashboard
2. Update `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET` in staging environment
3. Trigger manual smoke test via GitHub Actions
4. Verify smoke tests pass on the runner (check workflow logs)
5. Revoke old API keys in Alpaca dashboard

**Rotation checklist:**

```bash
# 1. Update secrets in GitHub UI
# Settings → Environments → staging → Update secrets

# 2. Trigger smoke test
# Actions → Staging Image Smoke Test → Run workflow → main branch

# 3. Verify the run (no persistent host exists — check Actions UI)
#    Visit: Actions → Staging Image Smoke Test → latest run
#    Confirm `validate-credentials` and `smoke-test` jobs are green.

# 4. Inspect authentication in the runner's step logs
#    In the smoke-test job, open the "Show container logs" / "Collect logs"
#    step and grep for auth/key errors in the execution_gateway output.
```

**Note:** There is no persistent staging host (`staging.example.com` is not a
real endpoint). Rotation is verified purely through the GitHub Actions
workflow logs on the ephemeral runner.

---

## Smoke Test Process

### Automatic Run

Triggered automatically on:
- Merge to `main` or `master` branch
- Successful completion of CI tests

Workflow: `.github/workflows/deploy-staging.yml`

**Steps:**

1. **Credential Validation** (Job 1: `validate-credentials`)
   - Verify paper API credentials exist
   - Block live API keys
   - Validate DRY_RUN=true

2. **Smoke Test** (Job 2: `smoke-test`)
   - Pull latest Docker images from ghcr.io
   - Bring compose stack up on the runner
   - Wait for health checks (120s timeout)
   - Hit `/health` and `/api/v1/config` on localhost
   - Capture image info; runner + stack discarded on job end

> **Reminder:** Job 2 runs on the GitHub runner, not on a remote host. A
> passing run means "images start and enforce paper mode", not "a persistent
> staging environment was updated".

**View smoke-test status (automatic and manual runs):**

```bash
# GitHub Actions UI is the ONLY place to view smoke-test status.
# The runner is ephemeral — there is no persistent host to shell into.
https://github.com/YOUR_ORG/trading_platform/actions/workflows/deploy-staging.yml
```

Open the latest workflow run to see:
- `validate-credentials` job status
- `smoke-test` job status, including per-step logs and container output
  (`docker compose ps`, `docker compose logs`, captured inside the job).

**Local reproduction (optional):**

If you want to reproduce the smoke test on your workstation against the same
images the workflow pulls, you can run a local compose stack. These commands
target *your local Docker daemon*, not any remote staging host:

```bash
# Pull the images the workflow uses
docker compose -f docker-compose.staging.yml pull

# Bring the stack up locally
docker compose -f docker-compose.staging.yml up -d

# Check running services on your machine
docker compose -f docker-compose.staging.yml ps

# View logs
docker compose -f docker-compose.staging.yml logs -f

# Tear down when done
docker compose -f docker-compose.staging.yml down
```

### Manual Smoke Test Run

**Trigger a manual smoke test:**

1. Go to: Actions → Staging Image Smoke Test
2. Click "Run workflow"
3. Select branch: `main`
4. Click "Run workflow"

**Manual smoke-test use cases:**
- Testing configuration changes before merging
- After credential rotation (verify keys are valid)
- Re-testing a previous image version (see
  [Testing Previous Image Versions](#testing-previous-image-versions))

---

## Smoke Tests

Smoke tests run automatically as part of the workflow's `smoke-test` job,
against the images pulled onto the ephemeral runner. They are the primary
signal of image health — there is no separate "post-deploy" phase.

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
docker compose -f docker-compose.staging.yml ps

# 2. Verify health checks
for port in 8001 8002 8003; do
  curl -f http://localhost:$port/health && echo " ✅ Port $port" || echo " ❌ Port $port"
done

# 3. Check logs for errors
docker compose -f docker-compose.staging.yml logs --tail=50 | grep -i "error\|critical"

# 4. Verify paper trading mode
docker compose -f docker-compose.staging.yml exec execution_gateway env | grep -E "DRY_RUN|ALPACA_PAPER"

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
docker compose -f docker-compose.staging.yml ps

# View logs for failing service
docker compose -f docker-compose.staging.yml logs signal_service
docker compose -f docker-compose.staging.yml logs execution_gateway
docker compose -f docker-compose.staging.yml logs orchestrator

# Check resource usage
docker stats

# Inspect specific container
docker inspect staging_signal_service
```

**Common causes:**
1. **Database connection failure:** Check `DB_PASSWORD` secret
2. **Redis connection failure:** Ensure redis container is healthy
3. **Missing API credentials:** Verify GitHub secrets `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET` (mapped to container env `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`)
4. **Port conflicts:** Check if ports 8001/8002/8003 are available

**Solutions:**

```bash
# Restart all services
docker compose -f docker-compose.staging.yml restart

# Rebuild and restart specific service
docker compose -f docker-compose.staging.yml up -d --force-recreate execution_gateway

# Check environment variables
docker compose -f docker-compose.staging.yml config | grep -A 5 "execution_gateway:"
```

### Issue: Credential validation fails

**Symptoms:**
- Workflow fails at "validate-credentials" job
- Error: "ALPACA_PAPER_API_KEY not found"

**Solutions:**

1. Verify secrets exist in GitHub Environment:
   - Repository → Settings → Environments → staging → Secrets

2. Check secret names match exactly:
   - GitHub secrets: `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET`
   - Container env: `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`

3. Ensure workflow uses correct environment:
   ```yaml
   environment: staging  # Must be present in job definition
   ```

### Issue: Live API key detected (blocked)

**Symptoms:**
- Workflow fails with error: "ALPACA_LIVE_API_KEY found in staging environment"

**This is EXPECTED BEHAVIOR - the workflow is preventing unsafe credentials
from reaching the smoke test.**

**Solutions:**

1. Remove live API keys from staging environment:
   - Repository → Settings → Environments → staging → Secrets
   - Delete `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_API_SECRET`

2. Ensure only paper API keys are present:
   - `ALPACA_PAPER_API_KEY`
   - `ALPACA_PAPER_API_SECRET`

3. Never add live credentials to staging environment

---

## Testing Previous Image Versions

> **No remote rollback exists.** Because the smoke test runs on an ephemeral
> runner and discards the stack at job end, there is nothing to "roll back" on
> a remote host. The procedures below describe how to re-run the smoke test
> against a previous image version, and how to reproduce that locally.

### Re-run Smoke Test Against a Previous Version

**1. Identify the last good image version:**

```bash
# Check GitHub Actions history for the last green run
https://github.com/YOUR_ORG/trading_platform/actions/workflows/deploy-staging.yml

# Note the commit SHA of the last successful smoke-test run.
```

**2. Trigger the workflow on that SHA:**

```
# In GitHub UI: Actions → Staging Image Smoke Test → Run workflow
# Branch/Ref: select the commit SHA (or a tag) of the last good version.
```

**3. Verify the re-run:**

```bash
# There is no remote host to curl. Verify in the Actions UI:
# Open the run → smoke-test job → confirm health check and
# /api/v1/config assertions pass in the step logs.
```

### Local Reproduction of a Previous Version

If you need to poke at the previous image set interactively on your
workstation (e.g., to reproduce a smoke-test failure), you can run it
locally. None of these commands touch any remote infrastructure:

```bash
# Stop any stack from a prior local run
docker compose -f docker-compose.staging.yml down

# Optional: wipe local volumes (WARNING: deletes local data)
docker compose -f docker-compose.staging.yml down -v

# Pull specific image versions by SHA tag
docker pull ghcr.io/YOUR_ORG/trading-platform-signal_service:PREVIOUS_SHA
docker pull ghcr.io/YOUR_ORG/trading-platform-execution_gateway:PREVIOUS_SHA
docker pull ghcr.io/YOUR_ORG/trading-platform-orchestrator:PREVIOUS_SHA

# Retag locally as :latest so compose picks them up
docker tag ghcr.io/YOUR_ORG/trading-platform-signal_service:PREVIOUS_SHA \
  ghcr.io/YOUR_ORG/trading-platform-signal_service:latest

# Bring the local stack up
docker compose -f docker-compose.staging.yml up -d

# Run the same smoke checks the workflow runs
curl -f http://localhost:8001/health
curl -f http://localhost:8002/health
curl -f http://localhost:8003/health
```

---

## Monitoring and Alerts

> **Scope note:** The smoke-test workflow does not expose a persistent
> Grafana/Prometheus/Loki stack on a remote host. The URLs below only apply
> when you run the compose stack locally for reproduction (see
> [Local Reproduction](#manual-smoke-test-run)). In CI, observability
> artifacts are limited to the workflow's step logs and any uploaded
> artifacts.

### Access Monitoring Dashboards (local reproduction only)

When you run `docker compose -f docker-compose.staging.yml up -d` locally:

**Grafana:**
- URL: http://localhost:3000
- Username: admin
- Password: (from your local `GRAFANA_PASSWORD` env var)

**Prometheus:**
- URL: http://localhost:9090

**Loki (logs):**
- Access via Grafana → Explore → Loki datasource

### Key Signals to Check in CI Logs

Within the `smoke-test` job's step output:

**Service health:**
- All services respond to `/health` with 200 OK
- Health check completes within the 120 s workflow timeout

**Trading safety:**
- DRY_RUN=true in container env dumps
- ALPACA_PAPER=true confirmed via `/api/v1/config`
- No live API calls (validation job blocks live keys)

**Error signals (grep the captured container logs):**
- Zero ERROR/CRITICAL lines during startup
- No HTTP 5xx responses during smoke checks

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

- CI/CD Pipeline (P1T10) (available in git history at tag archive-pre-ai-company)
- [Centralized Logging Guide](../GETTING_STARTED/LOGGING_GUIDE.md)
- [Logging Queries](./logging-queries.md)
- [Docker Build Pipeline Tests](../../tests/test_docker_build.py)

---

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2025-10-22 | Initial staging deployment runbook | Claude Code |
