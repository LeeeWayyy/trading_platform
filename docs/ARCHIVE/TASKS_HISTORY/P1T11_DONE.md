---
id: P1T11
title: "Workflow Optimization & Testing Fixes"
phase: P1
task: T11
priority: P1
owner: "@development-team"
state: DONE
created: 2025-10-24
completed: 2025-10-24
dependencies: []
estimated_effort: "24-29 hours"
actual_effort: "12 hours"
related_adrs: ["ADR-00XX: Workflow Automation and Hard Gates"]
related_docs: ["docs/TASKS/P1T11_CURRENT_STATE.md", "docs/TASKS/P1T11_REVIEW_SUMMARY.md"]
features: []
started: 2025-10-24
---

# P1T11: Workflow Optimization & Testing Fixes

**Phase:** P1 (Hardening, 46-90 days)
**Status:** TASK (Not Started)
**Priority:** P1 (HIGH)
**Owner:** @development-team
**Created:** 2025-10-24
**Estimated Effort:** 24-29 hours total (includes 3h investigation - complete; 21-26h implementation across 3 components)

---

## Objective

Implement systematic workflow improvements to prevent AI assistant concentration loss, enforce correct tool usage, eliminate test masking, and reduce commits-per-PR through hard gates and automated state transitions.

**Success Criteria:**
1. AI assistant cannot skip workflow steps (enforced by gates)
2. AI assistant cannot use wrong tools (tool constraints enforced)
3. AI assistant cannot mask test failures (hard test gates enforced)
4. PR commit count reduced from 10-20 to 2-4 per subfeature
5. Workflow transitions automated with clear next-step reminders

---

## Problem Statement

### Current Issues (Identified by User)

1. **AI Concentration Loss:**
   - AI forgets workflow patterns during long sessions
   - "MANDATORY" documentation is not enforced
   - Context window fills → patterns forgotten

2. **Wrong Tool Usage:**
   - AI uses direct zen-mcp tools instead of clink
   - Causes API permission errors
   - Multiple paths to same goal → AI chooses "easier" wrong path

3. **Test Masking:**
   - AI skips or masks test failures instead of fixing
   - No hard gate prevents continuing with failing tests
   - Pressure to progress > pressure to fix

4. **Too Many Commits Per PR:**
   - Current: 10-20 commits per task
   - Should be: 2-4 commits per subfeature
   - No batching strategy, commit-per-component creates noise

### Root Causes

- **Soft gates:** Documentation says "MANDATORY" but nothing enforces it
- **Tool availability:** Wrong tools are exposed and accessible
- **No state tracking:** Workflow state is in AI's memory, not system
- **Large task scope:** Full PxTy tasks are too large for single PR
- **Manual transitions:** AI must remember next workflow step

---

## Current State (Investigation Findings)

**Investigation Date:** 2025-10-22
**Detailed Analysis:** See `docs/TASKS/P1T11_CURRENT_STATE.md`

### What Already Exists

**Git Hooks (Partially Working):**
- ✅ `.git/hooks/pre-commit` (103 lines) - Enforces mypy, ruff, unit tests (HARD gate)
- ✅ `.git/hooks/commit-msg` (93 lines) - Enforces zen-mcp review approval (HARD gate)
- ❌ **NOT version-controlled** (.git/hooks/ not in git) - **CRITICAL FLAW** (Gemini finding)
- ❌ Post-commit hook missing (no next-step reminders)

**Pre-commit Framework (Version-Controlled):**
- ✅ `.pre-commit-config.yaml` exists and configured (25 lines)
- ✅ Upstream hooks: black, ruff, yaml/json checks
- ❌ No custom local hooks for zen-mcp gates yet

**Task Creation Infrastructure:**
- ✅ `./scripts/tasks.py` (967 lines) with full lifecycle management
- ✅ **Already supports PxTy-Fz naming convention!** (Feature creation built-in)
- ✅ Templates: `00-TEMPLATE_TASK.md`, `00-TEMPLATE_FEATURE.md`
- ✅ Git-integrated state transitions (TASK → PROGRESS → DONE)

