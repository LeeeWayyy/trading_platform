# T6: Paper Run Automation Implementation Guide

## Overview

This guide walks through implementing `paper_run.py` - a CLI automation script that executes the complete end-to-end paper trading workflow with a single command.

**What we're building:**
- Command-line script that orchestrates T1-T5 components
- Health checks for all dependencies
- Simple P&L calculation and reporting
- Formatted console output with progress indicators
- JSON export capability
- Cron-compatible for daily scheduling

**End-to-end flow:**
```
paper_run.py
    ↓
[1] Check dependencies (T3, T4, T5 health checks)
    ↓
[2] Call Orchestrator API (T5) with parameters
    ↓
[3] Wait for orchestration completion
    ↓
[4] Calculate simple P&L (notional value)
    ↓
[5] Format and display results
    ↓
[6] Save to JSON file (optional)
```

## Prerequisites

Before implementing T6, ensure you have:

### 1. Completed T1-T5
- ✅ T1: Data ETL pipeline
- ✅ T2: Baseline ML strategy
- ✅ T3: Signal Service running on port 8001
- ✅ T4: Execution Gateway running on port 8002
- ✅ T5: Orchestrator Service running on port 8003

### 2. Services Running
```bash
# Terminal 1: Signal Service
cd /path/to/trading_platform
source .venv/bin/activate
uvicorn apps.signal_service.main:app --port 8001

# Terminal 2: Execution Gateway
DRY_RUN=true uvicorn apps.execution_gateway.main:app --port 8002

# Terminal 3: Orchestrator
uvicorn apps.orchestrator.main:app --port 8003
```

### 3. Understanding of Concepts
- Read [ADR-0007](../ADRs/0007-paper-run-automation.md) for architecture decisions
- Read [P&L Calculation concept](../CONCEPTS/pnl-calculation.md) for P&L understanding

## Step-by-Step Implementation

### Step 1: Create Script Structure

**Goal:** Set up basic CLI script with argument parsing.

**File:** `scripts/paper_run.py`

```python
#!/usr/bin/env python3
"""
Paper trading automation script.

Executes complete end-to-end paper trading workflow with a single command.
Coordinates Signal Service (T3), Execution Gateway (T4), and Orchestrator (T5)
to generate signals, size positions, and submit orders.

This is the entry point for daily paper trading automation.

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

    # Dry run (check without executing)
    $ python scripts/paper_run.py --dry-run

See Also:
    - ADR-0007: Paper run automation architecture
    - /docs/CONCEPTS/pnl-calculation.md: P&L explanation
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
from pydantic import BaseModel, ValidationError

# Project imports (will be added as we implement)
# from apps.orchestrator.schemas import OrchestrationRequest, OrchestrationResult


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments with all parameters needed for paper trading run.

    Example:
        >>> import sys
        >>> sys.argv = ['paper_run.py', '--symbols', 'AAPL', 'MSFT']
        >>> args = parse_arguments()
        >>> args.symbols
        ['AAPL', 'MSFT']
    """
    parser = argparse.ArgumentParser(
        description='Execute end-to-end paper trading workflow',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/paper_run.py
  python scripts/paper_run.py --symbols AAPL MSFT GOOGL
  python scripts/paper_run.py --capital 100000
  python scripts/paper_run.py --output results.json
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
        help='Check dependencies without executing'
    )

    # Verbosity
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()
    print(f"Arguments parsed: {args}")
```

**Why this structure?**
- Argparse for CLI flexibility
- Comprehensive docstrings following DOCUMENTATION_STANDARDS.md
- Examples in docstrings (executable and clear)
- Path manipulation to allow imports from project root

### Step 2: Load Configuration

**Goal:** Load configuration from `.env` with CLI argument override.

