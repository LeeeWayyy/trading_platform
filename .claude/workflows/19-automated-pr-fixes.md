# Automated PR Fix Cycle

**Purpose:** Automatically address PR review comments and CI failures
**When:** After creating PR, while waiting for approval
**Prerequisites:** PR created with `gh pr create`
**Expected Outcome:** PR comments addressed, CI passing, PR ready for merge

---

## Quick Reference

**Time Savings:**
- Manual PR fix cycle: 30-60 minutes per iteration (read comments, fix, push, wait for CI)
- Automated PR fix cycle: 10-15 minutes per iteration (automation handles parsing + fixing)
- **Savings: 20-45 minutes per iteration (50-75% reduction)**

**What's Automated:**
- ✅ PR comment polling (gh API)
- ✅ Comment parsing and categorization
- ✅ Auto-fix for HIGH confidence comments
- ✅ CI failure detection
- ✅ CI log analysis and auto-fix
- ✅ Commit with standardized format
- ✅ Push to remote

**What Remains Human-Guided:**
- 🧑 LOW confidence fixes (architecture changes, logic bugs)
- 🧑 Breaking changes requiring user approval
- 🧑 Final merge decision

---

## Automated Fix Loop

### Option 1: Polling Loop (Implemented)

```bash
WHILE PR not approved:
  1. Poll PR status (every 5 minutes)
  2. IF new comments → parse and auto-fix
  3. IF CI failures → parse logs and auto-fix
  4. Sleep 5 minutes
  5. Repeat

MAX_ITERATIONS: 10 attempts
TIMEOUT: 2 hours
```

### Option 2: Webhook-Triggered (Future Enhancement)

```yaml
# .github/workflows/auto-fix-pr-comments.yml
name: Auto-Fix PR Comments

on:
  pull_request_review:
    types: [submitted]
  check_run:
    types: [completed]
  issue_comment:
    types: [created]

jobs:
  auto-fix:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Parse PR comments
        run: ./scripts/parse_pr_comments.py
      - name: Auto-fix issues
        run: ./scripts/auto_fix_pr_issues.py
      - name: Commit and push
        run: |
          git config user.name "Claude Code Bot"
          git config user.email "noreply@anthropic.com"
          git add .
          git commit -m "fix(auto): Address PR comment #${{ github.event.comment.id }}"
          git push
```

---

## Step-by-Step Process

### Step 1: Poll PR Status

**Action:** Check for new comments and CI status

```bash
# Get PR number from current branch
PR_NUMBER=$(gh pr view --json number -q .number)

# Check PR status
PR_STATUS=$(gh pr view $PR_NUMBER --json reviewDecision,statusCheckRollup -q '{reviewDecision, statusCheckRollup}')

# Parse review decision
REVIEW_DECISION=$(echo "$PR_STATUS" | jq -r '.reviewDecision')
# Values: APPROVED, CHANGES_REQUESTED, COMMENTED, null

# Parse CI status
CI_STATUS=$(echo "$PR_STATUS" | jq -r '.statusCheckRollup[0].conclusion')
# Values: SUCCESS, FAILURE, PENDING, null
```

---

### Step 2: Fetch and Parse PR Comments

**Action:** Get all comment types (inline, review, issue)

```bash
# Fetch inline comments (code-specific)
INLINE_COMMENTS=$(gh api repos/{owner}/{repo}/pulls/$PR_NUMBER/comments \
  | jq '.[] | {
      id: .id,
      type: "inline",
      file: .path,
      line: .line,
      body: .body,
      user: .user.login,
      created_at: .created_at
    }')

# Fetch review comments (general feedback)
REVIEW_COMMENTS=$(gh pr view $PR_NUMBER --json reviews \
  | jq '.reviews[] | {
      id: .id,
      type: "review",
      body: .body,
      user: .author.login,
      created_at: .submittedAt,
      state: .state
    }')

# Fetch issue comments (discussion)
ISSUE_COMMENTS=$(gh api repos/{owner}/{repo}/issues/$PR_NUMBER/comments \
  | jq '.[] | {
      id: .id,
      type: "issue",
      body: .body,
      user: .user.login,
      created_at: .created_at
    }')

# Combine all comments
ALL_COMMENTS=$(echo "$INLINE_COMMENTS $REVIEW_COMMENTS $ISSUE_COMMENTS" | jq -s 'add')
```

