# Zen-MCP Review Workflows

**Purpose:** Two-tier review system for code quality and trading safety validation
**Tools:** zen-mcp via clink (gemini → codex two-phase reviews)
**Policy:** See [Clink-Only Tool Usage](./_common/clink-policy.md) and [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## Two-Tier Review System

| Tier | When | Duration | Scope | Phase Pattern |
|------|------|----------|-------|---------------|
| **Tier 1: Quick Review** | Before EVERY commit | ~2-3 min | Staged changes (safety focus) | Gemini → Codex |
| **Tier 2: Deep Review** | Before creating PR | ~3-5 min | All branch changes (comprehensive) | Gemini → Codex |

**Two-phase process (both tiers):**
1. **Phase 1 (Gemini):** Comprehensive analysis (~1-2 min for quick, ~2-3 min for deep)
2. **Phase 2 (Codex):** Synthesis and validation (~30-60 sec for quick, ~1-2 min for deep)

**Total overhead:** ~5% of development time for massive safety benefit

---

## Tier 1: Quick Review (Pre-Commit)

**MANDATORY before EVERY commit that touches code**

### When to Use

**✅ Required for:**
- Every progressive commit (30-60 min intervals)
- Any trading logic, order placement, risk checks
- Circuit breaker code, data handling, API endpoints

**🚫 Can skip only for:**
- Auto-generated files (package-lock.json, poetry.lock)
- Documentation-only changes (mark with `#docs-only`)
- Emergency hotfixes (with user approval + mandatory post-commit review)

### Quick Review Process

```bash
# 1. Stage changes
git add <files>
git status
git diff --cached

# 2. Request review
"Review my staged changes with zen-mcp quick review"

# 3. Fix HIGH/CRITICAL immediately (context is fresh!)
git add <fixed-files>
"I've fixed [issue], please verify"

# 4. Get approval and commit
git commit -m "feat: Add feature

zen-mcp-review: approved
continuation-id: abc123-def456"
```

### Commit Message Marker Standard

**⚠️ CRITICAL: All commits MUST include machine-readable review markers**

CI verification (`scripts/verify_gate_compliance.py`) requires these markers in commit messages:

#### Format 1: Quick Review (Tier 1)
```
zen-mcp-review: approved
continuation-id: <uuid>
```

**Example:**
```bash
git commit -m "feat: Add position validation

zen-mcp-review: approved
continuation-id: 8b0c8bcf-622a-4bbf-aad2-d06f2ae06344"
```

#### Format 2: Deep Review (Tier 2)
```
zen-mcp-review: approved
gemini-continuation-id: <uuid>
codex-continuation-id: <uuid>
```

**Example:**
```bash
git commit -m "fix(workflow): Complete Phase 0 audit

zen-mcp-review: approved
gemini-continuation-id: ae512f21-f9fe-4c3a-9e7e-bfaa8b07e5fd
codex-continuation-id: fa10318a-2b4b-4b22-b79d-9b379dff5033"
```

#### Requirements

**ALL three fields required:**
1. `zen-mcp-review: approved` (approval marker)
2. **Quick review:** `continuation-id: <uuid>` (single reviewer)
3. **Deep review:** BOTH `gemini-continuation-id:` AND `codex-continuation-id:` (dual reviewers)

**Legacy format supported (deep review):**
- `gemini-review: <id>` (alias for `gemini-continuation-id:`)
- `codex-review: <id>` (alias for `codex-continuation-id:`)

#### Getting Continuation IDs

**From workflow_gate.py:**
```bash
./scripts/workflow_gate.py request-review commit
# Output includes: continuation_id: abc123...

./scripts/workflow_gate.py request-review pr
# Output includes: gemini_continuation_id: xyz... and codex_continuation_id: def...
```

**From zen-mcp review output:**
```
# Look for in response:
"Continuation ID: abc123-def456-..."
"Gemini continuation: xyz..."
"Codex continuation: def..."
```

#### What Happens If Missing?

**Local development:**
- Pre-commit hook checks workflow-state.json commit history
- Blocks commits made with `--no-verify`

**CI (GitHub Actions):**
- `verify_gate_compliance.py` checks commit message markers
- **Fails PR if markers missing** (workflow-state.json is gitignored in CI)
- Exit code 1 → CI test failure

**⚠️ If you rewrite commit history (git rebase, git commit --amend, git filter-branch):**
- Commit hashes change
- Local workflow-state.json has old hashes → verification fails locally
- CI still passes (uses markers, not workflow-state.json)
- **Solution:** Update workflow-state.json commit_history with new hashes

### Severity Handling

| Severity | Action |
|----------|--------|
| **HIGH/CRITICAL** | ❌ MUST fix before commit (non-negotiable) |
| **MEDIUM** | ⚠️ Fix if <5 min OR defer with justification |
| **LOW** | ℹ️ Fix if trivial OR note in TODO |

**Don't spend >15 min total on review fixes**

---

## Tier 2: Deep Review (Pre-PR)

**MANDATORY before creating ANY pull request**

### When to Use

**✅ Required for:**
- Feature complete, all progressive commits done
- All quick reviews passed, local tests passing
- Before running `gh pr create`

**Why both tiers?**
- Quick reviews catch per-commit issues
- Deep review catches integration issues, architecture patterns, test coverage gaps

### Deep Review Process (Multi-Iteration Until Clean)

**CRITICAL: Independent reviews, fresh start after each fix iteration**

```bash
# 1. Verify completion
make ci-local  # All tests pass
git log master..HEAD --oneline  # Review commits

# 2. Request INDEPENDENT reviews (both fresh, no cross-contamination)
"Request independent deep review from Gemini (fresh, no prior context)"
# → Gemini reviews complete branch independently

"Request independent deep review from Codex (fresh, no prior context)"
# → Codex reviews complete branch independently (NOT building on Gemini)

# 3. If ANY reviewer finds ANY issues (even MEDIUM):
#    a. Fix ALL issues found
#    b. Commit fixes
#    c. RESTART from step 2 with FRESH reviews (no memory of previous iteration)

# 4. Repeat until BOTH reviewers approve with ZERO issues
# Result: ✅ Both Gemini AND Codex approve with NO issues found

# 5. Create PR with both continuation IDs
```

### Deep Review Scope

1. Architecture & Design
2. Code Quality (types, errors, logging)
3. Trading Safety (breakers, idempotency, limits)
4. Testing (coverage, edge cases)
5. Documentation (docstrings, ADRs)
6. Integration (API contracts, schemas)

**Zero-Tolerance Policy:**
- CRITICAL: MUST fix
- HIGH: MUST fix
- MEDIUM: MUST fix (no deferral)
- LOW: Fix or create follow-up task

**Why independent reviews?**
- Gemini → Codex synthesis can miss issues (one reviewer's blind spots carry forward)
- Independent fresh reviews catch different issue types
- Example: Fresh Codex review found CLI wiring issues Gemini missed

**Why restart after fixes?**
- Fresh perspective prevents "fix fatigue"
- Ensures fixes didn't introduce new issues
- No memory of "what was already checked" - complete re-validation

---

## Decision Points

### Should I skip review?

**Quick review:**
- ✅ Skip for docs-only (`git commit -m "... #docs-only"`)
- ❌ NEVER skip for trading logic, order code, risk checks

**Deep review:**
- ❌ NEVER skip before PR
- Even if all quick reviews passed, integration issues can exist

### Review taking too long?

**Normal:** 2-3 min (quick), 3-5 min (deep)
**Too long:** > 6 minutes

**If timeout:**
1. Check zen-mcp server status
2. Check network connection
3. Try single-phase (codex only) as fallback
4. See "Server Unavailable" below

### Too many issues to fix?

**Quick review:**
- Fix ALL HIGH/CRITICAL
- Fix MEDIUM if <5 min each
- Don't spend >15 min total

**Deep review:**
- Fix ALL HIGH/CRITICAL
- Fix top MEDIUM issues (<30 min total)
- Defer rest with justification in PR description

### Should I defer MEDIUM issues?

**Defer if:**
- Requires separate investigation (>30 min)
- Out of scope for current feature
- User approves deferral

**Document deferral:**
```markdown
## Deferred from Zen Review
- **MEDIUM:** Optimize query in get_positions()
- Reason: Requires profiling analysis
- Follow-up: Task P1T15
- Continuation-id: abc123
```

---

## Common Issues

### Zen-MCP Server Unavailable

**Emergency override (with user approval ONLY):**
```bash
git commit -m "feat: Add feature

ZEN_REVIEW_OVERRIDE: Server unavailable
Reason: Critical bugfix
Will request post-commit review ASAP"
```

**Then:**
- Document in team chat
- Request review when server returns
- Create follow-up PR if issues found

### Can't Tell If Approved

**Ask explicitly:**
```
"Zen, confirm: is this code approved for commit, or are there blocking issues?"
```

**Look for:**
- ✅ "Safe to commit" / "Approved" / "Ready for PR"
- ❌ "Fix [X] before committing" / "Blocking issue"

### Don't Understand Issue

**Ask for details:**
```
"Zen, explain why [issue] is a problem and provide a code example of the fix"
```

### Disagreement with Zen

**Discuss, don't ignore:**
```
"I think zen's assessment might be incorrect because [reason]. Can you explain the concern?"
```

**Then ask user:**
```
User: "What did zen say?"
Claude: "Zen flagged [X] as HIGH because [Y]. Should I fix or override?"
User: "Fix it" OR "Override with justification"
```

**Don't silently ignore feedback!**

### False Positive

**Verify and document:**
```
"Zen flagged [issue], but it's actually handled at line [N]. Can you verify this is a false positive?"

# If confirmed false positive, document for future improvements
```

---

## Examples

### Example: Quick Review - Clean Approval

```bash
$ git add apps/execution_gateway/order_placer.py
$ "Quick review my staged changes"

# Two-phase: Gemini → Codex
# Result: ✅ Approved

$ git commit -m "feat: Add position validation

zen-mcp-review: approved
continuation-id: abc123"
```

### Example: Quick Review - Critical Issue Found

```bash
$ "Quick review my staged changes"

# Gemini finds CRITICAL: missing circuit breaker
# Codex confirms: DO NOT commit

# Fix immediately
$ git add apps/execution_gateway/order_placer.py
$ "I've added circuit breaker check, verify"

# Codex verifies
# Result: ✅ Fixed - Safe to commit

$ git commit -m "feat: Add position validation

- Implement check_position_limits()
- Add circuit breaker (zen critical fix)

zen-mcp-review: approved (critical issue fixed)
continuation-id: abc123"
```

### Example: Deep Review - Multi-Iteration Until Clean

```bash
# === Iteration 1 ===
$ "Request independent deep review from Gemini (fresh perspective)"
# Gemini finds: 2 CRITICAL, 1 HIGH, 2 MEDIUM

$ "Request independent deep review from Codex (fresh perspective)"
# Codex finds: 1 CRITICAL (CLI wiring), 1 MEDIUM (different than Gemini's)

# Fix ALL issues from BOTH reviewers
$ git add <files>
$ git commit -m "fix: Address review findings - iteration 1"

# === Iteration 2 (FRESH reviews, no memory of iteration 1) ===
$ "Request independent deep review from Gemini (fresh, complete branch)"
# Gemini finds: 1 MEDIUM (new issue in fixes)

$ "Request independent deep review from Codex (fresh, complete branch)"
# Codex finds: 0 issues

# Fix Gemini's MEDIUM
$ git add <files>
$ git commit -m "fix: Address Gemini MEDIUM finding - iteration 2"

# === Iteration 3 (FRESH reviews again) ===
$ "Request independent deep review from Gemini (fresh, complete branch)"
# Gemini: ✅ NO issues found

$ "Request independent deep review from Codex (fresh, complete branch)"
# Codex: ✅ NO issues found

# Result: ✅ BOTH reviewers approve with ZERO issues
# Create PR with both continuation IDs
```

---

## Validation Checklist

**Quick review succeeded:**
- [ ] Review requested and completed (~2-3 min)
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM fixed or explicitly deferred
- [ ] Explicit "safe to commit" approval
- [ ] Continuation ID in commit message

**Deep review succeeded:**
- [ ] Review requested and completed (~3-5 min)
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] MEDIUM fixed or deferred with justification
- [ ] Explicit "ready for PR" approval
- [ ] Continuation ID in PR description
- [ ] All tests pass (`make ci-local`)

---

## See Also

- [Zen-MCP Review Process](./_common/zen-review-process.md) - Complete tier details
- [Clink-Only Tool Usage](./_common/clink-policy.md) - Tool policy
- [01-git.md](./01-git.md) - Progressive commits and PRs (uses quick & deep review)
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../docs/STANDARDS/GIT_WORKFLOW.md) - Review requirements
