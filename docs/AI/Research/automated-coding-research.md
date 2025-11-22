# Automated Coding Research

**Purpose:** Research and design patterns for automating the per-component coding workflow
**Date:** 2025-11-02
**Related:** P1T13-F3 Phase 3 - Automated Coding Workflow

---

## Research Questions

1. How to automate the 4-step pattern (implement → test → review → commit) per component?
2. How to handle review feedback automatically (auto-fix vs. escalate)?
3. How to integrate task state tracking (`.claude/task-state.json`)?
4. What error handling patterns are needed for robust automation?
5. How to ensure quality gates remain MANDATORY while being automated?

---

## Current Manual Workflow (Baseline)

From CLAUDE.md and existing workflows:

```markdown
FOR EACH logical component:
  1. Implement logic (TDD approach)
  2. Create test cases (comprehensive coverage)
  3. **MANDATORY: Request zen-mcp review** (clink + codex + gemini)
  4. **MANDATORY: Run `make ci-local`**
  5. Commit ONLY after review approval + CI passes
```

**Time per component:** 30-60 minutes
- Implementation: 15-20 min
- Test creation: 10-15 min
- Review: 2-3 min (quick review via clink + codex + gemini)
- CI: 3-5 min
- Commit: 1-2 min

**Pain points:**
- Manual context switching between implementation, testing, review, CI
- Forgetting review gates (documented root cause of 7 fix commits)
- CI failures discovered late (after commit attempt)
- Task state tracking often forgotten

---

## Proposed Automated Workflow

### Architecture

```
Component Plan (from automated analysis)
    ↓
FOR EACH component:
    ↓
    ┌─────────────────────────────────────┐
    │ Step 1: Implement Logic (TDD)       │
    │ - Read component specification      │
    │ - Implement with type hints         │
    │ - Add comprehensive docstrings      │
    └──────────────┬──────────────────────┘
                   ↓
    ┌─────────────────────────────────────┐
    │ Step 2: Create Test Cases           │
    │ - Success path tests                │
    │ - Failure path tests                │
    │ - Edge case tests                   │
    └──────────────┬──────────────────────┘
                   ↓
    ┌─────────────────────────────────────┐
    │ Step 3: Run CI Locally              │
    │ - make ci-local                     │
    │ - IF FAIL → parse errors            │
    │   → auto-fix (type/lint/test)       │
    │   → retry (max 3 attempts)          │
    │   → IF still fail → escalate        │
    └──────────────┬──────────────────────┘
                   ↓
    ┌─────────────────────────────────────┐
    │ Step 4: Request Zen Review          │
    │ - clink + codex + gemini            │
    │ - IF NEEDS REVISION → parse issues  │
    │   → auto-fix                        │
    │   → re-request review               │
    │   → IF 3 iterations → escalate      │
    └──────────────┬──────────────────────┘
                   ↓
    ┌─────────────────────────────────────┐
    │ Step 5: Commit + Update State       │
    │ - git add <files>                   │
    │ - git commit with zen review ID     │
    │ - Update .claude/task-state.json    │
    └─────────────────────────────────────┘
```

---

## Auto-Fix Strategies

### 1. CI Failure Auto-Fix

**Type Errors (mypy):**
```python
# Example error: "Missing return type annotation"
# Pattern: Function definition without return type

# Auto-fix strategy:
# 1. Parse error: "apps/risk_manager/monitor.py:42: Missing return annotation"
# 2. Read file around line 42
# 3. Identify function signature
# 4. Infer return type from function body or docstring
# 5. Add annotation: def function_name(...) -> <type>:
# 6. Re-run mypy to verify
```

**Lint Errors (ruff):**
```python
# Example error: "Unused import on line 15"
# Auto-fix: Remove line 15

# Example error: "Line too long (120 > 100)"
# Auto-fix: Use black formatter to wrap line
```