**Workflow Documentation:**
- ✅ 12 comprehensive workflow files (`./AI/Workflows/`)
- ✅ 3-tier review system documented (quick/deep/task reviews)
- ✅ Task review workflow (`./AI/Workflows/02-planning.md`)
- ✅ Two-phase review process: gemini planner → codex planner

### Critical Problems Identified

1. **Git Hook Distribution (CRITICAL - Gemini Review Finding)**
   - `.git/hooks/` not version-controlled
   - Only works on one developer's machine
   - **Solution:** Migrate to `.pre-commit-config.yaml` local hooks

2. **Review Marker Workflow Undefined (HIGH - Gemini Review Finding)**
   - Commit-msg hook requires "zen-mcp-review: approved" marker
   - No documentation on HOW marker gets into commit message
   - **Solution:** Create `scripts/zen_review.sh` wrapper + workflow documentation

3. **Missing Automated Gates**
   - ❌ TodoWrite state verification not in hooks
   - ❌ Branch naming not enforced
   - ❌ Post-commit next-step reminders missing
   - ❌ No metrics/override logging

### Key Insights from Investigation

1. **Most infrastructure exists** - Don't reinvent, integrate with what works
2. **Pre-commit framework is the solution** - Already configured, just add local hooks
3. **Subfeature support already implemented** - `./scripts/tasks.py` has PxTy-Fz creation
4. **Review gates partially working** - CI and approval gates exist, just need version control

---

## Constraints

### Critical Constraints
- **❌ DO NOT TOUCH MASTER BRANCH** - All work must be done on feature branches
- All commits must be on `feature/P1T11-*` branches only
- No direct commits to master or any protected branches
- Use proper git workflow: feature branch → PR → review → merge

---

## Acceptance Criteria

### Must Have (P0)
- [ ] **AC1:** Only clink tool accessible to AI (documented and trained)
- [ ] **AC2:** Pre-commit hook blocks on test failures (via pre-commit framework)
- [ ] **AC3:** Commit-msg hook blocks without review approval
- [ ] **AC4:** TodoWrite state verified in hooks
- [ ] **AC5:** Next workflow step printed after commit
- [ ] **AC6:** Task decomposition template created for PxTy-Fz naming
- [ ] **AC7:** Branch naming convention enforced in hooks
- [ ] **AC8:** ADR-00XX created documenting workflow automation decisions

### Should Have (P1)
- [ ] **AC9:** Emergency override mechanism (with warning log)
- [ ] **AC10:** Comprehensive error messages
- [ ] **AC11:** Hook execution time <5 seconds
- [ ] **AC12:** State file validation
- [ ] **AC13:** PR commit count <4 for subfeatures (from 10-20)

### Nice to Have (P2)
- [ ] Visual workflow diagram
- [ ] Automated subfeature tracking dashboard
- [ ] Metrics collection (commits per PR, gate failures)

---

## Approach

### High-Level Plan

This task is broken into 3 logical components, each following the 4-step pattern:

1. **Component A: Tool Access Restriction**
   - Document clink-only usage for zen-mcp interactions
   - Update CLAUDE.md and workflow files with strong emphasis
   - Create troubleshooting guide for wrong tool errors

2. **Component B: Hard Gates via Pre-commit Framework**
   - Integrate with `.pre-commit-config.yaml` (version-controlled!)
   - Create helper scripts for test/todo/branch validation
   - Implement review marker workflow
   - Add metrics and override logging

3. **Component C: Subfeature Branching Strategy**
   - Document `./scripts/tasks.py` usage for task/feature creation
   - Add branch naming enforcement
   - Update GIT_WORKFLOW.md and CLAUDE.md

### Logical Components

