#!/usr/bin/env python3
"""CLI for syncing Alpaca corporate-action announcements to local parquet."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.data.data_providers.alpaca_corp_actions_sync import (  # noqa: E402
    AlpacaCorporateActionsSyncManager,
)
from libs.data.data_quality.manifest import ManifestManager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

DATA_ROOT = Path("data")
STORAGE_PATH = DATA_ROOT / "alpaca" / "sip" / "corp_actions"
MANIFEST_DIR = DATA_ROOT / "manifests"
LOCK_DIR = DATA_ROOT / "locks"


def _load_dotenv() -> None:
    """Load repo-root .env for local CLI use when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(".env"))


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def _manifest_manager() -> ManifestManager:
    return ManifestManager(
        storage_path=MANIFEST_DIR,
        lock_dir=LOCK_DIR,
        data_root=DATA_ROOT,
    )


def _manager(
    *,
    storage_path: Path,
    limit: int,
    throttle_seconds: float = 0.0,
) -> AlpacaCorporateActionsSyncManager:
    _load_dotenv()
    return AlpacaCorporateActionsSyncManager.from_env(
        storage_path=storage_path,
        manifest_manager=_manifest_manager(),
        data_root=DATA_ROOT,
        limit=limit,
        request_interval_seconds=throttle_seconds,
    )


def _run_full_sync(args: argparse.Namespace) -> int:
    symbols = _parse_csv(args.symbols)
    ca_types = _parse_csv(args.types)
    ids = _parse_csv(args.ids)
    if not symbols and not ids:
        raise ValueError("Provide --symbols or --ids to bound the corporate-actions sync")

    with _manager(
        storage_path=args.storage_path,
        limit=args.limit,
        throttle_seconds=args.throttle_seconds,
    ) as manager:
        manifest = manager.full_sync(
            start_date=args.start_date,
            end_date=args.end_date,
            symbols=symbols,
            ca_types=ca_types,
            ids=ids,
        )
    print(
        f"Corporate actions sync complete: {manifest.row_count:,} rows across "
        f"{len(manifest.file_paths)} file(s)"
    )
    print(f"Manifest version: {manifest.manifest_version}")
    print(f"Checksum: {manifest.checksum[:16]}...")
    return 0


def _run_status(_args: argparse.Namespace) -> int:
    manifest = _manifest_manager().load_manifest(AlpacaCorporateActionsSyncManager.DATASET_NAME)
    if manifest is None:
        print("alpaca_sip_corp_actions: Not synced")
        return 0

    print("alpaca_sip_corp_actions:")
    print(f"  Last sync: {manifest.sync_timestamp.isoformat()}")
    print(f"  Date range: {manifest.start_date} to {manifest.end_date}")
    print(f"  Rows: {manifest.row_count:,}")
    print(f"  Files: {len(manifest.file_paths)}")
    print(f"  Manifest version: {manifest.manifest_version}")
    print(f"  Status: {manifest.validation_status}")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    manager = AlpacaCorporateActionsSyncManager(
        client=_VerifyOnlyClient(),
        storage_path=args.storage_path,
        manifest_manager=_manifest_manager(),
        data_root=DATA_ROOT,
    )
    errors = manager.verify_integrity()
    if errors:
        print(f"Verification failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("Verification passed")
    return 0


def _run_round_trip(args: argparse.Namespace) -> int:
    with _manager(
        storage_path=args.storage_path,
        limit=args.limit,
        throttle_seconds=args.throttle_seconds,
    ) as manager:
        report = manager.run_round_trip_checks()
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.output is None:
        print(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
    print(
        "Corporate actions round-trip: "
        f"status={report.status} checks={len(report.results)} "
        f"hash={report.content_hash[:16]}..."
    )
    return 0 if report.status == "passed" else 1


class _VerifyOnlyClient:
    """Client placeholder for integrity-only commands."""

    def get_corporate_actions(self, params: Mapping[str, str | int]) -> Mapping[str, Any]:
        raise RuntimeError("verify does not fetch Alpaca corporate actions")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpaca-corp-actions-sync",
        description="Sync Alpaca corporate-action announcements to local parquet.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    full_sync = subparsers.add_parser("full-sync", help="Run a corporate-actions sync.")
    full_sync.add_argument("--symbols", default="", help="Comma-separated ticker symbols.")
    full_sync.add_argument(
        "--types",
        default="",
        help="Comma-separated corporate-action types supported by Alpaca.",
    )
    full_sync.add_argument(
        "--ids",
        default="",
        help="Comma-separated Alpaca corporate-action ids. Cannot be combined with symbols/types.",
    )
    full_sync.add_argument("--start-date", type=_parse_date, required=True)
    full_sync.add_argument("--end-date", type=_parse_date, required=True)
    full_sync.add_argument("--storage-path", type=Path, default=STORAGE_PATH)
    full_sync.add_argument(
        "--limit", type=int, default=AlpacaCorporateActionsSyncManager.DEFAULT_LIMIT
    )
    full_sync.add_argument(
        "--throttle-seconds",
        type=float,
        default=0.0,
        help="Sleep this many seconds between chunked Alpaca requests.",
    )
    full_sync.set_defaults(func=_run_full_sync)

    status = subparsers.add_parser("status", help="Show current manifest status.")
    status.set_defaults(func=_run_status)

    verify = subparsers.add_parser("verify", help="Verify local files against manifest.")
    verify.add_argument("--storage-path", type=Path, default=STORAGE_PATH)
    verify.set_defaults(func=_run_verify)

    round_trip = subparsers.add_parser(
        "round-trip",
        help="Validate known split/dividend corporate actions through the API.",
    )
    round_trip.add_argument("--storage-path", type=Path, default=STORAGE_PATH)
    round_trip.add_argument(
        "--limit",
        type=int,
        default=AlpacaCorporateActionsSyncManager.DEFAULT_LIMIT,
    )
    round_trip.add_argument(
        "--throttle-seconds",
        type=float,
        default=0.0,
        help="Sleep this many seconds between chunked Alpaca requests.",
    )
    round_trip.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    round_trip.set_defaults(func=_run_round_trip)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
