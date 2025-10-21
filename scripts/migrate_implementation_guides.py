#!/usr/bin/env python3
"""
Migrate IMPLEMENTATION_GUIDES to new task lifecycle format.

Converts old implementation guides to PxTy_DONE.md format with proper front matter.
"""

import re
from pathlib import Path
from datetime import date

PROJECT_ROOT = Path(__file__).parent.parent
IMPL_GUIDES = PROJECT_ROOT / "docs" / "IMPLEMENTATION_GUIDES"
TASKS_DIR = PROJECT_ROOT / "docs" / "TASKS"
CONCEPTS_DIR = PROJECT_ROOT / "docs" / "CONCEPTS"

# Migration mapping
MIGRATION_MAP = {
    "p0t1-data-etl.md": ("P0T1", "Data ETL Pipeline", "P0", "T1"),
    "p0t2-baseline-strategy.md": ("P0T2", "Baseline Qlib Strategy", "P0", "T2"),
    "p0t3-signal-service.md": ("P0T3", "Signal Service", "P0", "T3"),
    "p0t3-p4-fastapi-application.md": ("P0T3-F4", "FastAPI Application Framework", "P0", "T3", "F4"),
    "p0t3-p5-hot-reload.md": ("P0T3-F5", "Model Hot Reload", "P0", "T3", "F5"),
    "p0t3-p6-integration-tests.md": ("P0T3-F6", "Integration Tests", "P0", "T3", "F6"),
    "p0t4-execution-gateway.md": ("P0T4", "Execution Gateway", "P0", "T4"),
    "p0t5-orchestrator.md": ("P0T5", "Trade Orchestrator", "P0", "T5"),
    "p0t6-paper-run.md": ("P0T6", "Paper Trading Runner", "P0", "T6"),
    "p1.1t2-redis-integration.md": ("P1T2", "Redis Integration", "P1", "T2"),
    "p1.1t3-duckdb-analytics.md": ("P1T3", "DuckDB Analytics", "P1", "T3"),
    "p1.1t4-timezone-timestamps.md": ("P1T4", "Timezone & Timestamps", "P1", "T4"),
    "p1.1t5-operational-status.md": ("P1T5", "Operational Status Dashboard", "P1", "T5"),
    "p1.2t1-realtime-market-data-phase1.md": ("P1T1-F1", "Real-time Market Data - Phase 1", "P1", "T1", "F1"),
    "p1.2t1-realtime-market-data-phase3.md": ("P1T1-F3", "Real-time Market Data - Phase 3", "P1", "T1", "F3"),
    "p1.2t3-risk-management.md": ("P1T6", "Risk Management System", "P1", "T6"),
}

# Special files to move to CONCEPTS
SPECIAL_FILES = {
    "workflow-optimization-zen-mcp.md": "workflow-optimization-zen-mcp.md",
    "zen-mcp-integration-proposal.md": "zen-mcp-integration-proposal.md",
}


def extract_title_from_content(content: str) -> str:
    """Extract title from markdown content (first # heading)."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1) if match else "Untitled"


def generate_front_matter(task_id: str, title: str, phase: str, task: str, feature: str = None) -> str:
    """Generate YAML front matter for DONE file."""
    today = date.today().isoformat()

    fm = f"""---
id: {task_id}
title: "{title}"
phase: {phase}
task: {task}
priority: {phase}
owner: "@development-team"
state: DONE
created: {today}
started: {today}
completed: {today}
duration: "Completed prior to task lifecycle system"
dependencies: []
related_adrs: []
related_docs: []"""

    if feature:
        fm += f"\nfeature: {feature}\nparent_task: {phase}{task}"

    fm += "\n---\n"
    return fm


def migrate_file(old_file: Path, task_info: tuple) -> None:
    """Migrate single implementation guide to new format."""
    # Read old content
    with open(old_file, "r", encoding="utf-8") as f:
        old_content = f.read()

    # Parse task info
    task_id = task_info[0]
    title = task_info[1]
    phase = task_info[2]
    task = task_info[3]
    feature = task_info[4] if len(task_info) > 4 else None

    # Generate new file path
    new_filename = f"{task_id}_DONE.md"
    new_file = TASKS_DIR / new_filename

    # Generate front matter
    front_matter = generate_front_matter(task_id, title, phase, task, feature)

    # Create new content
    # Strip old title if it exists at the top
    content_lines = old_content.split("\n")
    start_idx = 0
    for i, line in enumerate(content_lines):
        if line.strip() and not line.startswith("#"):
            start_idx = i
            break
        if line.startswith("# "):
            start_idx = i + 1
            break

    body_content = "\n".join(content_lines[start_idx:]).strip()

    # Build new document
    new_content = f"""{front_matter}