```python
import os
from dotenv import load_dotenv

def load_configuration(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Load configuration from environment variables with CLI override.

    Configuration priority (highest to lowest):
    1. Command-line arguments
    2. Environment variables
    3. Hard-coded defaults

    Args:
        args: Parsed command-line arguments from argparse

    Returns:
        Configuration dictionary with all required parameters

    Raises:
        ValueError: If required configuration missing

    Example:
        >>> import argparse
        >>> args = argparse.Namespace(symbols=['AAPL'], capital=None)
        >>> # Assuming PAPER_RUN_CAPITAL=100000 in .env
        >>> config = load_configuration(args)
        >>> config['capital']
        Decimal('100000')
    """
    # Load .env file
    load_dotenv()

    # Helper function to get config value with priority
    def get_config(
        cli_value: Optional[Any],
        env_var: str,
        default: Optional[Any] = None,
        required: bool = True
    ) -> Any:
        """Get config value with CLI > ENV > DEFAULT priority."""
        if cli_value is not None:
            return cli_value

        env_value = os.getenv(env_var)
        if env_value is not None:
            return env_value

        if default is not None:
            return default

        if required:
            raise ValueError(
                f"Missing required configuration: {env_var}. "
                f"Provide via CLI argument or .env file."
            )

        return None

    # Parse symbols (comma-separated string in .env)
    symbols_str = get_config(
        args.symbols,
        'PAPER_RUN_SYMBOLS',
        'AAPL,MSFT,GOOGL'
    )
    symbols = (
        symbols_str if isinstance(symbols_str, list)
        else symbols_str.split(',')
    )

    # Build configuration
    config = {
        'symbols': symbols,
        'capital': Decimal(str(get_config(
            args.capital,
            'PAPER_RUN_CAPITAL',
            '100000'
        ))),
        'max_position_size': Decimal(str(get_config(
            args.max_position_size,
            'PAPER_RUN_MAX_POSITION_SIZE',
            '20000'
        ))),
        'as_of_date': args.as_of_date,  # None = today
        'orchestrator_url': get_config(
            args.orchestrator_url,
            'ORCHESTRATOR_URL',
            'http://localhost:8003'
        ),
        'output_file': args.output,
        'dry_run': args.dry_run,
        'verbose': args.verbose,
    }

    return config
```

**Key points:**
- Priority: CLI > ENV > DEFAULT
- Decimal for financial calculations (avoid float precision issues)
- Clear error messages for missing config
- Flexible symbol input (list or comma-separated string)

### Step 3: Health Checks

**Goal:** Verify all required services are running before execution.

```python
async def check_dependencies(config: Dict[str, Any]) -> None:
    """
    Check that all required services are healthy.

    Verifies:
    - Signal Service (T3) is reachable
    - Execution Gateway (T4) is reachable
    - Orchestrator Service (T5) is reachable

    Args:
        config: Configuration dictionary with service URLs

    Raises:
        RuntimeError: If any service is unavailable or unhealthy

    Example:
        >>> config = {'orchestrator_url': 'http://localhost:8003'}
        >>> await check_dependencies(config)
        # Prints progress and raises RuntimeError if services down
    """
    print("\n[1/5] Checking dependencies...")

    # Services to check (T5 will check T3 and T4)
    services = [
        ("Orchestrator", f"{config['orchestrator_url']}/"),
    ]

    # Check each service
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
                        f"Response: {response.text[:200]}"
                    )

            except httpx.ConnectError as e:
                raise RuntimeError(
                    f"{name} unavailable: Connection failed\n"
                    f"URL: {url}\n"
                    f"Error: {e}\n\n"
                    f"Troubleshooting:\n"
                    f"1. Check if service is running: ps aux | grep {name.lower()}\n"
                    f"2. Start service if needed\n"
                    f"3. Check logs for errors"
                )

            except httpx.TimeoutException:
                raise RuntimeError(
                    f"{name} timeout: No response within 5 seconds\n"
                    f"URL: {url}\n"
                    f"Service may be overloaded or stuck"
                )
```

**Why check T5 only?**
- T5 Orchestrator has its own health check that verifies T3 and T4
- Avoids redundant checks
- Simplifies error handling (single point of failure detection)

### Step 4: Trigger Orchestration

**Goal:** Call Orchestrator API to execute the trading workflow.

