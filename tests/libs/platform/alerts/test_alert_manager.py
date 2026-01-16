"""
Unit tests for libs.platform.alerts.alert_manager.

Tests cover:
- AlertManager initialization (with/without optional dependencies)
- Alert triggering (success, queue full, no channels, timezone handling)
- Alert acknowledgment
- Rule fetching (success, not found, disabled)
- Delivery creation (success, deduplication, masked PII)
- Delivery enqueueing (success, failures, error handling)
- Recipient hash secret loading
- Sync Redis derivation

Target: 85%+ branch coverage (baseline from 0%)
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import ANY, AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from libs.platform.alerts.alert_manager import AlertManager, _RuleChannels
from libs.platform.alerts.delivery_service import QueueFullError
from libs.platform.alerts.models import AlertEvent, ChannelConfig, ChannelType


class TestAlertManagerInitialization:
    """Tests for AlertManager initialization."""

    @patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret")
    @patch("libs.platform.alerts.alert_manager.Queue")
    @patch("libs.platform.alerts.alert_manager.QueueDepthManager")
    def test_init_with_defaults(
        self, mock_depth_manager_class, mock_queue_class, mock_get_secret
    ):
        """Test AlertManager initializes with default RQ queue and depth manager."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379}
        mock_delivery_func = Mock()
        mock_get_secret.return_value = "test_secret"

        manager = AlertManager(
            db_pool=mock_db_pool,
            redis_client=mock_redis_async,
            delivery_job_func=mock_delivery_func,
        )

        assert manager.db_pool is mock_db_pool
        assert manager.redis is mock_redis_async
        assert manager._delivery_job_func is mock_delivery_func
        # Verify Queue created with name "alerts"
        mock_queue_class.assert_called_once()
        assert mock_queue_class.call_args[0][0] == "alerts"
        # Verify QueueDepthManager created with redis_client
        mock_depth_manager_class.assert_called_once_with(mock_redis_async)

    @patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret")
    def test_init_with_custom_queue_and_manager(self, mock_get_secret):
        """Test AlertManager accepts custom RQ queue and depth manager."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()
        mock_rq_queue = Mock()
        mock_depth_manager = Mock()
        mock_get_secret.return_value = "test_secret"

        manager = AlertManager(
            db_pool=mock_db_pool,
            redis_client=mock_redis_async,
            delivery_job_func=mock_delivery_func,
            rq_queue=mock_rq_queue,
            queue_depth_manager=mock_depth_manager,
        )

        assert manager.queue is mock_rq_queue
        assert manager.queue_depth_manager is mock_depth_manager

    @patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret")
    @patch("libs.platform.alerts.alert_manager.Redis")
    def test_init_with_custom_sync_redis(self, mock_redis_class, mock_get_secret):
        """Test AlertManager accepts custom sync Redis client."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_delivery_func = Mock()
        mock_redis_sync = Mock()
        mock_get_secret.return_value = "test_secret"

        with patch("libs.platform.alerts.alert_manager.Queue"):
            manager = AlertManager(
                db_pool=mock_db_pool,
                redis_client=mock_redis_async,
                delivery_job_func=mock_delivery_func,
                redis_sync=mock_redis_sync,
            )

        # Should not call _derive_sync_redis or Redis constructor
        mock_redis_class.assert_not_called()

    @patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret")
    def test_init_missing_recipient_secret_raises_error(self, mock_get_secret):
        """Test AlertManager raises ValueError when recipient secret missing."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {}
        mock_delivery_func = Mock()
        mock_get_secret.side_effect = ValueError("Secret not configured")

        with patch("libs.platform.alerts.alert_manager.Queue"):
            with pytest.raises(ValueError, match="Secret not configured"):
                AlertManager(
                    db_pool=mock_db_pool,
                    redis_client=mock_redis_async,
                    delivery_job_func=mock_delivery_func,
                )


class TestDeriveSyncRedis:
    """Tests for _derive_sync_redis helper method."""

    @patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret")
    @patch("libs.platform.alerts.alert_manager.Redis")
    def test_derive_sync_redis_from_async(self, mock_redis_class, mock_get_secret):
        """Test _derive_sync_redis constructs sync Redis from async connection params."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }
        mock_delivery_func = Mock()
        mock_get_secret.return_value = "test_secret"

        with patch("libs.platform.alerts.alert_manager.Queue"):
            AlertManager(
                db_pool=mock_db_pool,
                redis_client=mock_redis_async,
                delivery_job_func=mock_delivery_func,
            )

        # Verify Redis called with connection params
        mock_redis_class.assert_called_once_with(host="localhost", port=6379, db=0)


