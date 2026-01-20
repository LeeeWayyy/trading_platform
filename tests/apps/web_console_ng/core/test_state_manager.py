# tests/apps/web_console_ng/core/test_state_manager.py
"""Comprehensive unit tests for state_manager.py.

Tests cover:
- Custom JSON encoding/decoding for datetime, date, and Decimal types
- State persistence and restoration with Redis
- User preferences management with atomic updates
- Pending form handling with idempotent order IDs
- Concurrency handling (WatchError retries)
- Edge cases and error handling
- Session management and reconnection logic
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import WatchError

from apps.web_console_ng.core.state_manager import (
    TradingJSONEncoder,
    UserStateManager,
    _StateManagerRegistry,
    get_state_manager,
    trading_json_decoder,
    trading_json_object_hook,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def mock_redis_store():
    """Mock Redis store for state manager tests."""
    with patch("apps.web_console_ng.core.state_manager.get_redis_store") as mock:
        mock_instance = AsyncMock()
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture()
def user_state_manager(mock_redis_store):
    """Create UserStateManager instance with mocked Redis."""
    return UserStateManager("test-user-1", role="trader", strategies=["strategy-a"])


# =============================================================================
# TradingJSONEncoder Tests
# =============================================================================


class TestTradingJSONEncoder:
    """Tests for custom JSON encoder handling datetime, date, and Decimal types."""

    def test_encodes_naive_datetime_with_z_suffix(self) -> None:
        """Verify naive datetime is assumed UTC and encoded with 'Z' suffix."""
        naive_dt = datetime(2024, 1, 15, 10, 30, 45)
        payload = {"timestamp": naive_dt}

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["timestamp"]["__type__"] == "datetime"
        assert raw["timestamp"]["value"] == "2024-01-15T10:30:45Z"
        assert raw["timestamp"]["value"].endswith("Z")

    def test_encodes_utc_aware_datetime(self) -> None:
        """Verify UTC-aware datetime is encoded with timezone info."""
        utc_dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=UTC)
        payload = {"timestamp": utc_dt}

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["timestamp"]["__type__"] == "datetime"
        # UTC-aware datetime should have timezone offset in isoformat
        assert "2024-01-15T10:30:45" in raw["timestamp"]["value"]

    def test_encodes_timezone_aware_datetime_non_utc(self) -> None:
        """Verify non-UTC timezone-aware datetime preserves offset."""
        # Eastern timezone (UTC-5)
        eastern = timezone(timedelta(hours=-5))
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=eastern)
        payload = {"timestamp": dt}

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["timestamp"]["__type__"] == "datetime"
        assert "-05:00" in raw["timestamp"]["value"]

    def test_encodes_date(self) -> None:
        """Verify date objects are encoded correctly."""
        d = date(2024, 1, 15)
        payload = {"trade_date": d}

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["trade_date"]["__type__"] == "date"
        assert raw["trade_date"]["value"] == "2024-01-15"

    def test_encodes_decimal(self) -> None:
        """Verify Decimal objects are encoded as strings to preserve precision."""
        decimal_val = Decimal("123.456789")
        payload = {"price": decimal_val}

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["price"]["__type__"] == "Decimal"
        assert raw["price"]["value"] == "123.456789"

    def test_encodes_multiple_special_types(self) -> None:
        """Verify encoder handles multiple special types in same payload."""
        payload = {
            "timestamp": datetime(2024, 1, 15, 10, 30, 45),
            "trade_date": date(2024, 1, 15),
            "price": Decimal("100.50"),
            "quantity": Decimal("1500.75"),
            "normal_string": "test",
            "normal_int": 42,
        }

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["timestamp"]["__type__"] == "datetime"
        assert raw["trade_date"]["__type__"] == "date"
        assert raw["price"]["__type__"] == "Decimal"
        assert raw["quantity"]["__type__"] == "Decimal"
        assert raw["normal_string"] == "test"
        assert raw["normal_int"] == 42

    def test_encodes_nested_structures(self) -> None:
        """Verify encoder handles nested dicts and lists with special types."""
        payload = {
            "orders": [
                {"timestamp": datetime(2024, 1, 15, 10, 30), "price": Decimal("100.50")},
                {"timestamp": datetime(2024, 1, 15, 11, 30), "price": Decimal("101.25")},
            ],
            "metadata": {"date": date(2024, 1, 15), "version": 1},
        }

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        raw = json.loads(encoded)

        assert raw["orders"][0]["timestamp"]["__type__"] == "datetime"
        assert raw["orders"][0]["price"]["__type__"] == "Decimal"
        assert raw["metadata"]["date"]["__type__"] == "date"

    def test_raises_typeerror_for_unsupported_type(self) -> None:
        """Verify encoder raises TypeError for unsupported types."""
        payload = {"custom": object()}

        with pytest.raises(TypeError):
            json.dumps(payload, cls=TradingJSONEncoder)


# =============================================================================
# trading_json_object_hook Tests
# =============================================================================


class TestTradingJSONObjectHook:
    """Tests for custom JSON decoder for datetime, date, and Decimal types."""

    def test_decodes_datetime_with_z_suffix(self) -> None:
        """Verify datetime with 'Z' suffix is decoded as UTC-aware."""
        encoded = json.dumps(
            {"timestamp": datetime(2024, 1, 15, 10, 30, 45)}, cls=TradingJSONEncoder
        )
        decoded = json.loads(encoded, object_hook=trading_json_object_hook)

        assert isinstance(decoded["timestamp"], datetime)
        assert decoded["timestamp"].tzinfo == UTC
        assert decoded["timestamp"].year == 2024
        assert decoded["timestamp"].month == 1
        assert decoded["timestamp"].day == 15

    def test_decodes_date(self) -> None:
        """Verify date objects are decoded correctly."""
        encoded = json.dumps({"trade_date": date(2024, 1, 15)}, cls=TradingJSONEncoder)
        decoded = json.loads(encoded, object_hook=trading_json_object_hook)

        assert isinstance(decoded["trade_date"], date)
        assert decoded["trade_date"] == date(2024, 1, 15)

    def test_decodes_decimal(self) -> None:
        """Verify Decimal objects are decoded correctly preserving precision."""
        encoded = json.dumps({"price": Decimal("123.456789")}, cls=TradingJSONEncoder)
        decoded = json.loads(encoded, object_hook=trading_json_object_hook)

        assert isinstance(decoded["price"], Decimal)
        assert decoded["price"] == Decimal("123.456789")

    def test_roundtrip_preserves_values(self) -> None:
        """Verify encode-decode roundtrip preserves all values correctly."""
        original = {
            "timestamp": datetime(2024, 1, 15, 10, 30, 45),
            "trade_date": date(2024, 1, 15),
            "price": Decimal("100.50"),
        }

        encoded = json.dumps(original, cls=TradingJSONEncoder)
        decoded = json.loads(encoded, object_hook=trading_json_object_hook)

        # Naive datetime becomes UTC-aware after roundtrip
        assert decoded["timestamp"].replace(tzinfo=None) == original["timestamp"]
        assert decoded["timestamp"].tzinfo == UTC
        assert decoded["trade_date"] == original["trade_date"]
        assert decoded["price"] == original["price"]

    def test_ignores_regular_dicts(self) -> None:
        """Verify decoder doesn't interfere with regular dict objects."""
        payload = {"key": "value", "number": 42, "nested": {"inner": "data"}}

        encoded = json.dumps(payload)
        decoded = json.loads(encoded, object_hook=trading_json_object_hook)

        assert decoded == payload

    def test_trading_json_decoder_is_same_as_hook(self) -> None:
        """Verify trading_json_decoder is an alias for trading_json_object_hook."""
        assert trading_json_decoder is trading_json_object_hook


