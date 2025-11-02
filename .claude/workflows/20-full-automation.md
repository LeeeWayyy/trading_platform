# Full Automation Workflow

**Purpose:** Orchestrate end-to-end automated coding from task → PR ready for merge
**When:** User requests autonomous task implementation
**Prerequisites:** Task document exists in `/docs/TASKS/P1TXX_TASK.md`
**Expected Outcome:** PR created with all reviews passed, CI green, ready for merge

---

## Quick Reference

**Usage:**
```
User: "Implement task P1T14 autonomously"
```

**Total Time Savings:**
- Manual end-to-end: 8-12 hours (analysis, coding, reviews, PR fixes)
- Automated end-to-end: 3-5 hours (automation + human oversight)
- **Savings: 5-7 hours (50-60% reduction)**

**What's Fully Automated:**
- ✅ Task document validation (gemini planner)
- ✅ Pre-implementation analysis (17-automated-analysis.md)
- ✅ Review request invocation (gemini → codex two-phase)
- ✅ CI execution and auto-fix (type/lint)
- ✅ Commit automation with approval markers
- ✅ PR creation
- ✅ PR comment parsing and auto-fix
- ✅ Task state tracking updates

**What Requires Human Input:**
- 🧑 Implementation logic (TDD)
- 🧑 Test case creation
- 🧑 Plan approval before coding
- 🧑 Final PR merge decision
- 🧑 LOW confidence fixes (escalated)

**Quality Gates (Fully Preserved):**
- 🔒 Task creation review (gemini → codex two-phase)
- 🔒 Quick review per component (gemini → codex two-phase)
- 🔒 Deep review before PR (gemini → codex)
- 🔒 ALL reviewers must approve (gemini AND codex)
- 🔒 ALL issues from ALL reviewers must be addressed
- 🔒 ALL CI checks must pass

---

## End-to-End Process

### Phase 1: Task Validation (3-5 min)

**Input:** Task document path

```python
# User triggers automation
task_id = "P1T14"
task_doc_path = f"/docs/TASKS/{task_id}_TASK.md"

# Step 1: Request task creation review (gemini → codex two-phase)
task_review = run_task_creation_review(task_doc_path)

if task_review["status"] == "NEEDS REVISION":
    escalate(
        reason="Task document issues detected",
        issues=task_review["issues"]
    )
    return

# Extract continuation_id for future reference
continuation_id = task_review["continuation_id"]
```

**Workflow:** [13-task-creation-review.md](./13-task-creation-review.md)

---

### Phase 2: Automated Planning (15-20 min)

**Action:** Generate component breakdown via automated analysis

```python
# Step 2: Run automated pre-implementation analysis
analysis_result = run_automated_analysis(task_doc_path)

# Returns:
component_plan = {
    "requirement_summary": {...},
    "impacted_components": [...],
    "tests_to_update": [...],
    "pattern_parity": {...},
    "call_sites": [...],
    "component_breakdown": [
        {
            "name": "Position Limit Validation",
            "description": "Add position limit check before order submission",
            "files_to_modify": [...],
            "test_files": [...],
            "acceptance_criteria": [...],
            "edge_cases": [...]
        },
        # ... more components
    ],
    "edge_cases": [...]
}

# Step 3: Present plan to user for approval
print("📋 Generated component plan:")
print(json.dumps(component_plan["component_breakdown"], indent=2))

user_approval = input("Approve plan? (yes/no): ")

if user_approval.lower() != "yes":
    print("❌ Plan rejected - exiting automation")
    return
```

**Workflow:** [17-automated-analysis.md](./17-automated-analysis.md)

---

### Phase 3: Automated Coding (varies by component count)

**Action:** FOR EACH component, run automated coding cycle

