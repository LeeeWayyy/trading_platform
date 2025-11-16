#!/usr/bin/env python3
"""
Shared git diff hashing utilities for workflow enforcement.

Component A2.1 (P1T13-F5): Server-side Review-Hash validation
This module provides the single source of truth for computing git diff hashes,
ensuring byte-for-byte parity between:
- Local pre-commit hooks (WorkflowGate)
- CI validation (verify_gate_compliance.py)

Key Design Principles:
1. Identical git commands and flags in all code paths
2. Raw byte hashing (no .decode() → .encode() round-trip)
3. Merge commit support via first-parent diff

Author: Claude Code
Date: 2025-11-15
Task: P1T13-F5 Phase A.2 Component 1
"""

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

# Project root for git operations (libs/common -> trading_platform)
PROJECT_ROOT = Path(__file__).parent.parent.parent


def is_merge_commit(commit_sha: str, cwd: Optional[Path] = None) -> bool:
    """
    Detect if a commit is a merge (has 2+ parents).

    Args:
        commit_sha: Git commit SHA to check
        cwd: Working directory (defaults to PROJECT_ROOT)

    Returns:
        True if commit has 2 or more parents (is a merge)

    Raises:
        subprocess.CalledProcessError: If git command fails

    Example:
        >>> is_merge_commit("abc123")  # Regular commit
        False
        >>> is_merge_commit("def456")  # Merge commit
        True
    """
    result = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", commit_sha],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd or PROJECT_ROOT,
    )

    # Output format: "<commit_sha> <parent1_sha> [<parent2_sha> ...]"
    # parents[0] is the commit itself, parents[1:] are actual parents
    parents = result.stdout.strip().split()

    # Merge if more than 2 items (commit + 2+ parents)
    return len(parents) > 2


def compute_git_diff_hash(
    commit_sha: Optional[str] = None,
    is_merge: Optional[bool] = None,
    cwd: Optional[Path] = None
) -> str:
    """
    Compute SHA256 hash of git diff with exact WorkflowGate parity.

    This function ensures identical hashing behavior between:
    - Local: WorkflowGate._compute_staged_hash() during pre-commit
    - CI: verify_gate_compliance.py during PR validation

    Merge Commit Handling:
    - Local (staging): `git diff --staged` → hashes full merge result
    - CI (validation): `git diff <commit>^1 <commit>` → same merge result
    - Both produce byte-for-byte identical output ✅

    Args:
        commit_sha: If provided, hash this commit's changes.
                   If None, hash currently staged changes.
        is_merge: If True, treat as merge commit (diff against first parent).
                 If None, auto-detect from commit.
        cwd: Working directory (defaults to PROJECT_ROOT)

    Returns:
        SHA256 hexdigest of git diff output (raw bytes).
        Empty string if no changes (staged area is clean).

    Raises:
        subprocess.CalledProcessError: If git command fails

    Examples:
        >>> # Hash staged changes (pre-commit hook)
        >>> compute_git_diff_hash()
        'a1b2c3d4...'

        >>> # Hash regular commit (CI validation)
        >>> compute_git_diff_hash('abc123')
        'a1b2c3d4...'

        >>> # Hash merge commit (CI validation)
        >>> compute_git_diff_hash('def456', is_merge=True)
        'e5f6g7h8...'
    """
    if commit_sha:
        # Validating a historical commit in CI

        # Auto-detect merge status if not provided
        if is_merge is None:
            is_merge = is_merge_commit(commit_sha, cwd=cwd)

        if is_merge:
            # Merge commit: diff against first parent
            # This reproduces what was in the staging area during merge
            # git diff <first_parent> <merge_commit> shows full merge result
            cmd = [
                "git",
                "--no-pager",
                "diff",
                f"{commit_sha}^1",  # First parent (usually master/main)
                commit_sha,         # The merge commit
                "--binary",         # Include binary file content correctly
                "--no-color",       # Disable color escape codes
                "--no-ext-diff",    # Ignore external diff helpers
            ]
        else:
            # Regular commit: standard show
            cmd = [
                "git",
                "--no-pager",
                "show",
                commit_sha,
                "--format=",        # Suppress commit message/metadata
                "--binary",
                "--no-color",
                "--no-ext-diff",
            ]
    else:
        # Hashing staged changes (local pre-commit hook)
        # EXACT same command as WorkflowGate (lines 446-454)
        cmd = [
            "git",
            "--no-pager",
            "diff",
            "--staged",         # Only staged changes (not working tree)
            "--binary",
            "--no-color",
            "--no-ext-diff",
        ]

    result = subprocess.run(
        cmd,
        capture_output=True,  # capture stdout as bytes
        check=True,
        cwd=cwd or PROJECT_ROOT,
    )

    if not result.stdout:
        # No changes (empty staging area or empty commit)
        return ""

    # CRITICAL: Hash raw bytes, NOT .decode().encode()
    # This ensures binary files, locale differences, etc. all hash identically
    hasher = hashlib.sha256()
    hasher.update(result.stdout)  # result.stdout is bytes, not str
    return hasher.hexdigest()


if __name__ == "__main__":
    # CLI usage: ./scripts/hash_utils.py [commit_sha]
    import sys

    if len(sys.argv) > 1:
        # Hash specific commit
        commit = sys.argv[1]
        hash_value = compute_git_diff_hash(commit_sha=commit)
        print(f"Hash for commit {commit[:8]}: {hash_value}")
    else:
        # Hash staged changes
        hash_value = compute_git_diff_hash()
        if hash_value:
            print(hash_value)
        else:
            print("(no staged changes)", file=sys.stderr)
            sys.exit(1)