class TestTriggerAlert:
    """Tests for trigger_alert() main alert creation flow."""

    @pytest.fixture
    def manager(self):
        """Create AlertManager with mocked dependencies."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()
        mock_rq_queue = Mock()
        mock_depth_manager = AsyncMock()
        mock_depth_manager.is_accepting = AsyncMock(return_value=True)
        mock_depth_manager.increment = AsyncMock()

        with patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret", return_value="secret"):
            manager = AlertManager(
                db_pool=mock_db_pool,
                redis_client=mock_redis_async,
                delivery_job_func=mock_delivery_func,
                rq_queue=mock_rq_queue,
                queue_depth_manager=mock_depth_manager,
            )
        return manager

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.alert_queue_full_total")
    @patch("libs.platform.alerts.alert_manager.alert_dropped_total")
    async def test_trigger_alert_queue_full_raises_error(
        self, mock_dropped_metric, mock_queue_full_metric, manager
    ):
        """Test trigger_alert() raises QueueFullError when queue at capacity."""
        manager.queue_depth_manager.is_accepting = AsyncMock(return_value=False)

        with pytest.raises(QueueFullError):
            await manager.trigger_alert(
                rule_id="rule123",
                trigger_value=Decimal("100.5"),
                triggered_at=datetime.now(UTC),
            )

        # Verify metrics incremented
        mock_queue_full_metric.inc.assert_called_once()
        mock_dropped_metric.labels.assert_called_once_with(channel="all", reason="queue_full")
        mock_dropped_metric.labels.return_value.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_alert_no_channels_raises_error(self, manager):
        """Test trigger_alert() raises ValueError when rule has no enabled channels."""
        # Mock _fetch_rule to return rule with empty channels
        manager._fetch_rule = AsyncMock(return_value=_RuleChannels(name="TestRule", channels=[]))

        with pytest.raises(ValueError, match="has no enabled channels"):
            await manager.trigger_alert(
                rule_id="rule123",
                trigger_value=Decimal("100.5"),
                triggered_at=datetime.now(UTC),
            )

    @pytest.mark.asyncio
    async def test_trigger_alert_success_with_timezone_aware_timestamp(self, manager):
        """Test trigger_alert() successfully creates event with timezone-aware timestamp."""
        triggered_at = datetime.now(UTC)
        event_id = str(uuid4())
        rule_id = str(uuid4())
        mock_channel = ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com")
        manager._fetch_rule = AsyncMock(
            return_value=_RuleChannels(name="TestRule", channels=[mock_channel])
        )

        # Mock DB operations
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(
            return_value={
                "id": event_id,
                "rule_id": rule_id,
                "triggered_at": triggered_at,
                "trigger_value": Decimal("100.5"),
                "acknowledged_at": None,
                "acknowledged_by": None,
                "acknowledged_note": None,
                "routed_channels": ["email"],
                "created_at": triggered_at,
            }
        )
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock(return_value=None)  # Return None to propagate exceptions
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)  # Return None to propagate exceptions
        manager.db_pool.connection = Mock(return_value=mock_conn)

        # Mock _create_deliveries and _enqueue_deliveries
        manager._create_deliveries = AsyncMock(return_value=[("delivery123", mock_channel)])
        manager._enqueue_deliveries = AsyncMock()

        result = await manager.trigger_alert(
            rule_id=rule_id,
            trigger_value=Decimal("100.5"),
            triggered_at=triggered_at,
        )

        assert isinstance(result, AlertEvent)
        assert str(result.id) == event_id
        assert str(result.rule_id) == rule_id
        manager._enqueue_deliveries.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_alert_success_with_naive_timestamp(self, manager):
        """Test trigger_alert() converts naive timestamp to UTC-aware."""
        triggered_at = datetime(2025, 1, 15, 10, 0, 0)  # Naive timestamp
        event_id = str(uuid4())
        rule_id = str(uuid4())
        mock_channel = ChannelConfig(type=ChannelType.SLACK, recipient="#alerts")
        manager._fetch_rule = AsyncMock(
            return_value=_RuleChannels(name="TestRule", channels=[mock_channel])
        )

        # Mock DB operations
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(
            return_value={
                "id": event_id,
                "rule_id": rule_id,
                "triggered_at": triggered_at.replace(tzinfo=UTC),
                "trigger_value": None,
                "acknowledged_at": None,
                "acknowledged_by": None,
                "acknowledged_note": None,
                "routed_channels": ["slack"],
                "created_at": triggered_at.replace(tzinfo=UTC),
            }
        )
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock(return_value=None)
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        manager.db_pool.connection = Mock(return_value=mock_conn)

        manager._create_deliveries = AsyncMock(return_value=[("delivery456", mock_channel)])
        manager._enqueue_deliveries = AsyncMock()

        result = await manager.trigger_alert(
            rule_id=rule_id,
            trigger_value=None,
            triggered_at=triggered_at,
        )

        assert str(result.id) == event_id
        # Verify timestamp passed to execute is timezone-aware
        call_args = mock_cur.execute.call_args[0][1]
        assert call_args[2].tzinfo is not None

    @pytest.mark.asyncio
    async def test_trigger_alert_no_event_row_returned_raises_error(self, manager):
        """Test trigger_alert() raises RuntimeError when INSERT fails to return row."""
        triggered_at = datetime.now(UTC)
        rule_id = str(uuid4())
        mock_channel = ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com")
        manager._fetch_rule = AsyncMock(
            return_value=_RuleChannels(name="TestRule", channels=[mock_channel])
        )

        # Mock DB to return None (INSERT failure)
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=None)
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock(return_value=None)
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        manager.db_pool.connection = Mock(return_value=mock_conn)

        with pytest.raises(RuntimeError, match="Failed to insert alert_event"):
            await manager.trigger_alert(
                rule_id=rule_id,
                trigger_value=Decimal("100.5"),
                triggered_at=triggered_at,
            )


class TestAcknowledgeAlert:
    """Tests for acknowledge_alert() method."""

    @pytest.fixture
    def manager(self):
        """Create AlertManager with mocked dependencies."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()

        with patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret", return_value="secret"):
            with patch("libs.platform.alerts.alert_manager.Queue"):
                with patch("libs.platform.alerts.alert_manager.QueueDepthManager"):
                    manager = AlertManager(
                        db_pool=mock_db_pool,
                        redis_client=mock_redis_async,
                        delivery_job_func=mock_delivery_func,
                    )
        return manager

    @pytest.mark.asyncio
    async def test_acknowledge_alert_success(self, manager):
        """Test acknowledge_alert() updates alert_events with acknowledgment."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.execute = AsyncMock()
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        await manager.acknowledge_alert(
            alert_id="alert123",
            user_id="user456",
            note="Acknowledged by user",
        )

        mock_cur.execute.assert_called_once()
        call_args = mock_cur.execute.call_args[0]
        assert "UPDATE alert_events" in call_args[0]
        assert call_args[1][1] == "user456"
        assert call_args[1][2] == "Acknowledged by user"
        assert call_args[1][3] == "alert123"
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_acknowledge_alert_without_note(self, manager):
        """Test acknowledge_alert() works without optional note."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.execute = AsyncMock()
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        await manager.acknowledge_alert(
            alert_id="alert789",
            user_id="user123",
        )

        call_args = mock_cur.execute.call_args[0]
        assert call_args[1][2] is None  # note parameter is None


