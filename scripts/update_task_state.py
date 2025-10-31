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
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def load_state(state_file: Path) -> dict[str, Any]:
    """Load current task state."""
    if not state_file.exists():
        return {}
    with open(state_file) as f:
        return json.load(f)


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    """Save task state with pretty formatting."""
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
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
    state = load_state(state_file)

    if not state.get("current_task"):
        print("❌ No active task found. Start a task first.")
        sys.exit(1)

    # Update progress
    completed = state["progress"]["completed_components"] + 1
    total = state["progress"]["total_components"]
    next_component_num = component_num + 1

    state["progress"]["completed_components"] = completed
    state["progress"]["completion_percentage"] = int((completed / total) * 100)

    # Add to completed work
    component_name = state["progress"]["current_component"]["name"]
    state["completed_work"][f"Component {component_num}"] = {
        "name": component_name,
        "commit": commit_hash,
        "files": files or [],
        "tests_added": tests_added,
        "review_approved": True,
        "continuation_id": continuation_id or "N/A",
        "completed_at": datetime.utcnow().isoformat() + "Z",
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
        state["current_task"]["completed"] = datetime.utcnow().isoformat() + "Z"

    # Update metadata
    state["meta"]["last_updated"] = datetime.utcnow().isoformat() + "Z"

    save_state(state_file, state)

    # Display summary
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
    state = {
        "current_task": {
            "task_id": task_id,
            "title": title,
            "phase": task_id[:2],  # e.g., "P2" from "P2T1"
            "branch": branch,
            "task_file": task_file,
            "state": "IN_PROGRESS",
            "started": datetime.utcnow().date().isoformat(),
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
            "last_updated": datetime.utcnow().isoformat() + "Z",
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
    state = load_state(state_file)

    if not state.get("current_task"):
        print("❌ No active task found.")
        sys.exit(1)

    state["current_task"]["state"] = "COMPLETE"
    state["current_task"]["completed"] = datetime.utcnow().isoformat() + "Z"
    state["progress"]["completion_percentage"] = 100
    state["progress"]["current_component"] = None
    state["meta"]["last_updated"] = datetime.utcnow().isoformat() + "Z"

    save_state(state_file, state)
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
