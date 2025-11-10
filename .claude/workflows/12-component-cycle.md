# Component Development Cycle (4-Step Pattern)

**Purpose:** Reusable checklist for implementing any logical component safely.

**When to Use:** Every discrete unit of functionality (API endpoint, service, module, strategy).

---

## The Four Steps

| Step | Description | Expected Outputs |
|------|-------------|------------------|
| 1. Implement | Write/update production code | Feature code, docs, config |
| 2. Test | Create/extend automated tests | Passing tests, updated fixtures |
| 3. Review | Request zen-mcp quick review | Review approval + continuation_id |
| 4. Commit | Commit reviewed changes only | Clean commit with message |

**Todo Template:**
```markdown
- [ ] Implement <component>
- [ ] Create tests for <component>
- [ ] Request zen-mcp review for <component>
- [ ] Commit <component>
```

---

## Workflow Gate Commands

workflow_gate.py enforces the 4-step pattern automatically:

| Command | When | Purpose |
|---------|------|---------|
| `set-component "Name"` | Start of cycle | Set current component |
| `advance test` | After implementing | Move to test step |
| `advance review` | After testing | Move to review step |
| `record-review <id> APPROVED` | After zen-mcp review | Record approval |
| `record-ci true` | After `make ci-local` | Record CI pass |
| `status` | Anytime | Check current state |

**Workflow transitions:**
```
implement → test → review → (commit) → implement (reset)
```

**Commit prerequisites (enforced by pre-commit hook):**
1. Current step = `review`
2. Zen-MCP review = `APPROVED`
3. CI = `PASSED`

**If commit blocked:** Run `./scripts/workflow_gate.py status` to see what's missing.

---

## Context Monitoring Integration

**Check context at workflow transitions to prevent mid-task interruptions:**

| Threshold | Status | Action |
|-----------|--------|--------|
| < 70% | ✅ OK | Continue normal workflow |
| 70-84% | ⚠️ WARNING | Delegation RECOMMENDED |
| ≥ 85% | 🚨 CRITICAL | Delegation MANDATORY |

**Check context:**
```bash
./scripts/workflow_gate.py check-context  # At each step transition
```

**Delegate work when ≥70%:**
```bash
# Delegate non-core tasks to subagent (see 16-subagent-delegation.md)
./scripts/workflow_gate.py record-delegation "Search for API patterns"
# Context resets to 0 after delegation
```

**Context automatically resets after:**
- Successful delegation
- Successful commit

**See:** `.claude/workflows/16-subagent-delegation.md` for complete delegation workflow

---

## Usage Checklist

- Track four todos for each component
- Stage only files related to current component before review
- Capture review findings immediately while context is fresh
- For larger refactors, create follow-up component cycle instead of expanding scope

---

## Anti-Patterns

| Anti-Pattern | Why It Fails | Fix |
|--------------|-------------|-----|
| Single todo "Implement & commit" | Skips tests and review | Expand into 4 explicit todos |
| Combining multiple components | Mega commits, confusing reviews | Run cycle separately per component |
| Deferring review until "the end" | Reviewers lose context, bugs slip in | Review immediately after tests pass |
| Treating docs as optional | Trading systems rely on clarity | Include docs in implementation step |

---

**Keep this bookmarked** - other workflows reference it instead of duplicating.