Each component follows the mandatory 4-step pattern:

**Component A: Tool Access Restriction (3-4 hours)**
1. **Implement:** Documentation updates for tool access restriction
   - Update CLAUDE.md with strong clink-only emphasis
   - Update workflow files with clink usage reminders
   - Create troubleshooting guide for wrong-tool errors
2. **Test:** Create test cases/validation for documentation completeness
   - Verify all zen-mcp sections reference clink
   - Validate troubleshooting guide covers common errors
   - Check workflow examples show correct tool usage
3. **Review:** Request zen-mcp review via clink + codex codereviewer
   - Review documentation changes for clarity and completeness
   - Validate emphasis on clink-only usage
4. **Commit:** Commit changes after review approval
   - Include zen-mcp-review marker
   - Update related_docs in front matter

**Component B: Hard Gates (14-16 hours)**
1. **Implement:** Pre-commit framework integration and helper scripts
   - Add local hook to .pre-commit-config.yaml
   - Create scripts/hooks/zen_pre_commit.sh orchestrator
   - Create helper scripts (verify_tests.sh, verify_todo.sh, verify_branch_name.sh)
   - Implement review marker workflow (scripts/zen_review.sh)
   - Set up TodoWrite state schema and metrics logging
2. **Test:** Create test cases for all gates
   - Unit tests for bash hook logic
   - Integration tests for full commit workflow
   - Test TodoWrite state verification
   - Test emergency override scenarios
   - Validate hook execution time <5s
3. **Review:** Request zen-mcp review via clink + codex codereviewer
   - Review hook logic for correctness and performance
   - Validate error messages are clear
   - Verify state verification logic
4. **Commit:** Commit changes after review approval
   - Include zen-mcp-review marker
   - Update .pre-commit-config.yaml (version-controlled!)

**Component C: Subfeature Branching Strategy (4-6 hours)**
1. **Implement:** Documentation and branch naming enforcement
   - Create ./AI/Workflows/02-planning.md
   - Update docs/STANDARDS/GIT_WORKFLOW.md with subfeature strategy
   - Update CLAUDE.md references to ./scripts/tasks.py
   - Document PxTy-Fz decomposition examples
2. **Test:** Create test cases for branch naming validation
   - Test branch name regex patterns
   - Validate enforcement via pre-commit hook
   - Test valid and invalid branch names
3. **Review:** Request zen-mcp review via clink + codex codereviewer
   - Review documentation for clarity
   - Validate examples are comprehensive
   - Check ./scripts/tasks.py usage is clear
4. **Commit:** Commit changes after review approval
   - Include zen-mcp-review marker
   - Update documentation cross-references

---

## Implementation Details

### Component A: Tool Access Restriction (3-4 hours)

**Current State:**
- ✅ Investigation complete: MCP config is system-level, not project-level
- ❌ Cannot enforce tool restriction at project level

**Approach:**
Focus on documentation and training rather than technical restriction.

**Tasks:**
1. Update `CLAUDE.md`: Add strong clink-only emphasis in zen-mcp section
2. Update workflow files: Add clink usage reminders to review workflows
3. Create `docs/TROUBLESHOOTING.md`: Document wrong-tool error patterns

**File Changes:**
- `CLAUDE.md` (update zen-mcp section)
- `./AI/Workflows/03-reviews.md` (add clink reminder)
- `./AI/Workflows/03-reviews.md` (add clink reminder)
- `./AI/Workflows/02-planning.md` (add clink reminder)
- `docs/TROUBLESHOOTING.md` (new - tool usage errors guide)

**Exit Criteria:**
- CLAUDE.md emphasizes clink-only usage with clear examples
- All review workflows explicitly show clink commands
- Troubleshooting guide documents wrong-tool errors

---

### Component B: Hard Gates (14-16 hours)

**Current State:**
- ✅ `.git/hooks/pre-commit` exists but not version-controlled
- ✅ `.pre-commit-config.yaml` exists
- ❌ No local hooks in pre-commit framework yet

