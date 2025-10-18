#!/usr/bin/env python3
"""
Unit and integration tests for paper_run.py script.

Tests the complete paper trading automation workflow including:
- Argument parsing
- Configuration loading
- Health checks
- P&L calculation
- Output formatting
- End-to-end execution

Usage:
    pytest scripts/test_paper_run.py -v
    pytest scripts/test_paper_run.py -v -k test_health_checks

Requirements:
    - pytest
    - pytest-asyncio
    - httpx (for mocking)
"""

import pytest
import sys
import os
import json
import argparse
from pathlib import Path
from decimal import Decimal
from unittest.mock import Mock, patch, AsyncMock
from io import StringIO

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.paper_run import (
    parse_arguments,
    load_configuration,
    calculate_simple_pnl,
    calculate_enhanced_pnl,
    fetch_current_prices,
    fetch_positions,
    format_console_output,
    check_dependencies,
    trigger_orchestration,
    save_results,
)


class TestArgumentParsing:
    """Test command-line argument parsing."""

    def test_parse_arguments_defaults(self):
        """Test parsing with no arguments (all defaults)."""
        with patch('sys.argv', ['paper_run.py']):
            args = parse_arguments()

            assert args.symbols is None
            assert args.capital is None
            assert args.max_position_size is None
            assert args.as_of_date is None
            assert args.orchestrator_url is None
            assert args.output is None
            assert args.dry_run is False
            assert args.verbose is False

    def test_parse_arguments_symbols(self):
        """Test parsing custom symbols."""
        with patch('sys.argv', ['paper_run.py', '--symbols', 'AAPL', 'MSFT', 'GOOGL']):
            args = parse_arguments()

            assert args.symbols == ['AAPL', 'MSFT', 'GOOGL']

    def test_parse_arguments_capital(self):
        """Test parsing custom capital."""
        with patch('sys.argv', ['paper_run.py', '--capital', '50000']):
            args = parse_arguments()

            assert args.capital == 50000.0

    def test_parse_arguments_dry_run(self):
        """Test dry-run flag."""
        with patch('sys.argv', ['paper_run.py', '--dry-run']):
            args = parse_arguments()

            assert args.dry_run is True

    def test_parse_arguments_verbose(self):
        """Test verbose flag."""
        with patch('sys.argv', ['paper_run.py', '--verbose']):
            args = parse_arguments()

            assert args.verbose is True

    def test_parse_arguments_output(self):
        """Test output file path."""
        with patch('sys.argv', ['paper_run.py', '--output', '/tmp/results.json']):
            args = parse_arguments()

            assert args.output == '/tmp/results.json'


