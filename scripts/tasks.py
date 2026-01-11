#!/usr/bin/env python3
"""
Task Lifecycle Management CLI

Manages task lifecycle (TASK → PROGRESS → DONE) for the trading platform project.

Usage:
    # View next task
    ./scripts/tasks.py next
    ./scripts/tasks.py next --phase P1

    # Phase management
    ./scripts/tasks.py create-phase P2 --source docs/PLANNING/trading_platform_realization_plan.md
    ./scripts/tasks.py generate-tasks-from-phase P2

    # Task management
    ./scripts/tasks.py create P0T5 --title "New Task" --owner "@alice"
    ./scripts/tasks.py start P0T0
    ./scripts/tasks.py complete P0T0
    ./scripts/tasks.py list --state PROGRESS

    # Utilities
    ./scripts/tasks.py sync-status
    ./scripts/tasks.py lint
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent
TASKS_DIR = PROJECT_ROOT / "docs" / "TASKS"
TEMPLATES_DIR = TASKS_DIR

# Template files
TEMPLATE_TASK = TEMPLATES_DIR / "00-TEMPLATE_TASK.md"
TEMPLATE_PROGRESS = TEMPLATES_DIR / "00-TEMPLATE_PROGRESS.md"
TEMPLATE_DONE = TEMPLATES_DIR / "00-TEMPLATE_DONE.md"
TEMPLATE_FEATURE = TEMPLATES_DIR / "00-TEMPLATE_FEATURE.md"
TEMPLATE_PHASE_PLANNING = TEMPLATES_DIR / "00-TEMPLATE_PHASE_PLANNING.md"

# Planning directory
PLANNING_DIR = PROJECT_ROOT / "docs" / "PLANNING"

TaskState = Literal["TASK", "PROGRESS", "DONE"]


@dataclass
class TaskMetadata:
    """Parsed task metadata from front matter."""

    id: str
    title: str
    phase: str
    task: str
    priority: str
    owner: str
    state: TaskState
    created: str
    started: str | None = None
    completed: str | None = None
    duration: str | None = None
    file_path: Path | None = None


def parse_front_matter(file_path: Path) -> dict[str, str]:
    """
    Parse YAML front matter from a markdown file.

    Returns:
        Dictionary of key-value pairs from front matter.

    Raises:
        ValueError: If front matter is malformed.
    """
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    # Extract front matter
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        raise ValueError(f"No front matter found in {file_path}")

    front_matter = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ": " in line:
            key, value = line.split(": ", 1)
            # Remove quotes from value
            value = value.strip('"').strip("'")
            front_matter[key] = value

    return front_matter


def update_front_matter(file_path: Path, updates: dict[str, str]) -> None:
    """
    Update front matter in a markdown file.

    Args:
        file_path: Path to markdown file
        updates: Dictionary of key-value pairs to update
    """
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    # Extract front matter
    match = re.match(r"^(---\n)(.*?)(\n---\n)(.*)", content, re.DOTALL)
    if not match:
        raise ValueError(f"No front matter found in {file_path}")

    front_matter_lines = match.group(2).split("\n")
    rest_of_content = match.group(4)

    # Update front matter lines
    updated_lines = []
    updated_keys = set()

    for line in front_matter_lines:
        if not line.strip() or line.strip().startswith("#"):
            updated_lines.append(line)
            continue

        if ": " in line:
            key, value = line.split(": ", 1)
            if key in updates:
                updated_lines.append(f"{key}: {updates[key]}")
                updated_keys.add(key)
            else:
                updated_lines.append(line)
        else:
            updated_lines.append(line)

    # Add any new keys that weren't in the original front matter
    for key, value in updates.items():
        if key not in updated_keys:
            updated_lines.append(f"{key}: {value}")

    # Reconstruct file
    new_content = f"---\n{chr(10).join(updated_lines)}\n---\n{rest_of_content}"

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def get_task_file(task_id: str, state: TaskState | None = None) -> Path | None:
    """
    Find task file by ID and optionally state.

    Args:
        task_id: Task ID (e.g., "P0T0")
        state: Optional state filter (TASK, PROGRESS, DONE)

    Returns:
        Path to task file if found, None otherwise
    """
    if state:
        pattern = f"{task_id}_{state}.md"
        file_path = TASKS_DIR / pattern
        return file_path if file_path.exists() else None

    # Try all states
    for s in ["TASK", "PROGRESS", "DONE"]:
        file_path = TASKS_DIR / f"{task_id}_{s}.md"
        if file_path.exists():
            return file_path

    return None


def list_tasks(state: TaskState | None = None, phase: str | None = None) -> list[TaskMetadata]:
    """
    List all tasks, optionally filtered by state and/or phase.

    Args:
        state: Filter by state (TASK, PROGRESS, DONE)
        phase: Filter by phase (P0, P1, P2)

    Returns:
        List of TaskMetadata objects
    """
    tasks = []

    # Pattern to match task files (exclude templates)
    pattern = "P*T*_*.md"

    for file_path in TASKS_DIR.glob(pattern):
        # Skip templates
        if file_path.name.startswith("00-TEMPLATE"):
            continue

        try:
            fm = parse_front_matter(file_path)

            # Apply filters
            if state and fm.get("state") != state:
                continue
            if phase and fm.get("phase") != phase:
                continue

            metadata = TaskMetadata(
                id=fm["id"],
                title=fm["title"],
                phase=fm["phase"],
                task=fm["task"],
                priority=fm.get("priority", ""),
                owner=fm.get("owner", ""),
                state=fm["state"],  # type: ignore
                created=fm.get("created", ""),
                started=fm.get("started"),
                completed=fm.get("completed"),
                duration=fm.get("duration"),
                file_path=file_path,
            )
            tasks.append(metadata)
        except (KeyError, ValueError) as e:
            print(f"⚠️  Warning: Failed to parse {file_path}: {e}", file=sys.stderr)
            continue

    # Sort by phase, then task number
    tasks.sort(key=lambda t: (t.phase, t.task))

    return tasks


def create_task(task_id: str, title: str, owner: str, phase: str, effort: str = "X days") -> None:
    """
    Create a new task from template.

    Args:
        task_id: Task ID (e.g., "P0T5")
        title: Task title
        owner: Task owner (e.g., "@alice")
        phase: Phase (P0, P1, P2)
        effort: Estimated effort

    Raises:
        ValueError: If task already exists or invalid ID format
    """
    # Validate task ID format
    match = re.match(r"^P(\d+)T(\d+)$", task_id)
    if not match:
        raise ValueError(f"Invalid task ID format: {task_id}. Expected format: PxTy (e.g., P0T5)")

    _phase_num = match.group(1)
    task_num = match.group(2)

    # Check if task already exists
    if get_task_file(task_id):
        raise ValueError(f"Task {task_id} already exists!")

    # Read template
    if not TEMPLATE_TASK.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE_TASK}")

    with open(TEMPLATE_TASK, encoding="utf-8") as f:
        template_content = f.read()

    # Replace placeholders
    today = date.today().isoformat()
    phase_desc = {
        "P0": "P0 (MVP Core, 0-45 days)",
        "P1": "P1 (Hardening & Automation, 46-90 days)",
        "P2": "P2 (Advanced Features, 91-120 days)",
    }.get(phase, phase)

    replacements = {
        "P0T0": task_id,
        "Task Title Here": title,
        "P0": phase,
        "T0": f"T{task_num}",
        "@development-team": owner,
        "YYYY-MM-DD": today,
        "X days": effort,
        "**Phase:** P0 (MVP Core, 0-45 days)": f"**Phase:** {phase_desc}",
    }

    new_content = template_content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)

    # Write new task file
    new_file = TASKS_DIR / f"{task_id}_TASK.md"
    with open(new_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Created task: {new_file}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {new_file} to fill in details")
    print(f"   2. When ready to start: ./scripts/tasks.py start {task_id}")


def create_feature(feature_id: str, title: str, owner: str, parent_task: str, phase: str) -> None:
    """
    Create a new feature from template.

    Args:
        feature_id: Feature ID (e.g., "P0T1-F1")
        title: Feature title
        owner: Feature owner (e.g., "@alice")
        parent_task: Parent task ID (e.g., "P0T1")
        phase: Phase (P0, P1, P2)

    Raises:
        ValueError: If feature already exists or invalid ID format
    """
    # Validate feature ID format
    match = re.match(r"^P(\d+)T(\d+)-F(\d+)$", feature_id)
    if not match:
        raise ValueError(
            f"Invalid feature ID format: {feature_id}. Expected format: PxTy-Fz (e.g., P0T1-F1)"
        )

    # Check if feature already exists
    if get_task_file(feature_id):
        raise ValueError(f"Feature {feature_id} already exists!")

    # Read template
    if not TEMPLATE_FEATURE.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE_FEATURE}")

    with open(TEMPLATE_FEATURE, encoding="utf-8") as f:
        template_content = f.read()

    # Replace placeholders
    today = date.today().isoformat()
    phase_desc = {
        "P0": "P0 (MVP Core, 0-45 days)",
        "P1": "P1 (Hardening & Automation, 46-90 days)",
        "P2": "P2 (Advanced Features, 91-120 days)",
    }.get(phase, phase)

    feature_num = match.group(3)

    replacements = {
        "P0T0-F0": feature_id,
        "Feature Title Here": title,
        "P0": phase,
        "P0T0": parent_task,
        "F0": f"F{feature_num}",
        "@development-team": owner,
        "YYYY-MM-DD": today,
        "**Phase:** P0 (MVP Core, 0-45 days)": f"**Phase:** {phase_desc}",
    }

    new_content = template_content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)

    # Write new feature file
    new_file = TASKS_DIR / f"{feature_id}_PROGRESS.md"
    with open(new_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Created feature: {new_file}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {new_file} to fill in details")
    print("   2. Track implementation using 4-step pattern")
    print(f"   3. When complete: ./scripts/tasks.py complete {feature_id}")


def start_task(task_id: str) -> None:
    """
    Transition task from TASK → PROGRESS.

    Args:
        task_id: Task ID (e.g., "P0T0")

    Raises:
        ValueError: If task doesn't exist or not in TASK state
    """
    # Find TASK file
    task_file = get_task_file(task_id, "TASK")
    if not task_file:
        raise ValueError(f"Task {task_id}_TASK.md not found. Has it already been started?")

    # New PROGRESS file path
    progress_file = TASKS_DIR / f"{task_id}_PROGRESS.md"

    # Git rename
    subprocess.run(["git", "mv", str(task_file), str(progress_file)], check=True)

    # Update front matter
    today = date.today().isoformat()
    update_front_matter(progress_file, {"state": "PROGRESS", "started": today})

    print(f"✅ Started task: {task_id}")
    print(f"   File renamed: {task_file.name} → {progress_file.name}")
    print("   State: TASK → PROGRESS")
    print(f"   Started: {today}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {progress_file} to track implementation")
    print("   2. Follow 4-step pattern for each component:")
    print("      - Implement → Test → Review → Commit")
    print(f"   3. When complete: ./scripts/tasks.py complete {task_id}")


def complete_task(task_id: str) -> None:
    """
    Transition task from PROGRESS → DONE.

    Args:
        task_id: Task ID (e.g., "P0T0")

    Raises:
        ValueError: If task doesn't exist or not in PROGRESS state
    """
    # Find PROGRESS file
    progress_file = get_task_file(task_id, "PROGRESS")
    if not progress_file:
        raise ValueError(f"Task {task_id}_PROGRESS.md not found. Has it already been completed?")

    # Parse metadata to get started date
    fm = parse_front_matter(progress_file)
    started = fm.get("started", "")
    _created = fm.get("created", "")

    # New DONE file path
    done_file = TASKS_DIR / f"{task_id}_DONE.md"

    # Git rename
    subprocess.run(["git", "mv", str(progress_file), str(done_file)], check=True)

    # Calculate duration
    today = date.today().isoformat()
    duration = "X days"
    if started:
        try:
            start_date = datetime.fromisoformat(started).date()
            end_date = date.today()
            days = (end_date - start_date).days
            duration = f"{days} days"
        except ValueError as e:
            print(f"Warning: Could not parse start date '{started}': {e}")

    # Update front matter
    update_front_matter(done_file, {"state": "DONE", "completed": today, "duration": duration})

    print(f"✅ Completed task: {task_id}")
    print(f"   File renamed: {progress_file.name} → {done_file.name}")
    print("   State: PROGRESS → DONE")
    print(f"   Completed: {today}")
    print(f"   Duration: {duration}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {done_file} to document completion details")
    print("   2. Update docs/TASKS/INDEX.md")
    print("   3. Update docs/GETTING_STARTED/PROJECT_STATUS.md")
    print("   Or run: ./scripts/tasks.py sync-status")


def create_phase(phase: str, source: str | None = None, interactive: bool = True) -> None:
    """
    Create a phase planning document (Px_PLANNING.md).

    Args:
        phase: Phase ID (P0, P1, P2)
        source: Optional path to master plan (e.g., docs/PLANNING/trading_platform_realization_plan.md)
        interactive: If True, prompt user for manual extraction

    Raises:
        ValueError: If phase planning already exists or invalid phase format
    """
    # Validate phase ID
    if not re.match(r"^P[0-2]$", phase):
        raise ValueError(f"Invalid phase ID: {phase}. Expected P0, P1, or P2.")

    # Check if phase planning already exists
    phase_file = TASKS_DIR / f"{phase}_PLANNING.md"
    if phase_file.exists():
        raise ValueError(f"Phase planning already exists: {phase_file}")

    # Phase descriptions
    phase_info = {
        "P0": ("MVP Core", "0-45 days", "Core functionality and end-to-end pipeline"),
        "P1": (
            "Hardening & Automation",
            "46-90 days",
            "Production readiness and advanced features",
        ),
        "P2": ("Advanced Features", "91-120 days", "Optimization and scaling"),
    }

    phase_name, timeline, description = phase_info[phase]

    if source:
        # Try to extract from source
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        print(f"📖 Reading master plan from {source}...")
        with open(source_path, encoding="utf-8") as f:
            _master_content = f.read()

        print("\n⚠️  Automatic extraction not yet implemented.")
        print(f"   Please manually extract {phase} content from {source}")
        print(f"   and paste into {phase_file}")

    # Read template
    if not TEMPLATE_PHASE_PLANNING.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE_PHASE_PLANNING}")

    with open(TEMPLATE_PHASE_PLANNING, encoding="utf-8") as f:
        template_content = f.read()

    # Replace placeholders
    today = date.today().isoformat()
    replacements = {
        "P0": phase,
        "MVP Core": phase_name,
        "0-45 days": timeline,
        "YYYY-MM-DD": today,
    }

    new_content = template_content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)

    # Write phase planning file
    with open(phase_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Created phase planning: {phase_file}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {phase_file} to fill in phase details")
    print("   2. Add task breakdown (T0, T1, T2, ...)")
    print(f"   3. When ready: ./scripts/tasks.py generate-tasks-from-phase {phase}")


def generate_tasks_from_phase(phase: str, dry_run: bool = False) -> None:
    """
    Generate individual task files (PxTy_TASK.md) from phase planning document.

    Args:
        phase: Phase ID (P0, P1, P2)
        dry_run: If True, show what would be created without creating files

    Raises:
        ValueError: If phase planning doesn't exist
    """
    # Find phase planning file
    phase_file = TASKS_DIR / f"{phase}_PLANNING.md"
    if not phase_file.exists():
        raise ValueError(f"Phase planning not found: {phase_file}")

    print(f"📖 Reading phase planning from {phase_file}...")

    with open(phase_file, encoding="utf-8") as f:
        content = f.read()

    # Parse tasks from phase planning
    # Look for task headers like "#### T1.1:" or "#### T0:" or "### T1:"
    task_pattern = r"^#{3,4}\s+(T\d+(?:\.\d+)?)[:\s]+(.+)$"
    tasks_to_create = []

    for line in content.split("\n"):
        match = re.match(task_pattern, line)
        if match:
            task_num_raw = match.group(1)  # e.g., "T1.1" or "T0" or "T2.3"
            task_title = match.group(2).strip()

            # Parse task number with optional feature sub-number
            # T1.1 → Task 1, Feature 1 → P1T1-F1
            # T1 → Task 1 → P1T1
            # T2.3 → Task 2, Feature 3 → P1T2-F3
            task_match = re.match(r"T(\d+)(?:\.(\d+))?", task_num_raw)
            if task_match:
                task_num = task_match.group(1)
                feature_num = task_match.group(2)  # May be None

                if feature_num:
                    # T1.1 → P1T1-F1 (feature format)
                    task_id = f"{phase}T{task_num}-F{feature_num}"
                else:
                    # T1 → P1T1 (regular task format)
                    task_id = f"{phase}T{task_num}"

                # Check if task already exists
                if not get_task_file(task_id):
                    tasks_to_create.append((task_id, task_title))

    if not tasks_to_create:
        print(f"⚠️  No new tasks found to create in {phase_file}")
        print("   All tasks may already exist, or no task headers found.")
        return

    print(f"\n📋 Found {len(tasks_to_create)} task(s) to create:")
    for task_id, title in tasks_to_create:
        status = "✅ Would create" if dry_run else "✅ Creating"
        print(f"   {status}: {task_id} - {title}")

    if dry_run:
        print("\n💡 Run without --dry-run to create these tasks")
        return

    # Create tasks
    created_count = 0
    for task_id, title in tasks_to_create:
        try:
            # Check if this is a feature (contains "-F")
            is_feature = "-F" in task_id

            if is_feature:
                # For features, use create_feature function
                # Extract: P1T1-F1 → phase=P1, parent_task=P1T1, feature_num=F1
                feature_match = re.match(r"^(P\d+)(T\d+)-(F\d+)$", task_id)
                if not feature_match:
                    print(f"⚠️  Skipping invalid feature ID: {task_id}")
                    continue

                task_phase = feature_match.group(1)
                parent_task = feature_match.group(1) + feature_match.group(2)
                feature_id = task_id

                # Create feature file
                create_feature(
                    feature_id=feature_id,
                    title=title,
                    owner="@development-team",
                    parent_task=parent_task,
                    phase=task_phase,
                )
            else:
                # Regular task creation
                phase_match = re.match(r"^(P\d+)", task_id)
                if not phase_match:
                    print(f"⚠️  Skipping invalid task ID: {task_id}")
                    continue

                task_phase = phase_match.group(1)

                # Create task using existing create_task function
                create_task(
                    task_id=task_id,
                    title=title,
                    owner="@development-team",
                    phase=task_phase,
                    effort="X days",
                )
            created_count += 1
        except ValueError as e:
            print(f"⚠️  Failed to create {task_id}: {e}")

    print(f"\n✅ Created {created_count}/{len(tasks_to_create)} task(s)")
    print("\n📝 Next steps:")
    print("   1. Edit each task file to add details")
    if tasks_to_create:
        print(f"   2. Start first task: ./scripts/tasks.py start {tasks_to_create[0][0]}")
    else:
        print(
            f"   2. All tasks already exist, check task status with: ./scripts/tasks.py list --phase {phase}"
        )


def lint_tasks() -> int:
    """
    Lint all task files for metadata completeness and consistency.

    Returns:
        Number of errors found
    """
    tasks = list_tasks()
    errors = 0

    print("🔍 Linting task files...\n")

    # Check for required front matter fields
    required_fields = ["id", "title", "phase", "task", "priority", "owner", "state", "created"]

    for task in tasks:
        file_path = task.file_path
        if not file_path:
            continue

        fm = parse_front_matter(file_path)

        # Check required fields
        for field in required_fields:
            if field not in fm or not fm[field]:
                print(f"❌ {file_path.name}: Missing required field '{field}'")
                errors += 1

        # State-specific checks
        if task.state == "PROGRESS":
            if not fm.get("started"):
                print(f"❌ {file_path.name}: PROGRESS task missing 'started' date")
                errors += 1

        if task.state == "DONE":
            if not fm.get("completed"):
                print(f"❌ {file_path.name}: DONE task missing 'completed' date")
                errors += 1
            if not fm.get("duration"):
                print(f"❌ {file_path.name}: DONE task missing 'duration'")
                errors += 1

        # Check for duplicate state files
        task_id = task.id
        state_files = []
        for s in ["TASK", "PROGRESS", "DONE"]:
            f = TASKS_DIR / f"{task_id}_{s}.md"
            if f.exists():
                state_files.append(f.name)

        if len(state_files) > 1:
            print(f"❌ {task_id}: Multiple state files found: {', '.join(state_files)}")
            errors += 1

    if errors == 0:
        print("✅ All task files passed linting!")
    else:
        print(f"\n❌ Found {errors} error(s)")

    return errors


def sync_status() -> None:
    """
    Synchronize INDEX.md and PROJECT_STATUS.md with current task states.
    """
    print("🔄 Syncing task status...\n")

    tasks = list_tasks()

    # Count by phase and state
    counts: dict[str, dict[str, int]] = {
        "P0": {"TASK": 0, "PROGRESS": 0, "DONE": 0, "total": 0},
        "P1": {"TASK": 0, "PROGRESS": 0, "DONE": 0, "total": 0},
        "P2": {"TASK": 0, "PROGRESS": 0, "DONE": 0, "total": 0},
    }

    for task in tasks:
        if task.phase in counts:
            counts[task.phase][task.state] += 1
            counts[task.phase]["total"] += 1

    # Print summary
    print("📊 Task Summary:")
    print()
    print("| Phase | Tasks | TASK | PROGRESS | DONE | % Complete |")
    print("|-------|-------|------|----------|------|------------|")

    total_tasks = 0
    total_done = 0

    for phase in ["P0", "P1", "P2"]:
        c = counts[phase]
        total = c["total"]
        done = c["DONE"]
        pct = int(100 * done / total) if total > 0 else 0

        total_tasks += total
        total_done += done

        print(
            f"| {phase} | {total:5} | {c['TASK']:4} | {c['PROGRESS']:8} | {c['DONE']:4} | {pct:9}% |"
        )

    overall_pct = int(100 * total_done / total_tasks) if total_tasks > 0 else 0
    print(
        f"| **Total** | **{total_tasks:3}** | **{sum(c['TASK'] for c in counts.values()):2}** | **{sum(c['PROGRESS'] for c in counts.values()):6}** | **{sum(c['DONE'] for c in counts.values()):2}** | **{overall_pct:7}%** |"
    )

    print(f"\n✅ Overall progress: {overall_pct}% ({total_done}/{total_tasks} tasks complete)")
    print("\n💡 Note: Auto-generation of INDEX.md and PROJECT_STATUS.md coming soon!")


def next_task(phase: str | None = None) -> None:
    """
    Show the next task to work on.

    Priority:
    1. First PROGRESS task (current work in progress)
    2. If none, first TASK file (next pending task)

    Args:
        phase: Optional phase filter (P0, P1, P2)
    """
    # Check for tasks in PROGRESS state
    progress_tasks = list_tasks(state="PROGRESS", phase=phase)

    if progress_tasks:
        task = progress_tasks[0]
        print("🔄 Current work in progress:\n")
        print(f"ID:       {task.id}")
        print(f"Title:    {task.title}")
        print(f"Phase:    {task.phase}")
        print(f"Owner:    {task.owner}")
        print(f"State:    {task.state}")
        print(f"Started:  {task.started}")
        print(f"File:     {task.file_path}")
        print(f"\n📝 To complete: ./scripts/tasks.py complete {task.id}")
        return

    # No PROGRESS tasks, check for TASK files
    pending_tasks = list_tasks(state="TASK", phase=phase)

    if pending_tasks:
        task = pending_tasks[0]
        print("📋 Next pending task:\n")
        print(f"ID:       {task.id}")
        print(f"Title:    {task.title}")
        print(f"Phase:    {task.phase}")
        print(f"Owner:    {task.owner}")
        print(f"Priority: {task.priority}")
        print(f"File:     {task.file_path}")
        print(f"\n📝 To start: ./scripts/tasks.py start {task.id}")
        return

    # No tasks found
    phase_str = f" in phase {phase}" if phase else ""
    print(f"✅ No pending tasks{phase_str}!")
    print("\n💡 All tasks are complete or no tasks exist.")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Task Lifecycle Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show next task to work on
  ./scripts/tasks.py next
  ./scripts/tasks.py next --phase P1

  # Create new task
  ./scripts/tasks.py create P0T5 --title "Data ETL" --owner "@alice" --phase P0

  # Start task (TASK → PROGRESS)
  ./scripts/tasks.py start P0T5

  # Complete task (PROGRESS → DONE)
  ./scripts/tasks.py complete P0T5

  # List tasks
  ./scripts/tasks.py list --state PROGRESS
  ./scripts/tasks.py list --phase P0

  # Sync status
  ./scripts/tasks.py sync-status

  # Lint tasks
  ./scripts/tasks.py lint
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Next command
    next_parser = subparsers.add_parser("next", help="Show next task to work on")
    next_parser.add_argument("--phase", choices=["P0", "P1", "P2"], help="Filter by phase")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create new task from template")
    create_parser.add_argument("task_id", help="Task ID (e.g., P0T5)")
    create_parser.add_argument("--title", required=True, help="Task title")
    create_parser.add_argument("--owner", required=True, help="Task owner (e.g., @alice)")
    create_parser.add_argument("--phase", required=True, choices=["P0", "P1", "P2"], help="Phase")
    create_parser.add_argument("--effort", default="X days", help="Estimated effort")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start task (TASK → PROGRESS)")
    start_parser.add_argument("task_id", help="Task ID (e.g., P0T0)")

    # Complete command
    complete_parser = subparsers.add_parser("complete", help="Complete task (PROGRESS → DONE)")
    complete_parser.add_argument("task_id", help="Task ID (e.g., P0T0)")

    # List command
    list_parser = subparsers.add_parser("list", help="List tasks")
    list_parser.add_argument(
        "--state", choices=["TASK", "PROGRESS", "DONE"], help="Filter by state"
    )
    list_parser.add_argument("--phase", choices=["P0", "P1", "P2"], help="Filter by phase")

    # Sync command
    subparsers.add_parser("sync-status", help="Sync INDEX.md and PROJECT_STATUS.md")

    # Lint command
    subparsers.add_parser("lint", help="Lint task files for completeness")

    # Create-phase command
    create_phase_parser = subparsers.add_parser(
        "create-phase", help="Create phase planning document"
    )
    create_phase_parser.add_argument("phase", choices=["P0", "P1", "P2"], help="Phase ID")
    create_phase_parser.add_argument(
        "--source",
        help="Path to master plan (e.g., docs/PLANNING/trading_platform_realization_plan.md)",
    )

    # Generate-tasks-from-phase command
    gen_tasks_parser = subparsers.add_parser(
        "generate-tasks-from-phase", help="Generate task files from phase planning"
    )
    gen_tasks_parser.add_argument("phase", choices=["P0", "P1", "P2"], help="Phase ID")
    gen_tasks_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be created"
    )

    args = parser.parse_args()

    try:
        if args.command == "next":
            next_task(phase=args.phase)

        elif args.command == "create":
            create_task(args.task_id, args.title, args.owner, args.phase, args.effort)

        elif args.command == "start":
            start_task(args.task_id)

        elif args.command == "complete":
            complete_task(args.task_id)

        elif args.command == "list":
            tasks = list_tasks(state=args.state, phase=args.phase)  # type: ignore

            if not tasks:
                print("No tasks found matching criteria")
                return

            print(f"Found {len(tasks)} task(s):\n")
            print(f"{'ID':<10} {'State':<12} {'Title':<40} {'Owner':<15}")
            print("-" * 80)

            for task in tasks:
                print(f"{task.id:<10} {task.state:<12} {task.title:<40} {task.owner:<15}")

        elif args.command == "sync-status":
            sync_status()

        elif args.command == "lint":
            errors = lint_tasks()
            sys.exit(1 if errors > 0 else 0)

        elif args.command == "create-phase":
            create_phase(args.phase, source=args.source)

        elif args.command == "generate-tasks-from-phase":
            generate_tasks_from_phase(args.phase, dry_run=args.dry_run)

        else:
            parser.print_help()

    except (ValueError, FileNotFoundError) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"❌ Git command failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
