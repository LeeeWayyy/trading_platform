# Zen-MCP Comprehensive Review Workflow

**Purpose:** Single comprehensive review system for code quality and trading safety validation
**Tools:** zen-mcp via clink (gemini → codex two-phase reviews)
**Policy:** See [Clink-Only Tool Usage](./_common/clink-policy.md) and [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## Single-Tier Comprehensive Review System

| When | Duration | Scope | Phase Pattern |
|------|----------|-------|---------------|
| Before EVERY commit AND before creating PR | ~3-5 min | All changes (staged or branch) | Gemini → Codex |

**Two-phase process:**
1. **Phase 1 (Gemini):** Comprehensive analysis (~2-3 min)
2. **Phase 2 (Codex):** Synthesis and validation (~1-2 min)

**Total overhead:** ~5% of development time for massive safety benefit

---

## When to Use Comprehensive Review

**✅ Required for:**
- Every commit that touches code (progressive commits every 30-60 min)
- Before creating any pull request
- Any trading logic, order placement, risk checks
- Circuit breaker code, data handling, API endpoints

**🚫 Can skip only for:**
- Auto-generated files (package-lock.json, poetry.lock)
- Documentation-only changes (mark with `#docs-only`)
- Emergency hotfixes (with user approval + mandatory post-commit review)

---

## Comprehensive Review Process

### Step 1: Verify Completion

```bash
# For progressive commits:
git add <files>
git status
git diff --cached

# For PR creation:
make ci-local  # All tests pass
git log master..HEAD --oneline  # Review commits
```

### Step 2: Request Independent Two-Phase Review

**CRITICAL: Use two-phase Gemini → Codex pattern for all reviews**

#### Phase 1: Gemini Comprehensive Analysis

```bash
"Request comprehensive zen-mcp review from Gemini (fresh, independent analysis)

Review all staged changes / all branch changes with focus on:

**Architecture & Design:**
- Design patterns used correctly and consistently?
- Code well-structured and maintainable?
- Integration impact on other components?

**Trading Safety (CRITICAL):**
- Circuit breaker checks before critical operations?
- Client_order_id values deterministic and collision-free?
- Per-symbol and total position limits enforced?
- Order state transitions valid and checked?

**Concurrency & Data Safety (HIGH):**
- Redis WATCH/MULTI/EXEC for concurrent updates?
- DB operations wrapped in proper transactions?
- Read-modify-write sequences atomic?

**Error Handling (HIGH):**
- Exceptions caught, logged with context, re-raised?
- Logs include strategy_id, client_order_id, symbol?
- Errors propagated correctly to callers?

**Code Quality (MEDIUM):**
- All function signatures properly typed?
- Input data validated (Pydantic, assertions)?
- Connections, files, resources properly closed?
- None cases handled safely?

**Security (HIGH):**
- Credentials, API keys never hardcoded or logged?
- SQL queries parameterized (no string interpolation)?
- User inputs and external data validated?

**Configuration & Environment (MEDIUM):**
- DRY_RUN flag respected for order submission?
- Magic numbers/strings replaced with config values?
- All configs from settings/env, not hardcoded?

**Standards Compliance (MEDIUM):**
- Functions documented per /docs/STANDARDS/DOCUMENTATION_STANDARDS.md?
- Code follows /docs/STANDARDS/CODING_STANDARDS.md?
- Tests added for new code paths?

**Domain-Specific (HIGH):**
- Research and production share feature calculation code?
- All timestamps UTC and timezone-aware?
- Changes comply with OpenAPI specs in /docs/API/?

**Testing & Edge Cases:**
- All code paths tested?
- Boundary conditions handled?
- Failure modes tested?

**Documentation:**
- Docstrings complete and accurate?
- ADRs needed for architectural changes?
- User-facing docs updated?

**Performance:**
- Obvious performance issues?
- Database query optimization needed?
- Memory management concerns?

Provide comprehensive analysis with all issues categorized by severity."
```

#### Phase 2: Codex Independent Review

**IMPORTANT: Completely independent fresh review - NO reference to Gemini's analysis**

```bash
"Request comprehensive zen-mcp review from Codex (fresh, independent analysis)

Review the same staged changes / branch changes with focus on:

**Architecture & Design:**
- Design patterns used correctly and consistently?
- Code well-structured and maintainable?
- Integration impact on other components?

**Trading Safety (CRITICAL):**
- Circuit breaker checks before critical operations?
- Client_order_id values deterministic and collision-free?
- Per-symbol and total position limits enforced?
- Order state transitions valid and checked?

**Concurrency & Data Safety (HIGH):**
- Redis WATCH/MULTI/EXEC for concurrent updates?
- DB operations wrapped in proper transactions?
- Read-modify-write sequences atomic?

**Error Handling (HIGH):**
- Exceptions caught, logged with context, re-raised?
- Logs include strategy_id, client_order_id, symbol?
- Errors propagated correctly to callers?

**Code Quality (MEDIUM):**
- All function signatures properly typed?
- Input data validated (Pydantic, assertions)?
- Connections, files, resources properly closed?
- None cases handled safely?

**Security (HIGH):**
- Credentials, API keys never hardcoded or logged?
- SQL queries parameterized (no string interpolation)?
- User inputs and external data validated?

**Configuration & Environment (MEDIUM):**
- DRY_RUN flag respected for order submission?
- Magic numbers/strings replaced with config values?
- All configs from settings/env, not hardcoded?

**Standards Compliance (MEDIUM):**
- Functions documented per /docs/STANDARDS/DOCUMENTATION_STANDARDS.md?
- Code follows /docs/STANDARDS/CODING_STANDARDS.md?
- Tests added for new code paths?

**Domain-Specific (HIGH):**
- Research and production share feature calculation code?
- All timestamps UTC and timezone-aware?
- Changes comply with OpenAPI specs in /docs/API/?

**Testing & Edge Cases:**
- All code paths tested?
- Boundary conditions handled?
- Failure modes tested?

**Documentation:**
- Docstrings complete and accurate?
- ADRs needed for architectural changes?
- User-facing docs updated?

**Performance:**
- Obvious performance issues?
- Database query optimization needed?
- Memory management concerns?

Provide independent comprehensive analysis with all issues categorized by severity.
DO NOT reference or build upon Gemini's review - provide completely fresh perspective."
```

### Step 3: Handle Findings

**Severity Handling:**

| Severity | Action |
|----------|--------|
| **HIGH/CRITICAL** | ❌ MUST fix before commit (non-negotiable) |
| **MEDIUM** | ⚠️ Fix immediately OR defer with justification |
| **LOW** | ℹ️ Fix if trivial OR note in TODO |

**For progressive commits:**
- Fix ALL HIGH/CRITICAL immediately (context is fresh!)
- Fix MEDIUM if <5 min each
- Don't spend >15 min total on review fixes

**For PR creation:**
- Fix ALL HIGH/CRITICAL
- Fix ALL MEDIUM (no deferral - zero tolerance)
- Fix or create follow-up task for LOW

### Step 4: Fix-and-Verify Iteration Loop

**If ANY issues found (even MEDIUM):**

```bash
# 1. Fix all issues
git add <fixed-files>
git commit -m "fix: Address review findings - iteration N"

# 2. RESTART with FRESH independent reviews (no memory of previous iteration)
# → Phase 1: Fresh Gemini review of complete changes
# → Phase 2: Fresh Codex synthesis

# 3. Repeat until BOTH reviewers approve with ZERO issues
```

**Why independent reviews each iteration?**
- Fresh perspective prevents "fix fatigue"
- Ensures fixes didn't introduce new issues
- No memory of "what was already checked" - complete re-validation
- Example: Fresh Codex review found CLI wiring issues Gemini missed

**Why restart after fixes?**
- Gemini → Codex synthesis can miss issues if carrying blind spots forward
- Independent reviews catch different issue types
- Complete branch re-validation ensures nothing broken

### Step 5: Commit or Create PR

**IMPORTANT: Only include continuation IDs from the FINAL approved iteration (when both reviewers approved with zero issues)**

**For progressive commits:**
```bash
git commit -m "feat: Add feature

zen-mcp-review: approved
gemini-continuation-id: <final-uuid-from-approved-iteration>
codex-continuation-id: <final-uuid-from-approved-iteration>"
```

**For PR creation:**
```bash
# Include in PR description:
## Zen-MCP Comprehensive Review
- Iterations: X (until zero issues)
- Final Gemini Continuation ID: `<uuid-from-final-approved-iteration>`
- Final Codex Continuation ID: `<uuid-from-final-approved-iteration>`
- Issues caught and fixed: Y
- Status: ✅ Approved (both reviewers, zero issues)
```

---

## Commit Message Marker Standard

**⚠️ CRITICAL: All commits MUST include machine-readable review markers**

CI verification (`scripts/verify_gate_compliance.py`) requires these markers:

### Required Format

```
zen-mcp-review: approved
gemini-continuation-id: <uuid>
codex-continuation-id: <uuid>
```

**Example:**
```bash
git commit -m "fix(workflow): Complete comprehensive review

zen-mcp-review: approved
gemini-continuation-id: ae512f21-f9fe-4c3a-9e7e-bfaa8b07e5fd
codex-continuation-id: fa10318a-2b4b-4b22-b79d-9b379dff5033"
```

### Requirements

**ALL three fields required:**
1. `zen-mcp-review: approved` (approval marker)
2. `gemini-continuation-id: <uuid>` (Gemini reviewer)
3. `codex-continuation-id: <uuid>` (Codex reviewer)

**Legacy aliases supported:**
- `gemini-review: <id>` (alias for `gemini-continuation-id:`)
- `codex-review: <id>` (alias for `codex-continuation-id:`)

### Getting Continuation IDs

**From workflow_gate.py:**
```bash
./scripts/workflow_gate.py request-review commit
# OR
./scripts/workflow_gate.py request-review pr

# Output includes both IDs
```

**From zen-mcp review output:**
```
# Look for in response:
"Continuation ID: abc123-def456-..."
"Gemini continuation: xyz..."
"Codex continuation: def..."
```

### What Happens If Missing?

**Local development:**
- Pre-commit hook checks workflow-state.json commit history
- Blocks commits made with `--no-verify`

**CI (GitHub Actions):**
- `verify_gate_compliance.py` checks commit message markers
- **Fails PR if markers missing**
- Exit code 1 → CI test failure

---

## Review Scope Categories

### 1. Architecture & Design
- Design patterns correctly applied
- Code organization and structure
- Integration impacts
- System design consistency

### 2. Code Quality
- Type hints complete
- Data validation (Pydantic models)
- Error handling patterns
- Resource cleanup
- Null/None handling

### 3. Trading Safety
- Circuit breaker integration
- Idempotent order IDs
- Position limit validation
- Order state transitions
- Risk check enforcement

### 4. Testing
- Test coverage (unit, integration, edge cases)
- Boundary condition testing
- Error scenario testing
- Test quality and maintainability

### 5. Documentation
- Docstrings (per standards)
- ADRs for architectural changes
- User-facing docs updates
- Code comments where needed

### 6. Integration
- API contract compliance
- Schema compatibility
- Cross-service impacts
- Database migration needs

---

## Decision Points

### Should I skip review?

**❌ NEVER skip for:**
- Trading logic, order code, risk checks
- Circuit breaker integration
- Database schema changes
- API contract changes

**✅ Can skip only for:**
- Docs-only (`git commit -m "... #docs-only"`)
- Auto-generated files
- Emergency with approval + post-review

### Review taking too long?

**Normal:** 3-5 min per iteration
**Too long:** > 6 minutes

**If timeout:**
1. Check zen-mcp server status
2. Check network connection
3. Try single-phase (gemini or codex only) as fallback
4. See "Server Unavailable" below

### Too many issues to fix?

**Progressive commits:**
- Fix ALL HIGH/CRITICAL
- Fix MEDIUM if <5 min each
- Don't spend >15 min total

**PR creation:**
- Fix ALL HIGH/CRITICAL
- Fix ALL MEDIUM (zero tolerance)
- Defer LOW with justification only

### Should I defer MEDIUM issues?

**For progressive commits: Yes, if:**
- Requires separate investigation (>5 min)
- Out of scope for current commit
- Will be fixed in next commit

**For PR creation: No**
- ALL MEDIUM must be fixed
- Zero-tolerance before PR
- No deferral allowed

**Document any deferral (commits only):**
```markdown
## Deferred from Zen Review
- **MEDIUM:** Optimize query in get_positions()
- Reason: Requires profiling analysis
- Follow-up: Next commit in 30min
- Continuation-id: abc123
```

---

## Common Issues

### Zen-MCP Server Unavailable

**⚠️ CRITICAL: AI agents MUST NOT use ZEN_REVIEW_OVERRIDE without explicit human approval**

**Emergency override procedure (REQUIRES explicit user approval):**

1. **AI agent must ask user first:**
   ```
   "Zen-MCP server is unavailable. Options:
   1. Wait and retry review when server returns
   2. Use ZEN_REVIEW_OVERRIDE (requires your explicit approval)

   Which option do you prefer?"
   ```

2. **Only after user explicitly approves option 2, commit with override:**
   ```bash
   git commit -m "feat: Add feature

   ZEN_REVIEW_OVERRIDE: Server unavailable
   Reason: Critical bugfix
   User approved by: [user name]
   Will request post-commit review ASAP"
   ```

3. **Then:**
   - Document in team chat
   - Request review when server returns
   - Create follow-up PR if issues found

**❌ NEVER:**
- Use `ZEN_REVIEW_OVERRIDE` without asking user first
- Assume user wants to bypass review
- Use override for convenience or time-saving
- Use `git commit --no-verify` (detected by CI and blocked)

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

### Example 1: Progressive Commit - Clean Approval

```bash
$ git add apps/execution_gateway/order_placer.py
$ "Comprehensive review my staged changes"

# Phase 1: Gemini analysis → ✅ No issues
# Phase 2: Codex synthesis → ✅ Approved

$ git commit -m "feat: Add position validation

zen-mcp-review: approved
gemini-continuation-id: abc123-...
codex-continuation-id: def456-..."
```

### Example 2: Progressive Commit - Issues Found & Fixed

```bash
$ "Comprehensive review my staged changes"

# Phase 1: Gemini finds CRITICAL: missing circuit breaker
# Phase 2: Codex confirms: DO NOT commit

# Fix immediately
$ git add apps/execution_gateway/order_placer.py
$ "I've added circuit breaker check, request fresh verification"

# Fresh Phase 1: Gemini reviews fixes → ✅ No issues
# Fresh Phase 2: Codex verifies → ✅ Approved

$ git commit -m "feat: Add position validation

- Implement check_position_limits()
- Add circuit breaker (zen critical fix)

zen-mcp-review: approved (critical issue fixed)
gemini-continuation-id: xyz789-...
codex-continuation-id: uvw012-..."
```

### Example 3: PR Creation - Multi-Iteration Until Clean

```bash
# === Iteration 1 ===
$ "Request comprehensive review from Gemini (fresh, independent analysis)"
# Gemini finds: 2 CRITICAL, 1 HIGH, 2 MEDIUM
# Returns gemini_continuation_id_1 (DISCARD - issues found)

$ "Request comprehensive review from Codex (fresh, independent analysis)"
# Codex finds: 1 CRITICAL (CLI wiring - different from Gemini), 1 MEDIUM
# Returns codex_continuation_id_1 (DISCARD - issues found)

# Fix ALL issues from BOTH reviewers
$ git add <files>
$ git commit -m "fix: Address review findings - iteration 1"

# === Iteration 2 (FRESH reviews, no memory) ===
$ "Request comprehensive review from Gemini (fresh, complete branch)"
# Gemini finds: 1 MEDIUM (new issue in fixes)
# Returns gemini_continuation_id_2 (DISCARD - issues found)

$ "Request comprehensive review from Codex (fresh, complete branch)"
# Codex finds: 0 issues
# Returns codex_continuation_id_2 (DISCARD - Gemini still has issues)

# Fix Gemini's MEDIUM
$ git add <files>
$ git commit -m "fix: Address Gemini MEDIUM - iteration 2"

# === Iteration 3 (FRESH reviews again) ===
$ "Request comprehensive review from Gemini (fresh, complete branch)"
# Gemini: ✅ NO issues
# Returns gemini_continuation_id_3 (KEEP - approved)

$ "Request comprehensive review from Codex (fresh, complete branch)"
# Codex: ✅ NO issues
# Returns codex_continuation_id_3 (KEEP - approved)

# Result: ✅ BOTH reviewers approve with ZERO issues
# ONLY NOW record continuation IDs from iteration 3 (final approved)
# Create PR with gemini_continuation_id_3 + codex_continuation_id_3
```

---

## Validation Checklist

**Comprehensive review succeeded:**
- [ ] Two-phase review requested and completed (~3-5 min)
- [ ] ALL HIGH/CRITICAL issues fixed
- [ ] ALL MEDIUM fixed (for PR) OR deferred with justification (for commits)
- [ ] Explicit "approved" from both Gemini AND Codex
- [ ] Both continuation IDs in commit message / PR description
- [ ] All tests pass (`make ci-local` for PR)

---

## See Also

- [Zen-MCP Review Process](./_common/zen-review-process.md) - Complete tier details
- [Clink-Only Tool Usage](./_common/clink-policy.md) - Tool policy
- [Zen Review Command](./_common/zen-review-command.md) - Slash command details
- [01-git.md](./01-git.md) - Progressive commits and PRs workflow
- [/docs/STANDARDS/GIT_WORKFLOW.md](../../STANDARDS/GIT_WORKFLOW.md) - Review requirements
