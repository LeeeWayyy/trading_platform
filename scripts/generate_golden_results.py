#!/usr/bin/env python3
"""Generate golden backtest results for regression testing.

This script regenerates the golden results used for backtest regression tests.
It should only be run when:
1. Major alpha logic changes
2. Dataset schema changes
3. Quarterly refresh (optional)

Usage:
    python scripts/generate_golden_results.py --use-placeholders [--dry-run]
    python scripts/generate_golden_results.py --validate

Options:
    --use-placeholders  Required flag to explicitly regenerate with placeholder values
                        (prevents accidental overwrite of real golden baselines)
    --dry-run           Show what would be generated without writing files
    --validate          Validate existing checksums instead of regenerating

Requirements:
    - Active Python venv
    - Access to dataset snapshots (or mock data for testing)

NOTE: Once PITBacktester is integrated (T5.1/T5.2), this script will run actual
backtests instead of using placeholders. The --use-placeholders flag will then
be deprecated.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from libs.common.file_utils import hash_file_sha256  # noqa: E402

GOLDEN_RESULTS_DIR = PROJECT_ROOT / "tests" / "regression" / "golden_results"


def _compute_storage_size_mb(directory: Path) -> tuple[float, int]:
    """Compute total storage size in decimal MB (1MB = 1,000,000 bytes) and raw bytes.

    Uses banker's rounding (round half to even) to 2 decimal places.
    """
    total_bytes = sum(f.stat().st_size for f in directory.glob("*.json") if f.is_file())
    return round(total_bytes / 1_000_000, 2), total_bytes


def generate_placeholder_golden_results(dry_run: bool = False) -> dict[str, Any]:
    """Generate placeholder golden results for initial setup.

    In production, this would run actual backtests via PITBacktester.
    For now, generates placeholder values that can be replaced with
    real backtest results when data is available.

    Args:
        dry_run: If True, show what would be generated without writing

    Returns:
        Summary of generated files
    """
    print("Generating golden results...")

    # Golden result configurations
    configs = [
        {
            "filename": "momentum_2020_2022",
            "alpha_name": "momentum",
            "alpha_params": {"lookback_days": 252, "skip_days": 21, "winsorize_pct": 0.01},
            "start_date": "2020-01-01",
            "end_date": "2022-12-31",
            "weight_method": "zscore",
            "random_seed": 42,
            "snapshot_id": "golden_v1.0.0",
            # Placeholder metrics (replace with actual backtest results)
            "metrics": {
                "mean_ic": 0.025,
                "icir": 0.85,
                "hit_rate": 0.52,
                "coverage": 0.95,
                "long_short_spread": 0.008,
                "average_turnover": 0.15,
                "decay_half_life": 15.0,
            },
        },
        {
            "filename": "value_2020_2022",
            "alpha_name": "value",
            "alpha_params": {"winsorize_pct": 0.01},
            "start_date": "2020-01-01",
            "end_date": "2022-12-31",
            "weight_method": "zscore",
            "random_seed": 42,
            "snapshot_id": "golden_v1.0.0",
            # Placeholder metrics (replace with actual backtest results)
            "metrics": {
                "mean_ic": 0.018,
                "icir": 0.65,
                "hit_rate": 0.51,
                "coverage": 0.88,
                "long_short_spread": 0.006,
                "average_turnover": 0.08,
                "decay_half_life": 25.0,
            },
        },
    ]

    generated_files: list[dict[str, str]] = []

    for config in configs:
        filename = config["filename"]
        metrics = config["metrics"]

        # Generate metrics file
        metrics_path = GOLDEN_RESULTS_DIR / f"{filename}.json"
        if dry_run:
            print(f"  [DRY-RUN] Would write: {metrics_path}")
        else:
            with open(metrics_path, "w", encoding="utf-8") as f:
                json.dump(metrics, f, indent=2)
                f.write("\n")
            print(f"  Wrote: {metrics_path}")

        # Generate config file
        config_data = {
            "alpha_name": config["alpha_name"],
            "alpha_params": config["alpha_params"],
            "start_date": config["start_date"],
            "end_date": config["end_date"],
            "weight_method": config["weight_method"],
            "random_seed": config["random_seed"],
            "snapshot_id": config["snapshot_id"],
        }
        config_path = GOLDEN_RESULTS_DIR / f"{filename}_config.json"
        if dry_run:
            print(f"  [DRY-RUN] Would write: {config_path}")
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
                f.write("\n")
            print(f"  Wrote: {config_path}")

        # Track for manifest (both metrics and config files)
        if not dry_run:
            generated_files.append(
                {
                    "name": f"{filename}.json",
                    "checksum": f"sha256:{hash_file_sha256(metrics_path)}",
                }
            )
            generated_files.append(
                {
                    "name": f"{filename}_config.json",
                    "checksum": f"sha256:{hash_file_sha256(config_path)}",
                }
            )

    return {"files": generated_files, "configs": configs}


def write_manifest(golden_files: list[dict[str, str]], dry_run: bool = False) -> None:
    """Write manifest with per-file checksums for reproducibility validation.

    Args:
        golden_files: List of {"name": ..., "checksum": ...} entries
        dry_run: If True, show what would be written without writing
    """
    manifest_path = GOLDEN_RESULTS_DIR / "manifest.json"

    # Create manifest content (storage_size computed after writing)
    manifest = {
        "version": "v1.0.0",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dataset_snapshot_id": "golden_v1.0.0",
        "regeneration_triggers": [
            "Major alpha logic change",
            "Dataset schema change",
            "Quarterly refresh (optional)",
        ],
        "last_regenerated": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "regenerated_by": "scripts/generate_golden_results.py",
        "storage_size_mb": 0.01,  # Placeholder, updated after write
        "storage_size_note": "Decimal MB (1MB = 1,000,000 bytes), includes manifest",
        "golden_files": golden_files,
    }

    if dry_run:
        print(f"\n[DRY-RUN] Would write manifest to: {manifest_path}")
        print(json.dumps(manifest, indent=2))
        return

    # Write manifest first
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    # Recompute storage size including manifest, then update manifest
    storage_size, total_bytes = _compute_storage_size_mb(GOLDEN_RESULTS_DIR)
    manifest["storage_size_mb"] = storage_size

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"\nWrote manifest: {manifest_path}")

    # Validate storage limit using exact bytes (not rounded)
    if total_bytes > 10_000_000:  # 10MB in bytes
        print(
            f"ERROR: Storage size ({total_bytes:,} bytes / {storage_size} MB) exceeds 10MB limit!",
            file=sys.stderr,
        )
        raise SystemExit(1)


def validate_checksums() -> bool:
    """Validate all golden file checksums match manifest.

    Returns:
        True if all checksums valid, False otherwise
    """
    manifest_path = GOLDEN_RESULTS_DIR / "manifest.json"

    if not manifest_path.exists():
        print("ERROR: manifest.json not found")
        return False

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    all_valid = True
    for entry in manifest["golden_files"]:
        file_path = GOLDEN_RESULTS_DIR / entry["name"]

        if not file_path.exists():
            print(f"ERROR: Missing file: {entry['name']}")
            all_valid = False
            continue

        actual = f"sha256:{hash_file_sha256(file_path)}"
        expected = entry["checksum"]

        if actual != expected:
            print(f"ERROR: Checksum mismatch for {entry['name']}")
            print(f"  Expected: {expected}")
            print(f"  Actual:   {actual}")
            all_valid = False
        else:
            print(f"OK: {entry['name']}")

    return all_valid


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate golden backtest results for regression testing"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without writing files",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate existing checksums instead of regenerating",
    )
    parser.add_argument(
        "--use-placeholders",
        action="store_true",
        help="Explicitly confirm regeneration with placeholder values (required)",
    )

    args = parser.parse_args()

    # Guard against accidental regeneration
    if not args.validate and not args.use_placeholders:
        print("ERROR: Regeneration requires explicit --use-placeholders flag")
        print("       This prevents accidental overwrite of real golden baselines.")
        print("")
        print("Usage:")
        print("  python scripts/generate_golden_results.py --use-placeholders [--dry-run]")
        print("  python scripts/generate_golden_results.py --validate")
        return 1

    # Ensure output directory exists
    GOLDEN_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.validate:
        print("Validating golden result checksums...")
        if validate_checksums():
            print("\nAll checksums valid!")
            return 0
        else:
            print("\nChecksum validation FAILED!")
            return 1

    # Generate golden results
    result = generate_placeholder_golden_results(dry_run=args.dry_run)

    # Write manifest
    write_manifest(result["files"], dry_run=args.dry_run)

    if args.dry_run:
        print("\n[DRY-RUN] No files were written.")
    else:
        print("\nGolden results regeneration complete!")
        print("Remember to commit changes and request PR review.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
