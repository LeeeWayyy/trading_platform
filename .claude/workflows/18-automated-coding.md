# Automated Coding Workflow

**Purpose:** Automate the per-component coding cycle (implement → test → review → CI → commit)
**When:** After automated planning generates component breakdown
**Prerequisites:** Component plan from automated analysis
**Expected Outcome:** All components implemented with automated review gates and CI validation

---

## Quick Reference

**Time Savings Per Component:**
- Manual: 39 minutes average (implement, test, manual review request, CI, commit)
- Automated: 30 minutes average (automation handles review requests, CI execution, commits)
- **Savings: 9 minutes per component (23% reduction)**

**What's Automated:**
- ✅ Review request invocation (clink + gemini → codex two-phase)
- ✅ Review response parsing
- ✅ Auto-fix for HIGH confidence issues (type errors, lint errors)
- ✅ CI execution (make ci-local)
- ✅ Auto-fix for CI failures (type/lint only)
- ✅ Commit with zen review approval markers
- ✅ Task state tracking updates

**What Remains Human-Guided:**
- 🧑 Implementation logic (TDD)
- 🧑 Test case creation
- 🧑 Review of auto-fixes (escalate if low confidence)
- 🧑 Complex bug fixes (logic errors)

**Quality Gates Preserved:**
- 🔒 ALL zen-mcp reviews remain MANDATORY (gemini → codex two-phase)
- 🔒 ALL reviewers must approve (gemini AND codex)
- 🔒 ALL issues from ALL reviewers must be addressed
- 🔒 ALL CI checks must pass

---

## Per-Component Automated Loop

### Input

```python
component_plan = {
    "name": "Position Limit Validation",
    "description": "Add position limit check before order submission",
    "files_to_modify": [
        "apps/execution_gateway/order_placer.py",
        "libs/risk_manager/position_limits.py"
    ],
    "test_files": [
        "tests/apps/execution_gateway/test_order_placer.py",
        "tests/libs/risk_manager/test_position_limits.py"
    ],
    "acceptance_criteria": [
        "Check position limits before every order",
        "Raise RiskViolation if limit exceeded",
        "Log limit checks with strategy_id"
    ],
    "edge_cases": [
        "Empty position (first order)",
        "At limit boundary",
        "Negative positions (shorts)"
    ]
}
```

### Step 1: Implement Logic (TDD)

**Action:** Human implements core logic with type hints and docstrings

```python
# Example implementation
# apps/execution_gateway/order_placer.py

def check_position_limit(
    self,
    symbol: str,
    order_qty: int,
    strategy_id: str
) -> None:
    """
    Check if order would exceed position limits.

    Args:
        symbol: Trading symbol
        order_qty: Order quantity (positive for buy, negative for sell)
        strategy_id: Strategy identifier

    Raises:
        RiskViolation: If order would exceed position limit
    """
    current_pos = self.position_tracker.get_position(symbol, strategy_id)
    limit = self.risk_limits.get_max_position(symbol)

    projected_pos = current_pos + order_qty

    if abs(projected_pos) > limit:
        self.logger.error(
            "Position limit exceeded",
            extra={
                "symbol": symbol,
                "strategy_id": strategy_id,
                "current_pos": current_pos,
                "order_qty": order_qty,
                "projected_pos": projected_pos,
                "limit": limit
            }
        )
        raise RiskViolation(
            f"Order would exceed position limit: "
            f"projected={projected_pos}, limit={limit}"
        )

    self.logger.info(
        "Position limit check passed",
        extra={
            "symbol": symbol,
            "strategy_id": strategy_id,
            "projected_pos": projected_pos,
            "limit": limit
        }
    )
```

**Duration:** 15-20 minutes (human-guided, same as manual)

---

### Step 2: Create Test Cases

**Action:** Human creates comprehensive test coverage

```python
# tests/apps/execution_gateway/test_order_placer.py

def test_position_limit_check_passes_when_under_limit():
    """Test position limit check allows order when under limit."""
    placer = OrderPlacer(...)

    # Mock current position: 50, limit: 100, order: 30
    # Projected: 80 (under limit)
    placer.check_position_limit("AAPL", 30, "strategy_1")

    # Should not raise

def test_position_limit_check_fails_when_exceeds_limit():
    """Test position limit check blocks order when exceeds limit."""
    placer = OrderPlacer(...)

    # Mock current position: 50, limit: 100, order: 60
    # Projected: 110 (exceeds limit)
    with pytest.raises(RiskViolation):
        placer.check_position_limit("AAPL", 60, "strategy_1")

def test_position_limit_check_at_boundary():
    """Test position limit check at exact boundary."""
    placer = OrderPlacer(...)

    # Mock current position: 50, limit: 100, order: 50
    # Projected: 100 (at limit, should allow)
    placer.check_position_limit("AAPL", 50, "strategy_1")

    # Should not raise

def test_position_limit_check_with_negative_positions():
    """Test position limit check with short positions."""
    placer = OrderPlacer(...)

    # Mock current position: -50, limit: 100, order: -60
    # Projected: -110 (exceeds limit)
    with pytest.raises(RiskViolation):
        placer.check_position_limit("AAPL", -60, "strategy_1")
```

