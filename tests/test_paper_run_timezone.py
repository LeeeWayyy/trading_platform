"""
Tests for timezone-aware timestamps in paper_run.py.

Validates that all timestamps in paper_run.py are timezone-aware (UTC)
according to P1.1T4 requirements.

Test Coverage:
- Console output timestamps are timezone-aware
- JSON export timestamps are timezone-aware
- Timestamps use ISO 8601 format
- Timestamps are in UTC timezone
- JSON includes explicit timezone field

Usage:
    pytest tests/test_paper_run_timezone.py -v
"""

import json
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.paper_run import (
    format_console_output,
    save_results,
)


def create_simple_pnl_metrics(total_notional: Decimal = Decimal('50000')) -> dict:
    """Helper function to create complete simple P&L metrics for testing."""
    return {
        'total_notional': total_notional,
        'num_signals': 2,
        'num_orders_submitted': 2,
        'num_orders_accepted': 2,
        'num_orders_rejected': 0,
        'success_rate': 100.0,
        'duration_seconds': 4.2
    }


class TestConsoleOutputTimezone:
    """Test that console output uses timezone-aware timestamps."""

    def test_console_output_timestamp_format(self, capsys):
        """Test that console output timestamp is in ISO 8601 format with timezone."""
        # Arrange
        config = {
            'symbols': ['AAPL', 'MSFT'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None
        }
        result = {'status': 'completed'}
        pnl_metrics = create_simple_pnl_metrics(Decimal('50000'))

        # Fixed timezone-aware timestamp
        fixed_time = datetime(2025, 1, 17, 9, 0, 0, tzinfo=timezone.utc)

        # Act
        format_console_output(config, result, fixed_time)

        # Assert
        captured = capsys.readouterr()
        output = captured.out

        # Check that timestamp appears in ISO 8601 format
        assert '2025-01-17T09:00:00+00:00' in output, \
            "Timestamp should be in ISO 8601 format with timezone offset"

    def test_console_output_timestamp_includes_timezone(self, capsys):
        """Test that console timestamp includes +00:00 timezone offset."""
        # Arrange
        config = {
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None
        }
        result = {'status': 'completed'}
        pnl_metrics = {}

        # Act
        format_console_output(config, result, datetime.now(timezone.utc))

        # Assert
        captured = capsys.readouterr()
        output = captured.out

        # Verify timezone offset is present
        # ISO 8601 format should end with +00:00 for UTC
        assert '+00:00' in output, \
            "Timestamp must include timezone offset (+00:00 for UTC)"

    def test_console_output_uses_utc_timezone(self, capsys):
        """Test that console output uses UTC timezone (not local time)."""
        # Arrange
        config = {
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None
        }
        result = {'status': 'completed'}
        pnl_metrics = {}

        # Fixed UTC timestamp
        fixed_time = datetime(2025, 1, 17, 14, 30, 0, tzinfo=timezone.utc)

        # Act
        format_console_output(config, result, fixed_time)

        # Assert - verify timestamp is in UTC format
        captured = capsys.readouterr()
        output = captured.out
        assert '2025-01-17T14:30:00+00:00' in output, \
            "Timestamp should be in UTC timezone"


class TestJSONExportTimezone:
    """Test that JSON export includes timezone-aware timestamps."""

    @pytest.mark.asyncio
    async def test_json_export_timestamp_format(self, tmp_path):
        """Test that JSON export timestamp is in ISO 8601 format with timezone."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['AAPL', 'MSFT'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None
        }
        result = {
            'run_id': 'test-run-123',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('50000'))

        # Fixed timezone-aware timestamp
        fixed_time = datetime(2025, 1, 17, 14, 30, 0, tzinfo=timezone.utc)

        # Act
        await save_results(config, result, pnl_metrics, fixed_time)

        # Assert
        # Load and verify JSON content
        with open(output_file) as f:
            data = json.load(f)

        assert 'timestamp' in data, "JSON should include timestamp field"
        assert data['timestamp'] == '2025-01-17T14:30:00+00:00', \
            "Timestamp should be in ISO 8601 format with timezone"

    @pytest.mark.asyncio
    async def test_json_export_includes_timezone_field(self, tmp_path):
        """Test that JSON export includes explicit 'timezone' field set to 'UTC'."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
        }
        result = {
            'run_id': 'test-run-456',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('30000'))

        # Act
        await save_results(config, result, pnl_metrics, datetime.now(timezone.utc))

        # Assert
        with open(output_file) as f:
            data = json.load(f)

        assert 'timezone' in data, "JSON should include explicit timezone field"
        assert data['timezone'] == 'UTC', \
            "Timezone field should be set to 'UTC'"

    @pytest.mark.asyncio
    async def test_json_timestamp_is_utc_not_local(self, tmp_path):
        """Test that JSON timestamp uses UTC, not local time."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
        }
        result = {
            'run_id': 'test-run-789',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('25000'))

        # Fixed UTC timestamp
        fixed_time = datetime(2025, 1, 17, 9, 0, 0, tzinfo=timezone.utc)

        # Act
        await save_results(config, result, pnl_metrics, fixed_time)

        # Assert - verify timestamp is in UTC format
        with open(output_file) as f:
            data = json.load(f)

        assert data['timestamp'] == '2025-01-17T09:00:00+00:00', \
            "Timestamp should be in UTC timezone"

    @pytest.mark.asyncio
    async def test_json_timestamp_ends_with_utc_offset(self, tmp_path):
        """Test that JSON timestamp ends with +00:00 (UTC offset)."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['MSFT'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
        }
        result = {
            'run_id': 'test-run-101',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('15000'))

        # Act
        await save_results(config, result, pnl_metrics, datetime.now(timezone.utc))

        # Assert
        with open(output_file) as f:
            data = json.load(f)

        timestamp = data['timestamp']
        assert timestamp.endswith('+00:00'), \
            f"Timestamp should end with +00:00 (UTC offset), got: {timestamp}"

    @pytest.mark.asyncio
    async def test_json_timestamp_parseable_with_timezone(self, tmp_path):
        """Test that JSON timestamp can be parsed back to timezone-aware datetime."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['GOOGL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
        }
        result = {
            'run_id': 'test-run-202',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('40000'))

        # Act
        await save_results(config, result, pnl_metrics, datetime.now(timezone.utc))

        # Assert
        with open(output_file) as f:
            data = json.load(f)

        timestamp_str = data['timestamp']

        # Parse timestamp back to datetime
        parsed_dt = datetime.fromisoformat(timestamp_str)

        # Verify it's timezone-aware
        assert parsed_dt.tzinfo is not None, \
            "Parsed timestamp should be timezone-aware"

        # Verify it's UTC
        assert parsed_dt.tzinfo == timezone.utc, \
            "Parsed timestamp should be in UTC timezone"


class TestTimezoneConsistency:
    """Test that timestamps are consistent across console and JSON output."""

    @pytest.mark.asyncio
    async def test_console_and_json_use_same_timestamp(self, tmp_path, capsys):
        """Test that console and JSON use the exact same timestamp when passed the same value."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
            'as_of_date': None
        }
        result = {
            'run_id': 'test-run-303',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('35000'))

        # Generate timestamp once (simulating what main() does)
        run_timestamp = datetime(2025, 1, 17, 12, 0, 0, tzinfo=timezone.utc)

        # Act - both console and JSON output with same timestamp
        format_console_output(config, result, run_timestamp)
        await save_results(config, result, pnl_metrics, run_timestamp)

        # Assert
        # Verify console output
        captured = capsys.readouterr()
        assert '2025-01-17T12:00:00+00:00' in captured.out, \
            "Console should show the provided timestamp"

        # Verify JSON output
        with open(output_file) as f:
            data = json.load(f)

        assert data['timestamp'] == '2025-01-17T12:00:00+00:00', \
            "JSON should have the exact same timestamp as console"
        assert data['timezone'] == 'UTC'


