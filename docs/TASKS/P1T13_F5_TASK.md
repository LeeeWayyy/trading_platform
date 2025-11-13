---
id: P1T13-F5
title: "Workflow Meta-Optimization: AI Enforcement, PR Automation & Subtask Isolation"
phase: P1
task: T13-F5
priority: P1
owner: "@development-team"
state: PLANNING
created: 2025-11-12
updated: 2025-11-12
dependencies: ["P1T13-F4"]
estimated_effort: "24-32 hours"
related_adrs: []
related_docs: ["CLAUDE.md", ".claude/workflows/", "scripts/workflow_gate.py", "scripts/update_task_state.py"]
features: ["hard_gated_workflow", "pr_webhook_automation", "hierarchical_subtask_management"]
branch: "feature/P1T13-F5-workflow-meta-optimization"
---

# P1T13-F5: Workflow Meta-Optimization: AI Enforcement, PR Automation & Subtask Isolation

**Phase:** P1 (Hardening)
**Status:** PLANNING (Gemini review: NEEDS_REVISION → Addressing feedback)
**Priority:** P1 (HIGH)
**Owner:** @development-team
**Created:** 2025-11-12
**Estimated Effort:** 24-32 hours (Phase 0 audit + 3 subfeatures recommended)
**Dependencies:** P1T13-F4 (Workflow Intelligence & Context Efficiency)
**Gemini Continuation ID:** d6494788-f538-4ccd-911e-fce60ba8bb42

---

## Objective

**Meta-optimization of the workflow system itself** to address three critical gaps:
1. **Hard-gated AI enforcement** - Force AI assistants to follow workflows (not rely on documentation)
2. **PR review webhook automation** - Auto-respond to reviewer comments with fixes
3. **Hierarchical subtask management** - Isolate subtask state to prevent parent pollution

This is a "meta" task: improving the tools that improve our workflow.

---

## Problem Statement

### 1. AI Workflow Compliance Gap

**Current Issue:** Despite comprehensive documentation (CLAUDE.md, .claude/workflows/), AI assistants (Claude Code) sometimes bypass workflow requirements:
- Skip pre-implementation analysis
- Make changes after review approval
- Bypass review gates
- Skip CI runs

**Impact:**
- 7+ fix commits on P1T13-F3 due to skipped gates
- 10-15 hours wasted fixing issues that reviews would have caught
- Workflow discipline degrades over sessions

**Root Cause:** Documentation-based enforcement is insufficient. AI can "forget" or "misinterpret" requirements.

### 2. PR Review Response Bottleneck

**Current Issue:** Manual process for responding to PR review comments:
1. Reviewer posts comment on PR
2. Wait for developer/AI to see comment
3. Developer/AI manually reads and fixes
4. Manual re-request review
5. Manual verification of fix

**Impact:**
- Slow feedback loops (hours to days)
- Context switching costs
- Inconsistent fix quality
- Reviewer frustration with delayed responses

**Root Cause:** No automation between GitHub PR events and AI coding tools.

### 3. Subtask State Pollution

**Current Issue:** Single `.claude/task-state.json` file tracks all task progress:
- Parent task and subtask states mix in same file
- `current_component` field switches between parent/child contexts
- Completing subtask pollutes parent progress tracking
- No clear hierarchy in `remaining_components` array

**Example Pollution:**
```json
{
  "current_task": {"task_id": "P1T13"},  // Parent
  "progress": {
    "current_component": {"name": "P1T13-F4 Component 2"}  // Child!
  },
  "remaining_components": [
    {"name": "Parent Component 3"},       // Mixed!
    {"name": "P1T13-F4 Component 3"}      // Mixed!
  ]
}
```

**Impact:**
- Hard to determine parent vs child progress
- Auto-resume logic confused by mixed state
- Difficult to rollup subtask completion to parent
- Git branch switching breaks component tracking

**Root Cause:** Flat state structure designed for simple tasks, breaks down with subfeatures.

---

## Research Summary

### Consultation Results