---

### Step 3: Categorize Comments

**Action:** Filter actionable comments

```python
def categorize_comments(comments):
    """
    Categorize PR comments into actionable vs. non-actionable.

    Args:
        comments: List of PR comments (inline, review, issue)

    Returns:
        Categorized comments dict
    """
    actionable = []
    questions = []
    approvals = []
    non_actionable = []

    for comment in comments:
        body = comment["body"].lower()

        # Filter non-actionable
        if any(phrase in body for phrase in ["lgtm", "👍", "thanks", "nice"]):
            non_actionable.append(comment)
            continue

        # Approvals
        if any(phrase in body for phrase in ["approved", "looks good", "ship it"]):
            approvals.append(comment)
            continue

        # Questions (require human response)
        if "?" in body or any(phrase in body for phrase in ["why", "how", "what"]):
            questions.append(comment)
            continue

        # Actionable (code change requests)
        if any(phrase in body for phrase in [
            "missing",
            "should",
            "need to",
            "must",
            "required",
            "add",
            "fix",
            "change",
            "update",
            "remove"
        ]):
            actionable.append({
                **comment,
                "severity": determine_severity(comment),
                "confidence": determine_fix_confidence(comment)
            })

    return {
        "actionable": actionable,
        "questions": questions,
        "approvals": approvals,
        "non_actionable": non_actionable,
        "stats": {
            "total": len(comments),
            "actionable": len(actionable),
            "questions": len(questions),
            "approvals": len(approvals)
        }
    }
```

**Severity Determination:**

```python
def determine_severity(comment):
    """Determine comment severity based on keywords."""
    body = comment["body"].lower()

    # High severity (blocking)
    if any(phrase in body for phrase in [
        "blocking",
        "critical",
        "security",
        "safety",
        "circuit breaker",
        "risk violation",
        "idempotency"
    ]):
        return "high"

    # Medium severity (quality)
    if any(phrase in body for phrase in [
        "inconsistent",
        "missing test",
        "error handling",
        "logging",
        "pattern"
    ]):
        return "medium"

    # Low severity (style)
    return "low"
```

**Fix Confidence Determination:**

```python
def determine_fix_confidence(comment):
    """Determine confidence level for auto-fixing."""
    body = comment["body"].lower()

    # HIGH confidence (mechanical fixes)
    if any(phrase in body for phrase in [
        "missing docstring",
        "typo",
        "unused import",
        "formatting",
        "line too long",
        "missing type hint"
    ]):
        return "HIGH"

    # MEDIUM confidence (standard pattern application)
    if any(phrase in body for phrase in [
        "missing logging",
        "inconsistent error handling",
        "missing retry decorator",
        "add circuit breaker check"
    ]):
        return "MEDIUM"

    # LOW confidence (requires judgment)
    return "LOW"
```

---

### Step 4: Auto-Fix Actionable Comments

**Action:** Apply fixes for HIGH/MEDIUM confidence comments