class TestTimezoneRegression:
    """Regression tests to ensure timezone awareness is maintained."""

    def test_datetime_import_includes_timezone(self):
        """Test that timezone is imported from datetime module."""
        # This test verifies the import statement includes timezone
        # to prevent accidental removal in future refactoring
        import scripts.paper_run as paper_run_module

        # Verify timezone is available in the module
        assert hasattr(paper_run_module, 'timezone'), \
            "timezone should be imported from datetime module"

    @pytest.mark.asyncio
    async def test_no_naive_datetime_in_json(self, tmp_path):
        """Test that JSON never contains naive datetimes (without timezone)."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['AAPL'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
        }
        result = {
            'run_id': 'test-run-404',
            'status': 'completed',
            'mappings': []
        }
        pnl_metrics = create_simple_pnl_metrics(Decimal('45000'))

        # Act
        await save_results(config, result, pnl_metrics, datetime.now(timezone.utc))

        # Assert
        with open(output_file) as f:
            data = json.load(f)

        timestamp_str = data['timestamp']

        # Naive datetime would not have timezone offset
        # Timezone-aware ISO format always includes offset like +00:00
        assert '+' in timestamp_str or 'Z' in timestamp_str, \
            f"Timestamp must include timezone offset or 'Z' (Zulu time), got: {timestamp_str}"


class TestEnhancedPnLTimezone:
    """Test timezone handling with enhanced P&L metrics."""

    @pytest.mark.asyncio
    async def test_json_with_enhanced_pnl_includes_timezone(self, tmp_path):
        """Test that JSON with enhanced P&L still includes timezone fields."""
        # Arrange
        output_file = tmp_path / "results.json"
        config = {
            'output_file': str(output_file),
            'symbols': ['AAPL', 'MSFT'],
            'capital': Decimal('100000'),
            'max_position_size': Decimal('20000'),
        }
        result = {
            'run_id': 'test-run-505',
            'status': 'completed',
            'mappings': []
        }
        # Enhanced P&L metrics (with total_pnl key)
        pnl_metrics = {
            'realized_pnl': Decimal('1234.56'),
            'unrealized_pnl': Decimal('789.01'),
            'total_pnl': Decimal('2023.57'),
            'num_open_positions': 2,
            'num_closed_positions': 1,
            'total_positions': 3,
            'per_symbol': {
                'AAPL': {
                    'realized': Decimal('500.00'),
                    'unrealized': Decimal('200.00'),
                    'qty': 100,
                    'avg_entry_price': Decimal('150.00'),
                    'current_price': Decimal('152.00'),
                    'status': 'open'
                },
                'MSFT': {
                    'realized': Decimal('734.56'),
                    'unrealized': Decimal('589.01'),
                    'qty': 50,
                    'avg_entry_price': Decimal('300.00'),
                    'current_price': Decimal('311.78'),
                    'status': 'open'
                },
                'GOOGL': {
                    'realized': Decimal('0.00'),
                    'unrealized': Decimal('0.00'),
                    'qty': 0,
                    'avg_entry_price': Decimal('2800.00'),
                    'current_price': None,
                    'status': 'closed'
                }
            }
        }

        # Act
        await save_results(config, result, pnl_metrics, datetime.now(timezone.utc))

        # Assert
        with open(output_file) as f:
            data = json.load(f)

        # Verify timezone fields are present even with enhanced P&L
        assert 'timestamp' in data
        assert 'timezone' in data
        assert data['timezone'] == 'UTC'

        # Verify timestamp is timezone-aware
        timestamp_str = data['timestamp']
        assert '+00:00' in timestamp_str or timestamp_str.endswith('Z')

        # Verify enhanced P&L data is also present
        assert 'results' in data
        assert 'realized_pnl' in data['results']
        assert 'unrealized_pnl' in data['results']
        assert 'total_pnl' in data['results']
