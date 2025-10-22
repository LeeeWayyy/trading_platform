#!/usr/bin/env python3
"""
Renumber phase tasks from Tx.y notation to sequential Tz numbering.

Converts track-based task numbering (T1.1, T1.2, T2.1) to sequential (T0, T1, T2...)
and updates all related files and references.

Usage:
    ./scripts/renumber_phase.py P1 --dry-run   # Preview changes
    ./scripts/renumber_phase.py P1 --apply     # Apply changes
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TASKS_DIR = PROJECT_ROOT / "docs" / "TASKS"


def parse_planning_structure(phase: str) -> dict[str, int]:
    """
    Parse Px_PLANNING.md to build mapping from Tx.y notation to sequential numbers.

    Args:
        phase: Phase ID (P0, P1, P2)

    Returns:
        Dictionary mapping "Tx.y" -> sequential_number (e.g., "T1.1" -> 0, "T1.2" -> 1)
    """
    planning_file = TASKS_DIR / f"{phase}_PLANNING.md"
    if not planning_file.exists():
        raise FileNotFoundError(f"Planning file not found: {planning_file}")

    with open(planning_file, encoding="utf-8") as f:
        content = f.read()

    # Find all task headers with Tx.y notation
    # Pattern: #### T1.1: Task Title or ### T1.1: Task Title
    pattern = r"^#{3,4}\s+T(\d+)\.(\d+):\s+(.+)$"
    tasks_by_track: dict[int, list[tuple[int, str]]] = {}

    for line in content.split("\n"):
        match = re.match(pattern, line)
        if match:
            track_num = int(match.group(1))
            task_num = int(match.group(2))
            title = match.group(2).strip()

            if track_num not in tasks_by_track:
                tasks_by_track[track_num] = []

            tasks_by_track[track_num].append((task_num, title))

    # Build mapping from Tx.y to sequential number
    mapping = {}
    sequential = 0

    for track in sorted(tasks_by_track.keys()):
        tasks = sorted(tasks_by_track[track], key=lambda x: x[0])
        for task_num, title in tasks:
            old_notation = f"T{track}.{task_num}"
            mapping[old_notation] = sequential
            sequential += 1

    return mapping


def find_existing_task_files(phase: str) -> list[Path]:
    """
    Find all existing task files for a phase.

    Args:
        phase: Phase ID (P0, P1, P2)

    Returns:
        List of Path objects for task files
    """
    pattern = f"{phase}T*_*.md"
    files = list(TASKS_DIR.glob(pattern))

    # Exclude PLANNING file
    files = [f for f in files if not f.name.endswith("_PLANNING.md")]

    return sorted(files)


def extract_task_id_from_filename(filename: str) -> str:
    """
    Extract task ID from filename.

    Examples:
        P1T2_DONE.md -> P1T2
        P1T1-F1_PROGRESS.md -> P1T1-F1
    """
    match = re.match(r"(P\d+T\d+(?:-F\d+)?)_", filename)
    if match:
        return match.group(1)
    return ""


def build_renaming_map(
    phase: str, notation_map: dict[str, int], existing_files: list[Path]
) -> dict[str, str]:
    """
    Build mapping of old task IDs to new task IDs.

    Args:
        phase: Phase ID
        notation_map: Tx.y -> sequential number mapping
        existing_files: List of existing task files

    Returns:
        Dictionary mapping old_task_id -> new_task_id (e.g., "P1T2" -> "P1T1")
    """
    # First, infer which Tx.y notation each current file represents
    # by reading the title and matching it to the planning doc

    # For now, use a simple heuristic:
    # P1T1-F1 likely came from T2.1 (Real-time features in Track 2)
    # P1T2-P1T5 likely came from T1.2-T1.5 (Track 1 tasks)
    # P1T6 likely came from T2.3 (Track 2 task)

    renaming = {}

    for file_path in existing_files:
        old_id = extract_task_id_from_filename(file_path.name)
        if not old_id:
            continue

        # Read title from file to match against planning
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        title_match = re.search(r'^title:\s*"(.+)"', content, re.MULTILINE)
        if not title_match:
            print(f"⚠️  Could not find title in {file_path.name}")
            continue

        title = title_match.group(1)

        # Match title to notation in planning file
        # Read planning again to find title match
        planning_file = TASKS_DIR / f"{phase}_PLANNING.md"
        with open(planning_file, encoding="utf-8") as f:
            planning_content = f.read()

        # Find the Tx.y notation for this title
        pattern = r"^#{3,4}\s+(T\d+\.\d+):\s+(.+)$"
        for line in planning_content.split("\n"):
            match = re.match(pattern, line)
            if match:
                notation = match.group(1)
                plan_title = match.group(2).strip()

                # Check if titles match (fuzzy match for emojis/symbols, punctuation)
                # Remove emojis, punctuation, extra spaces, normalize case
                def normalize_title(t):
                    t = re.sub(r"[⭐🔶🔷]", "", t)  # Remove priority symbols
                    t = re.sub(r"[&-]", " ", t)  # Replace & and - with space
                    t = re.sub(r"\s+", " ", t)  # Normalize whitespace
                    return t.strip().lower()

                plan_title_clean = normalize_title(plan_title)
                file_title_clean = normalize_title(title)

                # Extract key words for matching
                plan_words = set(plan_title_clean.split())
                file_words = set(file_title_clean.split())

                # Match if significant overlap (at least 2 common words, or substring match)
                common_words = plan_words & file_words
                if (
                    len(common_words) >= 2
                    or plan_title_clean in file_title_clean
                    or file_title_clean in plan_title_clean
                ):
                    # Found the match!
                    sequential_num = notation_map.get(notation)
                    if sequential_num is not None:
                        # Build new task ID
                        if "-F" in old_id:
                            # Feature: P1T1-F1 -> P1T5-F1
                            base_id, feature = old_id.split("-")
                            new_id = f"{phase}T{sequential_num}-{feature}"
                        else:
                            # Regular task: P1T2 -> P1T1
                            new_id = f"{phase}T{sequential_num}"

                        renaming[old_id] = new_id
                        break

    return renaming


def preview_changes(renaming_map: dict[str, str], existing_files: list[Path]) -> None:
    """
    Print preview of changes that will be made.

    Args:
        renaming_map: old_id -> new_id mapping
        existing_files: List of existing files
    """
    print("\n📋 Renumbering Plan:\n")
    print(f"{'Old ID':<15} → {'New ID':<15} {'Filename':<40}")
    print("-" * 70)

    for file_path in existing_files:
        old_id = extract_task_id_from_filename(file_path.name)
        new_id = renaming_map.get(old_id, old_id)

        if old_id != new_id:
            status = "✅ RENAME"
        else:
            status = "⚠️  NO CHANGE"

        print(f"{old_id:<15} → {new_id:<15} {file_path.name:<40} {status}")

    print(f"\nTotal files: {len(existing_files)}")
    print(
        f"Files to rename: {sum(1 for f in existing_files if renaming_map.get(extract_task_id_from_filename(f.name), extract_task_id_from_filename(f.name)) != extract_task_id_from_filename(f.name))}"
    )


def apply_renaming(phase: str, renaming_map: dict[str, str], existing_files: list[Path]) -> None:
    """
    Apply renaming using git mv and update front matter.

    Args:
        phase: Phase ID
        renaming_map: old_id -> new_id mapping
        existing_files: List of existing files
    """
    renamed_count = 0

    for file_path in existing_files:
        old_id = extract_task_id_from_filename(file_path.name)
        new_id = renaming_map.get(old_id)

        if not new_id or old_id == new_id:
            continue

        # Build new filename
        new_filename = file_path.name.replace(old_id, new_id)
        new_path = TASKS_DIR / new_filename

        # Git rename
        print(f"🔄 Renaming: {file_path.name} → {new_filename}")
        subprocess.run(["git", "mv", str(file_path), str(new_path)], check=True)

        # Update front matter (id field)
        with open(new_path, encoding="utf-8") as f:
            content = f.read()

        # Update id field
        updated_content = re.sub(
            r"^id:\s*" + re.escape(old_id) + r"\s*$", f"id: {new_id}", content, flags=re.MULTILINE
        )

        # Update title in markdown header (e.g., # P1T2: Title → # P1T1: Title)
        updated_content = re.sub(
            r"^(#\s+)" + re.escape(old_id) + r":",
            r"\1" + new_id + ":",
            updated_content,
            flags=re.MULTILINE,
        )

        with open(new_path, "w", encoding="utf-8") as f:
            f.write(updated_content)

        renamed_count += 1

    print(f"\n✅ Renamed {renamed_count} file(s)")


def update_planning_file(phase: str, notation_map: dict[str, int]) -> None:
    """
    Rewrite Px_PLANNING.md to use sequential task numbers.

    Args:
        phase: Phase ID
        notation_map: Tx.y -> sequential number mapping
    """
    planning_file = TASKS_DIR / f"{phase}_PLANNING.md"

    with open(planning_file, encoding="utf-8") as f:
        content = f.read()

    # Replace all Tx.y: with Tz:
    # Pattern: #### T1.1: Title -> #### T0: Title
    def replace_notation(match):
        prefix = match.group(1)  # #### or ###
        notation = f"T{match.group(2)}.{match.group(3)}"  # T1.1
        rest = match.group(4)  # : Title...

        sequential_num = notation_map.get(notation)
        if sequential_num is not None:
            return f"{prefix}T{sequential_num}{rest}"
        else:
            return match.group(0)  # No change if not in map

    pattern = r"^(#{3,4}\s+)T(\d+)\.(\d+)(:\s+.+)$"
    updated_content = re.sub(pattern, replace_notation, content, flags=re.MULTILINE)

    with open(planning_file, "w", encoding="utf-8") as f:
        f.write(updated_content)

    print(f"✅ Updated {planning_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Renumber phase tasks from Tx.y to sequential Tz",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview changes
  ./scripts/renumber_phase.py P1 --dry-run

  # Apply renumbering
  ./scripts/renumber_phase.py P1 --apply
        """,
    )

    parser.add_argument("phase", choices=["P0", "P1", "P2"], help="Phase to renumber")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--apply", action="store_true", help="Apply renumbering changes")

    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.error("Must specify either --dry-run or --apply")

    try:
        print(f"🔍 Analyzing {args.phase}_PLANNING.md structure...")
        notation_map = parse_planning_structure(args.phase)

        print("\n📊 Task Numbering Map:")
        for notation, seq_num in sorted(notation_map.items(), key=lambda x: x[1]):
            print(f"  {notation} → T{seq_num}")

        existing_files = find_existing_task_files(args.phase)
        print(f"\n📁 Found {len(existing_files)} existing task file(s)")

        renaming_map = build_renaming_map(args.phase, notation_map, existing_files)

        preview_changes(renaming_map, existing_files)

        if args.apply:
            print("\n🚀 Applying changes...")
            apply_renaming(args.phase, renaming_map, existing_files)
            update_planning_file(args.phase, notation_map)
            print("\n✅ Renumbering complete!")
            print("\n📝 Next steps:")
            print("   1. Review changes: git status")
            print("   2. Test task CLI: ./scripts/tasks.py list --phase " + args.phase)
            print(
                "   3. Commit: git commit -m 'Renumber "
                + args.phase
                + " tasks to sequential numbering'"
            )
        else:
            print("\n💡 Run with --apply to execute changes")

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
