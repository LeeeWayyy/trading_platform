---
id: P1T13-F3
title: "AI Coding Automation: Context Optimization & Full-Cycle Workflow"
phase: P1
task: T13-F3
priority: P1
owner: "@development-team"
state: APPROVED
created: 2025-11-01
updated: 2025-11-01
dependencies: ["P1T13"]
estimated_effort: "12-16 hours"
related_adrs: []
related_docs: ["CLAUDE.md", ".claude/workflows/", "docs/STANDARDS/"]
features: ["context_optimization", "full_automation"]
branch: "feature/P1T13-F3-automation"
---

# P1T13-F3: AI Coding Automation - Context Optimization & Full-Cycle Workflow

**Phase:** P1 (Hardening, 46-90 days)
**Status:** PROPOSED (Awaiting gemini + codex planner approval)
**Priority:** P1 (MEDIUM-HIGH)
**Owner:** @development-team
**Created:** 2025-11-01
**Updated:** 2025-11-01
**Estimated Effort:** 12-16 hours
**Dependencies:** P1T13 (Documentation & Workflow Optimization)

**Review Status:**
- Gemini planner: ✅ APPROVED (continuation_id: 9613b833-d705-47f1-85d1-619f80accb0e)
- Codex planner: ✅ APPROVED (continuation_id: 9613b833-d705-47f1-85d1-619f80accb0e)

---

## Objective

Enable Claude CLI (AI coder) to achieve full autonomous coding cycles from planning through merge, while optimizing context usage through subagent delegation patterns.

**Two Main Goals:**

1. **Context Optimization**: Implement orchestrator-subagent delegation pattern to prevent context pollution and multiply effective working capacity

2. **Full Automation**: Realize end-to-end autonomous workflow: task planning → auto-coding → review → PR creation → auto-fix review comments → auto-fix CI failures → iterate until merge

---

## Problem Statement

### Current Pain Points

**1. Context Pollution (Primary Issue)**

Current architecture uses a single 200k context window for ALL tasks:
- Core tasks (planning, coding, reviewing) compete with non-core tasks (file search, doc lookup, analysis)
- Context fills rapidly with tangential information
- No isolation between independent subtasks
- Results in premature context exhaustion and lost continuity

**Impact:**
- ~30-40% of context wasted on non-core operations
- Session interruptions requiring manual continuation
- Lost context leads to inconsistent implementation
- Reduced working capacity per session

**2. Manual Intervention Bottlenecks**

Current workflow requires manual intervention at EVERY stage:

```
Task → [MANUAL planning] → [MANUAL coding] → [MANUAL quick review request]
    → [MANUAL commit] → [MANUAL deep review request] → [MANUAL PR creation]
    → [MANUAL read PR comments] → [MANUAL fix comments] → [MANUAL read CI logs]
    → [MANUAL fix CI failures] → [MANUAL iteration until clean]
```

**Gaps:**
- No automated planning workflow
- No self-driving coding mode
- Review requests manual (though review itself is automated via zen-mcp)
- PR creation manual
- No automated PR comment reading/addressing
- No automated CI log analysis/fixing
- No loop structure to iterate until reviewers approve

**Impact:**
- Every step requires user prompt
- User must manually check PR comments and CI status
- Slow iteration cycles (hours → days for complex PRs)
- High cognitive load on user to manage workflow state

**3. Weak Integration Points**

Existing quality gates (zen-mcp reviews) work well but:
- Not integrated into automated workflow
- No automatic application of review feedback
- No GitHub Actions integration for auto-fixing
- Manual handoff between review → fix → re-review cycles

---

## Proposed Solutions

### **Component 1: Context Optimization via Subagent Delegation (4-5 hours)**

**Architecture: Orchestrator → Sub-Agent Pattern**

Implement hub-and-spoke architecture where Claude CLI orchestrator delegates non-core tasks to specialist subagents with isolated 200k context windows.

**Design:**

```
┌─────────────────────────────────────────────┐
│   Claude CLI Orchestrator (Main Context)   │
│   - Task planning                           │
│   - Core implementation                     │
│   - Review coordination                     │
│   - Decision making                         │
│   └─────────────┬───────────────────────────┘
│                 │
│     Delegates non-core tasks ↓
│     (Provides minimal context slice)
│
├─────────────────┼─────────────────┬──────────────────┐
│                 │                 │                  │
▼                 ▼                 ▼                  ▼
┌─────────────┐ ┌──────────────┐ ┌───────────────┐ ┌──────────────┐
│ File Search │ │ Doc Lookup   │ │ Code Analysis │ │ Test Runner  │
│ Subagent    │ │ Subagent     │ │ Subagent      │ │ Subagent     │
│ (200k ctx)  │ │ (200k ctx)   │ │ (200k ctx)    │ │ (200k ctx)   │
└─────────────┘ └──────────────┘ └───────────────┘ └──────────────┘
      │                 │                 │                 │
      └─────────────────┴─────────────────┴─────────────────┘
                            │
               Returns summary results only
                      (no full context)
```