**Test Failures:**
```python
# Example error: "AssertionError: assert 100 == 50"
# Auto-fix strategy:
# 1. Analyze test expectation vs. actual value
# 2. Determine if test is wrong OR implementation is wrong
# 3. IF test is wrong (e.g., wrong expected value) → fix test
# 4. IF implementation is wrong → fix implementation
# 5. Gray area → escalate to human
```

**Auto-fix decision tree:**
```
IF error_type == "type_error":
    confidence = HIGH  # Type errors are mechanical fixes
    → Auto-fix (add annotations, import types)
ELIF error_type == "lint_error":
    confidence = HIGH  # Lint errors are style fixes
    → Auto-fix (remove unused imports, format lines)
ELIF error_type == "test_failure":
    IF test_logic_error:  # Wrong expected value
        confidence = MEDIUM
        → Auto-fix (update test expectation)
    ELIF implementation_bug:
        confidence = LOW  # Logic bugs are risky
        → Escalate to human
ELSE:
    → Escalate to human
```

### 2. Review Feedback Auto-Fix

**Blocking Issues:**
```
# Example: "Missing circuit breaker check before order submission"

# Auto-fix strategy:
# 1. Parse comment: file, line, issue description
# 2. Read code context (±10 lines around issue)
# 3. Identify fix pattern from established patterns
#    (e.g., circuit breaker check → add standard check)
# 4. Apply fix
# 5. Re-request review with continuation_id
```

**Major Issues:**
```
# Example: "Inconsistent error handling - some exceptions not logged"

# Auto-fix strategy:
# 1. Find all exception handlers in component
# 2. Identify missing logging calls
# 3. Add logging with proper context (strategy_id, client_order_id)
# 4. Re-request review
```

**Minor Issues (style, docs):**
```
# Example: "Missing docstring for parameter 'max_attempts'"

# Auto-fix strategy:
# 1. Parse missing docstring parameter
# 2. Infer parameter purpose from code
# 3. Add docstring entry
# 4. Re-request review
```

**Auto-fix confidence levels:**
```
blocking_issue_confidence = {
    "missing_circuit_breaker": HIGH,    # Standard pattern exists
    "missing_risk_check": HIGH,          # Standard pattern exists
    "idempotency_violation": MEDIUM,     # May need context
    "race_condition": LOW,               # Requires careful analysis
}

major_issue_confidence = {
    "inconsistent_logging": HIGH,       # Standard pattern exists
    "missing_error_handling": MEDIUM,   # May need judgment
    "performance_issue": LOW,           # Requires profiling
}

minor_issue_confidence = {
    "missing_docstring": HIGH,          # Can infer from code
    "style_violation": HIGH,            # Mechanical fix
    "naming_issue": MEDIUM,             # May need context
}
```

---

## Error Handling & Escalation

### Escalation Triggers

**1. Max Iterations Reached:**
```python
MAX_CI_FIX_ATTEMPTS = 3
MAX_REVIEW_FIX_ITERATIONS = 3

if ci_fix_attempts > MAX_CI_FIX_ATTEMPTS:
    escalate(reason="CI failures persist after 3 auto-fix attempts")

if review_iterations > MAX_REVIEW_FIX_ITERATIONS:
    escalate(reason="Review issues persist after 3 auto-fix iterations")
```

**2. Low Confidence Fixes:**
```python
if fix_confidence == "LOW":
    escalate(reason="Auto-fix confidence too low - requires human judgment")
```

**3. Timeout:**
```python
MAX_AUTOMATION_RUNTIME = 2 * 60 * 60  # 2 hours (from task doc)

if elapsed_time > MAX_AUTOMATION_RUNTIME:
    escalate(reason="Automation exceeded 2-hour runtime limit")
```

**4. Breaking Changes:**
```python
if requires_adr or requires_user_approval:
    escalate(reason="Architectural change requires human approval")
```

### Escalation Format

