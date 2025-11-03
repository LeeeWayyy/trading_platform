# Component Development Cycle (4-Step Pattern)

**Purpose:** Provide a reusable checklist for implementing any logical component safely.
**When to Use:** Every time you introduce, refactor, or retire a discrete unit of functionality (API endpoint, service, module, strategy, etc.).

---

## Why This Pattern Exists

Trading workflows fail when we skip validation steps. This pattern keeps the loop tight:

1. **Implementation remains focused** – prevents mixing unrelated changes.
2. **Tests are written before confidence fades** – regression risk drops.
3. **Zen-MCP review happens with full context** – reviewers see tested code.
4. **Commits stay small and auditable** – easier to bisect and roll back.

Treat these steps as a **mini life cycle** for each component. Never advance to the next stage until the current one is complete.

---

## The Four Steps

| Step | Description | Expected Outputs |
|------|-------------|------------------|
| 1. Implement | Write or update the production code. | Feature code, docs, configuration updates. |
| 2. Test | Create/extend automated tests and run them locally. | Passing unit/integration tests, updated fixtures. |
| 3. Review | Request zen-mcp quick review for the staged changes. | Review transcript + continuation_id, fixes applied. |
| 4. Commit | Commit only what was reviewed and approved. | Clean commit with descriptive message. |

**Markdown Todo Template**
```markdown
- [ ] Implement <component>
- [ ] Create tests for <component>
- [ ] Request zen-mcp review for <component>
- [ ] Commit <component>
```

Replace `<component>` with the concrete scope (e.g., "position limit validation").

---

## Workflow Gate Enforcement (MANDATORY)

**CRITICAL:** Commits are now enforced via workflow gates. The 4-step pattern is enforced programmatically through:

### Hard Gates (Automatic Enforcement)
- **Pre-commit hook** blocks commits unless prerequisites are met
- **CI verification** detects `--no-verify` bypasses
- **State machine** tracks progress through workflow steps

### Workflow State Transitions
```
implement → test → review → (commit) → implement
```

After successful commit, state resets to `implement` for next component.

### CLI Commands

**Set component name (at start of cycle):**
```bash
./scripts/workflow_gate.py set-component "Position Limit Validation"
```

**Advance workflow step:**
```bash
# After completing implementation
./scripts/workflow_gate.py advance test

# After creating tests
./scripts/workflow_gate.py advance review
```

**Record review approval:**
```bash
# After zen-mcp review completes
./scripts/workflow_gate.py record-review <continuation_id> APPROVED
```

**Record CI result:**
```bash
# After running make ci-local successfully
./scripts/workflow_gate.py record-ci true
```

**Check current state:**
```bash
./scripts/workflow_gate.py status
```

**Commit prerequisites (enforced by pre-commit hook):**
1. Current step must be `review`
2. Zen-MCP review status must be `APPROVED`
3. CI must have passed (`make ci-local`)

**If commit blocked:**
```bash
# Check what's missing
./scripts/workflow_gate.py status

# Example output shows required actions:
#   Zen Review: NOT_REQUESTED
#   CI: NOT_RUN
```

**After successful commit:**
```bash
# State automatically resets to 'implement' for next component
# Set new component name and repeat cycle
./scripts/workflow_gate.py set-component "Next Component Name"
```

### ⚠️ WARNING: Never Bypass Gates

**DO NOT use `git commit --no-verify`**
- Bypasses quality gates
- Defeats entire workflow system
- Detected by CI verification (workflow fails)
- Causes review debt and technical debt

If commit is blocked, **fix the prerequisites** instead of bypassing.

---

## Context-Aware Workflow Pattern (Component 3)

**Purpose:** Prevent context exhaustion from interrupting critical work by monitoring context usage and triggering delegation when thresholds are exceeded.

### Context Monitoring During Workflow

**Check context usage at workflow transitions:**
```bash
# After implementing (before advancing to test)
./scripts/workflow_gate.py check-context

# After creating tests (before advancing to review)
./scripts/workflow_gate.py check-context

# Before committing
./scripts/workflow_gate.py check-context
```

**Context thresholds:**
- **< 70%:** ✅ OK - Continue normal workflow
- **70-84%:** ⚠️ WARNING - Delegation RECOMMENDED
- **≥ 85%:** 🚨 CRITICAL - Delegation MANDATORY

### When to Delegate Work

**At 70% threshold (RECOMMENDED):**
- Consider delegating non-core tasks to subagent
- Use Task tool for exploratory work (codebase search, pattern analysis)
- See `.claude/workflows/16-subagent-delegation.md` for delegation workflow

**At 85% threshold (MANDATORY):**
- MUST delegate before continuing
- Complete current component commit first
- Then delegate remaining work to fresh context

**Record delegation:**
```bash
# After using Task tool or delegating work
./scripts/workflow_gate.py record-delegation "Search for API usage patterns"

# Context automatically resets to 0 after delegation
```

### Manual Context Recording

**If you can estimate token usage:**
```bash
# Record current context usage manually
./scripts/workflow_gate.py record-context 120000

# Check status and get recommendations
./scripts/workflow_gate.py suggest-delegation
```

**Context resets after:**
- Successful delegation (via `record-delegation`)
- Successful commit (automatic)

### Integration with 4-Step Pattern

**Updated workflow with context monitoring:**
1. **Before implementing:** Check context, delegate if ≥70%
2. **Implement** the component
3. **After implementing:** Check context, delegate if ≥70%
4. **Test** with TDD
5. **After testing:** Check context, delegate if ≥70%
6. **Review** via zen-mcp
7. **Before commit:** Check context one final time
8. **Commit** (context resets automatically)

**Why this helps:**
- Prevents mid-task context compaction interruptions
- Ensures delegation happens at natural boundaries (workflow transitions)
- Preserves continuity for complex implementations
- Automatic context reset after commit keeps tracking accurate

**See also:** `.claude/workflows/16-subagent-delegation.md` for complete delegation workflow

---

## Usage Checklist

- Track the four todos in your task ticket or working notes.
- Only stage files related to the current component before requesting review.
- Capture review findings immediately while context is fresh.
- If a finding requires larger refactors, create a follow-up component cycle rather than expanding scope mid-stream.

---

## Anti-Patterns to Avoid

| Anti-Pattern | Why It Fails | Fix |
|--------------|-------------|-----|
| Single todo like "Implement & commit" | Skips tests and review. | Expand into four explicit todos. |
| Combining multiple components in one cycle | Creates mega commits and confusing reviews. | Run the cycle separately for each component. |
| Deferring zen review until "the end" | Reviewers lose context; bugs slip in. | Request review immediately after tests pass. |
| Treating documentation as optional | Trading systems rely on clarity. | Include docs/config updates in the implementation step. |

---

## FAQ

**Q: Can I merge the test and implementation steps if I practice TDD?**
A: Keep them separate in the checklist. You may iterate between them, but both boxes must be checked explicitly.

**Q: What if the review suggests additional tests?**
A: Add the tests, restage, rerun quick review using the continuation_id, then proceed to commit.

**Q: How does this align with deep review?**
A: Quick reviews happen per component cycle. Deep review aggregates all completed cycles before opening the PR.

---

Keep this file bookmarked; other workflows will reference it instead of duplicating the full explanation.