**Approach:**
Migrate hook logic to pre-commit framework with version-controlled scripts.

**Tasks:**
1. **Pre-commit Framework Integration:**
   - Add local hook to `.pre-commit-config.yaml`
   - Create `scripts/hooks/zen_pre_commit.sh` orchestrator

2. **Review Marker Workflow:**
   - Create `scripts/zen_review.sh` wrapper
   - Update workflow documentation

3. **Helper Scripts:**
   - `scripts/hooks/verify_tests.sh`: Delegate to existing CI
   - `scripts/hooks/verify_todo.sh`: TodoWrite state verification
   - `scripts/hooks/verify_branch_name.sh`: Branch naming validation

4. **TodoWrite State Setup:**
   - Create `.claude/state/current-todo.json` schema
   - Implement verification logic

5. **Metrics & Logging:**
   - Event log: `logs/zen_hooks_events.jsonl`
   - Override log: `logs/zen_hooks_overrides.jsonl`

**File Changes:**
- `.pre-commit-config.yaml` (add local hook entry)
- `scripts/hooks/zen_pre_commit.sh` (new orchestrator)
- `scripts/hooks/verify_tests.sh` (new)
- `scripts/hooks/verify_todo.sh` (new)
- `scripts/hooks/verify_branch_name.sh` (new)
- `scripts/zen_review.sh` (new review marker workflow wrapper)
- `.claude/state/current-todo.json` (new template)
- `logs/zen_hooks_events.jsonl` (new, initially empty)
- `logs/zen_hooks_overrides.jsonl` (new, initially empty)
- `./AI/Workflows/01-git.md` (update with review marker workflow)
- `./AI/Workflows/03-reviews.md` (update with zen_review.sh reference)

**Exit Criteria:**
- `.pre-commit-config.yaml` has local hook (version-controlled!)
- Cannot commit with failing tests (via pre-commit framework)
- Cannot commit without active todo (via pre-commit framework)
- Cannot commit without review approval (existing commit-msg hook)
- Review marker workflow documented and working
- Hook execution time <5s

---

### Component C: Subfeature Branching Strategy (4-6 hours)

**Current State:**
- ✅ `./scripts/tasks.py` already supports PxTy-Fz naming
- ❌ No branch naming enforcement
- ❌ Task decomposition guidance incomplete

**Approach:**
Leverage existing infrastructure, add enforcement and documentation.

**Tasks:**
1. **Branch Naming Enforcement:**
   - Regex: `^feature/P\\d+T\\d+(-F\\d+)?-[a-z0-9-]+$`
   - Integrated in `scripts/hooks/verify_branch_name.sh` (Component B)

2. **Documentation Updates:**
   - Create `./AI/Workflows/02-planning.md`
   - Update `docs/STANDARDS/GIT_WORKFLOW.md`
   - Update `CLAUDE.md`

**File Changes:**
- `./AI/Workflows/02-planning.md` (new workflow guide)
- `docs/STANDARDS/GIT_WORKFLOW.md` (add subfeature strategy)
- `CLAUDE.md` (update task creation references to use ./scripts/tasks.py)

**Exit Criteria:**
- Branch naming validated via pre-commit framework
- `./scripts/tasks.py` usage documented in workflows
- Examples of PxTy-Fz decomposition provided
- CLAUDE.md references proper task creation workflow

---

## Dependencies

### Required
- Existing TodoWrite tool functionality
- Git hooks support (standard Git feature)
- Bash scripting environment
- Pre-commit framework already installed

### Blockers
None identified - all dependencies available.

---

## Risks & Mitigations

### Risk 1: Git Hooks Too Slow
**Severity:** MEDIUM
**Impact:** Developer frustration, hooks disabled
**Mitigation:**
- Performance test each hook
- Optimize state file reads
- Cache validation results
- Target: <5 seconds total execution

