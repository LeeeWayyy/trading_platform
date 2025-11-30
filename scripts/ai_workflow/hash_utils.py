#!/usr/bin/env python3
"""
Git diff hashing utilities for workflow enforcement.

Provides the single source of truth for computing git diff hashes,
ensuring byte-for-byte parity between:
- Local pre-commit hooks (WorkflowGate)
- CI validation

Key Design Principles:
1. Identical git commands and flags in all code paths
2. Raw byte hashing (no .decode() → .encode() round-trip)
3. Merge commit support via first-parent diff
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def is_merge_commit(commit_sha: str, cwd: Path | None = None) -> bool:
    """
    Detect if a commit is a merge (has 2+ parents).

    Args:
        commit_sha: Git commit SHA to check
        cwd: Working directory (defaults to current directory)

    Returns:
        True if commit has 2 or more parents (is a merge)

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    result = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", commit_sha],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd or Path.cwd(),
    )

    # Output format: "<commit_sha> <parent1_sha> [<parent2_sha> ...]"
    parents = result.stdout.strip().split()

    # Merge if more than 2 items (commit + 2+ parents)
    return len(parents) > 2


def compute_git_diff_hash(
    commit_sha: str | None = None,
    is_merge: bool | None = None,
    cwd: Path | None = None,
) -> str:
    """
    Compute SHA256 hash of git diff with exact parity across environments.

    This function ensures identical hashing behavior between:
    - Local: WorkflowGate._compute_staged_hash() during pre-commit
    - CI: verify_gate_compliance.py during PR validation

    Merge Commit Handling:
    - Local (staging): `git diff --staged` → hashes full merge result
    - CI (validation): `git diff <commit>^1 <commit>` → same merge result

    Args:
        commit_sha: If provided, hash this commit's changes.
                   If None, hash currently staged changes.
        is_merge: If True, treat as merge commit (diff against first parent).
                 If None, auto-detect from commit.
        cwd: Working directory (defaults to current directory)

    Returns:
        SHA256 hexdigest of git diff output (raw bytes).
        Empty string if no changes (staged area is clean).

    Raises:
        subprocess.CalledProcessError: If git command fails

    Examples:
        >>> # Hash staged changes (pre-commit hook)
        >>> compute_git_diff_hash()
        'a1b2c3d4...'

        >>> # Hash specific commit (CI validation)
        >>> compute_git_diff_hash('abc123')
        'a1b2c3d4...'
    """
    if commit_sha:
        # Validating a historical commit in CI
        if is_merge is None:
            is_merge = is_merge_commit(commit_sha, cwd=cwd)

        if is_merge:
            # Merge commit: diff against first parent
            cmd = [
                "git",
                "--no-pager",
                "diff",
                f"{commit_sha}^1",
                commit_sha,
                "--binary",
                "--no-color",
                "--no-ext-diff",
            ]
        else:
            # Regular commit: standard show
            cmd = [
                "git",
                "--no-pager",
                "show",
                commit_sha,
                "--format=",
                "--binary",
                "--no-color",
                "--no-ext-diff",
            ]
    else:
        # Hashing staged changes (local pre-commit hook)
        cmd = [
            "git",
            "--no-pager",
            "diff",
            "--staged",
            "--binary",
            "--no-color",
            "--no-ext-diff",
        ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        check=True,
        cwd=cwd or Path.cwd(),
    )

    if not result.stdout:
        return ""

    # CRITICAL: Hash raw bytes, NOT .decode().encode()
    hasher = hashlib.sha256()
    hasher.update(result.stdout)
    return hasher.hexdigest()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        commit = sys.argv[1]
        hash_value = compute_git_diff_hash(commit_sha=commit)
        print(hash_value)
    else:
        hash_value = compute_git_diff_hash()
        if hash_value:
            print(hash_value)
        else:
            print("(no staged changes)", file=sys.stderr)
            sys.exit(1)
