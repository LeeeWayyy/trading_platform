"""
Integration tests for end-to-end P&L calculation workflow (P1T0 - AC6).

Tests validate P&L against known scenarios with complete workflow:
fetch prices → calculate P&L → verify expected outcomes.

See Also:
    - scripts/paper_run.py - Complete paper trading workflow
    - ADR-0008: Enhanced P&L calculation architecture
"""

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.ops.paper_run import calculate_enhanced_pnl, fetch_current_prices  # noqa: E402


@pytest.mark.integration()
class TestPNLIntegration:
    """Integration tests for complete P&L calculation workflow (AC6)."""

    @pytest.mark.asyncio()
    async def test_known_scenario_profit(self) -> None:
        """AC6: Validate P&L against known profit scenario."""
        # Given: Known positions and prices
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"},
            {"symbol": "MSFT", "qty": 0, "avg_entry_price": "300.00", "realized_pl": "500.00"},
        ]
        mock_quotes = {
            "AAPL": {"last_price": Decimal("152.00"), "ask_price": None, "bid_price": None}
        }
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = mock_quotes

        # When: Execute P&L calculation workflow
        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            open_symbols = [p["symbol"] for p in positions if p.get("qty", 0) != 0]
            prices = await fetch_current_prices(open_symbols, {})
            result = await calculate_enhanced_pnl(positions, prices)

        # Then: Verify expected P&L values
        assert result["realized_pnl"] == Decimal("500.00")
        assert result["unrealized_pnl"] == Decimal("200.00")
        assert result["total_pnl"] == Decimal("700.00")

    @pytest.mark.asyncio()
    async def test_known_scenario_loss(self) -> None:
        """AC6: Validate P&L against known loss scenario."""
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"},
            {"symbol": "MSFT", "qty": 0, "avg_entry_price": "300.00", "realized_pl": "-300.00"},
        ]
        mock_quotes = {
            "AAPL": {"last_price": Decimal("145.00"), "ask_price": None, "bid_price": None}
        }
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = mock_quotes

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            open_symbols = [p["symbol"] for p in positions if p.get("qty", 0) != 0]
            prices = await fetch_current_prices(open_symbols, {})
            result = await calculate_enhanced_pnl(positions, prices)

        assert result["realized_pnl"] == Decimal("-300.00")
        assert result["unrealized_pnl"] == Decimal("-500.00")
        assert result["total_pnl"] == Decimal("-800.00")

    @pytest.mark.asyncio()
    async def test_known_scenario_short_positions(self) -> None:
        """AC6: Validate P&L with short positions."""
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"},
            {"symbol": "GOOGL", "qty": -30, "avg_entry_price": "140.00", "realized_pl": "0"},
        ]
        mock_quotes = {
            "AAPL": {"last_price": Decimal("152.00"), "ask_price": None, "bid_price": None},
            "GOOGL": {"last_price": Decimal("135.00"), "ask_price": None, "bid_price": None},
        }
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = mock_quotes

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            open_symbols = [p["symbol"] for p in positions if p.get("qty", 0) != 0]
            prices = await fetch_current_prices(open_symbols, {})
            result = await calculate_enhanced_pnl(positions, prices)

        assert result["unrealized_pnl"] == Decimal("350.00")
        assert result["per_symbol"]["GOOGL"]["unrealized"] == Decimal("150.00")

    @pytest.mark.asyncio()
    async def test_known_scenario_complex_mixed(self) -> None:
        """AC6: Validate complex scenario with mixed positions."""
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "50.00"},
            {"symbol": "MSFT", "qty": 0, "avg_entry_price": "300.00", "realized_pl": "500.00"},
            {"symbol": "GOOGL", "qty": -30, "avg_entry_price": "140.00", "realized_pl": "0"},
            {"symbol": "NVDA", "qty": 40, "avg_entry_price": "400.00", "realized_pl": "0"},
        ]
        mock_quotes = {
            "AAPL": {"last_price": Decimal("152.00"), "ask_price": None, "bid_price": None},
            "GOOGL": {"last_price": Decimal("135.00"), "ask_price": None, "bid_price": None},
            "NVDA": {"last_price": Decimal("390.00"), "ask_price": None, "bid_price": None},
        }
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = mock_quotes

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            open_symbols = [p["symbol"] for p in positions if p.get("qty", 0) != 0]
            prices = await fetch_current_prices(open_symbols, {})
            result = await calculate_enhanced_pnl(positions, prices)

        # Realized: 50 + 500 = 550
        # Unrealized: 200 (AAPL) + 150 (GOOGL) - 400 (NVDA) = -50
        assert result["realized_pnl"] == Decimal("550.00")
        assert result["unrealized_pnl"] == Decimal("-50.00")
        assert result["total_pnl"] == Decimal("500.00")
        assert result["num_open_positions"] == 3
        assert result["num_closed_positions"] == 1

    @pytest.mark.asyncio()
    async def test_json_export_structure(self) -> None:
        """AC6: Validate JSON export structure matches specification."""
        positions = [
            {"symbol": "AAPL", "qty": 100, "avg_entry_price": "150.00", "realized_pl": "0"}
        ]
        mock_quotes = {
            "AAPL": {"last_price": Decimal("152.00"), "ask_price": None, "bid_price": None}
        }
        mock_executor = MagicMock()
        mock_executor.get_latest_quotes.return_value = mock_quotes

        with patch("scripts.ops.paper_run.AlpacaExecutor", return_value=mock_executor):
            open_symbols = [p["symbol"] for p in positions if p.get("qty", 0) != 0]
            prices = await fetch_current_prices(open_symbols, {})
            result = await calculate_enhanced_pnl(positions, prices)

        # Validate structure
        assert set(result.keys()) == {
            "realized_pnl",
            "unrealized_pnl",
            "total_pnl",
            "per_symbol",
            "num_open_positions",
            "num_closed_positions",
            "total_positions",
        }
        assert set(result["per_symbol"]["AAPL"].keys()) == {
            "realized",
            "unrealized",
            "qty",
            "avg_entry_price",
            "current_price",
            "status",
        }