**Delegation Criteria:**

**Delegate to subagent:**
- File searches across codebase (Glob, Grep)
- Documentation lookup (non-critical reference docs)
- Code analysis for understanding (not implementation)
- Test execution and log analysis (delegatable)
- CI log analysis (delegatable)
- PR comment extraction (delegatable)

**Keep in main context:**
- Task planning and requirement analysis
- Core implementation logic
- Architecture decisions
- Review coordination (but delegate review execution via zen-mcp)
- Commit creation and PR creation
- Direct user interaction and clarification

**Implementation Approach:**

**Option A: Claude Code Agent Tool (Native Support)**

Leverage existing Claude Code `Task` tool with specialized subagent types:

```python
# Example delegation pattern
Task(
    description="Search codebase for circuit breaker implementations",
    prompt="Find all call sites of check_circuit_breaker() in apps/ and libs/. Return file:line references only, no full code.",
    subagent_type="Explore",  # Uses independent 200k context
)
# Orchestrator receives: ["apps/execution_gateway/order_placer.py:42", ...]
# Main context saved: ~15-20k tokens (no full file contents)
```

**Option B: Script-Based Hook Integration**

Create pre-task hook that spawns isolated Claude CLI instances:

```bash
# .claude/hooks/delegate_subtask.sh
#!/bin/bash
# Spawn isolated Claude CLI instance for non-core task

TASK_TYPE=$1  # "file_search", "doc_lookup", "test_run"
TASK_PROMPT=$2

# Run in isolated instance (separate context)
claude_code_cli --task "$TASK_TYPE" --prompt "$TASK_PROMPT" --return-summary

# Returns: JSON summary with minimal context
# { "results": [...], "summary": "...", "token_cost": 5000 }
```

**Option C: Hybrid Approach (Recommended)**

- Use native `Task` tool for codebase exploration (leverages Explore subagent)
- Use zen-mcp clink for code review delegation (existing pattern)
- Reserve script hooks for future custom delegation needs

**Workflow Integration:**

Update `.claude/workflows/00-analysis-checklist.md` and `01-git-commit.md`:

```markdown
## Phase 1: Comprehensive Analysis (30-60 min)

### 1. Find ALL Impacted Components (15 min)

**NEW: Use subagent delegation for search-heavy tasks**

# Delegate codebase search to Explore subagent (prevents context pollution)
Task(description="Find call sites", prompt="Search for all calls to function_name", subagent_type="Explore")

# Orchestrator receives summary: ["file1:line", "file2:line", ...]
# Main context saved: ~20k tokens
```

**Success Metrics:**

- Context usage per task reduced by ≥30% (measured via token counts)
- Main context window remains available for ≥50% longer sessions
- No loss of quality (validated via output comparison)
- Subagent delegation transparent to user (no additional prompts or clarifications required) *(Gemini suggestion)*
- Tight file/line scopes in delegated prompts to minimize context leaks *(Codex suggestion)*

**Deliverables:**

1. `.claude/workflows/16-subagent-delegation.md` - Delegation pattern guide
2. Updated workflows (00, 01, 03, 04) with delegation examples
3. `.claude/hooks/delegate_subtask.sh` - Script hook (Option B/C)
4. Baseline vs. optimized context usage metrics
5. Subagent delegation decision tree (when to delegate vs. keep in main context)
6. **Task state tracking**: Updates to `.claude/task-state.json` after each phase completion

---

### **Component 2: Full Automation Workflow (8-10 hours)**

**Architecture: Self-Driving Plan-Do-Check-Act Loop**

Implement autonomous coding cycle with zero manual intervention from task assignment through PR merge.

**Design:**