**Duration:** 10-15 minutes (human-guided, same as manual)

---

### Step 3: Run CI Locally (Automated)

**Action:** Automated workflow executes `make ci-local` with auto-fix

```python
# Automated CI execution
ci_attempts = 0
MAX_CI_ATTEMPTS = 3

while ci_attempts < MAX_CI_ATTEMPTS:
    ci_result = run_command("make ci-local")

    if ci_result.returncode == 0:
        print("✅ CI passed")
        update_task_state(
            component="Position Limit Validation",
            step="ci",
            status="completed",
            attempts=ci_attempts + 1
        )
        break

    # Parse CI failures
    failures = parse_ci_failures(ci_result.stderr)

    # Attempt auto-fix for HIGH confidence issues
    auto_fix_applied = False
    for failure in failures:
        if failure["type"] == "type_error" and failure["confidence"] == "HIGH":
            # Auto-fix: Add missing type annotation
            apply_type_fix(failure)
            auto_fix_applied = True
        elif failure["type"] == "lint_error" and failure["confidence"] == "HIGH":
            # Auto-fix: Run formatter or remove unused import
            apply_lint_fix(failure)
            auto_fix_applied = True
        elif failure["type"] == "test_failure":
            # Test failures require human judgment
            escalate(
                reason=f"Test failure: {failure['test_name']}",
                failure=failure
            )
            return

    if not auto_fix_applied:
        escalate(
            reason=f"CI failures persist after {ci_attempts + 1} attempts",
            failures=failures
        )
        return

    ci_attempts += 1

if ci_attempts >= MAX_CI_ATTEMPTS:
    escalate(
        reason="CI failed after 3 auto-fix attempts",
        last_failures=failures
    )
```

**Auto-Fix Examples:**

```python
# Type error auto-fix
# Error: Missing return type annotation on line 42
# Before:
def get_position(self, symbol: str, strategy_id: str):
    return self.positions.get((symbol, strategy_id), 0)

# After (auto-fixed):
def get_position(self, symbol: str, strategy_id: str) -> int:
    return self.positions.get((symbol, strategy_id), 0)

# Lint error auto-fix
# Error: Unused import on line 5
# Before:
from typing import Dict, List, Optional, Tuple  # Tuple unused

# After (auto-fixed):
from typing import Dict, List, Optional
```

**Duration:** 3-5 minutes (automated, 0 human time)

---

### Step 4: Request Zen Review (Automated Two-Phase)

**Action:** Automated workflow requests clink + gemini → codex review

```python
# Phase 1: Gemini Review
review_iterations = 0
MAX_REVIEW_ITERATIONS = 3

while review_iterations < MAX_REVIEW_ITERATIONS:
    # Request gemini review
    gemini_review = mcp__zen-mcp__clink(
        cli_name="gemini",
        role="codereviewer",
        prompt=f"""Review component: {component_plan['name']}

Files changed:
{format_file_list(component_plan['files_to_modify'])}

Test files:
{format_file_list(component_plan['test_files'])}

Acceptance criteria:
{format_criteria(component_plan['acceptance_criteria'])}

Focus areas:
1. Trading safety (circuit breaker, risk checks)
2. Idempotency
3. Pattern parity (retries, logging, error handling)
4. Test coverage (success + failure + edge cases)

Deliverable:
- Approval status: APPROVED / NEEDS REVISION
- Blocking issues (if any)
- Major issues (if any)
- Minor issues (if any)
- Continuation ID for codex synthesis
""",
        absolute_file_paths=[
            *component_plan['files_to_modify'],
            *component_plan['test_files']
        ],
        continuation_id=continuation_id if review_iterations > 0 else None
    )

    # Parse gemini review
    gemini_status = parse_review_status(gemini_review)
    continuation_id = extract_continuation_id(gemini_review)

    # Phase 2: Codex Review (synthesis)
    codex_review = mcp__zen-mcp__clink(
        cli_name="codex",
        role="codereviewer",
        prompt=f"""Synthesize gemini review and provide final recommendation.

Gemini review summary:
{gemini_review}

Task: Validate gemini findings and provide:
1. Concurrence or additional concerns
2. Final approval status: APPROVED / NEEDS REVISION
3. Actionable next steps (if revision needed)
""",
        absolute_file_paths=[
            *component_plan['files_to_modify'],
            *component_plan['test_files']
        ],
        continuation_id=continuation_id
    )

    codex_status = parse_review_status(codex_review)

    # CRITICAL: BOTH reviewers must approve
    if gemini_status == "APPROVED" and codex_status == "APPROVED":
        print("✅ Review approved by BOTH gemini AND codex")
        update_task_state(
            component="Position Limit Validation",
            step="review",
            status="completed",
            continuation_id=continuation_id,
            iterations=review_iterations + 1
        )
        break

    # Extract issues from BOTH reviewers
    all_issues = extract_issues(gemini_review) + extract_issues(codex_review)

    # Attempt auto-fix for HIGH confidence issues
    auto_fix_applied = False
    for issue in all_issues:
        if issue["severity"] == "minor" and issue["confidence"] == "HIGH":
            # Auto-fix: Missing docstring, style issues
            apply_minor_fix(issue)
            auto_fix_applied = True
        elif issue["severity"] == "major" and issue["confidence"] == "HIGH":
            # Auto-fix: Missing logging, standard pattern application
            apply_major_fix(issue)
            auto_fix_applied = True
        elif issue["severity"] == "blocking":
            # Blocking issues require human judgment
            escalate(
                reason=f"Blocking issue from {issue['reviewer']}: {issue['description']}",
                issue=issue,
                continuation_id=continuation_id
            )
            return

    if not auto_fix_applied:
        escalate(
            reason=f"Review issues persist after {review_iterations + 1} iterations",
            issues=all_issues,
            continuation_id=continuation_id
        )
        return

    review_iterations += 1

if review_iterations >= MAX_REVIEW_ITERATIONS:
    escalate(
        reason="Review issues unresolved after 3 iterations",
        last_issues=all_issues,
        continuation_id=continuation_id
    )
```

