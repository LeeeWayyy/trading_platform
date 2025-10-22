---
id: P1T13
title: "Configuration Verification Endpoint"
phase: P1
task: T13
priority: P2
owner: "@development-team"
state: TASK
created: 2025-10-22
dependencies: [P1T10]
estimated_effort: "1 day"
related_adrs: []
related_docs: []
features: []
---

# P1T13: Configuration Verification Endpoint

**Phase:** P1 (Hardening & Automation)
**Status:** TASK (Not Started)
**Priority:** P2 (Post-merge enhancement)
**Owner:** @development-team
**Created:** 2025-10-22
**Estimated Effort:** 1 day

---

## Objective

Add `/api/v1/config` endpoint to execution_gateway and orchestrator for programmatic safety verification.

**Success looks like:**
- Config endpoint exposes DRY_RUN, ALPACA_PAPER, environment flags
- Staging smoke tests automatically verify paper trading mode
- E2E tests assert config values
- TODOs removed and replaced with working implementations

---

## Context

P1T10 deep review (continuation_id: `7ffca4f4-e7f5-4e1e-8420-682be1327208`) identified TODOs:
- `.github/workflows/deploy-staging.yml:155-158` - TODO: Add config verification
- `tests/e2e/test_signal_to_execution.py:150-157` - TODO: Test paper trading mode

---

## Acceptance Criteria

- [ ] **AC1:** `/api/v1/config` endpoint implemented in execution_gateway
- [ ] **AC2:** `/api/v1/config` endpoint implemented in orchestrator
- [ ] **AC3:** Endpoints expose: DRY_RUN, ALPACA_PAPER, ENVIRONMENT, version
- [ ] **AC4:** Staging smoke tests verify config and assert paper trading mode
- [ ] **AC5:** E2E tests verify config values
- [ ] **AC6:** All TODOs removed
- [ ] **AC7:** OpenAPI specs updated

---

## Approach

1. Implement `/api/v1/config` endpoints in both services
2. Update staging smoke tests to verify config
3. Update E2E tests with config assertions
4. Update OpenAPI specs
5. Remove TODOs
6. Follow 4-step pattern per component

---

## Files to Create/Modify

- `apps/execution_gateway/main.py` (ADD endpoint)
- `apps/orchestrator/main.py` (ADD endpoint)
- `.github/workflows/deploy-staging.yml` (UPDATE smoke tests)
- `tests/e2e/test_signal_to_execution.py` (UPDATE tests)
- `docs/API/execution_gateway.openapi.yaml` (UPDATE)
- `docs/API/orchestrator.openapi.yaml` (UPDATE)

---

## Dependencies

**Blockers:**
- ✅ P1T10: CI/CD Pipeline (completed)

---

## Related

- Deep review: continuation_id `7ffca4f4-e7f5-4e1e-8420-682be1327208`
- Codex recommendation: Post-merge follow-up for safety verification
