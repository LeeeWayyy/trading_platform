"""Tests for logging configuration.

Tests verify:
- configure_logging sets up JSON logging correctly
- TraceIDFilter adds trace IDs to log records
- log_with_context adds context fields properly
"""

import json
import logging
from io import StringIO

import pytest

from libs.common.logging.config import (
    TraceIDFilter,
    configure_logging,
    get_logger,
    log_with_context,
)
from libs.common.logging.context import clear_trace_id, set_trace_id


class TestTraceIDFilter:
    """Test suite for TraceIDFilter."""

    def setup_method(self) -> None:
        """Clear trace ID before each test."""
        clear_trace_id()

    def teardown_method(self) -> None:
        """Clear trace ID after each test."""
        clear_trace_id()

    def test_filter_adds_trace_id_to_record(self) -> None:
        """Test that filter adds trace ID from context to record."""
        trace_filter = TraceIDFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )

        set_trace_id("test-123")
        result = trace_filter.filter(record)

        assert result is True  # Filter should always pass through
        assert hasattr(record, "trace_id")
        assert record.trace_id == "test-123"  # type: ignore

    def test_filter_adds_none_when_no_trace_id(self) -> None:
        """Test that filter adds None when no trace ID in context."""
        trace_filter = TraceIDFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test",
            args=(),
            exc_info=None,
        )

        clear_trace_id()
        result = trace_filter.filter(record)

        assert result is True
        assert hasattr(record, "trace_id")
        assert record.trace_id is None  # type: ignore


class TestConfigureLogging:
    """Test suite for configure_logging."""

    def teardown_method(self) -> None:
        """Clean up logging configuration after each test."""
        # Reset root logger
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(logging.WARNING)

    def test_configure_logging_returns_root_logger(self) -> None:
        """Test that configure_logging returns root logger."""
        logger = configure_logging(service_name="test")

        assert logger is logging.getLogger()

    def test_configure_logging_sets_log_level(self) -> None:
        """Test that configure_logging sets correct log level."""
        logger = configure_logging(service_name="test", log_level="DEBUG")

        assert logger.level == logging.DEBUG

        # Reset and test INFO level
        logger.handlers.clear()
        logger = configure_logging(service_name="test", log_level="INFO")

        assert logger.level == logging.INFO

    def test_configure_logging_invalid_level_raises_error(self) -> None:
        """Test that invalid log level raises ValueError."""
        with pytest.raises(ValueError, match="Invalid log level"):
            configure_logging(service_name="test", log_level="INVALID")

    def test_configure_logging_outputs_json(self) -> None:
        """Test that configured logger outputs valid JSON."""
        # Capture log output
        stream = StringIO()
        handler = logging.StreamHandler(stream)

        # Configure logging
        logger = configure_logging(service_name="test_service")
        # Replace handler with our test handler
        logger.handlers.clear()
        logger.addHandler(handler)
        # Need to set formatter manually for test
        from libs.common.logging.formatter import JSONFormatter

        handler.setFormatter(JSONFormatter(service_name="test_service"))
        handler.addFilter(TraceIDFilter())

        # Log a message
        set_trace_id("test-abc-123")
        logger.info("Test message")

        # Parse output as JSON
        output = stream.getvalue()
        log_dict = json.loads(output.strip())

        assert log_dict["service"] == "test_service"
        assert log_dict["level"] == "INFO"
        assert log_dict["message"] == "Test message"
        assert log_dict["trace_id"] == "test-abc-123"

        clear_trace_id()

    def test_configure_logging_removes_existing_handlers(self) -> None:
        """Test that configure_logging removes existing handlers."""
        logger = logging.getLogger()
        # Record initial handler count (pytest may add handlers)
        initial_count = len(logger.handlers)

        # Add a dummy handler
        dummy_handler = logging.StreamHandler()
        logger.addHandler(dummy_handler)

        assert len(logger.handlers) == initial_count + 1

        # Configure logging should remove existing handlers
        configure_logging(service_name="test")

        # Should have exactly 1 handler (the new one)
        assert len(logger.handlers) == 1
        assert logger.handlers[0] is not dummy_handler


class TestGetLogger:
    """Test suite for get_logger."""

    def test_get_logger_returns_logger(self) -> None:
        """Test that get_logger returns a logger instance."""
        logger = get_logger("test")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test"

    def test_get_logger_none_returns_root(self) -> None:
        """Test that get_logger(None) returns root logger."""
        logger = get_logger(None)

        assert logger is logging.getLogger()


class TestLogWithContext:
    """Test suite for log_with_context."""

    def setup_method(self) -> None:
        """Set up logger for testing."""
        self.stream = StringIO()
        self.logger = logging.getLogger("test")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()

        handler = logging.StreamHandler(self.stream)
        from libs.common.logging.formatter import JSONFormatter

        handler.setFormatter(JSONFormatter(service_name="test"))
        handler.addFilter(TraceIDFilter())
        self.logger.addHandler(handler)

        clear_trace_id()

    def teardown_method(self) -> None:
        """Clean up after test."""
        self.logger.handlers.clear()
        clear_trace_id()

    def test_log_with_context_adds_context_fields(self) -> None:
        """Test that log_with_context adds fields to context dict."""
        log_with_context(
            self.logger,
            "INFO",
            "Order placed",
            symbol="AAPL",
            qty=100,
            client_order_id="order-123",
        )

        output = self.stream.getvalue()
        log_dict = json.loads(output.strip())

        assert "context" in log_dict
        assert log_dict["context"]["symbol"] == "AAPL"
        assert log_dict["context"]["qty"] == 100
        assert log_dict["context"]["client_order_id"] == "order-123"

    def test_log_with_context_different_levels(self) -> None:
        """Test log_with_context with different log levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            self.stream.truncate(0)
            self.stream.seek(0)

            log_with_context(self.logger, level, f"Test {level} message", test="value")

            output = self.stream.getvalue()
            log_dict = json.loads(output.strip())

            assert log_dict["level"] == level
            assert log_dict["message"] == f"Test {level} message"
