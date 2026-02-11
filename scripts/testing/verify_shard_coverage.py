#!/usr/bin/env python3
"""
Validates that:
1. Every test is assigned to exactly one shard
2. No test is missing from all shards
3. No test appears in multiple shards (DUPLICATE DETECTION)

IMPORTANT: pytest nodeids format is 'tests/path/test_file.py::TestClass::test_method'
We extract the file path and match against shard definitions using fnmatch for glob support.
"""
import fnmatch
import subprocess
import sys
from pathlib import Path

SHARD_DEFINITIONS = {
    "libs-core": ["tests/libs/core/**", "tests/libs/common/**", "tests/libs/test_*.py"],
    "libs-platform": [
        "tests/libs/platform/**",
        "tests/libs/models/**",
        "tests/libs/web_console_data/**",
        "tests/libs/web_console_services/**",
    ],
    "libs-trading": ["tests/libs/trading/**", "tests/libs/data/**", "tests/libs/analytics/**"],
    "apps-services": ["tests/apps/**"],
    "strategies": ["tests/strategies/**", "tests/research/**"],
    "root-and-misc": [
        "tests/test_*.py",
        "tests/regression/**",
        "tests/workflows/**",
        "tests/fixtures/**",
        "tests/infra/**",
        "tests/load/**",
    ],
}

# Tests excluded from shard validation (they run in separate jobs or manually)
EXCLUDED_PATTERNS = [
    "tests/integration/**",
    "tests/e2e/**",
    "tests/scripts/**",
]


def nodeid_to_filepath(nodeid: str) -> str:
    """Extract file path from pytest nodeid.

    'tests/libs/core/test_redis.py::TestRedis::test_connect' -> 'tests/libs/core/test_redis.py'
    """
    return nodeid.split("::")[0]


def matches_shard(filepath: str, shard_patterns: list[str]) -> bool:
    """Check if filepath matches any of the shard's glob patterns."""
    for pattern in shard_patterns:
        # Convert glob pattern to fnmatch pattern
        # 'tests/libs/core/**' matches any file under tests/libs/core/
        if pattern.endswith("**"):
            prefix = pattern[:-2]  # Remove '**'
            if filepath.startswith(prefix):
                return True
        elif fnmatch.fnmatch(filepath, pattern):
            return True
    return False


def is_excluded(filepath: str) -> bool:
    """Check if filepath matches any excluded pattern."""
    for pattern in EXCLUDED_PATTERNS:
        if pattern.endswith("**"):
            prefix = pattern[:-2]
            if filepath.startswith(prefix):
                return True
        elif fnmatch.fnmatch(filepath, pattern):
            return True
    return False


def collect_tests() -> list[str]:
    """Collect all tests using pytest --collect-only."""
    # Build deselect args from quarantine file
    deselect_args: list[str] = []
    quarantine_file = Path("tests/quarantine.txt")
    if quarantine_file.exists():
        for line in quarantine_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                test_id = line.split("|")[0].strip()
                if test_id:
                    deselect_args.extend(["--deselect", test_id])

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "--quiet",
        "--quiet",  # Double quiet for nodeid-only output
        "-m",
        "not integration and not e2e",
        *deselect_args,
        "tests/",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": "."},
    )

    # Parse output - each line with :: is a test nodeid
    tests = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and line.startswith("tests/"):
            tests.append(line)

    return tests


def main() -> int:
    # Collect tests using pytest
    print("Collecting tests...")
    all_tests = collect_tests()

    if not all_tests:
        print("ERROR: No tests collected")
        return 1

    print(f"Collected {len(all_tests)} tests")

    # Filter out excluded tests (integration, e2e)
    excluded_count = 0
    filtered_tests = []
    for nodeid in all_tests:
        filepath = nodeid_to_filepath(nodeid)
        if is_excluded(filepath):
            excluded_count += 1
        else:
            filtered_tests.append(nodeid)

    # Map each test to its shard(s)
    test_to_shards: dict[str, list[str]] = {}
    for nodeid in filtered_tests:
        filepath = nodeid_to_filepath(nodeid)
        test_to_shards[nodeid] = []
        for shard, patterns in SHARD_DEFINITIONS.items():
            if matches_shard(filepath, patterns):
                test_to_shards[nodeid].append(shard)

    # Check for issues
    missing = [t for t, shards in test_to_shards.items() if len(shards) == 0]
    duplicates = [t for t, shards in test_to_shards.items() if len(shards) > 1]

    if missing:
        print(f"ERROR: {len(missing)} tests not assigned to any shard:")
        for t in missing[:10]:
            print(f"  - {t}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    if duplicates:
        print(f"ERROR: {len(duplicates)} tests assigned to multiple shards:")
        for t in duplicates[:10]:
            print(f"  - {t} -> {test_to_shards[t]}")
        if len(duplicates) > 10:
            print(f"  ... and {len(duplicates) - 10} more")

    if missing or duplicates:
        return 1

    print(
        f"✓ Shard validation PASSED: {len(filtered_tests)} tests in shards, "
        f"{excluded_count} excluded (integration/e2e)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
