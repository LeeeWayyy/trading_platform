#!/usr/bin/env python3
"""
Paper trading automation script.

Executes complete end-to-end paper trading workflow with a single command.
Coordinates Signal Service (T3), Execution Gateway (T4), and Orchestrator (T5)
to generate signals, size positions, and submit orders.

This is the entry point for daily paper trading automation. It provides:
- One-command execution of the complete trading pipeline
- Health checks for all dependencies
- Simple P&L calculation and reporting
- Formatted console output with progress indicators
- JSON export capability for analysis
- Cron-compatible for daily scheduling

Usage:
    python scripts/paper_run.py
    python scripts/paper_run.py --symbols AAPL MSFT GOOGL
    python scripts/paper_run.py --capital 100000 --max-position-size 20000
    python scripts/paper_run.py --output results.json

Examples:
    # Basic run with defaults from .env
    $ python scripts/paper_run.py

    # Custom symbols and capital
    $ python scripts/paper_run.py --symbols AAPL MSFT --capital 50000

    # Save results to JSON
    $ python scripts/paper_run.py --output /tmp/paper_run_$(date +%Y%m%d).json

    # Dry run (check dependencies without executing)
    $ python scripts/paper_run.py --dry-run

    # Verbose mode for debugging
    $ python scripts/paper_run.py --verbose

Exit Codes:
    0: Success
    1: Dependency errors (services unavailable)
    2: Orchestration/runtime errors
    3: Configuration/data errors

See Also:
    - ADR-0007: Paper run automation architecture
    - /docs/CONCEPTS/pnl-calculation.md: P&L explanation
    - /docs/IMPLEMENTATION_GUIDES/t6-paper-run.md: Implementation guide
"""

import sys
import os
import asyncio
import argparse
import json
from pathlib import Path
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Third-party imports
import httpx
from dotenv import load_dotenv


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments for paper trading run.

    Provides CLI interface for:
    - Trading parameters (symbols, capital, position size)
    - Service URLs (override .env defaults)
    - Output options (JSON export, dry-run mode)
    - Verbosity control

    Returns:
        Parsed arguments with all parameters needed for paper trading run.
        Arguments override .env configuration when provided.

    Example:
        >>> import sys
        >>> sys.argv = ['paper_run.py', '--symbols', 'AAPL', 'MSFT']
        >>> args = parse_arguments()
        >>> args.symbols
        ['AAPL', 'MSFT']

    Notes:
        - All arguments are optional (defaults from .env or hard-coded)
        - CLI arguments have highest priority over .env
        - Use --help to see all available options
    """
    parser = argparse.ArgumentParser(
        description='Execute end-to-end paper trading workflow',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic run with defaults
  python scripts/paper_run.py

  # Custom symbols and capital
  python scripts/paper_run.py --symbols AAPL MSFT GOOGL --capital 100000

  # Save results to dated JSON file
  python scripts/paper_run.py --output results/paper_run_$(date +%%Y%%m%%d).json

  # Dry run (check dependencies only)
  python scripts/paper_run.py --dry-run

  # Verbose mode for debugging
  python scripts/paper_run.py --verbose

Exit codes:
  0: Success
  1: Dependency errors
  2: Orchestration errors
  3: Configuration errors
        """
    )

    # Trading parameters
    parser.add_argument(
        '--symbols',
        nargs='+',
        default=None,
        help='Symbols to trade (default: from PAPER_RUN_SYMBOLS env var)'
    )

    parser.add_argument(
        '--capital',
        type=float,
        default=None,
        help='Total capital in dollars (default: from PAPER_RUN_CAPITAL env var)'
    )

    parser.add_argument(
        '--max-position-size',
        type=float,
        default=None,
        help='Max position size per symbol (default: from PAPER_RUN_MAX_POSITION_SIZE env var)'
    )

    parser.add_argument(
        '--as-of-date',
        type=str,
        default=None,
        help='As-of date for signals (YYYY-MM-DD, default: today)'
    )

    # Service URLs (override .env)
    parser.add_argument(
        '--orchestrator-url',
        type=str,
        default=None,
        help='Orchestrator service URL (default: from ORCHESTRATOR_URL env var)'
    )

    # Output options
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Save results to JSON file (optional)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Check dependencies without executing orchestration'
    )

    # Verbosity
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output for debugging'
    )

    return parser.parse_args()


