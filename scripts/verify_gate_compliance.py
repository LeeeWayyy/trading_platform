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

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from libs.common.hash_utils import is_merge_commit


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

    Accepts two formats:

    1. NEW: Shared-context iteration format (single continuation ID):
       - continuation-id: <uuid-from-approved-iteration>

    2. LEGACY: Independent review format (dual continuation IDs):
       - gemini-continuation-id: <uuid> (or gemini-review:)
       - codex-continuation-id: <uuid> (or codex-review:)

    Both formats require:
    - zen-mcp-review: approved

    Note: Review-Hash requirement was removed - the hook infrastructure for
    generating Review-Hash trailers was never completed.
    """
    message = get_commit_message(commit_hash).lower()

    # Check for approval marker (required)
    has_approval = "zen-mcp-review: approved" in message
    if not has_approval:
        return False

    # Check for continuation ID formats
    # Accept EITHER new shared format OR legacy dual-phase format

    # NEW format: Single shared continuation-id (shared-context iterations)
    # Pattern matches "continuation-id:" but NOT "gemini-continuation-id:" or "codex-continuation-id:"
    # Uses negative lookbehind to avoid matching legacy keys that contain "continuation-id" as substring
    shared_id_pattern = r"(?:^|\n)\s*(?<![a-z-])continuation-id:\s*[a-f0-9-]+"
    has_shared_id = bool(re.search(shared_id_pattern, message))

    # LEGACY format: Dual-phase (gemini + codex) continuation IDs
    gemini_trailer_pattern = r"(?:^|\n)\s*(?:gemini-continuation-id|gemini-review):"
    codex_trailer_pattern = r"(?:^|\n)\s*(?:codex-continuation-id|codex-review):"
    has_gemini = bool(re.search(gemini_trailer_pattern, message))
    has_codex = bool(re.search(codex_trailer_pattern, message))
    has_dual_phase = has_gemini and has_codex

    # Accept either new shared format OR legacy dual-phase format
    return has_shared_id or has_dual_phase


def main():
    """Main verification logic."""
    pr_commits = get_pr_commits()

    if not pr_commits:
        print("✅ No commits to verify (empty PR or single-commit branch)")
        return 0

    # Detect CI environment (GitHub Actions, GitLab CI, etc.)
    is_ci = os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"

    # Track skipped merges for later use
    skipped_merges = []

    # Identify GitHub auto-merge commits to skip
    if is_ci:
        for commit_hash in pr_commits:
            if is_merge_commit(commit_hash):
                # Get committer email to verify GitHub web-flow
                try:
                    committer_email_result = subprocess.run(
                        ["git", "log", "-1", "--format=%ce", commit_hash],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    committer_email = committer_email_result.stdout.strip()
                except subprocess.CalledProcessError:
                    committer_email = ""

                # GitHub merge commits use specific committer emails
                is_github_merge = committer_email in (
                    "web-flow@users.noreply.github.com",
                    "noreply@github.com",
                )

                if is_github_merge:
                    skipped_merges.append(commit_hash)
                    print(f"  ⏭️  Skipping GitHub auto-merge commit {commit_hash[:8]}")

    # PR #61 Fix (Codex HIGH): Always verify markers in CI (defense-in-depth)
    # Note: Review-Hash validation removed - hook infrastructure was never completed
    if is_ci:
        print("ℹ️  Verifying review approval markers...")
        print("   (Checking for zen-mcp-review approval + continuation IDs)")
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
            print("❌ REVIEW MARKER VALIDATION FAILED!")
            print(f"   Found {len(missing_markers)} commit(s) missing approval markers")
            print()
            print("   This could indicate:")
            print("   - Commit was made with --no-verify (bypassed pre-commit hook)")
            print("   - Review markers were manually removed")
            print("   - Review process was bypassed")
            print()
            print("   All commits must have approval markers (one of these formats):")
            print()
            print("   NEW format (shared-context iterations):")
            print("   - zen-mcp-review: approved")
            print("   - continuation-id: <uuid>")
            print()
            print("   LEGACY format (independent reviews):")
            print("   - zen-mcp-review: approved")
            print("   - gemini-continuation-id: <uuid>")
            print("   - codex-continuation-id: <uuid>")
            print()
            print("   Commits missing markers:")
            for commit_hash in missing_markers:
                print(f"     - {commit_hash[:8]}")
            print()
            return 1

        print(f"✅ All {len(pr_commits) - len(skipped_merges)} commit(s) have approval markers")
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
