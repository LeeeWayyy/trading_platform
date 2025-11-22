# Zen-MCP Code Review Command

**Purpose:** Perform mandatory comprehensive zen-mcp code review before committing changes.

**Usage:** `/zen-review`

---

## Instructions for Claude Code

When this command is invoked, follow this workflow:

### Step 1: Check Files to Review

**For staged changes (progressive commits):**
```bash
git diff --cached --name-only --diff-filter=ACM
```

**If no files staged:**
- Tell user: "❌ No files staged for commit. Please stage files first with `git add <files>`"
- Ask: "Would you like to review all branch changes vs origin/master instead?"
- If yes, proceed to branch review mode

**For branch changes (before PR):**
```bash
git diff origin/master...HEAD --name-only --diff-filter=ACM
```

**If on main/master branch:**
- Tell user: "⚠️ Already on main branch. Create a feature branch first: `git checkout -b feature/name`"
- Stop here

**If no changes:**
- Tell user: "❌ No changes found"
- Stop here

**If changes found:**
- List all changed files to user (all types: .py, .sh, .yaml, .md, .sql, etc.)
- Proceed to Step 2

### Step 2: Request Comprehensive Zen-MCP Review

Use the two-phase gemini → codex pattern for thorough review:

**Phase 1 - Gemini Comprehensive Analysis:**

```
"Use zen clink with gemini codereviewer (model: gemini-2.5-pro) for comprehensive analysis:

**Architecture & Design:**
 - Design patterns: Are patterns used correctly and consistently?
 - Code organization: Is code well-structured and maintainable?
 - Integration: How do changes affect other components?

**Trading Safety (CRITICAL):**
 - Circuit breaker checks: Are circuit breaker states checked before critical operations?
 - Idempotent order IDs: Are client_order_id values deterministic and collision-free?
 - Position limit validation: Are per-symbol and total position limits enforced?
 - Order state validation: Are state transitions valid and checked?

**Concurrency & Data Safety (HIGH):**
 - Race conditions: Are Redis WATCH/MULTI/EXEC patterns used for concurrent updates?
 - Database transactions: Are DB operations wrapped in proper transactions?
 - Atomic operations: Are read-modify-write sequences atomic?

**Error Handling (HIGH):**
 - Exception handling: Are exceptions caught, logged with context, and re-raised appropriately?
 - Logging completeness: Do logs include strategy_id, client_order_id, symbol, and other context?
 - Error propagation: Are errors propagated correctly to callers?

**Code Quality (MEDIUM):**
 - Type hints: Are all function signatures properly typed?
 - Data validation: Is input data validated (Pydantic models, assertions)?
 - Resource cleanup: Are connections, files, and resources properly closed?
 - Null/None handling: Are None cases handled safely?

**Security (HIGH):**
 - Secrets handling: Are credentials, API keys never hardcoded or logged?
 - SQL injection: Are all SQL queries parameterized (no string interpolation)?
 - Input validation: Are user inputs and external data validated before use?

**Configuration & Environment (MEDIUM):**
 - DRY_RUN mode: Is DRY_RUN flag respected for order submission?
 - No hardcoding: Are magic numbers/strings replaced with config values?
 - Environment variables: Are all configs from settings/env, not hardcoded?

**Standards Compliance (MEDIUM):**
 - Docstrings: Are all functions documented per /docs/STANDARDS/DOCUMENTATION_STANDARDS.md?
 - Coding standards: Does code follow /docs/STANDARDS/CODING_STANDARDS.md?
 - Test coverage: Are tests added for new code paths?

**Domain-Specific (HIGH):**
 - Feature parity: Do research and production share feature calculation code?
 - Timezone handling: Are all timestamps UTC and timezone-aware?
 - API contracts: Do changes comply with OpenAPI specs in /docs/API/?

**Testing & Edge Cases:**
 - Test coverage: Are all code paths tested?
 - Edge cases: Are boundary conditions handled?
 - Error scenarios: Are failure modes tested?

**Documentation:**
 - Docstrings complete and accurate?
 - ADRs needed for architectural changes?
 - User-facing docs updated?

**Performance:**
 - Are there obvious performance issues?
 - Database query optimization needed?
 - Memory management concerns?

Provide comprehensive analysis with all issues categorized by severity (CRITICAL/HIGH/MEDIUM/LOW).
Provide continuation_id for follow-up synthesis."
```

Include the file paths as attachments.

**Phase 2 - Codex Independent Review (fresh perspective):**

```
"Use zen clink with codex codereviewer (model: gpt-5-codex) for fresh independent review:

Review the same files with comprehensive analysis (same as Gemini criteria):
[Include same file paths and comprehensive checklist]

Provide INDEPENDENT analysis:
1. All issues found (categorized by severity)
2. Prioritized action items
3. Final verdict: APPROVED / MUST FIX / RECOMMENDED FIXES

DO NOT reference Gemini's review - provide completely fresh perspective."
```

### Step 3: Analyze Findings

**Parse zen-mcp responses for:**
- Issue severity (CRITICAL, HIGH, MEDIUM, LOW)
- Issue locations (file:line)
- Issue descriptions
- Recommended fixes