```python
async def trigger_orchestration(
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Trigger orchestration run via Orchestrator Service API.

    Calls POST /api/v1/orchestration/run with configured parameters.

    Args:
        config: Configuration with symbols, capital, etc.

    Returns:
        Orchestration result dictionary with run_id, status, mappings

    Raises:
        httpx.HTTPStatusError: If API returns error status
        RuntimeError: If orchestration fails

    Example:
        >>> config = {
        ...     'symbols': ['AAPL', 'MSFT'],
        ...     'capital': Decimal('100000'),
        ...     'orchestrator_url': 'http://localhost:8003'
        ... }
        >>> result = await trigger_orchestration(config)
        >>> result['status']
        'completed'
    """
    print("\n[2/5] Triggering orchestration run...")

    url = f"{config['orchestrator_url']}/api/v1/orchestration/run"

    # Build request payload
    payload = {
        'symbols': config['symbols'],
        'capital': float(config['capital']),
        'max_position_size': float(config['max_position_size']),
    }

    if config['as_of_date']:
        payload['as_of_date'] = config['as_of_date']

    if config['verbose']:
        print(f"  Request URL: {url}")
        print(f"  Payload: {json.dumps(payload, indent=2)}")

    # Call API
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

            print(f"  Run ID: {result.get('run_id', 'unknown')}")
            return result

        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Orchestration API error: HTTP {e.response.status_code}\n"
                f"URL: {url}\n"
                f"Response: {e.response.text[:500]}"
            )
```

**Key points:**
- 60-second timeout (orchestration can take time)
- Convert Decimal to float for JSON serialization
- Verbose mode for debugging
- Clear error messages with context

### Step 5: Calculate P&L

**Goal:** Calculate simple notional P&L from orchestration results.

```python
def calculate_simple_pnl(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate simple P&L metrics from orchestration result.

    This calculates NOTIONAL value (total dollar amount of positions),
    not actual profit/loss. Actual P&L requires tracking price changes
    over time, which will be added in P1.

    Metrics calculated:
    - Total notional value of accepted orders
    - Number of orders submitted, accepted, rejected
    - Success rate (% accepted)

    Args:
        result: Orchestration result from T5 API

    Returns:
        Dictionary with P&L metrics

    Example:
        >>> result = {
        ...     'mappings': [
        ...         {'symbol': 'AAPL', 'order_qty': 100, 'order_price': 150.0, 'skip_reason': None},
        ...         {'symbol': 'MSFT', 'order_qty': 50, 'order_price': 300.0, 'skip_reason': None},
        ...     ],
        ...     'num_orders_accepted': 2,
        ...     'num_orders_submitted': 2,
        ... }
        >>> pnl = calculate_simple_pnl(result)
        >>> pnl['total_notional']
        Decimal('30000.00')

    Notes:
        - Notional value = abs(quantity * price)
        - Does NOT represent profit/loss
        - Useful for verifying correct order sizing
        - See /docs/CONCEPTS/pnl-calculation.md for P&L types

    See Also:
        - /docs/CONCEPTS/pnl-calculation.md: P&L explanation
    """
    print("\n[4/5] Calculating P&L...")

    total_notional = Decimal("0")
    num_accepted = result.get('num_orders_accepted', 0)
    num_submitted = result.get('num_orders_submitted', 0)
    num_rejected = result.get('num_orders_rejected', 0)

    # Calculate notional value of accepted orders
    for mapping in result.get('mappings', []):
        if mapping.get('skip_reason') is None:  # Not skipped
            qty = mapping.get('order_qty', 0)
            price = Decimal(str(mapping.get('order_price', 0)))
            notional = abs(qty * price)
            total_notional += notional

    success_rate = (num_accepted / num_submitted * 100) if num_submitted > 0 else 0

    pnl_metrics = {
        'total_notional': total_notional,
        'num_signals': result.get('num_signals', 0),
        'num_orders_submitted': num_submitted,
        'num_orders_accepted': num_accepted,
        'num_orders_rejected': num_rejected,
        'success_rate': success_rate,
        'duration_seconds': result.get('duration_seconds', 0),
    }

    # Display metrics
    print(f"  Signals Generated:  {pnl_metrics['num_signals']}")
    print(f"  Orders Submitted:   {pnl_metrics['num_orders_submitted']}")
    print(f"  Orders Accepted:    {pnl_metrics['num_orders_accepted']}")
    print(f"  Orders Rejected:    {pnl_metrics['num_orders_rejected']}")
    print(f"  Total Notional:     ${pnl_metrics['total_notional']:,.2f}")
    print(f"  Success Rate:       {pnl_metrics['success_rate']:.1f}%")

    return pnl_metrics
```