# =============================================================================
# UserStateManager Initialization Tests
# =============================================================================


class TestUserStateManagerInit:
    """Tests for UserStateManager initialization."""

    def test_init_with_required_params(self, mock_redis_store) -> None:
        """Verify UserStateManager initializes with required user_id."""
        manager = UserStateManager("user-123")

        assert manager.user_id == "user-123"
        assert manager.role is None
        assert manager.strategies == []
        assert manager.state_key == "user_state:user-123"

    def test_init_with_all_params(self, mock_redis_store) -> None:
        """Verify UserStateManager initializes with all parameters."""
        manager = UserStateManager("user-123", role="admin", strategies=["strat-a", "strat-b"])

        assert manager.user_id == "user-123"
        assert manager.role == "admin"
        assert manager.strategies == ["strat-a", "strat-b"]
        assert manager.state_key == "user_state:user-123"

    def test_state_key_prefix_constant(self) -> None:
        """Verify STATE_KEY_PREFIX constant value."""
        assert UserStateManager.STATE_KEY_PREFIX == "user_state:"

    def test_state_ttl_constant(self) -> None:
        """Verify STATE_TTL is 24 hours (86400 seconds)."""
        assert UserStateManager.STATE_TTL == 86400


# =============================================================================
# UserStateManager State Persistence Tests
# =============================================================================


