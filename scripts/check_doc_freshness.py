#!/usr/bin/env python3
"""
Check documentation freshness and spec coverage.

Uses git commit timestamps (not filesystem mtimes) to detect when
source directories have changed without corresponding doc updates.
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
        ]
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

    # Spec checks
    missing_specs: list[str] = []
    spec_reports: list[FreshnessReport] = []

    if SPECS_DIR.exists():
        _, missing_specs = _expected_spec_files()
        if missing_specs:
            exit_code |= 4

        # Per-spec freshness (warning only)
        for spec_file in sorted(SPECS_DIR.rglob("*.md")):
            source_dir = _spec_source_dir(spec_file)
            if not source_dir:
                continue
            report = check_freshness(str(spec_file.relative_to(PROJECT_ROOT)), [source_dir])
            # Spec files do not participate in missing/orphan checks.
            report["missing"] = []
            report["orphaned"] = []
            spec_reports.append(report)

    # Attach missing specs to REPO_MAP report for summary
    repo_report["missing_specs"] = sorted(missing_specs)
    reports = [repo_report] + spec_reports

    if args.json:
        payload = {
            "reports": reports,
            "exit_code": exit_code,
        }
        print(json.dumps(payload, indent=2))
        return exit_code

    print("\nDocumentation Freshness Report")
    _print_report(repo_report, args.verbose)

    if repo_report["missing_specs"]:
        print(f"\nWarning: Missing spec files ({len(repo_report['missing_specs'])}):")
        if args.verbose:
            for spec in repo_report["missing_specs"]:
                print(f"  - {spec}")

    if spec_reports:
        print("\nSpec Freshness (warnings only)")
        for report in spec_reports:
            if report["stale"] or report["missing"] or report["orphaned"]:
                _print_report(report, args.verbose)

    if exit_code == 0:
        print("\nOK: All documentation checks passed")
    else:
        print(f"\nERROR: Documentation checks failed (exit {exit_code})")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
