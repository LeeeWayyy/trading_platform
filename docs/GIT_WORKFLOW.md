# Git Workflow & Pull Request Automation

## Overview

This document explains how to work with Git and automate pull request creation when using Claude Code or other AI assistants.

## Prerequisites

### 1. Install GitHub CLI

```bash
# macOS
brew install gh

# Linux
# See https://github.com/cli/cli/blob/trunk/docs/install_linux.md

# Windows
# See https://github.com/cli/cli#windows
```

### 2. Authenticate GitHub CLI

```bash
# Authenticate with GitHub
gh auth login

# Select:
# - GitHub.com
# - HTTPS
# - Login with a web browser (recommended)
# - Follow the prompts

# Verify authentication
gh auth status
```

### 3. Configure Git

```bash
# Set your identity (if not already done)
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Recommended: Set default branch name
git config --global init.defaultBranch main

# Recommended: Enable commit signing (optional but good practice)
git config --global commit.gpgsign true
```

## Automated PR Workflow with Claude Code

### Workflow Overview

When you ask Claude Code to implement a feature, it can automatically:

1. ✅ Create a feature branch
2. ✅ Make code changes
3. ✅ Write tests
4. ✅ Run tests and linting
5. ✅ Commit changes with descriptive messages
6. ✅ Push to remote repository
7. ✅ Create pull request with detailed description
8. ✅ Link related ADRs and documentation

### How to Enable Automatic PR Creation

**Option 1: Explicit Request (Recommended for Learning)**
```
User: "Implement idempotent order submission (ticket T4),
       then create a PR when done"
```

**Option 2: Default Behavior (Configure in Prompts)**

Add to your `prompts/assistant_rules.md`:
```markdown
## Automatic Pull Request Creation

After successfully implementing and testing any feature:
1. Create a feature branch (if not already on one)
2. Commit all changes with descriptive messages
3. Push to remote repository
4. Create a pull request using `gh pr create`
5. Include in PR description:
   - Summary of changes
   - Related ADR references
   - Testing completed
   - Checklist from /docs/TESTING.md
```

**Option 3: Use a Slash Command**

Create `.claude/commands/implement-and-pr.md`:
```markdown
You are implementing a ticket and creating a PR afterwards.

Workflow:
1. Ask user which ticket to implement
2. Read ticket from /docs/TASKS/
3. Follow implementation process from CLAUDE.md
4. After validation phase passes:
   a. Create feature branch if not exists
   b. Commit all changes
   c. Push to remote
   d. Create PR with gh pr create
5. Return PR URL to user
```

Then use: `/implement-and-pr`

## PR Creation Template

When Claude creates a PR, it should use this structure:

```markdown
## Summary
Brief description of what was implemented (1-2 sentences).

## Related Work
- Ticket: #T4 (or link to /docs/TASKS/P0_TICKETS.md#t4)
- ADR: ADR-0004 (if applicable)
- Implementation Guide: /docs/IMPLEMENTATION_GUIDES/phase-6-execution-gateway.md

## Changes Made
- [ ] Implemented `deterministic_id()` function
- [ ] Added order deduplication in `place_order()` endpoint
- [ ] Created unit tests for ID generation
- [ ] Created integration tests for duplicate detection
- [ ] Updated OpenAPI spec with new error responses
- [ ] Added concept documentation for idempotency

## Testing Completed
- [x] Unit tests pass (`make test`)
- [x] Linting passes (`make lint`)
- [x] Manual testing in DRY_RUN mode
- [x] Contract tests against OpenAPI spec
- [x] Integration test with Alpaca paper API

## Documentation Updated
- [x] /docs/CONCEPTS/idempotency.md created
- [x] /docs/IMPLEMENTATION_GUIDES/phase-6-execution-gateway.md updated
- [x] ADR-0004 created and accepted
- [x] Code has comprehensive docstrings
- [x] OpenAPI spec updated

## Educational Value
This PR demonstrates:
- Hash-based idempotency pattern
- Retry safety without duplicates
- Deterministic ID generation
- Handling broker 409 conflicts

## Checklist
- [x] Tests added/updated
- [x] OpenAPI updated (if API changed)
- [x] Migrations included (if DB changed)
- [x] Docs updated (REPO_MAP / ADR / TASKS)
- [x] ADR created (if architectural change)
- [x] Concept docs created (if trading-specific)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Example Commands Claude Will Use

### Creating a Feature Branch
```bash
# Claude will run:
git checkout -b feature/t4-idempotent-orders
```

### Committing Changes
```bash
# If ADR exists, commit it first
git add docs/ADRs/0004-idempotent-order-submission.md
git commit -m "ADR-0004: Use deterministic client_order_id for idempotency"

# Then commit implementation
git add apps/execution_gateway/
git add tests/
git commit -m "Implement idempotent order submission (ADR-0004)

- Add deterministic_id() function using SHA256
- Update place_order() to check for duplicates
- Handle Alpaca 409 conflicts
- Add comprehensive tests

Closes #T4"
```

### Pushing and Creating PR
```bash
# Push branch
git push -u origin feature/t4-idempotent-orders

# Create PR with gh CLI
gh pr create \
  --title "Implement idempotent order submission (T4)" \
  --body "$(cat <<'EOF'
## Summary
Implements idempotent order submission using deterministic client_order_id generation.

## Related Work
- Ticket: T4 (/docs/TASKS/P0_TICKETS.md)
- ADR: ADR-0004

