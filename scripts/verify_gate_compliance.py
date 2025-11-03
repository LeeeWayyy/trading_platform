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
import subprocess
import sys
from pathlib import Path


def get_pr_commits():
    """Get list of commit hashes in current PR/branch."""
    # Use environment variable for dynamic base branch detection
    # Falls back to master if not in CI environment
    base_branch = os.getenv('GITHUB_BASE_REF', 'master')
    base_ref = f"origin/{base_branch}"

    # Get commits between base branch and HEAD
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
    """Load .claude/workflow-state.json if it exists."""
    state_file = Path(".claude/workflow-state.json")
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse workflow state: {e}")
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
    """Check if commit message contains zen-mcp review approval markers."""
    message = get_commit_message(commit_hash)
    # Check for both zen-mcp-review and continuation-id markers
    has_approval = "zen-mcp-review: approved" in message.lower()
    has_continuation_id = "continuation-id:" in message.lower()
    return has_approval and has_continuation_id


def main():
    """Main verification logic."""
    pr_commits = get_pr_commits()

    if not pr_commits:
        print("✅ No commits to verify (empty PR or single-commit branch)")
        return 0

    state = load_workflow_state()

    # Detect CI environment (GitHub Actions, GitLab CI, etc.)
    is_ci = os.getenv('CI') == 'true' or os.getenv('GITHUB_ACTIONS') == 'true'

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
                print("   - zen-mcp-review: approved")
                print("   - continuation-id: <id>")
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