```python
def auto_fix_pr_comment(comment):
    """
    Auto-fix a PR comment if confidence is HIGH or MEDIUM.

    Args:
        comment: Categorized comment with severity and confidence

    Returns:
        FixResult with status and details
    """
    if comment["confidence"] == "LOW":
        return FixResult(
            status="escalated",
            reason="Low confidence fix requires human judgment",
            comment=comment
        )

    # HIGH confidence fixes
    if comment["confidence"] == "HIGH":
        if "missing docstring" in comment["body"].lower():
            return fix_missing_docstring(comment)
        elif "unused import" in comment["body"].lower():
            return fix_unused_import(comment)
        elif "missing type hint" in comment["body"].lower():
            return fix_missing_type_hint(comment)
        elif "typo" in comment["body"].lower():
            return fix_typo(comment)

    # MEDIUM confidence fixes
    if comment["confidence"] == "MEDIUM":
        if "missing logging" in comment["body"].lower():
            return add_structured_logging(comment)
        elif "missing retry" in comment["body"].lower():
            return add_retry_decorator(comment)
        elif "circuit breaker" in comment["body"].lower():
            return add_circuit_breaker_check(comment)
        elif "inconsistent error handling" in comment["body"].lower():
            return standardize_error_handling(comment)

    return FixResult(
        status="escalated",
        reason="No matching fix pattern",
        comment=comment
    )
```

**Example Fix: Add Missing Circuit Breaker Check**

```python
def add_circuit_breaker_check(comment):
    """
    Add circuit breaker check before order submission.

    Comment example:
    "Missing circuit breaker check before order submission on line 142"
    """
    # Parse file and line from comment
    file_path = comment["file"]
    line_number = comment["line"]

    # Read file
    with open(file_path, "r") as f:
        lines = f.readlines()

    # Find function containing the line
    function_start = find_function_start(lines, line_number)

    # Insert circuit breaker check
    circuit_breaker_check = """
    # Check circuit breaker before order submission
    if self.redis.get("cb:state") == b"TRIPPED":
        self.logger.error(
            "Circuit breaker tripped - blocking order",
            extra={"symbol": symbol, "strategy_id": strategy_id}
        )
        raise CircuitBreakerTripped("Circuit breaker is tripped")
    """

    lines.insert(function_start + 1, circuit_breaker_check)

    # Write file
    with open(file_path, "w") as f:
        f.writelines(lines)

    return FixResult(
        status="fixed",
        file=file_path,
        description="Added circuit breaker check",
        comment_id=comment["id"]
    )
```

---

### Step 5: CI Failure Auto-Fix

**Action:** Detect and fix CI failures

```bash
# Get CI run status
CI_RUN_ID=$(gh run list --branch $(git branch --show-current) --json databaseId,conclusion -q '.[0] | select(.conclusion=="failure") | .databaseId')

if [ -n "$CI_RUN_ID" ]; then
    # Get failure logs
    CI_LOGS=$(gh run view $CI_RUN_ID --log)

    # Parse failures
    TEST_FAILURES=$(echo "$CI_LOGS" | grep -E "FAILED|ERROR" | head -20)
    TYPE_ERRORS=$(echo "$CI_LOGS" | grep "error:" | grep -E "Missing|Incompatible" | head -20)
    LINT_ERRORS=$(echo "$CI_LOGS" | grep -E "ruff|black" | head -20)

    # Auto-fix type errors
    if [ -n "$TYPE_ERRORS" ]; then
        python ./scripts/auto_fix_type_errors.py "$TYPE_ERRORS"
    fi

    # Auto-fix lint errors
    if [ -n "$LINT_ERRORS" ]; then
        make fmt  # Run black + ruff auto-fix
    fi

    # Test failures require human intervention
    if [ -n "$TEST_FAILURES" ]; then
        echo "⚠️ Test failures detected - escalating to human"
        exit 1
    fi
fi
```

---

### Step 6: Commit with Standardized Format

**Action:** Commit fixes with tracking metadata

```bash
# Standardized PR fix commit format
git add <modified_files>

git commit -m "$(cat <<'EOF'
fix(auto): Address PR comment #$COMMENT_ID - $BRIEF_DESCRIPTION

PR-comment-id: $COMMENT_ID
File: $FILE_PATH:$LINE_NUMBER
Reviewer: $REVIEWER_USERNAME

Fix: $DETAILED_FIX_DESCRIPTION

Zen-review: Verified fix (clink + codex)
Continuation-id: $CONTINUATION_ID

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"

# Push to remote
git push origin $(git branch --show-current)
```

