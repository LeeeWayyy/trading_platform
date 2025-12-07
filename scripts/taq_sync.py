#!/usr/bin/env python3
"""
TAQ Data Sync CLI.

Synchronize TAQ data from WRDS to local Parquet storage.

Commands:
    sync-aggregates: Sync aggregate datasets (1min bars, daily RV, spread stats)
    sync-sample: Sync tick samples for a specific date
    cleanup: Remove data beyond retention period

Usage:
    python scripts/taq_sync.py sync-aggregates --symbols SP500 --dataset 1min_bars \
        --start-date 2024-01-01 --end-date 2024-01-31

    python scripts/taq_sync.py sync-sample --date 2024-01-15 --symbols AAPL,MSFT

    python scripts/taq_sync.py cleanup --retention-days 365
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path

import click

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from libs.data_providers.taq_storage import (  # noqa: E402
    TAQStorageManager,
    register_taq_schemas,
)
from libs.data_providers.wrds_client import WRDSClient  # noqa: E402
from libs.data_quality.manifest import ManifestManager  # noqa: E402
from libs.data_quality.schema import SchemaRegistry  # noqa: E402
from libs.data_quality.validation import DataValidator  # noqa: E402
from libs.data_quality.versioning import DatasetVersionManager  # noqa: E402

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_sp500_symbols() -> list[str]:
    """Get current SP500 symbols (placeholder - use actual source in prod)."""
    # In production, fetch from a maintained SP500 list
    # For now, return common large-cap symbols
    return [
        "AAPL",
        "MSFT",
        "AMZN",
        "NVDA",
        "GOOGL",
        "META",
        "TSLA",
        "BRK.B",
        "UNH",
        "XOM",
        "JNJ",
        "JPM",
        "V",
        "PG",
        "MA",
        "HD",
        "CVX",
        "MRK",
        "ABBV",
        "LLY",
        "PEP",
        "KO",
        "COST",
        "AVGO",
        "TMO",
        "MCD",
        "WMT",
        "ACN",
        "CSCO",
        "DHR",
        "ADBE",
        "ABT",
        "CRM",
        "NKE",
        "PFE",
        "ORCL",
    ]


def parse_symbols(symbols_str: str) -> list[str]:
    """Parse symbols from string input.

    Args:
        symbols_str: Comma-separated symbols or 'SP500' for index constituents.

    Returns:
        List of symbol strings.
    """
    if symbols_str.upper() == "SP500":
        return get_sp500_symbols()
    return [s.strip().upper() for s in symbols_str.split(",") if s.strip()]


def create_taq_manager(
    wrds_client: WRDSClient | None,
    storage_path: Path,
) -> TAQStorageManager:
    """Create configured TAQStorageManager instance."""
    # Set up paths
    manifest_path = Path("data/manifests/taq")
    snapshot_path = Path("data/snapshots/taq")
    lock_dir = Path("data/locks")

    # Create managers
    manifest_manager = ManifestManager(storage_path=manifest_path)
    version_manager = DatasetVersionManager(
        manifest_manager=manifest_manager,
        snapshots_dir=snapshot_path,
    )
    validator = DataValidator()
    schema_registry = SchemaRegistry()

    # Register TAQ schemas
    register_taq_schemas(schema_registry)

    return TAQStorageManager(
        wrds_client=wrds_client,
        storage_path=storage_path,
        lock_dir=lock_dir,
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        validator=validator,
        schema_registry=schema_registry,
    )


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose: bool) -> None:
    """TAQ Data Sync CLI.

    Synchronize TAQ data from WRDS to local Parquet storage.
    """
    setup_logging(verbose)


@cli.command("sync-aggregates")
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated symbols or 'SP500' for index",
)
@click.option(
    "--dataset",
    type=click.Choice(["1min_bars", "daily_rv", "spread_stats"]),
    required=True,
    help="Aggregate dataset to sync",
)
@click.option(
    "--start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--incremental/--full",
    default=True,
    help="Incremental sync (default) or full refresh",
)
@click.option(
    "--snapshot",
    is_flag=True,
    help="Create versioned snapshot after sync",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be synced without executing",
)
def sync_aggregates(
    symbols: str,
    dataset: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    incremental: bool,
    snapshot: bool,
    dry_run: bool,
) -> None:
    """Sync TAQ aggregate data from WRDS.

    Examples:
        # Sync 1-minute bars for AAPL and MSFT
        python scripts/taq_sync.py sync-aggregates \\
            --symbols AAPL,MSFT \\
            --dataset 1min_bars \\
            --start-date 2024-01-01 \\
            --end-date 2024-01-31

        # Sync SP500 with snapshot
        python scripts/taq_sync.py sync-aggregates \\
            --symbols SP500 \\
            --dataset daily_rv \\
            --start-date 2024-01-01 \\
            --end-date 2024-01-31 \\
            --snapshot
    """
    symbol_list = parse_symbols(symbols)
    start = start_date.date()
    end = end_date.date()

    logger.info(
        "TAQ aggregates sync starting",
        extra={
            "component": "taq_sync_cli",
            "dataset": dataset,
            "symbols_count": len(symbol_list),
            "start_date": str(start),
            "end_date": str(end),
            "incremental": incremental,
            "snapshot": snapshot,
            "dry_run": dry_run,
        },
    )

    if dry_run:
        click.echo(f"[DRY RUN] Would sync {dataset}:")
        click.echo(f"  Symbols: {len(symbol_list)} ({symbol_list[:5]}...)")
        click.echo(f"  Date range: {start} to {end}")
        click.echo(f"  Incremental: {incremental}")
        click.echo(f"  Create snapshot: {snapshot}")
        return

    # Verify WRDS credentials
    if not os.getenv("WRDS_USERNAME"):
        click.echo("Error: WRDS_USERNAME environment variable not set", err=True)
        sys.exit(1)

    storage_path = Path("data/taq")

    try:
        # Connect to WRDS
        click.echo("Connecting to WRDS...")
        wrds_client = WRDSClient()

        # Create manager and sync
        manager = create_taq_manager(wrds_client, storage_path)

        click.echo(f"Syncing {dataset} for {len(symbol_list)} symbols...")
        manifest = manager.sync_aggregates(
            dataset=dataset,  # type: ignore[arg-type]
            symbols=symbol_list,
            start_date=start,
            end_date=end,
            incremental=incremental,
            create_snapshot=snapshot,
        )

        click.echo(f"Sync completed: {manifest.row_count} rows, {len(manifest.file_paths)} files")

        if snapshot:
            click.echo(f"Snapshot created: taq_{dataset}_{end.strftime('%Y%m%d')}")

    except Exception as e:
        logger.exception("Sync failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("sync-sample")
@click.option(
    "--date",
    "sample_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Sample date (YYYY-MM-DD)",
)
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated symbols",
)
@click.option(
    "--snapshot",
    is_flag=True,
    help="Create versioned snapshot after sync",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be synced without executing",
)
def sync_sample(
    sample_date: datetime.datetime,
    symbols: str,
    snapshot: bool,
    dry_run: bool,
) -> None:
    """Sync TAQ tick samples for a specific date.

    Examples:
        python scripts/taq_sync.py sync-sample \\
            --date 2024-01-15 \\
            --symbols AAPL,MSFT,GOOGL
    """
    symbol_list = parse_symbols(symbols)
    date = sample_date.date()

    logger.info(
        "TAQ samples sync starting",
        extra={
            "component": "taq_sync_cli",
            "sample_date": str(date),
            "symbols_count": len(symbol_list),
            "snapshot": snapshot,
            "dry_run": dry_run,
        },
    )

    if dry_run:
        click.echo("[DRY RUN] Would sync tick samples:")
        click.echo(f"  Date: {date}")
        click.echo(f"  Symbols: {symbol_list}")
        click.echo(f"  Create snapshot: {snapshot}")
        return

    # Verify WRDS credentials
    if not os.getenv("WRDS_USERNAME"):
        click.echo("Error: WRDS_USERNAME environment variable not set", err=True)
        sys.exit(1)

    storage_path = Path("data/taq")

    try:
        # Connect to WRDS
        click.echo("Connecting to WRDS...")
        wrds_client = WRDSClient()

        # Create manager and sync
        manager = create_taq_manager(wrds_client, storage_path)

        click.echo(f"Syncing tick samples for {date}...")
        manifest = manager.sync_samples(
            sample_date=date,
            symbols=symbol_list,
            create_snapshot=snapshot,
        )

        click.echo(f"Sync completed: {manifest.row_count} rows, {len(symbol_list)} symbols")

        if snapshot:
            click.echo(f"Snapshot created: taq_samples_{date.strftime('%Y%m%d')}")

    except Exception as e:
        logger.exception("Sync failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("cleanup")
@click.option(
    "--retention-days",
    type=int,
    default=365,
    help="Days to retain data (default: 365)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without executing",
)
def cleanup(retention_days: int, dry_run: bool) -> None:
    """Clean up old TAQ data beyond retention period.

    Examples:
        python scripts/taq_sync.py cleanup --retention-days 365
        python scripts/taq_sync.py cleanup --dry-run
    """
    logger.info(
        "TAQ cleanup starting",
        extra={
            "component": "taq_sync_cli",
            "retention_days": retention_days,
            "dry_run": dry_run,
        },
    )

    storage_path = Path("data/taq")

    if dry_run:
        cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
        click.echo(f"[DRY RUN] Would delete data older than {cutoff}")
        click.echo(f"  Retention: {retention_days} days")
        return

    try:
        manager = create_taq_manager(None, storage_path)
        deleted = manager.cleanup(retention_days=retention_days)
        click.echo(f"Cleanup completed: {deleted} items deleted")

    except Exception as e:
        logger.exception("Cleanup failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