class TestConfigurationLoading:
    """Test configuration loading with priority: CLI > ENV > DEFAULT."""

    def test_load_configuration_cli_override(self):
        """Test CLI arguments override environment variables."""
        args = argparse.Namespace(
            symbols=['TSLA'],
            capital=75000,
            max_position_size=15000,
            as_of_date='2024-12-31',
            orchestrator_url='http://example.com',
            execution_gateway_url='http://example.com:8002',
            output='/tmp/out.json',
            dry_run=True,
            verbose=True
        )

        with patch.dict(os.environ, {
            'PAPER_RUN_SYMBOLS': 'AAPL,MSFT',  # Should be ignored
            'PAPER_RUN_CAPITAL': '100000',     # Should be ignored
        }):
            config = load_configuration(args)

            assert config['symbols'] == ['TSLA']  # From CLI, not ENV
            assert config['capital'] == Decimal('75000')
            assert config['max_position_size'] == Decimal('15000')
            assert config['as_of_date'] == '2024-12-31'
            assert config['orchestrator_url'] == 'http://example.com'
            assert config['output_file'] == '/tmp/out.json'
            assert config['dry_run'] is True
            assert config['verbose'] is True

    def test_load_configuration_env_vars(self):
        """Test loading from environment variables."""
        args = argparse.Namespace(
            symbols=None,
            capital=None,
            max_position_size=None,
            as_of_date=None,
            orchestrator_url=None,
            execution_gateway_url=None,
            output=None,
            dry_run=False,
            verbose=False
        )

        with patch.dict(os.environ, {
            'PAPER_RUN_SYMBOLS': 'AAPL,MSFT,GOOGL',
            'PAPER_RUN_CAPITAL': '100000',
            'PAPER_RUN_MAX_POSITION_SIZE': '20000',
            'ORCHESTRATOR_URL': 'http://localhost:8003',
        }):
            config = load_configuration(args)

            assert config['symbols'] == ['AAPL', 'MSFT', 'GOOGL']
            assert config['capital'] == Decimal('100000')
            assert config['max_position_size'] == Decimal('20000')
            assert config['orchestrator_url'] == 'http://localhost:8003'

    def test_load_configuration_defaults(self):
        """Test hard-coded defaults when no CLI or ENV."""
        args = argparse.Namespace(
            symbols=None,
            capital=None,
            max_position_size=None,
            as_of_date=None,
            orchestrator_url=None,
            execution_gateway_url=None,
            output=None,
            dry_run=False,
            verbose=False
        )

        with patch.dict(os.environ, {}, clear=True):
            config = load_configuration(args)

            # Defaults from code
            assert config['symbols'] == ['AAPL', 'MSFT', 'GOOGL']
            assert config['capital'] == Decimal('100000')
            assert config['max_position_size'] == Decimal('20000')
            assert config['orchestrator_url'] == 'http://localhost:8003'

    def test_load_configuration_decimal_precision(self):
        """Test that financial values use Decimal (not float)."""
        args = argparse.Namespace(
            symbols=None,
            capital=100000.50,  # Float input
            max_position_size=20000.25,
            as_of_date=None,
            orchestrator_url=None,
            execution_gateway_url=None,
            output=None,
            dry_run=False,
            verbose=False
        )

        config = load_configuration(args)

        # Should be Decimal, not float
        assert isinstance(config['capital'], Decimal)
        assert isinstance(config['max_position_size'], Decimal)
        assert config['capital'] == Decimal('100000.50')
        assert config['max_position_size'] == Decimal('20000.25')


class TestPNLCalculation:
    """Test P&L calculation logic."""

    def test_calculate_simple_pnl_basic(self):
        """Test basic P&L calculation with two accepted orders."""
        result = {
            'mappings': [
                {
                    'symbol': 'AAPL',
                    'order_qty': 100,
                    'order_price': 150.0,
                    'skip_reason': None
                },
                {
                    'symbol': 'MSFT',
                    'order_qty': 50,
                    'order_price': 300.0,
                    'skip_reason': None
                },
            ],
            'num_signals': 2,
            'num_orders_submitted': 2,
            'num_orders_accepted': 2,
            'num_orders_rejected': 0,
            'duration_seconds': 4.2,
        }

        # Capture print output
        with patch('sys.stdout', new=StringIO()):
            pnl = calculate_simple_pnl(result)

        assert pnl['total_notional'] == Decimal('30000.00')  # (100*150) + (50*300)
        assert pnl['num_signals'] == 2
        assert pnl['num_orders_submitted'] == 2
        assert pnl['num_orders_accepted'] == 2
        assert pnl['num_orders_rejected'] == 0
        assert pnl['success_rate'] == 100.0  # 2/2 = 100%
        assert pnl['duration_seconds'] == 4.2

    def test_calculate_simple_pnl_with_rejection(self):
        """Test P&L calculation with rejected orders."""
        result = {
            'mappings': [
                {'symbol': 'AAPL', 'order_qty': 100, 'order_price': 150.0, 'skip_reason': None},
                {'symbol': 'MSFT', 'order_qty': 0, 'order_price': 0, 'skip_reason': 'insufficient_capital'},
            ],
            'num_signals': 2,
            'num_orders_submitted': 1,  # Only AAPL submitted
            'num_orders_accepted': 1,
            'num_orders_rejected': 0,
            'duration_seconds': 3.5,
        }

        with patch('sys.stdout', new=StringIO()):
            pnl = calculate_simple_pnl(result)

        assert pnl['total_notional'] == Decimal('15000.00')  # Only AAPL
        assert pnl['success_rate'] == 100.0  # 1/1 = 100%

    def test_calculate_simple_pnl_partial_failure(self):
        """Test P&L with some orders rejected."""
        result = {
            'mappings': [
                {'symbol': 'AAPL', 'order_qty': 100, 'order_price': 150.0, 'skip_reason': None},
                {'symbol': 'MSFT', 'order_qty': 50, 'order_price': 300.0, 'skip_reason': None},
            ],
            'num_signals': 2,
            'num_orders_submitted': 2,
            'num_orders_accepted': 1,  # Only one accepted
            'num_orders_rejected': 1,   # One rejected
            'duration_seconds': 5.0,
        }

        with patch('sys.stdout', new=StringIO()):
            pnl = calculate_simple_pnl(result)

        # Notional still counts both (since both have skip_reason=None)
        assert pnl['total_notional'] == Decimal('30000.00')
        assert pnl['success_rate'] == 50.0  # 1/2 = 50%

    def test_calculate_simple_pnl_short_positions(self):
        """Test P&L calculation with short positions (negative qty)."""
        result = {
            'mappings': [
                {'symbol': 'AAPL', 'order_qty': -100, 'order_price': 150.0, 'skip_reason': None},  # Short
            ],
            'num_signals': 1,
            'num_orders_submitted': 1,
            'num_orders_accepted': 1,
            'num_orders_rejected': 0,
            'duration_seconds': 2.0,
        }

        with patch('sys.stdout', new=StringIO()):
            pnl = calculate_simple_pnl(result)

        # abs() used, so notional is positive
        assert pnl['total_notional'] == Decimal('15000.00')

    def test_calculate_simple_pnl_zero_submitted(self):
        """Test P&L when no orders submitted (avoid division by zero)."""
        result = {
            'mappings': [],
            'num_signals': 2,
            'num_orders_submitted': 0,
            'num_orders_accepted': 0,
            'num_orders_rejected': 0,
            'duration_seconds': 1.0,
        }

        with patch('sys.stdout', new=StringIO()):
            pnl = calculate_simple_pnl(result)

        assert pnl['total_notional'] == Decimal('0')
        assert pnl['success_rate'] == 0  # Avoid division by zero


