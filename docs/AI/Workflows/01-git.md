# Git Workflows (Commits & Pull Requests)

**Purpose:** Progressive commits with quality gates + PR creation workflow
**Tools:** git, gh CLI, workflow_gate.py, zen-mcp reviews
**Policy:** [Component Cycle](./12-component-cycle.md), [Reviews](./03-reviews.md)

---

## Workflow Overview

```
Component Cycle → Commit (every 30-60 min) → ... → Deep Review → PR
    ↓
1. Implement
2. Test
3. Review (zen-mcp)
4. Commit (after approval)
```

**See:** [12-component-cycle.md](./12-component-cycle.md) for complete 4-step pattern

---

## Part 1: Progressive Commits

**When:** Every 30-60 minutes after completing a logical component
**Prerequisites:** Component cycle complete (implement → test → review → ready to commit)

###🔒 Workflow Gate Enforcement (AUTOMATIC)

**Pre-commit hook blocks commits unless:**
1. ✅ Current step = `review`
2. ✅ Zen-MCP review = `APPROVED`
3. ✅ CI passed (`make ci-local`)

**Workflow commands:**
```bash
# Set component
./scripts/workflow_gate.py set-component "Component Name"

# Advance through steps
./scripts/workflow_gate.py advance test
./scripts/workflow_gate.py advance review

# Record review approval
./scripts/workflow_gate.py record-review <continuation_id> APPROVED

# Record CI pass
make ci-local && ./scripts/workflow_gate.py record-ci true

# Check status if blocked
./scripts/workflow_gate.py status
```

**⚠️ NEVER use `git commit --no-verify`** — Bypasses quality gates, detected by CI

### Context-Aware Commits

**Check context before commit:**
```bash
./scripts/workflow_gate.py check-context
```

**Thresholds:**
- **< 70%:** ✅ OK - Continue
- **70-84%:** ⚠️ Delegation RECOMMENDED
- **≥ 85%:** 🚨 Delegation MANDATORY

**If ≥70%, delegate non-core work:**
```bash
./scripts/workflow_gate.py record-delegation "Task description"
# Context resets to 0 after delegation
```

**See:** [16-subagent-delegation.md](./16-subagent-delegation.md)

### Commit Process

```bash
# 1. Stage changes for current component only
git add <files-for-this-component>
git status

# 2. Zen-mcp quick review (if not done via workflow_gate.py)
# See 03-reviews.md Tier 1

# 3. Commit with zen approval markers
git commit -m "$(cat <<'EOF'
feat(scope): Add feature

- Implementation details
- Zen review findings addressed

zen-mcp-review: approved
continuation-id: abc123-def456

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

zen-mcp-review: approved
continuation-id: <id>        # Quick review (single continuation)
OR
gemini-continuation-id: <id> # Deep review (dual phase)
codex-continuation-id: <id>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

**Types:** feat, fix, docs, refactor, test, chore

**Review Marker Formats:**
- **Quick Review:** Single `continuation-id` for combined gemini→codex review
- **Deep Review:** Separate `gemini-continuation-id` + `codex-continuation-id` for dual-phase review
**Scope:** component, service, or area

### Common Scenarios

**Scenario: Commit Blocked**
```bash
$ git commit -m "message"
# ❌ Blocked: Missing zen-mcp review

# Fix: Check status
$ ./scripts/workflow_gate.py status
# Current Step: test (must be 'review')
# Zen Review: NOT_REQUESTED

# Request review, record approval, commit
```

**Scenario: Multiple Components**
```bash
# Component 1
./scripts/workflow_gate.py set-component "Component A"
# ... implement, test, review, commit

# Component 2 (workflow resets automatically after commit)
./scripts/workflow_gate.py set-component "Component B"
# ... implement, test, review, commit
```

**Scenario: Emergency Hotfix**
```bash
# With user approval ONLY
git commit -m "fix: Critical bug

ZEN_REVIEW_OVERRIDE: Emergency production fix
Reason: [justification]
Will request post-commit review ASAP"
```

---

## Part 2: Pull Requests

**When:** Feature complete, all commits done, tests passing
**Prerequisites:** All component cycles complete, deep review approved

### Pre-PR Checklist

```bash
# 1. Ensure all commits done
git log master..HEAD --oneline

# 2. Run full test suite
make ci-local

# 3. Deep review (MANDATORY)
# See 03-reviews.md Tier 2
"Review all branch changes with zen-mcp deep review (master..HEAD)"

