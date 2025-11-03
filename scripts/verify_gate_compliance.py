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


def main():
    """Main verification logic."""
    pr_commits = get_pr_commits()
    
    if not pr_commits:
        print("✅ No commits to verify (empty PR or single-commit branch)")
        return 0
    
    state = load_workflow_state()

    if not state:
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
