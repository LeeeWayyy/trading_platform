# Zen-MCP Comprehensive Review Workflow

**Purpose:** Code quality and trading safety validation via shared-context iteration reviews
**Tools:** zen-mcp via clink (reviewers defined in `.ai_workflow/config.json`)

---

## ⚠️ CRITICAL: Two Mandatory Requirements

> **1. ALWAYS pack with Repomix BEFORE every review request**
> - Run `/repomix-commands:pack-local` on all changed directories
> - This provides ~70% token reduction and enables thorough analysis
> - **NEVER request a review without packing first**
>
> **2. ALWAYS get ALL reviewers' approval BEFORE starting a new iteration**
> - Each iteration MUST end with explicit approval from ALL reviewers
> - DO NOT start a new iteration until current iteration has all approvals
> - If ANY reviewer has not approved, continue re-reviewing in the current iteration

---

## Review System Overview

| When | Duration | Scope |
|------|----------|-------|
| Before EVERY commit AND before creating PR | ~3-5 min | All changes (staged or branch) |

**Approach:** Shared-context iterations with fresh-start gates between iterations.

**Benefits:**
- Lower context usage (reviewers share context within iteration)
- Accumulated findings from all reviewers before fixing
- Fresh-start gate ensures unbiased final approval

---

## When to Review

**Required for:**
- Every commit that touches code
- Before creating any pull request
- Trading logic, order placement, risk checks
- Circuit breaker code, data handling, API endpoints

**Can skip only for:**
- Auto-generated files (package-lock.json, poetry.lock)
- Documentation-only changes (mark with `#docs-only`)
- Emergency hotfixes (with user approval + mandatory post-commit review)

---

## Review Process

### Step 0: Pack Context for Review (⚠️ MANDATORY - NEVER SKIP)

**🚨 CRITICAL: ALWAYS pack with Repomix BEFORE every review request. NO EXCEPTIONS.**

```bash
# Pack the directories you modified
/repomix-commands:pack-local ./libs/<changed> ./apps/<changed> ./tests/<changed>
```

**Why pack before review?**
- Provides reviewers with structured, compressed context
- ~70% token reduction enables more thorough analysis
- Reviewers can understand full component context, not just diffs
- Catches issues that span multiple files

**What to pack:**
- All directories with staged changes
- Related test directories
- Any dependencies that reviewers should understand

**⚠️ AI agents MUST NOT proceed to Step 1 without completing Step 0.**

**See [06-repomix.md](./06-repomix.md) for complete guide**

### Step 1: Prepare Changes

```bash
git add <files>
git status
git diff --cached
```

### Step 2: Start Iteration

#### First Reviewer: Fresh Start (No Continuation ID)

Request review from the first enabled reviewer with this prompt:

```
Request comprehensive zen-mcp review (fresh start, no continuation ID)

Review all staged changes with focus on but not limited to:

**Architecture & Design (HIGH):**
- Design patterns and maintainability standards followed?
- Integration impact on other components considered?

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

**Security (HIGH):**
- Credentials, API keys never hardcoded or logged?
- SQL queries parameterized?

**Code Quality (MEDIUM):**
- All function signatures properly typed?
- Input data validated (Pydantic, assertions)?
- Docstrings complete and accurate?
- ADRs created for architectural changes?

**Testing & Edge Cases (MEDIUM):**
- All code paths and boundary conditions tested?
- Failure modes handled correctly?

**Domain-Specific (HIGH):**
- Research and production share feature calculation code?
- All timestamps UTC and timezone-aware?

Provide comprehensive analysis with issues categorized by severity.
```

**This generates `continuation_id` for this iteration.**

#### Subsequent Reviewers: Use Same Continuation ID

```
Request comprehensive zen-mcp review
Using continuation_id: <continuation_id_from_first_reviewer>

[SAME REVIEW PROMPT AS ABOVE]

Build upon the shared context by adding your own independent findings.
```

### Step 3: Fix ALL Issues

**⚠️ ZERO TOLERANCE - Fix ALL issues including LOW severity. DO NOT SKIP.**

| Severity | Action |
|----------|--------|
| **HIGH/CRITICAL** | ❌ MUST fix immediately - blocks commit |
| **MEDIUM** | ❌ MUST fix immediately - no deferral allowed |
| **LOW** | ❌ MUST fix - no exceptions, no "cleanup later" |

**CRITICAL: AI coders MUST NOT:**
- Ignore LOW issues because "they're minor"
- Defer fixes to "future cleanup"
- Proceed to commit without addressing ALL issues
- Rationalize skipping issues (e.g., "it's pre-existing code")

**Only skip if:** You can prove it's a **false positive** AND document the reasoning in the commit message.

**If unsure:** Fix it. When in doubt, fix it.

### Step 4: Iteration Loop

#### Within Iteration (Shared Context)

If issues found:
1. Collect ALL issues from ALL reviewers
2. Fix ALL issues
3. Re-review using **SAME continuation_id**
4. **🚨 CRITICAL: Continue re-reviewing until ALL reviewers explicitly approve**
5. **DO NOT start a new iteration until you have approval from EVERY reviewer**

