# CI Configuration Validation

This commit changes CI configuration files only (.github/workflows/, .github/markdown-link-check-config.json).

## Manual Testing Required

1. **Branch Protection Check**: Verify GH_TOKEN is passed correctly
   - Run workflow on GitHub Actions
   - Check step "Verify branch protection configuration" succeeds
   - Confirm administration:read permission allows API access

2. **Link Check**: Verify example URL is ignored
   - Run markdown-link-check locally or in CI
   - Confirm https://console.trading-platform.example.com is skipped
   - No false positives in link-check results

3. **Permission Scoping**: Verify administration:read is job-scoped
   - Check integration-tests job does NOT have administration:read
   - Confirm only test-and-coverage job has the permission

## Verification Commands

```bash
# Test link-check configuration locally
npx markdown-link-check docs/RUNBOOKS/web-console-user-guide.md -c .github/markdown-link-check-config.json

# Verify workflow syntax
gh workflow view "CI - Tests & Coverage"
```

## Review Approval

- ✅ Gemini Review: APPROVED (suggested administration:read permission)
- ✅ Codex Review: APPROVED (suggested job-scoped permissions)
- ✅ All review feedback addressed