```
┌──────────────────────────────────────────────────────────────────┐
│                    FULL AUTOMATION LOOP                          │
└──────────────────────────────────────────────────────────────────┘

1. PLAN (Automated)
   ├─ Read task document (P1TXX_TASK.md)
   ├─ Run pre-implementation analysis (.claude/workflows/00-analysis-checklist.md)
   ├─ Generate implementation plan with components
   ├─ Request task creation review (clink + gemini planner)
   └─ Get user approval for plan

2. DO (Automated)
   FOR EACH component:
     ├─ Implement logic
     ├─ Create test cases (TDD)
     ├─ Run tests locally (make ci-local)
     ├─ Request quick review (clink + codex + gemini)
     ├─ IF review issues found:
     │   ├─ Auto-fix issues
     │   └─ Re-request verification
     ├─ Commit (with zen review approval in message)
     └─ Update task state (.claude/task-state.json)

3. CHECK (Automated)
   ├─ Request deep review (clink + gemini → codex)
   ├─ IF deep review issues found:
   │   ├─ Auto-fix or defer (document deferral)
   │   └─ Re-request verification
   ├─ Create pull request (gh pr create)
   └─ Monitor PR for comments and CI status

4. ACT (Automated Iteration)
   WHILE PR not approved:
     ├─ Read PR review comments (gh api)
     ├─ IF review comments exist:
     │   ├─ Parse comments (file:line + feedback)
     │   ├─ Auto-fix each comment
     │   ├─ Request verification (clink + codex)
     │   └─ Commit fixes
     ├─ Read CI failure logs (gh run view)
     ├─ IF CI failures exist:
     │   ├─ Delegate log analysis (subagent)
     │   ├─ Auto-fix failures
     │   ├─ Run tests locally first
     │   └─ Commit fixes
     ├─ Push updated branch
     └─ Wait for re-review (check every 5 min)

5. MERGE (Manual Gate)
   ├─ Notify user: "PR ready for merge"
   └─ User: gh pr merge (keep manual control)
```

**Implementation Approach:**

**A. Automated Planning Workflow (2-3 hours)**

Create `.claude/workflows/17-automated-planning.md`:

```markdown
# Automated Planning Workflow

## Trigger
User: "Implement task P1T14 autonomously"

## Process
1. Read docs/TASKS/P1T14_TASK.md
2. Run 00-analysis-checklist.md (find ALL impacted components)
3. Generate component breakdown with 4-step pattern
4. Request task creation review (clink + gemini planner)
5. Present plan to user for approval
6. IF approved → proceed to automated coding
```

**B. Automated Coding Workflow (3-4 hours)**

Create `.claude/workflows/18-automated-coding.md`:

```markdown
# Automated Coding Workflow

## Per-Component Loop
FOR EACH component in plan:
  1. Implement logic (TDD)
  2. Create test cases
  3. make ci-local (auto-fix if fails)
  4. Request quick review (clink + codex + gemini) - MANDATORY
  5. IF issues → auto-fix → re-verify
  6. Commit with zen review ID
  7. Update .claude/task-state.json
```

**C. Automated PR Review Addressing (2-3 hours)**

Create `.claude/workflows/19-automated-pr-fixes.md`:

```markdown
# Automated PR Fix Cycle

## GitHub Actions Integration

### Option 1: Polling Loop (Simpler)
WHILE PR not approved:
  1. gh pr view --json reviewDecision,comments
  2. IF new comments → parse and auto-fix
  3. gh run list --json conclusion
  4. IF CI failures → auto-fix
  5. Sleep 5 minutes
  6. Repeat

### Option 2: Webhook-Triggered (Future)
GitHub Action triggers on:
  - pull_request_review
  - check_run.completed
  - issue_comment

Invokes: .github/workflows/auto-fix-pr-comments.yml
```

**Detailed Workflow:**

```bash
# Step 1: Read PR comments
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments \
  | jq '.[] | {file: .path, line: .line, body: .body}'

# Step 2: Parse comments to actionable fixes
# Example comment:
# File: apps/execution_gateway/order_placer.py
# Line: 42
# Body: "Missing circuit breaker check before order submission"

# Step 3: Auto-fix (delegate to main orchestrator)
# - Read file context around line 42
# - Generate fix based on comment
# - Apply fix
# - Run tests

# Step 4: Request verification
# clink + codex: "Verify fix for PR comment: [comment body]"

# Step 5: Commit fix (standardized format)
git add <file>
git commit -m "fix(auto): Address PR comment #45 - Missing circuit breaker check

PR-comment-id: 12345678
File: apps/execution_gateway/order_placer.py:42
Reviewer: @gemini-code-assist

Fix: Added circuit breaker check before order submission

Zen-review: Verified fix (clink + codex)
Continuation-id: <id>"

# Note: Standardized commit format for automation (Gemini + Codex suggestion)
# Pattern: fix(auto): Address PR comment #<pr> - <brief description>
# Includes: PR-comment-id, file:line, reviewer, fix description, zen-review, continuation_id

# Step 6: Push
git push

# GitHub Actions auto-triggers re-review
```

**D. Automated CI Failure Fixing (2-3 hours)**

