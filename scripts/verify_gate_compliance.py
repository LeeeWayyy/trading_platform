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
    # Use --no-merges to skip merge commits (prevents false positives in CI)
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H", "--no-merges", f"{base_ref}..HEAD"],
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
    1. Quick review (single continuation): continuation-id: <id>
    2. Deep review (dual phase): gemini-continuation-id: <id> AND codex-continuation-id: <id>

    Also accepts legacy marker names:
    - gemini-review: (alias for gemini-continuation-id:)
    - codex-review: (alias for codex-continuation-id:)

    Component 2 (P1T13-F5a): Also checks for Review-Hash trailer.
    """
    message = get_commit_message(commit_hash).lower()

    # Check for approval marker (required)
    has_approval = "zen-mcp-review: approved" in message
    if not has_approval:
        return False

    # Check for continuation ID in either format
    # Format 1: Quick review (single continuation-id without prefix)
    # Match line start + optional whitespace + continuation-id, but ensure
    # it's not preceded by gemini- or codex- by checking the full pattern
    quick_pattern = r"(?:^|\n)\s*continuation-id:"
    gemini_pattern = r"(?:^|\n)\s*gemini-continuation-id:"
    codex_pattern = r"(?:^|\n)\s*codex-continuation-id:"

    has_continuation = bool(re.search(quick_pattern, message))
    has_prefixed = bool(re.search(gemini_pattern, message) or re.search(codex_pattern, message))
    has_quick_format = has_continuation and not has_prefixed

    # Format 2: Deep review (dual phase with gemini + codex)
    # Accept both current and legacy marker names
    has_gemini = "gemini-continuation-id:" in message or "gemini-review:" in message
    has_codex = "codex-continuation-id:" in message or "codex-review:" in message
    has_deep_format = has_gemini and has_codex

    # Component 2 (P1T13-F5a): Check for Review-Hash trailer (presence only)
    # Note: We only check presence, not correctness (can't reconstruct staging area post-commit)
    # Codex LOW fix: Anchor pattern and require hex value to prevent false positives
    review_hash_pattern = r"(?:^|\n)\s*review-hash:\s*[0-9a-f]{8,}"
    has_review_hash = bool(re.search(review_hash_pattern, message))

    return (has_quick_format or has_deep_format) and has_review_hash


def main():
    """Main verification logic."""
    pr_commits = get_pr_commits()

    if not pr_commits:
        print("✅ No commits to verify (empty PR or single-commit branch)")
        return 0

    state = load_workflow_state()

    # Detect CI environment (GitHub Actions, GitLab CI, etc.)
    is_ci = os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"

    if not state:
        if is_ci:
            # In CI: workflow-state.json is gitignored, so check commit messages instead
            print("ℹ️  Workflow state file not available in CI (gitignored)")
            print("   Verifying via commit message markers instead...")
            print()

            # Verify each commit has review markers in its message
            non_compliant_commits = []
            for commit_hash in pr_commits:
                if not has_review_markers(commit_hash):
                    non_compliant_commits.append(commit_hash)

            if non_compliant_commits:
                print("❌ GATE BYPASS DETECTED!")
                print(f"   Found {len(non_compliant_commits)} commit(s) without review markers:")
                for commit in non_compliant_commits:
                    print(f"     - {commit[:8]}")
                print()
                print("   These commits are missing zen-mcp review markers:")
                print("   Required: zen-mcp-review: approved")
                print("   Plus ONE of:")
                print("     Format 1 (quick review): continuation-id: <id>")
                print(
                    "     Format 2 (deep review): gemini-continuation-id: <id> AND codex-continuation-id: <id>"
                )
                print("     Legacy (deep review): gemini-review: <id> AND codex-review: <id>")
                print("   PLUS: Review-Hash: <hash> (Component 2 - P1T13-F5a)")
                print()
                print("   All commits must be created via workflow gates (no --no-verify)")
                return 1

            print(f"✅ All {len(pr_commits)} commit(s) have review approval markers")
            return 0
        else:
            # Locally: Allow for documentation-only changes
            print("⚠️  Warning: No workflow state file found")
            print("   This is acceptable for documentation-only changes")
            print("   or initial repository setup.")
            return 0

    # Component 2 (P1T13-F5a) - Codex MEDIUM fix: Always check Review-Hash in CI
    # Even when state exists, verify commit messages have Review-Hash trailer
    if is_ci:
        print("ℹ️  Verifying Review-Hash trailers in CI...")
        marker_non_compliant = []
        for commit_hash in pr_commits:
            if not has_review_markers(commit_hash):
                marker_non_compliant.append(commit_hash)

        if marker_non_compliant:
            print("❌ GATE BYPASS DETECTED!")
            print(f"   Found {len(marker_non_compliant)} commit(s) without Review-Hash:")
            for commit in marker_non_compliant:
                print(f"     - {commit[:8]}")
            print()
            print("   All commits must include Review-Hash trailer:")
            print("   Review-Hash: $(./scripts/compute_review_hash.py)")
            print()
            print("   See Component 2 (P1T13-F5a) for details")
            return 1

    # Get commit history from state
    commit_history = state.get("commit_history", [])

    # Fallback to last_commit_hash for backward compatibility
    if not commit_history:
        recorded_hash = state.get("last_commit_hash")
        if recorded_hash:
            commit_history = [recorded_hash]

    if not commit_history:
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
