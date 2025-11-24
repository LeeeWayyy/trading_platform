#!/usr/bin/env python3
"""
Verify that all commits in PR followed workflow gates.

Detects commits made with --no-verify by checking if commit hashes
match those recorded in .claude/workflow-state.json.

This script runs in CI to catch workflow gate bypasses.

Exit codes:
  0 - All commits compliant
  1 - Non-compliant commits detected (used --no-verify)

Author: Claude Code
Date: 2025-11-02
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from libs.common.hash_utils import compute_git_diff_hash, is_merge_commit


def get_pr_commits_from_github():
    """Get commits from GitHub PR API (most reliable in CI)."""
    # Check if we're in a PR context
    # Note: GITHUB_PR_NUMBER and PR_NUMBER are optional convenience variables
    # that may be set in custom workflow configurations. Standard GitHub Actions
    # workflows should rely on GITHUB_REF parsing below.
    pr_number = os.getenv("GITHUB_PR_NUMBER") or os.getenv("PR_NUMBER")

    if not pr_number:
        # Try to extract from GITHUB_REF (format: refs/pull/123/merge)
        github_ref = os.getenv("GITHUB_REF", "")
        if "/pull/" in github_ref:
            pr_number = github_ref.split("/pull/")[1].split("/")[0]

    if not pr_number:
        return None  # Not in PR context

    try:
        # Use GitHub CLI to get actual PR commits (ground truth)
        result = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "commits", "--jq", ".commits[].oid"],
            capture_output=True,
            text=True,
            check=True,
        )
        commits = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]
        return commits
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None  # gh CLI not available or command failed


def get_pr_commits():
    """Get list of commit hashes in current PR/branch."""
    # Try GitHub API first (most reliable in CI)
    github_commits = get_pr_commits_from_github()
    if github_commits:
        return github_commits

    # Fallback to git (for local development or non-PR contexts)
    # Use environment variable for dynamic base branch detection
    # Falls back to master if not in CI environment
    base_branch = os.getenv("GITHUB_BASE_REF", "master")
    base_ref = f"origin/{base_branch}"

    # Get commits between base branch and HEAD
    # Component A2.1 (P1T13-F5): Include merge commits for Review-Hash validation
    # Previously used --no-merges, but we need to validate ALL commits including merges
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H", f"{base_ref}..HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        commits = result.stdout.strip().split("\n")
        return [c for c in commits if c]  # Filter out empty strings
    except subprocess.CalledProcessError as e:
        print(f"Error getting PR commits (base: {base_ref}): {e}")
        return []


def load_workflow_state():
    """Load .claude/workflow-state.json if it exists.

    Returns None if:
    - File doesn't exist (acceptable for doc-only changes)
    - File is malformed (treated as error, falls back to marker verification)

    Note: When None is returned due to malformed JSON, the caller should
    proceed with commit message marker verification as a fallback.
    """
    state_file = Path(".claude/workflow-state.json")
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print("⚠️  Warning: Malformed workflow state file (.claude/workflow-state.json)")
        print(f"   JSON parse error: {e}")
        print("   Falling back to commit message marker verification")
        print()
        return None


def get_commit_message(commit_hash):
    """Get full commit message for a given commit hash."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B", commit_hash],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def has_review_markers(commit_hash):
    """Check if commit message contains zen-mcp review approval markers.

    All reviews now use comprehensive independent review format:
    - gemini-continuation-id: <final-uuid-from-approved-iteration>
    - codex-continuation-id: <final-uuid-from-approved-iteration>

    Also accepts legacy marker names:
    - gemini-review: (alias for gemini-continuation-id:)
    - codex-review: (alias for codex-continuation-id:)
    - continuation-id: (legacy single-phase format)

    Component 2 (P1T13-F5a): Also checks for Review-Hash trailer.
    """
    message = get_commit_message(commit_hash).lower()

    # Check for approval marker (required)
    has_approval = "zen-mcp-review: approved" in message
    if not has_approval:
        return False

    # Check for continuation ID formats
    # ONLY accept dual-phase (gemini + codex) format - no legacy format
    # This enforces the new comprehensive independent review policy
    gemini_pattern = r"(?:^|\n)\s*gemini-continuation-id:"
    codex_pattern = r"(?:^|\n)\s*codex-continuation-id:"

    # Use regex patterns to avoid false positives
    gemini_trailer_pattern = r"(?:^|\n)\s*(?:gemini-continuation-id|gemini-review):"
    codex_trailer_pattern = r"(?:^|\n)\s*(?:codex-continuation-id|codex-review):"
    has_gemini = bool(re.search(gemini_trailer_pattern, message))
    has_codex = bool(re.search(codex_trailer_pattern, message))
    has_dual_phase = has_gemini and has_codex

    # Component 2 (P1T13-F5a): Check for Review-Hash trailer (presence only)
    # Note: We only check presence, not correctness (can't reconstruct staging area post-commit)
    # Must match exactly 64-char hex hash OR empty string (for empty commits)
    # Use same pattern as extract_review_hash() for consistency
    # Pattern allows trailers after Review-Hash (e.g., Co-authored-by, Signed-off-by)
    review_hash_pattern = r"(?:^|\n)\s*review-hash:\s*([0-9a-f]{64}|)\s*(?:\n|$)"
    has_review_hash = bool(re.search(review_hash_pattern, message, re.IGNORECASE | re.MULTILINE))

    # Require BOTH dual-phase review markers AND review hash
    # No backward compatibility - all commits must have independent gemini + codex reviews
    return has_dual_phase and has_review_hash


