"""Tests for trace ID context management.

Tests verify:
- Trace ID generation creates unique UUIDs
- Trace ID storage and retrieval from context
- LogContext manager properly scopes trace IDs
"""

import pytest

from libs.core.common.logging.context import (
    TRACE_ID_HEADER,
    LogContext,
    clear_trace_id,
    generate_trace_id,
    get_or_create_trace_id,
    get_trace_id,
    set_trace_id,
)


class TestTraceIDGeneration:
    """Test suite for trace ID generation."""

    def test_generate_trace_id_returns_uuid(self) -> None:
        """Test that generate_trace_id returns a valid UUID string."""
        trace_id = generate_trace_id()

        assert isinstance(trace_id, str)
        assert len(trace_id) == 36  # UUID format: 8-4-4-4-12
        assert trace_id.count("-") == 4

    def test_generate_trace_id_unique(self) -> None:
        """Test that each generated trace ID is unique."""
        ids = [generate_trace_id() for _ in range(100)]

        # All IDs should be unique
        assert len(set(ids)) == 100


class TestTraceIDContext:
    """Test suite for trace ID context management."""

    def setup_method(self) -> None:
        """Clear trace ID before each test."""
        clear_trace_id()

    def teardown_method(self) -> None:
        """Clear trace ID after each test."""
        clear_trace_id()

    def test_get_trace_id_none_by_default(self) -> None:
        """Test that trace ID is None when not set."""
        assert get_trace_id() is None

    def test_set_and_get_trace_id(self) -> None:
        """Test setting and retrieving trace ID."""
        test_id = "test-trace-123"
        set_trace_id(test_id)

        assert get_trace_id() == test_id

    def test_set_trace_id_empty_raises_error(self) -> None:
        """Test that setting empty trace ID raises ValueError."""
        with pytest.raises(ValueError, match="Trace ID cannot be empty"):
            set_trace_id("")

        with pytest.raises(ValueError, match="Trace ID cannot be empty"):
            set_trace_id(None)  # type: ignore

    def test_clear_trace_id(self) -> None:
        """Test that clear_trace_id removes trace ID."""
        set_trace_id("test-123")
        assert get_trace_id() is not None

        clear_trace_id()
        assert get_trace_id() is None

    def test_get_or_create_trace_id_creates_new(self) -> None:
        """Test that get_or_create_trace_id generates new ID when none exists."""
        trace_id = get_or_create_trace_id()

        assert trace_id is not None
        assert len(trace_id) == 36
        assert get_trace_id() == trace_id

    def test_get_or_create_trace_id_returns_existing(self) -> None:
        """Test that get_or_create_trace_id returns existing ID."""
        existing_id = "existing-123"
        set_trace_id(existing_id)

        trace_id = get_or_create_trace_id()

        assert trace_id == existing_id

    def test_trace_id_header_constant(self) -> None:
        """Test that TRACE_ID_HEADER constant is defined correctly."""
        assert TRACE_ID_HEADER == "X-Trace-ID"


class TestLogContext:
    """Test suite for LogContext manager."""

    def setup_method(self) -> None:
        """Clear trace ID before each test."""
        clear_trace_id()

    def teardown_method(self) -> None:
        """Clear trace ID after each test."""
        clear_trace_id()

    def test_log_context_sets_trace_id(self) -> None:
        """Test that LogContext sets trace ID within context."""
        test_id = "context-test-123"

        with LogContext(test_id) as trace_id:
            assert trace_id == test_id
            assert get_trace_id() == test_id

    def test_log_context_generates_trace_id_if_none(self) -> None:
        """Test that LogContext generates trace ID if none provided."""
        with LogContext() as trace_id:
            assert trace_id is not None
            assert len(trace_id) == 36
            assert get_trace_id() == trace_id

    def test_log_context_restores_previous_id(self) -> None:
        """Test that LogContext restores previous trace ID after exiting."""
        original_id = "original-123"
        set_trace_id(original_id)

        with LogContext("temporary-456"):
            assert get_trace_id() == "temporary-456"

        # Should restore original ID
        assert get_trace_id() == original_id

    def test_log_context_clears_if_no_previous_id(self) -> None:
        """Test that LogContext clears trace ID if none existed before."""
        assert get_trace_id() is None

        with LogContext("temporary-789"):
            assert get_trace_id() == "temporary-789"

        # Should be None again
        assert get_trace_id() is None

    def test_nested_log_contexts(self) -> None:
        """Test that nested LogContext managers work correctly."""
        with LogContext("outer-123") as outer_id:
            assert get_trace_id() == outer_id

            with LogContext("inner-456") as inner_id:
                assert get_trace_id() == inner_id
                assert inner_id != outer_id

            # Should restore outer ID
            assert get_trace_id() == outer_id

        # Should be None after both contexts exit
        assert get_trace_id() is None

    def test_log_context_preserves_id_on_exception(self) -> None:
        """Test that LogContext restores ID even if exception occurs."""
        original_id = "original-999"
        set_trace_id(original_id)

        # Helper to verify ID change and raise exception (single callable for PT012)
        def raise_error_in_context():
            with LogContext("temporary-111"):
                # Verify ID was changed inside context
                assert get_trace_id() == "temporary-111"
                raise ValueError("Test error")

        # Verify exception is raised
        with pytest.raises(ValueError, match="Test error"):
            raise_error_in_context()

        # Should still restore original ID
        assert get_trace_id() == original_id
