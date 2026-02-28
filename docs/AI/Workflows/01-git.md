# Git Workflows (Commits & Pull Requests)

**Purpose:** Progressive commits with review approval + PR creation workflow
**Tools:** git, gh CLI, `/review` skill, `/pr-fix` skill

---

## Workflow Overview

```
Implement → Test → /review → Commit → ... → CI → PR
```

---

## Part 1: Progressive Commits

**When:** After completing a logical component
**Prerequisites:** Code written, tests written, `/review` approved

### Commit Process

```bash
# 1. Stage changes
git add <files-for-this-component>

# 2. Run review (repeat until zero issues on first try)
/review

# 3. Commit with zen approval markers
git commit -m "feat(scope): Add feature" -m "zen-mcp-review: approved
continuation-id: <uuid-from-final-review>"
```

**⚠️ NEVER use `git commit --no-verify`** — detected by CI

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

zen-mcp-review: approved
continuation-id: <uuid>
```

**Types:** feat, fix, docs, refactor, test, chore

**Docs-only commits** (no `.py/.sh/.js/.ts/.yml/.yaml/.toml/.cfg/.ini`, `Makefile`, `Dockerfile*`, or `.claude/skills/*.md`/`.claude/commands/*.md` files) can skip zen trailers:
```
docs: update README
```

### Common Scenarios

**Scenario: Commit Blocked (missing review)**
```bash
# Run /review, get approval, add trailers to commit message
/review
```

**Scenario: Emergency Hotfix**
```bash
# With user approval ONLY
git commit -m "fix: Critical bug

ZEN_REVIEW_OVERRIDE: Emergency production fix
User approved by: [name]"
```

---

## Part 2: Pull Requests

**When:** Feature complete, all commits done, tests passing
**Prerequisites:** All commits done, tests passing, review approved

### Pre-PR Checklist

```bash
# 1. Ensure all commits done
git log master..HEAD --oneline

# 2. Run full test suite
make ci-local

# 3. Run branch review
/review branch

# 4. Push
git push -u origin <branch>
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

## Review
zen-mcp-review: approved
continuation-id: <uuid>
EOF
)"
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

### Commit blocked (missing review)

**Solution:** Run `/review`, get approval, include trailers in commit message.

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

**Solution:** Split into multiple smaller PRs or use subfeature branches.

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

**Commit:**
- [ ] `/review` approved (zero issues)
- [ ] Commit message includes `continuation-id`
- [ ] Tests passing (`make ci-local`)

**PR:**
- [ ] All commits have review trailers
- [ ] CI passing
- [ ] No merge conflicts
- [ ] Title follows convention

---

## See Also

- [/docs/STANDARDS/GIT_WORKFLOW.md](../../STANDARDS/GIT_WORKFLOW.md) - Git standards