**Gemini Planner Recommendations:**
1. **Phase 1: Harden AI Workflow Enforcement**
   - Server-side pre-receive hook (GitHub Action fallback)
   - Enhance workflow_gate.py to track code state hash
   - Block commits if staged changes differ from reviewed state
2. **Phase 2: Automate PR Review Webhook**
   - New microservice: `review_orchestrator` (FastAPI)
   - Listen for GitHub PR comment webhooks
   - Orchestrate: Parse comment → AI fix via clink → workflow_gate cycle → push fix
   - Safety: Circuit breaker (max 3 attempts), full workflow gates before push
3. **Phase 3: Isolate Subtask Management**
   - Hierarchical `.claude/task-state.json` with `subtasks` array
   - Per-subtask `workflow-state.json` files in `.claude/subtasks/P1T13-F1/`
   - Update workflow_gate.py to accept `--task-id` argument
   - Progress rollup logic in update_task_state.py

**Codex Planner Recommendations:**
1. **Step 1: Map Current Enforcement Surface**
   - Inventory all gates, bypass vectors
   - Create enforcement matrix
2. **Step 2: Design Hard-Gated Assistant Compliance Layer**
   - Hook-based enforcement (blocking mode)
   - File-based flags (component.lock, review.required)
   - Prompt scaffolds with workflow status injection
   - API-level allow/deny wrapper
3. **Step 3: Plan PR Review Webhook Automation**
   - GitHub Actions vs webhook server comparison
   - Event payload parsing and comment classification
   - Orchestration script re-runs workflow_gate.status after fixes
   - Safety: max retries, label-based scoping, reviewer confirmation
4. **Step 4: Refine Subtask Management Strategy**
   - Per-subtask state JSON + aggregator script
   - Branch naming (parent/child) with rebase rules
   - Reporting script for rollup without log pollution

**Web Research Findings:**
- **PR Automation Tools:** PR-Agent (Qodo), CodeRabbit, Codedog - all use webhook → AI → comment pattern
- **Architecture:** GitHub Actions (simpler) vs custom webhook server (more control)
- **Safety Patterns:**
  - Circuit breakers (max retries)
  - WIP labels to prevent premature merge
  - Structured output (JSON) for reliable parsing
  - Non-blocking initially (build trust)
- **Best Practices:**
  - Start with narrow scope (single responsibility)
  - Request structured output for easy parsing
  - Never pass secrets in diffs

---

## Proposed Solution

### Architecture Decision: Phase 0 + 3 Subfeatures

Given 24-32h estimate, **RECOMMENDED** to split into:
- **Phase 0 (Prerequisite):** Current Workflow Audit & Remediation (4-6h)
- **P1T13-F5a:** Hard-Gated AI Workflow Enforcement (6-8h)
- **P1T13-F5b:** PR Review Webhook Automation (10-12h)
- **P1T13-F5c:** Hierarchical Subtask Management (4-6h)

**Rationale for Phase 0:** Before building new workflow features, we must audit and fix existing workflow system issues. This ensures we build on a solid foundation and don't propagate existing bugs into new components.

Each phase/subfeature can be developed, reviewed, and merged independently.

---

## Components Breakdown

### Phase 0: Current Workflow Audit & Remediation (4-6h)

**Goal:** Identify and fix existing issues in the current workflow system before building new features.

**Rationale:**
- We're building on top of workflow_gate.py, update_task_state.py, and git hooks
- Current system may have bugs, edge cases, or design flaws
- Fresh audit by Gemini + Codex provides independent perspective
- Fix foundation before adding complexity

**Components:**

1. **Component 1: Comprehensive Workflow Scan (1-2h)**
   - Use Gemini + Codex to scan current workflow files:
     - `scripts/workflow_gate.py` (full file, not truncated)
     - `scripts/update_task_state.py`
     - `.git/hooks/pre-commit`
     - `.git/hooks/commit-msg`
     - `.claude/workflows/*.md` (all workflow docs)
     - `CLAUDE.md` (workflow guidance)
   - Prompt both reviewers to identify:
     - Bugs or logic errors
     - Missing error handling
     - Race conditions or concurrency issues
     - Incomplete implementations (TODOs, stubs)
     - Inconsistencies between docs and code
     - Security vulnerabilities
     - Edge cases not handled
   - **Gate:** Comprehensive findings report from both reviewers

