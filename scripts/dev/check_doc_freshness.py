#!/usr/bin/env python3
"""
Check documentation freshness and spec coverage.

Uses git commit timestamps (not filesystem mtimes) to detect when
source directories have changed without corresponding doc updates.

Exit codes (bitmask):
    0  - All checks passed
    1  - Missing entries in REPO_MAP
    2  - Orphaned entries in REPO_MAP
    4  - Missing spec files
    8  - REPO_MAP stale (>7 days)
    16 - Spec files stale (>1 day)
    32 - Architecture config stale (>1 day)
    64 - Source file structure changed (files added/deleted since spec update)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/dev/ → scripts/ → repo_root
DOCS_DIR = PROJECT_ROOT / "docs"
REPO_MAP_PATH = Path("docs/GETTING_STARTED/REPO_MAP.md")
SPECS_DIR = DOCS_DIR / "SPECS"

EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}


class DirectoryState(TypedDict):
    """Typed structure for directory state."""

    subdirs: list[str]
    last_modified: str


class FreshnessReport(TypedDict):
    """Report from freshness check."""

    doc_path: str
    missing: list[str]
    orphaned: list[str]
    deprecated: list[str]
    stale: bool
    last_doc_update: str
    last_source_change: str
    missing_specs: list[str]
    files_added: list[str]
    files_deleted: list[str]
    files_modified: list[str]


@dataclass(frozen=True)
class GitTimestamp:
    """Parsed git timestamp with ISO string and datetime value."""

    iso: str
    dt: datetime


def normalize_path(path: str) -> str:
    """
    Normalize path to canonical format for comparison.

    Rules:
        - Strip leading "./"
        - Ensure trailing "/" for directories
        - Root-relative (no leading "/")
    """

    normalized = path.strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/"):
        normalized = normalized[1:]
    if normalized and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def _run_git_log_timestamp(path: str, fmt: str = "%cI") -> GitTimestamp:
    """Return the last git commit timestamp for a path."""

    result = subprocess.run(
        ["git", "log", "-1", f"--format={fmt}", "--", path],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    stamp = result.stdout.strip()
    if not stamp:
        return GitTimestamp("1970-01-01T00:00:00Z", datetime(1970, 1, 1, tzinfo=UTC))

    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        parsed = datetime(1970, 1, 1, tzinfo=UTC)
        stamp = "1970-01-01T00:00:00Z"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    iso = parsed.isoformat()
    return GitTimestamp(iso, parsed)


def _list_immediate_subdirs(path: Path) -> list[str]:
    """List immediate subdirectory names under path, excluding known noise."""

    if not path.exists() or not path.is_dir():
        return []
    subdirs = []
    for entry in sorted(path.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in EXCLUDED_DIRS:
            continue
        subdirs.append(entry.name)
    return subdirs


ARCH_CONFIG_PATH = Path("docs/ARCHITECTURE/system_map.config.json")


def get_source_directories() -> dict[str, list[str]]:
    """
    Returns mapping of documentation files to source directories they document.

    Special behavior:
        - If `docs/SPECS/` does not exist, SKIP all spec checks.
    """

    mapping: dict[str, list[str]] = {
        str(REPO_MAP_PATH): [
            "apps/",
            "libs/",
            "scripts/",
            "tests/",
            "config/",
            "infra/",
            "db/",
            "docs/",
            "strategies/",
            "migrations/",
            ".ai_workflow/",
            ".github/",
        ],
        # Architecture config tracks component additions/changes
        str(ARCH_CONFIG_PATH): [
            "apps/*/",
            "libs/*/",
            "strategies/*/",
        ],
    }

    if not SPECS_DIR.exists():
        return mapping

    mapping["docs/SPECS/services/*.md"] = ["apps/*/"]
    mapping["docs/SPECS/libs/*.md"] = ["libs/*/"]
    mapping["docs/SPECS/strategies/*.md"] = ["strategies/*/"]
    return mapping


def get_directory_state(path: str) -> DirectoryState:
    """
    Get current state of directory using git commit timestamps.
    """

    normalized = normalize_path(path)
    dir_path = PROJECT_ROOT / normalized
    subdirs = _list_immediate_subdirs(dir_path)
    last_modified = _run_git_log_timestamp(normalized).iso
    return {"subdirs": subdirs, "last_modified": last_modified}


def _extract_directory_tokens(line: str) -> list[str]:
    """Extract directory-like tokens from a line."""

    tokens: list[str] = []
    for match in re.finditer(r"(?:\.?/?)[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/", line):
        token = match.group(0)
        if "://" in token:
            continue
        if token.startswith("http"):
            continue
        tokens.append(token)
    return tokens


def parse_documented_entries(doc_path: str) -> tuple[set[str], set[str]]:
    """
    Parse documentation file to extract documented directory names.

    Returns:
        Tuple of (active_entries, deprecated_entries)
    """

    active: set[str] = set()
    deprecated: set[str] = set()
    doc_file = PROJECT_ROOT / doc_path
    if not doc_file.exists():
        return active, deprecated

    with open(doc_file, encoding="utf-8") as handle:
        for line in handle:
            entries = _extract_directory_tokens(line)
            if not entries:
                continue
            if "[DEPRECATED]" in line:
                for entry in entries:
                    deprecated.add(normalize_path(entry))
            else:
                for entry in entries:
                    active.add(normalize_path(entry))

    return active, deprecated


def _expand_source_patterns(source_dirs: Iterable[str]) -> set[str]:
    """Expand source directory patterns to actual directories."""

    expanded: set[str] = set()
    for raw in source_dirs:
        pattern = normalize_path(raw)
        if pattern.endswith("*/"):
            parent = pattern[:-2]
            parent_path = PROJECT_ROOT / parent
            for name in _list_immediate_subdirs(parent_path):
                expanded.add(normalize_path(f"{parent}{name}/"))
        else:
            dir_path = PROJECT_ROOT / pattern
            if dir_path.exists() and dir_path.is_dir():
                expanded.add(pattern)
    return expanded


def _filter_doc_entries_to_scope(entries: set[str], source_dirs: Iterable[str]) -> set[str]:
    """Filter documented entries to only those in scope of source directory patterns."""

    scoped: set[str] = set()
    for entry in entries:
        for raw in source_dirs:
            pattern = normalize_path(raw)
            if pattern.endswith("*/"):
                parent = pattern[:-2]
                if not entry.startswith(parent):
                    continue
                remainder = entry[len(parent) :].strip("/")
                if remainder and "/" not in remainder:
                    scoped.add(entry)
            else:
                if entry == pattern:
                    scoped.add(entry)
    return scoped


def _max_git_timestamp(paths: Iterable[str]) -> GitTimestamp:
    """Return the most recent git timestamp among paths."""

    most_recent = GitTimestamp("1970-01-01T00:00:00Z", datetime(1970, 1, 1, tzinfo=UTC))
    for path in paths:
        stamp = _run_git_log_timestamp(path)
        if stamp.dt > most_recent.dt:
            most_recent = stamp
    return most_recent


def _is_path_dirty(path: Path) -> bool:
    """Return True if path has uncommitted changes (staged or unstaged)."""

    result = subprocess.run(
        ["git", "status", "--porcelain", "--", str(path)],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return bool(result.stdout.strip())


def check_freshness(doc_path: str, source_dirs: list[str]) -> FreshnessReport:
    """
    Compare documented entries against actual directory state.
    """

    active_entries, deprecated_entries = parse_documented_entries(doc_path)
    actual_dirs = _expand_source_patterns(source_dirs)

    scoped_active = _filter_doc_entries_to_scope(active_entries, source_dirs)
    scoped_deprecated = _filter_doc_entries_to_scope(deprecated_entries, source_dirs)

    missing = sorted(actual_dirs - scoped_active)

    orphaned_candidates = scoped_active - actual_dirs
    orphaned = sorted(entry for entry in orphaned_candidates if entry not in scoped_deprecated)
    deprecated = sorted(scoped_deprecated)

    doc_ts = _run_git_log_timestamp(doc_path)
    source_ts = _max_git_timestamp(actual_dirs)
    stale = doc_ts.dt < source_ts.dt

    return {
        "doc_path": doc_path,
        "missing": missing,
        "orphaned": orphaned,
        "deprecated": deprecated,
        "stale": stale,
        "last_doc_update": doc_ts.iso,
        "last_source_change": source_ts.iso,
        "missing_specs": [],
        "files_added": [],
        "files_deleted": [],
        "files_modified": [],
    }


def _expected_spec_files() -> tuple[list[str], list[str]]:
    """Return expected spec files and missing ones based on current source dirs."""

    expected: list[str] = []
    missing: list[str] = []

    def add_expected(spec_root: Path, source_root: Path) -> None:
        for name in _list_immediate_subdirs(source_root):
            spec_path = spec_root / f"{name}.md"
            expected.append(str(spec_path.relative_to(PROJECT_ROOT)))
            if not spec_path.exists():
                missing.append(str(spec_path.relative_to(PROJECT_ROOT)))

    add_expected(SPECS_DIR / "services", PROJECT_ROOT / "apps")
    add_expected(SPECS_DIR / "libs", PROJECT_ROOT / "libs")
    add_expected(SPECS_DIR / "strategies", PROJECT_ROOT / "strategies")

    return expected, missing


def _days_between(older: datetime, newer: datetime) -> int:
    """Return whole days between two datetimes."""

    delta = newer - older
    return int(delta.total_seconds() // 86400)


def _spec_source_dir(spec_path: Path) -> str | None:
    """Return the documented source directory for a spec file."""

    try:
        relative = spec_path.relative_to(PROJECT_ROOT / "docs" / "SPECS")
    except ValueError:
        return None

    if len(relative.parts) != 2:
        return None
    category, filename = relative.parts
    name = filename.removesuffix(".md")
    if not name:
        return None

    if category == "services":
        return normalize_path(f"apps/{name}")
    if category == "libs":
        return normalize_path(f"libs/{name}")
    if category == "strategies":
        return normalize_path(f"strategies/{name}")
    return None


def _get_uncommitted_changes(source_dir: str) -> tuple[list[str], list[str], list[str]]:
    """Get uncommitted file changes (staged and unstaged) in source_dir.

    Args:
        source_dir: Source directory path (e.g., "apps/web_console_ng/")

    Returns:
        Tuple of (added_files, deleted_files, modified_files) that are uncommitted
    """
    added: list[str] = []
    deleted: list[str] = []
    modified: list[str] = []

    # Get uncommitted changes (staged and unstaged)
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", source_dir],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    # Use rstrip() not strip() to preserve leading spaces in status codes
    # Git status format: "XY filepath" where X/Y can be ' ' (space)
    for line in result.stdout.rstrip().split("\n"):
        if not line:
            continue
        # Status is first 2 chars: XY where X=staged, Y=unstaged
        # A = added, D = deleted, M = modified, R = renamed, ? = untracked
        status = line[:2]
        filepath = line[3:]  # Skip status and space

        # Handle renamed files (R  old -> new)
        # A rename is treated as deletion of old path + addition of new path
        if "R" in status:
            try:
                old_path, new_path = filepath.split(" -> ")
                # Git may quote paths with spaces, so strip them
                old_path = old_path.strip('"')
                new_path = new_path.strip('"')
                if old_path.endswith(".py"):
                    deleted.append(old_path)
                if new_path.endswith(".py"):
                    added.append(new_path)
            except ValueError:
                # Unparseable rename line, skip
                pass
            continue

        # Only track Python files for other statuses
        if not filepath.endswith(".py"):
            continue

        # Check for new files (staged 'A' or untracked '?')
        if status[0] == "A" or status == "??":
            added.append(filepath)
        # Check for deleted files
        elif status[0] == "D" or status[1] == "D":
            deleted.append(filepath)
        # Check for modified files
        elif status[0] == "M" or status[1] == "M":
            modified.append(filepath)

    return added, deleted, modified


def _get_files_changed_since(source_dir: str, since_commit: str) -> tuple[list[str], list[str]]:
    """Get files added and deleted in source_dir since a given commit.

    Args:
        source_dir: Source directory path (e.g., "apps/web_console_ng/")
        since_commit: Git commit hash or ref to compare against

    Returns:
        Tuple of (added_files, deleted_files) relative to source_dir
    """
    added: list[str] = []
    deleted: list[str] = []

    # Get diff of files changed since the commit
    result = subprocess.run(
        ["git", "diff", "--name-status", since_commit, "HEAD", "--", source_dir],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, filepath = parts
        # Only track Python files for now
        if not filepath.endswith(".py"):
            continue
        # Get path relative to source_dir
        rel_path = filepath
        if status == "A":
            added.append(rel_path)
        elif status == "D":
            deleted.append(rel_path)

    return added, deleted


def _get_commit_at_doc_update(doc_path: str) -> str | None:
    """Get the git commit hash when the doc was last updated."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", doc_path],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    commit = result.stdout.strip()
    return commit if commit else None


