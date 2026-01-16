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
import sys
from pathlib import Path

SHARD_DEFINITIONS = {
    "libs-core": ["tests/libs/core/**"],
    "libs-platform": ["tests/libs/platform/**"],
    "libs-trading": ["tests/libs/trading/**", "tests/libs/data/**"],
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


def main():
    # Load all discovered tests (pytest nodeids)
    all_tests_file = Path("all_tests.txt")
    if not all_tests_file.exists():
        print(f"ERROR: {all_tests_file} not found. Run pytest --collect-only first.")
        return 1

    all_tests = all_tests_file.read_text().strip().split("\n")
    all_tests = [t for t in all_tests if t.strip()]  # Filter empty lines

    if not all_tests:
        print("ERROR: No tests found in all_tests.txt")
        return 1

    # Map each test to its shard(s)
    test_to_shards: dict[str, list[str]] = {}
    for nodeid in all_tests:
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

    print(f"✓ Shard validation PASSED: {len(all_tests)} tests, all in exactly one shard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
