# Progressive Git Commit Workflow

**Purpose:** Safely commit code changes with mandatory zen-mcp quality gate
**Prerequisites:** Working in a feature branch (not master), changes implemented and tested
**Expected Outcome:** Code committed with quality validation, ready for next development cycle
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Commit EVERY 30-60 minutes of active development:**
- After implementing a logical component
- After tests pass for that component
- Before taking a break or ending session
- Before attempting risky refactoring
- When switching between different parts of a feature

**Frequency is critical for:**
- Regular backups of work in progress
- Easier debugging (bisect to find regressions)
- Clear development history
- Ability to resume after interruptions

---

## ⚠️ MANDATORY: Component Development Cycle

**CRITICAL:** Before committing, every logical component MUST complete the [4-step component development cycle](./component-cycle.md):

1. **Implement** the component
2. **Test** with TDD
3. **Review** via zen-mcp (clink + codex)
4. **Commit** after approval

**See [component-cycle.md](./component-cycle.md)** for:
- Complete 4-step pattern documentation
- Todo template for each component
- Usage checklist and anti-patterns
- Examples and FAQ

---

## Step-by-Step Process

### 1. Verify You're on a Feature Branch

```bash
git branch --show-current
```

**Expected:** Should show `feature/your-branch-name`, NOT `master`

**What this does:** Ensures you're not committing directly to master (violation of workflow rules)

**If on master:**
```bash
# Create feature branch immediately
git checkout -b feature/descriptive-name
```

### 2. Stage Your Changes

```bash
# Stage specific files
git add apps/execution_gateway/order_placer.py tests/test_order_placer.py

# Or stage all modified files (use with caution)
git add -u

# Check what's staged
git status
```

**What this does:** Prepares changes for commit and review

### 3. Request Zen-MCP Review (MANDATORY)

**This is the critical quality gate - DO NOT SKIP!**

**Option A: Use slash command (recommended)**
```
/zen-review quick
```

**Option B: Tell Claude directly**
```
"Review my staged changes with zen-mcp"
```

**What zen-mcp checks:**
- 🛡️ Circuit breaker checks before order placement
- 🔄 Idempotent order IDs (no duplicates)
- 📊 Position limit validation
- 🔐 Race conditions and concurrency issues
- 📝 Proper error handling
- 🔍 Structured logging with context

**Wait for review results** (~30 seconds)

### 4. Address ALL Findings

**Severity levels and required actions:**

**HIGH/CRITICAL (Blocking):**
- ❌ MUST fix before committing
- These are safety issues that could cause:
  - Duplicate orders
  - Circuit breaker bypasses
  - Race conditions
  - Money-losing bugs

**MEDIUM:**
- ⚠️ MUST fix OR document deferral reason
- Examples: Missing logging, unclear variable names, incomplete error handling

**LOW:**
- ℹ️ Fix if time permits
- Examples: Code style suggestions, minor optimizations

**Fix the issues immediately** (context is fresh!)

### 5. Re-request Review to Verify Fixes

**After fixing issues, verify with zen-mcp:**

```
"I've fixed the issues, please verify"
```

**Zen-mcp will use continuation_id** to remember the previous review context and verify your fixes.

**Only proceed when zen-mcp approves** (or user explicitly overrides)

### 6. Run Tests Locally

```bash
# Run full test suite
make test

# Run specific tests if you know what changed
pytest tests/apps/execution_gateway/test_order_placer.py -v

# Run linting
make lint
```

**Expected:** ✅ All tests pass, no lint errors

**What this does:** Validates code works before committing (prevents CI failures)

**If tests fail:**
- Fix the failures
- Go back to step 3 (zen-mcp review of fixes)
- Don't commit until green!

### 7. Update Documentation (If Task State Changed)

**Check if documentation needs updating:**

#### Task Lifecycle Changes
If you just completed a subfeature or entire task:

**For subfeature completion (PxTy-Fz):**
1. Ensure all 4-step components are marked complete in TodoWrite
2. Create PR for subfeature (see [02-git-pr.md](./02-git-pr.md))
3. After PR merged, update task progress file (PxTy_PROGRESS.md)

**For entire task completion (PxTy):**
1. Ensure all subfeatures (if any) are merged
2. Rename `PxTy_PROGRESS.md` to `PxTy_DONE.md`
3. Update phase planning document (Px_PLANNING.md)

**See:** [`.claude/workflows/00-task-breakdown.md`](./00-task-breakdown.md) for subfeature workflow details

#### Update Planning Documents (MANDATORY)

**A. Update Phase Planning (Px_PLANNING.md):**
```markdown
**Completed:**
- ✅ P1T5 - Real-Time Market Data (Oct 20, PR#25)

**Status:** 8/11 tasks complete (73%)

**Last Updated:** October 20, 2024
```

**B. Update Task Index (docs/TASKS/INDEX.md):**