**Example Commit Message:**

```
fix(auto): Address PR comment #12345678 - Missing circuit breaker check

PR-comment-id: 12345678
File: apps/execution_gateway/order_placer.py:142
Reviewer: @gemini-code-assist

Fix: Added circuit breaker check before order submission in place_order() method.
Now checks Redis cb:state and raises CircuitBreakerTripped if state is TRIPPED.
Includes structured logging with symbol and strategy_id context.

Zen-review: Verified fix (clink + codex)
Continuation-id: abc123def456

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

### Step 7: Verify Fix via Two-Phase Review

**Action:** **MANDATORY** two-phase review (gemini → codex) for ALL auto-fixes

**CRITICAL:** Even automated fixes require the same rigorous two-phase review as manual commits per CLAUDE.md policy.

```python
# MANDATORY: Two-phase review for ALL auto-fixes (HIGH/MEDIUM/LOW confidence)
# Phase 1: Gemini review
gemini_review = mcp__zen-mcp__clink(
    cli_name="gemini",
    role="codereviewer",
    prompt=f"""Review automated fix for PR comment.

**Original PR Comment:**
{comment['body']}

**File:** {comment['file']}:{comment['line']}
**Reviewer:** {comment['user']}
**Fix Confidence:** {fix_confidence}

**Automated Fix Applied:**
{fix_description}

**Review Focus:**
1. Does fix correctly address the PR comment?
2. Are there any side effects or new issues introduced?
3. Does fix follow established patterns (retries, logging, error handling)?
4. Is code quality maintained (type hints, docstrings)?

**Deliverable:**
- Approval status: APPROVED / NEEDS REVISION
- Blocking/Major/Minor issues (if any)
- Continuation ID for codex synthesis
""",
    absolute_file_paths=[comment['file']]
)

# Phase 2: Codex synthesis
codex_review = mcp__zen-mcp__clink(
    cli_name="codex",
    role="codereviewer",
    prompt=f"""Synthesize gemini review for automated PR fix.

**Gemini Review Summary:**
{gemini_review}

**Task:** Validate gemini findings and provide final recommendation.

**Deliverable:**
- Final approval status: APPROVED / NEEDS REVISION
- Actionable next steps (if revision needed)
""",
    absolute_file_paths=[comment['file']],
    continuation_id=extract_continuation_id(gemini_review)
)

# CRITICAL: BOTH reviewers must approve
gemini_status = parse_review_status(gemini_review)
codex_status = parse_review_status(codex_review)

if gemini_status == "APPROVED" and codex_status == "APPROVED":
    print("✅ Auto-fix approved by BOTH gemini AND codex")
    # Proceed with commit
else:
    # Extract ALL issues from BOTH reviewers
    all_issues = extract_issues(gemini_review) + extract_issues(codex_review)
    escalate(
        reason="Auto-fix failed two-phase review",
        comment=comment,
        fix=fix_description,
        gemini_status=gemini_status,
        codex_status=codex_status,
        issues=all_issues
    )
