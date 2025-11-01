# Progressive Git Commit Workflow

**Purpose:** Safely commit code changes with mandatory zen-mcp quality gate
**When:** Every 30-60 minutes of development, after completing a logical component
**Prerequisites:** Working in feature branch, changes implemented and tested
**Expected Outcome:** Code committed with quality validation, ready for next development cycle

---

## ⚠️ MANDATORY: Component Development Cycle

**Before committing, complete the [4-step component cycle](./component-cycle.md):**

1. **Implement** the component
2. **Test** with TDD
3. **Review** via zen-mcp (clink + codex) — **NEVER skip!**
4. **Commit** after approval

**See [component-cycle.md](./component-cycle.md)** for complete pattern documentation.

---

## Quick Reference

**Git Commands:** See [Git Commands Reference](./_common/git-commands.md)
**Testing:** See [Test Commands Reference](./_common/test-commands.md)
**Zen Review:** See [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## Step-by-Step Process

### 1. Verify Feature Branch

```bash
git branch --show-current
# Expected: feature/your-branch-name, NOT master
```

**If on master:** Create feature branch immediately:
```bash
git checkout -b feature/<type>/PxTy-description
```

See [Git Commands Reference](./_common/git-commands.md) for branch naming conventions.

### 2. Stage Changes

```bash
# Stage specific files
git add apps/execution_gateway/order_placer.py tests/test_order_placer.py

# Check what's staged
git status
```

### 3. Request Zen-MCP Review (MANDATORY)

**🔒 CRITICAL QUALITY GATE - DO NOT SKIP!**

**Option A: Use slash command**
```
/zen-review quick
```

**Option B: Tell Claude directly**
```
"Review my staged changes with zen-mcp"
```

**What zen-mcp checks:**
- Circuit breaker enforcement
- Idempotent order IDs
- Position limit validation
- Race conditions
- Error handling
- Structured logging

**Wait for review** (~30 seconds)

See [Zen-MCP Review Process](./_common/zen-review-process.md) for complete Tier 1 review details.

### 4. Address ALL Findings

**Severity levels:**
- **HIGH/CRITICAL:** ❌ MUST fix before committing (safety issues)
- **MEDIUM:** ⚠️ Fix OR document deferral
- **LOW:** ℹ️ Fix if time permits

**Fix issues immediately** while context is fresh!

### 5. Re-request Review to Verify Fixes

```
"I've fixed the issues, please verify"
```

Zen-mcp uses `continuation_id` to verify fixes. **Only proceed when approved.**

### 6. Run Tests Locally

```bash
# Full CI suite (MANDATORY before commit)
make ci-local
```

See [Test Commands Reference](./_common/test-commands.md) for all testing options.

**Expected:** ✅ All tests pass, no lint errors

**If tests fail:**
- Fix the failures
- Go back to step 3 (zen-mcp review of fixes)
- Don't commit until green!

### 7. Update Task State (If Component Complete)

**After completing a component:**

```bash
./scripts/update_task_state.py complete \
    --component 2 \
    --commit $(git rev-parse HEAD) \
    --files <file-list> \
    --tests <test-count> \
    --continuation-id <from-zen-review>

git add .claude/task-state.json
git commit --amend --no-edit
```

**See Also:** [Update Task State Workflow](./15-update-task-state.md)

### 8. Commit the Changes

Use commit message format from [Git Commands Reference](./_common/git-commands.md#commit-message-format).

**Example:**
```bash
git commit -m "Add position limit validation with circuit breaker

- Implement check_position_limits() function
- Integrate circuit breaker check before validation
- Add logging for limit violations
- Handle edge case when position is None

Zen-review: Approved (continuation_id: abc123...)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

### 9. Push Regularly (Recommended)

```bash
# First time pushing this branch
git push -u origin feature/your-branch-name

# Subsequent pushes
git push
```

**Frequency:** Every 2-3 commits or end of day

### 10. Return to Development

Continue coding for another 30-60 minutes, then repeat!

---

## Decision Points

### Should I commit now?

**Commit if:**
- ✅ 30-60 minutes since last commit
- ✅ Completed logical unit of work
- ✅ Tests pass
- ✅ About to take break
- ✅ About to start risky refactoring

**Don't commit if:**
- ❌ Code doesn't compile/run
- ❌ Tests failing
- ❌ Changes incomplete mid-thought
- ❌ On master branch (create feature branch first!)

### Zen-mcp found issues - what now?

**HIGH/CRITICAL:**
1. Fix immediately
2. Re-request review
3. Only commit when approved

**MEDIUM:**
1. Fix if straightforward (< 5 min)
2. OR document deferral with follow-up task

**LOW:**
1. Fix if trivial
2. OR note for future cleanup

### Should I squash commits?

**No - keep progressive commits!**
- Clear history shows development process
- Easier debugging (git bisect)
- Can revert specific changes

**Only squash if:**
- Multiple commits fixing same typo
- Accidental debug commits
- User explicitly requests it

---

## Common Issues

### Forgot to Create Feature Branch

```bash
# Create feature branch without losing work
git checkout -b feature/descriptive-name
# Your changes come with you!
```

### Zen-MCP Server Unavailable

**Emergency override only** (user approval required):
```bash
git commit -m "Add feature X

ZEN_REVIEW_OVERRIDE: Server temporarily unavailable
Reason: [explain urgency]
Will perform post-commit review ASAP"
```

Then request zen review of commit when server returns.

### Tests Pass Locally But Fail in CI

```bash
# Common causes:
# 1. Environment differences - check .env.example
# 2. Missing dependencies
poetry install

# 3. Database migration needed
alembic upgrade head

# Fix, then go through full commit workflow again
```

### Staged Wrong Files

```bash
# Unstage specific file
git reset HEAD apps/debug_temp.py

# Unstage all
git reset HEAD
```

### Commit Message Has Typo

```bash
# If not pushed yet
git commit --amend
```

---

## Example: Normal Progressive Commit

```bash
# After 45 minutes working on position limits...

$ git add apps/execution_gateway/order_placer.py tests/test_order_placer.py

$ git status
On branch feature/position-limits
Changes to be committed:
  modified:   apps/execution_gateway/order_placer.py
  modified:   tests/test_order_placer.py

# Request zen review
"Review my staged changes with zen-mcp"

# Zen: ✅ Approved with minor suggestion to add logging

# Add logging
$ git add apps/execution_gateway/order_placer.py

"I've added logging, please verify"
# Zen: ✅ All issues resolved

# Run tests
$ make ci-local
===================== 58 passed in 2.14s ======================

# Commit
$ git commit -m "Add position limit validation with logging

- Implement check_position_limits() function
- Add circuit breaker check before validation
- Add structured logging when limits exceeded
- Add tests for limits and edge cases

Zen-review: Approved

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"

[feature/position-limits abc1234] Add position limit validation with logging
 2 files changed, 87 insertions(+), 3 deletions(-)

$ git push
```

---

## Validation Checklist

- [ ] Zen-mcp review completed and approved
- [ ] All tests pass locally (`make ci-local`)
- [ ] Commit created with clear message
- [ ] Ready for next 30-60 min development cycle

---

## Related Workflows

- [component-cycle.md](./component-cycle.md) - 4-step component development pattern
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Quick zen review details
- [15-update-task-state.md](./15-update-task-state.md) - Task state management
- [02-git-pr.md](./02-git-pr.md) - Creating pull requests
- [05-testing.md](./05-testing.md) - Running and debugging tests

## References

- [Git Commands Reference](./_common/git-commands.md) - Git operations and conventions
- [Test Commands Reference](./_common/test-commands.md) - Testing commands and patterns
- [Zen-MCP Review Process](./_common/zen-review-process.md) - Three-tier review system
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Git workflow policies
- [/docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md) - Code quality standards