class TestEnhancedPNLCalculation:
    """Test enhanced P&L calculation with realized/unrealized breakdown."""

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_open_positions_only(self):
        """Test enhanced P&L with only open positions (unrealized only)."""
        positions = [
            {
                'symbol': 'AAPL',
                'qty': 100,
                'avg_entry_price': '150.00',
                'realized_pl': '0.00'
            },
            {
                'symbol': 'MSFT',
                'qty': 50,
                'avg_entry_price': '300.00',
                'realized_pl': '0.00'
            }
        ]

        current_prices = {
            'AAPL': Decimal('152.00'),  # +$2 per share
            'MSFT': Decimal('305.00')   # +$5 per share
        }

        pnl = await calculate_enhanced_pnl(positions, current_prices)

        # Unrealized P&L
        # AAPL: (152 - 150) * 100 = +$200
        # MSFT: (305 - 300) * 50 = +$250
        # Total: +$450
        assert pnl['unrealized_pnl'] == Decimal('450.00')
        assert pnl['realized_pnl'] == Decimal('0.00')
        assert pnl['total_pnl'] == Decimal('450.00')
        assert pnl['num_open_positions'] == 2
        assert pnl['num_closed_positions'] == 0

        # Per-symbol checks
        assert pnl['per_symbol']['AAPL']['unrealized'] == Decimal('200.00')
        assert pnl['per_symbol']['AAPL']['realized'] == Decimal('0.00')
        assert pnl['per_symbol']['AAPL']['status'] == 'open'
        assert pnl['per_symbol']['MSFT']['unrealized'] == Decimal('250.00')

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_closed_positions_only(self):
        """Test enhanced P&L with only closed positions (realized only)."""
        positions = [
            {
                'symbol': 'AAPL',
                'qty': 0,  # Closed
                'avg_entry_price': '150.00',
                'realized_pl': '500.00'
            },
            {
                'symbol': 'MSFT',
                'qty': 0,  # Closed
                'avg_entry_price': '300.00',
                'realized_pl': '-100.00'
            }
        ]

        current_prices = {}  # No prices needed for closed positions

        pnl = await calculate_enhanced_pnl(positions, current_prices)

        assert pnl['realized_pnl'] == Decimal('400.00')  # 500 - 100
        assert pnl['unrealized_pnl'] == Decimal('0.00')
        assert pnl['total_pnl'] == Decimal('400.00')
        assert pnl['num_open_positions'] == 0
        assert pnl['num_closed_positions'] == 2

        # Per-symbol checks
        assert pnl['per_symbol']['AAPL']['realized'] == Decimal('500.00')
        assert pnl['per_symbol']['AAPL']['unrealized'] == Decimal('0.00')
        assert pnl['per_symbol']['AAPL']['status'] == 'closed'
        assert pnl['per_symbol']['MSFT']['realized'] == Decimal('-100.00')

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_mixed_positions(self):
        """Test enhanced P&L with both open and closed positions."""
        positions = [
            {
                'symbol': 'AAPL',
                'qty': 100,  # Open long
                'avg_entry_price': '150.00',
                'realized_pl': '0.00'
            },
            {
                'symbol': 'MSFT',
                'qty': 0,  # Closed
                'avg_entry_price': '300.00',
                'realized_pl': '500.00'
            },
            {
                'symbol': 'GOOGL',
                'qty': -50,  # Open short
                'avg_entry_price': '140.00',
                'realized_pl': '100.00'  # Some realized from partial close
            }
        ]

        current_prices = {
            'AAPL': Decimal('152.00'),   # +$2 profit (long)
            'GOOGL': Decimal('135.00')   # +$5 profit (short)
        }

        pnl = await calculate_enhanced_pnl(positions, current_prices)

        # Realized: 0 + 500 + 100 = 600
        # Unrealized: (152-150)*100 + (135-140)*(-50) = 200 + 250 = 450
        # Total: 1050
        assert pnl['realized_pnl'] == Decimal('600.00')
        assert pnl['unrealized_pnl'] == Decimal('450.00')
        assert pnl['total_pnl'] == Decimal('1050.00')
        assert pnl['num_open_positions'] == 2
        assert pnl['num_closed_positions'] == 1

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_short_position_profit(self):
        """Test unrealized P&L for profitable short position."""
        positions = [
            {
                'symbol': 'AAPL',
                'qty': -100,  # Short 100 shares at $150
                'avg_entry_price': '150.00',
                'realized_pl': '0.00'
            }
        ]

        current_prices = {
            'AAPL': Decimal('145.00')  # Price dropped to $145 (profit)
        }

        pnl = await calculate_enhanced_pnl(positions, current_prices)

        # Short profit: (145 - 150) * (-100) = (-5) * (-100) = +500
        assert pnl['unrealized_pnl'] == Decimal('500.00')
        assert pnl['total_pnl'] == Decimal('500.00')

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_short_position_loss(self):
        """Test unrealized P&L for losing short position."""
        positions = [
            {
                'symbol': 'AAPL',
                'qty': -100,  # Short 100 shares at $150
                'avg_entry_price': '150.00',
                'realized_pl': '0.00'
            }
        ]

        current_prices = {
            'AAPL': Decimal('155.00')  # Price rose to $155 (loss)
        }

        pnl = await calculate_enhanced_pnl(positions, current_prices)

        # Short loss: (155 - 150) * (-100) = 5 * (-100) = -500
        assert pnl['unrealized_pnl'] == Decimal('-500.00')
        assert pnl['total_pnl'] == Decimal('-500.00')

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_missing_price_fallback(self):
        """Test fallback to avg_entry_price when current price unavailable."""
        positions = [
            {
                'symbol': 'AAPL',
                'qty': 100,
                'avg_entry_price': '150.00',
                'realized_pl': '0.00'
            }
        ]

        current_prices = {}  # Missing AAPL price

        # Capture stderr to check warning
        with patch('sys.stderr', new=StringIO()):
            pnl = await calculate_enhanced_pnl(positions, current_prices)

        # Fallback to avg_entry_price means zero unrealized P&L
        assert pnl['unrealized_pnl'] == Decimal('0.00')
        assert pnl['per_symbol']['AAPL']['current_price'] == Decimal('150.00')

    @pytest.mark.asyncio
    async def test_calculate_enhanced_pnl_empty_positions(self):
        """Test enhanced P&L with no positions."""
        positions = []
        current_prices = {}

        pnl = await calculate_enhanced_pnl(positions, current_prices)

        assert pnl['realized_pnl'] == Decimal('0.00')
        assert pnl['unrealized_pnl'] == Decimal('0.00')
        assert pnl['total_pnl'] == Decimal('0.00')
        assert pnl['num_open_positions'] == 0
        assert pnl['num_closed_positions'] == 0
        assert pnl['per_symbol'] == {}