**⚠️ MANDATORY: Each iteration MUST end with ALL reviewers approving:**
- Get Reviewer 1 approval ✓
- Get Reviewer 2 approval ✓
- Get Reviewer N approval ✓
- **ONLY THEN** can you consider starting a new iteration (if fixes were made)

#### Between Iterations (Fresh Gate)

**⚠️ PREREQUISITE: You MUST have ALL approvals from the current iteration before starting a new one.**

If fixes were made in iteration N (and ALL reviewers approved):
1. Start iteration N+1 **FRESH** (no continuation_id)
2. First reviewer uses **EXACTLY THE SAME PROMPT as iteration 1** (no mention of previous iterations, no bias about what was fixed)
3. Generates NEW continuation_id
4. Follow within-iteration loop
5. **Again, get ALL approvals before proceeding**

**CRITICAL:** The prompt for iteration N must be identical to iteration 1. Do NOT say "review my fixes" or "check the changes I made". The reviewer must see the code with completely fresh eyes.

### Step 5: Approval Condition

**Approved when:** ALL reviewers explicitly approve (say "APPROVED", "safe to merge", etc.)

**Approval with issues:**
- If reviewer says "APPROVED" with **only LOW issues** → fix them → commit (no fresh iteration needed)
- If reviewer lists **MEDIUM/HIGH issues** → treat as rejection → fix all issues → start fresh iteration
- If reviewer does NOT say "APPROVED" → fix all issues → start fresh iteration

**Note:** An "APPROVED" with MEDIUM/HIGH issues is contradictory and should be treated as a rejection requiring fixes.

**⚠️ You still MUST fix ALL issues including LOW before committing.**

Record that `continuation_id` as the approval marker.

---

## Commit Message Format

```
feat: Add feature

zen-mcp-review: approved
continuation-id: <uuid-from-approved-iteration>
```

**Example:**
```bash
git commit -m "fix(workflow): Complete comprehensive review

zen-mcp-review: approved
continuation-id: ae512f21-f9fe-4c3a-9e7e-bfaa8b07e5fd"
```

---

## Quick Reference

### ⚠️ Two Critical Requirements (NEVER SKIP)

1. **Pack with Repomix** → BEFORE every review request
2. **Get ALL approvals** → BEFORE starting any new iteration

### Iteration Flow

```
⚠️ STEP 0: Pack with Repomix (MANDATORY before any review)

Iteration 1:
  Reviewer 1 (fresh) → generates continuation_id_1
  Reviewer 2 (uses continuation_id_1)
  Issues found? → Fix → Re-review with continuation_id_1
  ⚠️ WAIT: Get ALL approvals (Reviewer 1 ✓ + Reviewer 2 ✓)
  All approved + fixes were made? → Start Iteration 2

Iteration 2:
  Reviewer 1 (fresh) → generates continuation_id_2
  Reviewer 2 (uses continuation_id_2)
  ⚠️ WAIT: Get ALL approvals (Reviewer 1 ✓ + Reviewer 2 ✓)
  All approve on first try? → APPROVED with continuation_id_2
```

### Decision Tree

```
Start Review
    ↓
⚠️ MANDATORY: Pack with Repomix first
    ↓
First reviewer starts FRESH (no continuation_id)
    ↓
Subsequent reviewers use SAME continuation_id
    ↓
Issues found?
    YES → Fix all → Re-review with SAME continuation_id
              ↓
          ⚠️ ALL reviewers approved?
              NO  → Continue re-reviewing (DO NOT start new iteration)
              YES → Were fixes made this iteration?
                        YES → Start NEW iteration (fresh)
                        NO  → APPROVED ✓
```

---

## Common Issues

### Server Unavailable

**AI agents MUST ask user before using override:**

```
"Zen-MCP server unavailable. Options:
1. Wait and retry when server returns
2. Use ZEN_REVIEW_OVERRIDE (requires your explicit approval)

Which option do you prefer?"
```

Only after user approval:
```bash
git commit -m "feat: Add feature

ZEN_REVIEW_OVERRIDE: Server unavailable
User approved by: [user name]"
```

### Can't Tell If Approved

Ask explicitly: "Is this code approved for commit, or are there blocking issues?"

Look for:
- ✅ "Safe to commit" / "Approved"
- ❌ "Fix [X] before committing"

---

## Validation Checklist

**🚨 MANDATORY - Verify BOTH critical requirements:**
- [ ] **⚠️ REPOMIX: Packed ALL relevant directories with repomix BEFORE requesting review**
- [ ] **⚠️ ALL APPROVALS: Got ALL reviewers' approval for EACH iteration before starting new iteration**

**Standard checklist:**
- [ ] First reviewer started FRESH (no continuation ID)
- [ ] Subsequent reviewers used SAME continuation ID
- [ ] ALL issues fixed (zero tolerance)
- [ ] If fixes made, started NEW iteration FRESH (only after ALL approved current iteration)
- [ ] Final iteration: ALL reviewers approved on FIRST TRY
- [ ] Continuation ID in commit message
- [ ] All tests pass (`make ci-local`)