```python
# Step 4: Automated coding loop
for component in component_plan["component_breakdown"]:
    print(f"\n🤖 Processing component: {component['name']}")

    # Sub-step 1: Implementation (human-guided)
    print(f"🧑 Implement logic for: {component['name']}")
    print(f"Files: {component['files_to_modify']}")
    print(f"Acceptance criteria: {component['acceptance_criteria']}")

    input("Press Enter when implementation complete...")

    # Sub-step 2: Test creation (human-guided)
    print(f"🧑 Create test cases for: {component['name']}")
    print(f"Test files: {component['test_files']}")
    print(f"Edge cases: {component['edge_cases']}")

    input("Press Enter when tests complete...")

    # Sub-step 3: CI + auto-fix (automated)
    ci_result = run_ci_with_autofix(component)

    if ci_result["status"] == "escalated":
        escalate(
            reason=f"CI failures for {component['name']}",
            details=ci_result["reason"]
        )
        return

    # Sub-step 4: Review + auto-fix (automated, gemini → codex two-phase)
    review_result = run_review_with_autofix(component, continuation_id)

    if review_result["status"] == "escalated":
        escalate(
            reason=f"Review issues for {component['name']}",
            details=review_result["reason"]
        )
        return

    # Sub-step 5: Commit (automated)
    commit_result = commit_with_approval_markers(
        component,
        review_result["continuation_id"],
        ci_result["attempts"]
    )

    print(f"✅ Component completed: {component['name']}")
    print(f"   Commit: {commit_result['hash']}")

    # Update task state
    update_task_state(
        task_id=task_id,
        component=component["name"],
        step="commit",
        status="completed",
        commit_hash=commit_result["hash"]
    )
```

**Workflow:** [18-automated-coding.md](./18-automated-coding.md)

---

### Phase 4: Deep Review + PR Creation (3-5 min)

**Action:** Request deep review and create PR

```python
# Step 5: Deep review before PR (gemini → codex)
deep_review = run_deep_review(
    branch=git_current_branch(),
    continuation_id=continuation_id
)

if deep_review["status"] == "NEEDS REVISION":
    escalate(
        reason="Deep review identified blocking issues",
        issues=deep_review["issues"],
        continuation_id=deep_review["continuation_id"]
    )
    return

# Step 6: Create PR
pr_result = create_pr(
    title=f"feat({task_id}): {component_plan['requirement_summary']['objective']}",
    body=generate_pr_body(component_plan, deep_review),
    base="master"
)

print(f"✅ PR created: {pr_result['url']}")
print(f"   PR number: {pr_result['number']}")
```

**Workflows:**
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Deep review
- [02-git-pr.md](./02-git-pr.md) - PR creation

---

### Phase 5: Automated PR Fix Cycle (10-15 min per iteration)

**Action:** Poll for PR comments/CI failures and auto-fix

```python
# Step 7: Enter automated PR fix loop
pr_fix_result = automated_pr_fix_cycle(
    pr_number=pr_result["number"],
    max_iterations=10,
    timeout_hours=2
)

if pr_fix_result["status"] == "escalated":
    escalate(
        reason="PR fix cycle requires human intervention",
        details=pr_fix_result["reason"]
    )
    return

if pr_fix_result["status"] == "approved":
    print("✅ PR approved - ready for merge!")
    print(f"   Total iterations: {pr_fix_result['iterations']}")
    print(f"   Total time: {pr_fix_result['elapsed_time_seconds'] / 60:.1f} minutes")

    # Notify user
    notify_user(f"PR #{pr_result['number']} ready for merge: {pr_result['url']}")
```

**Workflow:** [19-automated-pr-fixes.md](./19-automated-pr-fixes.md)

---

## Emergency Controls

### Pause Automation

User can interrupt at any time:

```bash
# Set pause flag
echo "PAUSED" > .claude/automation-state.txt

# Automation checks this flag every loop iteration
if [ -f .claude/automation-state.txt ] && [ "$(cat .claude/automation-state.txt)" = "PAUSED" ]; then
    echo "⏸️  Automation paused by user"
    save_automation_state()
    exit 0
fi
```

### Resume Automation

User can resume from saved state:

```bash
# Clear pause flag
rm .claude/automation-state.txt

# Resume from task state
python ./scripts/resume_automation.py --task-id P1T14
```

### Abort Automation

User can abort completely:

```bash
# Set abort flag
echo "ABORTED" > .claude/automation-state.txt

# Automation cleans up and exits
if [ -f .claude/automation-state.txt ] && [ "$(cat .claude/automation-state.txt)" = "ABORTED" ]; then
    echo "🛑 Automation aborted by user"
    cleanup_automation_state()
    exit 1
fi
```

---

## Full Orchestration Script