2. **Component 2: Issue Prioritization & Triage (30min-1h)**
   - Categorize findings by severity:
     - **CRITICAL:** Blocks workflow, causes data loss, security risk
     - **HIGH:** Frequent failures, user confusion, workflow bypass
     - **MEDIUM:** Edge cases, minor bugs, usability issues
     - **LOW:** Documentation gaps, optimization opportunities
   - Prioritize fixes:
     - CRITICAL + HIGH: Must fix before Phase 0 completion
     - MEDIUM: Fix if time permits, otherwise document as known issues
     - LOW: Defer to future tasks
   - **Gate:** Prioritized issue list with fix estimates

3. **Component 3: Critical Issue Remediation (2-3h)**
   - Fix all CRITICAL and HIGH priority issues identified
   - For each fix:
     - Write test to reproduce issue
     - Implement fix
     - Verify test passes
     - Request zen-mcp review
     - Run CI
     - Commit with 4-step pattern
   - Document fixes in Phase 0 completion report
   - **Gate:** All CRITICAL + HIGH issues resolved, tests passing

4. **Component 4: Audit Report & Handoff (30min)**
   - Generate comprehensive audit report:
     - Issues found (by severity)
     - Issues fixed in Phase 0
     - Known issues deferred (with rationale)
     - Recommendations for Subfeatures A/B/C
   - Update CLAUDE.md with any critical workflow changes
   - **Gate:** Audit report delivered, foundation ready for new features

**Validation:**
- Test 1: workflow_gate.py handles all edge cases identified by reviewers
- Test 2: Git hooks work correctly in all scenarios (normal commit, amend, merge)
- Test 3: update_task_state.py handles concurrent updates safely
- Test 4: No CRITICAL or HIGH priority issues remain

**Risks:**
- Risk: Audit reveals fundamental design flaws requiring major refactor
  - Mitigation: If issues are pervasive, pause and create separate refactor task
- Risk: Fixes introduce new bugs
  - Mitigation: Comprehensive testing + zen-mcp review for each fix
- Risk: Audit takes longer than 6h budget
  - Mitigation: Time-box to 6h, defer MEDIUM issues if needed

**Expected Findings (Hypotheses to Test):**
Based on P1T13-F4 experience, we might find:
- Race conditions in workflow-state.json writes
- Missing validation in update_task_state.py arguments
- Incomplete error handling in git hooks
- Inconsistencies between CLAUDE.md and workflow_gate.py behavior
- Edge cases in component detection logic
- No tests for workflow_gate.py state transitions

**Deliverables:**
- Audit report document (`.claude/audits/P1T13-F5-phase0-audit.md`)
- Fixed issues (commits with tests)
- Updated tests for workflow_gate.py and update_task_state.py
- Known issues log (`.claude/known-issues.md`)

---

### Subfeature A: Hard-Gated AI Workflow Enforcement (6-8h)

**Goal:** Make it technically impossible for AI to bypass workflow gates.

**Components:**
1. **Component 1: Code State Fingerprinting (2h)**
   - Enhance workflow_gate.py to hash staged changes on review approval
   - Store hash in zen_review record: `{"continuation_id": "...", "staged_hash": "abc123"}`
   - check_commit() verifies current staged hash matches stored hash
   - **Gate:** Commit blocked if code changed after review

2. **Component 2: GitHub Action Enforcement (2h)**
   - Create `.github/workflows/workflow-gates.yml`
   - Trigger on `push` event
   - Extract review hash from commit message (embedded as trailer)
   - Validate: `Review-Hash: <sha256>` trailer present
   - If hash present, verify against commit diff
   - Fail workflow if gates not met (hash missing, hash mismatch)
   - **Gate:** CI fails if workflow bypassed or code changed post-review