```bash
# Step 1: Detect CI failure
gh run list --branch feature/xxx --json conclusion
# Returns: "conclusion": "failure"

# Step 2: Get failure logs (delegate to subagent to prevent context pollution)
Task(
  description="Analyze CI failure logs",
  prompt="Extract test failures and error messages from CI logs. Return: test name, error type, stack trace summary only.",
  subagent_type="general-purpose"
)

# Step 3: Auto-fix failures
# - Identify failure type (test failure, lint error, type error)
# - Apply appropriate fix
# - Run locally first (make ci-local)

# Step 4: Commit fix
git commit -m "Fix CI failure: <test_name>

Error: <error_summary>
Fix: <fix_description>

Verified locally: make ci-local passed"
```

**Workflow Integration:**

**Master Automation Workflow** (`.claude/workflows/20-full-automation.md`):

```markdown
# Full Automation Workflow

## Usage
User: "Implement P1T14 autonomously"

## Process
1. Run automated planning (17-automated-planning.md)
2. Get user approval
3. Run automated coding (18-automated-coding.md)
4. Run deep review
5. Create PR
6. Enter automated fix loop (19-automated-pr-fixes.md)
7. Notify user when PR ready for merge

## Emergency Override
User can interrupt at any time:
- "Pause automation" → save state, wait for user
- "Resume automation" → continue from saved state
```

**Quality Gates (Preserved from Current Workflow):**

All existing zen-mcp review gates remain MANDATORY:
- Tier 1 (Quick): clink + codex + gemini before EACH commit (~2-3 min)
- Tier 2 (Deep): clink + gemini → codex before PR (~3-5 min)
- Tier 3 (Task): clink + gemini planner before starting work (~2-3 min)

**Difference:** Reviews still happen but are **invoked automatically** by workflow, not manually by user.

**Success Metrics:**

- Zero manual interventions from task assignment → PR creation
- PR comment → fix → re-review cycle: <10 minutes (down from hours)
- CI failure → fix → re-pass cycle: <15 minutes (down from hours)
- Full task completion: 1-2 days → hours (for P1-sized tasks)
- Quality maintained: 100% of commits pass zen-mcp review gates
- Max automation runtime: 2 hours before escalation to user *(Codex suggestion)*
- Max iterations per PR: 10 attempts before user notification *(Codex suggestion)*
- Automated fixes run in clean working tree (no clobbering) *(Codex suggestion)*

**Deliverables:**

1. `.claude/workflows/17-automated-planning.md` - Automated planning workflow
2. `.claude/workflows/18-automated-coding.md` - Automated coding per component
3. `.claude/workflows/19-automated-pr-fixes.md` - Automated PR comment/CI fixing
4. `.claude/workflows/20-full-automation.md` - Master orchestration workflow
5. `.github/workflows/auto-fix-pr-comments.yml` - GitHub Actions integration (optional)
6. Updated `CLAUDE.md` with full automation workflow instructions
7. Throughput metrics: before/after comparison (time to completion)
8. **Task state tracking integration**: Automated updates to `.claude/task-state.json` using `./scripts/update_task_state.py`

---

## Implementation Plan

### Phase 1: Context Optimization (4-5 hours)

**Tasks:**

1. **Research existing subagent capabilities** (30 min)
   - Review Claude Code `Task` tool documentation
   - Identify supported subagent types (Explore, general-purpose)
   - Determine native vs. script-based approach

2. **Design delegation decision tree** (1 hour)
   - Document criteria: delegate vs. keep in main context
   - Create reference examples for each task type
   - Define context slice format (what info to pass to subagent)

3. **Implement delegation pattern** (1-2 hours)
   - Option A: Use native Task tool with Explore subagent
   - Option B: Create `.claude/hooks/delegate_subtask.sh` script
   - Option C: Hybrid approach (recommended)

4. **Update workflows with delegation** (1 hour)
   - Update `00-analysis-checklist.md` (file search delegation)
   - Update `01-git-commit.md` (test execution delegation)
   - Update `06-debugging.md` (log analysis delegation)

5. **Measure context optimization** (30 min)
   - Baseline: measure context usage for sample task WITHOUT delegation
   - Optimized: measure context usage for same task WITH delegation
   - Calculate % improvement (target: ≥30%)

6. **Create delegation guide** (30 min)
   - Write `.claude/workflows/16-subagent-delegation.md`
   - Document when/how to delegate
   - Provide code examples

**Success Criteria:**
- [  ] Delegation pattern implemented (Option A/B/C chosen)
- [  ] Decision tree documented
- [  ] ≥30% context usage reduction measured
- [  ] Workflows updated with delegation examples
- [  ] Delegation guide created

---

### Phase 2: Automated Planning (2-3 hours)

**Tasks:**

1. **Design planning workflow** (1 hour)
   - Input: task document path (docs/TASKS/P1TXX_TASK.md)
   - Process: read task → analyze requirements → generate component plan
   - Output: component breakdown + task creation review request
   - Approval gate: user must approve plan before coding

