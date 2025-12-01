"""
Tests for M1: Price Cache Decimal Validation.

M1 Fix: Ensures price_cache values are validated and normalized to Decimal
type during TradingOrchestrator initialization.

Contract:
- Accept: Decimal (passed through), int, float (converted via str())
- Reject: strings, None, objects (raise TypeError)
"""

from decimal import Decimal

import pytest

from apps.orchestrator.orchestrator import TradingOrchestrator


class TestPriceCacheDecimalValidation:
    """Test suite for M1 price cache Decimal validation."""

    def test_price_cache_decimal_preserved(self) -> None:
        """Decimal values should be preserved unchanged."""
        price_cache = {
            "AAPL": Decimal("150.50"),
            "MSFT": Decimal("300.00"),
        }

        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache=price_cache,
        )

        assert orchestrator.price_cache["AAPL"] == Decimal("150.50")
        assert orchestrator.price_cache["MSFT"] == Decimal("300.00")
        # Verify exact Decimal representation is preserved
        assert str(orchestrator.price_cache["AAPL"]) == "150.50"

    def test_price_cache_float_input_converted(self) -> None:
        """Float values should be auto-converted to Decimal."""
        price_cache = {
            "AAPL": 150.50,  # float
            "MSFT": 300.0,  # float
        }

        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache=price_cache,
        )

        # Converted to Decimal
        assert isinstance(orchestrator.price_cache["AAPL"], Decimal)
        assert isinstance(orchestrator.price_cache["MSFT"], Decimal)
        # Values preserved (via str() conversion)
        assert orchestrator.price_cache["AAPL"] == Decimal("150.5")
        assert orchestrator.price_cache["MSFT"] == Decimal("300.0")

    def test_price_cache_int_input_converted(self) -> None:
        """Integer values should be auto-converted to Decimal."""
        price_cache = {
            "AAPL": 150,  # int
            "MSFT": 300,  # int
        }

        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache=price_cache,
        )

        # Converted to Decimal
        assert isinstance(orchestrator.price_cache["AAPL"], Decimal)
        assert orchestrator.price_cache["AAPL"] == Decimal("150")

    def test_price_cache_invalid_type_rejected(self) -> None:
        """Non-numeric types (string, None, object) should raise TypeError."""
        # String
        with pytest.raises(TypeError, match="must be Decimal, int, or float"):
            TradingOrchestrator(
                signal_service_url="http://localhost:8001",
                execution_gateway_url="http://localhost:8002",
                capital=Decimal("100000"),
                max_position_size=Decimal("10000"),
                price_cache={"AAPL": "150.50"},  # string - rejected
            )

        # None
        with pytest.raises(TypeError, match="must be Decimal, int, or float"):
            TradingOrchestrator(
                signal_service_url="http://localhost:8001",
                execution_gateway_url="http://localhost:8002",
                capital=Decimal("100000"),
                max_position_size=Decimal("10000"),
                price_cache={"AAPL": None},  # None - rejected
            )

        # Object
        with pytest.raises(TypeError, match="must be Decimal, int, or float"):
            TradingOrchestrator(
                signal_service_url="http://localhost:8001",
                execution_gateway_url="http://localhost:8002",
                capital=Decimal("100000"),
                max_position_size=Decimal("10000"),
                price_cache={"AAPL": object()},  # object - rejected
            )

    def test_price_cache_bool_rejected(self) -> None:
        """Boolean values should raise TypeError (bool is subclass of int).

        Python's bool is a subclass of int, so isinstance(True, int) returns True.
        However, Decimal(str(True)) raises InvalidOperation. This test verifies
        booleans are explicitly rejected before the int/float conversion path.
        """
        # True
        with pytest.raises(TypeError, match="got bool"):
            TradingOrchestrator(
                signal_service_url="http://localhost:8001",
                execution_gateway_url="http://localhost:8002",
                capital=Decimal("100000"),
                max_position_size=Decimal("10000"),
                price_cache={"AAPL": True},  # bool - rejected
            )

        # False
        with pytest.raises(TypeError, match="got bool"):
            TradingOrchestrator(
                signal_service_url="http://localhost:8001",
                execution_gateway_url="http://localhost:8002",
                capital=Decimal("100000"),
                max_position_size=Decimal("10000"),
                price_cache={"AAPL": False},  # bool - rejected
            )

    def test_price_cache_precision_preserved(self) -> None:
        """Decimal(str(float)) should preserve exact representation.

        This tests the specific precision issue where float 0.1 has
        binary representation issues but Decimal(str(0.1)) gives exact "0.1".
        """
        price_cache = {
            "AAPL": 0.1,  # float with precision issue
        }

        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            price_cache=price_cache,
        )

        # Should be exactly "0.1", not "0.1000000000000000055511151231..."
        assert str(orchestrator.price_cache["AAPL"]) == "0.1"
        assert orchestrator.price_cache["AAPL"] == Decimal("0.1")