**Auto-Fix Examples:**

```python
# Minor issue auto-fix: Missing docstring parameter
# Issue: "Missing docstring for parameter 'strategy_id'"
# Before:
def check_position_limit(self, symbol: str, order_qty: int, strategy_id: str) -> None:
    """
    Check if order would exceed position limits.

    Args:
        symbol: Trading symbol
        order_qty: Order quantity
    """

# After (auto-fixed):
def check_position_limit(self, symbol: str, order_qty: int, strategy_id: str) -> None:
    """
    Check if order would exceed position limits.

    Args:
        symbol: Trading symbol
        order_qty: Order quantity (positive for buy, negative for sell)
        strategy_id: Strategy identifier
    """

# Major issue auto-fix: Missing structured logging
# Issue: "Inconsistent logging - missing strategy_id in log context"
# Before:
self.logger.info(f"Position check passed for {symbol}")

# After (auto-fixed):
self.logger.info(
    "Position limit check passed",
    extra={
        "symbol": symbol,
        "strategy_id": strategy_id,
        "projected_pos": projected_pos,
        "limit": limit
    }
)
```

**Duration:** 2-3 minutes (automated, includes both gemini and codex phases)

---

### Step 5: Commit with Approval Markers (Automated)

**Action:** Automated workflow commits with zen review approval

```python
# Automated commit
git_add_files(component_plan['files_to_modify'] + component_plan['test_files'])

commit_message = f"""feat({component_category}): {component_plan['name']}

{component_plan['description']}

Acceptance criteria:
{format_criteria_for_commit(component_plan['acceptance_criteria'])}

Test coverage:
- Success path: {count_success_tests(component_plan['test_files'])} tests
- Failure path: {count_failure_tests(component_plan['test_files'])} tests
- Edge cases: {count_edge_case_tests(component_plan['test_files'])} tests

zen-mcp-review: approved (two-phase)
gemini-review: approved
codex-review: approved
continuation-id: {continuation_id}
ci-local: passed (attempts: {ci_attempts + 1})

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
"""

run_command(f'git commit -m "{commit_message}"')

# Update task state
update_task_state(
    component="Position Limit Validation",
    step="commit",
    status="completed",
    commit_hash=get_current_commit_hash()
)

print(f"✅ Committed: {component_plan['name']}")
```

**Duration:** 1-2 minutes (automated, 0 human time)

---

## Error Handling & Escalation

### Escalation Triggers

1. **CI Failures After 3 Attempts:**
```python
{
    "component": "Position Limit Validation",
    "step": "CI Local Execution",
    "reason": "Test failures persist after 3 auto-fix attempts",
    "context": {
        "failures": [...],
        "logs": "<last 50 lines>",
        "files_modified": [...]
    },
    "recommendation": "Manual review required for test logic",
    "action": "PAUSE_AUTOMATION"
}
```