```python
#!/usr/bin/env python3
"""
Full automation orchestrator.

Usage:
    ./scripts/auto_implement_task.py --task-id P1T14
"""

import sys
import time
import json
from pathlib import Path


def full_automation_workflow(task_id: str):
    """
    End-to-end automated task implementation.

    Args:
        task_id: Task identifier (e.g., "P1T14")

    Returns:
        AutomationResult with PR number and metrics
    """
    start_time = time.time()

    print(f"🤖 Starting full automation for {task_id}")

    # Phase 1: Task validation (3-5 min)
    print("\n" + "=" * 60)
    print("Phase 1: Task Validation (gemini → codex)")
    print("=" * 60)

    task_doc_path = f"/docs/TASKS/{task_id}_TASK.md"
    task_review = run_task_creation_review(task_doc_path)

    if task_review["status"] == "NEEDS REVISION":
        return escalate_and_exit("Task validation failed", task_review["issues"])

    continuation_id = task_review["continuation_id"]

    # Phase 2: Automated planning (15-20 min)
    print("\n" + "=" * 60)
    print("Phase 2: Automated Planning")
    print("=" * 60)

    analysis_result = run_automated_analysis(task_doc_path)
    component_plan = analysis_result["plan"]

    # Present plan for approval
    print("\n📋 Generated component plan:")
    for i, component in enumerate(component_plan["component_breakdown"], 1):
        print(f"  {i}. {component['name']}")
        print(f"     Files: {', '.join(component['files_to_modify'])}")

    user_approval = input("\nApprove plan? (yes/no): ")

    if user_approval.lower() != "yes":
        return escalate_and_exit("Plan rejected by user", None)

    # Phase 3: Automated coding (varies)
    print("\n" + "=" * 60)
    print(f"Phase 3: Automated Coding ({len(component_plan['component_breakdown'])} components)")
    print("=" * 60)

    for i, component in enumerate(component_plan["component_breakdown"], 1):
        print(f"\n🤖 Component {i}/{len(component_plan['component_breakdown'])}: {component['name']}")

        # Check for pause/abort
        if check_automation_control() == "PAUSED":
            save_automation_state(task_id, phase=3, component=i)
            return AutomationResult(status="paused")
        elif check_automation_control() == "ABORTED":
            return AutomationResult(status="aborted")

        # Automated component cycle
        component_result = automated_component_cycle(component, continuation_id)

        if component_result["status"] == "escalated":
            return escalate_and_exit(
                f"Component '{component['name']}' requires intervention",
                component_result["reason"]
            )

        print(f"   ✅ Completed: {component_result['commit_hash']}")

    # Phase 4: Deep review + PR (3-5 min)
    print("\n" + "=" * 60)
    print("Phase 4: Deep Review + PR Creation (gemini → codex)")
    print("=" * 60)

    deep_review = run_deep_review(git_current_branch(), continuation_id)

    if deep_review["status"] == "NEEDS REVISION":
        return escalate_and_exit("Deep review failed", deep_review["issues"])

    pr_result = create_pr(
        title=f"feat({task_id}): {component_plan['requirement_summary']['objective']}",
        body=generate_pr_body(component_plan, deep_review)
    )

    print(f"\n✅ PR created: {pr_result['url']}")

    # Phase 5: Automated PR fix cycle (10-15 min per iteration)
    print("\n" + "=" * 60)
    print("Phase 5: Automated PR Fix Cycle")
    print("=" * 60)

    pr_fix_result = automated_pr_fix_cycle(
        pr_number=pr_result["number"],
        max_iterations=10,
        timeout_hours=2
    )

    if pr_fix_result["status"] == "escalated":
        return escalate_and_exit("PR fix cycle requires intervention", pr_fix_result["reason"])

    # Success!
    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print("✅ AUTOMATION COMPLETE")
    print("=" * 60)
    print(f"PR #{pr_result['number']}: {pr_result['url']}")
    print(f"Total time: {elapsed / 60:.1f} minutes")
    print(f"Components: {len(component_plan['component_breakdown'])}")
    print(f"Commits: {len(component_plan['component_breakdown'])}")
    print(f"PR fix iterations: {pr_fix_result['iterations']}")

    notify_user(f"Automation complete! PR ready for merge: {pr_result['url']}")

    return AutomationResult(
        status="success",
        pr_number=pr_result["number"],
        pr_url=pr_result["url"],
        metrics={
            "total_time_seconds": elapsed,
            "components_completed": len(component_plan["component_breakdown"]),
            "pr_fix_iterations": pr_fix_result["iterations"]
        }
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Full automation orchestrator")
    parser.add_argument("--task-id", required=True, help="Task ID (e.g., P1T14)")
    args = parser.parse_args()

    try:
        result = full_automation_workflow(args.task_id)

        if result.status == "success":
            print(f"\n🎉 Success! PR #{result.pr_number} ready for merge")
            sys.exit(0)
        elif result.status == "paused":
            print("\n⏸️  Automation paused - resume with --resume flag")
            sys.exit(0)
        else:
            print(f"\n❌ Automation {result.status}")
            sys.exit(1)

    except Exception as e:
        print(f"\n💥 Automation failed: {e}")
        sys.exit(1)
```