3. **Component 3: Prompt Scaffold Injection (2-3h)**
   - Create `.claude/prompts/workflow-status.md` template
   - Auto-inject current workflow state into AI context
   - Template includes: current step, required actions, blocking issues
   - Modify workflow_gate.py to generate prompt scaffolds
   - **Gate:** AI sees workflow status in every interaction

**Validation:**
- Test 1: Commit blocked when staged changes differ from reviewed state
- Test 2: GitHub Action fails when commit bypasses workflow
- Test 3: Prompt scaffold accurately reflects workflow state

**Risks:**
- Risk: Staged hash calculation complex
  - Mitigation: Use helper script `scripts/compute_review_hash.sh` with `git diff --staged | sha256sum` to ensure deterministic hashing across environments
- Risk: GitHub Action can't access workflow-state.json
  - Mitigation: Embed review hash in commit message as trailer (e.g., `Review-Hash: abc123`), accessible to GitHub Action without state file
- Risk: Commit message trailer easily forged
  - Mitigation: GitHub Action verifies hash by recomputing from commit diff, not just checking presence
- Risk: Merge commits break hash-to-diff mapping
  - Mitigation: Require squash-merge strategy for all PRs (enforced in GitHub repo settings)

---

### Subfeature B: PR Review Webhook Automation (10-12h)

**Goal:** Auto-respond to PR review comments with AI-generated fixes.

**Prerequisites:**
- Secure webhook infrastructure setup (public endpoint)
- GitHub App with write access (scoped token)
- Redis for circuit breaker state
- Secret management for tokens

**Components:**
1. **Component 1: Webhook Receiver Service (3-4h)**
   - New FastAPI service: `apps/review_orchestrator/`
   - Endpoint: `POST /webhooks/github/pr_comment`
   - Parse GitHub webhook payload
   - Extract: PR number, comment body, file/line references, commenter
   - Validate webhook signature (HMAC) + IP whitelist
   - Filter Draft PRs (no auto-fix for drafts)
   - Check for 👍 emoji reaction trigger (explicit opt-in)
   - Queue fix job (async background task)
   - **Gate:** Service receives, validates, and filters webhook

2. **Component 2: AI Fix Orchestrator (4-5h)**
   - Background worker: `orchestrate_pr_fix(pr_num, comment)`
   - Steps:
     1. Fetch PR context (gh pr view <pr_num>)
     2. Call clink with gemini/codex for fix suggestion
     3. Create fix branch: `pr-<pr_num>-auto-fix-<comment_id>`
     4. Apply fix to code
     5. Run workflow_gate.py cycle (implement → test → review → CI)
     6. If all gates pass: push to PR branch and comment "✅ Fixed"
     7. If gates fail: comment "❌ Auto-fix failed" with logs
   - **Gate:** Full workflow gates enforced on auto-fix

3. **Component 3: Safety, Circuit Breakers & Security (3h)**
   - Max 3 auto-fix attempts per comment (prevent infinite loops)
   - Track attempts in Redis: `pr_fix_attempts:<pr>:<comment_id>`
   - Reviewer confirmation: require 👍 emoji reaction to trigger (explicit opt-in)
   - Rate limiting: max 5 fixes per PR per hour
   - Token scoping: GitHub App token with minimal permissions (PR read/write only)
   - IP whitelisting: restrict webhook endpoint to GitHub IPs
   - Diff size limit: reject diffs >1000 lines (prevent large AI-generated changes)
   - Secret scanning: filter diffs through secret detector before sending to AI
   - Cost monitoring: track AI API costs per PR, alert on anomalies
   - Non-committing mode: start with "suggestion comments" before auto-push (build trust)
   - **Gate:** Security and safety mechanisms prevent abuse

**Validation:**
- Test 1: Webhook correctly parses PR comment and validates HMAC
- Test 2: Orchestrator generates and applies fix
- Test 3: Circuit breaker stops after 3 failed attempts
- Test 4: Only comments with 👍 emoji reaction trigger automation
- Test 5: Draft PRs ignored (no auto-fix triggered)
- Test 6: Large diffs (>1000 lines) rejected with explanation
- Test 7: Secrets in diff detected and webhook rejected