2. **Review Issues After 3 Iterations:**
```python
{
    "component": "Position Limit Validation",
    "step": "Zen Review",
    "reason": "Blocking issues from gemini/codex unresolved after 3 iterations",
    "context": {
        "gemini_issues": [...],
        "codex_issues": [...],
        "continuation_id": "abc123..."
    },
    "recommendation": "Manual fix required for architecture concerns",
    "action": "PAUSE_AUTOMATION"
}
```

3. **Timeout (2 hours):**
```python
{
    "component": "Position Limit Validation",
    "step": "Auto-Fix Loop",
    "reason": "Automation runtime exceeded 2 hours",
    "context": {
        "elapsed_time_seconds": 7200,
        "current_iteration": {...}
    },
    "recommendation": "Human intervention required",
    "action": "PAUSE_AUTOMATION"
}
```

### Escalation Response

When escalation occurs:
1. Save current state to `.claude/task-state.json`
2. Present escalation details to user
3. Wait for human input (fix manually or skip component)
4. Resume automation after human intervention

---

## Full Component Loop

```python
def automated_component_cycle(component_plan):
    """
    Automate the full component coding cycle.

    Args:
        component_plan: Component specification from automated analysis

    Returns:
        ComponentResult with status and metrics
    """
    start_time = time.time()

    # Step 1: Implement (human-guided, not automated)
    print(f"🧑 Implement logic for: {component_plan['name']}")
    print(f"Files to modify: {component_plan['files_to_modify']}")
    print(f"Acceptance criteria: {component_plan['acceptance_criteria']}")
    # Wait for human to complete implementation

    # Step 2: Create tests (human-guided, not automated)
    print(f"🧑 Create test cases for: {component_plan['name']}")
    print(f"Test files: {component_plan['test_files']}")
    print(f"Edge cases to cover: {component_plan['edge_cases']}")
    # Wait for human to complete tests

    # Step 3: CI (automated)
    ci_result = run_ci_with_autofix(component_plan)
    if ci_result["status"] == "escalated":
        return ComponentResult(status="escalated", reason=ci_result["reason"])

    # Step 4: Review (automated two-phase: gemini → codex)
    review_result = run_review_with_autofix(component_plan)
    if review_result["status"] == "escalated":
        return ComponentResult(status="escalated", reason=review_result["reason"])

    # Step 5: Commit (automated)
    commit_result = commit_with_approval_markers(
        component_plan,
        review_result["continuation_id"],
        ci_result["attempts"]
    )

    elapsed_time = time.time() - start_time

    return ComponentResult(
        status="completed",
        commit_hash=commit_result["hash"],
        metrics={
            "elapsed_time_seconds": elapsed_time,
            "ci_attempts": ci_result["attempts"],
            "review_iterations": review_result["iterations"],
            "escalations": 0
        }
    )
```

---

## Integration with Task State Tracking

```bash
# Auto-update task state after each step
./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "Position Limit Validation" \
  --step "implementation" \
  --status "completed"

./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "Position Limit Validation" \
  --step "tests" \
  --status "completed" \
  --test-count 12

./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "Position Limit Validation" \
  --step "ci" \
  --status "completed" \
  --attempts 1

./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "Position Limit Validation" \
  --step "review" \
  --status "completed" \
  --continuation-id "abc123..." \
  --iterations 1

./scripts/update_task_state.py \
  --task-id P1T13-F3 \
  --component "Position Limit Validation" \
  --step "commit" \
  --status "completed" \
  --commit-hash "$(git rev-parse HEAD)"
```

---

## Success Criteria

Automated coding workflow succeeds when:

- [  ] Per-component loop implemented
- [  ] Two-phase review process (gemini → codex) automated
- [  ] BOTH reviewers must approve (no single-reviewer commits)
- [  ] ALL issues from ALL reviewers must be addressed
- [  ] CI auto-fix for HIGH confidence issues (type/lint)
- [  ] Review auto-fix for HIGH confidence issues (minor/major)
- [  ] Escalation logic functional (3 attempts max, 2 hour timeout)
- [  ] Task state tracking integrated
- [  ] Quality gates preserved (ALL reviews MANDATORY)
- [  ] Time savings measured (≥20% per component)

---

## Related Workflows

- [17-automated-analysis.md](./17-automated-analysis.md) — Generates component plan
- [03-zen-review-quick.md](./03-zen-review-quick.md) — Manual quick review (reference for automation)
- [01-git-commit.md](./01-git-commit.md) — Manual commit process (reference for automation)
- [15-update-task-state.md](./15-update-task-state.md) — Task state tracking

---

## References

- `.claude/research/automated-coding-research.md` — Research and design
- `CLAUDE.md` — Two-phase review policy (gemini → codex)
- `docs/TASKS/P1T13_F3_AUTOMATION.md` — Task document

---

**Next Step:** Integrate into full automation workflow (20-full-automation.md)
