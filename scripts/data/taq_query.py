#!/usr/bin/env python3
"""
TAQ Data Query CLI.

Query TAQ data from local Parquet storage.

Commands:
    minute-bars: Query 1-minute bar data
    realized-volatility: Query daily realized volatility metrics
    spread-metrics: Query daily spread/market quality metrics
    ticks: Query tick samples for a specific date

Usage:
    python scripts/taq_query.py minute-bars --symbols AAPL,MSFT \
        --start 2024-01-01 --end 2024-01-31 --output out.parquet

    python scripts/taq_query.py realized-volatility --symbols AAPL \
        --start 2024-01-01 --end 2024-01-31 --window 5

    python scripts/taq_query.py ticks --date 2024-01-15 --symbols AAPL,MSFT
"""

from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path

import click
import polars as pl

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from libs.data.data_providers.taq_query_provider import TAQLocalProvider  # noqa: E402
from libs.data.data_quality.manifest import ManifestManager  # noqa: E402
from libs.data.data_quality.versioning import DatasetVersionManager  # noqa: E402

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_symbols(symbols_str: str) -> list[str]:
    """Parse comma-separated symbols."""
    return [s.strip().upper() for s in symbols_str.split(",") if s.strip()]


def create_provider(engine: str, as_of: datetime.date | None = None) -> TAQLocalProvider:
    """Create configured TAQLocalProvider instance."""
    storage_path = Path("data/taq")
    manifest_path = Path("data/manifests/taq")
    snapshot_path = Path("data/snapshots/taq")

    manifest_manager = ManifestManager(storage_path=manifest_path)

    version_manager = None
    if as_of is not None:
        version_manager = DatasetVersionManager(
            manifest_manager=manifest_manager,
            snapshots_dir=snapshot_path,
        )

    return TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        engine=engine,  # type: ignore[arg-type]
    )


def output_result(
    df: pl.DataFrame,
    output: str | None,
    format: str,
    show_metrics: bool,
) -> None:
    """Output query results.

    Args:
        df: Result DataFrame.
        output: Output file path (None for stdout).
        format: Output format (parquet, csv, json).
        show_metrics: Show timing/row stats.
    """
    if show_metrics:
        click.echo(f"Rows: {df.height}")
        click.echo(f"Columns: {df.columns}")

    if output:
        output_path = Path(output)
        if format == "parquet":
            df.write_parquet(output_path)
        elif format == "csv":
            df.write_csv(output_path)
        elif format == "json":
            df.write_json(output_path)
        click.echo(f"Written to {output_path}")
    else:
        # Print to stdout
        with pl.Config(tbl_rows=50):
            click.echo(df)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose: bool) -> None:
    """TAQ Data Query CLI.

    Query TAQ data from local Parquet storage.
    """
    setup_logging(verbose)


