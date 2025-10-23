---
id: P1T10
title: "CI/CD Pipeline"
phase: P1
task: T10
priority: P1
owner: "@development-team"
state: DONE
created: 2025-10-20
dependencies: []
estimated_effort: "3-5 days"
related_adrs: []
related_docs: []
features: []
started: 2025-10-22
completed: 2025-10-22
duration: 0 days
---

# P1T10: CI/CD Pipeline

**Phase:** P1 (Hardening & Automation, 46-90 days)
**Status:** PROGRESS (In Progress)
**Priority:** P1 (MVP)
**Owner:** @development-team
**Created:** 2025-10-20
**Started:** 2025-10-22
**Estimated Effort:** 3-5 days

---

## Naming Convention

**This task:** `P1T10_TASK.md` → `P1T10_PROGRESS.md` → `P1T10_DONE.md`

**If this task has multiple features/sub-components:**
- Feature 0: `P1T10-F0_PROGRESS.md` (separate tracking for complex features)
- Feature 1: `P1T10-F1_PROGRESS.md`

**Where:**
- **Px** = Phase (P1 = MVP/0-45 days, P1 = Hardening/46-90 days, P2 = Advanced/91-120 days)
- **Ty** = Task number within phase (T10, T1, T2, ...)
- **Fz** = Feature/sub-component within task (F0, F1, F2, ...)

---

## Objective

Implement automated testing and deployment pipeline to enable rapid iteration with confidence.

**Current State (P0):**
- Manual test execution (`make test`)
- Manual deployment steps
- No automated quality gates
- No integration testing in CI

**Success looks like:**
- GitHub Actions runs tests on every PR
- Docker images built and tagged automatically
- Automated deployment to staging environment
- Integration tests validate service interactions
- Quality gates prevent broken code from merging

---

## Acceptance Criteria

- [ ] **AC1:** GitHub Actions workflow runs unit tests + linters on every PR
- [ ] **AC2:** CI builds Docker images for all services and pushes to registry
- [ ] **AC3:** Integration tests validate service communication in CI environment
- [ ] **AC4:** Automated deployment to staging on merge to main branch
- [ ] **AC5:** Quality gates block merge if tests fail or coverage drops below 80%
- [ ] **AC6:** CI pipeline completes in under 10 minutes for fast feedback

---

## Approach

### High-Level Plan

1. **Design CI workflow** - Define GitHub Actions stages, caching strategy
2. **Implement test automation** - Unit tests, linters, coverage reporting
3. **Add Docker build** - Multi-stage builds, image tagging, registry push
4. **Integration testing** - Spin up services, run E2E tests, teardown
5. **Staging deployment** - Automated deploy on merge to main
6. **Quality gates** - Enforce test pass, coverage thresholds

### Logical Components

**Component 1: GitHub Actions Workflow** (PARTIALLY COMPLETE)
- ✅ EXISTING: `.github/workflows/ci-tests-coverage.yml` with test + lint jobs
- ✅ EXISTING: Python dependency caching, coverage reporting (codecov + PR comments)
- ✅ EXISTING: Quality gates (80% coverage threshold, mypy strict, ruff)
- NOTE: This component is 25% complete. Basic CI exists but missing integration tests
- Future work: Extend existing workflow for integration tests (Component 3)

**Component 2: Docker Build Pipeline**
- Create Dockerfiles for each service (multi-stage for size optimization)
- Add `.github/workflows/docker-build.yml` for image building
- Configure Docker layer caching
- Push images to GitHub Container Registry (ghcr.io)
- Tag images with commit SHA and branch name
- Request zen-mcp review & commit

**Component 3: Integration Testing in CI**
- Extend CI workflow to spin up services via docker-compose
- Add E2E tests validating service communication
- Use test fixtures for deterministic data
- Capture logs on failure for debugging
- Request zen-mcp review & commit

**Component 4: Staging Deployment Automation**
- Create `.github/workflows/deploy-staging.yml`
- Trigger on merge to main branch
- **SAFETY: Enforce paper-trading/sandbox credentials only**
  - Document required secret segregation (PAPER_API_KEY vs LIVE_API_KEY)
  - Add automated checks to block live-exchange API keys
  - Implement credential rotation flow
  - Use GitHub Environments with protection rules (staging, production)
  - Verify DRY_RUN=true or ALPACA_PAPER=true in staging config
- Pull latest images and restart services
- Run smoke tests post-deployment (verify paper trading mode active)
- Notify on deployment success/failure (Slack or email)
- Request zen-mcp review & commit

---

## Technical Details

### Files to Modify/Create
- `.github/workflows/ci.yml` - NEW: Main CI workflow (test, lint, coverage)
- `.github/workflows/docker-build.yml` - NEW: Docker image build workflow
- `.github/workflows/deploy-staging.yml` - NEW: Staging deployment workflow
- `Dockerfile` - NEW: Multi-stage Dockerfile for each service
  - `apps/signal_service/Dockerfile`
  - `apps/execution_gateway/Dockerfile`
  - `apps/orchestrator/Dockerfile`