class TestFetchPositions:
    """Test position fetching from T4 Execution Gateway."""

    @pytest.mark.asyncio
    async def test_fetch_positions_success(self):
        """Test successful position fetching."""
        expected_positions = [
            {'symbol': 'AAPL', 'qty': 100, 'avg_entry_price': '150.00'},
            {'symbol': 'MSFT', 'qty': 50, 'avg_entry_price': '300.00'}
        ]

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value=expected_positions)
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            positions = await fetch_positions('http://localhost:8002')

        assert positions == expected_positions

    @pytest.mark.asyncio
    async def test_fetch_positions_invalid_format(self):
        """Test error handling for invalid response format."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={'invalid': 'format'})  # Not a list
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(RuntimeError, match="unexpected format"):
                await fetch_positions('http://localhost:8002')

    @pytest.mark.asyncio
    async def test_fetch_positions_connection_error(self):
        """Test handling of connection errors."""
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(RuntimeError, match="unavailable"):
                await fetch_positions('http://localhost:8002')


class TestFetchCurrentPrices:
    """Test price fetching from Alpaca API."""

    @pytest.mark.asyncio
    async def test_fetch_current_prices_success(self):
        """Test successful price fetching."""
        symbols = ['AAPL', 'MSFT']
        config = {}

        # Mock AlpacaExecutor
        mock_executor = Mock()
        mock_executor.get_latest_quotes = Mock(return_value={
            'AAPL': {
                'last_price': Decimal('152.75'),
                'bid_price': Decimal('152.74'),
                'ask_price': Decimal('152.76')
            },
            'MSFT': {
                'last_price': Decimal('380.50'),
                'bid_price': Decimal('380.49'),
                'ask_price': Decimal('380.51')
            }
        })

        with patch('scripts.paper_run.AlpacaExecutor', return_value=mock_executor):
            with patch.dict(os.environ, {
                'ALPACA_API_KEY': 'test_key',
                'ALPACA_SECRET_KEY': 'test_secret',
                'ALPACA_BASE_URL': 'https://paper-api.alpaca.markets'
            }):
                prices = await fetch_current_prices(symbols, config)

        assert prices['AAPL'] == Decimal('152.75')
        assert prices['MSFT'] == Decimal('380.50')

    @pytest.mark.asyncio
    async def test_fetch_current_prices_empty_symbols(self):
        """Test fetching prices with empty symbol list."""
        prices = await fetch_current_prices([], {})
        assert prices == {}

    @pytest.mark.asyncio
    async def test_fetch_current_prices_alpaca_error(self):
        """Test graceful degradation when Alpaca API fails."""
        from apps.execution_gateway.alpaca_client import AlpacaConnectionError

        symbols = ['AAPL']
        config = {}

        mock_executor = Mock()
        mock_executor.get_latest_quotes = Mock(side_effect=AlpacaConnectionError("API down"))

        with patch('scripts.paper_run.AlpacaExecutor', return_value=mock_executor):
            with patch.dict(os.environ, {
                'ALPACA_API_KEY': 'test_key',
                'ALPACA_SECRET_KEY': 'test_secret'
            }):
                with patch('sys.stderr', new=StringIO()):
                    prices = await fetch_current_prices(symbols, config)

        # Should return empty dict on error (graceful degradation)
        assert prices == {}

    @pytest.mark.asyncio
    async def test_fetch_current_prices_missing_quote_fields(self):
        """Test handling of missing quote fields."""
        symbols = ['AAPL']
        config = {}

        # Mock quote with only bid_price (no last_price)
        mock_executor = Mock()
        mock_executor.get_latest_quotes = Mock(return_value={
            'AAPL': {
                'last_price': None,
                'bid_price': Decimal('152.00'),
                'ask_price': Decimal('153.00')
            }
        })

        with patch('scripts.paper_run.AlpacaExecutor', return_value=mock_executor):
            with patch.dict(os.environ, {
                'ALPACA_API_KEY': 'test_key',
                'ALPACA_SECRET_KEY': 'test_secret'
            }):
                prices = await fetch_current_prices(symbols, config)

        # Should calculate mid-quote: (152 + 153) / 2 = 152.50
        assert prices['AAPL'] == Decimal('152.50')


class TestHealthChecks:
    """Test service health checks."""

    @pytest.mark.asyncio
    async def test_check_dependencies_success(self):
        """Test successful health check."""
        config = {'orchestrator_url': 'http://localhost:8003'}

        # Mock httpx client
        mock_response = AsyncMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with patch('sys.stdout', new=StringIO()):
                # Should not raise
                await check_dependencies(config)

    @pytest.mark.asyncio
    async def test_check_dependencies_unhealthy(self):
        """Test health check with unhealthy service (non-200 status)."""
        config = {'orchestrator_url': 'http://localhost:8003'}

        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(RuntimeError, match="unhealthy"):
                await check_dependencies(config)

    @pytest.mark.asyncio
    async def test_check_dependencies_connection_error(self):
        """Test health check with connection error."""
        import httpx

        config = {'orchestrator_url': 'http://localhost:8003'}

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(RuntimeError, match="unavailable"):
                await check_dependencies(config)

    @pytest.mark.asyncio
    async def test_check_dependencies_timeout(self):
        """Test health check with timeout."""
        import httpx

        config = {'orchestrator_url': 'http://localhost:8003'}

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("Timeout")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(RuntimeError, match="timeout"):
                await check_dependencies(config)


class TestOrchestrationTrigger:
    """Test orchestration triggering."""

    @pytest.mark.asyncio
    async def test_trigger_orchestration_success(self):
        """Test successful orchestration trigger."""
        config = {
            'symbols': ['AAPL', 'MSFT'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None,
            'orchestrator_url': 'http://localhost:8003',
            'verbose': False,
        }

        expected_result = {
            'run_id': 'test-run-id',
            'status': 'completed',
            'num_signals': 2,
            'num_orders_accepted': 2,
        }

        # Mock response - json() is synchronous in httpx
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value=expected_result)  # Synchronous method
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with patch('sys.stdout', new=StringIO()):
                result = await trigger_orchestration(config)

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_trigger_orchestration_http_error(self):
        """Test orchestration trigger with HTTP error."""
        import httpx

        config = {
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None,
            'orchestrator_url': 'http://localhost:8003',
            'verbose': False,
        }

        # Mock response - json() and raise_for_status() are synchronous
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.text = "Internal error"
        mock_response.json = Mock(return_value={'detail': 'Orchestration failed'})  # Synchronous
        mock_response.raise_for_status = Mock(side_effect=httpx.HTTPStatusError(
            "500", request=Mock(), response=mock_response
        ))  # Synchronous

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(RuntimeError, match="Orchestration API error"):
                await trigger_orchestration(config)


class TestResultsSaving:
    """Test JSON results saving."""

    @pytest.mark.asyncio
    async def test_save_results_creates_file(self, tmp_path):
        """Test saving results to JSON file."""
        output_file = tmp_path / "results.json"

        config = {
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None,
            'output_file': str(output_file),
        }

        result = {
            'run_id': 'test-run-id',
            'status': 'completed',
            'mappings': [],
        }

        pnl_metrics = {
            'total_notional': Decimal('15000.00'),
            'num_signals': 1,
            'num_orders_submitted': 1,
            'num_orders_accepted': 1,
            'num_orders_rejected': 0,
            'success_rate': 100.0,
            'duration_seconds': 3.5,
        }

        with patch('sys.stdout', new=StringIO()):
            await save_results(config, result, pnl_metrics)

        # File should exist
        assert output_file.exists()

        # Load and verify content
        with open(output_file) as f:
            data = json.load(f)

        assert data['run_id'] == 'test-run-id'
        assert data['status'] == 'completed'
        assert data['parameters']['symbols'] == ['AAPL']
        assert data['results']['total_notional'] == 15000.00  # Converted to float
        assert data['results']['success_rate'] == 100.0

    @pytest.mark.asyncio
    async def test_save_results_creates_parent_dirs(self, tmp_path):
        """Test saving results creates parent directories."""
        output_file = tmp_path / "nested" / "dir" / "results.json"

        config = {
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'output_file': str(output_file),
        }

        result = {'run_id': 'test', 'status': 'completed', 'mappings': []}
        pnl_metrics = {
            'total_notional': Decimal('0'),
            'num_signals': 0,
            'num_orders_submitted': 0,
            'num_orders_accepted': 0,
            'num_orders_rejected': 0,
            'success_rate': 0,
            'duration_seconds': 0,
        }

        with patch('sys.stdout', new=StringIO()):
            await save_results(config, result, pnl_metrics)

        # Parent directories should be created
        assert output_file.parent.exists()
        assert output_file.exists()

    @pytest.mark.asyncio
    async def test_save_results_no_output(self):
        """Test saving results when no output file specified."""
        config = {'output_file': None}
        result = {}
        pnl_metrics = {}

        # Should do nothing (not raise)
        await save_results(config, result, pnl_metrics)

    @pytest.mark.asyncio
    async def test_save_results_enhanced_pnl(self, tmp_path):
        """Test saving results with enhanced P&L format."""
        output_file = tmp_path / "results.json"

        config = {
            'symbols': ['AAPL', 'MSFT'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None,
            'output_file': str(output_file),
        }

        result = {
            'run_id': 'test-enhanced-pnl',
            'status': 'completed',
            'mappings': [],
        }

        # Enhanced P&L data structure
        pnl_metrics = {
            'realized_pnl': Decimal('500.00'),
            'unrealized_pnl': Decimal('200.00'),
            'total_pnl': Decimal('700.00'),
            'num_open_positions': 2,
            'num_closed_positions': 1,
            'total_positions': 3,
            'per_symbol': {
                'AAPL': {
                    'realized': Decimal('100.00'),
                    'unrealized': Decimal('150.00'),
                    'qty': 100,
                    'avg_entry_price': Decimal('150.00'),
                    'current_price': Decimal('151.50'),
                    'status': 'open'
                },
                'MSFT': {
                    'realized': Decimal('400.00'),
                    'unrealized': Decimal('0.00'),
                    'qty': 0,
                    'avg_entry_price': Decimal('300.00'),
                    'current_price': None,
                    'status': 'closed'
                }
            }
        }

        with patch('sys.stdout', new=StringIO()):
            await save_results(config, result, pnl_metrics)

        # File should exist
        assert output_file.exists()

        # Load and verify content
        with open(output_file) as f:
            data = json.load(f)

        assert data['run_id'] == 'test-enhanced-pnl'
        assert data['status'] == 'completed'
        assert data['results']['realized_pnl'] == 500.00
        assert data['results']['unrealized_pnl'] == 200.00
        assert data['results']['total_pnl'] == 700.00
        assert data['results']['num_open_positions'] == 2
        assert data['results']['num_closed_positions'] == 1

        # Check per-symbol data
        assert 'AAPL' in data['results']['per_symbol']
        assert data['results']['per_symbol']['AAPL']['qty'] == 100
        assert data['results']['per_symbol']['AAPL']['status'] == 'open'
        assert data['results']['per_symbol']['MSFT']['status'] == 'closed'


class TestConsoleOutput:
    """Test console output formatting."""

    def test_format_console_output_completed(self):
        """Test formatted output for completed run."""
        config = {
            'symbols': ['AAPL', 'MSFT', 'GOOGL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None,
        }

        result = {'status': 'completed'}

        pnl_metrics = {
            'total_notional': Decimal('60000'),
            'success_rate': 100.0,
        }

        # Capture stdout
        captured = StringIO()
        with patch('sys.stdout', captured):
            format_console_output(config, result, pnl_metrics)

        output = captured.getvalue()

        assert 'PAPER TRADING RUN' in output
        assert 'AAPL, MSFT, GOOGL' in output
        assert '$100,000.00' in output
        assert '$20,000.00' in output
        assert 'COMPLETED ✓' in output

    def test_format_console_output_failed(self):
        """Test formatted output for failed run."""
        config = {
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': '2024-12-31',
        }

        result = {'status': 'failed'}
        pnl_metrics = {}

        captured = StringIO()
        with patch('sys.stdout', captured):
            format_console_output(config, result, pnl_metrics)

        output = captured.getvalue()

        assert 'FAILED ✗' in output
        assert 'As-of Date:   2024-12-31' in output


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