def load_configuration(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Load configuration from environment variables with CLI override.

    Configuration priority (highest to lowest):
    1. Command-line arguments
    2. Environment variables from .env file
    3. Hard-coded defaults

    This allows flexible configuration while providing sensible defaults.

    Args:
        args: Parsed command-line arguments from argparse

    Returns:
        Configuration dictionary with all required parameters including:
        - symbols: List of stock symbols to trade
        - capital: Total capital allocation (Decimal)
        - max_position_size: Maximum per-symbol position (Decimal)
        - orchestrator_url: URL of Orchestrator Service (T5)
        - output_file: Optional path to save JSON results
        - dry_run: Boolean flag for dependency check only
        - verbose: Boolean flag for detailed output

    Raises:
        ValueError: If required configuration is missing from all sources

    Example:
        >>> import argparse
        >>> args = argparse.Namespace(symbols=['AAPL'], capital=None)
        >>> # Assuming PAPER_RUN_CAPITAL=100000 in .env
        >>> config = load_configuration(args)
        >>> config['capital']
        Decimal('100000')
        >>> config['symbols']
        ['AAPL']

    Notes:
        - Uses Decimal for financial calculations (avoids float precision issues)
        - Symbols can be list or comma-separated string in .env
        - All monetary values converted to Decimal immediately
    """
    # Load .env file (searches parent directories if not found in cwd)
    load_dotenv()

    def get_config(
        cli_value: Optional[Any],
        env_var: str,
        default: Optional[Any] = None,
        required: bool = True
    ) -> Any:
        """
        Get configuration value with CLI > ENV > DEFAULT priority.

        Args:
            cli_value: Value from command-line argument (highest priority)
            env_var: Environment variable name to check
            default: Default value if not in CLI or ENV
            required: If True, raise ValueError if no value found

        Returns:
            Configuration value from highest priority source

        Raises:
            ValueError: If required=True and no value found
        """
        # Priority 1: CLI argument
        if cli_value is not None:
            return cli_value

        # Priority 2: Environment variable
        env_value = os.getenv(env_var)
        if env_value is not None:
            return env_value

        # Priority 3: Default value
        if default is not None:
            return default

        # Error if required but missing
        if required:
            raise ValueError(
                f"Missing required configuration: {env_var}\n"
                f"Provide via:\n"
                f"  1. CLI argument (highest priority)\n"
                f"  2. Environment variable in .env file\n"
                f"  3. Hard-coded default (if available)\n"
                f"\n"
                f"Example CLI: --{env_var.lower().replace('_', '-')} VALUE\n"
                f"Example ENV: {env_var}=VALUE in .env file"
            )

        return None

    # Parse symbols (can be list from CLI or comma-separated string from .env)
    symbols_raw = get_config(
        args.symbols,
        'PAPER_RUN_SYMBOLS',
        'AAPL,MSFT,GOOGL'  # Default MVP symbols
    )
    symbols = (
        symbols_raw if isinstance(symbols_raw, list)
        else [s.strip() for s in symbols_raw.split(',')]
    )

    # Build configuration dictionary
    config = {
        # Trading parameters (convert to Decimal for precision)
        'symbols': symbols,
        'capital': Decimal(str(get_config(
            args.capital,
            'PAPER_RUN_CAPITAL',
            '100000'  # $100k default
        ))),
        'max_position_size': Decimal(str(get_config(
            args.max_position_size,
            'PAPER_RUN_MAX_POSITION_SIZE',
            '20000'  # $20k per symbol default
        ))),
        'as_of_date': args.as_of_date,  # None = today

        # Service URLs
        'orchestrator_url': get_config(
            args.orchestrator_url,
            'ORCHESTRATOR_URL',
            'http://localhost:8003'  # T5 default port
        ),

        # Output options
        'output_file': args.output,
        'dry_run': args.dry_run,
        'verbose': args.verbose,
    }

    return config


async def check_dependencies(config: Dict[str, Any]) -> None:
    """
    Check that all required services are healthy before execution.

    Verifies:
    - Orchestrator Service (T5) is reachable and responding
    - T5's health check will validate T3 and T4 availability

    This fails fast if any dependency is unavailable, preventing
    partial execution and providing clear error messages.

    Args:
        config: Configuration dictionary with service URLs

    Raises:
        RuntimeError: If any service is unavailable or unhealthy, with
                     detailed error message and troubleshooting steps

    Example:
        >>> config = {'orchestrator_url': 'http://localhost:8003'}
        >>> await check_dependencies(config)
        # Prints: ✓ Orchestrator (http://localhost:8003/)
        # Raises RuntimeError if service down

    Notes:
        - Uses 5-second timeout per check
        - Only checks Orchestrator (T5), which validates T3 and T4
        - Provides specific troubleshooting steps in error messages
        - Safe to call multiple times (no side effects)
    """
    print("\n[1/5] Checking dependencies...")

    # Services to check
    # Note: We only check T5 Orchestrator, which has its own health check
    # that validates T3 (Signal Service) and T4 (Execution Gateway)
    services = [
        ("Orchestrator", f"{config['orchestrator_url']}/"),
    ]

    # Check each service with timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in services:
            try:
                response = await client.get(url)

                if response.status_code == 200:
                    print(f"  ✓ {name} ({url})")
                else:
                    raise RuntimeError(
                        f"{name} unhealthy: HTTP {response.status_code}\n"
                        f"URL: {url}\n"
                        f"Response: {response.text[:200]}\n"
                        f"\n"
                        f"Troubleshooting:\n"
                        f"1. Check service logs for errors\n"
                        f"2. Verify service configuration\n"
                        f"3. Restart service if needed"
                    )

            except httpx.ConnectError as e:
                raise RuntimeError(
                    f"{name} unavailable: Connection refused\n"
                    f"URL: {url}\n"
                    f"Error: {e}\n"
                    f"\n"
                    f"Troubleshooting:\n"
                    f"1. Check if service is running:\n"
                    f"   ps aux | grep orchestrator\n"
                    f"2. Start service if needed:\n"
                    f"   uvicorn apps.orchestrator.main:app --port 8003\n"
                    f"3. Check firewall/network settings\n"
                    f"4. Verify port {url.split(':')[-1].split('/')[0]} is not in use"
                )

            except httpx.TimeoutException:
                raise RuntimeError(
                    f"{name} timeout: No response within 5 seconds\n"
                    f"URL: {url}\n"
                    f"\n"
                    f"Service may be:\n"
                    f"- Overloaded (check CPU/memory usage)\n"
                    f"- Stuck (check logs for deadlocks)\n"
                    f"- Slow due to external dependency (database, etc.)\n"
                    f"\n"
                    f"Troubleshooting:\n"
                    f"1. Check service logs: tail -f logs/{name.lower()}.log\n"
                    f"2. Check system resources: top or htop\n"
                    f"3. Restart service if stuck"
                )


async def trigger_orchestration(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trigger orchestration run via Orchestrator Service API.

    Calls POST /api/v1/orchestration/run with configured parameters
    to execute the complete trading workflow:
    1. T5 fetches signals from T3 (Signal Service)
    2. T5 performs position sizing
    3. T5 submits orders to T4 (Execution Gateway)
    4. T5 persists results to database

    Args:
        config: Configuration with symbols, capital, max_position_size, etc.

    Returns:
        Orchestration result dictionary containing:
        - run_id: UUID of the orchestration run
        - status: 'completed', 'failed', or 'partial'
        - num_signals: Number of signals generated
        - num_orders_submitted: Number of orders submitted
        - num_orders_accepted: Number of orders accepted
        - num_orders_rejected: Number of orders rejected
        - mappings: List of signal-order mappings
        - duration_seconds: Time taken for orchestration

    Raises:
        httpx.HTTPStatusError: If API returns 4xx or 5xx status
        RuntimeError: If orchestration fails or returns error

    Example:
        >>> config = {
        ...     'symbols': ['AAPL', 'MSFT'],
        ...     'capital': Decimal('100000'),
        ...     'max_position_size': Decimal('20000'),
        ...     'orchestrator_url': 'http://localhost:8003'
        ... }
        >>> result = await trigger_orchestration(config)
        >>> result['status']
        'completed'
        >>> result['num_orders_accepted']
        2

    Notes:
        - Uses 60-second timeout (orchestration can take time)
        - Converts Decimal to float for JSON serialization
        - Verbose mode prints full request/response for debugging
        - Orchestrator handles all retry logic internally
    """
    print("\n[2/5] Triggering orchestration run...")

    url = f"{config['orchestrator_url']}/api/v1/orchestration/run"

    # Build request payload
    # Note: Convert Decimal to float for JSON serialization
    payload = {
        'symbols': config['symbols'],
        'capital': float(config['capital']),
        'max_position_size': float(config['max_position_size']),
    }

    # Optional: as_of_date for historical runs
    if config['as_of_date']:
        payload['as_of_date'] = config['as_of_date']

    # Debug output in verbose mode
    if config['verbose']:
        print(f"\n  Request URL: {url}")
        print(f"  Payload:")
        print(json.dumps(payload, indent=2))

    # Call Orchestrator API
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

            print(f"  Run ID: {result.get('run_id', 'unknown')}")

            if config['verbose']:
                print(f"\n  Response:")
                print(json.dumps(result, indent=2, default=str))

            return result

        except httpx.HTTPStatusError as e:
            # Extract error details from response
            error_detail = "Unknown error"
            try:
                error_json = e.response.json()
                error_detail = error_json.get('detail', str(error_json))
            except Exception:
                error_detail = e.response.text[:500]

            raise RuntimeError(
                f"Orchestration API error: HTTP {e.response.status_code}\n"
                f"URL: {url}\n"
                f"Error: {error_detail}\n"
                f"\n"
                f"Troubleshooting:\n"
                f"1. Check Orchestrator logs for details\n"
                f"2. Verify T3 and T4 services are healthy\n"
                f"3. Check database connectivity\n"
                f"4. Validate request parameters are correct"
            )

        except httpx.TimeoutException:
            raise RuntimeError(
                f"Orchestration timeout: No response within 60 seconds\n"
                f"URL: {url}\n"
                f"\n"
                f"Orchestration may be taking longer than expected.\n"
                f"This can happen if:\n"
                f"- Many symbols being processed\n"
                f"- T3 or T4 responding slowly\n"
                f"- Database queries are slow\n"
                f"\n"
                f"Check Orchestrator logs for progress."
            )


def calculate_simple_pnl(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate simple P&L metrics from orchestration result.

    This calculates NOTIONAL value (total dollar amount of positions),
    not actual profit/loss. Actual P&L requires tracking price changes
    over time, which will be added in P1.

    Notional value = sum(abs(quantity * price)) for all accepted orders

    This is useful for:
    - Verifying correct order sizing
    - Checking capital allocation
    - Validating max position size limits
    - Tracking execution success rate

    Args:
        result: Orchestration result from T5 API containing:
                - mappings: List of signal-order mappings
                - num_signals, num_orders_submitted, etc.

    Returns:
        Dictionary with P&L metrics:
        - total_notional: Total dollar value of positions (Decimal)
        - num_signals: Number of signals generated
        - num_orders_submitted: Number of orders submitted to T4
        - num_orders_accepted: Number of orders accepted by T4
        - num_orders_rejected: Number of orders rejected by T4
        - success_rate: Percentage of orders accepted (float, 0-100)
        - duration_seconds: Time taken for orchestration

    Example:
        >>> result = {
        ...     'mappings': [
        ...         {'symbol': 'AAPL', 'order_qty': 100, 'order_price': 150.0, 'skip_reason': None},
        ...         {'symbol': 'MSFT', 'order_qty': 50, 'order_price': 300.0, 'skip_reason': None},
        ...     ],
        ...     'num_signals': 2,
        ...     'num_orders_accepted': 2,
        ...     'num_orders_submitted': 2,
        ...     'num_orders_rejected': 0,
        ...     'duration_seconds': 4.2,
        ... }
        >>> pnl = calculate_simple_pnl(result)
        >>> pnl['total_notional']
        Decimal('30000.00')
        >>> pnl['success_rate']
        100.0

    Notes:
        - Notional value is NOT profit/loss
        - Does not account for price changes after entry
        - Skip_reason=None means order was submitted (not skipped)
        - Success rate = accepted / submitted * 100
        - See /docs/CONCEPTS/pnl-calculation.md for P&L types

    See Also:
        - /docs/CONCEPTS/pnl-calculation.md: P&L explanation and examples
        - ADR-0007: Why notional P&L for MVP
    """
    print("\n[3/5] Calculating P&L...")

    # Extract metrics from result
    num_signals = result.get('num_signals', 0)
    num_submitted = result.get('num_orders_submitted', 0)
    num_accepted = result.get('num_orders_accepted', 0)
    num_rejected = result.get('num_orders_rejected', 0)
    duration = result.get('duration_seconds', 0)

    # Calculate total notional value
    # Notional = abs(quantity * price) for each accepted order
    total_notional = Decimal("0")

    for mapping in result.get('mappings', []):
        # Skip orders that were not submitted (skip_reason present)
        if mapping.get('skip_reason') is not None:
            continue

        # Calculate notional for this order
        qty = mapping.get('order_qty', 0)
        price = Decimal(str(mapping.get('order_price', 0)))
        notional = abs(qty * price)
        total_notional += notional

    # Calculate success rate
    success_rate = (num_accepted / num_submitted * 100) if num_submitted > 0 else 0

    # Build metrics dictionary
    pnl_metrics = {
        'total_notional': total_notional,
        'num_signals': num_signals,
        'num_orders_submitted': num_submitted,
        'num_orders_accepted': num_accepted,
        'num_orders_rejected': num_rejected,
        'success_rate': success_rate,
        'duration_seconds': duration,
    }

    # Display metrics to console
    print(f"  Signals Generated:  {pnl_metrics['num_signals']}")
    print(f"  Orders Submitted:   {pnl_metrics['num_orders_submitted']}")
    print(f"  Orders Accepted:    {pnl_metrics['num_orders_accepted']}")
    print(f"  Orders Rejected:    {pnl_metrics['num_orders_rejected']}")
    print(f"  Total Notional:     ${pnl_metrics['total_notional']:,.2f}")
    print(f"  Success Rate:       {pnl_metrics['success_rate']:.1f}%")
    print(f"  Duration:           {pnl_metrics['duration_seconds']:.2f}s")

    return pnl_metrics


def format_console_output(
    config: Dict[str, Any],
    result: Dict[str, Any],
    pnl_metrics: Dict[str, Any]
) -> None:
    """
    Display formatted results to console.

    Prints a summary of the paper trading run with:
    - Header with timestamp and title
    - Configuration parameters used
    - Final status (SUCCESS/FAILED)
    - Clean, professional formatting

    Args:
        config: Configuration used for the run
        result: Orchestration result from T5
        pnl_metrics: P&L metrics calculated

    Example output:
        ========================================================================
          PAPER TRADING RUN - 2025-01-17 09:00:00 EST
        ========================================================================

        Symbols:      AAPL, MSFT, GOOGL
        Capital:      $100,000.00
        Max Position: $20,000.00

        ========================================================================
          PAPER RUN COMPLETE - Status: COMPLETED ✓
        ========================================================================
    """
    # Header with timestamp
    print("\n" + "=" * 80)
    print(f"  PAPER TRADING RUN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 80 + "\n")

    # Configuration summary
    print(f"Symbols:      {', '.join(config['symbols'])}")
    print(f"Capital:      ${config['capital']:,.2f}")
    print(f"Max Position: ${config['max_position_size']:,.2f}")
    if config['as_of_date']:
        print(f"As-of Date:   {config['as_of_date']}")

    # Final status
    status = result.get('status', 'unknown')
    status_symbol = "✓" if status == "completed" else "✗"

    print("\n" + "=" * 80)
    print(f"  PAPER RUN COMPLETE - Status: {status.upper()} {status_symbol}")
    print("=" * 80 + "\n")


async def save_results(
    config: Dict[str, Any],
    result: Dict[str, Any],
    pnl_metrics: Dict[str, Any]
) -> None:
    """
    Save results to JSON file if --output specified.

    Creates JSON file with complete results including:
    - Timestamp of run
    - Configuration parameters
    - Orchestration results
    - P&L metrics
    - Per-order details

    File is created in specified location with parent directories
    created automatically if needed.

    Args:
        config: Configuration dictionary
        result: Orchestration result from T5
        pnl_metrics: Calculated P&L metrics

    Example:
        >>> config = {'output_file': '/tmp/results.json'}
        >>> await save_results(config, result, pnl_metrics)
        # Creates /tmp/results.json with complete results

    Notes:
        - Does nothing if output_file not specified
        - Creates parent directories automatically
        - Converts Decimal to float for JSON serialization
        - ISO format timestamp for easy parsing
    """
    if not config.get('output_file'):
        return

    print("\n[4/5] Saving results...")

    # Build output data structure
    # Note: Convert Decimal to float for JSON serialization
    output_data = {
        'timestamp': datetime.now().isoformat(),
        'parameters': {
            'symbols': config['symbols'],
            'capital': float(config['capital']),
            'max_position_size': float(config['max_position_size']),
            'as_of_date': config.get('as_of_date'),
        },
        'results': {
            'total_notional': float(pnl_metrics['total_notional']),
            'num_signals': pnl_metrics['num_signals'],
            'num_orders_submitted': pnl_metrics['num_orders_submitted'],
            'num_orders_accepted': pnl_metrics['num_orders_accepted'],
            'num_orders_rejected': pnl_metrics['num_orders_rejected'],
            'success_rate': pnl_metrics['success_rate'],
            'duration_seconds': pnl_metrics['duration_seconds'],
        },
        'run_id': result.get('run_id'),
        'status': result.get('status'),
        'orders': [
            {
                'symbol': m.get('symbol'),
                'side': m.get('order_side'),
                'qty': m.get('order_qty'),
                'skip_reason': m.get('skip_reason'),
            }
            for m in result.get('mappings', [])
        ],
    }

    # Write to file
    output_path = Path(config['output_file'])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"  ✓ Saved to: {output_path}")


async def main() -> int:
    """
    Main entry point for paper_run.py script.

    Executes complete workflow:
    1. Parse command-line arguments
    2. Load configuration (CLI > ENV > DEFAULT)
    3. Check all service dependencies
    4. Trigger orchestration via T5 API (or exit if dry-run)
    5. Calculate simple P&L metrics
    6. Display formatted results
    7. Save to JSON file if requested

    Returns:
        Exit code:
        - 0: Success
        - 1: Dependency errors (services unavailable)
        - 2: Orchestration/runtime errors
        - 3: Configuration/data errors

    Example:
        >>> sys.exit(asyncio.run(main()))

    Notes:
        - All steps have progress indicators [1/5], [2/5], etc.
        - Errors are caught and formatted with troubleshooting steps
        - Verbose mode provides additional debug output
        - Safe to run multiple times (orchestration is idempotent)
    """
    try:
        # Parse arguments
        args = parse_arguments()

        # Load configuration (CLI > ENV > DEFAULT priority)
        config = load_configuration(args)

        # [1/5] Check dependencies (fail fast if services down)
        await check_dependencies(config)

        # Dry run exit (just check dependencies, don't execute)
        if config['dry_run']:
            print("\n✓ Dry run complete - all dependencies healthy")
            print("\nTo execute orchestration, run without --dry-run flag:")
            print("  python scripts/paper_run.py")
            return 0

        # [2/5] Trigger orchestration
        result = await trigger_orchestration(config)

        # [3/5] Calculate P&L
        pnl_metrics = calculate_simple_pnl(result)

        # [4/5] Save results (if --output specified)
        await save_results(config, result, pnl_metrics)

        # [5/5] Format and display final output
        format_console_output(config, result, pnl_metrics)

        # Success!
        return 0

    except ValueError as e:
        # Configuration errors (missing env vars, invalid params)
        print(f"\n❌ Configuration Error:", file=sys.stderr)
        print(f"{e}", file=sys.stderr)
        print("\nSee --help for usage information", file=sys.stderr)
        return 3

    except RuntimeError as e:
        # Runtime errors (service unavailable, orchestration failed)
        print(f"\n❌ Runtime Error:", file=sys.stderr)
        print(f"{e}", file=sys.stderr)
        return 2

    except KeyboardInterrupt:
        # User cancelled (Ctrl+C)
        print("\n\n⚠️  Cancelled by user", file=sys.stderr)
        return 130  # Standard exit code for SIGINT

    except Exception as e:
        # Unexpected errors
        print(f"\n❌ Unexpected Error:", file=sys.stderr)
        print(f"{e}", file=sys.stderr)

        # Print full traceback in verbose mode
        if 'config' in locals() and locals()['config'].get('verbose'):
            print("\nFull traceback:", file=sys.stderr)
            import traceback
            traceback.print_exc()

        return 1


if __name__ == '__main__':
    # Run async main and exit with its return code
    sys.exit(asyncio.run(main()))
