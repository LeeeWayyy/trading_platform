"""Tests for JSON log formatter.

Tests verify that logs are formatted correctly with:
- Required schema fields (timestamp, level, service, trace_id, message)
- Optional context fields
- Exception information
- Source location
"""

import json
import logging
from datetime import UTC, datetime

import pytest

from libs.common.logging.formatter import JSONFormatter


class TestJSONFormatter:
    """Test suite for JSONFormatter."""

    @pytest.fixture
    def formatter(self) -> JSONFormatter:
        """Create a JSONFormatter instance for testing."""
        return JSONFormatter(service_name="test_service")

    def test_basic_log_format(self, formatter: JSONFormatter) -> None:
        """Test that basic log is formatted as valid JSON with required fields."""
        # Create a log record
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.trace_id = "test-trace-123"

        # Format the record
        formatted = formatter.format(record)

        # Parse JSON
        log_dict = json.loads(formatted)

        # Verify required fields
        assert "timestamp" in log_dict
        assert log_dict["level"] == "INFO"
        assert log_dict["service"] == "test_service"
        assert log_dict["trace_id"] == "test-trace-123"
        assert log_dict["message"] == "Test message"

    def test_timestamp_format(self, formatter: JSONFormatter) -> None:
        """Test that timestamp is formatted as ISO 8601 in UTC."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        # Verify timestamp format (ISO 8601 with milliseconds and Z suffix)
        timestamp = log_dict["timestamp"]
        assert timestamp.endswith("Z")
        # Should be parseable as ISO 8601
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert dt.tzinfo == UTC

    def test_context_inclusion(self, formatter: JSONFormatter) -> None:
        """Test that context dict is included in output."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.context = {"symbol": "AAPL", "qty": 100}

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        assert "context" in log_dict
        assert log_dict["context"]["symbol"] == "AAPL"
        assert log_dict["context"]["qty"] == 100

    def test_no_context_when_disabled(self) -> None:
        """Test that context is not included when include_context=False."""
        formatter = JSONFormatter(service_name="test", include_context=False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.context = {"symbol": "AAPL"}

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        assert "context" not in log_dict

    def test_missing_trace_id(self, formatter: JSONFormatter) -> None:
        """Test that trace_id is None when not set."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        assert log_dict["trace_id"] is None

    def test_exception_logging(self, formatter: JSONFormatter) -> None:
        """Test that exceptions are properly formatted."""
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        assert "exception" in log_dict
        assert log_dict["exception"]["type"] == "ValueError"
        assert log_dict["exception"]["message"] == "Test error"
        assert "traceback" in log_dict["exception"]
        assert "ValueError" in log_dict["exception"]["traceback"]

    def test_source_location(self, formatter: JSONFormatter) -> None:
        """Test that source location is included."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.funcName = "test_function"

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        assert "source" in log_dict
        assert log_dict["source"]["file"] == "/path/to/file.py"
        assert log_dict["source"]["line"] == 42
        assert log_dict["source"]["function"] == "test_function"

    def test_extra_fields_as_context(self, formatter: JSONFormatter) -> None:
        """Test that extra fields are included in context."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )
        # Add extra fields
        record.custom_field = "custom_value"
        record.symbol = "AAPL"

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        # Extra fields should be in context
        assert "context" in log_dict
        assert log_dict["context"]["custom_field"] == "custom_value"
        assert log_dict["context"]["symbol"] == "AAPL"

    def test_different_log_levels(self, formatter: JSONFormatter) -> None:
        """Test formatting with different log levels."""
        for level_name, level_num in [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ]:
            record = logging.LogRecord(
                name="test",
                level=level_num,
                pathname="/path/to/file.py",
                lineno=42,
                msg=f"Test {level_name}",
                args=(),
                exc_info=None,
            )

            formatted = formatter.format(record)
            log_dict = json.loads(formatted)

            assert log_dict["level"] == level_name
            assert log_dict["message"] == f"Test {level_name}"

    def test_message_with_args(self, formatter: JSONFormatter) -> None:
        """Test formatting message with string substitution."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Processing %s symbols with %d orders",
            args=("AAPL", 10),
            exc_info=None,
        )

        formatted = formatter.format(record)
        log_dict = json.loads(formatted)

        assert log_dict["message"] == "Processing AAPL symbols with 10 orders"
