#!/usr/bin/env python3
"""
Context Checkpoint Management Script

Manages context checkpoints for AI coding session continuity.
Checkpoints preserve critical state before context-modifying operations (delegation, compaction, session end).

Usage:
    ./scripts/context_checkpoint.py create --type delegation
    ./scripts/context_checkpoint.py restore --id <checkpoint_id>
    ./scripts/context_checkpoint.py list [--type delegation]
    ./scripts/context_checkpoint.py cleanup --older-than 7d

Author: Claude Code
Date: 2025-11-02
"""

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Constants
PROJECT_ROOT = Path(__file__).parent.parent
CHECKPOINT_DIR = PROJECT_ROOT / ".claude" / "checkpoints"
TASK_STATE_FILE = PROJECT_ROOT / ".claude" / "task-state.json"
WORKFLOW_STATE_FILE = PROJECT_ROOT / ".claude" / "workflow-state.json"


def get_git_state() -> dict[str, Any]:
    """Capture current git state."""
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=PROJECT_ROOT, text=True
        ).strip()

        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()

        staged_output = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=PROJECT_ROOT, text=True
        ).strip()
        staged_files = staged_output.split("\n") if staged_output else []

        return {"branch": branch, "commit": commit, "staged_files": staged_files}
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to capture git state: {e}", file=sys.stderr)
        return {"branch": None, "commit": None, "staged_files": []}


def estimate_token_usage() -> int:
    """Estimate current context token usage (placeholder - returns 0 for now)."""
    # TODO: Implement actual token usage estimation
    # This would need to track:
    # - Files read in session
    # - Tool calls made
    # - Conversation history
    return 0


def create_checkpoint(checkpoint_type: str) -> str:
    """
    Create a context checkpoint.

    Args:
        checkpoint_type: Type of checkpoint ("delegation" or "session_end")

    Returns:
        checkpoint_id: UUID of created checkpoint
    """
    if checkpoint_type not in ["delegation", "session_end"]:
        raise ValueError(f"Invalid checkpoint type: {checkpoint_type}")

    # Ensure checkpoint directory exists
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate checkpoint ID
    checkpoint_id = str(uuid.uuid4())

    # Load complete task state (preserve all fields)
    task_state = {}
    if TASK_STATE_FILE.exists():
        with open(TASK_STATE_FILE) as f:
            task_state = json.load(f)

    # Load complete workflow state (preserve all fields)
    workflow_state = {}
    if WORKFLOW_STATE_FILE.exists():
        with open(WORKFLOW_STATE_FILE) as f:
            workflow_state = json.load(f)

    # Create checkpoint data
    checkpoint_data = {
        "id": checkpoint_id,
        "timestamp": datetime.now().isoformat(),
        "type": checkpoint_type,
        "context_data": {
            "task_state": task_state,  # Store complete task state
            "workflow_state": workflow_state,  # Store complete workflow state
            "delegation_history": [],  # TODO: Track delegations
            "critical_findings": [],  # TODO: Extract from context
            "pending_decisions": [],  # TODO: Extract from context
            "continuation_ids": [],  # TODO: Extract from zen-mcp reviews
        },
        "git_state": get_git_state(),
        "token_usage_estimate": estimate_token_usage(),
    }

    # Write checkpoint file
    checkpoint_file = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint_data, f, indent=2)

    # Update symlink for latest checkpoint of this type
    symlink_name = f"latest_{checkpoint_type}.json"
    symlink_path = CHECKPOINT_DIR / symlink_name

    # Remove existing symlink if it exists
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()

    # Create new symlink
    symlink_path.symlink_to(checkpoint_file.name)

    print(f"✓ Created {checkpoint_type} checkpoint: {checkpoint_id}")
    print(f"  File: {checkpoint_file}")
    print(f"  Symlink: {symlink_path}")

    return checkpoint_id


def restore_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    """
    Restore context from a checkpoint.

    Args:
        checkpoint_id: UUID of checkpoint to restore

    Returns:
        checkpoint_data: Checkpoint data
    """
    checkpoint_file = CHECKPOINT_DIR / f"{checkpoint_id}.json"

    if not checkpoint_file.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

    with open(checkpoint_file) as f:
        checkpoint_data = json.load(f)

    # Create backups of current state files before overwriting
    if TASK_STATE_FILE.exists():
        backup_path = TASK_STATE_FILE.parent / f"{TASK_STATE_FILE.name}.backup"
        with open(TASK_STATE_FILE) as src, open(backup_path, "w") as dst:
            dst.write(src.read())
        print(f"  Created backup: {backup_path}")

    if WORKFLOW_STATE_FILE.exists():
        backup_path = WORKFLOW_STATE_FILE.parent / f"{WORKFLOW_STATE_FILE.name}.backup"
        with open(WORKFLOW_STATE_FILE) as src, open(backup_path, "w") as dst:
            dst.write(src.read())
        print(f"  Created backup: {backup_path}")

    # Restore complete task state (preserves all fields: current_task, progress, completed_work, etc.)
    task_state = checkpoint_data["context_data"].get("task_state", {})
    if task_state:
        TASK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TASK_STATE_FILE, "w") as f:
            json.dump(task_state, f, indent=2)
        print(f"  Restored task state: {TASK_STATE_FILE}")

    # Restore complete workflow state (preserves all fields)
    workflow_state = checkpoint_data["context_data"].get("workflow_state", {})
    if workflow_state:
        WORKFLOW_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WORKFLOW_STATE_FILE, "w") as f:
            json.dump(workflow_state, f, indent=2)
        print(f"  Restored workflow state: {WORKFLOW_STATE_FILE}")

    print(f"✓ Restored checkpoint: {checkpoint_id}")
    print(f"  Type: {checkpoint_data['type']}")
    print(f"  Created: {checkpoint_data['timestamp']}")
    print(f"  Git branch: {checkpoint_data['git_state']['branch']}")
    print(f"  Git commit: {checkpoint_data['git_state']['commit'][:8]}")

    return checkpoint_data