def extract_review_hash(commit_sha: str) -> str | None:
    """
    Extract Review-Hash trailer from commit message.

    Component A2.1 (P1T13-F5): Server-side hash validation.

    Args:
        commit_sha: Git commit SHA

    Returns:
        Hash value if found, None otherwise

    Example:
        >>> extract_review_hash("abc123")
        'a1b2c3d4e5f6...'
    """
    message = get_commit_message(commit_sha)

    # Match Review-Hash: <hash_value>
    # Case-insensitive, allows whitespace
    # Allow 64-char hex hash OR empty string (for empty commits)
    # Pattern allows trailers after Review-Hash (e.g., Co-authored-by, Signed-off-by)
    pattern = r"(?:^|\n)\s*review-hash:\s*([0-9a-f]{64}|)\s*(?:\n|$)"
    match = re.search(pattern, message, re.IGNORECASE | re.MULTILINE)

    if match:
        hash_value = match.group(1).strip().lower()
        return hash_value  # Returns hash string or empty string ""
    return None


def validate_review_hash(commit_sha: str) -> bool:
    """
    Validate Review-Hash trailer against actual commit changes.

    Component A2.1 (P1T13-F5): Server-side validation with merge support.
    This ensures commits can't bypass Review-Hash requirement via --no-verify.

    Handles:
    - Regular commits: Hash of git show output
    - Merge commits: Hash of merge result (diff against first parent)
    - Empty commits: Hash of empty string
    - Initial commits: Exempt (no parent)

    Args:
        commit_sha: Git commit SHA to validate

    Returns:
        True if Review-Hash is valid or commit is exempt
        False if Review-Hash is missing or mismatched
    """
    # Check for initial commit (no parents)
    try:
        parents_result = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", commit_sha],
            capture_output=True,
            text=True,
            check=True,
        )
        parents = parents_result.stdout.strip().split()

        if len(parents) == 1:
            # Initial commit - exempt
            print(f"  ℹ️  Skipping initial commit {commit_sha[:8]} (no parent)")
            return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error checking commit parents for {commit_sha[:8]}: {e}")
        return False

    # Determine if merge commit
    try:
        merge = is_merge_commit(commit_sha)
        commit_type = "merge" if merge else "regular"
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error detecting merge status for {commit_sha[:8]}: {e}")
        return False

    # Compute actual hash from commit FIRST
    try:
        actual_hash = compute_git_diff_hash(commit_sha=commit_sha, is_merge=merge)
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Error computing hash for {commit_sha[:8]}: {e}")
        return False

    # Extract claimed hash from commit message
    claimed_hash = extract_review_hash(commit_sha)

    # Review-Hash trailer is REQUIRED for all commits (even empty ones)
    # For empty commits, the hash value itself can be empty, but trailer must exist
    if claimed_hash is None:
        print(f"  ❌ Missing Review-Hash trailer in {commit_type} commit {commit_sha[:8]}")
        print("     All commits must include 'Review-Hash:' trailer (even empty commits)")
        return False

    # Handle empty commits - require empty hash value
    if actual_hash == "":
        if claimed_hash == "":
            print(f"  ✅ Empty {commit_type} commit {commit_sha[:8]} (correct empty hash)")
            return True
        else:
            print(f"  ❌ Empty {commit_type} commit but hash mismatch")
            print(f"     Claimed: {claimed_hash[:16]}...")
            print("     Expected: (empty)")
            return False

    # For non-empty commits, hash value must not be empty
    if claimed_hash == "":
        print(f"  ❌ Empty Review-Hash in non-empty {commit_type} commit {commit_sha[:8]}")
        return False

    # Validate hash
    if claimed_hash != actual_hash:
        print(f"  ❌ HASH MISMATCH in {commit_type} commit {commit_sha[:8]}")
        print(f"     Claimed: {claimed_hash[:16]}...")
        print(f"     Actual:  {actual_hash[:16]}...")
        if merge:
            print("     Note: Merge validated with diff against first parent")
        return False

    # Success
    if merge:
        print(f"  ✅ Valid Review-Hash in merge commit {commit_sha[:8]} (merge result verified)")
    else:
        print(f"  ✅ Valid Review-Hash in commit {commit_sha[:8]}")
    return True


