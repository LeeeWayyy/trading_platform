# Branch Protection Standard

**Purpose:** Ensure all code merged into `master` has passed critical quality and workflow gates, specifically the `Review-Hash` validation that prevents bypassing pre-commit workflow enforcement.

**Owner:** DevOps / Repository Administrators

**Last Updated:** 2025-11-16

---

## Overview

This repository uses GitHub branch protection rules to enforce that all Pull Requests pass required CI checks before merging. The most critical check is the **Review-Hash validation** (`Run tests and check coverage` job), which verifies that all commits:

1. Followed the mandatory 6-step workflow (plan → plan-review → implement → test → review → commit)
2. Have valid `Review-Hash` trailers proving no post-review tampering
3. Cannot bypass pre-commit hooks with `--no-verify`

**Without branch protection, developers could bypass the pre-commit hook and merge unreviewed code.**

---

## Required Branch Protection Rules

### Target Branch: `master`

The following settings **MUST** be enabled for the `master` branch:

### 1. Require Pull Request Before Merging

- ✅ **Enabled:** All changes must go through pull requests
- **Rationale:** Prevents direct pushes to master that could bypass CI

### 2. Require Status Checks to Pass Before Merging

- ✅ **Enabled:** Pull requests must pass CI before merging
- **Required Checks:**
  - ✅ **`Run tests and check coverage`** (CRITICAL)
    - This is the job name from `.github/workflows/ci-tests-coverage.yml:35`
    - Runs `scripts/verify_gate_compliance.py` which validates Review-Hash
    - **This is the enforcement mechanism for the entire workflow system**

### 3. Require Branches to be Up to Date Before Merging

- ✅ **Enabled (Recommended):** Ensures tests run against latest master
- **Rationale:** Prevents merge conflicts and ensures clean CI on final merge commit

### 4. Optional but Recommended Settings

- ☑️ **Require conversation resolution before merging:** Ensures all review comments are addressed
- ☑️ **Require signed commits:** Additional security layer
- ☑️ **Include administrators:** Admins must also follow the rules (recommended)
- ☑️ **Restrict who can push to matching branches:** Limit to CI/CD accounts only

---

## Setup Instructions

**Prerequisites:**
- Repository admin access
- Understanding of GitHub branch protection rules

### Step-by-Step Configuration

1. **Navigate to Branch Protection Settings**
   - Go to repository: https://github.com/LeeeWayyy/trading_platform
   - Click **Settings** → **Branches** → **Branch protection rules**

2. **Add Protection Rule**
   - Click **Add rule** (or edit existing rule for `master`)
   - Set **Branch name pattern:** `master`

3. **Enable Pull Request Requirement**
   - ✅ Check **Require a pull request before merging**
   - Optionally set **Required approving reviews:** 1 (or more)

4. **Enable Status Check Requirements**
   - ✅ Check **Require status checks to pass before merging**
   - ✅ Check **Require branches to be up to date before merging**

5. **Select Required Status Checks**
   - In the **"Search for status checks"** box, type: `Run tests`
   - From the dropdown, select: **`Run tests and check coverage`**
   - **CRITICAL:** This exact job name must match `.github/workflows/ci-tests-coverage.yml:35`
   - The list will auto-populate after the first PR runs the workflow

6. **Optional Additional Settings**
   - ✅ **Require conversation resolution before merging** (recommended)
   - ☑️ **Include administrators** (recommended for consistency)

7. **Save Changes**
   - Scroll to bottom and click **Save changes**

---

## Verification

### How to Verify the Rule is Active

1. **Check Settings Page:**
   - Go to **Settings** → **Branches**
   - You should see a rule for `master` with a green checkmark

2. **Test with a Pull Request:**
   - Create a test PR to `master`
   - In the PR page, scroll to the "Merge pull request" button
   - You should see:
     ```
     Merging is blocked
     Required status check "Run tests and check coverage" has not succeeded
     ```
   - The check should show as **Required** (not just informational)

3. **Use the Verification Script:**
   ```bash
   # Run locally or check CI output
   python scripts/verify_branch_protection.py
   ```
   - Expected output: `✅ Branch protection correctly configured`

### Visual Indicators

When correctly configured, a PR will show:
- **Status checks section:** Lists "Run tests and check coverage" with a **Required** label
- **Merge button disabled** until all required checks pass
- **Red X or yellow circle** next to the check name if it's running or failed

---

## Troubleshooting

### Problem: "Run tests and check coverage" not appearing in status check list

**Cause:** GitHub only shows status checks that have run at least once.

**Solution:**
1. Merge or run one PR through the CI workflow
2. Wait for the workflow to complete
3. Return to branch protection settings
4. The check will now appear in the autocomplete list

