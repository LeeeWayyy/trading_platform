"""
Integration tests for real-time P&L endpoint.

Tests the GET /api/v1/positions/pnl/realtime endpoint with various price sources
and fallback scenarios.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from redis.exceptions import RedisError

from apps.execution_gateway.main import app, _resolve_and_calculate_pnl
from apps.execution_gateway.schemas import Position


@pytest.fixture
def test_client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def mock_positions():
    """Mock positions for testing."""
    return [
        Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("148.00"),  # Database price
            unrealized_pl=Decimal("-20.00"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        ),
        Position(
            symbol="MSFT",
            qty=Decimal("5"),
            avg_entry_price=Decimal("300.00"),
            current_price=Decimal("295.00"),  # Database price
            unrealized_pl=Decimal("-25.00"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        ),
    ]


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    mock = MagicMock()
    mock.get = MagicMock(return_value=None)
    return mock


class TestRealtimePnLEndpoint:
    """Tests for real-time P&L endpoint."""

    def test_endpoint_exists(self, test_client):
        """Test endpoint is accessible."""
        response = test_client.get("/api/v1/positions/pnl/realtime")
        # Should return 200 even with no positions
        assert response.status_code == 200

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_with_redis_prices(
        self, mock_redis, mock_db, test_client, mock_positions
    ):
        """Test P&L calculation with real-time prices from Redis."""
        # Setup database mock
        mock_db.get_all_positions.return_value = mock_positions

        # Setup Redis mock with real-time prices using mget (batch fetch)
        def redis_mget(keys):
            # Return prices for all keys in order
            result = []
            for key in keys:
                if key == "price:AAPL":
                    result.append(json.dumps({
                        "symbol": "AAPL",
                        "bid": 152.00,
                        "ask": 152.10,
                        "mid": 152.05,
                        "timestamp": "2024-10-19T14:30:00+00:00",
                    }))
                elif key == "price:MSFT":
                    result.append(json.dumps({
                        "symbol": "MSFT",
                        "bid": 305.00,
                        "ask": 305.10,
                        "mid": 305.05,
                        "timestamp": "2024-10-19T14:30:05+00:00",
                    }))
                else:
                    result.append(None)
            return result

        mock_redis.mget = MagicMock(side_effect=redis_mget)

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "positions" in data
        assert "total_positions" in data
        assert "total_unrealized_pl" in data
        assert "realtime_prices_available" in data
        assert "timestamp" in data

        # Verify counts
        assert data["total_positions"] == 2
        assert data["realtime_prices_available"] == 2

        # Verify AAPL position
        aapl_pos = next(p for p in data["positions"] if p["symbol"] == "AAPL")
        assert aapl_pos["price_source"] == "real-time"
        assert Decimal(aapl_pos["current_price"]) == Decimal("152.05")
        # P&L = (152.05 - 150.00) * 10 = 20.50
        assert Decimal(aapl_pos["unrealized_pl"]) == Decimal("20.50")
        # P&L % = (152.05 - 150.00) / 150.00 * 100 = 1.37%
        assert abs(Decimal(aapl_pos["unrealized_pl_pct"]) - Decimal("1.37")) < Decimal(
            "0.01"
        )

        # Verify MSFT position
        msft_pos = next(p for p in data["positions"] if p["symbol"] == "MSFT")
        assert msft_pos["price_source"] == "real-time"
        assert Decimal(msft_pos["current_price"]) == Decimal("305.05")
        # P&L = (305.05 - 300.00) * 5 = 25.25
        assert Decimal(msft_pos["unrealized_pl"]) == Decimal("25.25")

        # Verify total P&L
        # Total = 20.50 + 25.25 = 45.75
        assert Decimal(data["total_unrealized_pl"]) == Decimal("45.75")

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_with_database_fallback(
        self, mock_redis, mock_db, test_client, mock_positions
    ):
        """Test P&L calculation with database fallback when Redis unavailable."""
        # Setup database mock
        mock_db.get_all_positions.return_value = mock_positions

        # Setup Redis mock - no prices available (returns None for each key)
        mock_redis.mget = MagicMock(side_effect=lambda keys: [None] * len(keys))

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify all prices from database
        assert data["realtime_prices_available"] == 0

        # Verify AAPL uses database price
        aapl_pos = next(p for p in data["positions"] if p["symbol"] == "AAPL")
        assert aapl_pos["price_source"] == "database"
        assert Decimal(aapl_pos["current_price"]) == Decimal("148.00")
        # P&L = (148.00 - 150.00) * 10 = -20.00
        assert Decimal(aapl_pos["unrealized_pl"]) == Decimal("-20.00")

        # Verify MSFT uses database price
        msft_pos = next(p for p in data["positions"] if p["symbol"] == "MSFT")
        assert msft_pos["price_source"] == "database"
        assert Decimal(msft_pos["current_price"]) == Decimal("295.00")
        # P&L = (295.00 - 300.00) * 5 = -25.00
        assert Decimal(msft_pos["unrealized_pl"]) == Decimal("-25.00")

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_with_entry_price_fallback(
        self, mock_redis, mock_db, test_client
    ):
        """Test P&L calculation with entry price fallback when no prices available."""
        # Position with no current_price
        position = Position(
            symbol="TSLA",
            qty=Decimal("20"),
            avg_entry_price=Decimal("200.00"),
            current_price=None,  # No database price
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )

        # Setup database mock
        mock_db.get_all_positions.return_value = [position]

        # Setup Redis mock - no prices available (returns None for each key)
        mock_redis.mget = MagicMock(side_effect=lambda keys: [None] * len(keys))

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify fallback to entry price
        assert data["realtime_prices_available"] == 0
        tsla_pos = data["positions"][0]
        assert tsla_pos["price_source"] == "fallback"
        assert Decimal(tsla_pos["current_price"]) == Decimal("200.00")
        # P&L = (200.00 - 200.00) * 20 = 0.00
        assert Decimal(tsla_pos["unrealized_pl"]) == Decimal("0.00")
        assert Decimal(tsla_pos["unrealized_pl_pct"]) == Decimal("0.00")

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_mixed_price_sources(
        self, mock_redis, mock_db, test_client, mock_positions
    ):
        """Test P&L with mixed price sources (some Redis, some database)."""
        # Setup database mock
        mock_db.get_all_positions.return_value = mock_positions

        # Setup Redis mock - only AAPL has real-time price (using mget for batch fetch)
        def redis_mget(keys):
            result = []
            for key in keys:
                if key == "price:AAPL":
                    result.append(json.dumps({
                        "symbol": "AAPL",
                        "bid": 151.00,
                        "ask": 151.10,
                        "mid": 151.05,
                        "timestamp": "2024-10-19T14:30:00+00:00",
                    }))
                else:
                    result.append(None)
            return result

        mock_redis.mget = MagicMock(side_effect=redis_mget)

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify mixed sources
        assert data["realtime_prices_available"] == 1

        aapl_pos = next(p for p in data["positions"] if p["symbol"] == "AAPL")
        assert aapl_pos["price_source"] == "real-time"
        assert Decimal(aapl_pos["current_price"]) == Decimal("151.05")

        msft_pos = next(p for p in data["positions"] if p["symbol"] == "MSFT")
        assert msft_pos["price_source"] == "database"
        assert Decimal(msft_pos["current_price"]) == Decimal("295.00")

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_redis_error_handling(
        self, mock_redis, mock_db, test_client, mock_positions
    ):
        """Test graceful handling of Redis errors."""
        # Setup database mock
        mock_db.get_all_positions.return_value = mock_positions

        # Setup Redis mock to raise RedisError
        mock_redis.mget = MagicMock(side_effect=RedisError("Redis connection error"))

        # Make request - should still work with database fallback
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Should fall back to database prices
        assert data["realtime_prices_available"] == 0
        for pos in data["positions"]:
            assert pos["price_source"] == "database"

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_redis_timeout_error(
        self, mock_redis, mock_db, test_client, mock_positions
    ):
        """Test graceful handling of Redis timeout errors (not just ConnectionError)."""
        # Setup database mock
        mock_db.get_all_positions.return_value = mock_positions

        # Setup Redis mock to raise TimeoutError (different from ConnectionError)
        from redis.exceptions import TimeoutError as RedisTimeoutError
        mock_redis.mget = MagicMock(side_effect=RedisTimeoutError("Redis timeout"))

        # Make request - should still work with database fallback
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Should fall back to database prices
        assert data["realtime_prices_available"] == 0
        for pos in data["positions"]:
            assert pos["price_source"] == "database"

    @patch("apps.execution_gateway.main.db_client")
    def test_realtime_pnl_no_positions(self, mock_db, test_client):
        """Test endpoint with no open positions."""
        # Setup database mock - no positions
        mock_db.get_all_positions.return_value = []

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        assert data["total_positions"] == 0
        assert data["positions"] == []
        assert Decimal(data["total_unrealized_pl"]) == Decimal("0.00")
        assert data["total_unrealized_pl_pct"] is None
        assert data["realtime_prices_available"] == 0

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_percentage_calculations(
        self, mock_redis, mock_db, test_client
    ):
        """Test P&L percentage calculations are correct."""
        # Create positions with known prices for easy math
        positions = [
            Position(
                symbol="TEST1",
                qty=Decimal("100"),
                avg_entry_price=Decimal("100.00"),
                current_price=None,
                unrealized_pl=Decimal("0"),
                realized_pl=Decimal("0"),
                updated_at=datetime.now(timezone.utc),
            ),
            Position(
                symbol="TEST2",
                qty=Decimal("50"),
                avg_entry_price=Decimal("200.00"),
                current_price=None,
                unrealized_pl=Decimal("0"),
                realized_pl=Decimal("0"),
                updated_at=datetime.now(timezone.utc),
            ),
        ]

        mock_db.get_all_positions.return_value = positions

        # Setup Redis with prices that give 10% gain for both (using mget for batch fetch)
        def redis_mget(keys):
            result = []
            for key in keys:
                if key == "price:TEST1":
                    result.append(json.dumps({
                        "symbol": "TEST1",
                        "bid": 110.00,
                        "ask": 110.00,
                        "mid": 110.00,  # 10% gain
                        "timestamp": "2024-10-19T14:30:00+00:00",
                    }))
                elif key == "price:TEST2":
                    result.append(json.dumps({
                        "symbol": "TEST2",
                        "bid": 220.00,
                        "ask": 220.00,
                        "mid": 220.00,  # 10% gain
                        "timestamp": "2024-10-19T14:30:00+00:00",
                    }))
                else:
                    result.append(None)
            return result

        mock_redis.mget = MagicMock(side_effect=redis_mget)

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify per-position percentages
        test1_pos = next(p for p in data["positions"] if p["symbol"] == "TEST1")
        assert Decimal(test1_pos["unrealized_pl_pct"]) == Decimal("10.00")

        test2_pos = next(p for p in data["positions"] if p["symbol"] == "TEST2")
        assert Decimal(test2_pos["unrealized_pl_pct"]) == Decimal("10.00")

        # Verify total percentage
        # TEST1: 100 shares * $100 = $10,000 investment, $1,000 profit
        # TEST2: 50 shares * $200 = $10,000 investment, $1,000 profit
        # Total: $20,000 investment, $2,000 profit = 10%
        assert Decimal(data["total_unrealized_pl_pct"]) == Decimal("10.00")

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_timestamp_included(
        self, mock_redis, mock_db, test_client, mock_positions
    ):
        """Test that response includes timestamp."""
        mock_db.get_all_positions.return_value = mock_positions
        mock_redis.mget = MagicMock(side_effect=lambda keys: [None] * len(keys))

        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify timestamp exists and is recent
        assert "timestamp" in data
        timestamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        assert (now - timestamp).total_seconds() < 5  # Within 5 seconds

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_short_positions(self, mock_redis, mock_db, test_client):
        """Test P&L calculation for short positions."""
        # Short position - profits when price goes down
        position = Position(
            symbol="SHORT",
            qty=Decimal("-10"),  # Negative qty for short
            avg_entry_price=Decimal("150.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )

        mock_db.get_all_positions.return_value = [position]

        # Setup Redis with price that's lower (profit for short) - using mget for batch fetch
        def redis_mget(keys):
            result = []
            for key in keys:
                if key == "price:SHORT":
                    result.append(json.dumps({
                        "symbol": "SHORT",
                        "bid": 140.00,
                        "ask": 140.00,
                        "mid": 140.00,  # Price down $10
                        "timestamp": "2024-10-19T14:30:00+00:00",
                    }))
                else:
                    result.append(None)
            return result

        mock_redis.mget = MagicMock(side_effect=redis_mget)

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify short P&L
        short_pos = data["positions"][0]
        # P&L = (140 - 150) * (-10) = (-10) * (-10) = 100
        assert Decimal(short_pos["unrealized_pl"]) == Decimal("100.00")
        # P&L % = (unrealized_pl / (entry_price * abs(qty))) * 100
        # = (100 / (150 * 10)) * 100 = (100 / 1500) * 100 = 6.67%
        # Profitable short shows positive percentage (based on actual profit)
        assert abs(Decimal(short_pos["unrealized_pl_pct"]) - Decimal("6.67")) < Decimal(
            "0.01"
        )

    @patch("apps.execution_gateway.main.db_client")
    @patch("apps.execution_gateway.main.redis_client")
    def test_realtime_pnl_with_zero_price_edge_case(
        self, mock_redis, mock_db, test_client
    ):
        """
        Test that Decimal('0') is treated as a valid price (not falsy).

        This is an edge case fix from automated review: the condition
        `if pos.current_price:` would incorrectly skip Decimal('0') because
        it's falsy in Python. The fix uses `if pos.current_price is not None:`
        to explicitly check for None.
        """
        # Position with current_price = Decimal('0') (edge case)
        position = Position(
            symbol="ZERO",
            qty=Decimal("100"),
            avg_entry_price=Decimal("10.00"),
            current_price=Decimal("0"),  # Zero price should be valid
            unrealized_pl=Decimal("-1000.00"),  # Calculated from zero price
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )

        mock_db.get_all_positions.return_value = [position]

        # Redis has no real-time price - should fall back to database
        mock_redis.mget = MagicMock(side_effect=lambda keys: [None] * len(keys))

        # Make request
        response = test_client.get("/api/v1/positions/pnl/realtime")

        assert response.status_code == 200
        data = response.json()

        # Verify zero price is used from database (not fallback to entry price)
        zero_pos = data["positions"][0]
        assert zero_pos["price_source"] == "database"  # NOT "fallback"
        assert Decimal(zero_pos["current_price"]) == Decimal("0.00")

        # P&L should be calculated correctly with zero price
        # P&L = (0 - 10) * 100 = -1000
        assert Decimal(zero_pos["unrealized_pl"]) == Decimal("-1000.00")

        # P&L % = (-1000 / (10 * 100)) * 100 = -100%
        assert Decimal(zero_pos["unrealized_pl_pct"]) == Decimal("-100.00")


class TestResolveAndCalculatePnL:
    """Unit tests for _resolve_and_calculate_pnl helper function.
    
    This tests the refactored helper function that resolves price sources
    and calculates P&L, addressing Gemini's MEDIUM priority review feedback
    about improving modularity and readability.
    """

    def test_resolve_with_realtime_price(self):
        """Test price resolution when real-time price is available."""
        position = Position(
            symbol="AAPL",
            qty=Decimal("10"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("148.00"),  # Database price (should be ignored)
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # Real-time price data from Redis
        realtime_price_data = (Decimal("152.50"), datetime(2024, 10, 19, 14, 30, 0, tzinfo=timezone.utc))
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # Should use real-time price (not database price)
        assert position_pnl.price_source == "real-time"
        assert position_pnl.current_price == Decimal("152.50")
        assert is_realtime is True
        
        # Should have correct P&L: (152.50 - 150.00) * 10 = 25.00
        assert position_pnl.unrealized_pl == Decimal("25.00")
        assert position_pnl.last_price_update == datetime(2024, 10, 19, 14, 30, 0, tzinfo=timezone.utc)

    def test_resolve_with_database_fallback(self):
        """Test price resolution when real-time unavailable, falls back to database."""
        position = Position(
            symbol="MSFT",
            qty=Decimal("5"),
            avg_entry_price=Decimal("300.00"),
            current_price=Decimal("295.00"),  # Database price (should be used)
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # No real-time price available
        realtime_price_data = (None, None)
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # Should use database price
        assert position_pnl.price_source == "database"
        assert position_pnl.current_price == Decimal("295.00")
        assert is_realtime is False
        
        # Should have correct P&L: (295.00 - 300.00) * 5 = -25.00
        assert position_pnl.unrealized_pl == Decimal("-25.00")
        assert position_pnl.last_price_update is None  # Database source has no timestamp

    def test_resolve_with_entry_price_fallback(self):
        """Test price resolution when both real-time and database unavailable."""
        position = Position(
            symbol="GOOGL",
            qty=Decimal("3"),
            avg_entry_price=Decimal("140.00"),
            current_price=None,  # No database price
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # No real-time price available
        realtime_price_data = (None, None)
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # Should use entry price as fallback
        assert position_pnl.price_source == "fallback"
        assert position_pnl.current_price == Decimal("140.00")
        assert is_realtime is False
        
        # Should have zero P&L (using entry price)
        assert position_pnl.unrealized_pl == Decimal("0.00")
        assert position_pnl.last_price_update is None

    def test_resolve_with_short_position(self):
        """Test price resolution and P&L calculation for short positions."""
        position = Position(
            symbol="SHORT",
            qty=Decimal("-10"),  # Short position (negative qty)
            avg_entry_price=Decimal("150.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # Price went down (profitable for short)
        realtime_price_data = (Decimal("140.00"), datetime(2024, 10, 19, 14, 30, 0, tzinfo=timezone.utc))
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # Should use real-time price
        assert position_pnl.price_source == "real-time"
        assert position_pnl.current_price == Decimal("140.00")
        assert is_realtime is True
        
        # Should have positive P&L for profitable short: (140 - 150) * (-10) = 100
        assert position_pnl.unrealized_pl == Decimal("100.00")
        
        # P&L percentage should be positive: (100 / (150 * 10)) * 100 = 6.67%
        assert abs(position_pnl.unrealized_pl_pct - Decimal("6.67")) < Decimal("0.01")

    def test_resolve_with_zero_database_price(self):
        """Test that Decimal('0') database price is treated as valid (not None)."""
        position = Position(
            symbol="ZERO",
            qty=Decimal("100"),
            avg_entry_price=Decimal("10.00"),
            current_price=Decimal("0"),  # Zero is a valid price (edge case)
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # No real-time price
        realtime_price_data = (None, None)
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # Should use database price (Decimal('0') is valid, not fallback)
        assert position_pnl.price_source == "database"
        assert position_pnl.current_price == Decimal("0.00")
        assert is_realtime is False
        
        # Should have correct loss: (0 - 10) * 100 = -1000
        assert position_pnl.unrealized_pl == Decimal("-1000.00")

    def test_resolve_percentage_calculations(self):
        """Test that P&L percentage is calculated correctly."""
        position = Position(
            symbol="TEST",
            qty=Decimal("100"),
            avg_entry_price=Decimal("100.00"),
            current_price=None,
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # 10% gain
        realtime_price_data = (Decimal("110.00"), datetime.now(timezone.utc))
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # P&L: (110 - 100) * 100 = 1000
        assert position_pnl.unrealized_pl == Decimal("1000.00")
        
        # P&L %: (1000 / (100 * 100)) * 100 = 10%
        assert position_pnl.unrealized_pl_pct == Decimal("10.00")

    def test_resolve_with_none_tuple(self):
        """Test handling of (None, None) tuple for missing symbols."""
        position = Position(
            symbol="MISSING",
            qty=Decimal("10"),
            avg_entry_price=Decimal("50.00"),
            current_price=Decimal("55.00"),
            unrealized_pl=Decimal("0"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(timezone.utc),
        )
        
        # Explicitly (None, None) from .get() fallback
        realtime_price_data = (None, None)
        
        position_pnl, is_realtime = _resolve_and_calculate_pnl(position, realtime_price_data)
        
        # Should fall back to database price
        assert position_pnl.price_source == "database"
        assert position_pnl.current_price == Decimal("55.00")
        assert is_realtime is False