Move completed task from "Remaining" to "Completed" section:
```markdown
**Completed Tasks (8):**
- [P1T5_DONE.md](./P1T5_DONE.md) - Real-Time Market Data

**Remaining Tasks (3 tasks not started):**
- T6: Advanced Trading Strategies
```

Update quick status table:
```markdown
| Phase | Tasks | TASK | PROGRESS | DONE |
| P1    | 11    | 3    | 0        | 8    |
```

**When to skip:** If this is a mid-task commit (component not fully complete), skip this step.

**See Also:** [Phase Management Workflow](./12-phase-management.md) for detailed documentation update process.

### 8. Update Task State (MANDATORY for component completion)

**After completing a component**, update `.claude/task-state.json` to enable auto-resume:

```bash
# Example: Just completed Component 2
./scripts/update_task_state.py complete \
    --component 2 \
    --commit $(git rev-parse HEAD) \
    --files libs/allocation/multi_alpha.py tests/libs/allocation/test_multi_alpha.py \
    --tests 8 \
    --continuation-id 272e6449-85d2-4476-8f26-389a3820374f

# Stage the updated state
git add .claude/task-state.json

# Amend the component commit to include state update
git commit --amend --no-edit
```

**What this does:**
- Increments `completed_components` counter
- Updates `completion_percentage`
- Adds component to `completed_work` with metadata
- Advances `current_component` to next component
- Preserves `continuation_id` for review chain
- Enables automatic resume in next session

**When to skip:** Only skip if this is NOT a component completion (e.g., minor fix, documentation update).

**See Also:** [Update Task State Workflow](./15-update-task-state.md) for detailed guidance.

### 9. Write Commit Message

**Format:**
```bash
git commit -m "Brief summary (50 chars max)

- Bullet point 1 describing change
- Bullet point 2 describing change
- Reference ticket/ADR if applicable

Zen-review: Approved (continuation_id: abc123...)"
```

**Good commit messages:**
```bash
git commit -m "Add position limit validation with circuit breaker check

- Implement check_position_limits() function
- Integrate circuit breaker check before validation
- Add logging for limit violations
- Handle edge case when position is None

Fixes #T5
Zen-review: Approved"
```

**Bad commit messages:**
```bash
git commit -m "Fixed stuff"              # Too vague
git commit -m "Updates"                  # No information
git commit -m "WIP"                      # No description
```

**What this does:** Creates clear history for debugging and understanding changes later

### 10. Commit the Changes

```bash
git commit
# (Your editor opens with the message from step 9)
# Save and close
```

**Or use inline message for simple commits:**
```bash
git commit -m "Add position limit logging and edge case handling

- Add structured logging when limits exceeded
- Handle None position gracefully
- Add test for edge case

Zen-review: Approved"
```

**Expected:** Commit created successfully

### 11. Push Regularly (Optional but Recommended)

```bash
# First time pushing this branch
git push -u origin feature/your-branch-name

# Subsequent pushes
git push
```

**What this does:** Backs up your work to remote repository

**Frequency:** Every 2-3 commits or end of day

### 12. Return to Development

Continue coding for another 30-60 minutes, then repeat this workflow!

---

## Decision Points

### Should I commit now?

**Commit if:**
- ✅ 30-60 minutes have passed since last commit
- ✅ You've completed a logical unit of work
- ✅ Tests pass for what you've implemented
- ✅ About to take a break or end session
- ✅ About to start risky refactoring

**Don't commit if:**
- ❌ Code doesn't compile/run
- ❌ Tests are failing for your changes
- ❌ Changes are incomplete mid-thought
- ❌ You're on master branch (create feature branch first!)

### Zen-mcp found issues - what now?