# 4. Get approval
# Result: ✅ "Approved - Ready for PR"
# Save continuation_id for PR description
```

### PR Creation

**Basic PR:**
```bash
gh pr create \
  --title "feat(scope): Feature description" \
  --body "$(cat <<'EOF'
## Summary
- Main change 1
- Main change 2
- Main change 3

## Implementation
Brief overview of approach

## Testing
- Test coverage added
- All tests passing

## Zen Deep Review
Status: Approved
Continuation-id: abc123-def456

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**PR with Deferred Issues:**
```markdown
## Summary
...

## Deferred from Zen Review
- **MEDIUM:** Optimize query in get_positions()
- Reason: Requires profiling analysis
- Follow-up: Task P1T15
- Continuation-id: xyz789

## Zen Deep Review
Status: Approved with deferrals
Continuation-id: abc123-def456
```

### PR Title Conventions

- `feat(component): Add feature` — New functionality
- `fix(component): Fix bug` — Bug fix
- `refactor(component): Refactor logic` — Code restructuring
- `docs: Update documentation` — Documentation only
- `test(component): Add tests` — Test additions
- `chore: Update dependencies` — Maintenance

### Multi-Commit PR Organization

**Good commit history:**
```
feat(limits): Component 1 - Implement limit checks
feat(limits): Component 2 - Add circuit breaker integration
feat(limits): Component 3 - Add monitoring
docs(limits): Update ADR and concept docs
```

**Each commit:**
- Single logical component
- Passed zen-mcp quick review
- Passing tests
- Self-contained change

### After PR Created

```bash
# Get PR URL
gh pr view --web

# Monitor CI
gh pr checks

# Address review comments (new commits)
# After changes:
git push

# Request re-review if needed
gh pr review --approve  # (by reviewer)
```

### Draft PRs

**Use for WIP or early feedback:**
```bash
gh pr create --draft \
  --title "WIP: feat(component): Feature" \
  --body "Work in progress. Feedback welcome on approach."
```

**Mark ready when:**
- All components complete
- Deep review approved
- CI passing

```bash
gh pr ready
```

---

## Common Issues

### Commit blocked by workflow gate

**Solution:**
```bash
./scripts/workflow_gate.py status  # Shows what's missing
# Fix prerequisites (review, CI) then retry commit
```

### Forgot to request zen review

**Solution:**
```bash
# Request review now
"Quick review my staged changes"

# Record approval
./scripts/workflow_gate.py record-review <id> APPROVED

# Retry commit
git commit -m "message"
```

### PR conflicts with master

**Solution:**
```bash
# Update from master
git checkout master
git pull
git checkout feature/branch
git merge master

# Resolve conflicts
git add <resolved-files>
git commit

# Re-run tests
make ci-local

# Push
git push
```

### PR too large (>500 lines)

**Solution:**
- Split into multiple smaller PRs
- Or use subfeature branches (P1T13-F1, F2, F3)
- See [02-planning.md](./02-planning.md#when-to-use-subfeatures)

### Need to amend last commit

**⚠️ Only if:**
- Commit not pushed yet
- OR fixing pre-commit hook changes
- Check authorship first: `git log -1 --format='%an %ae'`

```bash
# Make changes
git add <files>
git commit --amend --no-edit

# Or with new message
git commit --amend -m "new message"
```

**NEVER amend:**
- Other developers' commits
- Commits already pushed (use new commit instead)

---

## Validation Checklists

**Commit succeeded:**
- [ ] Workflow gate passed (step=review, review=APPROVED, CI=PASSED)
- [ ] Zen-mcp quick review completed
- [ ] Commit message includes continuation_id
- [ ] Only component-specific files staged
- [ ] Tests passing

**PR created:**
- [ ] Deep review approved
- [ ] All commits follow progressive pattern
- [ ] PR description includes zen continuation_id
- [ ] CI passing
- [ ] No merge conflicts
- [ ] Title follows convention

---

## See Also

- [12-component-cycle.md](./12-component-cycle.md) - 4-step pattern (MANDATORY)
- [03-reviews.md](./03-reviews.md) - Quick & deep review workflows
- [02-planning.md](./02-planning.md) - Task breakdown and subfeatures
- [16-subagent-delegation.md](./16-subagent-delegation.md) - Context optimization
- [/docs/STANDARDS/GIT_WORKFLOW.md](../STANDARDS/GIT_WORKFLOW.md) - Git standards