## Changes Made
...
EOF
)"
```

## Branch Naming Conventions

Use consistent branch naming:

```bash
feature/t4-idempotent-orders     # New features
fix/circuit-breaker-recovery     # Bug fixes
docs/update-testing-guide        # Documentation only
refactor/extract-risk-checks     # Refactoring
chore/upgrade-dependencies       # Maintenance
```

## Controlling PR Creation

### When You Want Automatic PRs

Tell Claude:
```
"Implement ticket T4 and create a PR when tests pass"
```

### When You Want Manual Control

Tell Claude:
```
"Implement ticket T4 but don't create a PR yet -
 I want to review the changes first"
```

Then later:
```
"Create a PR for the changes we just made"
```

## PR Creation Safety Checks

Before Claude creates a PR, it should verify:

1. ✅ All tests pass (`make test`)
2. ✅ All linting passes (`make lint`)
3. ✅ On a feature branch (not main/master)
4. ✅ Changes are committed
5. ✅ ADR created if needed
6. ✅ Documentation updated

Claude will **not** automatically:
- ❌ Push to main/master directly
- ❌ Force push (unless explicitly requested and justified)
- ❌ Merge PRs (requires human review)
- ❌ Delete branches
- ❌ Modify git history with rebase/amend (unless explicitly requested)

## Working with Draft PRs

For work-in-progress, create draft PRs:

```bash
gh pr create --draft \
  --title "[WIP] Implement idempotent orders (T4)" \
  --body "Work in progress. Do not merge yet.

## TODO
- [ ] Add integration tests
- [ ] Update documentation
- [ ] Get ADR reviewed
"
```

Then convert to ready when done:
```bash
gh pr ready
```

## Updating Existing PRs

Claude can update PRs by adding commits:

```bash
# Make additional changes
git add apps/execution_gateway/
git commit -m "Address review feedback: improve error messages"
git push

# Add comment to PR
gh pr comment <PR_NUMBER> --body "Updated to address review feedback"
```

## Best Practices

### 1. One Feature Per PR
Keep PRs focused:
- ✅ Single ticket implementation
- ✅ Related tests and docs
- ❌ Multiple unrelated changes
- ❌ Mixing features and refactoring

### 2. Meaningful Commit Messages
```bash
# GOOD
"Implement deterministic order ID generation (ADR-0004)

- Add SHA256-based hash function
- Include order params and date in hash
- Truncate to 24 chars for Alpaca compatibility
- Add unit tests for collision resistance"

# BAD
"Fixed stuff"
"WIP"
"Updates"
```

### 3. Keep PRs Small
Aim for:
- < 500 lines of code changes
- < 10 files changed
- Single focused change

Large changes should be split into multiple PRs:
```
PR #1: Add deterministic ID generation (ADR-0004)
PR #2: Integrate ID generation into order submission
PR #3: Add duplicate detection logic
PR #4: Add integration tests
```

### 4. Link Everything
In PR description, link to:
- Ticket in /docs/TASKS/
- ADR in /docs/ADRs/
- Implementation guide
- Related concept docs
- Related PRs (if any)

### 5. Use PR Templates (Optional)

Create `.github/pull_request_template.md`:
```markdown
## Summary
<!-- Brief description of changes -->

## Related Work
- Ticket:
- ADR:
- Implementation Guide:

## Changes Made
- [ ]

## Testing Completed
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing in DRY_RUN mode

## Documentation Updated
- [ ] Concept docs
- [ ] Implementation guide
- [ ] ADR (if architectural change)
- [ ] Code has docstrings
- [ ] OpenAPI spec (if API changed)

## Checklist
- [ ] Tests added/updated
- [ ] OpenAPI updated (if API changed)
- [ ] Migrations included (if DB changed)
- [ ] Docs updated

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Troubleshooting

### Issue: `gh` command not found
```bash
# Install GitHub CLI
brew install gh  # macOS
# or follow instructions at https://cli.github.com/
```

### Issue: Authentication failed
```bash
# Re-authenticate
gh auth login

# Check status
gh auth status
```

### Issue: Permission denied
```bash
# Check remote URL uses HTTPS (not SSH if not configured)
git remote -v

# Update to HTTPS if needed
git remote set-url origin https://github.com/username/repo.git
```

### Issue: Branch protection rules
If main/master has branch protection:
- PRs are required (good!)
- Claude cannot push directly (good!)
- You'll need to review and merge via GitHub UI

### Issue: PR created on wrong branch
```bash
# Close the PR
gh pr close <PR_NUMBER>

# Create correct branch
git checkout -b correct-branch-name

# Cherry-pick commits
git cherry-pick <commit-hash>

# Push and create new PR
git push -u origin correct-branch-name
gh pr create
```

## Advanced: CI Integration

If you have GitHub Actions:

```yaml
# .github/workflows/ci.yml
name: CI
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install poetry
      - run: poetry install
      - run: poetry run pytest
      - run: poetry run mypy .
      - run: poetry run ruff check .
```

This will automatically run tests on every PR Claude creates.

## Summary: Enabling Automatic PRs

**Minimal setup:**
```bash
# 1. Install and authenticate
brew install gh
gh auth login

# 2. Tell Claude to create PRs
"Implement ticket T4 and create a PR when done"
```

**That's it!** Claude will handle:
- Branch creation
- Commits
- Testing
- Pushing
- PR creation with detailed description

You retain control:
- Review the PR before merging
- Request changes via GitHub UI
- Close/modify as needed
- Merge when satisfied

This workflow keeps you in the driver's seat while automating the tedious parts.