### Problem: Status check shows as optional, not required

**Cause:** The check name was not selected in the required checks list.

**Solution:**
1. Edit the branch protection rule
2. Search for "Run tests and check coverage"
3. Click to select it (should show a checkmark)
4. Save changes

### Problem: CI job name changed, breaking protection

**Cause:** The GitHub Actions job name in `ci-tests-coverage.yml` was changed.

**Solution:**
1. Update the branch protection rule to reference the new job name
2. Update this documentation
3. Update `scripts/verify_branch_protection.py` to check for the new name

**Prevention:** The `verify_branch_protection.py` script (run in CI) will alert if the job name changes.

### Problem: Protection rule exists but verify script fails

**Cause:** The verification script may be checking for the wrong job name or API response format changed.

**Solution:**
1. Manually check the branch protection settings
2. Run: `gh api repos/{owner}/{repo}/branches/master/protection | jq`
3. Compare the actual JSON structure with what the script expects
4. Update the script if GitHub's API changed

---

## Maintenance

### When to Update This Configuration

1. **CI Job Name Changes:**
   - If `.github/workflows/ci-tests-coverage.yml` job name changes
   - Update branch protection to reference new name
   - Update this documentation

2. **Adding New Required Checks:**
   - If new critical validations are added (e.g., security scans)
   - Add them to the required checks list
   - Document them in this file

3. **Repository Migration:**
   - If the repository is forked or migrated
   - Re-apply these branch protection rules
   - Verify with the verification script

### Automated Monitoring

The CI workflow includes a verification step that checks branch protection status:

```yaml
# .github/workflows/ci-tests-coverage.yml
- name: Verify Branch Protection Status
  run: python scripts/verify_branch_protection.py
  continue-on-error: true  # Non-blocking, informational only
```

This step will **warn** (but not fail the build) if branch protection is misconfigured. Check the CI logs regularly for warnings.

---

## Security Implications

### Why This Matters

Without branch protection:
- Developers could use `git commit --no-verify` to bypass workflow gates
- Unreviewed code could be merged directly to master
- The `Review-Hash` system would be bypassable
- The entire quality enforcement system would be voluntary, not mandatory

### Defense in Depth

This branch protection rule is one layer of a multi-layered security approach:

1. **Layer 1 (Developer):** Pre-commit hook blocks commits without review approval
2. **Layer 2 (CI):** `verify_gate_compliance.py` validates Review-Hash in all PR commits
3. **Layer 3 (GitHub):** Branch protection makes Layer 2 a **required** gate
4. **Layer 4 (Audit):** Git history preserves Review-Hash trailers for post-merge auditing

**If Layer 3 (branch protection) is disabled, the entire system degrades from mandatory to advisory.**

---

## References

- **GitHub Documentation:** [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/defining-the-mergeability-of-pull-requests/about-protected-branches)
- **Review-Hash Validation:** `scripts/verify_gate_compliance.py`
- **CI Workflow:** `.github/workflows/ci-tests-coverage.yml`
- **Verification Script:** `scripts/verify_branch_protection.py`
- **Workflow Documentation:** `.claude/workflows/01-git.md`

---

## FAQ

**Q: Can we temporarily disable branch protection for urgent hotfixes?**

A: **No.** The workflow gates exist precisely to prevent unreviewed code. For urgent fixes:
1. Create a feature branch
2. Implement the fix
3. Request expedited review via `./scripts/workflow_gate.py request-review commit`
4. Run `make ci-local` locally (2-3 minutes)
5. Merge the PR normally

The entire process takes <10 minutes with local CI. There is no valid reason to bypass it.

**Q: What if the CI check is flaky or has infrastructure issues?**

A: If the CI is genuinely broken (not a legitimate failure):
1. Fix the CI infrastructure first
2. Re-run the failed checks via GitHub UI ("Re-run failed jobs")
3. Do NOT disable branch protection as a workaround

**Q: Who can modify these branch protection rules?**

A: Only repository administrators. This is intentional. If you need changes:
1. Open an issue explaining the requirement
2. Get approval from the team
3. An admin will update the settings
4. Update this documentation to reflect the change

**Q: How do I verify the Review-Hash validation is actually running?**

A: Check any recent PR:
1. Go to the PR page
2. Click "Checks" tab
3. Expand "Run tests and check coverage"
4. Look for the "Verify workflow gate compliance" step
5. Check the logs for `✅ All X commit(s) have valid Review-Hash`

---

**Questions or issues? See `/docs/RUNBOOKS/ops.md` or open a GitHub issue.**
