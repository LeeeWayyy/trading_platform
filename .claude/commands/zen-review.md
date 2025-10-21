# Zen-MCP Code Review Command

**Purpose:** Perform mandatory zen-mcp code review before committing changes.

**Usage:** `/zen-review [quick|deep]`

---

## Instructions for Claude Code

When this command is invoked, follow this workflow:

### Step 1: Determine Review Mode

**Quick Mode (default):**
- Use for progressive commits (every 30-60 min)
- Reviews **staged changes** (git diff --cached)
- Focus on critical trading safety issues
- Target: <60 seconds
- **Requires files to be staged**

**Deep Mode:**
- Use before creating pull request
- Reviews **all branch changes** (git diff origin/main...HEAD)
- Comprehensive review
- Target: 3-5 minutes
- **Works with clean staging area** (all commits already made)

### Step 2: Check Files to Review

**For Quick Mode:**
```bash
git diff --cached --name-only --diff-filter=ACM
```

**If no files staged:**
- Tell user: "❌ No files staged for commit. Please stage files first with `git add <files>`"
- Stop here

**If files staged:**
- List the files to user (all types: .py, .sh, .yaml, .md, .sql, etc.)
- Proceed to Step 3

**For Deep Mode:**
```bash
git diff origin/main...HEAD --name-only --diff-filter=ACM
```

**If on main/master branch:**
- Tell user: "⚠️ Already on main branch. Create a feature branch first: `git checkout -b feature/name`"
- Stop here

**If no changes in branch:**
- Tell user: "❌ No changes in this branch vs origin/main"
- Stop here

**If changes found:**
- List all changed files to user
- Proceed to Step 3

### Step 3: Request Zen-MCP Review

**For Quick Review:**

Use zen clink with codex codereviewer role:

```
"Use zen clink with codex codereviewer (model: gpt-5-codex) to review my staged changes for:

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

Focus on HIGH and CRITICAL severity issues. Flag any blocking issues immediately."
```

Include the staged file paths as attachments.

**For Deep Review:**

```
"Use zen clink with codex codereviewer for comprehensive review of ALL branch changes.

**IMPORTANT:** Review all files changed in this branch (compare HEAD to origin/main),
NOT just staged files. The staging area may be clean - all commits are already made.

Check comprehensively:
 - Overall architecture and design patterns
 - Test coverage (unit, integration, edge cases)
 - Edge cases and error handling
 - Integration points with other services
 - Documentation completeness (docstrings, ADRs, guides)
 - Performance implications
 - Security considerations
 - Feature parity between research and production
 - Idempotency guarantees
 - Circuit breaker integration
 - Type hints and data validation
 - Concurrency safety (transactions, atomic operations)
 - Configuration and environment handling
 - Timezone handling (UTC timezone-aware)
 - API contract compliance

Provide detailed analysis with severity levels (CRITICAL/HIGH/MEDIUM/LOW).
Be thorough - this is the final gate before PR creation."
```

Include all changed files in the branch (from `git diff origin/main...HEAD`).

### Step 4: Analyze Findings

**Parse zen-mcp response for:**
- Issue severity (CRITICAL, HIGH, MEDIUM, LOW)
- Issue locations (file:line)
- Issue descriptions
- Recommended fixes

**Display summary to user:**
```
🔍 Zen-MCP Review Results:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Files reviewed: N
Issues found:
  - CRITICAL: X
  - HIGH: Y
  - MEDIUM: Z
  - LOW: W
```

### Step 5: Apply Enforcement Rules

**CRITICAL or HIGH issues found:**
- ❌ **CANNOT COMMIT**
- Tell user: "❌ Blocking issues found. Must fix before committing."
- List all CRITICAL/HIGH issues
- Offer to fix them automatically (ask user permission)
- If user wants to override, require explicit confirmation + document reason

**MEDIUM issues found:**
- ⚠️ **SHOULD FIX**
- Tell user: "⚠️ Medium severity issues found. Recommended to fix."
- List MEDIUM issues
- Ask user: "Fix now, defer with justification, or override?"

**Only LOW or no issues:**
- ✅ **SAFE TO COMMIT**
- Tell user: "✅ Zen-MCP review passed! Safe to commit."
- If LOW issues exist, mention them but don't block

### Step 6: Fix Issues (if needed)

**If user wants fixes:**
1. For each issue, read the file
2. Apply fix using Edit tool
3. Explain fix to user
4. Update todo list

**After all fixes applied:**
- Re-stage files: `git add <fixed-files>`
- Proceed to Step 7

### Step 7: Verify Fixes

**Re-request zen-mcp review:**

```
"Use zen clink with codex codereviewer to verify my fixes for the issues you found:

Previous issues fixed:
[List the issues that were addressed]

Please confirm:
1. Are the original issues resolved?
2. Did fixes introduce new issues?
3. Is it safe to commit now?"
```

**Based on verification response:**
- ✅ If approved → Tell user "✅ Zen-MCP verified fixes. Safe to commit!"
- ❌ If new issues → Return to Step 5
- ⚠️ If concerns → Discuss with user

### Step 8: Handle Zen-MCP Unavailability

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

### Step 9: Document Review

**Save review metadata:**
- Create/update `.zen-review-log.json`:
  ```json
  {
    "commit_sha": "pending",
    "timestamp": "ISO8601",
    "mode": "quick|deep",
    "files_reviewed": [...],
    "issues_found": {
      "critical": N,
      "high": N,
      "medium": N,
      "low": N
    },
    "continuation_id": "...",
    "status": "approved|overridden|skipped",
    "override_reason": "..."
  }
  ```

**For deep reviews before PR:**
- Tell user to include in PR description:
  ```
  ## Zen-MCP Review
  - Continuation ID: `...`
  - Issues caught and fixed: X
  - Status: ✅ Approved
  ```

---

## Example Execution

**User invokes:** `/zen-review`

**Claude Code:**
1. Checks staged files → 3 Python files found
2. Lists files to user
3. Requests quick review from zen-mcp via clink
4. Receives response: 1 HIGH issue, 2 MEDIUM issues
5. Blocks commit, lists issues
6. Asks user if should fix
7. User approves → fixes all issues
8. Re-requests verification
9. Zen approves → tells user safe to commit
10. Logs review metadata

---

## Success Criteria

✅ All CRITICAL and HIGH issues fixed or explicitly overridden
✅ Zen-MCP review documented (continuation_id saved)
✅ User informed of commit safety status
✅ Review metadata logged for audit trail

---

**See also:**
- `/docs/IMPLEMENTATION_GUIDES/workflow-optimization-zen-mcp.md`
- `/docs/STANDARDS/GIT_WORKFLOW.md` - Pre-commit review requirements
- `/scripts/zen_review.sh` - Helper script for developers
