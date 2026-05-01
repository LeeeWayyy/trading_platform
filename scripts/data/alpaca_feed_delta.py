#!/usr/bin/env python3
"""CLI for producing Alpaca IEX-vs-SIP historical bar delta reports."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.data.data_quality.alpaca_feed_delta import (  # noqa: E402
    AlpacaFeedDeltaComparator,
    FeedDeltaReport,
    normalize_symbols,
)

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)


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
    return normalize_symbols(parsed)


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


def _write_report(report: FeedDeltaReport, output: Path | None) -> None:
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if output is None:
        print(payload)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload + "\n")


def _run_compare(args: argparse.Namespace) -> int:
    _load_dotenv()
    symbols = _parse_symbols(args.symbols, args.symbols_file)
    start = _parse_datetime(args.start)
    end = _parse_datetime(args.end, end_of_day=True)
    if end < start:
        raise ValueError("--end must be >= --start")

    comparator = AlpacaFeedDeltaComparator.from_env()
    report = comparator.compare(
        symbols=symbols,
        start=start,
        end=end,
        timeframe=args.timeframe,
        adjustment_mode=args.adjustment,
        left_feed=args.left_feed,
        right_feed=args.right_feed,
    )
    _write_report(report, args.output)
    print(
        "Alpaca feed delta: "
        f"status={report.status} matched={report.summary['matched_bar_count']} "
        f"issues={report.summary['total_issue_count']} hash={report.content_hash[:16]}..."
    )
    return 1 if report.status == "failed" else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpaca-feed-delta",
        description="Produce an Alpaca IEX-vs-SIP historical bar delta report.",
    )
    parser.add_argument("--symbols", default="", help="Comma-separated ticker symbols.")
    parser.add_argument(
        "--symbols-file",
        type=Path,
        default=None,
        help="File containing one ticker symbol per line.",
    )
    parser.add_argument("--start", required=True, help="Start date or UTC datetime.")
    parser.add_argument("--end", required=True, help="End date or UTC datetime.")
    parser.add_argument("--timeframe", default="5Min", help="1Min, 5Min, 15Min, 1Hour, or 1Day.")
    parser.add_argument("--adjustment", default="all", help="Alpaca adjustment mode.")
    parser.add_argument("--left-feed", default="iex", help="Left Alpaca feed.")
    parser.add_argument("--right-feed", default="sip", help="Right Alpaca feed.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    parser.set_defaults(func=_run_compare)
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
