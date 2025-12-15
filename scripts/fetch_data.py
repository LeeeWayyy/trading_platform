#!/usr/bin/env python3
"""CLI for fetching market data via UnifiedDataFetcher.

Provides command-line access to the unified data fetcher, enabling consistent
data retrieval from yfinance (development) or CRSP (production) sources.

Exit Codes:
    0: Success
    1: Provider error (unavailable, not supported)
    2: Configuration error (invalid paths, missing config)
    3: Data error (empty result, invalid symbols)

Output Formats:
    --output file.parquet: Write Parquet file
    --output file.csv: Write CSV file
    (no --output): Print to stdout as table

Usage:
    python scripts/fetch_data.py prices --symbols AAPL,MSFT --start 2024-01-01 --end 2024-12-31
    python scripts/fetch_data.py prices --symbols AAPL --start 2024-01-01 --end 2024-12-31 --output prices.parquet
    python scripts/fetch_data.py universe --date 2024-01-15
    python scripts/fetch_data.py status

Examples:
    # Fetch prices for multiple symbols
    $ python scripts/fetch_data.py prices --symbols AAPL,MSFT,GOOGL --start 2024-01-01 --end 2024-03-31

    # Fetch prices and save to Parquet
    $ python scripts/fetch_data.py prices --symbols AAPL --start 2024-01-01 --end 2024-12-31 --output data.parquet

    # Get tradeable universe as of date
    $ python scripts/fetch_data.py universe --date 2024-01-15

    # Check provider status
    $ python scripts/fetch_data.py status

See Also:
    - docs/CONCEPTS/unified-data-fetcher.md: Concept documentation
    - docs/ADRs/ADR-016-data-provider-protocol.md: Architecture decision
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace

# Exit codes
EXIT_SUCCESS = 0
EXIT_PROVIDER_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_DATA_ERROR = 3


def create_fetcher() -> tuple:
    """Create UnifiedDataFetcher with available providers.

    Returns:
        Tuple of (UnifiedDataFetcher, FetcherConfig).

    Raises:
        ConfigurationError: If configuration is invalid.
    """
    from libs.data_providers import (
        FetcherConfig,
        UnifiedDataFetcher,
        YFinanceProvider,
    )

    config = FetcherConfig.from_env()

    # Validate configured paths exist and are directories
    config.validate_paths()

    # Initialize yfinance provider if path configured
    yfinance_provider = None
    if config.yfinance_storage_path is not None:
        yfinance_provider = YFinanceProvider(
            storage_path=config.yfinance_storage_path,
        )

    # Initialize CRSP provider if path configured
    crsp_provider = None
    if config.crsp_storage_path is not None:
        from libs.data_providers import CRSPLocalProvider

        crsp_provider = CRSPLocalProvider(
            data_dir=config.crsp_storage_path,
        )

    fetcher = UnifiedDataFetcher(
        config=config,
        yfinance_provider=yfinance_provider,
        crsp_provider=crsp_provider,
    )

    return fetcher, config


def handle_prices(args: Namespace) -> int:
    """Handle prices subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code.
    """
    from libs.data_providers import (
        ConfigurationError,
        FetcherConfig,
        ProductionProviderRequiredError,
        ProviderNotSupportedError,
        ProviderType,
        ProviderUnavailableError,
        UnifiedDataFetcher,
        YFinanceProvider,
    )

    # Parse arguments
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    if not symbols or symbols == [""]:
        print("Error: No symbols provided", file=sys.stderr)
        return EXIT_DATA_ERROR

    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError as e:
        print(f"Invalid date format: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if start_date > end_date:
        print(
            f"Start date ({start_date}) must be before end date ({end_date})",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    try:
        # Create fetcher with overridden provider if specified
        config = FetcherConfig.from_env()
        if args.provider != "auto":
            config.provider = ProviderType(args.provider)

        # Validate configured paths exist and are directories
        config.validate_paths()

        # Initialize yfinance provider if path configured
        yfinance_provider = None
        if config.yfinance_storage_path is not None:
            yfinance_provider = YFinanceProvider(
                storage_path=config.yfinance_storage_path,
            )

        # Initialize CRSP provider if path configured
        crsp_provider = None
        if config.crsp_storage_path is not None:
            from libs.data_providers import CRSPLocalProvider

            crsp_provider = CRSPLocalProvider(
                data_dir=config.crsp_storage_path,
            )

        fetcher = UnifiedDataFetcher(
            config=config,
            yfinance_provider=yfinance_provider,
            crsp_provider=crsp_provider,
        )

        # Fetch data
        df = fetcher.get_daily_prices(symbols, start_date, end_date)

        if df.is_empty():
            print("No data returned", file=sys.stderr)
            return EXIT_DATA_ERROR

        # Output
        if args.output:
            output_path = Path(args.output)
            if output_path.suffix == ".parquet":
                df.write_parquet(output_path)
            elif output_path.suffix == ".csv":
                df.write_csv(output_path)
            else:
                print(f"Unknown output format: {output_path.suffix}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            print(f"Wrote {df.height} rows to {output_path}")
        else:
            print(df)

        return EXIT_SUCCESS

    except (
        ProviderUnavailableError,
        ProviderNotSupportedError,
        ProductionProviderRequiredError,
    ) as e:
        print(f"Provider error: {e}", file=sys.stderr)
        return EXIT_PROVIDER_ERROR
    except ConfigurationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValueError as e:
        print(f"Data error: {e}", file=sys.stderr)
        return EXIT_DATA_ERROR


def handle_universe(args: Namespace) -> int:
    """Handle universe subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code.
    """
    from libs.data_providers import (
        ConfigurationError,
        ProductionProviderRequiredError,
        ProviderNotSupportedError,
        ProviderUnavailableError,
    )

    try:
        as_of_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError as e:
        print(f"Invalid date format: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        fetcher, _ = create_fetcher()

        # Get universe
        symbols = fetcher.get_universe(as_of_date)

        if not symbols:
            print("No symbols in universe", file=sys.stderr)
            return EXIT_DATA_ERROR

        # Output
        if args.output:
            output_path = Path(args.output)
            output_path.write_text("\n".join(symbols) + "\n")
            print(f"Wrote {len(symbols)} symbols to {output_path}")
        else:
            for symbol in symbols:
                print(symbol)

        return EXIT_SUCCESS

    except (
        ProviderUnavailableError,
        ProviderNotSupportedError,
        ProductionProviderRequiredError,
    ) as e:
        print(f"Provider error: {e}", file=sys.stderr)
        return EXIT_PROVIDER_ERROR
    except ConfigurationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValueError as e:
        print(f"Data error: {e}", file=sys.stderr)
        return EXIT_DATA_ERROR


def handle_status(args: Namespace) -> int:
    """Handle status subcommand.

    Output format:
        Environment: development
        Configured Provider: auto
        Active Provider: crsp
        Available Providers: crsp, yfinance
        Fallback Enabled: true
        CRSP Available: true
        yfinance Available: true

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code.
    """
    from libs.data_providers import (
        ConfigurationError,
        DataProviderError,
        ProviderType,
    )

    try:
        fetcher, config = create_fetcher()

        print(f"Environment: {config.environment}")
        print(f"Configured Provider: {config.provider.value}")

        try:
            active = fetcher.get_active_provider()
            print(f"Active Provider: {active}")
        except DataProviderError as e:
            print(f"Active Provider: ERROR - {e}")

        available = []
        if fetcher.is_available(ProviderType.CRSP):
            available.append("crsp")
        if fetcher.is_available(ProviderType.YFINANCE):
            available.append("yfinance")

        print(f"Available Providers: {', '.join(available) or 'none'}")
        print(f"Fallback Enabled: {str(config.fallback_enabled).lower()}")
        print(f"CRSP Available: {str(fetcher.is_available(ProviderType.CRSP)).lower()}")
        print(f"yfinance Available: {str(fetcher.is_available(ProviderType.YFINANCE)).lower()}")

        return EXIT_SUCCESS

    except ConfigurationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        description="Fetch market data via UnifiedDataFetcher",
        epilog="See docs/CONCEPTS/unified-data-fetcher.md for detailed documentation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # prices command
    prices_parser = subparsers.add_parser("prices", help="Fetch daily prices")
    prices_parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated symbols (e.g., AAPL,MSFT,GOOGL)",
    )
    prices_parser.add_argument(
        "--start",
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    prices_parser.add_argument(
        "--end",
        required=True,
        help="End date (YYYY-MM-DD)",
    )
    prices_parser.add_argument(
        "--provider",
        choices=["auto", "yfinance", "crsp"],
        default="auto",
        help="Data provider (default: auto)",
    )
    prices_parser.add_argument(
        "--output",
        help="Output file (.parquet or .csv). Omit for stdout.",
    )

    # universe command
    universe_parser = subparsers.add_parser("universe", help="Get tradeable universe")
    universe_parser.add_argument(
        "--date",
        required=True,
        help="As-of date (YYYY-MM-DD)",
    )
    universe_parser.add_argument(
        "--output",
        help="Output file (.txt, one symbol per line). Omit for stdout.",
    )

    # status command
    subparsers.add_parser("status", help="Show provider status and configuration")

    args = parser.parse_args()

    if args.command == "prices":
        return handle_prices(args)
    elif args.command == "universe":
        return handle_universe(args)
    elif args.command == "status":
        return handle_status(args)
    else:
        parser.print_help()
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