def check_file_structure_changes(
    spec_path: str, source_dir: str
) -> tuple[list[str], list[str], list[str]]:
    """Check if Python files were added/deleted/modified since spec was last updated.

    Includes both committed changes since spec update AND uncommitted changes
    in the working directory.

    Args:
        spec_path: Path to spec file (e.g., "docs/SPECS/services/web_console_ng.md")
        source_dir: Source directory (e.g., "apps/web_console_ng/")

    Returns:
        Tuple of (added_files, deleted_files, modified_files) since spec update
    """
    spec_commit = _get_commit_at_doc_update(spec_path)

    # Get committed changes since spec update
    if spec_commit:
        committed_added, committed_deleted = _get_files_changed_since(source_dir, spec_commit)
    else:
        # Spec never committed - can't compare committed history
        committed_added, committed_deleted = [], []

    # Get uncommitted changes (staged and unstaged)
    uncommitted_added, uncommitted_deleted, uncommitted_modified = _get_uncommitted_changes(
        source_dir
    )

    # Merge committed and uncommitted changes (dedup)
    all_added = sorted(set(committed_added + uncommitted_added))
    all_deleted = sorted(set(committed_deleted + uncommitted_deleted))
    all_modified = sorted(set(uncommitted_modified))

    return all_added, all_deleted, all_modified