---

## Success Metrics

### Time Savings

**Manual (Baseline):**
- Pre-implementation analysis: 60 min
- Coding (5 components × 39 min): 195 min
- Deep review: 5 min
- PR creation: 5 min
- PR fix cycle (3 iterations × 45 min): 135 min
- **Total: 400 minutes (6.7 hours)**

**Automated:**
- Task validation: 5 min (automated)
- Automated planning: 20 min (mostly automated)
- Coding (5 components × 30 min): 150 min (human + automation)
- Deep review: 5 min (automated)
- PR creation: 2 min (automated)
- PR fix cycle (3 iterations × 15 min): 45 min (automated)
- **Total: 227 minutes (3.8 hours)**

**Savings: 173 minutes (2.9 hours) per task (43% reduction)**

### Quality Preservation

**Review Coverage:**
- Task creation review: ✅ gemini → codex (two-phase)
- Quick review per component: ✅ gemini → codex (two-phase)
- Deep review before PR: ✅ gemini → codex
- ALL reviewers approve: ✅ gemini AND codex
- ALL issues addressed: ✅ from ALL reviewers

**CI Coverage:**
- CI runs per component: ✅ make ci-local
- Auto-fix for type/lint: ✅ HIGH confidence only
- Escalation for test failures: ✅ requires human

**Commit Quality:**
- Review approval markers: ✅ continuation_id included
- Standardized format: ✅ feat/fix/docs prefix
- Co-authored attribution: ✅ Claude Code bot

---

## Escalation Matrix

| Scenario | Trigger | Action |
|----------|---------|--------|
| Task validation failure | gemini/codex NEEDS REVISION | PAUSE, notify user, wait for fixes |
| Plan rejection | User declines approval | EXIT, no changes made |
| CI failures persist | 3 auto-fix attempts failed | PAUSE, escalate to user |
| Review issues persist | 3 fix iterations failed | PAUSE, escalate to user |
| PR comments LOW confidence | Cannot auto-fix | PAUSE, wait for human fix |
| Timeout (2 hours) | Elapsed time exceeded | PAUSE, save state, notify user |
| Max iterations (10) | PR fix cycle limit | PAUSE, request manual merge |

---

## Success Criteria

Full automation succeeds when:

- [  ] Task validation automated (gemini → codex two-phase)
- [  ] Automated planning generates comprehensive plan
- [  ] User approval gate functional
- [  ] Per-component automation working (18-automated-coding.md)
- [  ] Two-phase review (gemini → codex) enforced for ALL commits
- [  ] Deep review automated before PR
- [  ] PR fix cycle functional (19-automated-pr-fixes.md)
- [  ] Emergency controls working (pause/resume/abort)
- [  ] Time savings ≥40% measured
- [  ] Zero quality regression (all review gates preserved)
- [  ] Task state tracking integrated

---

## Related Workflows

- [13-task-creation-review.md](./13-task-creation-review.md) — Task validation
- [17-automated-analysis.md](./17-automated-analysis.md) — Automated planning
- [18-automated-coding.md](./18-automated-coding.md) — Per-component automation
- [19-automated-pr-fixes.md](./19-automated-pr-fixes.md) — PR fix cycle
- [04-zen-review-deep.md](./04-zen-review-deep.md) — Deep review
- [02-git-pr.md](./02-git-pr.md) — PR creation

---

## References

- `docs/TASKS/P1T13_F3_AUTOMATION.md` — Task document
- `.claude/research/automated-coding-research.md` — Research findings
- `CLAUDE.md` — Two-phase review policy (gemini → codex)

---

**🎯 Usage Example:**

```bash
# Start full automation
./scripts/auto_implement_task.py --task-id P1T14

# Automation runs autonomously:
# 1. Validates task (gemini → codex review)
# 2. Generates plan (17-automated-analysis.md)
# 3. Waits for user approval
# 4. Codes each component (18-automated-coding.md)
#    - Human implements + tests
#    - Automation handles CI + reviews + commits
# 5. Deep review + PR creation
# 6. PR fix cycle (19-automated-pr-fixes.md)
# 7. Notifies user when ready for merge

# User can pause/resume/abort at any time
```

**Expected outcome:** PR ready for merge in 3-5 hours vs. 8-12 hours manual (50-60% time savings)