2. **Implement planning automation** (1 hour)
   - Create `.claude/workflows/17-automated-planning.md`
   - Integrate 00-analysis-checklist.md (find ALL impacted components)
   - Auto-request task creation review (clink + gemini planner)
   - Present plan with acceptance criteria

3. **Test with sample task** (30 min)
   - Run automated planning on P1T13 (known good task)
   - Verify: comprehensive analysis, all components identified
   - Compare manual vs. automated plan quality

**Success Criteria:**
- [  ] Planning workflow created
- [  ] Automated planning generates comprehensive component plan
- [  ] Task creation review auto-requested
- [  ] User approval gate functional
- [  ] Test passed: plan quality ≥ manual planning

---

### Phase 3: Automated Coding (3-4 hours)

**Tasks:**

1. **Design per-component automation** (1 hour)
   - Loop structure: FOR EACH component in plan
   - Steps: implement → test → review → fix → commit
   - Quality gates: ALL zen-mcp reviews remain MANDATORY
   - Error handling: auto-retry on transient failures, escalate on persistent

2. **Implement coding loop** (1-2 hours)
   - Create `.claude/workflows/18-automated-coding.md`
   - Implement component loop with 4-step pattern
   - Auto-request quick review (clink + codex + gemini)
   - Auto-fix review issues (with verification)
   - Auto-commit with zen review ID

3. **Integrate task state tracking** (30 min)
   - Auto-update `.claude/task-state.json` after each component
   - Track: component status, commit hash, test count, review continuation_id
   - Use existing `./scripts/update_task_state.py` script

4. **Test with small feature** (1 hour)
   - Run automated coding on simple feature (e.g., add logging)
   - Verify: all components implemented, all tests pass, all reviews passed
   - Compare quality: automated vs. manual coding

**Success Criteria:**
- [  ] Coding loop implemented
- [  ] 4-step pattern enforced per component
- [  ] All zen-mcp review gates functional
- [  ] Task state auto-updated
- [  ] Test passed: quality ≥ manual coding

---

### Phase 4: Automated PR Creation & Deep Review (1-2 hours)

**Tasks:**

1. **Implement automated deep review** (30 min)
   - After all components complete → auto-request deep review
   - Use existing `.claude/workflows/04-zen-review-deep.md` pattern
   - Auto-fix or defer deep review issues
   - Get final approval before PR creation

2. **Implement automated PR creation** (30 min)
   - Use `gh pr create` with auto-generated description
   - Include: zen deep review continuation_id, component summary, deferred issues
   - Auto-request reviews from @gemini-code-assist @codex (via GitHub Actions)

3. **Test PR creation** (30 min)
   - Run full automation → PR creation
   - Verify: PR description complete, reviewers auto-requested
   - Check: deep review ID included for audit trail

**Success Criteria:**
- [  ] Deep review auto-requested after coding complete
- [  ] PR auto-created with comprehensive description
- [  ] Reviewers auto-requested via GitHub Actions
- [  ] Test passed: PR ready for review

---

### Phase 5: Automated PR Fix Cycle (3-4 hours)

**Tasks:**

1. **Implement PR comment reader** (1 hour)
   - Use `gh api` to fetch PR review comments
   - Parse: file path, line number, comment body
   - Filter: actionable comments vs. questions/approvals
   - Delegate parsing to subagent (prevent context pollution)

2. **Implement auto-fix loop** (1-2 hours)
   - FOR EACH actionable comment:
     - Read file context (subagent delegation)
     - Generate fix based on comment
     - Apply fix
     - Request verification (clink + codex)
     - Commit fix with PR comment reference
   - Re-request review after fixes

3. **Implement CI failure detector** (30 min)
   - Use `gh run list` to detect CI failures
   - Parse failure logs (delegate to subagent)
   - Extract: test name, error type, stack trace summary

4. **Implement CI auto-fix** (1 hour)
   - Identify failure type (test/lint/type error)
   - Apply appropriate fix
   - Run `make ci-local` first (local verification)
   - Commit fix with CI failure reference

5. **Test iteration loop** (30 min)
   - Simulate PR with review comments + CI failure
   - Run automated fix cycle
   - Verify: all comments addressed, CI passes, quality maintained

**Success Criteria:**
- [  ] PR comment reader functional
- [  ] Auto-fix loop addresses all comments
- [  ] CI failure detector functional
- [  ] CI auto-fix functional with local verification
- [  ] Test passed: PR goes from comments → clean

---

### Phase 6: Master Orchestration & Integration (1-2 hours)

**Tasks:**

1. **Create master workflow** (30 min)
   - Write `.claude/workflows/20-full-automation.md`
   - Orchestrate: planning → coding → PR → fix loop → merge notification
   - Include: emergency pause/resume functionality