**Risks:**
- Risk: AI introduces new bugs in fix
  - Mitigation: Full workflow gates (review + CI) required before push
- Risk: Infinite loop between auto-fix and new comments
  - Mitigation: Max attempts, require human confirmation (👍 emoji)
- Risk: Secrets exposed in diffs
  - Mitigation: Filter diffs through secret scanner before sending to AI
- Risk: Compromised webhook endpoint or write-access token
  - Mitigation: HMAC validation, IP whitelisting, scoped token (PR-only), secret rotation
- Risk: High AI costs from abuse or large PRs
  - Mitigation: Diff size limits, rate limiting, cost monitoring alerts
- Risk: User trust erosion if auto-fix unreliable
  - Mitigation: Start with "suggestion mode" (comment-only), graduate to auto-push after success rate >80%

**GitHub Webhook Setup:**
```bash
# Configure webhook in repo settings:
# URL: https://your-domain.com/webhooks/github/pr_comment
# Events: Issue comments (PR comments are issue comments)
# Secret: <generate strong secret>
```

---

### Subfeature C: Hierarchical Subtask Management (4-6h)

**Goal:** Isolate subtask state from parent task to prevent pollution.

**Edge Cases Addressed:**
- Git conflicts during concurrent parent/subtask updates
- update_task_state.py adaptation for subtask creation
- 2-level nesting limit (Task → Subtask only, not Task → Subtask → Component)

**Components:**
1. **Component 1: Hierarchical State Schema + update_task_state.py (2h)**
   - Update `.claude/task-state.json` schema:
     ```json
     {
       "current_task": {
         "task_id": "P1T13",
         "subtasks": [
           {
             "task_id": "P1T13-F4",
             "state_file": ".claude/subtasks/P1T13-F4/task-state.json",
             "status": "IN_PROGRESS"
           },
           {
             "task_id": "P1T13-F5",
             "state_file": ".claude/subtasks/P1T13-F5/task-state.json",
             "status": "NOT_STARTED"
           }
         ]
       }
     }
     ```
   - Each subtask has own directory: `.claude/subtasks/P1T13-F4/`
   - Subtask directory contains:
     - `task-state.json` (progress tracking)
     - `workflow-state.json` (4-step cycle state)
   - Add `create-subtask` command to update_task_state.py:
     ```bash
     ./scripts/update_task_state.py create-subtask --parent P1T13 --subtask P1T13-F4 --title "..." --components 3
     ```
   - **Gate:** Parent and child states fully isolated

2. **Component 2: Subtask-Aware workflow_gate.py (1-2h)**
   - Add `--task-id` argument to all commands
   - Auto-detect current task from:
     1. CLI argument: `--task-id P1T13-F4`
     2. Environment variable: `CLAUDE_CURRENT_TASK`
     3. Git branch name parsing: `feature/P1T13-F4-...` → `P1T13-F4`
   - Load state from appropriate directory:
     - Parent task: `.claude/workflow-state.json`
     - Subtask: `.claude/subtasks/<task_id>/workflow-state.json`
   - **Gate:** Commands operate on correct task context

3. **Component 3: Progress Rollup Logic with Locking (1-2h)**
   - Extend update_task_state.py with `rollup` command
   - When subtask completes:
     1. Acquire file lock on parent task-state.json (fcntl.flock)
     2. Mark subtask status = "COMPLETE" in parent's subtasks array
     3. Increment parent's completed_components count
     4. Update parent's completion_percentage
     5. Do NOT touch parent's current_component (stays at parent level)
     6. Release lock
   - Handle lock conflicts: retry with exponential backoff (max 3 attempts)
   - **Gate:** Parent progress updates without state pollution or race conditions

**Validation:**
- Test 1: Creating subtask isolates state files
- Test 2: workflow_gate.py auto-detects task context from branch
- Test 3: Completing subtask updates parent without pollution
- Test 4: Git conflicts in parent task-state.json handled gracefully
- Test 5: update_task_state.py creates subtask directories correctly