- `.dockerignore` - NEW: Exclude unnecessary files from Docker context
- `docker-compose.ci.yml` - NEW: CI-specific compose file for integration tests
- `tests/e2e/` - NEW: End-to-end integration tests
  - `test_signal_to_execution.py` - Validate signal → execution flow
  - `test_orchestrator_flow.py` - Validate full orchestration
- `scripts/deploy-staging.sh` - NEW: Staging deployment script

### APIs/Contracts
- No API changes required
- CI uses existing service APIs for integration testing

### Database Changes
- No database changes required
- CI uses test database (PostgreSQL container)

---

## Dependencies

**Blockers (must complete before starting):**
- None (can start immediately)

**Nice-to-have (can start without):**
- P1T9: Centralized Logging - Would enable CI log aggregation

**Blocks (other tasks waiting on this):**
- None (quality-of-life improvement, not blocking other work)

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| CI pipeline too slow (>10min) | Medium | Medium | Optimize with caching, parallel jobs, smaller Docker images |
| Integration tests flaky due to timing issues | Medium | Medium | Use retry logic, explicit waits, health checks before tests |
| Docker image registry costs | Low | Low | Use GitHub Container Registry (free for public repos) or self-host |
| Staging environment config drift | Medium | Low | Infrastructure as code, version control all configs |

---

## Testing Strategy

### Test Coverage Needed
- **Unit tests:**
  - Already covered by existing test suite (757 tests, 81% coverage)
  - CI validates test suite passes and coverage ≥80%
- **Integration tests:**
  - Service-to-service communication (signal → execution)
  - Database migrations work in CI environment
  - Redis pub/sub message passing
- **E2E tests:**
  - Full orchestrator flow (data → signals → orders)
  - Circuit breaker integration across services
  - Paper run end-to-end workflow

### Manual Testing
- [ ] Trigger CI workflow on test PR and verify all jobs pass
- [ ] Verify Docker images built and pushed to registry with correct tags
- [ ] Break a test intentionally and verify quality gate blocks merge
- [ ] Trigger staging deployment and verify services restart successfully
- [ ] Check CI pipeline runtime is under 10 minutes

---

## Documentation Requirements

### Must Create/Update
- [ ] ADR for CI/CD architecture (GitHub Actions, Docker strategy, staging deployment)
- [ ] Update `/docs/GETTING_STARTED/SETUP.md` with CI/CD setup instructions
- [ ] Create `/docs/RUNBOOKS/ci-cd-troubleshooting.md` for CI failures

### Must Update
- [ ] `/docs/GETTING_STARTED/REPO_MAP.md` for new `.github/workflows/` and `tests/e2e/`
- [ ] `/docs/GETTING_STARTED/PROJECT_STATUS.md` when complete
- [ ] `README.md` with CI badge and deployment status

---

## Related

**ADRs:**
- ADR (to be created): CI/CD Pipeline Architecture

**Documentation:**
- [P1_PLANNING.md](./P1_PLANNING.md#t10-cicd-pipeline) - Source planning document

**Tasks:**
- Nice-to-have: [P1T9_DONE.md](./P1T9_DONE.md) - Centralized Logging (enables CI log aggregation)

---

## Notes

**GitHub Actions Workflow Structure:**
```
.github/workflows/
├── ci.yml              # Main CI (test, lint, coverage) - runs on PR
├── docker-build.yml    # Docker image build - runs on main push
└── deploy-staging.yml  # Staging deployment - runs after docker-build
```

**Docker Multi-Stage Build Strategy:**
- Stage 1: Build dependencies (Python packages, compile extensions)
- Stage 2: Runtime (copy only needed artifacts, slim base image)
- Benefits: Smaller images (~200MB vs ~800MB), faster pulls, better caching

**CI Performance Optimization:**
- Python dependency caching: `actions/cache` with `requirements.txt` hash
- Docker layer caching: GitHub Actions cache or registry cache
- Parallel test execution: `pytest -n auto` with matrix strategy
- Target: <10min total pipeline time for fast feedback

**Staging Environment:**
- Separate from production (different database, API keys)
- Automated deployment on merge to `main` branch
- Smoke tests verify services healthy post-deployment
- Rollback on smoke test failure

**Reference:** See [P1_PLANNING.md](./P1_PLANNING.md#t10-cicd-pipeline) for original requirements.

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P1T10_TASK.md docs/TASKS/P1T10_PROGRESS.md

# 2. Update front matter in P1T10_PROGRESS.md:
#    state: PROGRESS
#    started: 2025-10-20

# 3. Commit
git add docs/TASKS/P1T10_PROGRESS.md
git commit -m "Start P1T10: Task Title"
```

**Or use automation:**
```bash
./scripts/tasks.py start P1T10
```