2. **Update CLAUDE.md** (30 min)
   - Add full automation workflow instructions
   - Document: when to use, how to trigger, emergency override
   - Update: decision points for autonomous vs. manual mode

3. **Create GitHub Actions integration** (optional) (1 hour)
   - Write `.github/workflows/auto-fix-pr-comments.yml`
   - Trigger on: pull_request_review, check_run.completed
   - Invoke: automated fix cycle
   - Alternative: polling loop (simpler, no Actions needed)

4. **End-to-end test** (1 hour)
   - Run full automation on medium-sized task (P1T15-like)
   - Verify: zero manual intervention from task → PR ready
   - Measure: time to completion, quality metrics
   - Compare: automated vs. manual workflow

**Success Criteria:**
- [  ] Master orchestration workflow functional
- [  ] CLAUDE.md updated with automation instructions
- [  ] End-to-end test passed: task → PR ready
- [  ] Quality maintained: all zen-mcp gates passed
- [  ] Time to completion reduced by ≥50%

---

## Implementation Guidance

### Branch Strategy

**Recommended approach:** Single feature branch for F3 implementation

```bash
# Create F3 feature branch from master
git checkout master
git pull
git checkout -b feature/P1T13-F3-automation

# Alternative: Split into two sub-branches if components are independent
# feature/P1T13-F3a-context-optimization (Phases 1-2)
# feature/P1T13-F3b-full-automation (Phases 3-6)
```

**Rationale:** F3 is 12-16h (borderline for subfeature splitting per `.claude/workflows/00-task-breakdown.md`). Use single branch unless components become too large for single PR review (>500 lines).

---

### Documentation Lifecycle (per `.claude/workflows/07-documentation.md`)

**Timing:**

1. **Before implementation (Phase 1):**
   - Create `.claude/workflows/16-subagent-delegation.md` (outline structure only)
   - Validates documentation approach before coding

2. **During implementation (Phases 2-5):**
   - Update workflow docs as features are implemented
   - Each phase completion updates corresponding workflow guide
   - Examples: executable and tested

3. **After implementation (Phase 6):**
   - Final review of all workflow docs
   - Update `CLAUDE.md` with full automation instructions
   - Validation checklist (from `07-documentation.md`):
     - [ ] All functions have Google-style docstrings
     - [ ] Examples are executable and correct
     - [ ] Links work (no 404s)
     - [ ] Follows DOCUMENTATION_STANDARDS.md

**New workflow files:**
- `.claude/workflows/16-subagent-delegation.md` - Created in Phase 1
- `.claude/workflows/17-automated-planning.md` - Created in Phase 2
- `.claude/workflows/18-automated-coding.md` - Created in Phase 3
- `.claude/workflows/19-automated-pr-fixes.md` - Created in Phase 5
- `.claude/workflows/20-full-automation.md` - Created in Phase 6

---

### Task State Tracking (per `.claude/workflows/14-task-resume.md`, `15-update-task-state.md`)

**MANDATORY:** Update `.claude/task-state.json` after EACH phase completion

**Workflow:**

1. **Start F3 implementation:**
   ```bash
   ./scripts/update_task_state.py start \
       --task P1T13-F3 \
       --title "AI Coding Automation: Context Optimization & Full-Cycle Workflow" \
       --branch feature/P1T13-F3-automation \
       --task-file docs/TASKS/P1T13_F3_AUTOMATION.md \
       --components 6  # 6 phases

   git add .claude/task-state.json
   git commit -m "chore: Start tracking P1T13-F3 task"
   ```

2. **After EACH phase completion:**
   ```bash
   # Example: Just finished Phase 1 (Context Optimization)
   ./scripts/update_task_state.py complete \
       --component 1 \
       --commit $(git rev-parse HEAD) \
       --files .claude/workflows/16-subagent-delegation.md .claude/workflows/00-analysis-checklist.md \
       --continuation-id <zen-review-id>

   git add .claude/task-state.json
   git commit --amend --no-edit  # Include state in phase commit
   ```

3. **Finish F3 task:**
   ```bash
   # After all 6 phases complete and PR merged
   ./scripts/update_task_state.py finish

   git add .claude/task-state.json
   git commit -m "chore: Mark P1T13-F3 task complete"
   ```

**Benefit:** Auto-resume between sessions (`.claude/workflows/14-task-resume.md` automatically reconstructs context)

---

### Component Development Cycle (4-Step Pattern per `.claude/workflows/01-git-commit.md`)

**Each phase = 1 logical component** → Apply 4-step pattern:

