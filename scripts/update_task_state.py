#!/usr/bin/env python3
"""
Update task state tracking file after completing components.

Usage:
    # Complete a component
    ./scripts/update_task_state.py complete --component 2 --commit abc1234

    # Start a new task
    ./scripts/update_task_state.py start --task P2T2 --branch feature/P2T2-xxx

    # Mark task complete
    ./scripts/update_task_state.py finish
"""

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _acquire_lock(state_file: Path, max_retries: int = 3) -> int:
    """Acquire exclusive file lock."""
    lock_file = state_file.parent / ".task-state.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        lock_fd = None
        try:
            lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY, 0o644)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd
        except OSError:
            # Close file descriptor before retry to prevent leak
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass  # Ignore close errors
            if attempt < max_retries - 1:
                time.sleep(0.1 * (2**attempt))
                continue
            # MEDIUM fix: Remove unreachable code (Gemini review)
            # Last attempt failed, raise error
            raise RuntimeError(f"Failed to acquire lock after {max_retries} attempts")


def _release_lock(lock_fd: int) -> None:
    """Release file lock."""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    except OSError as e:
        print(f"⚠️  Warning: Failed to release lock: {e}")


def _save_state_unlocked(state_file: Path, state: dict[str, Any]) -> None:
    """
    Save task state without acquiring lock (internal use only).

    Used by _locked_state context manager where lock is already held.
    For external use, call save_state() which includes locking.
    """
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file then rename
    temp_fd, temp_path = tempfile.mkstemp(
        dir=state_file.parent, prefix=".task-state-", suffix=".tmp"
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        Path(temp_path).replace(state_file)
    except OSError:
        Path(temp_path).unlink(missing_ok=True)
        raise


@contextmanager
def _locked_state(state_file: Path) -> Generator[dict[str, Any], None, None]:
    """
    Context manager for atomic read-modify-write operations.

    CRITICAL (CRIT-002 fix): Ensures entire read-modify-write cycle is wrapped
    in a file lock, preventing race conditions from concurrent processes.

    Usage:
        with _locked_state(state_file) as state:
            state["field"] = new_value
            # state automatically saved on exit with lock held

    The lock is held for the entire duration of:
    1. Load state
    2. Yield to caller for modifications
    3. Save modified state
    4. Release lock (in finally)
    """
    lock_fd = _acquire_lock(state_file)
    try:
        # Load state with lock held
        state = load_state(state_file)
        # Yield to caller for modifications
        yield state
        # Save modified state with lock still held
        _save_state_unlocked(state_file, state)
    finally:
        # Always release lock, even if exception occurred
        _release_lock(lock_fd)


def load_state(state_file: Path) -> dict[str, Any]:
    """Load current task state with error handling (MED-001 fix)."""
    if not state_file.exists():
        return {}
    try:
        with open(state_file) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️  Warning: Failed to parse task state file: {e}")
        print("   Backing up corrupt file and starting fresh...")
        # Backup corrupt file
        backup = state_file.with_suffix(".json.corrupt")
        state_file.rename(backup)
        print(f"   Corrupt file saved to: {backup}")
        return {}


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    """
    Save task state with atomic write and file locking (CRIT-002 fix).

    Also fixes MED-001 by handling corrupt JSON gracefully.

    Note: For read-modify-write operations, use _locked_state() context manager
    instead to ensure the entire cycle is atomic.
    """
    # Acquire lock for standalone save
    lock_fd = _acquire_lock(state_file)
    try:
        _save_state_unlocked(state_file, state)
    finally:
        _release_lock(lock_fd)

    print(f"✅ Task state updated: {state_file}")


def complete_component(
    state_file: Path,
    component_num: int,
    commit_hash: str,
    files: list[str] | None = None,
    tests_added: int = 0,
    continuation_id: str | None = None,
) -> None:
    """Mark a component as complete and advance to next."""
    with _locked_state(state_file) as state:
        if not state.get("current_task"):
            print("❌ No active task found. Start a task first.")
            sys.exit(1)

        # HIGH-003 fix: Validate component number matches current component
        current_comp_num = state["progress"]["current_component"]["number"]
        if component_num != current_comp_num:
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("❌ ERROR: Component number mismatch")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"   You specified: Component {component_num}")
            print(f"   Current component: Component {current_comp_num}")
            print(f"   Current name: {state['progress']['current_component']['name']}")
            print()
            print("   Complete components in order. Current component must finish first.")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            sys.exit(1)

        # Update progress
        completed = state["progress"]["completed_components"] + 1
        total = state["progress"]["total_components"]
        next_component_num = component_num + 1

        state["progress"]["completed_components"] = completed
        # HIGH fix: Guard against ZeroDivisionError (Gemini review)
        state["progress"]["completion_percentage"] = (
            int((completed / total) * 100) if total > 0 else 0
        )

        # Add to completed work
        component_name = state["progress"]["current_component"]["name"]
        state["completed_work"][f"Component {component_num}"] = {
            "name": component_name,
            "commit": commit_hash,
            "files": files or [],
            "tests_added": tests_added,
            "review_approved": True,
            "continuation_id": continuation_id or "N/A",
            "completed_at": datetime.now(UTC).isoformat(),
        }

        # Update current component
        if next_component_num <= total:
            # Get next component from remaining_components
            remaining = state.get("remaining_components", [])
            next_comp = next((c for c in remaining if c["number"] == next_component_num), None)
            if next_comp:
                # Found in remaining_components - use rich metadata
                state["progress"]["current_component"] = {
                    "number": next_component_num,
                    "name": next_comp["name"],
                    "status": "NOT_STARTED",
                    "description": next_comp.get("description", ""),
                }
                # Remove from remaining
                state["remaining_components"] = [
                    c for c in remaining if c["number"] != next_component_num
                ]
            else:
                # Not in remaining_components - use default
                # This happens when remaining_components is empty (initialized as [])
                state["progress"]["current_component"] = {
                    "number": next_component_num,
                    "name": f"Component {next_component_num}",
                    "status": "NOT_STARTED",
                    "description": "",
                }
        else:
            # All components complete
            state["progress"]["current_component"] = None
            state["current_task"]["state"] = "COMPLETE"
            state["current_task"]["completed"] = datetime.now(UTC).isoformat()

        # Update metadata (LOW-001 fix: use timezone-aware datetime)
        state["meta"]["last_updated"] = datetime.now(UTC).isoformat()
        # State automatically saved when exiting context

    # Display summary (outside lock to reduce lock hold time)
    print(f"\n📊 Task Progress: {state['progress']['completion_percentage']}%")
    print(f"   Completed: {completed}/{total} components")
    if state["progress"]["current_component"]:
        print(
            f"   Next: Component {next_component_num} - {state['progress']['current_component']['name']}"
        )
    else:
        print("   🎉 All components complete!")