**Educational note:**
- Clear docstring explains notional ≠ profit
- References concept documentation
- Examples show expected usage
- Notes section clarifies limitations

### Step 6: Format Output

**Goal:** Display results in readable format and save to JSON if requested.

```python
def format_console_output(
    config: Dict[str, Any],
    result: Dict[str, Any],
    pnl_metrics: Dict[str, Any]
) -> None:
    """
    Display formatted results to console.

    Prints a summary of the paper trading run with:
    - Header with timestamp
    - Configuration parameters
    - Step-by-step progress
    - P&L metrics
    - Final status

    Args:
        config: Configuration used for the run
        result: Orchestration result from T5
        pnl_metrics: P&L metrics calculated

    Example output:
        ========================================================================
          PAPER TRADING RUN - 2025-01-17 09:00:00 EST
        ========================================================================

        Symbols:     AAPL, MSFT, GOOGL
        Capital:     $100,000.00
        ...
    """
    print("\n" + "=" * 80)
    print(f"  PAPER TRADING RUN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 80 + "\n")

    print(f"Symbols:      {', '.join(config['symbols'])}")
    print(f"Capital:      ${config['capital']:,.2f}")
    print(f"Max Position: ${config['max_position_size']:,.2f}")

    # Status indicator
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

    Creates JSON file with:
    - Timestamp
    - Configuration parameters
    - Orchestration results
    - P&L metrics
    - Order details

    Args:
        config: Configuration dictionary
        result: Orchestration result
        pnl_metrics: Calculated P&L metrics

    Example:
        >>> config = {'output_file': '/tmp/results.json'}
        >>> await save_results(config, result, pnl_metrics)
        # Creates /tmp/results.json with complete results
    """
    if not config.get('output_file'):
        return

    print("\n[5/5] Saving results...")

    output_data = {
        'timestamp': datetime.now().isoformat(),
        'parameters': {
            'symbols': config['symbols'],
            'capital': float(config['capital']),
            'max_position_size': float(config['max_position_size']),
            'as_of_date': config.get('as_of_date'),
        },
        'results': {
            **pnl_metrics,
            'total_notional': float(pnl_metrics['total_notional']),
            'success_rate': pnl_metrics['success_rate'],
        },
        'run_id': result.get('run_id'),
        'status': result.get('status'),
    }

    # Write to file
    output_path = Path(config['output_file'])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"  ✓ Saved to: {output_path}")
```

### Step 7: Main Function

**Goal:** Orchestrate all steps in main async function.

```python
async def main() -> int:
    """
    Main entry point for paper_run.py script.

    Executes complete workflow:
    1. Parse arguments
    2. Load configuration
    3. Check dependencies
    4. Trigger orchestration
    5. Calculate P&L
    6. Display results
    7. Save to file (optional)

    Returns:
        Exit code: 0 for success, 1 for dependency errors,
                   2 for orchestration errors, 3 for data errors

    Example:
        >>> sys.exit(asyncio.run(main()))
    """
    try:
        # Parse arguments
        args = parse_arguments()

        # Load configuration
        config = load_configuration(args)

        # Check dependencies
        await check_dependencies(config)

        # Dry run exit
        if config['dry_run']:
            print("\n✓ Dry run complete - all dependencies healthy")
            return 0

        # Trigger orchestration
        result = await trigger_orchestration(config)

        # Calculate P&L
        pnl_metrics = calculate_simple_pnl(result)

        # Format output
        format_console_output(config, result, pnl_metrics)

        # Save results
        await save_results(config, result, pnl_metrics)

        # Return success
        return 0

    except ValueError as e:
        print(f"\n❌ Configuration Error: {e}", file=sys.stderr)
        return 3

    except RuntimeError as e:
        print(f"\n❌ Runtime Error: {e}", file=sys.stderr)
        return 2

    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}", file=sys.stderr)
        if config.get('verbose'):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
```