1. **Implement** phase logic (e.g., Phase 1: delegation pattern)
2. **Create test cases** (e.g., test context usage reduction ≥30%)
3. **Request quick review** (clink + codex + gemini) - MANDATORY
4. **Run `make ci-local`** - MANDATORY
5. **Commit** after review approval + CI pass
6. **Update task state** (`.claude/task-state.json`)

**Example for Phase 1:**

```markdown
- [ ] Phase 1: Implement subagent delegation logic
- [ ] Phase 1: Create context usage measurement tests
- [ ] Phase 1: Request quick review (clink + codex)
- [ ] Phase 1: Run make ci-local
- [ ] Phase 1: Commit Phase 1 after approval
- [ ] Phase 1: Update task state
```

---

### Related Workflows Reference

**Pre-implementation:**
- `.claude/workflows/00-analysis-checklist.md` - Comprehensive analysis BEFORE coding
- `.claude/workflows/13-task-creation-review.md` - Task validation (ALREADY APPROVED)
- `.claude/workflows/00-task-breakdown.md` - Subfeature branching strategy

**During implementation:**
- `.claude/workflows/01-git-commit.md` - Progressive commits (4-step pattern)
- `.claude/workflows/03-zen-review-quick.md` - Quick review per phase
- `.claude/workflows/05-testing.md` - Test execution
- `.claude/workflows/15-update-task-state.md` - Task state tracking

**Pre-PR:**
- `.claude/workflows/04-zen-review-deep.md` - Deep review before PR
- `.claude/workflows/02-git-pr.md` - PR creation

**Documentation:**
- `.claude/workflows/07-documentation.md` - Documentation standards
- `docs/STANDARDS/DOCUMENTATION_STANDARDS.md` - Google style docstrings

---

## Success Criteria

**Overall Success:**

1. **Context Optimization:**
   - [  ] Context usage reduced by ≥30% (measured)
   - [  ] Subagent delegation transparent to user
   - [  ] No quality loss (output comparison validated)
   - [  ] Session duration increased by ≥50%

2. **Full Automation:**
   - [  ] Zero manual interventions: task assignment → PR creation
   - [  ] PR comment → fix cycle: <10 minutes (automated)
   - [  ] CI failure → fix cycle: <15 minutes (automated)
   - [  ] All zen-mcp review gates remain MANDATORY (quality preserved)
   - [  ] Time to completion reduced by ≥50% for P1 tasks

3. **Quality Gates Preserved:**
   - [  ] Tier 1 (Quick): Still runs before EACH commit
   - [  ] Tier 2 (Deep): Still runs before PR creation
   - [  ] Tier 3 (Task): Still runs before starting work
   - [  ] All reviews auto-invoked but still MANDATORY

4. **User Control Maintained:**
   - [  ] User approval required for: task plan, PR merge
   - [  ] User can pause/resume automation at any time
   - [  ] Emergency override documented and functional

**Validation:**

- Context usage metrics: baseline vs. optimized (≥30% reduction)
- Time to completion metrics: manual vs. automated (≥50% reduction)
- Quality metrics: test pass rate, review approval rate (100% maintained)
- End-to-end test: complete task autonomously with zero manual steps
- Gemini planner approval: comprehensive design, realistic estimates
- Codex planner approval: implementation feasibility, quality gates preserved

---

## Out of Scope

**Not Included in F3:**

- **Pre-commit hook automation** → Manual review requests remain (automation invokes, but still manual trigger)
- **Automated merge** → User approval required for PR merge (safety gate)
- **Multi-repo support** → Single-repo automation only
- **Custom subagent creation** → Use existing Claude Code subagents only
- **LLM fine-tuning** → Use existing Claude Sonnet 4.5 model as-is
- **GitHub Actions for review invocation** → Polling loop sufficient for MVP
- **Slack/notification integration** → Console output sufficient
- **Rollback automation** → Manual rollback remains (high-risk operation)

---

## Related Work

**Builds on:**

- P1T13: Documentation & Workflow Optimization
  - Dual-reviewer process (gemini + codex)
  - Workflow simplification (reduced token usage)
  - Unified documentation index

**Enables:**

- Autonomous task completion (user provides task, AI completes end-to-end)
- Faster iteration cycles (hours → minutes for PR feedback loops)
- Higher developer productivity (AI handles routine workflow steps)
- Better context management (orchestrator delegates to specialists)
- Scalable automation (pattern applies to all future tasks)

---

## Risk Assessment

**Risks:**

1. **Subagent Delegation Quality Loss**
   - **Impact:** Medium
   - **Mitigation:**
     - Validate output quality: subagent results vs. main context results
     - Provide sufficient context slice to subagent (not too minimal)
     - Fall back to main context if subagent result insufficient

