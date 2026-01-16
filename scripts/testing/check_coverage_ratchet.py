#!/usr/bin/env python3
"""
Coverage ratchet prevents coverage regression while allowing incremental progress.
Baselines are stored in scripts/testing/coverage_baselines.json (see Part 3).
"""
import json
import subprocess
import sys
from pathlib import Path

BASELINES_FILE = Path(__file__).parent / "coverage_baselines.json"
P0_MODULES_FILE = Path(__file__).parent / "p0_modules.json"
MIN_BRANCH_COUNT = 5  # Modules with fewer branches are not counted (trivial files)
REPO_ROOT = Path(__file__).parent.parent.parent  # scripts/testing/ -> repo root

# Target coverage goals (informational, baselines ratchet towards these)
TARGET_COVERAGE = {
    "overall": 85,
    "P0_modules": 95,  # libs/trading/*, apps/execution_gateway/*, apps/signal_service/*
    "P1_modules": 90,  # libs/platform/*, apps/orchestrator/*
}


def normalize_path(filepath: str) -> str:
    """Normalize coverage paths to repo-relative format.

    coverage.json may report absolute paths; baselines use repo-relative.
    This ensures consistent matching.
    """
    p = Path(filepath)
    if p.is_absolute():
        try:
            return str(p.relative_to(REPO_ROOT))
        except ValueError:
            return filepath  # Not under repo root, return as-is
    # Normalize separators and remove leading ./
    return str(Path(filepath)).lstrip("./")


def load_baselines() -> dict:
    """Load baselines from JSON file."""
    if not BASELINES_FILE.exists():
        print(f"⚠️  Baselines file not found: {BASELINES_FILE}")
        print("Creating initial baseline with current coverage...")
        return {"version": 1, "overall": 0, "modules": {}}
    with open(BASELINES_FILE) as f:
        return json.load(f)


def get_current_coverage() -> dict:
    """Parse coverage.json to get current branch coverage per module."""
    result = subprocess.run(
        ["coverage", "json", "-o", "-"], capture_output=True, text=True
    )

    # Error handling for missing/empty coverage data
    if result.returncode != 0:
        print(f"ERROR: coverage json failed: {result.stderr}")
        sys.exit(1)

    if not result.stdout.strip():
        print("ERROR: coverage.json output is empty. Did tests run with --cov?")
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON from coverage: {e}")
        sys.exit(1)

    # Validate required keys exist
    if "totals" not in data or "files" not in data:
        print("ERROR: coverage.json missing 'totals' or 'files' keys")
        sys.exit(1)

    totals = data["totals"]
    if totals.get("num_branches", 0) == 0:
        print("ERROR: No branches found in coverage data. Check --cov-branch flag.")
        sys.exit(1)

    coverage = {
        "overall": totals["covered_branches"] / totals["num_branches"] * 100,
        "_branch_counts": {},  # Track branch counts for minimum enforcement
    }

    for filepath, filedata in data["files"].items():
        normalized = normalize_path(filepath)  # Normalize to repo-relative path
        num_branches = filedata["summary"].get("num_branches", 0)
        if num_branches >= MIN_BRANCH_COUNT:  # Enforce minimum branch count
            coverage[normalized] = (
                filedata["summary"]["covered_branches"] / num_branches * 100
            )
            coverage["_branch_counts"][normalized] = num_branches

    return coverage


def check_ratchet() -> int:
    """Check coverage against baselines. Returns exit code."""
    baselines = load_baselines()
    current = get_current_coverage()

    failures = []
    warnings = []

    for module, baseline in baselines.get("modules", {}).items():
        if module not in current:
            # Module not in coverage - could be missing or too few branches
            if module in current.get("_branch_counts", {}):
                warnings.append(
                    f"{module}: skipped (only {current['_branch_counts'][module]} branches)"
                )
            else:
                warnings.append(f"{module}: not found in coverage data")
            continue

        actual = current.get(module, 0)
        if actual < baseline:
            failures.append(f"{module}: {actual:.1f}% < {baseline}% baseline")

    # Check overall
    if current["overall"] < baselines["overall"]:
        failures.append(
            f"overall: {current['overall']:.1f}% < {baselines['overall']}% baseline"
        )

    # Print warnings
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  ⚠ {w}")
        print()

    if failures:
        print("Coverage ratchet FAILED - regression detected:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print("✓ Coverage ratchet PASSED - no regressions detected")
    print(f"Current overall coverage: {current['overall']:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(check_ratchet())
