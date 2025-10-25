# Example: Handling Review Feedback Loop

Example showing how to handle multiple rounds of review feedback systematically.

## Scenario

PR created, Codex finds multiple issues requiring fixes and re-review.

## Initial Review Feedback

**Codex review findings:**
- **HIGH:** Missing null check in position calculation
- **MEDIUM:** Add logging for limit violations
- **LOW:** Variable naming could be clearer

## Handling Feedback

```bash
# PR created, Codex finds issues

# Fix HIGH and MEDIUM immediately
# (Edit code to add null check and logging)
$ git add apps/execution_gateway/order_placer.py
$ git commit -m "Add null check and logging per Codex review

- Add null check for current_position before calculation (HIGH)
- Add structured logging for all limit violations (MEDIUM)
- Helps prevent NoneType errors and improves observability

Zen-mcp verified fixes before commit ✅"

$ git push

# Request re-review
$ gh pr comment 26 --body "Fixed the null check and added logging.

**Changes:**
- Added null check before position calculation
- Added logging with trade context for all violations

@codex please review latest commit to verify fixes."

# Codex responds: "Fixes look good, but now notice edge case in error handling"

# Fix new issue found during re-review
# (Edit code to handle edge case)
$ git add apps/execution_gateway/order_placer.py
$ git commit -m "Improve error handling for edge case per Codex

- Handle case where limit is None (configuration error)
- Raise ConfigurationError instead of allowing None comparison
- Add test for misconfigured limits

Zen-mcp verified fix ✅"

$ git push

$ gh pr comment 26 --body "@codex verified edge case fix, please approve if no further issues"

# Codex: "All issues resolved ✅"
# Gemini: "No issues ✅"

# Merge!
$ gh pr merge 26 --merge
```

## Key Patterns

### 1. Fix Multiple Issues in One Commit
When Codex finds several issues, fix them all together:
- ✅ One commit with all HIGH/MEDIUM fixes
- ❌ Not separate commits for each issue (creates noise)

### 2. Request Specific Re-Review
Be explicit about what you fixed:
```bash
gh pr comment 26 --body "Fixed issues X, Y, Z.
@codex please verify these specific changes."
```

### 3. Expect Iteration
First review may find A, B, C
Re-review may find D (only visible after A, B, C fixed)
This is NORMAL and GOOD - prevents bugs

### 4. Document Deferred Issues
If LOW issue deferred:
```bash
gh pr comment 26 --body "Deferring LOW issue (variable naming) to P1T10.
Approved by @user, will clean up in next refactoring pass."
```

## Timeline for This Example

- **Initial PR:** Created at 10:00 AM
- **Codex review:** Complete at 10:45 AM (3 issues found)
- **Fix HIGH/MEDIUM:** 10:45-11:00 AM (15 min)
- **Push + comment:** 11:00 AM
- **Codex re-review:** Complete at 11:30 AM (1 new edge case found)
- **Fix edge case:** 11:30-11:45 AM (15 min)
- **Push + comment:** 11:45 AM
- **Final approval:** 12:00 PM
- **Merge:** 12:05 PM

**Total:** ~2 hours elapsed, ~30 min active work

## What Went Well

✅ Fixed HIGH/MEDIUM immediately (didn't defer)
✅ Zen-mcp reviewed fixes before committing (prevented new bugs)
✅ Explicit re-review requests (faster turnaround)
✅ Iteration expected (edge case found in round 2 is normal)
✅ Clear commit messages (explained what and why)

## What to Avoid

❌ Committing without zen review of fixes
❌ Fixing issues piecemeal (multiple commits for same review round)
❌ Pushing without re-requesting review
❌ Getting frustrated by iteration (it's finding real bugs!)
