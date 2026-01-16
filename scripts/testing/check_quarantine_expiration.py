#!/usr/bin/env python3
"""
Check quarantine.txt for expired entries.
Fails CI if any entry is past its expiration date without renewal.
"""
import sys
from datetime import datetime
from pathlib import Path

QUARANTINE_FILE = Path("tests/quarantine.txt")
TODAY = datetime.now().date()


def main():
    if not QUARANTINE_FILE.exists():
        print("✓ No quarantine file found")
        return 0

    expired = []
    with open(QUARANTINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            test_path, added_date, expiration_date = parts[0], parts[1], parts[2]
            try:
                exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
            except ValueError:
                print(f"ERROR: Invalid expiration date format in: {line}")
                continue
            if exp_date < TODAY:
                expired.append((test_path, expiration_date))

    if expired:
        print("ERROR: Expired quarantine entries found!")
        print("Either fix the flaky test, renew with justification, or permanently skip.")
        print()
        for test, exp in expired:
            print(f"  EXPIRED ({exp}): {test}")
        return 1

    total_entries = sum(1 for line in QUARANTINE_FILE.open() if line.strip() and not line.startswith("#"))
    print(f"✓ Quarantine check passed: {total_entries} entries, none expired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