**Risks:**
- Risk: Task ID detection from branch fails
  - Mitigation: Fallback to env var, then manual --task-id
- Risk: Rollup logic becomes complex with nested subtasks
  - Mitigation: Support only 2 levels of nesting (Task → Subtask) per YAGNI principle
- Risk: Git conflicts in parent task-state.json during rollup
  - Mitigation: Use file locking (fcntl) before updates, retry with backoff on conflict
- Risk: update_task_state.py not adapted for subtask creation
  - Mitigation: Add `create-subtask` command to update_task_state.py in Component 1

---

## Implementation Strategy

### Recommended Approach: Sequential Phases

```bash
# Phase 0: Workflow Audit & Remediation (PREREQUISITE)
git checkout -b feature/P1T13-F5-phase0-audit
# Scan with Gemini + Codex → identify issues → fix critical ones
# 4 components with 4-step pattern each
# PR #0 → merge to master
# ⚠️ MUST complete before starting Subfeatures A/B/C

# Subfeature A: Hard-Gated Enforcement
git checkout master && git pull
git checkout -b feature/P1T13-F5a-hard-gates
# Implement 3 components with 4-step pattern each
# PR #1 → merge to master

# Subfeature B: PR Webhook Automation
git checkout master && git pull
git checkout -b feature/P1T13-F5b-pr-webhook
# Implement 3 components with 4-step pattern each
# PR #2 → merge to master

# Subfeature C: Hierarchical Subtasks
git checkout master && git pull
git checkout -b feature/P1T13-F5c-subtask-hierarchy
# Implement 3 components with 4-step pattern each
# PR #3 → merge to master
```

**Benefits:**
- Phase 0 fixes foundation issues before building on it
- Independent reviews (smaller PRs)
- Progressive value delivery
- Easier rollback if issues discovered
- Clearer git history

