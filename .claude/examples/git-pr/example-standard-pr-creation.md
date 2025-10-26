# Example: Standard PR Creation

Complete example showing the full PR workflow from feature completion to merge.

## Scenario

Feature complete after 6 progressive commits implementing position limit validation (P0T5).

## Step-by-Step

```bash
# Feature complete after 6 progressive commits

# 1. Deep zen review
"Deep review all branch changes with zen-mcp"
# ✅ Approved - 1 MEDIUM issue found and fixed

# 2. Run CI checks locally
$ make ci-local
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1/3: Type checking with mypy --strict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Success: no issues found in 95 source files
Step 2/3: Linting with ruff
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All checks passed!
Step 3/3: Running tests
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
======== 1015 passed, 15 skipped, 84 deselected in 53.04s =========
✓ All CI checks passed!

# 3. Push
$ git push

# 4. Create PR
$ gh pr create --title "Add position limit validation (P0T5)"
# (Enter description using template)

Creating pull request for feature/position-limits into master in LeeeWayyy/trading_platform

https://github.com/LeeeWayyy/trading_platform/pull/26

# 5. Automated reviews requested automatically

# 6. Wait for feedback...

# 7. Address feedback
# (Fix issues found)
$ git add <files>
$ git commit -m "Address review feedback"
$ git push

$ gh pr comment 26 --body "@gemini-code-assist @codex updated to address your feedback, please verify"

# 8. Reviewers approve
# Codex: "All issues resolved, approved ✅"
# Gemini: "No issues found ✅"

# 9. Merge
$ gh pr merge 26 --merge
✓ Merged Pull Request #26 (Add position limit validation)
✓ Deleted branch feature/position-limits

# Done!
```

## Key Points

- **Deep review BEFORE PR** - Caught 1 issue early
- **make ci-local** - Verified all checks pass locally
- **Template description** - Complete context for reviewers
- **Automated reviews** - Triggered automatically on PR creation
- **Single feedback cycle** - Fixed issues, got approval, merged
- **Preserve commits** - Used `--merge` to keep progressive history

## Timeline

- **Deep review:** ~5 minutes
- **Local CI:** ~2 minutes
- **PR creation:** ~3 minutes
- **Review feedback:** ~1 hour (waiting for automated reviews)
- **Fix issues:** ~15 minutes
- **Re-review:** ~30 minutes (automated)
- **Merge:** ~1 minute

**Total active time:** ~25 minutes
**Total elapsed time:** ~2 hours (including wait times)
