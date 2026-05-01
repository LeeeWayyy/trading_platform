#!/usr/bin/env python3
"""CLI for syncing Alpaca SIP daily bars to local parquet."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.data.data_providers.alpaca_sip_sync import AlpacaSIPSyncManager  # noqa: E402
from libs.data.data_quality.alpaca_sip_integrity import AlpacaSIPIntegrityChecker  # noqa: E402
from libs.data.data_quality.manifest import ManifestManager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

DATA_ROOT = Path("data")
STORAGE_PATH = DATA_ROOT / "alpaca" / "sip" / "daily"
MANIFEST_DIR = DATA_ROOT / "manifests"
LOCK_DIR = DATA_ROOT / "locks"


def _load_dotenv() -> None:
    """Load repo-root .env for local CLI use when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(".env"))


def _parse_symbols(symbols: str, symbols_file: Path | None) -> list[str]:
    parsed: list[str] = []
    if symbols:
        parsed.extend(part.strip() for part in symbols.split(","))
    if symbols_file is not None:
        parsed.extend(
            line.strip()
            for line in symbols_file.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    normalized = [symbol.upper() for symbol in parsed if symbol.strip()]
    if not normalized:
        raise ValueError("Provide --symbols or --symbols-file")
    return normalized


def _parse_datetime(value: str, *, end_of_day: bool = False) -> datetime.datetime:
    if "T" in value:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed_date = datetime.date.fromisoformat(value)
        time_value = datetime.time.max if end_of_day else datetime.time.min
        parsed = datetime.datetime.combine(parsed_date, time_value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def _manifest_manager() -> ManifestManager:
    return ManifestManager(
        storage_path=MANIFEST_DIR,
        lock_dir=LOCK_DIR,
        data_root=DATA_ROOT,
    )


def _manager(
    *,
    storage_path: Path,
    chunk_size: int,
    throttle_seconds: float,
    feed: str,
    adjustment: str,
) -> AlpacaSIPSyncManager:
    _load_dotenv()
    return AlpacaSIPSyncManager.from_env(
        storage_path=storage_path,
        manifest_manager=_manifest_manager(),
        data_root=DATA_ROOT,
        request_chunk_size=chunk_size,
        request_interval_seconds=throttle_seconds,
        feed=feed,
        adjustment=adjustment,
    )


def _run_full_sync(args: argparse.Namespace) -> int:
    symbol_list = _parse_symbols(args.symbols, args.symbols_file)
    manager = _manager(
        storage_path=args.storage_path,
        chunk_size=args.chunk_size,
        throttle_seconds=args.throttle_seconds,
        feed=args.feed,
        adjustment=args.adjustment,
    )
    manifest = manager.full_sync(
        symbol_list,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    print(
        f"Sync complete: {manifest.row_count:,} rows across "
        f"{len(manifest.file_paths)} partition(s)"
    )
    print(f"Manifest version: {manifest.manifest_version}")
    print(f"Checksum: {manifest.checksum[:16]}...")
    return 0


def _run_status(_args: argparse.Namespace) -> int:
    manifest = _manifest_manager().load_manifest(AlpacaSIPSyncManager.DATASET_NAME)
    if manifest is None:
        print("alpaca_sip_daily: Not synced")
        return 0

    print("alpaca_sip_daily:")
    print(f"  Last sync: {manifest.sync_timestamp.isoformat()}")
    print(f"  Date range: {manifest.start_date} to {manifest.end_date}")
    print(f"  Rows: {manifest.row_count:,}")
    print(f"  Files: {len(manifest.file_paths)}")
    print(f"  Manifest version: {manifest.manifest_version}")
    print(f"  Status: {manifest.validation_status}")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    manager = AlpacaSIPSyncManager(
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


def _run_estimate(args: argparse.Namespace) -> int:
    symbol_list = _parse_symbols(args.symbols, args.symbols_file)
    estimate = AlpacaSIPSyncManager.estimate_full_sync(
        symbol_list,
        start_year=args.start_year,
        end_year=args.end_year,
        request_chunk_size=args.chunk_size,
        request_interval_seconds=args.throttle_seconds,
        requests_per_minute=args.requests_per_minute,
    )
    print(json.dumps(estimate.to_dict(), indent=2, sort_keys=True))
    return 0


def _run_integrity(args: argparse.Namespace) -> int:
    _load_dotenv()
    symbols = _parse_symbols(args.symbols, args.symbols_file)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end, end_of_day=True)
    if end < start:
        raise ValueError("--end must be >= --start")

    report = AlpacaSIPIntegrityChecker.from_env().run(
        symbols=symbols,
        start=start,
        end=end,
        timeframe=args.timeframe,
        adjustment_mode=args.adjustment,
        feed=args.feed,
        max_mismatch_samples=args.max_mismatch_samples,
    )
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.output is None:
        print(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
    print(
        "SIP integrity: "
        f"status={report.status} first_rows={report.first_row_count} "
        f"second_rows={report.second_row_count} mismatches={report.mismatch_count} "
        f"hash={report.content_hash[:16]}..."
    )
    return 0 if report.status in {"passed", "warning"} else 1


class _VerifyOnlyClient:
    """Client placeholder for integrity-only commands."""

    def get_stock_bars(self, request_params: object) -> object:
        raise RuntimeError("verify does not fetch Alpaca bars")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpaca-sip-sync",
        description="Sync Alpaca SIP daily bars to local parquet.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    full_sync = subparsers.add_parser("full-sync", help="Run a full daily-bar sync.")
    full_sync.add_argument("--symbols", default="", help="Comma-separated ticker symbols.")
    full_sync.add_argument(
        "--symbols-file",
        type=Path,
        default=None,
        help="File containing one ticker symbol per line.",
    )
    full_sync.add_argument("--start-year", type=int, default=2016)
    full_sync.add_argument("--end-year", type=int, default=None)
    full_sync.add_argument("--storage-path", type=Path, default=STORAGE_PATH)
    full_sync.add_argument("--chunk-size", type=int, default=200)
    full_sync.add_argument("--throttle-seconds", type=float, default=0.0)
    full_sync.add_argument("--feed", default="sip")
    full_sync.add_argument("--adjustment", default="all")
    full_sync.set_defaults(func=_run_full_sync)

    status = subparsers.add_parser("status", help="Show current manifest status.")
    status.set_defaults(func=_run_status)

    verify = subparsers.add_parser("verify", help="Verify local files against manifest.")
    verify.add_argument("--storage-path", type=Path, default=STORAGE_PATH)
    verify.set_defaults(func=_run_verify)

    estimate = subparsers.add_parser(
        "estimate",
        help="Estimate request count, rows, storage, and duration for a SIP sync.",
    )
    estimate.add_argument("--symbols", default="", help="Comma-separated ticker symbols.")
    estimate.add_argument(
        "--symbols-file",
        type=Path,
        default=None,
        help="File containing one ticker symbol per line.",
    )
    estimate.add_argument("--start-year", type=int, default=2016)
    estimate.add_argument("--end-year", type=int, default=None)
    estimate.add_argument("--chunk-size", type=int, default=200)
    estimate.add_argument("--throttle-seconds", type=float, default=0.0)
    estimate.add_argument(
        "--requests-per-minute",
        type=int,
        default=None,
        help="Optional provider/account request-rate budget for a duration floor.",
    )
    estimate.set_defaults(func=_run_estimate)

    integrity = subparsers.add_parser(
        "integrity",
        help="Pull a fixed SIP window twice and compare deterministic row hashes.",
    )
    integrity.add_argument("--symbols", default="", help="Comma-separated ticker symbols.")
    integrity.add_argument(
        "--symbols-file",
        type=Path,
        default=None,
        help="File containing one ticker symbol per line.",
    )
    integrity.add_argument("--start", required=True, help="Start date or UTC datetime.")
    integrity.add_argument("--end", required=True, help="End date or UTC datetime.")
    integrity.add_argument(
        "--timeframe",
        default="1Day",
        help="1Min, 5Min, 15Min, 1Hour, or 1Day.",
    )
    integrity.add_argument("--adjustment", default="all", help="Alpaca adjustment mode.")
    integrity.add_argument("--feed", default="sip", help="Alpaca feed to verify.")
    integrity.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    integrity.add_argument("--max-mismatch-samples", type=int, default=100)
    integrity.set_defaults(func=_run_integrity)
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