2. **Automated Fix Cycle Generates Bad Fixes**
   - **Impact:** High (could introduce bugs)
   - **Mitigation:**
     - All fixes MUST pass zen-mcp review (clink + codex verification)
     - Run `make ci-local` BEFORE committing automated fixes
     - User can review automated commits and revert if needed
     - Emergency pause: user can stop automation at any time

3. **Context Pollution from Iteration Loops**
   - **Impact:** Medium
   - **Mitigation:**
     - Use continuation_id to preserve context across review cycles
     - Delegate log analysis to subagents (prevent main context pollution)
     - Clear strategy for context cleanup between components

4. **GitHub API Rate Limits**
   - **Impact:** Low-Medium
   - **Mitigation:**
     - Polling loop: check every 5 minutes (not continuous)
     - Use conditional requests (If-Modified-Since headers)
     - Fall back to manual notification if rate limited

5. **Automation Runaway (Infinite Loop)**
   - **Impact:** High
   - **Mitigation:**
     - Max iteration limit: 10 attempts per PR before escalating to user
     - Emergency pause functionality (user can stop at any time)
     - Timeout: 2 hours max automation runtime, then notify user
     - Clear escape hatch: user approval required for plan + merge

6. **Loss of User Control**
   - **Impact:** High (user feels disconnected from process)
   - **Mitigation:**
     - User approval REQUIRED for: task plan, PR merge
     - Console output shows all automation steps (full transparency)
     - User can pause/resume at any time
     - Emergency override documented (revert to manual workflow)

---

## Notes

- This is initial proposal (revision 1) awaiting gemini + codex planner feedback
- Focus on MVP: simple automation first, expand based on learnings
- Preserve ALL existing quality gates (zen-mcp reviews remain MANDATORY)
- User control maintained: approval required for plan + merge, can pause/resume
- Context optimization via subagent delegation addresses root cause of context pollution
- Full automation addresses manual bottlenecks but preserves safety gates

---

## Review History

**Round 1 (2025-11-01):**
- Gemini planner: ✅ **APPROVED** - "Exceptionally well-defined, ambitious, directly addresses primary bottlenecks"
- Codex planner: ✅ **APPROVED** - "Technically sound with implementation call-outs addressed"
- Continuation ID: 9613b833-d705-47f1-85d1-619f80accb0e
- Status: **APPROVED by both reviewers - Ready for implementation**

**Key Reviewer Feedback Incorporated:**

From **Gemini**:
1. Added qualitative success metric: "Subagent delegation transparent to user"
2. Standardized automated fix commit format: `fix(auto): Address PR comment #<pr> - <description>`
3. Include continuation_id in automated fix commits for traceability

From **Codex**:
1. Budget 2-3h reserve for orchestration layer (continuation_id propagation, error handling)
2. Ensure auto-commits respect existing pre-commit hooks
3. Add jitter/backoff to polling loop, serialize concurrent comment+CI fixes
4. Log continuation_ids in `.claude/task-state.json` for audit trail
5. Clarify iteration counter resets after human intervention
6. Add cumulative 2h runtime limit before escalation
7. Emergency pause must persist state atomically
8. Define telemetry/logging standards
9. Break Phase 5 into discrete modules (comment fetcher, fixer, verifier)
10. Safeguards: prevent force-push, run fixes in clean working tree

---

## References

**Research Sources:**

- AI Coding Automation: Full-cycle automation patterns (plan-do-check-act)
- Context Optimization: Orchestrator-worker patterns for LLMs
- Automated PR Fixes: GitHub Actions integration with AI review agents

**Existing Workflows:**

- `.claude/workflows/00-analysis-checklist.md` - Pre-implementation analysis
- `.claude/workflows/00-task-breakdown.md` - Task decomposition and subfeature branching
- `.claude/workflows/01-git-commit.md` - Progressive commits with zen review
- `.claude/workflows/03-zen-review-quick.md` - Quick pre-commit review
- `.claude/workflows/04-zen-review-deep.md` - Deep pre-PR review
- `.claude/workflows/07-documentation.md` - Documentation writing workflow
- `.claude/workflows/13-task-creation-review.md` - Task planning review
- `.claude/workflows/14-task-resume.md` - Auto-resume workflow
- `.claude/workflows/15-update-task-state.md` - Task state tracking
- `CLAUDE.md` - Primary guidance document
- `docs/STANDARDS/GIT_WORKFLOW.md` - Git workflow policies
- `docs/STANDARDS/DOCUMENTATION_STANDARDS.md` - Documentation standards

**Industry Tools:**

- Bito AI Code Review Agent (Claude Sonnet 3.5 for PR reviews)
- LangGraph (multi-agent orchestration framework)
- Claude Code `Task` tool (native subagent support)