**If HIGH/CRITICAL:**
1. Fix immediately (don't defer)
2. Re-request review
3. Only commit when approved

**If MEDIUM:**
1. Fix if straightforward (< 5 min)
2. OR document deferral: "Deferred: Will address in separate commit because..."
3. Create TODO or follow-up task

**If LOW:**
1. Fix if trivial
2. OR note for future cleanup
3. Don't let LOW issues block progress

### Should I squash commits later?

**No - keep progressive commits!**
- ✅ Clear history shows development process
- ✅ Easier to debug (git bisect)
- ✅ Can revert specific changes
- ✅ PR reviewers see incremental progress

**Don't squash unless:**
- Multiple commits fixing same typo
- Accidental commits of debug code
- User explicitly requests it

---

## Common Issues & Solutions

### Issue: Forgot to Create Feature Branch

**Symptom:** You're on `master` and have uncommitted changes

**Solution:**
```bash
# Create feature branch without losing work
git checkout -b feature/descriptive-name

# Your changes come with you!
# Now continue with normal commit workflow
```

### Issue: Zen-MCP Server Unavailable

**Symptom:** Zen review request times out or errors

**Solution (Emergency Only):**
```bash
# Only use if zen truly unavailable AND user approves
git commit -m "Add feature X

ZEN_REVIEW_OVERRIDE: Server temporarily unavailable
Reason: [explain urgency]
Will perform post-commit review and create follow-up PR if issues found"
```

**Then:**
- Request zen review of commit ASAP when server returns
- Create follow-up PR if issues found
- Document in team chat

### Issue: Tests Pass Locally But Fail in CI

**Symptom:** Committed code, pushed, CI fails

**Solution:**
```bash
# Don't panic! Common causes:

# 1. Environment differences
# Check .env.example vs your .env

# 2. Missing dependencies
poetry install

# 3. Database migration needed
alembic upgrade head

# 4. Tests depend on specific data
# Check test fixtures and data setup

# Fix the issue, then:
git add <fixed files>
# Go through full commit workflow again
```

### Issue: Staged Wrong Files

**Symptom:** `git status` shows files you don't want to commit

**Solution:**
```bash
# Unstage specific file
git reset HEAD apps/debug_temp.py

# Unstage all, start over
git reset HEAD

# Then stage only what you want
git add <correct files>
```

### Issue: Commit Message Has Typo

**Symptom:** Just committed, noticed typo in message

**Solution:**
```bash
# If not pushed yet
git commit --amend

# Edit message, save, done

# If already pushed - leave it (not worth force push for typo)
```

---

## Examples

### Example 1: Normal Progressive Commit

```bash
# Working on position limits for 45 minutes...

$ git add apps/execution_gateway/order_placer.py tests/test_order_placer.py

$ git status
On branch feature/position-limits
Changes to be committed:
  modified:   apps/execution_gateway/order_placer.py
  modified:   tests/test_order_placer.py

# Request zen review
"Review my staged changes with zen-mcp"

# Zen responds: ✅ Approved with minor suggestion to add logging

# Add logging
$ git add apps/execution_gateway/order_placer.py

# Verify fix
"I've added logging, please verify"

# Zen responds: ✅ All issues resolved

# Run tests
$ make test
===================== 58 passed in 2.14s ======================

# Commit
$ git commit -m "Add position limit validation with logging

- Implement check_position_limits() function
- Add circuit breaker check before validation
- Add structured logging when limits exceeded
- Add tests for limits and edge cases

Zen-review: Approved"

[feature/position-limits abc1234] Add position limit validation with logging
 2 files changed, 87 insertions(+), 3 deletions(-)

# Push
$ git push

# Continue developing...
```

### Example 2: Zen Review Catches Critical Issue

```bash
$ git add apps/execution_gateway/order_placer.py

"Review my staged changes with zen-mcp"

# Zen responds: ❌ CRITICAL - Missing circuit breaker check!

# Fix immediately
# Add: if self.breaker.is_tripped(): raise CircuitBreakerTripped()

$ git add apps/execution_gateway/order_placer.py

"I've added the circuit breaker check, please verify"

# Zen responds: ✅ Fixed correctly, approved

$ make test && make lint
# All pass

$ git commit -m "Add position validation with circuit breaker

- Add check_position_limits() with breaker check
- Prevents validation when breaker tripped
- Add tests

Zen-review: Critical issue found and fixed"

# Issue caught and fixed in 2 minutes, not in PR review days later!
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] Zen-mcp review completed and approved
- [ ] All tests pass locally (`make test`)
- [ ] Linting passes (`make lint`)
- [ ] Commit created with clear message
- [ ] You're ready for next 30-60 min development cycle

**What to check if something seems wrong:**
- Check `git log` - commit should be visible
- Check `git status` - should say "nothing to commit, working tree clean"
- Check `git branch --show-current` - should show your feature branch
- Verify zen review was actually performed (check continuation_id)

---

## Related Workflows

- [03-zen-review-quick.md](./03-zen-review-quick.md) - Details on quick zen review process
- [04-zen-review-deep.md](./04-zen-review-deep.md) - Comprehensive review before PR
- [15-update-task-state.md](./15-update-task-state.md) - Update task state for auto-resume
- [02-git-pr.md](./02-git-pr.md) - Creating pull request after feature complete
- [05-testing.md](./05-testing.md) - Running and debugging tests
- [06-debugging.md](./06-debugging.md) - When tests fail or bugs occur

---

## References

**Standards & Policies:**
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Git workflow policies
- [/docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md) - Code quality standards
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Testing requirements

**Implementation Guides:**
- [/docs/CONCEPTS/workflow-optimization-zen-mcp.md](../../docs/CONCEPTS/workflow-optimization-zen-mcp.md) - Zen-MCP integration details

---

**Maintenance Notes:**
- Update when zen-mcp review process changes
- Review quarterly or when git workflow updated
- Notify @development-team if substantial changes needed