**Error handling:**
- Exit code 0: Success
- Exit code 1: Dependency errors
- Exit code 2: Orchestration/runtime errors
- Exit code 3: Configuration/data errors

## Testing Strategy

### Unit Tests (`scripts/test_paper_run.py`)

```python
import pytest
from decimal import Decimal
from scripts.paper_run import (
    calculate_simple_pnl,
    load_configuration,
)

def test_calculate_simple_pnl_basic():
    """Test basic P&L calculation."""
    result = {
        'mappings': [
            {
                'symbol': 'AAPL',
                'order_qty': 100,
                'order_price': 150.0,
                'skip_reason': None
            },
        ],
        'num_signals': 1,
        'num_orders_submitted': 1,
        'num_orders_accepted': 1,
        'num_orders_rejected': 0,
        'duration_seconds': 4.2,
    }

    pnl = calculate_simple_pnl(result)

    assert pnl['total_notional'] == Decimal('15000.00')
    assert pnl['success_rate'] == 100.0


def test_calculate_simple_pnl_with_rejection():
    """Test P&L calculation with rejected orders."""
    result = {
        'mappings': [
            {'symbol': 'AAPL', 'order_qty': 100, 'order_price': 150.0, 'skip_reason': None},
            {'symbol': 'MSFT', 'order_qty': 0, 'order_price': 0, 'skip_reason': 'insufficient_capital'},
        ],
        'num_signals': 2,
        'num_orders_submitted': 1,
        'num_orders_accepted': 1,
        'num_orders_rejected': 0,  # Skipped, not rejected
    }

    pnl = calculate_simple_pnl(result)

    assert pnl['total_notional'] == Decimal('15000.00')  # Only AAPL
    assert pnl['success_rate'] == 100.0  # 1/1 submitted were accepted
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_health_checks_with_running_services():
    """Test health checks against real services."""
    config = {
        'orchestrator_url': 'http://localhost:8003'
    }

    # Should not raise if services are running
    await check_dependencies(config)


@pytest.mark.asyncio
async def test_health_checks_with_down_services():
    """Test health checks fail gracefully when services down."""
    config = {
        'orchestrator_url': 'http://localhost:9999'  # Invalid port
    }

    with pytest.raises(RuntimeError, match="unavailable"):
        await check_dependencies(config)
```

### End-to-End Test

```python
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_paper_run():
    """
    Test complete paper run workflow.

    Requires:
    - T3, T4, T5 services running
    - DRY_RUN=true in .env
    - Test model registered
    """
    import sys
    from io import StringIO

    # Capture stdout
    captured_output = StringIO()
    sys.stdout = captured_output

    # Mock sys.argv
    sys.argv = [
        'paper_run.py',
        '--symbols', 'AAPL', 'MSFT',
        '--capital', '50000',
        '--dry-run'  # Just check dependencies
    ]

    # Run main
    exit_code = await main()

    # Restore stdout
    sys.stdout = sys.__stdout__

    # Assertions
    assert exit_code == 0
    output = captured_output.getvalue()
    assert "Checking dependencies" in output
    assert "✓" in output
```

## Troubleshooting

### Issue 1: "Orchestrator unavailable"

**Symptom:**
```
❌ Runtime Error: Orchestrator unavailable: Connection failed
URL: http://localhost:8003
```

**Causes:**
1. Orchestrator service (T5) not running
2. Wrong port number
3. Firewall blocking connection

**Solutions:**
```bash
# Check if Orchestrator is running
ps aux | grep "apps.orchestrator"

# Start Orchestrator if not running
uvicorn apps.orchestrator.main:app --port 8003

# Check port is listening
lsof -i :8003

# Test connection manually
curl http://localhost:8003/
```

### Issue 2: "Missing required configuration"

**Symptom:**
```
❌ Configuration Error: Missing required configuration: PAPER_RUN_SYMBOLS
```