def list_checkpoints(checkpoint_type: str | None = None) -> list[dict[str, Any]]:
    """
    List all checkpoints, optionally filtered by type.

    Args:
        checkpoint_type: Filter by checkpoint type (optional)

    Returns:
        checkpoints: List of checkpoint metadata
    """
    if not CHECKPOINT_DIR.exists():
        return []

    checkpoints = []
    for checkpoint_file in sorted(CHECKPOINT_DIR.glob("*.json"), reverse=True):
        # Skip symlinks
        if checkpoint_file.is_symlink():
            continue

        try:
            with open(checkpoint_file) as f:
                data = json.load(f)

            # Filter by type if specified
            if checkpoint_type and data.get("type") != checkpoint_type:
                continue

            checkpoints.append(
                {
                    "id": data["id"],
                    "type": data["type"],
                    "timestamp": data["timestamp"],
                    "git_commit": (
                        data["git_state"]["commit"][:8] if data["git_state"]["commit"] else "N/A"
                    ),
                }
            )
        except (json.JSONDecodeError, KeyError) as e:
            print(
                f"Warning: Skipping corrupted checkpoint {checkpoint_file.name}: {e}",
                file=sys.stderr,
            )

    return checkpoints


def cleanup_checkpoints(older_than_days: int = 7, keep_latest: int = 10) -> int:
    """
    Clean up old checkpoints.

    Args:
        older_than_days: Delete checkpoints older than this many days
        keep_latest: Keep at least this many latest checkpoints per type

    Returns:
        deleted_count: Number of checkpoints deleted
    """
    if not CHECKPOINT_DIR.exists():
        return 0

    cutoff_date = datetime.now() - timedelta(days=older_than_days)
    deleted_count = 0

    # Group checkpoints by type
    checkpoints_by_type: dict[str, list[tuple]] = {}

    for checkpoint_file in CHECKPOINT_DIR.glob("*.json"):
        # Skip symlinks
        if checkpoint_file.is_symlink():
            continue

        try:
            with open(checkpoint_file) as f:
                data = json.load(f)

            checkpoint_type = data["type"]
            timestamp = datetime.fromisoformat(data["timestamp"])

            if checkpoint_type not in checkpoints_by_type:
                checkpoints_by_type[checkpoint_type] = []

            checkpoints_by_type[checkpoint_type].append((checkpoint_file, timestamp))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(
                f"Warning: Skipping corrupted checkpoint {checkpoint_file.name}: {e}",
                file=sys.stderr,
            )

    # Clean up each type separately
    for _, checkpoints in checkpoints_by_type.items():
        # Sort by timestamp (newest first)
        checkpoints.sort(key=lambda x: x[1], reverse=True)

        # Keep latest N checkpoints
        for i, (checkpoint_file, timestamp) in enumerate(checkpoints):
            if i < keep_latest:
                # Always keep the latest N
                continue

            # Delete if older than cutoff
            if timestamp < cutoff_date:
                checkpoint_file.unlink()
                deleted_count += 1
                print(f"✓ Deleted old checkpoint: {checkpoint_file.name}")

    return deleted_count


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Manage context checkpoints for AI coding sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a delegation checkpoint before delegating to subagent
  %(prog)s create --type delegation

  # Create a session-end checkpoint before ending coding session
  %(prog)s create --type session_end

  # Restore from a specific checkpoint
  %(prog)s restore --id abc123...

  # List all checkpoints
  %(prog)s list

  # List only delegation checkpoints
  %(prog)s list --type delegation

  # Clean up checkpoints older than 7 days
  %(prog)s cleanup --older-than 7d
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Create checkpoint
    create_parser = subparsers.add_parser("create", help="Create a new checkpoint")
    create_parser.add_argument(
        "--type", required=True, choices=["delegation", "session_end"], help="Checkpoint type"
    )

    # Restore checkpoint
    restore_parser = subparsers.add_parser("restore", help="Restore from a checkpoint")
    restore_parser.add_argument("--id", required=True, help="Checkpoint ID to restore")

    # List checkpoints
    list_parser = subparsers.add_parser("list", help="List checkpoints")
    list_parser.add_argument(
        "--type", choices=["delegation", "session_end"], help="Filter by checkpoint type"
    )

    # Cleanup checkpoints
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up old checkpoints")
    cleanup_parser.add_argument(
        "--older-than", default="7d", help="Delete checkpoints older than (e.g., 7d)"
    )
    cleanup_parser.add_argument(
        "--keep-latest", type=int, default=10, help="Keep at least N latest checkpoints per type"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "create":
            create_checkpoint(args.type)
        elif args.command == "restore":
            restore_checkpoint(args.id)
        elif args.command == "list":
            checkpoints = list_checkpoints(args.type)
            if not checkpoints:
                print("No checkpoints found.")
            else:
                print(f"{'ID':<36}  {'Type':<15}  {'Timestamp':<26}  {'Git Commit'}")
                print("-" * 100)
                for cp in checkpoints:
                    print(
                        f"{cp['id']:<36}  {cp['type']:<15}  {cp['timestamp']:<26}  {cp['git_commit']}"
                    )
        elif args.command == "cleanup":
            # Parse older_than (e.g., "7d" -> 7 days)
            older_than_str = args.older_than
            if older_than_str.endswith("d"):
                older_than_days = int(older_than_str[:-1])
            else:
                older_than_days = int(older_than_str)

            deleted = cleanup_checkpoints(older_than_days, args.keep_latest)
            print(f"✓ Cleaned up {deleted} checkpoint(s)")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