class TestFetchRule:
    """Tests for _fetch_rule() method."""

    @pytest.fixture
    def manager(self):
        """Create AlertManager with mocked dependencies."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()

        with patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret", return_value="secret"):
            with patch("libs.platform.alerts.alert_manager.Queue"):
                with patch("libs.platform.alerts.alert_manager.QueueDepthManager"):
                    manager = AlertManager(
                        db_pool=mock_db_pool,
                        redis_client=mock_redis_async,
                        delivery_job_func=mock_delivery_func,
                    )
        return manager

    @pytest.mark.asyncio
    async def test_fetch_rule_success_with_enabled_channels(self, manager):
        """Test _fetch_rule() returns rule with enabled channels."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(
            return_value={
                "name": "High Drawdown Alert",
                "channels": [
                    {"type": "email", "recipient": "test@example.com", "enabled": True},
                    {"type": "slack", "recipient": "#alerts", "enabled": True},
                ],
            }
        )
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        result = await manager._fetch_rule("rule123")

        assert result.name == "High Drawdown Alert"
        assert len(result.channels) == 2
        assert result.channels[0].type == ChannelType.EMAIL
        assert result.channels[1].type == ChannelType.SLACK

    @pytest.mark.asyncio
    async def test_fetch_rule_filters_disabled_channels(self, manager):
        """Test _fetch_rule() filters out disabled channels."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(
            return_value={
                "name": "Test Rule",
                "channels": [
                    {"type": "email", "recipient": "test@example.com", "enabled": True},
                    {"type": "slack", "recipient": "#alerts", "enabled": False},
                    {"type": "sms", "recipient": "+1234567890"},  # Missing enabled (default True)
                ],
            }
        )
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        result = await manager._fetch_rule("rule456")

        # Only email and sms should be included (slack disabled)
        assert len(result.channels) == 2
        assert result.channels[0].type == ChannelType.EMAIL
        assert result.channels[1].type == ChannelType.SMS

    @pytest.mark.asyncio
    async def test_fetch_rule_not_found_raises_error(self, manager):
        """Test _fetch_rule() raises ValueError when rule not found."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=None)
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        with pytest.raises(ValueError, match="not found or disabled"):
            await manager._fetch_rule("nonexistent_rule")

    @pytest.mark.asyncio
    async def test_fetch_rule_empty_channels_list(self, manager):
        """Test _fetch_rule() handles empty channels list."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(
            return_value={
                "name": "No Channels Rule",
                "channels": [],
            }
        )
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        result = await manager._fetch_rule("rule_empty")

        assert result.name == "No Channels Rule"
        assert len(result.channels) == 0

    @pytest.mark.asyncio
    async def test_fetch_rule_null_channels(self, manager):
        """Test _fetch_rule() handles null channels field."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(
            return_value={
                "name": "Null Channels Rule",
                "channels": None,
            }
        )
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        result = await manager._fetch_rule("rule_null")

        assert result.name == "Null Channels Rule"
        assert len(result.channels) == 0