class TestUserStateManagerPersistence:
    """Tests for save_critical_state and restore_state methods."""

    @pytest.mark.asyncio()
    async def test_save_critical_state_success(self, user_state_manager, mock_redis_store) -> None:
        """Verify save_critical_state persists state with metadata to Redis."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        state = {
            "preferences": {"theme": "dark", "layout": "grid"},
            "filters": {"symbol": "AAPL"},
        }

        await user_state_manager.save_critical_state(state)

        # Verify Redis setex was called
        master.setex.assert_called_once()
        call_args = master.setex.call_args[0]

        assert call_args[0] == "user_state:test-user-1"  # key
        assert call_args[1] == 86400  # TTL

        # Verify saved data structure
        saved_json = json.loads(call_args[2])
        assert saved_json["data"] == state
        assert saved_json["version"] == 1
        assert "saved_at" in saved_json
        # Verify timestamp is UTC ISO format
        saved_at = datetime.fromisoformat(saved_json["saved_at"])
        assert saved_at.tzinfo is not None

    @pytest.mark.asyncio()
    async def test_save_critical_state_with_special_types(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_critical_state handles datetime and Decimal types."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        state = {
            "last_trade": datetime(2024, 1, 15, 10, 30),
            "last_price": Decimal("100.50"),
            "trade_date": date(2024, 1, 15),
        }

        await user_state_manager.save_critical_state(state)

        master.setex.assert_called_once()
        saved_json = json.loads(master.setex.call_args[0][2])

        # Verify special types are encoded
        assert "__type__" in saved_json["data"]["last_trade"]
        assert "__type__" in saved_json["data"]["last_price"]
        assert "__type__" in saved_json["data"]["trade_date"]

    @pytest.mark.asyncio()
    async def test_restore_state_success(self, user_state_manager, mock_redis_store) -> None:
        """Verify restore_state retrieves and decodes state from Redis master."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        saved_state = {
            "data": {
                "preferences": {"theme": "dark"},
                "filters": {"symbol": "AAPL"},
            },
            "saved_at": datetime.now(UTC).isoformat(),
            "version": 1,
        }
        master.get.return_value = json.dumps(saved_state, cls=TradingJSONEncoder)

        restored = await user_state_manager.restore_state()

        # Verify reads from master (not replica)
        mock_redis_store.get_master.assert_called_once()
        master.get.assert_called_once_with("user_state:test-user-1")

        assert restored == saved_state["data"]

    @pytest.mark.asyncio()
    async def test_restore_state_reads_from_master_not_replica(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify restore_state reads from master to avoid stale replica data."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = None

        await user_state_manager.restore_state()

        # Should call get_master, not get_slave
        mock_redis_store.get_master.assert_called_once()
        assert not hasattr(mock_redis_store, "get_slave") or not mock_redis_store.get_slave.called

    @pytest.mark.asyncio()
    async def test_restore_state_empty_when_no_data(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify restore_state returns empty dict when no data exists."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = None

        restored = await user_state_manager.restore_state()

        assert restored == {}

    @pytest.mark.asyncio()
    async def test_restore_state_handles_corrupted_json(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify restore_state handles corrupted JSON gracefully."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = "{invalid-json"

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            restored = await user_state_manager.restore_state()

        assert restored == {}
        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        assert "corrupted state" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_restore_state_records_metric_on_json_error(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify restore_state records metric when JSON decode fails."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = "{not-valid-json"

        with patch("apps.web_console_ng.core.metrics.record_state_save_error") as record_error:
            restored = await user_state_manager.restore_state()

        assert restored == {}
        record_error.assert_called_once_with("json_decode_error")

    @pytest.mark.asyncio()
    async def test_restore_state_handles_missing_metrics_module(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify restore_state doesn't fail when metrics module unavailable."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = "invalid{json"

        # Simulate ImportError for metrics module
        with patch("apps.web_console_ng.core.state_manager.logger"):
            with patch.dict("sys.modules", {"apps.web_console_ng.core.metrics": None}):
                restored = await user_state_manager.restore_state()

        # Should not raise, just return empty dict
        assert restored == {}

    @pytest.mark.asyncio()
    async def test_restore_state_with_special_types(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify restore_state correctly decodes datetime and Decimal types."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        saved_state = {
            "data": {
                "last_trade": datetime(2024, 1, 15, 10, 30, 45),
                "last_price": Decimal("100.50"),
                "trade_date": date(2024, 1, 15),
            },
            "saved_at": datetime.now(UTC).isoformat(),
            "version": 1,
        }
        master.get.return_value = json.dumps(saved_state, cls=TradingJSONEncoder)

        restored = await user_state_manager.restore_state()

        assert isinstance(restored["last_trade"], datetime)
        assert isinstance(restored["last_price"], Decimal)
        assert isinstance(restored["trade_date"], date)
        assert restored["last_price"] == Decimal("100.50")


# =============================================================================
# UserStateManager Preferences Tests
# =============================================================================


class TestUserStateManagerPreferences:
    """Tests for save_preferences and update_preference methods."""

    @pytest.mark.asyncio()
    async def test_save_preferences_success(self, user_state_manager, mock_redis_store) -> None:
        """Verify save_preferences atomically updates a single preference."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        # Mock pipeline
        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None

        # Existing state
        existing_state = {"data": {"preferences": {"theme": "light"}}}
        pipe.get.return_value = json.dumps(existing_state)

        await user_state_manager.save_preferences("layout", "grid")

        # Verify WATCH was called
        pipe.watch.assert_called_once_with("user_state:test-user-1")

        # Verify MULTI/EXEC pattern
        pipe.multi.assert_called_once()
        pipe.execute.assert_called_once()

        # Verify data was saved with both old and new preferences
        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert saved_json["data"]["preferences"]["theme"] == "light"
        assert saved_json["data"]["preferences"]["layout"] == "grid"

    @pytest.mark.asyncio()
    async def test_save_preferences_creates_preferences_if_missing(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_preferences creates preferences dict if it doesn't exist."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None

        # No existing preferences
        pipe.get.return_value = json.dumps({"data": {}})

        await user_state_manager.save_preferences("theme", "dark")

        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert saved_json["data"]["preferences"] == {"theme": "dark"}

    @pytest.mark.asyncio()
    async def test_save_preferences_handles_no_existing_state(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_preferences works when no state exists yet."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = None  # No existing state

        await user_state_manager.save_preferences("theme", "dark")

        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert saved_json["data"]["preferences"] == {"theme": "dark"}

    @pytest.mark.asyncio()
    async def test_save_preferences_retries_on_watch_error(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_preferences retries on WatchError (concurrent modification)."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {"preferences": {"theme": "light"}}})

        # First attempt fails with WatchError, second succeeds
        pipe.execute.side_effect = [WatchError(), None]

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            await user_state_manager.save_preferences("theme", "dark")

        # Verify retry happened
        assert pipe.execute.call_count == 2
        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        assert "retry due to WatchError" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_save_preferences_fails_after_max_retries(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_preferences raises after max retries on WatchError."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})

        # All attempts fail
        pipe.execute.side_effect = WatchError()

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            with pytest.raises(WatchError):
                await user_state_manager.save_preferences("theme", "dark")

        # Verify error was logged on final failure
        mock_logger.error.assert_called_once()
        assert "failed after retries" in mock_logger.error.call_args[0][0]

    @pytest.mark.asyncio()
    async def test_save_preferences_handles_corrupted_existing_state(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_preferences uses empty state if existing state is corrupted."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = "{invalid-json"

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            await user_state_manager.save_preferences("theme", "dark")

        # Verify warning was logged
        mock_logger.warning.assert_called()
        assert "Failed to parse state JSON" in mock_logger.warning.call_args[0][0]

        # Verify new state was saved despite corrupted existing state
        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert saved_json["data"]["preferences"] == {"theme": "dark"}

    @pytest.mark.asyncio()
    async def test_update_preference_is_alias_for_save_preferences(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify update_preference is a backward-compatible wrapper."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})

        await user_state_manager.update_preference("theme", "dark")

        # Verify it calls save_preferences logic
        pipe.setex.assert_called_once()
        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert saved_json["data"]["preferences"] == {"theme": "dark"}


# =============================================================================
# UserStateManager Pending Forms Tests
# =============================================================================


class TestUserStateManagerPendingForms:
    """Tests for save_pending_form and clear_pending_form methods."""

    @pytest.mark.asyncio()
    async def test_save_pending_form_success(self, user_state_manager, mock_redis_store) -> None:
        """Verify save_pending_form stores form data with metadata."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})

        form_data = {"symbol": "AAPL", "quantity": 100}
        await user_state_manager.save_pending_form("order-form-1", form_data)

        saved_json = json.loads(pipe.setex.call_args[0][2])
        pending_forms = saved_json["data"]["pending_forms"]

        assert "order-form-1" in pending_forms
        assert pending_forms["order-form-1"]["data"] == form_data
        assert "saved_at" in pending_forms["order-form-1"]
        assert pending_forms["order-form-1"]["client_order_id"] is None
        assert "original_data_hash" in pending_forms["order-form-1"]

    @pytest.mark.asyncio()
    async def test_save_pending_form_with_client_order_id(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_pending_form stores client_order_id for idempotent resubmission."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})

        form_data = {"symbol": "AAPL", "quantity": 100}
        client_order_id = "coid-12345"

        await user_state_manager.save_pending_form(
            "order-form-1", form_data, client_order_id=client_order_id
        )

        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert (
            saved_json["data"]["pending_forms"]["order-form-1"]["client_order_id"]
            == client_order_id
        )

    @pytest.mark.asyncio()
    async def test_save_pending_form_includes_data_hash(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_pending_form includes sha256 hash of form data."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})

        form_data = {"symbol": "AAPL", "quantity": 100}
        expected_hash = hashlib.sha256(
            json.dumps(form_data, sort_keys=True).encode("utf-8")
        ).hexdigest()

        await user_state_manager.save_pending_form("order-form-1", form_data)

        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert (
            saved_json["data"]["pending_forms"]["order-form-1"]["original_data_hash"]
            == expected_hash
        )

    @pytest.mark.asyncio()
    async def test_save_pending_form_preserves_existing_forms(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_pending_form preserves other existing pending forms."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None

        existing_state = {
            "data": {
                "pending_forms": {
                    "form-1": {"data": {"field": "value1"}},
                }
            }
        }
        pipe.get.return_value = json.dumps(existing_state)

        await user_state_manager.save_pending_form("form-2", {"field": "value2"})

        saved_json = json.loads(pipe.setex.call_args[0][2])
        pending_forms = saved_json["data"]["pending_forms"]

        # Both forms should exist
        assert "form-1" in pending_forms
        assert "form-2" in pending_forms
        assert pending_forms["form-1"]["data"] == {"field": "value1"}
        assert pending_forms["form-2"]["data"] == {"field": "value2"}

    @pytest.mark.asyncio()
    async def test_save_pending_form_retries_on_watch_error(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_pending_form retries on WatchError."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})
        pipe.execute.side_effect = [WatchError(), None]

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            await user_state_manager.save_pending_form("form-1", {"data": "test"})

        assert pipe.execute.call_count == 2
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio()
    async def test_save_pending_form_fails_after_max_retries(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify save_pending_form raises after max retries."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})
        pipe.execute.side_effect = WatchError()

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            with pytest.raises(WatchError):
                await user_state_manager.save_pending_form("form-1", {"data": "test"})

        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio()
    async def test_clear_pending_form_removes_form(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify clear_pending_form removes specified form."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None

        existing_state = {
            "data": {
                "pending_forms": {
                    "form-1": {"data": {"a": 1}},
                    "form-2": {"data": {"b": 2}},
                }
            }
        }
        pipe.get.return_value = json.dumps(existing_state)

        await user_state_manager.clear_pending_form("form-1")

        saved_json = json.loads(pipe.setex.call_args[0][2])
        pending_forms = saved_json["data"]["pending_forms"]

        # form-1 should be removed, form-2 should remain
        assert "form-1" not in pending_forms
        assert "form-2" in pending_forms

    @pytest.mark.asyncio()
    async def test_clear_pending_form_handles_nonexistent_form(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify clear_pending_form handles clearing non-existent form gracefully."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {"pending_forms": {}}})

        # Should not raise
        await user_state_manager.clear_pending_form("nonexistent-form")

        saved_json = json.loads(pipe.setex.call_args[0][2])
        assert saved_json["data"]["pending_forms"] == {}

    @pytest.mark.asyncio()
    async def test_clear_pending_form_retries_on_watch_error(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify clear_pending_form retries on WatchError."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {"pending_forms": {"form-1": {}}}})
        pipe.execute.side_effect = [WatchError(), None]

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            await user_state_manager.clear_pending_form("form-1")

        assert pipe.execute.call_count == 2
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio()
    async def test_clear_pending_form_fails_after_max_retries(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify clear_pending_form raises after max retries."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {"pending_forms": {}}})
        pipe.execute.side_effect = WatchError()

        with patch("apps.web_console_ng.core.state_manager.logger") as mock_logger:
            with pytest.raises(WatchError):
                await user_state_manager.clear_pending_form("form-1")

        mock_logger.error.assert_called_once()


# =============================================================================
# UserStateManager Reconnection Tests
# =============================================================================


class TestUserStateManagerReconnection:
    """Tests for on_reconnect method."""

    @pytest.mark.asyncio()
    async def test_on_reconnect_restores_state_and_fetches_api_data(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify on_reconnect combines persisted state with fresh API data."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        persisted_state = {
            "data": {
                "preferences": {"theme": "dark"},
                "filters": {"symbol": "AAPL"},
                "pending_forms": {"form-1": {"data": {}}},
            }
        }
        master.get.return_value = json.dumps(persisted_state)

        with patch("apps.web_console_ng.core.state_manager.AsyncTradingClient") as client_patch:
            client_instance = AsyncMock()
            client_patch.get.return_value = client_instance
            client_instance.fetch_positions.return_value = [{"symbol": "AAPL", "qty": 100}]
            client_instance.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

            result = await user_state_manager.on_reconnect(ui_context=object())

        # Verify combined result
        assert result["preferences"] == {"theme": "dark"}
        assert result["filters"] == {"symbol": "AAPL"}
        assert result["pending_forms"] == {"form-1": {"data": {}}}
        assert result["api_data"]["positions"] == [{"symbol": "AAPL", "qty": 100}]
        assert result["api_data"]["kill_switch"] == {"state": "DISENGAGED"}

    @pytest.mark.asyncio()
    async def test_on_reconnect_passes_auth_context_to_client(self, mock_redis_store) -> None:
        """Verify on_reconnect passes user_id, role, strategies to API client."""
        manager = UserStateManager("user-123", role="admin", strategies=["strat-a", "strat-b"])
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = json.dumps({"data": {}})

        with patch("apps.web_console_ng.core.state_manager.AsyncTradingClient") as client_patch:
            client_instance = AsyncMock()
            client_patch.get.return_value = client_instance
            client_instance.fetch_positions.return_value = []
            client_instance.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

            await manager.on_reconnect(ui_context=object())

        # Verify auth context was passed
        client_instance.fetch_positions.assert_called_once_with(
            "user-123", role="admin", strategies=["strat-a", "strat-b"]
        )
        client_instance.fetch_kill_switch_status.assert_called_once_with(
            "user-123", role="admin", strategies=["strat-a", "strat-b"]
        )

    @pytest.mark.asyncio()
    async def test_on_reconnect_returns_empty_sections_when_no_state(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify on_reconnect handles missing state sections gracefully."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master
        master.get.return_value = None  # No persisted state

        with patch("apps.web_console_ng.core.state_manager.AsyncTradingClient") as client_patch:
            client_instance = AsyncMock()
            client_patch.get.return_value = client_instance
            client_instance.fetch_positions.return_value = []
            client_instance.fetch_kill_switch_status.return_value = {"state": "DISENGAGED"}

            result = await user_state_manager.on_reconnect(ui_context=object())

        assert result["preferences"] == {}
        assert result["filters"] == {}
        assert result["pending_forms"] == {}
        assert "api_data" in result


# =============================================================================
# UserStateManager Delete State Tests
# =============================================================================


class TestUserStateManagerDeleteState:
    """Tests for delete_state method."""

    @pytest.mark.asyncio()
    async def test_delete_state_removes_from_redis(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify delete_state removes user state from Redis."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        await user_state_manager.delete_state()

        mock_redis_store.get_master.assert_called_once()
        master.delete.assert_called_once_with("user_state:test-user-1")


# =============================================================================
# _StateManagerRegistry Tests
# =============================================================================


class TestStateManagerRegistry:
    """Tests for _StateManagerRegistry and get_state_manager singleton."""

    @pytest.mark.asyncio()
    async def test_registry_close(self) -> None:
        """Verify registry close sets _closed flag."""
        registry = _StateManagerRegistry()

        assert registry._closed is False

        await registry.close()

        assert registry._closed is True

    def test_get_state_manager_returns_singleton(self) -> None:
        """Verify get_state_manager returns same instance."""
        # Reset global instance for test isolation
        import apps.web_console_ng.core.state_manager as sm

        sm._state_manager_instance = None

        instance1 = get_state_manager()
        instance2 = get_state_manager()

        assert instance1 is instance2
        assert isinstance(instance1, _StateManagerRegistry)

    def test_get_state_manager_creates_instance_on_first_call(self) -> None:
        """Verify get_state_manager creates instance on first call."""
        import apps.web_console_ng.core.state_manager as sm

        sm._state_manager_instance = None

        instance = get_state_manager()

        assert instance is not None
        assert sm._state_manager_instance is instance


# =============================================================================
# Edge Cases and Integration Tests
# =============================================================================


class TestEdgeCases:
    """Edge case and integration tests."""

    @pytest.mark.asyncio()
    async def test_concurrent_preference_updates_with_watch_errors(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Verify multiple concurrent updates retry correctly on WatchError."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {"preferences": {}}})

        # Simulate contention: first 2 attempts fail, third succeeds
        pipe.execute.side_effect = [WatchError(), WatchError(), None]

        with patch("apps.web_console_ng.core.state_manager.logger"):
            await user_state_manager.save_preferences("key", "value")

        # Should have retried and succeeded on third attempt
        assert pipe.execute.call_count == 3

    @pytest.mark.asyncio()
    async def test_full_workflow_save_restore_delete(
        self, user_state_manager, mock_redis_store
    ) -> None:
        """Integration test: save → restore → delete workflow."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        # Save
        state = {"preferences": {"theme": "dark"}, "filters": {"symbol": "AAPL"}}
        await user_state_manager.save_critical_state(state)

        # Simulate restore (return what was saved)
        saved_data = master.setex.call_args[0][2]
        master.get.return_value = saved_data

        # Restore
        restored = await user_state_manager.restore_state()
        assert restored == state

        # Delete
        await user_state_manager.delete_state()
        master.delete.assert_called_once_with("user_state:test-user-1")

    @pytest.mark.asyncio()
    async def test_nested_watch_error_handling(self, user_state_manager, mock_redis_store) -> None:
        """Verify nested WATCH/MULTI/EXEC errors are handled correctly."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        pipe = AsyncMock()
        master.pipeline = MagicMock(return_value=pipe)
        pipe.__aenter__.return_value = pipe
        pipe.__aexit__.return_value = None
        pipe.get.return_value = json.dumps({"data": {}})

        # Simulate watch errors on both preference and form operations
        pipe.execute.side_effect = [WatchError(), None]

        with patch("apps.web_console_ng.core.state_manager.logger"):
            await user_state_manager.save_preferences("key1", "value1")
            # Reset for next call
            pipe.execute.side_effect = [WatchError(), None]
            await user_state_manager.save_pending_form("form-1", {"data": "test"})

        # Both should have succeeded after retry
        assert pipe.execute.call_count == 4  # 2 calls × 2 attempts

    def test_encoder_handles_deeply_nested_structures(self) -> None:
        """Verify encoder handles deeply nested dicts and lists."""
        payload = {
            "level1": {
                "level2": {
                    "level3": [
                        {"timestamp": datetime(2024, 1, 1), "price": Decimal("100.50")},
                        {"timestamp": datetime(2024, 1, 2), "price": Decimal("101.25")},
                    ]
                }
            }
        }

        encoded = json.dumps(payload, cls=TradingJSONEncoder)
        decoded = json.loads(encoded, object_hook=trading_json_object_hook)

        assert isinstance(decoded["level1"]["level2"]["level3"][0]["timestamp"], datetime)
        assert isinstance(decoded["level1"]["level2"]["level3"][0]["price"], Decimal)

    @pytest.mark.asyncio()
    async def test_state_with_empty_values(self, user_state_manager, mock_redis_store) -> None:
        """Verify state manager handles empty values correctly."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        # Save state with empty values
        state = {"preferences": {}, "filters": {}, "pending_forms": {}}
        await user_state_manager.save_critical_state(state)

        saved_data = master.setex.call_args[0][2]
        master.get.return_value = saved_data

        restored = await user_state_manager.restore_state()
        assert restored == state

    @pytest.mark.asyncio()
    async def test_large_state_serialization(self, user_state_manager, mock_redis_store) -> None:
        """Verify state manager handles large state objects."""
        master = AsyncMock()
        mock_redis_store.get_master.return_value = master

        # Create large state with many entries
        large_state = {
            "preferences": {f"pref_{i}": f"value_{i}" for i in range(100)},
            "filters": {f"filter_{i}": [f"val_{j}" for j in range(10)] for i in range(50)},
            "pending_forms": {
                f"form_{i}": {
                    "data": {f"field_{j}": Decimal(f"{i}.{j}") for j in range(20)},
                    "timestamp": datetime(2024, 1, 1, i % 24, i % 60),
                }
                for i in range(30)
            },
        }

        await user_state_manager.save_critical_state(large_state)

        # Verify it was saved
        master.setex.assert_called_once()
        saved_data = master.setex.call_args[0][2]

        # Verify it can be decoded
        master.get.return_value = saved_data
        restored = await user_state_manager.restore_state()

        # Spot check some values
        assert len(restored["preferences"]) == 100
        assert len(restored["filters"]) == 50
        assert len(restored["pending_forms"]) == 30