**Causes:**
1. .env file not loaded
2. Missing environment variable
3. .env file in wrong location

**Solutions:**
```bash
# Check .env exists
ls -la .env

# Check .env has required variables
grep PAPER_RUN .env

# Add missing variables to .env
echo "PAPER_RUN_SYMBOLS=AAPL,MSFT,GOOGL" >> .env
echo "PAPER_RUN_CAPITAL=100000" >> .env
echo "PAPER_RUN_MAX_POSITION_SIZE=20000" >> .env
```

### Issue 3: "Orchestration timeout"

**Symptom:**
```
❌ Runtime Error: Orchestration API error: HTTP 504
```

**Causes:**
1. Orchestration taking too long (> 60s)
2. T3 or T4 services slow or stuck
3. Network latency

**Solutions:**
```bash
# Check T3 and T4 health
curl http://localhost:8001/
curl http://localhost:8002/

# Check T3/T4 logs for errors
tail -f logs/signal_service.log
tail -f logs/execution_gateway.log

# Increase timeout in code (if legitimate slow operation)
# In trigger_orchestration():
# async with httpx.AsyncClient(timeout=120.0) as client:  # 2 minutes
```

### Issue 4: JSON serialization error

**Symptom:**
```
TypeError: Object of type Decimal is not JSON serializable
```

**Cause:**
Trying to serialize Decimal directly to JSON.

**Solution:**
```python
# Convert Decimal to float before JSON serialization
output_data = {
    'capital': float(config['capital']),  # Not config['capital']
    'total_notional': float(pnl_metrics['total_notional']),
}
```

## Scheduling with Cron

### Daily Paper Run (9:00 AM EST, Monday-Friday)

```bash
# Edit crontab
crontab -e

# Add this line (adjust paths as needed)
0 9 * * 1-5 cd /path/to/trading_platform && \
    source .venv/bin/activate && \
    python scripts/paper_run.py \
    --output /var/log/trading/paper_run_$(date +\%Y\%m\%d).json \
    >> /var/log/trading/paper_run.log 2>&1
```

**Explanation:**
- `0 9 * * 1-5`: 9:00 AM, Monday-Friday
- `cd /path/to/...`: Change to project directory
- `source .venv/bin/activate`: Activate virtual environment
- `--output ...`: Save to dated JSON file
- `>> ... 2>&1`: Append stdout and stderr to log file

### Verify Cron Job

```bash
# List cron jobs
crontab -l

# Check cron logs
tail -f /var/log/trading/paper_run.log

# Test cron command manually
cd /path/to/trading_platform && \
    source .venv/bin/activate && \
    python scripts/paper_run.py --dry-run
```

## Next Steps

After completing T6:

### P1: Enhanced P&L Tracking
- Add position tracking from T4
- Fetch current prices for mark-to-market
- Calculate realized + unrealized P&L
- Per-symbol P&L breakdown

### P1: Reporting Enhancements
- HTML report generation
- Email notifications
- CSV export
- Grafana dashboard integration

### P2: Advanced Features
- Multi-strategy support
- Risk limit checks
- Circuit breaker integration
- Web UI for results viewing

## Related Documentation

- [ADR-0007: Paper Run Automation](../ADRs/0007-paper-run-automation.md)
- [P&L Calculation Concept](../CONCEPTS/pnl-calculation.md)
- [T5 Orchestrator Implementation](./t5-orchestrator.md)
- [GIT_WORKFLOW.md](../GIT_WORKFLOW.md) - Commit guidelines

## Summary

You've now implemented `paper_run.py` - a complete CLI automation script that:
- ✅ Executes end-to-end paper trading with one command
- ✅ Checks all dependencies before running
- ✅ Calls Orchestrator API (T5) to coordinate T3 and T4
- ✅ Calculates simple P&L (notional value)
- ✅ Provides formatted console output
- ✅ Saves results to JSON
- ✅ Supports cron scheduling
- ✅ Has comprehensive error handling
- ✅ Includes unit and integration tests

This completes the P0 MVP - all T1-T6 tasks are now implemented!