class TestCreateDeliveries:
    """Tests for _create_deliveries() method."""

    @pytest.fixture
    def manager(self):
        """Create AlertManager with mocked dependencies."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()

        with patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret", return_value="test_secret"):
            with patch("libs.platform.alerts.alert_manager.Queue"):
                with patch("libs.platform.alerts.alert_manager.QueueDepthManager"):
                    manager = AlertManager(
                        db_pool=mock_db_pool,
                        redis_client=mock_redis_async,
                        delivery_job_func=mock_delivery_func,
                    )
        return manager

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.mask_recipient")
    @patch("libs.platform.alerts.alert_manager.compute_dedup_key")
    async def test_create_deliveries_success(self, mock_dedup, mock_mask, manager):
        """Test _create_deliveries() creates delivery rows with masked PII."""
        mock_dedup.return_value = "dedup_key_123"
        mock_mask.return_value = "test***@example.com"

        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value={"id": "delivery123"})
        mock_cur.execute = AsyncMock()

        event_id = str(uuid4())
        rule_id = str(uuid4())
        alert_event = AlertEvent(
            id=event_id,
            rule_id=rule_id,
            triggered_at=datetime.now(UTC),
            trigger_value=Decimal("100.5"),
            acknowledged_at=None,
            acknowledged_by=None,
            acknowledged_note=None,
            routed_channels=["email"],
            created_at=datetime.now(UTC),
        )
        channels = [ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com")]

        result = await manager._create_deliveries(
            cur=mock_cur,
            alert_event=alert_event,
            rule_channels=channels,
            triggered_at=alert_event.triggered_at,
        )

        assert len(result) == 1
        assert result[0][0] == "delivery123"
        assert result[0][1].recipient == "test@example.com"
        mock_dedup.assert_called_once_with(
            rule_id,
            "email",
            "test@example.com",
            alert_event.triggered_at,
            "test_secret",
        )
        mock_mask.assert_called_once_with("test@example.com", "email")

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.mask_recipient")
    @patch("libs.platform.alerts.alert_manager.compute_dedup_key")
    async def test_create_deliveries_deduplication_conflict(self, mock_dedup, mock_mask, manager):
        """Test _create_deliveries() skips duplicate deliveries (ON CONFLICT DO NOTHING)."""
        mock_dedup.return_value = "dedup_key_existing"
        mock_mask.return_value = "test***@example.com"

        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=None)  # Conflict, no row returned
        mock_cur.execute = AsyncMock()

        event_id = str(uuid4())
        rule_id = str(uuid4())
        alert_event = AlertEvent(
            id=event_id,
            rule_id=rule_id,
            triggered_at=datetime.now(UTC),
            trigger_value=None,
            acknowledged_at=None,
            acknowledged_by=None,
            acknowledged_note=None,
            routed_channels=["slack"],
            created_at=datetime.now(UTC),
        )
        channels = [ChannelConfig(type=ChannelType.SLACK, recipient="#alerts")]

        result = await manager._create_deliveries(
            cur=mock_cur,
            alert_event=alert_event,
            rule_channels=channels,
            triggered_at=alert_event.triggered_at,
        )

        # No deliveries returned because of conflict
        assert len(result) == 0

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.mask_recipient")
    @patch("libs.platform.alerts.alert_manager.compute_dedup_key")
    async def test_create_deliveries_multiple_channels(self, mock_dedup, mock_mask, manager):
        """Test _create_deliveries() creates multiple deliveries for multiple channels."""
        mock_dedup.side_effect = ["dedup1", "dedup2", "dedup3"]
        mock_mask.side_effect = ["email***", "slack***", "sms***"]

        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(side_effect=[
            {"id": "delivery1"},
            {"id": "delivery2"},
            None,  # Third channel conflicts (already delivered)
        ])
        mock_cur.execute = AsyncMock()

        event_id = str(uuid4())
        rule_id = str(uuid4())
        alert_event = AlertEvent(
            id=event_id,
            rule_id=rule_id,
            triggered_at=datetime.now(UTC),
            trigger_value=Decimal("50.0"),
            acknowledged_at=None,
            acknowledged_by=None,
            acknowledged_note=None,
            routed_channels=["email", "slack", "sms"],
            created_at=datetime.now(UTC),
        )
        channels = [
            ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com"),
            ChannelConfig(type=ChannelType.SLACK, recipient="#alerts"),
            ChannelConfig(type=ChannelType.SMS, recipient="+1234567890"),
        ]

        result = await manager._create_deliveries(
            cur=mock_cur,
            alert_event=alert_event,
            rule_channels=channels,
            triggered_at=alert_event.triggered_at,
        )

        # Only 2 deliveries (third conflicted)
        assert len(result) == 2
        assert result[0][0] == "delivery1"
        assert result[1][0] == "delivery2"


class TestMarkDeliveryFailed:
    """Tests for _mark_delivery_failed() method."""

    @pytest.fixture
    def manager(self):
        """Create AlertManager with mocked dependencies."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()

        with patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret", return_value="secret"):
            with patch("libs.platform.alerts.alert_manager.Queue"):
                with patch("libs.platform.alerts.alert_manager.QueueDepthManager"):
                    manager = AlertManager(
                        db_pool=mock_db_pool,
                        redis_client=mock_redis_async,
                        delivery_job_func=mock_delivery_func,
                    )
        return manager

    @pytest.mark.asyncio
    async def test_mark_delivery_failed_updates_status(self, manager):
        """Test _mark_delivery_failed() updates delivery status and error message."""
        mock_conn = AsyncMock()
        mock_cur = AsyncMock()
        mock_cur.execute = AsyncMock()
        mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_cur.__aexit__ = AsyncMock()
        mock_conn.cursor = Mock(return_value=mock_cur)
        mock_conn.commit = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        manager.db_pool.connection = Mock(return_value=mock_conn)

        await manager._mark_delivery_failed("delivery123", "Enqueue failed: Connection timeout")

        mock_cur.execute.assert_called_once()
        call_args = mock_cur.execute.call_args[0]
        assert "UPDATE alert_deliveries" in call_args[0]
        assert call_args[1][0] == "failed"  # DeliveryStatus.FAILED.value
        assert call_args[1][1] == "Enqueue failed: Connection timeout"
        assert call_args[1][2] == "delivery123"
        mock_conn.commit.assert_called_once()


