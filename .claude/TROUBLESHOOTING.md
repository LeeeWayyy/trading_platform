# Troubleshooting Guide

This document provides solutions to common issues encountered during development.

---

## Table of Contents

- [Zen-MCP + Clink Tool Errors](#zen-mcp--clink-tool-errors)
- [Git Workflow Issues](#git-workflow-issues)
- [Development Environment](#development-environment)
- [Testing Issues](#testing-issues)

---

## Zen-MCP + Clink Tool Errors

### 🚨 API Permission Errors

**Symptom:** You see errors like:
```
Error: API permission denied
Error: Unauthorized access to zen-mcp API
Error: Invalid API credentials
```

**Root Cause:** Using direct zen-mcp tools instead of clink

**Why This Happens:**
- MCP server configuration is system-level (not project-level)
- Direct zen tools (chat, thinkdeep, debug, codereview, etc.) bypass CLI authentication
- Only `mcp__zen__clink` is properly configured with CLI authentication

**✅ Solution:**

**STEP 1: Identify the wrong tool usage**

Look for patterns like these in your workflow:
```python
# ❌ WRONG - These cause API permission errors
mcp__zen-mcp__chat(...)
mcp__zen-mcp__thinkdeep(...)
mcp__zen-mcp__codereview(...)
mcp__zen-mcp__debug(...)
mcp__zen-mcp__consensus(...)
mcp__zen-mcp__planner(...)
mcp__zen-mcp__precommit(...)
mcp__zen-mcp__analyze(...)
```

**STEP 2: Replace with correct clink usage**

```python
# ✅ CORRECT - Use clink with appropriate CLI and role
mcp__zen__clink(
    prompt="Your request here",
    cli_name="codex",  # or "gemini" depending on use case
    role="codereviewer"  # or "planner" or "default"
)
```

**STEP 3: Choose correct cli_name and role**

| Use Case | cli_name | role | Example |
|----------|----------|------|---------|
| Quick code review before commit | `codex` | `codereviewer` | Review staged changes for safety |
| Deep review before PR (step 1) | `gemini` | `codereviewer` | Comprehensive branch review (first pass) |
| Deep review before PR (step 2) | `codex` | `codereviewer` | Comprehensive branch review (second pass with continuation_id) |
| Task planning/validation (step 1) | `gemini` | `planner` | Validate task document (first pass) |
| Task planning/validation (step 2) | `codex` | `planner` | Validate task document (second pass with continuation_id) |
| General questions | `codex` | `default` | Ask about implementation approach |
| Complex debugging | `gemini` | `default` | Investigate complex issues |

**STEP 4: Verify the fix**

After replacing the tool, you should see:
```
✅ Clink successfully connected to [cli_name]
✅ Review completed successfully
✅ continuation_id: [uuid]
```

---

### 🔍 Wrong Tool - How to Identify

**Quick Check:** If you're using ANY zen-mcp tool OTHER than `clink`, you're using the wrong tool!

**Common Mistakes:**

| ❌ Wrong Pattern | ✅ Correct Replacement |
|-----------------|----------------------|
| `mcp__zen-mcp__codereview(step="...", findings="...")` | `mcp__zen__clink(prompt="Review this code...", cli_name="codex", role="codereviewer")` |
| `mcp__zen-mcp__planner(step="...", plan="...")` | `mcp__zen__clink(prompt="Review this plan...", cli_name="gemini", role="planner")` |
| `mcp__zen-mcp__debug(step="...", hypothesis="...")` | `mcp__zen__clink(prompt="Help debug this issue...", cli_name="gemini", role="default")` |
| `mcp__zen-mcp__chat(prompt="...")` | `mcp__zen__clink(prompt="...", cli_name="codex", role="default")` |

**Why This Limitation Exists:**

The tool restriction is **not enforceable at project level** because:
- MCP configuration is system-level (`~/.claude/config/`)
- Cannot be configured in project `.claude/` directory
- Relies on **documentation + workflow discipline** rather than technical gates

This is why we have:
- Strong emphasis in `CLAUDE.md`
- Reminders in all workflow files
- This troubleshooting guide

---

### 📚 Prevention Strategies

**1. Always Reference Workflow Files**

Before using zen-mcp, check:
- `.claude/workflows/03-reviews.md` - Quick reviews
- `.claude/workflows/03-reviews.md` - Deep reviews
- `.claude/workflows/13-task-creation-review.md` - Task planning

Each has the correct clink usage pattern at the top!

**2. Memorize the Pattern**

```python
# Template to remember
mcp__zen__clink(
    prompt="What you want to do",
    cli_name="codex or gemini",
    role="codereviewer or planner or default"
)
```

**3. Check CLAUDE.md Section**

See [CLAUDE.md - Zen-MCP + Clink Integration](/CLAUDE.md#zen-mcp--clink-integration) for complete policy and examples.

**4. Look for Error Patterns**

If you see "API permission" or "Unauthorized" → you used wrong tool!

---

### 🆘 Emergency: Zen Server Unavailable

**Symptom:** Clink requests timeout or fail to connect

**Possible Causes:**
1. Zen-MCP server not running
2. Network connectivity issues
3. CLI authentication expired
4. Server maintenance

**Solution:**

**STEP 1: Check server status**
```bash
# Check if server is responding
curl -I https://zen-mcp.example.com/health
```

**STEP 2: Verify CLI authentication**
```bash
# Re-authenticate if needed
codex login
gemini login
```

**STEP 3: Emergency override (USER APPROVAL REQUIRED)**

If server is truly unavailable and you have a critical commit:

1. Ask user for approval:
   ```
   "Zen-mcp server is unavailable. Can I skip review for this commit?"
   ```

2. If approved, add override marker to commit:
   ```bash
   git commit -m "Add position validation

   ZEN_REVIEW_OVERRIDE: Server temporarily unavailable
   Reason: [Critical bugfix/blocking issue]
   Will request post-commit review when server returns"
   ```

3. Document in team chat and request review ASAP when server returns

**NEVER skip review without user approval!**

---

## Git Workflow Issues

### Branch Naming Violations

**Symptom:** Pre-commit hook blocks commit with "Invalid branch name" error

**Cause:** Branch doesn't follow naming convention

**Expected Format:**
```
feature/P0T1-short-description
feature/P1T11-workflow-optimization
bugfix/P0T2-fix-circuit-breaker
```

**Solution:**
```bash
# Rename current branch
git branch -m feature/PxTy-description

# Push renamed branch
git push -u origin feature/PxTy-description
```

See `/docs/STANDARDS/GIT_WORKFLOW.md` for complete branch naming rules.

---

### Master Branch Protection

**Symptom:** Accidentally committed to master branch

**Solution:**

**STEP 1: Check current branch**
```bash
git branch --show-current
```

**STEP 2: If on master, create feature branch**
```bash
# Create feature branch from current state
git checkout -b feature/PxTy-description

# Reset master to origin
git checkout master
git reset --hard origin/master
```

**STEP 3: Cherry-pick commits if needed**
```bash
git checkout feature/PxTy-description
git cherry-pick <commit-hash>
```

**Prevention:** Always create feature branch before starting work!

---

## Development Environment

### Missing Dependencies

**Symptom:** Import errors when running code

**Solution:**
```bash
# Reinstall dependencies
poetry install

# Or for specific groups
poetry install --with dev,test
```

### Database Connection Errors

**Symptom:** Cannot connect to Postgres/Redis

**Solution:**
```bash
# Check infrastructure is running
docker-compose ps

# Restart if needed
make down
make up

# Check connection
make status
```

### Type Check Failures

**Symptom:** `mypy` errors during `make lint`

**Solution:**

1. **Read the error carefully** - mypy is usually right!
2. **Add missing type hints:**
   ```python
   # ❌ Missing hints
   def process(data):
       return data

   # ✅ Correct
   def process(data: dict[str, Any]) -> dict[str, Any]:
       return data
   ```
3. **Use proper imports:**
   ```python
   from typing import Any, Optional
   ```

See `/docs/STANDARDS/CODING_STANDARDS.md` for type hint patterns.

---

## Testing Issues

### Tests Passing Locally But Failing in CI

**Common Causes:**
1. Missing test fixtures
2. Hardcoded paths
3. Timezone assumptions
4. Race conditions

**Solution:**

**STEP 1: Run tests in isolation**
```bash
# Run single test
pytest tests/path/to/test.py::test_name -v

# Run with same settings as CI
make ci-local
```

**STEP 2: Check for common issues**
- Absolute paths → use fixtures
- Time-dependent tests → freeze time
- Order-dependent tests → use `pytest-randomly`

### Pre-commit Hook Blocks on Test Failures

**Symptom:** Cannot commit because tests are failing

**This is INTENTIONAL - tests must pass!**

**Solution:**

**STEP 1: Fix the failing tests**
```bash
# Run tests to see failures
make test

# Fix the code or test
# Re-run until green
```

**STEP 2: Emergency override (RARE, user approval required)**
```bash
# Only if truly urgent and user approved
OVERRIDE_TESTS=1 git commit -m "..."
```

**NEVER use override without fixing tests later!**

---

## Getting Help

### Check Documentation First

1. **Workflow guides:** `.claude/workflows/*.md`
2. **Standards:** `/docs/STANDARDS/*.md`
3. **API specs:** `/docs/API/*.openapi.yaml`
4. **This guide:** `.claude/TROUBLESHOOTING.md`

### Still Stuck?

1. **Search recent issues:**
   ```bash
   git log --all --grep="similar error"
   ```

2. **Check ADRs for architectural context:**
   ```bash
   ls /docs/ADRs/
   ```

3. **Ask the team:**
   - Describe what you tried
   - Include error messages
   - Share relevant code snippets

---

## Related Documentation

- [CLAUDE.md](/CLAUDE.md) - Main project guidance
- [Workflows](/.claude/workflows/README.md) - Development workflows
- [Standards](/docs/STANDARDS/) - Coding and git standards
- [Concepts](/docs/CONCEPTS/) - Trading domain concepts

---

**Last Updated:** 2025-10-24
**Owner:** @development-team
