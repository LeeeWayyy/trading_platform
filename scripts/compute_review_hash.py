#!/usr/bin/env python3
"""
Helper script to compute review hash for staged changes.

Component 1 (P1T13-F5a): Code State Fingerprinting
Codex MEDIUM fix: Python script (not shell) for portability and single source of truth

Usage:
    ./scripts/compute_review_hash.py

Outputs the SHA256 hash of staged changes to stdout.
This ensures the same hashing logic as workflow_gate.py by importing the method.

Author: Claude Code
Date: 2025-11-13
"""

import sys
from pathlib import Path

# Add project root to path to import WorkflowGate
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.workflow_gate import WorkflowGate


def main() -> int:
    """
    Compute and print SHA256 hash of staged changes.

    Returns:
        int: Exit code (0 for success, 1 for error)
    """
    try:
        gate = WorkflowGate()
        staged_hash = gate._compute_staged_hash()

        if not staged_hash:
            print("(no staged changes)", file=sys.stderr)
            return 0

        # Output hash to stdout for piping
        print(staged_hash)
        return 0

    except Exception as e:
        print(f"Error computing review hash: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