class TestEnqueueDeliveries:
    """Tests for _enqueue_deliveries() method."""

    @pytest.fixture
    def manager(self):
        """Create AlertManager with mocked dependencies."""
        mock_db_pool = Mock()
        mock_redis_async = Mock()
        mock_redis_async.connection_pool.connection_kwargs = {"host": "localhost", "port": 6379, "db": 0}
        mock_delivery_func = Mock()
        mock_rq_queue = Mock()
        mock_depth_manager = AsyncMock()
        mock_depth_manager.increment = AsyncMock()
        mock_depth_manager.decrement = AsyncMock()

        with patch("libs.platform.alerts.alert_manager.get_recipient_hash_secret", return_value="secret"):
            manager = AlertManager(
                db_pool=mock_db_pool,
                redis_client=mock_redis_async,
                delivery_job_func=mock_delivery_func,
                rq_queue=mock_rq_queue,
                queue_depth_manager=mock_depth_manager,
            )
        return manager

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.asyncio.to_thread")
    async def test_enqueue_deliveries_success_single_channel(self, mock_to_thread, manager):
        """Test _enqueue_deliveries() successfully enqueues single delivery."""
        mock_to_thread.return_value = None  # enqueue succeeds

        deliveries = [
            ("delivery123", ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com"))
        ]

        await manager._enqueue_deliveries(
            deliveries=deliveries,
            rule_name="Test Alert",
            trigger_value=Decimal("100.5"),
            triggered_at=datetime.now(UTC),
        )

        manager.queue_depth_manager.increment.assert_called_once()
        mock_to_thread.assert_called_once()
        # Verify enqueue called with correct args
        call_args = mock_to_thread.call_args[0]
        assert call_args[0] == manager.queue.enqueue
        assert call_args[1] == manager._delivery_job_func
        assert call_args[2] == "delivery123"
        assert call_args[3] == "email"
        assert call_args[4] == "test@example.com"
        assert "Test Alert" in call_args[5]  # subject

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.alert_dropped_total")
    @patch("libs.platform.alerts.alert_manager.asyncio.to_thread")
    async def test_enqueue_deliveries_enqueue_failure(self, mock_to_thread, mock_dropped_metric, manager):
        """Test _enqueue_deliveries() handles enqueue failures gracefully."""
        mock_to_thread.side_effect = Exception("RQ connection failed")
        manager._mark_delivery_failed = AsyncMock()

        deliveries = [
            ("delivery456", ChannelConfig(type=ChannelType.SLACK, recipient="#alerts"))
        ]

        await manager._enqueue_deliveries(
            deliveries=deliveries,
            rule_name="Critical Alert",
            trigger_value=None,
            triggered_at=datetime.now(UTC),
        )

        # Verify decrement called on failure
        manager.queue_depth_manager.decrement.assert_called_once()
        # Verify metric incremented
        mock_dropped_metric.labels.assert_called_once_with(channel="slack", reason="enqueue_failed")
        mock_dropped_metric.labels.return_value.inc.assert_called_once()
        # Verify delivery marked as failed
        manager._mark_delivery_failed.assert_called_once_with(
            "delivery456", ANY  # Error message includes exception details
        )

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.asyncio.to_thread")
    async def test_enqueue_deliveries_multiple_channels_mixed_results(self, mock_to_thread, manager):
        """Test _enqueue_deliveries() handles mixed success/failure results."""
        # First succeeds, second fails, third succeeds
        mock_to_thread.side_effect = [
            None,  # Success
            Exception("Network error"),  # Failure
            None,  # Success
        ]
        manager._mark_delivery_failed = AsyncMock()

        deliveries = [
            ("delivery1", ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com")),
            ("delivery2", ChannelConfig(type=ChannelType.SLACK, recipient="#alerts")),
            ("delivery3", ChannelConfig(type=ChannelType.SMS, recipient="+1234567890")),
        ]

        await manager._enqueue_deliveries(
            deliveries=deliveries,
            rule_name="Multi-Channel Alert",
            trigger_value=Decimal("75.0"),
            triggered_at=datetime.now(UTC),
        )

        # Verify increment called 3 times (once per delivery)
        assert manager.queue_depth_manager.increment.call_count == 3
        # Verify decrement called once (for failure)
        manager.queue_depth_manager.decrement.assert_called_once()
        # Verify only failed delivery marked
        manager._mark_delivery_failed.assert_called_once_with("delivery2", ANY)

    @pytest.mark.asyncio
    async def test_enqueue_deliveries_empty_list(self, manager):
        """Test _enqueue_deliveries() handles empty deliveries list."""
        await manager._enqueue_deliveries(
            deliveries=[],
            rule_name="No Deliveries Alert",
            trigger_value=Decimal("10.0"),
            triggered_at=datetime.now(UTC),
        )

        # Should not call increment or enqueue
        manager.queue_depth_manager.increment.assert_not_called()

    @pytest.mark.asyncio
    @patch("libs.platform.alerts.alert_manager.asyncio.to_thread")
    async def test_enqueue_deliveries_null_trigger_value(self, mock_to_thread, manager):
        """Test _enqueue_deliveries() handles None trigger_value in message body."""
        mock_to_thread.return_value = None

        deliveries = [
            ("delivery789", ChannelConfig(type=ChannelType.EMAIL, recipient="test@example.com"))
        ]

        await manager._enqueue_deliveries(
            deliveries=deliveries,
            rule_name="Null Value Alert",
            trigger_value=None,  # No trigger value
            triggered_at=datetime.now(UTC),
        )

        # Verify body contains "value=N/A"
        call_args = mock_to_thread.call_args[0]
        body = call_args[6]  # 7th argument is body
        assert "value=N/A" in body
