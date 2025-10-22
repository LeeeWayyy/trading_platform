---
id: P1T12
title: "Workflow Composite Actions"
phase: P1
task: T12
priority: P2
owner: "@development-team"
state: TASK
created: 2025-10-22
dependencies: [P1T10]
estimated_effort: "0.5 days"
related_adrs: []
related_docs: []
features: []
---

# P1T12: Workflow Composite Actions

**Phase:** P1 (Hardening & Automation)
**Status:** TASK (Not Started)
**Priority:** P2 (Post-merge enhancement)
**Owner:** @development-team
**Created:** 2025-10-22
**Estimated Effort:** 0.5 days

---

## Objective

Extract duplicated "wait for healthy services" shell logic into reusable GitHub composite action.

**Success looks like:**
- No duplicated wait-for-services logic across workflows
- Composite action parameterized and reusable
- All workflows using the action pass CI

---

## Context

P1T10 deep review (continuation_id: `7ffca4f4-e7f5-4e1e-8420-682be1327208`) identified duplicated logic:
- `.github/workflows/ci-tests-coverage.yml:166-178` (CI integration tests)
- `.github/workflows/deploy-staging.yml:133-145` (staging deployment)

---

## Acceptance Criteria

- [ ] **AC1:** Composite action `.github/actions/wait-for-services/action.yml` created
- [ ] **AC2:** Action parameterized (compose file, max iterations, sleep interval)
- [ ] **AC3:** CI workflow updated to use composite action
- [ ] **AC4:** Staging workflow updated to use composite action
- [ ] **AC5:** All workflows pass CI

---

## Approach

1. Create `.github/actions/wait-for-services/action.yml`
2. Parameterize: compose file path, iterations, sleep seconds
3. Update CI and staging workflows
4. Test all workflows pass
5. Follow 4-step pattern per component

---

## Files to Create/Modify

- `.github/actions/wait-for-services/action.yml` (NEW)
- `.github/workflows/ci-tests-coverage.yml` (UPDATE)
- `.github/workflows/deploy-staging.yml` (UPDATE)

---

## Dependencies

**Blockers:**
- ✅ P1T10: CI/CD Pipeline (completed)

---

## Related

- Deep review: continuation_id `7ffca4f4-e7f5-4e1e-8420-682be1327208`
- Codex recommendation: Post-merge follow-up for maintainability
