"""
Tests for validate_review_hash() function in verify_gate_compliance.py

Component A2.1 (P1T13-F5): Server-side Review-Hash validation
Tests hash correctness validation (not just presence) for regular and merge commits.

Author: Claude Code
Date: 2025-11-15
Task: P1T13-F5 Phase A.2 Component 1
"""

import subprocess

import pytest

from libs.common.hash_utils import compute_git_diff_hash
from scripts.verify_gate_compliance import validate_review_hash


@pytest.fixture()
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize repo with master as default branch
    subprocess.run(["git", "init", "-b", "master"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )

    # Change to temp repo and ensure it's restored after test
    monkeypatch.chdir(repo_dir)

    return repo_dir


def test_validate_review_hash_happy_path(temp_git_repo):
    """Valid Review-Hash should pass validation."""

    # Create a commit with correct Review-Hash
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], check=True)

    # Compute correct hash
    correct_hash = compute_git_diff_hash()

    subprocess.run(
        ["git", "commit", "-m", f"Test commit\n\nReview-Hash: {correct_hash}"],
        check=True,
        capture_output=True,
    )

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = result.stdout.strip()

    # Validate
    assert validate_review_hash(commit_sha), "Valid Review-Hash should pass"


def test_validate_review_hash_mismatch(temp_git_repo):
    """Incorrect Review-Hash should fail validation."""

    # Setup: Create initial commit first to avoid exemption
    (temp_git_repo / "setup.txt").write_text("setup\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "Setup"], check=True, capture_output=True)

    # Create a commit with wrong Review-Hash
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], check=True)

    wrong_hash = "a" * 64  # Fake hash

    subprocess.run(
        ["git", "commit", "-m", f"Test commit\n\nReview-Hash: {wrong_hash}"],
        check=True,
        capture_output=True,
    )

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = result.stdout.strip()

    # Validate
    assert not validate_review_hash(commit_sha), "Wrong Review-Hash should fail"


def test_validate_review_hash_missing(temp_git_repo):
    """Missing Review-Hash should fail validation."""

    # Setup: Create initial commit first to avoid exemption
    (temp_git_repo / "setup.txt").write_text("setup\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "Setup"], check=True, capture_output=True)

    # Create a commit without Review-Hash
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], check=True)

    subprocess.run(
        ["git", "commit", "-m", "Test commit"],
        check=True,
        capture_output=True,
    )

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = result.stdout.strip()

    # Validate
    assert not validate_review_hash(commit_sha), "Missing Review-Hash should fail"


def test_validate_review_hash_merge_commit(temp_git_repo):
    """Merge commits should be validated using first-parent diff."""

    # Setup: Create two branches
    (temp_git_repo / "file.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    # Branch 1
    subprocess.run(["git", "checkout", "-b", "branch1"], check=True, capture_output=True)
    (temp_git_repo / "file.txt").write_text("branch1\n")
    subprocess.run(["git", "commit", "-am", "B1"], check=True, capture_output=True)

    # Branch 2
    subprocess.run(["git", "checkout", "master"], check=True, capture_output=True)
    (temp_git_repo / "file.txt").write_text("branch2\n")
    subprocess.run(["git", "commit", "-am", "B2"], check=True, capture_output=True)

    # Merge with conflict resolution
    subprocess.run(["git", "merge", "--no-commit", "branch1"], check=False, capture_output=True)
    (temp_git_repo / "file.txt").write_text("resolved\n")
    subprocess.run(["git", "add", "."], check=True)

    # Compute correct hash for merge
    correct_hash = compute_git_diff_hash()

    subprocess.run(
        ["git", "commit", "-m", f"Merge branch1\n\nReview-Hash: {correct_hash}"],
        check=True,
        capture_output=True,
    )

    # Get merge commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    merge_commit_sha = result.stdout.strip()

    # Validate
    assert validate_review_hash(merge_commit_sha), "Valid merge commit Review-Hash should pass"


def test_validate_review_hash_initial_commit(temp_git_repo):
    """Initial commits should be exempt (no parent to validate against)."""

    # Create initial commit (will be exempt)
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = result.stdout.strip()

    # Validate (should be exempt)
    assert validate_review_hash(commit_sha), "Initial commit should be exempt"


def test_validate_review_hash_empty_commit(temp_git_repo):
    """Empty commits should validate correctly (empty hash)."""

    # Create initial commit first
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    # Create empty commit with correct empty hash
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Empty commit\n\nReview-Hash: "],
        check=True,
        capture_output=True,
    )

    # Get commit SHA
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    commit_sha = result.stdout.strip()

    # Validate
    assert validate_review_hash(commit_sha), "Empty commit with empty hash should pass"