```python
escalation = {
    "component": "Position Limit Validation",
    "step": "CI Local Execution",
    "reason": "Test failures persist after 3 auto-fix attempts",
    "context": {
        "failures": [
            {
                "test": "test_inverse_vol_weight_calculation",
                "error": "AssertionError: assert 0.4 == 0.6",
                "fix_attempts": [
                    "Attempt 1: Updated expected value → Still failed",
                    "Attempt 2: Fixed calculation formula → Still failed",
                    "Attempt 3: Adjusted test data → Still failed"
                ]
            }
        ],
        "logs": "<last 50 lines of CI output>",
        "files_modified": ["libs/allocation/multi_alpha.py", "tests/libs/allocation/test_multi_alpha.py"]
    },
    "recommendation": "Review inverse volatility weighting formula - may need manual analysis",
    "continuation_id": "<review_continuation_id>" if in_review else None
}
```

---

## Task State Tracking Integration

### `.claude/task-state.json` Schema

```json
{
  "task_id": "P1T13-F3",
  "phase": "phase3",
  "status": "in_progress",
  "components": [
    {
      "name": "automated-coding-workflow",
      "status": "in_progress",
      "steps": {
        "implementation": {
          "status": "completed",
          "timestamp": "2025-11-02T08:00:00Z",
          "commit_hash": null
        },
        "tests": {
          "status": "completed",
          "timestamp": "2025-11-02T08:15:00Z",
          "test_count": 12,
          "commit_hash": null
        },
        "ci": {
          "status": "completed",
          "timestamp": "2025-11-02T08:20:00Z",
          "passed": true,
          "attempts": 1,
          "commit_hash": null
        },
        "review": {
          "status": "completed",
          "timestamp": "2025-11-02T08:25:00Z",
          "continuation_id": "abc123...",
          "approval_status": "APPROVED",
          "iterations": 1,
          "commit_hash": null
        },
        "commit": {
          "status": "completed",
          "timestamp": "2025-11-02T08:30:00Z",
          "commit_hash": "abc1234"
        }
      }
    }
  ],
  "automation_metrics": {
    "total_runtime_seconds": 1800,
    "ci_fix_attempts": 1,
    "review_iterations": 1,
    "escalations": 0
  }
}
```

### Update Script Integration

```bash
# Auto-update after each step
./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "automated-coding-workflow" \
  --step "implementation" \
  --status "completed" \
  --timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Update with metrics after commit
./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "automated-coding-workflow" \
  --step "commit" \
  --status "completed" \
  --commit-hash "$(git rev-parse HEAD)" \
  --metrics '{"ci_attempts": 1, "review_iterations": 1}'
```

---

## Quality Gate Preservation

**CRITICAL:** All existing zen-mcp review gates remain MANDATORY.

### Comparison: Manual vs. Automated

| Aspect | Manual Workflow | Automated Workflow |
|--------|-----------------|-------------------|
| **Review Invocation** | Human manually calls clink | Automated workflow calls clink |
| **Review Gates** | MANDATORY (codex + gemini) | MANDATORY (codex + gemini) - **UNCHANGED** |
| **Review Approval** | Human reads review response | Automated workflow parses response |
| **Fix Issues** | Human fixes manually | Automated workflow attempts auto-fix |
| **Escalation** | N/A (human always in loop) | **NEW:** Auto-escalate if fix confidence low |
| **Commit** | After human approval | After automated review approval **AND** CI pass |

**Key principle:** Automation invokes reviews, NOT bypasses them.

---

## Time Savings Projection

### Per-Component Time Breakdown

**Manual (Current):**
- Implementation: 15-20 min (human-guided)
- Test creation: 10-15 min (human-guided)
- Review request: 1 min (manual clink call)
- Review wait: 2-3 min (clink execution)
- CI execution: 3-5 min (manual make ci-local)
- Commit: 1-2 min (manual git commit)
- **Total: 32-46 min per component** (average: 39 min)