**Display comprehensive summary to user:**
```
🔍 Zen-MCP Comprehensive Review Results:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Files reviewed: N
Total issues found: X

By Severity:
  - CRITICAL: X
  - HIGH: Y
  - MEDIUM: Z
  - LOW: W

Top Issues:
  1. [CRITICAL] File.py:123 - Description
  2. [HIGH] File.py:456 - Description
  ...
```

### Step 4: Apply Enforcement Rules

**CRITICAL or HIGH issues found:**
- ❌ **CANNOT COMMIT**
- Tell user: "❌ Blocking issues found. Must fix before committing."
- List all CRITICAL/HIGH issues with file:line locations
- Offer to fix them automatically (ask user permission)
- If user wants to override, require explicit confirmation + document reason

**MEDIUM issues found:**
- ⚠️ **SHOULD FIX**
- Tell user: "⚠️ Medium severity issues found. Recommended to fix before committing."
- List MEDIUM issues
- Ask user: "Fix now, defer with justification, or override?"

**Only LOW or no issues:**
- ✅ **SAFE TO COMMIT**
- Tell user: "✅ Zen-MCP comprehensive review passed! Safe to commit."
- If LOW issues exist, mention them but don't block

### Step 5: Fix Issues (if needed)

**If user wants fixes:**
1. For each issue, read the file
2. Apply fix using Edit tool
3. Explain fix to user
4. Update todo list

**After all fixes applied:**
- Re-stage files: `git add <fixed-files>`
- Proceed to Step 6

### Step 6: Fresh Independent Reviews After Fixes

**IMPORTANT: Request completely fresh reviews - do NOT mention previous issues or fixes**

**Phase 1 - Gemini Fresh Review:**
```
"Use zen clink with gemini codereviewer for fresh comprehensive review of all staged changes:

[Include same comprehensive checklist as initial review]

Review all the code as-is. Provide fresh independent analysis with no reference to any previous reviews."
```

**Phase 2 - Codex Fresh Review:**
```
"Use zen clink with codex codereviewer for fresh comprehensive review of all staged changes:

[Include same comprehensive checklist as initial review]

Review all the code as-is. Provide fresh independent analysis with no reference to any previous reviews (including Gemini's)."
```

**Based on fresh review responses:**
- ✅ If BOTH approve with ZERO issues → Record final continuation IDs → Tell user "✅ Safe to commit!"
- ❌ If ANY issues found → Return to Step 4 (fix and restart fresh reviews)
- Note: Discard continuation IDs from iterations that found issues - only keep final approved IDs

### Step 7: Handle Zen-MCP Unavailability

**If zen-mcp is unavailable (API quota, network, etc.):**

1. Tell user: "⚠️ Zen-MCP unavailable: [error message]"
2. Offer options:
   - **Option A:** Wait and retry
   - **Option B:** Manual review (user reviews code themselves)
   - **Option C:** Override and commit (requires explicit user approval)

**If user chooses Override:**
- Document in commit message:
  ```
  ⚠️ COMMITTED WITHOUT ZEN-MCP REVIEW
  Reason: [zen unavailable / urgent fix / etc.]
  Manual review: [user name / none]
  ```

### Step 8: Document Final Approved Review

**ONLY record continuation IDs from the final iteration where BOTH reviewers approved with ZERO issues**

**Save review metadata:**
- Create/update `.zen-review-log.json`:
  ```json
  {
    "commit_sha": "pending",
    "timestamp": "ISO8601",
    "files_reviewed": [...],
    "iterations": N,
    "final_gemini_continuation_id": "...",
    "final_codex_continuation_id": "...",
    "status": "approved|overridden|skipped",
    "override_reason": "..."
  }
  ```

**For commits, tell user to include in commit message:**
```
zen-mcp-review: approved
gemini-continuation-id: <final-uuid-from-approved-iteration>
codex-continuation-id: <final-uuid-from-approved-iteration>
```

**For PRs before creation:**
- Tell user to include in PR description:
  ```
  ## Zen-MCP Comprehensive Review
  - Iterations: X (until zero issues)
  - Final Gemini Continuation ID: `<uuid-from-final-approved-iteration>`
  - Final Codex Continuation ID: `<uuid-from-final-approved-iteration>`
  - Status: ✅ Approved (both reviewers, zero issues)
  ```

---

## Example Execution

**User invokes:** `/zen-review`

**Claude Code:**
1. Checks staged files → 3 Python files found
2. Lists files to user
3. Requests Phase 1 (Gemini) comprehensive review
4. Requests Phase 2 (Codex) synthesis
5. Receives combined response: 1 HIGH issue, 2 MEDIUM issues
6. Blocks commit, lists issues with details
7. Asks user if should fix
8. User approves → fixes all issues
9. Re-requests two-phase verification
10. Both Gemini and Codex approve → tells user safe to commit
11. Logs review metadata with both continuation IDs

---

## Success Criteria

✅ All CRITICAL and HIGH issues fixed or explicitly overridden
✅ Zen-MCP two-phase review documented (both continuation_ids saved)
✅ User informed of commit safety status
✅ Review metadata logged for audit trail
✅ Comprehensive coverage of all review categories

---

**See also:**
- [Zen-MCP Review Process](./zen-review-process.md) - Complete review system details
- [03-reviews.md](../03-reviews.md) - Full review workflow
- `/docs/STANDARDS/GIT_WORKFLOW.md` - Pre-commit review requirements
