"""Tests for alpha research framework exceptions."""

import pytest

from libs.trading.alpha.exceptions import (
    AlphaResearchError,
    AlphaValidationError,
    InsufficientDataError,
    MissingForwardReturnError,
    PITViolationError,
)


class TestExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_base_exception(self):
        """Test AlphaResearchError is base exception."""
        exc = AlphaResearchError("Base error")
        assert isinstance(exc, Exception)
        assert str(exc) == "Base error"

    def test_pit_violation_inherits(self):
        """Test PITViolationError inherits from base."""
        exc = PITViolationError("PIT violation")
        assert isinstance(exc, AlphaResearchError)
        assert isinstance(exc, Exception)

    def test_missing_forward_return_inherits(self):
        """Test MissingForwardReturnError inherits from base."""
        exc = MissingForwardReturnError("Missing forward return")
        assert isinstance(exc, AlphaResearchError)
        assert isinstance(exc, Exception)

    def test_insufficient_data_inherits(self):
        """Test InsufficientDataError inherits from base."""
        exc = InsufficientDataError("Not enough data")
        assert isinstance(exc, AlphaResearchError)

    def test_validation_error_inherits(self):
        """Test AlphaValidationError inherits from base."""
        exc = AlphaValidationError("Validation failed")
        assert isinstance(exc, AlphaResearchError)


class TestPITViolationError:
    """Tests for PITViolationError."""

    def test_raise_and_catch(self):
        """Test raising and catching PITViolationError."""
        with pytest.raises(PITViolationError) as exc_info:
            raise PITViolationError("Requested date 2024-01-15 exceeds snapshot")

        assert "snapshot" in str(exc_info.value).lower()

    def test_can_catch_as_base(self):
        """Test can catch as AlphaResearchError."""
        with pytest.raises(AlphaResearchError):
            raise PITViolationError("PIT violation")

    def test_message_format(self):
        """Test error message format."""
        exc = PITViolationError("Requested date 2024-01-15 but snapshot ends 2024-01-01")
        msg = str(exc)
        assert "2024-01-15" in msg
        assert "2024-01-01" in msg


class TestMissingForwardReturnError:
    """Tests for MissingForwardReturnError."""

    def test_raise_and_catch(self):
        """Test raising and catching MissingForwardReturnError."""
        with pytest.raises(MissingForwardReturnError) as exc_info:
            raise MissingForwardReturnError("Forward return horizon 20 exceeds snapshot end")

        assert "horizon" in str(exc_info.value).lower()

    def test_can_catch_as_base(self):
        """Test can catch as AlphaResearchError."""
        with pytest.raises(AlphaResearchError):
            raise MissingForwardReturnError("Missing returns")

    def test_fail_fast_semantics(self):
        """Test error represents fail-fast behavior."""
        # This error should be raised immediately, not return NaN
        exc = MissingForwardReturnError(
            "Forward return date 2024-02-01 exceeds snapshot end 2024-01-15. "
            "Reduce backtest end_date or horizon, or use a newer snapshot."
        )

        msg = str(exc)
        assert "reduce" in msg.lower() or "snapshot" in msg.lower()


class TestInsufficientDataError:
    """Tests for InsufficientDataError."""

    def test_raise_and_catch(self):
        """Test raising and catching InsufficientDataError."""
        with pytest.raises(InsufficientDataError):
            raise InsufficientDataError("Need at least 30 observations")

    def test_typical_usage(self):
        """Test typical error message."""
        exc = InsufficientDataError("IC calculation requires at least 30 observations, got 15")
        assert "30" in str(exc)
        assert "15" in str(exc)


class TestAlphaValidationError:
    """Tests for AlphaValidationError."""

    def test_raise_and_catch(self):
        """Test raising and catching AlphaValidationError."""
        with pytest.raises(AlphaValidationError):
            raise AlphaValidationError("Missing required column 'signal'")

    def test_validation_scenarios(self):
        """Test various validation error scenarios."""
        scenarios = [
            "Missing required columns: {'signal'}",
            "Infinite values detected: 5 inf values",
            "Z-score exceeds threshold: max |z| = 10.5",
            "Invalid date in output: expected 2024-01-01",
        ]

        for msg in scenarios:
            exc = AlphaValidationError(msg)
            assert str(exc) == msg


class TestExceptionCatchPatterns:
    """Tests for recommended exception handling patterns."""

    def test_catch_all_alpha_errors(self):
        """Test catching all alpha errors with base class."""
        errors = [
            PITViolationError("pit"),
            MissingForwardReturnError("fwd"),
            InsufficientDataError("data"),
            AlphaValidationError("valid"),
        ]

        for error in errors:
            with pytest.raises(AlphaResearchError):
                raise error

    def test_specific_handling(self):
        """Test handling specific exceptions differently."""

        def process_with_error(error_type: str):
            if error_type == "pit":
                raise PITViolationError("PIT error")
            elif error_type == "fwd":
                raise MissingForwardReturnError("Forward error")
            elif error_type == "data":
                raise InsufficientDataError("Data error")

        # Handle PIT violations specially
        try:
            process_with_error("pit")
        except PITViolationError:
            result = "pit_handled"
        except AlphaResearchError:
            result = "other_handled"

        assert result == "pit_handled"

        # Other errors caught by base
        try:
            process_with_error("data")
        except PITViolationError:
            result = "pit_handled"
        except AlphaResearchError:
            result = "other_handled"

        assert result == "other_handled"