def main():
    """Main verification logic."""
    pr_commits = get_pr_commits()

    if not pr_commits:
        print("✅ No commits to verify (empty PR or single-commit branch)")
        return 0

    # Detect CI environment (GitHub Actions, GitLab CI, etc.)
    is_ci = os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"

    # Component A2.1 (P1T13-F5) - Server-side Review-Hash validation
    # CRITICAL: This runs FIRST, before any early returns
    # Validate Review-Hash correctness (not just presence)
    # Supports merge commits via first-parent diff strategy
    if is_ci:
        print("ℹ️  Validating Review-Hash correctness in CI...")
        print("   (Component A2.1: Server-side hash validation with merge support)")
        print()

        invalid_hashes = []
        skipped_merges = []
        for commit_hash in pr_commits:
            # Skip ONLY GitHub auto-generated merge commits (not developer merges)
            # Developer merge commits must have Review-Hash like any other commit
            if is_merge_commit(commit_hash):
                message = get_commit_message(commit_hash)

                # Get committer email to verify GitHub web-flow (robust detection)
                try:
                    committer_email_result = subprocess.run(
                        ["git", "log", "-1", "--format=%ce", commit_hash],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    committer_email = committer_email_result.stdout.strip()
                except subprocess.CalledProcessError:
                    committer_email = ""  # Could not get email, proceed to validation

                # GitHub merge commits use different committer emails:
                # - web-flow@users.noreply.github.com (UI merges)
                # - noreply@github.com (PR testing merge commits)
                # Trust these emails for GitHub-generated merges (allows custom merge messages)
                # Note: Email spoofing is theoretically possible but requires local commit +
                # force push, which is immediately visible in PR history and caught by branch protection
                is_github_merge = committer_email in (
                    "web-flow@users.noreply.github.com",
                    "noreply@github.com",
                )

                if is_github_merge:
                    skipped_merges.append(commit_hash)
                    print(f"  ⏭️  Skipping GitHub auto-merge commit {commit_hash[:8]}")
                    continue

            if not validate_review_hash(commit_hash):
                invalid_hashes.append(commit_hash)

        if invalid_hashes:
            print()
            print("❌ REVIEW-HASH VALIDATION FAILED!")
            print(f"   Found {len(invalid_hashes)} commit(s) with invalid Review-Hash")
            print()
            print("   Possible causes:")
            print("   - Commit made with --no-verify (bypassed pre-commit hook)")
            print("   - Post-review tampering (amended commit after review)")
            print("   - Manual commit message editing")
            print()
            print("   All commits must have valid Review-Hash trailer:")
            print("   Review-Hash: <sha256_hash_of_changes>")
            print()
            print("   See Component A2.1 (P1T13-F5) for details")
            return 1

        print()
        validated_count = len(pr_commits) - len(skipped_merges)
        print(f"✅ All {validated_count} commit(s) have valid Review-Hash")
        if skipped_merges:
            print(f"   ({len(skipped_merges)} GitHub merge commit(s) skipped)")
        print()

    # PR #61 Fix (Codex HIGH): Always verify markers in CI (defense-in-depth)
    # This check must run REGARDLESS of workflow state to prevent bypasses
    if is_ci:
        print("ℹ️  Verifying review approval markers (in addition to Review-Hash)...")
        print("   (Defense in depth: hash + markers required)")
        print()

        missing_markers = []
        for commit_hash in pr_commits:
            # Skip GitHub auto-generated merge commits (already checked above)
            if commit_hash in skipped_merges:
                continue

            if not has_review_markers(commit_hash):
                missing_markers.append(commit_hash)

        if missing_markers:
            print()
            print("❌ MARKER VALIDATION FAILED!")
            print(
                f"   Found {len(missing_markers)} commit(s) with valid Review-Hash but NO approval markers"
            )
            print()
            print("   This could indicate:")
            print("   - Review markers were manually removed")
            print("   - Commit was made with --no-verify and hash forged locally")
            print("   - Review process was bypassed")
            print()
            print("   All commits must have BOTH:")
            print("   1. Valid Review-Hash trailer (cryptographic proof)")
            print("   2. Approval markers (zen-mcp-review: approved + continuation-id)")
            print()
            print("   Commits missing markers:")
            for commit_hash in missing_markers:
                print(f"     - {commit_hash[:8]}")
            print()
            print("   See PR #61 (Codex P1 finding) for rationale")
            return 1

        print(f"✅ All {len(pr_commits) - len(skipped_merges)} commit(s) have approval markers")
        print("   (BOTH Review-Hash and markers verified)")
        print()

    # Load workflow state (may be missing in CI, that's OK after marker check passed)
    state = load_workflow_state()

    if not state:
        # Locally: Allow for documentation-only changes
        # In CI: Already verified markers above, state is optional
        print("ℹ️  No workflow state file found")
        if is_ci:
            print("   Acceptable after marker verification passed")
        else:
            print("   Acceptable for documentation-only changes or initial setup")
        return 0

    # Get commit history from state
    commit_history = state.get("commit_history", [])

    # Fallback to last_commit_hash for backward compatibility
    if not commit_history:
        recorded_hash = state.get("last_commit_hash")
        if recorded_hash:
            commit_history = [recorded_hash]

    # PR #61 Fix (Codex MEDIUM): Treat empty commit_history as failure in CI
    if not commit_history:
        if is_ci:
            print("❌ COMMIT HISTORY VALIDATION FAILED!")
            print("   Workflow state exists but commit_history is empty")
            print()
            print("   This could indicate:")
            print("   - Workflow state was manually truncated")
            print("   - State file corruption")
            print("   - Attempt to bypass commit tracking")
            print()
            print("   In CI, all commits must be tracked in workflow state")
            print("   See PR #61 (Codex MEDIUM finding) for rationale")
            return 1
        else:
            print("⚠️  Warning: No commit history found in workflow state")
            print("   This may be a first commit or workflow state not initialized")
            return 0

    # Validate EVERY commit in the PR against commit_history
    non_compliant_commits = []
    for commit_hash in pr_commits:
        if commit_hash not in commit_history:
            non_compliant_commits.append(commit_hash)

    if non_compliant_commits:
        print("❌ GATE BYPASS DETECTED!")
        print(f"   Found {len(non_compliant_commits)} non-compliant commit(s):")
        for commit in non_compliant_commits:
            print(f"     - {commit[:8]}")
        print()
        print("   These commits were likely made with --no-verify")
        print("   All commits must pass workflow gates:")
        print("   - Zen-MCP review (clink + gemini → codex)")
        print("   - CI passing (make ci-local)")
        print()
        print("   Review required: Verify all commits followed the 4-step pattern")
        return 1

    print(f"✅ All {len(pr_commits)} commit(s) compliant with workflow gates")
    print(f"   Verified against {len(commit_history)} recorded commit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