### Risk 2: False Positive Gate Failures
**Severity:** MEDIUM
**Impact:** Legitimate commits blocked
**Mitigation:**
- Emergency override flag: `git commit --no-verify`
- Log override usage to `logs/zen_hooks_overrides.jsonl`
- Clear error messages with resolution steps

### Risk 3: TodoWrite State Corruption
**Severity:** LOW
**Impact:** Hooks fail, commits blocked
**Mitigation:**
- JSON schema validation
- State file backup on write
- Manual recovery instructions

---

## Testing Strategy

### Unit Tests
- Bash hook logic (using bats or shunit2)
- State file read/write functions
- Branch name validation regex

### Integration Tests
- Full commit workflow with gates
- TodoWrite state transitions
- Emergency override scenarios

### Manual Validation
- Test each gate type independently
- Verify error messages clarity
- Confirm next-step reminders work

---

## Documentation Requirements

### ADR (MANDATORY)
**Required:** ADR-00XX: Workflow Automation and Hard Gates
- Tool restriction rationale (system-level config, documentation-based approach)
- Hard gate vs soft gate decision
- Pre-commit framework vs direct .git/hooks/
- Subfeature branching strategy (PxTy-Fz naming)
- Emergency override policy
- Review marker workflow design

### Workflow Updates
- `./AI/Workflows/02-planning.md` (new - subfeature decomposition guide)
- `./AI/Workflows/01-git.md` (update with review marker workflow)
- `./AI/Workflows/03-reviews.md` (add zen_review.sh reference)
- `./AI/Workflows/README.md` (add new workflow)

### Standards Updates
- `docs/STANDARDS/GIT_WORKFLOW.md` (subfeature branching strategy)
- `CLAUDE.md` (reference new workflows, emphasize clink-only usage)
- `docs/TROUBLESHOOTING.md` (new - wrong tool errors)

---

## Timeline

### Investigation Phase (COMPLETE - 3 hours)
- ✅ Current state analysis
- ✅ Infrastructure discovery
- ✅ Plan revision based on gemini + codex review

### Component A: Tool Restriction (3-4 hours)
- Day 1: Documentation updates (CLAUDE.md + workflows)
- Day 1: Troubleshooting guide creation

### Component B: Hard Gates (14-16 hours)
- Day 2: Pre-commit framework integration (4h)
- Day 2-3: Helper scripts implementation (3h)
- Day 3: Review marker workflow (3h)
- Day 3-4: Caching optimization (2h)
- Day 4: TodoWrite state + Metrics logging (2h)
- Day 4-5: Testing (2h)

### Component C: Subfeature Strategy (4-6 hours)
- Day 5: Task breakdown workflow documentation (2h)
- Day 5: Standards documentation updates (1h)
- Day 5: CLAUDE.md updates (1h)
- Day 5-6: Testing (1h)

**Total:** 24-29 hours (including investigation: 3h already complete)
**Implementation Only:** 21-26 hours across 5-6 days

---

## Design Decisions (Investigation Findings)

### 1. MCP Configuration Location

**Decision:** MCP server configuration is system-level, not project-level

**Finding:**
- MCP servers configured in user's system config (~/.config/claude or similar)
- Cannot enforce tool restriction at project level
- `.claude/settings.local.json` only contains git command permissions

**Approach:**
- Document clink-only usage strongly in CLAUDE.md
- Add reminder comments in all review workflows
- Training-based approach (not technical restriction)

### 2. Emergency Override Policy

**Decision:** Allow `--no-verify` but log usage for review

**Rationale:**
- Override already exists (standard git feature, cannot remove)
- Blocking all overrides could trap developers in emergencies

**Implementation:**
- Log to `logs/zen_hooks_overrides.jsonl`
- Print remediation reminder after override
- Weekly review of override log (operational practice)

### 3. Metrics Collection

