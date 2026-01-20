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


def main() -> int:
    if not QUARANTINE_FILE.exists():
        print("✓ No quarantine file found")
        return 0

    expired = []
    invalid_format = []
    with open(QUARANTINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            test_path, _, expiration_date = parts[0], parts[1], parts[2]
            try:
                exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
            except ValueError:
                invalid_format.append((test_path, expiration_date))
                continue
            if exp_date < TODAY:
                expired.append((test_path, expiration_date))

    has_errors = False

    if invalid_format:
        print("ERROR: Invalid date format in quarantine entries!")
        print("Date format must be YYYY-MM-DD (e.g., 2025-03-15)")
        print()
        for test, date_str in invalid_format:
            print(f"  INVALID DATE '{date_str}': {test}")
        print()
        has_errors = True

    if expired:
        print("ERROR: Expired quarantine entries found!")
        print("Either fix the flaky test, renew with justification, or permanently skip.")
        print()
        for test, exp in expired:
            print(f"  EXPIRED ({exp}): {test}")
        has_errors = True

    if has_errors:
        return 1

    total_entries = sum(
        1 for line in QUARANTINE_FILE.open() if line.strip() and not line.startswith("#")
    )
    print(f"✓ Quarantine check passed: {total_entries} entries, none expired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