def start_task(
    state_file: Path,
    task_id: str,
    title: str,
    branch: str,
    task_file: str,
    total_components: int,
) -> None:
    """Start tracking a new task."""
    # LOW-001 fix: use timezone-aware datetime
    now_iso = datetime.now(UTC).isoformat()
    state = {
        "current_task": {
            "task_id": task_id,
            "title": title,
            "phase": task_id[:2],  # e.g., "P2" from "P2T1"
            "branch": branch,
            "task_file": task_file,
            "state": "IN_PROGRESS",
            "started": datetime.now(UTC).date().isoformat(),
        },
        "progress": {
            "total_components": total_components,
            "completed_components": 0,
            "current_component": {
                "number": 1,
                "name": "Component 1",
                "status": "NOT_STARTED",
            },
            "completion_percentage": 0,
        },
        "completed_work": {},
        "remaining_components": [],
        "next_steps": [],
        "context": {"continuation_ids": {}, "key_decisions": [], "important_notes": []},
        "meta": {
            "last_updated": now_iso,
            "updated_by": "update_task_state.py",
            "auto_resume_enabled": True,
        },
    }

    save_state(state_file, state)
    print(f"✅ Started tracking task: {task_id} - {title}")
    print(f"   Branch: {branch}")
    print(f"   Total components: {total_components}")


def finish_task(state_file: Path) -> None:
    """Mark entire task as complete."""
    with _locked_state(state_file) as state:
        if not state.get("current_task"):
            print("❌ No active task found.")
            sys.exit(1)

        # LOW-001 fix: use timezone-aware datetime
        now_iso = datetime.now(UTC).isoformat()
        state["current_task"]["state"] = "COMPLETE"
        state["current_task"]["completed"] = now_iso
        state["progress"]["completion_percentage"] = 100
        state["progress"]["current_component"] = None
        state["meta"]["last_updated"] = now_iso
        # State automatically saved when exiting context

    print(f"🎉 Task complete: {state['current_task']['task_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update task state tracking")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Complete component command
    complete_parser = subparsers.add_parser("complete", help="Mark component complete")
    complete_parser.add_argument("--component", type=int, required=True, help="Component number")
    complete_parser.add_argument("--commit", required=True, help="Commit hash")
    complete_parser.add_argument("--files", nargs="+", help="Modified files")
    complete_parser.add_argument("--tests", type=int, default=0, help="Number of tests added")
    complete_parser.add_argument("--continuation-id", help="Review continuation ID")

    # Start task command
    start_parser = subparsers.add_parser("start", help="Start new task")
    start_parser.add_argument("--task", required=True, help="Task ID (e.g., P2T1)")
    start_parser.add_argument("--title", required=True, help="Task title")
    start_parser.add_argument("--branch", required=True, help="Git branch name")
    start_parser.add_argument("--task-file", required=True, help="Task document path")
    start_parser.add_argument("--components", type=int, required=True, help="Total components")

    # Finish task command
    subparsers.add_parser("finish", help="Mark entire task complete")

    args = parser.parse_args()

    # Find project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    state_file = project_root / ".claude" / "task-state.json"

    if args.command == "complete":
        complete_component(
            state_file,
            args.component,
            args.commit,
            args.files,
            args.tests,
            args.continuation_id,
        )
    elif args.command == "start":
        start_task(
            state_file,
            args.task,
            args.title,
            args.branch,
            args.task_file,
            args.components,
        )
    elif args.command == "finish":
        finish_task(state_file)


if __name__ == "__main__":
    main()