**Decision:** JSONL event log for hook executions

**Rationale:**
- Lightweight, append-only, easy to parse
- No external dependencies
- File: `logs/zen_hooks_events.jsonl`

### 4. Subfeature Naming Convention

**Decision:** PxTy-Fz format (e.g., P1T15-F2)

**Finding:**
- **ALREADY SUPPORTED** in `./scripts/tasks.py`!
- Template exists: `00-TEMPLATE_FEATURE.md`
- Command: `./scripts/tasks.py create P1T15-F1 --title "..." --parent P1T15 --phase P1`

**Branch Naming:**
- Format: `feature/P1T15-F2-description`
- Regex: `^feature/P\\d+T\\d+-F\\d+-[a-z0-9-]+$`
- Enforced via pre-commit framework

### 5. TodoWrite State File Location

**Decision:** `.claude/state/current-todo.json`

**Schema:**
```json
{
  "id": "todo-123",
  "title": "Implement component",
  "status": "in_progress",
  "updated_at": "2025-10-22T18:00:00Z"
}
```

**Hook Verification:**
- Check file exists
- Check valid JSON
- Check status is `in_progress` or `pending`
- Check freshness (<4 hours old)
- Fail commit if checks fail

---

## Success Metrics

### Quantitative
- PR commit count: Target <4 (from 10-20)
- Gate failure rate: >0 (proves gates working)
- Tool error rate: 0 (after documentation updates)
- Test masking incidents: 0
- Hook execution time: <5s

### Qualitative
- AI follows workflow without reminders
- Clear error messages when gates fail
- Improved PR review experience (smaller diffs)
- User confidence in process enforcement

---

## Related Tasks

- **P1T11-F2:** Automated workflow state machine (future)
- **P1T11-F3:** Workflow visualization dashboard (future)
- **P1T10:** CI/CD pipeline (already complete, may need integration)

---

## Review Checkpoints

1. **After Component A:** Verify documentation updates complete
2. **After Component B:** Test all gates independently
3. **After Component C:** Verify branch naming enforcement
4. **Before Final PR:** Deep review with gemini + codex

---

## Task Creation Review Checklist

**RECOMMENDED:** Before starting work, request task creation review to validate scope and requirements.

See [`./AI/Workflows/02-planning.md`](../../AI/Workflows/02-planning.md) for workflow details.

**Review validates:**
- [x] Objective is clear and measurable
- [x] Success criteria are testable
- [x] Functional requirements are comprehensive
- [x] Component breakdown follows 4-step pattern
- [x] Time estimates are reasonable (validated by gemini + codex)
- [x] Dependencies and blockers identified
- [x] ADR requirement clear for architectural changes
- [x] Test strategy comprehensive

**Review Status:**
- ✅ **APPROVED** by gemini-2.5-pro planner (2025-10-22)
- ✅ **APPROVED** by gpt-5-codex planner (2025-10-22)
- Continuation ID: 8e5e4d88-8279-4822-a547-260d546a21fa

---

## Notes

- **⚠️ CRITICAL: DO NOT TOUCH MASTER BRANCH** - All work on feature branches only!
- This task was reviewed and approved by gemini + codex using the task creation review workflow
- Investigation phase (3 hours) already complete with detailed findings documented
- Plan revised based on investigation findings (pre-commit framework, existing infrastructure)
- Will serve as reference implementation for future workflow improvements
- Implements the very patterns it documents (subfeature decomposition, systematic reviews)

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P1T11_TASK.md docs/TASKS/P1T11_PROGRESS.md

# 2. Update front matter in P1T11_PROGRESS.md:
#    state: PROGRESS
#    started: YYYY-MM-DD

# 3. Commit
git add docs/TASKS/P1T11_PROGRESS.md
git commit -m "Start P1T11: Workflow Optimization & Testing Fixes"
```

**Or use automation:**
```bash
./scripts/tasks.py start P1T11
```
