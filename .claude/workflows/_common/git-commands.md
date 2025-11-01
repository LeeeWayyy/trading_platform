# Git Commands Reference

**Purpose:** Common git operations and conventions used across all workflows.

## Branch Naming Convention

```bash
<type>/PxTy(-Fz)?-<description>
```

**Examples:**
- `feature/P1T5-circuit-breaker`
- `fix/P2T3-F1-twap-slicing`
- `docs/P0T1-adr-updates`

**Types:** `feature`, `fix`, `docs`, `refactor`, `test`, `chore`

## Common Git Operations

### Create Feature Branch
```bash
git checkout master
git pull
git checkout -b feature/P1T5-circuit-breaker
```

### Stage and Commit
```bash
git add <files>
git commit -m "feat(component): Description

- Detailed change 1
- Detailed change 2

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Push to Remote
```bash
git push -u origin feature/P1T5-circuit-breaker
```

### Create Pull Request
```bash
gh pr create --title "feat(component): Description" --body "$(cat <<'EOF'
## Summary
- Change summary

## Test plan
- [ ] Tests pass locally
- [ ] CI passes
- [ ] Deep review completed

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Commit Message Format

**Structure:**
```
<type>(<scope>): <subject>

<body>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

**Types:** `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

## See Also

- [Git Workflow Standards](/docs/STANDARDS/GIT_WORKFLOW.md) - Complete git standards
- [Git Commit Workflow](../01-git-commit.md) - Step-by-step commit process
- [Git PR Workflow](../02-git-pr.md) - Step-by-step PR creation