@cli.command("minute-bars")
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated symbols",
)
@click.option(
    "--start",
    "start_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end",
    "end_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Point-in-time date for snapshot query",
)
@click.option(
    "--engine",
    type=click.Choice(["duckdb", "polars"]),
    default="duckdb",
    help="Query engine (default: duckdb)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output file path",
)
@click.option(
    "--format",
    type=click.Choice(["parquet", "csv", "json"]),
    default="parquet",
    help="Output format (default: parquet)",
)
@click.option(
    "--metrics",
    is_flag=True,
    help="Show timing and row stats",
)
def minute_bars(
    symbols: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    as_of: datetime.datetime | None,
    engine: str,
    output: str | None,
    format: str,
    metrics: bool,
) -> None:
    """Query 1-minute bar data.

    Examples:
        python scripts/taq_query.py minute-bars \\
            --symbols AAPL,MSFT \\
            --start 2024-01-15 \\
            --end 2024-01-15 \\
            --output out.parquet

        python scripts/taq_query.py minute-bars \\
            --symbols AAPL \\
            --start 2024-01-01 \\
            --end 2024-01-31 \\
            --as-of 2024-02-01
    """
    symbol_list = parse_symbols(symbols)
    start = start_date.date()
    end = end_date.date()
    as_of_date = as_of.date() if as_of else None

    logger.info(
        "Querying minute bars",
        extra={
            "symbols": symbol_list,
            "start": str(start),
            "end": str(end),
            "as_of": str(as_of_date),
            "engine": engine,
        },
    )

    import time

    t0 = time.time()

    try:
        provider = create_provider(engine, as_of_date)
        df = provider.fetch_minute_bars(
            symbols=symbol_list,
            start_date=start,
            end_date=end,
            as_of=as_of_date,
        )
        provider.close()

        elapsed = time.time() - t0
        if metrics:
            click.echo(f"Query time: {elapsed:.2f}s")

        output_result(df, output, format, metrics)

    except Exception as e:
        logger.exception("Query failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("realized-volatility")
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated symbols",
)
@click.option(
    "--start",
    "start_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end",
    "end_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--window",
    type=click.Choice(["5", "30"]),
    default="5",
    help="RV sampling window in minutes (default: 5)",
)
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Point-in-time date for snapshot query",
)
@click.option(
    "--engine",
    type=click.Choice(["duckdb", "polars"]),
    default="duckdb",
    help="Query engine (default: duckdb)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output file path",
)
@click.option(
    "--format",
    type=click.Choice(["parquet", "csv", "json"]),
    default="parquet",
    help="Output format (default: parquet)",
)
@click.option(
    "--metrics",
    is_flag=True,
    help="Show timing and row stats",
)
def realized_volatility(
    symbols: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    window: str,
    as_of: datetime.datetime | None,
    engine: str,
    output: str | None,
    format: str,
    metrics: bool,
) -> None:
    """Query daily realized volatility metrics.

    Examples:
        python scripts/taq_query.py realized-volatility \\
            --symbols AAPL \\
            --start 2024-01-01 \\
            --end 2024-01-31 \\
            --window 5
    """
    symbol_list = parse_symbols(symbols)
    start = start_date.date()
    end = end_date.date()
    as_of_date = as_of.date() if as_of else None
    window_int = int(window)

    logger.info(
        "Querying realized volatility",
        extra={
            "symbols": symbol_list,
            "start": str(start),
            "end": str(end),
            "window": window_int,
            "as_of": str(as_of_date),
            "engine": engine,
        },
    )

    import time

    t0 = time.time()

    try:
        provider = create_provider(engine, as_of_date)
        df = provider.fetch_realized_volatility(
            symbols=symbol_list,
            start_date=start,
            end_date=end,
            window=window_int,
            as_of=as_of_date,
        )
        provider.close()

        elapsed = time.time() - t0
        if metrics:
            click.echo(f"Query time: {elapsed:.2f}s")

        output_result(df, output, format, metrics)

    except Exception as e:
        logger.exception("Query failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("spread-metrics")
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated symbols",
)
@click.option(
    "--start",
    "start_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end",
    "end_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Point-in-time date for snapshot query",
)
@click.option(
    "--engine",
    type=click.Choice(["duckdb", "polars"]),
    default="duckdb",
    help="Query engine (default: duckdb)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output file path",
)
@click.option(
    "--format",
    type=click.Choice(["parquet", "csv", "json"]),
    default="parquet",
    help="Output format (default: parquet)",
)
@click.option(
    "--metrics",
    is_flag=True,
    help="Show timing and row stats",
)
def spread_metrics(
    symbols: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    as_of: datetime.datetime | None,
    engine: str,
    output: str | None,
    format: str,
    metrics: bool,
) -> None:
    """Query daily spread/market quality metrics.

    Examples:
        python scripts/taq_query.py spread-metrics \\
            --symbols SPY \\
            --start 2024-01-01 \\
            --end 2024-01-31
    """
    symbol_list = parse_symbols(symbols)
    start = start_date.date()
    end = end_date.date()
    as_of_date = as_of.date() if as_of else None

    logger.info(
        "Querying spread metrics",
        extra={
            "symbols": symbol_list,
            "start": str(start),
            "end": str(end),
            "as_of": str(as_of_date),
            "engine": engine,
        },
    )

    import time

    t0 = time.time()

    try:
        provider = create_provider(engine, as_of_date)
        df = provider.fetch_spread_metrics(
            symbols=symbol_list,
            start_date=start,
            end_date=end,
            as_of=as_of_date,
        )
        provider.close()

        elapsed = time.time() - t0
        if metrics:
            click.echo(f"Query time: {elapsed:.2f}s")

        output_result(df, output, format, metrics)

    except Exception as e:
        logger.exception("Query failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("ticks")
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
    "--engine",
    type=click.Choice(["duckdb", "polars"]),
    default="duckdb",
    help="Query engine (default: duckdb)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output file path",
)
@click.option(
    "--format",
    type=click.Choice(["parquet", "csv", "json"]),
    default="parquet",
    help="Output format (default: parquet)",
)
@click.option(
    "--metrics",
    is_flag=True,
    help="Show timing and row stats",
)
def ticks(
    sample_date: datetime.datetime,
    symbols: str,
    engine: str,
    output: str | None,
    format: str,
    metrics: bool,
) -> None:
    """Query tick samples for a specific date.

    Examples:
        python scripts/taq_query.py ticks \\
            --date 2024-01-15 \\
            --symbols AAPL,MSFT \\
            --output ticks.parquet
    """
    symbol_list = parse_symbols(symbols)
    date = sample_date.date()

    logger.info(
        "Querying tick samples",
        extra={
            "date": str(date),
            "symbols": symbol_list,
            "engine": engine,
        },
    )

    import time

    t0 = time.time()

    try:
        provider = create_provider(engine)
        df = provider.fetch_ticks(
            sample_date=date,
            symbols=symbol_list,
        )
        provider.close()

        elapsed = time.time() - t0
        if metrics:
            click.echo(f"Query time: {elapsed:.2f}s")

        output_result(df, output, format, metrics)

    except Exception as e:
        logger.exception("Query failed", extra={"error": str(e)})
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