**Automated (Proposed):**
- Implementation: 15-20 min (still human-guided, automation assists)
- Test creation: 10-15 min (still human-guided, automation generates templates)
- Review request: **automated** (0 min human time)
- Review wait: 2-3 min (clink execution - same)
- CI execution: **automated** (0 min human time)
- CI auto-fix: 2-5 min (if needed, automated)
- Review auto-fix: 2-5 min (if needed, automated)
- Commit: **automated** (0 min human time)
- State update: **automated** (0 min human time)
- **Total: 25-35 min per component** (average: 30 min)

**Time Savings:** 9 minutes per component (23% reduction)

**For 5-component task:**
- Manual: 195 minutes (3.25 hours)
- Automated: 150 minutes (2.5 hours)
- **Savings: 45 minutes (23%)**

**Additional benefits:**
- Zero forgotten review gates (automation enforces)
- Zero forgotten CI runs
- Zero forgotten state updates
- **Quality increase:** Consistent application of all safety checks

---

## Implementation Risks & Mitigations

### Risk 1: Auto-Fix Introduces Bugs

**Likelihood:** MEDIUM
**Impact:** HIGH (incorrect auto-fixes corrupt implementation)

**Mitigation:**
1. Conservative auto-fix strategy (only HIGH confidence fixes)
2. Always re-run CI after auto-fix
3. Always re-request review after auto-fix
4. Escalate after 3 failed auto-fix attempts
5. Log all auto-fixes for audit trail

### Risk 2: Automation Doesn't Escalate Properly

**Likelihood:** LOW
**Impact:** HIGH (automation stuck in infinite loop)

**Mitigation:**
1. Hard timeout: 2 hours max runtime
2. Max iteration limits (3 CI attempts, 3 review iterations)
3. Explicit escalation triggers (low confidence, breaking changes)
4. User can interrupt automation at any time

### Risk 3: Quality Gates Weakened

**Likelihood:** LOW (if implemented correctly)
**Impact:** CRITICAL (defeats purpose of quality gates)

**Mitigation:**
1. **MANDATORY:** All zen-mcp reviews still execute (not bypassed)
2. **MANDATORY:** All CI checks still run (not bypassed)
3. Automation only automates invocation and parsing, NOT approval
4. Review continuation_id preserved for multi-turn conversations
5. Codex + Gemini approvals required before commit (same as manual)

### Risk 4: Task State Corruption

**Likelihood:** LOW
**Impact:** MEDIUM (state tracking unreliable)

**Mitigation:**
1. Use atomic JSON updates (read-modify-write with file locking)
2. Validate schema before write
3. Git-track task-state.json (version history)
4. Fallback to manual state update if script fails

---

## Success Criteria

Phase 3 implementation succeeds when:

- [  ] Automated coding loop implemented (18-automated-coding.md)
- [  ] 4-step pattern enforced per component (impl → test → ci → review → commit)
- [  ] Auto-fix strategies implemented (CI + review feedback)
- [  ] Escalation logic functional (max iterations, low confidence, timeout)
- [  ] Task state tracking integrated (.claude/task-state.json)
- [  ] Quality gates preserved (all zen-mcp reviews MANDATORY)
- [  ] Time savings measured (≥20% reduction per component)
- [  ] Test with sample feature (verify end-to-end automation)

---

## Next Steps

1. Create `.claude/workflows/18-automated-coding.md` with detailed automation logic
2. Implement auto-fix helpers (parse errors, apply fixes, verify)
3. Integrate task state update script calls
4. Test with simple feature (e.g., add logging to existing function)
5. Measure time savings and quality metrics
6. Update CLAUDE.md with automated coding workflow integration
7. Request zen-mcp review (clink + codex) for research + workflow

---

## References

- P1T13_F3_AUTOMATION.md - Task document with Phase 3 requirements
- .claude/workflows/17-automated-analysis.md - Automated planning pattern
- .claude/workflows/16-subagent-delegation.md - Delegation patterns
- CLAUDE.md - Existing 4-step pattern manual workflow
- .claude/workflows/03-reviews.md - Quick review process
- .claude/workflows/01-git.md - Commit workflow with review gates