# {task_id}: {title} ✅

**Phase:** {phase} ({'MVP Core, 0-45 days' if phase == 'P0' else 'Hardening & Automation, 46-90 days' if phase == 'P1' else 'Advanced Features, 91-120 days'})
**Status:** DONE (Completed prior to task lifecycle system)
**Priority:** {phase}
**Owner:** @development-team

---

## Original Implementation Guide

**Note:** This content was migrated from `docs/IMPLEMENTATION_GUIDES/{old_file.name}`
and represents work completed before the task lifecycle management system was implemented.

---

{body_content}

---

## Migration Notes

**Migrated:** {date.today().isoformat()}
**Original File:** `docs/IMPLEMENTATION_GUIDES/{old_file.name}`
**Migration:** Automated migration to task lifecycle system

**Historical Context:**
This task was completed before the PxTy_TASK → _PROGRESS → _DONE lifecycle
system was introduced. The content above represents the implementation guide
that was created during development.

For new tasks, use the structured DONE template with:
- Summary of what was built
- Code references
- Test coverage details
- Zen-MCP review history
- Lessons learned
- Metrics

See `docs/TASKS/00-TEMPLATE_DONE.md` for the current standard format.
"""

    # Write new file
    with open(new_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Migrated: {old_file.name} → {new_filename}")


def migrate_special_file(old_file: Path, new_name: str) -> None:
    """Move special files to CONCEPTS directory."""
    new_file = CONCEPTS_DIR / new_name

    # Simply copy content (these are concept docs, not task implementations)
    with open(old_file, "r", encoding="utf-8") as f:
        content = f.read()

    with open(new_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ Moved: {old_file.name} → docs/CONCEPTS/{new_name}")


def main():
    """Main migration function."""
    print("🔄 Starting IMPLEMENTATION_GUIDES migration...\n")

    # Ensure CONCEPTS dir exists
    CONCEPTS_DIR.mkdir(exist_ok=True)

    migrated_count = 0
    special_count = 0

    # Migrate regular task files
    print("📋 Migrating task implementation guides:\n")
    for old_name, task_info in MIGRATION_MAP.items():
        old_file = IMPL_GUIDES / old_name
        if old_file.exists():
            migrate_file(old_file, task_info)
            migrated_count += 1
        else:
            print(f"⚠️  File not found: {old_name}")

    # Move special files
    print("\n📚 Moving special files to CONCEPTS:\n")
    for old_name, new_name in SPECIAL_FILES.items():
        old_file = IMPL_GUIDES / old_name
        if old_file.exists():
            migrate_special_file(old_file, new_name)
            special_count += 1
        else:
            print(f"⚠️  File not found: {old_name}")

    print(f"\n✅ Migration complete!")
    print(f"   Tasks migrated: {migrated_count}")
    print(f"   Concepts moved: {special_count}")
    print(f"\n📝 Next steps:")
    print(f"   1. Review migrated files in docs/TASKS/")
    print(f"   2. Update cross-references if needed")
    print(f"   3. Run: ./scripts/tasks.py lint")
    print(f"   4. Run: ./scripts/tasks.py sync-status")
    print(f"   5. Git add and commit the migration")
    print(f"   6. Remove docs/IMPLEMENTATION_GUIDES/ directory")


if __name__ == "__main__":
    main()
