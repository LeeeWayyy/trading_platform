#!/usr/bin/env python3
"""
WRDS Sync CLI - Command-line interface for WRDS data synchronization.

Usage:
    python scripts/wrds_sync.py full-sync --dataset crsp_daily --start-year 2000
    python scripts/wrds_sync.py incremental --dataset crsp_daily
    python scripts/wrds_sync.py incremental --all
    python scripts/wrds_sync.py status
    python scripts/wrds_sync.py verify --dataset crsp_daily
    python scripts/wrds_sync.py lock-status
    python scripts/wrds_sync.py force-unlock --dataset crsp_daily
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import typer

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from libs.data_providers.locking import LockAcquisitionError
from libs.data_providers.sync_manager import SyncManager
from libs.data_providers.wrds_client import WRDSClient, WRDSConfig
from libs.data_quality.manifest import ManifestManager
from libs.data_quality.schema import SchemaRegistry
from libs.data_quality.validation import DataValidator

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s", "extra": %(extra)s}',
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)


# Create a custom formatter that includes extra fields
class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "extra", {})
        record.extra = str(extra) if extra else "{}"
        return super().format(record)


for handler in logging.root.handlers:
    handler.setFormatter(
        StructuredFormatter(
            '{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s", "extra": %(extra)s}',
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="wrds-sync",
    help="WRDS data synchronization tool",
    no_args_is_help=True,
)

# Default paths
DATA_ROOT = Path("data")
WRDS_DIR = DATA_ROOT / "wrds"
LOCK_DIR = DATA_ROOT / "locks"
MANIFEST_DIR = DATA_ROOT / "manifests"
SCHEMA_DIR = DATA_ROOT / "schemas"

# Known datasets
DATASETS = ["crsp_daily", "compustat_annual", "compustat_quarterly", "fama_french"]


def get_sync_manager() -> tuple[SyncManager, WRDSClient]:
    """Create and return SyncManager with connected WRDSClient."""
    config = WRDSConfig()
    client = WRDSClient(config)
    client.connect()

    manifest_manager = ManifestManager(
        storage_path=MANIFEST_DIR,
        lock_dir=LOCK_DIR,
    )
    validator = DataValidator()
    schema_registry = SchemaRegistry(
        storage_path=SCHEMA_DIR,
        lock_dir=LOCK_DIR,
    )

    manager = SyncManager(
        wrds_client=client,
        storage_path=WRDS_DIR,
        lock_dir=LOCK_DIR,
        manifest_manager=manifest_manager,
        validator=validator,
        schema_registry=schema_registry,
    )

    return manager, client


@app.command()
def full_sync(
    dataset: str = typer.Option(..., "--dataset", "-d", help="Dataset to sync"),
    start_year: int = typer.Option(2000, "--start-year", "-s", help="First year"),
    end_year: int = typer.Option(None, "--end-year", "-e", help="Last year (default: current)"),
) -> None:
    """Execute full dataset synchronization from WRDS."""
    typer.echo(f"Starting full sync for {dataset}...")

    try:
        manager, client = get_sync_manager()
        try:
            manifest = manager.full_sync(dataset, start_year, end_year)
            typer.echo(
                f"✓ Sync complete: {manifest.row_count} rows in {len(manifest.file_paths)} files"
            )
            typer.echo(f"  Checksum: {manifest.checksum[:16]}...")
        finally:
            client.close()
    except LockAcquisitionError as err:
        typer.echo("✗ Error: Could not acquire lock. Another sync may be running.", err=True)
        raise typer.Exit(2) from err
    except Exception as err:
        typer.echo(f"✗ Error: {err}", err=True)
        raise typer.Exit(1) from err


@app.command()
def incremental(
    dataset: str = typer.Option(None, "--dataset", "-d", help="Dataset to sync"),
    all_datasets: bool = typer.Option(False, "--all", help="Sync all datasets"),
) -> None:
    """Execute incremental sync for new data since last sync."""
    if not dataset and not all_datasets:
        typer.echo("Error: Specify --dataset or --all", err=True)
        raise typer.Exit(1)

    datasets = DATASETS if all_datasets else [dataset]

    try:
        manager, client = get_sync_manager()
        try:
            for ds in datasets:
                typer.echo(f"Syncing {ds}...")
                try:
                    manifest = manager.incremental_sync(ds)
                    typer.echo(f"  ✓ {ds}: {manifest.row_count} rows")
                except ValueError as e:
                    typer.echo(f"  ✗ {ds}: {e}", err=True)
        finally:
            client.close()
    except LockAcquisitionError as err:
        typer.echo("✗ Error: Could not acquire lock.", err=True)
        raise typer.Exit(2) from err
    except Exception as err:
        typer.echo(f"✗ Error: {err}", err=True)
        raise typer.Exit(1) from err


@app.command()
def status() -> None:
    """Show sync status for all datasets."""
    manifest_manager = ManifestManager(storage_path=MANIFEST_DIR)

    typer.echo("Dataset Sync Status")
    typer.echo("=" * 60)

    for ds in DATASETS:
        manifest = manifest_manager.load_manifest(ds)
        if manifest:
            typer.echo(f"\n{ds}:")
            typer.echo(f"  Last sync: {manifest.sync_timestamp.isoformat()}")
            typer.echo(f"  Date range: {manifest.start_date} to {manifest.end_date}")
            typer.echo(f"  Rows: {manifest.row_count:,}")
            typer.echo(f"  Files: {len(manifest.file_paths)}")
            typer.echo(f"  Schema: {manifest.schema_version}")
            typer.echo(f"  Status: {manifest.validation_status}")
        else:
            typer.echo(f"\n{ds}: Not synced")


@app.command()
def verify(
    dataset: str = typer.Option(..., "--dataset", "-d", help="Dataset to verify"),
) -> None:
    """Verify integrity of synced data (checksums only, no download)."""
    typer.echo(f"Verifying {dataset}...")

    manifest_manager = ManifestManager(storage_path=MANIFEST_DIR)
    validator = DataValidator()
    schema_registry = SchemaRegistry(storage_path=SCHEMA_DIR)

    # Create minimal manager for verification
    manager = SyncManager(
        wrds_client=None,  # type: ignore  # Not needed for verify
        storage_path=WRDS_DIR,
        lock_dir=LOCK_DIR,
        manifest_manager=manifest_manager,
        validator=validator,
        schema_registry=schema_registry,
    )

    errors = manager.verify_integrity(dataset)

    if errors:
        typer.echo(f"✗ Verification failed with {len(errors)} error(s):", err=True)
        for error in errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo("✓ Verification passed")


@app.command("lock-status")
def lock_status() -> None:
    """Show current lock status for all datasets."""
    typer.echo("Lock Status")
    typer.echo("=" * 60)

    for ds in DATASETS:
        lock_path = LOCK_DIR / f"{ds}.lock"
        if lock_path.exists():
            import json

            with open(lock_path) as f:
                lock_data = json.load(f)
            typer.echo(f"\n{ds}: LOCKED")
            typer.echo(f"  PID: {lock_data.get('pid')}")
            typer.echo(f"  Host: {lock_data.get('hostname')}")
            typer.echo(f"  Acquired: {lock_data.get('acquired_at')}")
            typer.echo(f"  Expires: {lock_data.get('expires_at')}")
        else:
            typer.echo(f"\n{ds}: unlocked")


@app.command("force-unlock")
def force_unlock(
    dataset: str = typer.Option(..., "--dataset", "-d", help="Dataset to unlock"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Force-unlock a dataset. USE WITH CAUTION."""
    lock_path = LOCK_DIR / f"{dataset}.lock"

    if not lock_path.exists():
        typer.echo(f"{dataset} is not locked")
        return

    if not confirm:
        typer.echo(f"WARNING: Force-unlocking {dataset} may cause data corruption")
        typer.echo("if another sync process is actually running.")
        if not typer.confirm("Are you sure?"):
            raise typer.Abort()

    lock_path.unlink()
    # fsync parent directory for crash safety
    _fsync_directory(lock_path.parent)
    typer.echo(f"✓ Unlocked {dataset}")
    logger.warning(
        "Force-unlocked dataset",
        extra={"event": "sync.lock.force_unlock", "dataset": dataset},
    )


def _fsync_directory(dir_path: Path) -> None:
    """Sync directory for crash safety."""
    try:
        fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # Best effort


if __name__ == "__main__":
    app()