**Critical Path:** Phase 0 → A → B (B benefits from A's enforcement)
**Parallel Path:** C can start after Phase 0, doesn't depend on A or B

**Alternative:** Single branch if components tightly coupled (unlikely here).

---

## Acceptance Criteria

### Phase 0: Current Workflow Audit
- [ ] Gemini and Codex scanned all workflow files and git hooks
- [ ] Findings categorized by severity (CRITICAL/HIGH/MEDIUM/LOW)
- [ ] All CRITICAL issues fixed with tests
- [ ] All HIGH issues fixed with tests
- [ ] Audit report delivered documenting findings and fixes
- [ ] Known issues log created for deferred MEDIUM/LOW items
- [ ] CI passing after all fixes

### Subfeature A: Hard-Gated Enforcement
- [ ] Commit blocked if staged changes differ from reviewed state
- [ ] GitHub Action fails when workflow gates bypassed
- [ ] Prompt scaffold injected with current workflow status
- [ ] AI can no longer bypass workflow requirements

### Subfeature B: PR Webhook Automation
- [ ] Webhook receives and validates GitHub PR comment events
- [ ] Orchestrator generates fixes via clink + gemini/codex
- [ ] Full workflow gates enforced on auto-fixes
- [ ] Circuit breaker prevents infinite loops (max 3 attempts)
- [ ] Only comments with `auto-fix` label or 👍 reaction trigger
- [ ] Secrets filtered from diffs before sending to AI

### Subfeature C: Hierarchical Subtasks
- [ ] Parent and subtask states isolated in separate directories
- [ ] workflow_gate.py auto-detects task context from branch/env
- [ ] Completing subtask updates parent without polluting current_component
- [ ] Can work on subtask without affecting parent workflow state

---

## Testing Strategy

### Unit Tests
- `test_workflow_gate_staged_hash.py` - Hash calculation and verification
- `test_pr_webhook_parser.py` - Payload parsing and validation
- `test_hierarchical_state.py` - State isolation and rollup logic

### Integration Tests
- `test_hard_gate_enforcement.py` - End-to-end commit blocking
- `test_pr_auto_fix_orchestrator.py` - Webhook → AI → fix → push flow
- `test_subtask_lifecycle.py` - Create → work → complete → rollup

### E2E Tests
- Manual test: Try to bypass workflow gates (should fail)
- Manual test: Post PR comment with `auto-fix` label (should auto-respond)
- Manual test: Create subtask, complete it, verify parent updated

---

## Success Metrics

**Phase 0:**
- **Issues identified:** >10 findings from audit (baseline quality check)
- **Critical issues fixed:** 100% (cannot proceed otherwise)
- **High issues fixed:** 100% (cannot proceed otherwise)
- **Test coverage added:** All fixes covered by tests
- **Audit time:** ≤6h (time-boxed)

**Subfeature A:**
- **Workflow bypass rate:** 0% (down from ~20% observed in P1T13-F3)
- **Fix commits due to skipped gates:** 0 (down from 7 in P1T13-F3)

**Subfeature B:**
- **PR review response time:** <5 minutes (down from hours/days)
- **Auto-fix success rate:** >60% on first attempt
- **Reviewer satisfaction:** Measured via feedback survey

**Subfeature C:**
- **State pollution incidents:** 0 (down from ~3 per complex task)
- **Auto-resume accuracy:** 100% (correct context on session restart)

---

## Dependencies

**External:**
- GitHub webhook access (repo admin permissions) - for Subfeature B
- FastAPI for review_orchestrator service - for Subfeature B
- Redis for circuit breaker state - for Subfeature B

**Internal:**
- P1T13-F4 (Workflow Intelligence) must be complete
- **Phase 0 audit must complete before ANY subfeature work**
- workflow_gate.py and update_task_state.py in stable state (ensured by Phase 0)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Staged hash detection breaks on binary files | Medium | High | Use git diff hash only for text files, skip binaries |
| GitHub Action can't access workflow-state.json | Medium | High | Embed review hash in commit message trailer (accessible without state file) |
| Compromised webhook endpoint or write token | Low | Critical | HMAC validation, IP whitelisting, scoped token, secret rotation |
| High AI costs from abuse/large PRs | Medium | Medium | Diff size limits (1000 lines), rate limiting, cost monitoring |
| User trust erosion if auto-fix unreliable | Medium | High | Start with suggestion mode, graduate to auto-push at >80% success |
| PR webhook overwhelms system with spam | Low | Medium | Rate limiting + HMAC signature validation |
| Auto-fixes introduce bugs | Medium | High | Require full workflow gates (review + CI) before push |
| Git conflicts in parent task-state.json | Medium | Medium | File locking (fcntl) + retry with backoff |
| Hierarchical state too complex | Low | Medium | Support only 2 levels (Task → Subtask), not 3+ |

---

## Timeline Estimate (Updated with Phase 0)

**Total: 24-32 hours**

| Phase/Subfeature | Components | Est. Hours | Dependencies |
|-----------------|-----------|-----------|--------------|
| **Phase 0: Audit** | 4 | 4-6h | **PREREQUISITE (must complete first)** |
| A: Hard Gates | 3 | 6-8h | Phase 0 complete |
| B: PR Webhook | 3 | 10-12h | Phase 0 complete, Infrastructure setup |
| C: Subtask Hierarchy | 3 | 4-6h | Phase 0 complete |

**Critical Path:** Phase 0 → A → B (B benefits from A's enforcement)
**Parallel Path:** C can start after Phase 0, doesn't depend on A or B

**Phase 0 is MANDATORY:** Must fix foundation before building new features

---

## Architecture Decisions (Resolved via Gemini Review)

1. **Q1: GitHub pre-receive hooks vs GitHub Action?**
   - **Decision:** Use GitHub Action
   - **Justification:** Pre-receive hooks not available on GitHub.com. GitHub Action configured as required status check is the standard enforcement method.

2. **Q2: PR webhook hosting - FastAPI microservice vs GitHub Actions?**
   - **Decision:** Use FastAPI microservice
   - **Justification:** Complex orchestration logic, stateful retry mechanism (Redis), and secure secret management better suited to dedicated Python service than YAML workflows.

3. **Q3: Subtask nesting depth - 2 or 3 levels?**
   - **Decision:** Support only 2 levels (Task → Subtask)
   - **Justification:** YAGNI principle. 2-level hierarchy covers vast majority of use cases. Can extend later if strong need arises.

4. **Q4: Auto-fix trigger - all comments vs labeled?**
   - **Decision:** Explicit opt-in via 👍 emoji reaction
   - **Justification:** Prioritize safety and user trust. Explicit trigger builds confidence. Suggested flow: bot offers help → user reacts with 👍 → fix triggered.

---

## Related Documentation

- **Workflows:** `.claude/workflows/02-planning.md`, `.claude/workflows/03-reviews.md`
- **Scripts:** `scripts/workflow_gate.py`, `scripts/update_task_state.py`
- **Tasks:** `docs/TASKS/P1T13_F4_PROGRESS.md` (precursor)
- **Standards:** `docs/STANDARDS/GIT_WORKFLOW.md`

---

## Next Steps

1. ✅ **Gemini planning review completed** (NEEDS_REVISION → feedback incorporated)
2. ✅ **Codex validation review completed** (APPROVED)
3. ✅ **Phase 0 added** (Current Workflow Audit as prerequisite)
4. **Break into phase + subfeature tasks:**
   - Create `P1T13_F5_PHASE0_TASK.md` (audit & remediation)
   - Create `P1T13_F5a_TASK.md` (hard gates)
   - Create `P1T13_F5b_TASK.md` (PR webhook)
   - Create `P1T13_F5c_TASK.md` (subtask hierarchy)
5. **Start with Phase 0** (MANDATORY prerequisite - audit & fix foundation)
6. **Then proceed to Subfeature A** (after Phase 0 complete)

---

## Review History

**Gemini Planner Review #1** (2025-11-12)
- **Status:** NEEDS_REVISION → Addressed
- **Continuation ID:** d6494788-f538-4ccd-911e-fce60ba8bb42
- **Key Findings:**
  - Effort underestimated (16-20h → 20-26h)
  - Subfeature B complexity underestimated (6-8h → 10-12h)
  - Security risks missing (webhook compromise, AI costs, trust erosion)
  - Edge cases missing (Draft PRs, large diffs, git conflicts)
  - GitHub Action mitigation weak (embed hash in commit message instead)
  - Architecture decisions approved with recommendations
- **Actions Taken:** All findings addressed in revision above

**Codex Planner Review #1** (2025-11-12)
- **Status:** ✅ APPROVED
- **Continuation ID:** d6494788-f538-4ccd-911e-fce60ba8bb42 (reused from Gemini)
- **Key Validations:**
  - Technical approaches sound (hash verification, FastAPI webhook, file locking)
  - Estimates realistic (20-26h aligns with revised scope)
  - Security measures sufficient (HMAC, IP whitelist, scoped tokens)
  - Risk coverage comprehensive (all major risks mitigated)
  - Architecture decisions validated (GitHub Action, FastAPI, 2-level nesting, 👍 trigger)
- **Recommendations:**
  - Watch Component B3 (3h is tight for safety + monitoring)
  - Document merge-commit strategy (squash-only recommended)
  - Helper script for hash calculation (avoid env drift)
- **Actions Taken:** Editorial fix applied, merge-commit guidance added

**User Addendum** (2025-11-12)
- **Addition:** Phase 0 (Current Workflow Audit & Remediation)
- **Rationale:** Fix foundation before building new features
- **Impact:** Estimate increased from 20-26h to 24-32h
- **Approach:** Scan with Gemini + Codex → prioritize findings → fix CRITICAL/HIGH issues
- **Dependencies:** Phase 0 now PREREQUISITE for all subfeatures

---

**Questions or need help?**
- Review this task document with gemini + codex planners
- See `.claude/workflows/02-planning.md` for planning workflow
- See `docs/TASKS/P1T13_F4_PROGRESS.md` for related context