```

---

## Full Automation Loop

```python
def automated_pr_fix_cycle(pr_number, max_iterations=10, timeout_hours=2):
    """
    Automated PR fix cycle.

    Args:
        pr_number: GitHub PR number
        max_iterations: Max fix iterations before escalation
        timeout_hours: Max runtime before escalation

    Returns:
        PRFixResult with status and metrics
    """
    start_time = time.time()
    iteration = 0

    while iteration < max_iterations:
        elapsed = time.time() - start_time
        if elapsed > timeout_hours * 3600:
            escalate(reason=f"Timeout: {timeout_hours} hours exceeded")
            return PRFixResult(status="escalated", reason="timeout")

        # Step 1: Check PR status
        pr_status = get_pr_status(pr_number)

        if pr_status["reviewDecision"] == "APPROVED":
            print("✅ PR approved - automation complete")
            return PRFixResult(
                status="approved",
                iterations=iteration,
                elapsed_time_seconds=elapsed
            )

        # Step 2: Fetch new comments
        new_comments = fetch_new_comments(pr_number, since_iteration=iteration)

        # Step 3: Categorize comments
        categorized = categorize_comments(new_comments)

        # Step 4: Auto-fix actionable comments
        fix_results = []
        for comment in categorized["actionable"]:
            fix_result = auto_fix_pr_comment(comment)
            fix_results.append(fix_result)

            if fix_result["status"] == "escalated":
                escalate(
                    reason=f"Comment requires human judgment",
                    comment=comment
                )
                return PRFixResult(status="escalated", reason="low_confidence_fix")

        # Step 5: Check CI failures
        ci_status = check_ci_status(pr_number)

        if ci_status == "FAILURE":
            ci_fix_result = auto_fix_ci_failures(pr_number)

            if ci_fix_result["status"] == "escalated":
                escalate(
                    reason="CI failures persist after auto-fix",
                    failures=ci_fix_result["failures"]
                )
                return PRFixResult(status="escalated", reason="ci_failures")

        # Step 6: Commit and push fixes
        if fix_results:
            commit_pr_fixes(fix_results)
            push_to_remote()

        # Step 7: Wait before next iteration
        print(f"💤 Sleeping 5 minutes before next check (iteration {iteration + 1}/{max_iterations})")
        time.sleep(300)  # 5 minutes

        iteration += 1

    escalate(reason=f"Max iterations ({max_iterations}) reached")
    return PRFixResult(status="escalated", reason="max_iterations")
```

---

## Escalation Scenarios

### 1. Low Confidence Comment

```json
{
  "type": "pr_comment_escalation",
  "pr_number": 123,
  "comment": {
    "id": 12345678,
    "file": "apps/execution_gateway/order_placer.py",
    "line": 142,
    "body": "This approach might cause race conditions in high-frequency scenarios",
    "severity": "high",
    "confidence": "LOW"
  },
  "reason": "Architectural concern requires human analysis",
  "action": "PAUSE_AUTOMATION"
}
```

### 2. Persistent CI Failures

```json
{
  "type": "ci_failure_escalation",
  "pr_number": 123,
  "failures": [
    {
      "test": "test_position_limit_validation",
      "error": "AssertionError: Expected RiskViolation not raised",
      "attempts": 3
    }
  ],
  "reason": "Test logic errors persist after 3 auto-fix attempts",
  "action": "PAUSE_AUTOMATION"
}
```

### 3. Timeout

```json
{
  "type": "timeout_escalation",
  "pr_number": 123,
  "elapsed_time_hours": 2,
  "reason": "Automation exceeded 2-hour runtime limit",
  "action": "PAUSE_AUTOMATION"
}
```

---

## Success Criteria

Automated PR fix cycle succeeds when:

- [  ] Comment polling implemented (gh API)
- [  ] Comment categorization functional (actionable vs. non-actionable)
- [  ] Auto-fix for HIGH confidence comments (docstrings, imports, types)
- [  ] Auto-fix for MEDIUM confidence comments (logging, patterns)
- [  ] CI failure detection and auto-fix (type/lint only)
- [  ] Standardized commit format
- [  ] Escalation logic functional (low confidence, persistent failures, timeout)
- [  ] Time savings measured (50-75% per iteration)

---

## Related Workflows

- [18-automated-coding.md](./18-automated-coding.md) — Per-component automation
- [02-git-pr.md](./02-git-pr.md) — Manual PR creation
- [10-ci-triage.md](./10-ci-triage.md) — Manual CI failure resolution

---

## References

- `docs/TASKS/P1T13_F3_AUTOMATION.md` — Task document
- `.claude/research/automated-coding-research.md` — Auto-fix strategies

---

**Next Step:** Integrate into full automation workflow (20-full-automation.md)