def _print_report(report: FreshnessReport, verbose: bool) -> None:
    """Print a human-readable report section."""

    print(f"\nDOC {report['doc_path']}")
    if report["missing"]:
        print(f"  Missing entries ({len(report['missing'])}):")
        if verbose:
            for entry in report["missing"]:
                print(f"    - {entry}")
    if report["orphaned"]:
        print(f"  Orphaned entries ({len(report['orphaned'])}):")
        if verbose:
            for entry in report["orphaned"]:
                print(f"    - {entry}")
    if report["deprecated"] and verbose:
        print(f"  Deprecated entries ({len(report['deprecated'])}):")
        for entry in report["deprecated"]:
            print(f"    - {entry}")
    if verbose:
        print(f"  Last doc update: {report['last_doc_update']}")
        print(f"  Last source change: {report['last_source_change']}")


def main() -> int:
    """Main entry point. Returns exit code."""

    parser = argparse.ArgumentParser(description="Check documentation freshness and spec coverage")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    mapping = get_source_directories()

    reports: list[FreshnessReport] = []
    exit_code = 0

    # REPO_MAP check (always)
    repo_report = check_freshness(str(REPO_MAP_PATH), mapping[str(REPO_MAP_PATH)])

    # Blocking staleness for REPO_MAP (>7 days)
    if _is_path_dirty(REPO_MAP_PATH):
        repo_report["stale"] = False
    else:
        repo_doc_ts = datetime.fromisoformat(repo_report["last_doc_update"].replace("Z", "+00:00"))
        repo_source_ts = datetime.fromisoformat(repo_report["last_source_change"].replace("Z", "+00:00"))
        repo_stale_days = _days_between(repo_doc_ts, repo_source_ts)
        if repo_stale_days > 7:
            exit_code |= 8
            repo_report["stale"] = True
        else:
            repo_report["stale"] = False

    if repo_report["missing"]:
        exit_code |= 1
    if repo_report["orphaned"]:
        exit_code |= 2

    reports.append(repo_report)

    # Architecture config check (blocking staleness >1 day)
    arch_report: FreshnessReport | None = None
    if ARCH_CONFIG_PATH.exists() and str(ARCH_CONFIG_PATH) in mapping:
        arch_report = check_freshness(str(ARCH_CONFIG_PATH), mapping[str(ARCH_CONFIG_PATH)])
        # Architecture config doesn't have missing/orphaned concept
        arch_report["missing"] = []
        arch_report["orphaned"] = []

        if _is_path_dirty(ARCH_CONFIG_PATH):
            arch_report["stale"] = False
        elif arch_report["stale"]:
            arch_doc_ts = datetime.fromisoformat(arch_report["last_doc_update"].replace("Z", "+00:00"))
            arch_source_ts = datetime.fromisoformat(arch_report["last_source_change"].replace("Z", "+00:00"))
            arch_stale_days = _days_between(arch_doc_ts, arch_source_ts)
            if arch_stale_days > 1:
                exit_code |= 32
            else:
                arch_report["stale"] = False

        reports.append(arch_report)

    # Spec checks
    missing_specs: list[str] = []
    spec_reports: list[FreshnessReport] = []
    stale_specs: list[str] = []

    if SPECS_DIR.exists():
        _, missing_specs = _expected_spec_files()
        if missing_specs:
            exit_code |= 4

        # Per-spec freshness (now blocking if stale >1 day)
        for spec_file in sorted(SPECS_DIR.rglob("*.md")):
            source_dir = _spec_source_dir(spec_file)
            if not source_dir:
                continue
            spec_rel_path = str(spec_file.relative_to(PROJECT_ROOT))
            report = check_freshness(spec_rel_path, [source_dir])
            # Spec files do not participate in missing/orphan checks.
            report["missing"] = []
            report["orphaned"] = []

            # Check for file structure changes (files added/deleted/modified since spec update)
            # Skip if spec has uncommitted changes (user is actively updating it)
            if _is_path_dirty(spec_file):
                report["files_added"] = []
                report["files_deleted"] = []
                report["files_modified"] = []
            else:
                added, deleted, modified = check_file_structure_changes(spec_rel_path, source_dir)
                report["files_added"] = added
                report["files_deleted"] = deleted
                report["files_modified"] = modified
                if added or deleted or modified:
                    exit_code |= 64

            # Check for blocking staleness (>1 day) unless spec has uncommitted changes
            if _is_path_dirty(spec_file):
                report["stale"] = False
            elif report["stale"]:
                spec_doc_ts = datetime.fromisoformat(report["last_doc_update"].replace("Z", "+00:00"))
                spec_source_ts = datetime.fromisoformat(report["last_source_change"].replace("Z", "+00:00"))
                spec_stale_days = _days_between(spec_doc_ts, spec_source_ts)
                if spec_stale_days > 1:
                    stale_specs.append(report["doc_path"])
                    exit_code |= 16
                else:
                    # Stale but within 1-day grace period
                    report["stale"] = False

            spec_reports.append(report)

    # Attach missing specs to REPO_MAP report for summary
    repo_report["missing_specs"] = sorted(missing_specs)
    # Build final reports list including arch_report if present
    reports = [repo_report] + ([arch_report] if arch_report else []) + spec_reports

    if args.json:
        payload = {
            "reports": reports,
            "exit_code": exit_code,
        }
        print(json.dumps(payload, indent=2))
        return exit_code

    print("\nDocumentation Freshness Report")
    _print_report(repo_report, args.verbose)

    # Architecture config staleness check
    if arch_report is not None:
        if arch_report["stale"]:
            print(f"\nERROR: Architecture config stale - update {ARCH_CONFIG_PATH}")
            _print_report(arch_report, args.verbose)
        elif args.verbose:
            print(f"\nArchitecture Config: {ARCH_CONFIG_PATH}")
            print(f"  Last config update: {arch_report['last_doc_update']}")
            print(f"  Last source change: {arch_report['last_source_change']}")

    if repo_report["missing_specs"]:
        print(f"\nWarning: Missing spec files ({len(repo_report['missing_specs'])}):")
        if args.verbose:
            for spec in repo_report["missing_specs"]:
                print(f"  - {spec}")

    if stale_specs:
        print(f"\nERROR: Stale spec files ({len(stale_specs)}) - update docs or source:")
        for spec in stale_specs:
            print(f"  - {spec}")

    # Report file structure changes (files added/deleted/modified since spec update)
    specs_with_changes = [
        r
        for r in spec_reports
        if r["files_added"] or r["files_deleted"] or r["files_modified"]
    ]
    if specs_with_changes:
        print(f"\nERROR: Source files changed ({len(specs_with_changes)} specs affected):")
        print("  Spec files need updating to document changes.")
        for report in specs_with_changes:
            print(f"\n  {report['doc_path']}:")
            if report["files_added"]:
                print(f"    Files added ({len(report['files_added'])}):")
                for f in report["files_added"][:10]:  # Limit output
                    print(f"      + {f}")
                if len(report["files_added"]) > 10:
                    print(f"      ... and {len(report['files_added']) - 10} more")
            if report["files_deleted"]:
                print(f"    Files deleted ({len(report['files_deleted'])}):")
                for f in report["files_deleted"][:10]:
                    print(f"      - {f}")
                if len(report["files_deleted"]) > 10:
                    print(f"      ... and {len(report['files_deleted']) - 10} more")
            if report["files_modified"]:
                print(f"    Files modified ({len(report['files_modified'])}):")
                for f in report["files_modified"][:10]:
                    print(f"      ~ {f}")
                if len(report["files_modified"]) > 10:
                    print(f"      ... and {len(report['files_modified']) - 10} more")

    if spec_reports:
        # Show non-blocking spec reports (within grace period or dirty)
        non_stale_reports = [r for r in spec_reports if r["doc_path"] not in stale_specs]
        if any(r["stale"] for r in non_stale_reports):
            print("\nSpec Freshness (within 1-day grace period)")
            for report in non_stale_reports:
                if report["stale"]:
                    _print_report(report, args.verbose)

    if exit_code == 0:
        print("\nOK: All documentation checks passed")
    else:
        print(f"\nERROR: Documentation checks failed (exit {exit_code})")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
