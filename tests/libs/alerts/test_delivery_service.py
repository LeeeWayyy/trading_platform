"""Tests for alert delivery service and queue depth manager."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import psycopg
import pytest
import redis.exceptions

from libs.alerts.delivery_service import (
    DeliveryExecutor,
    QueueDepthManager,
    QueueFullError,
    is_transient_error,
)
from libs.alerts.models import AlertDelivery, ChannelType, DeliveryResult, DeliveryStatus


class TestQueueFullError:
    """Test QueueFullError exception."""

    def test_default_retry_after(self):
        """Test default retry_after value."""
        error = QueueFullError()
        assert error.retry_after == 60

    def test_custom_retry_after(self):
        """Test custom retry_after value."""
        error = QueueFullError(retry_after=120)
        assert error.retry_after == 120

    def test_error_message(self):
        """Test error message format."""
        error = QueueFullError(retry_after=30)
        assert "30s" in str(error)


class TestQueueDepthManager:
    """Test QueueDepthManager."""

    @pytest.fixture()
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=1)
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(return_value=True)
        redis.eval = AsyncMock(return_value=0)
        return redis

    @pytest.fixture()
    def queue_depth_manager(self, mock_redis):
        """Create QueueDepthManager with mock Redis."""
        return QueueDepthManager(mock_redis)

    @pytest.mark.asyncio()
    async def test_increment(self, queue_depth_manager, mock_redis):
        """Test incrementing queue depth."""
        mock_redis.incr = AsyncMock(return_value=5)

        result = await queue_depth_manager.increment()

        assert result == 5
        mock_redis.incr.assert_called_once()

    @pytest.mark.asyncio()
    async def test_decrement(self, queue_depth_manager, mock_redis):
        """Test decrementing queue depth."""
        mock_redis.eval = AsyncMock(return_value=3)

        result = await queue_depth_manager.decrement()

        assert result == 3

    @pytest.mark.asyncio()
    async def test_decrement_clamps_at_zero(self, queue_depth_manager, mock_redis):
        """Test decrement doesn't go below zero."""
        mock_redis.eval = AsyncMock(return_value=0)

        result = await queue_depth_manager.decrement()

        assert result == 0

    @pytest.mark.asyncio()
    async def test_get_depth_empty(self, queue_depth_manager, mock_redis):
        """Test getting depth when empty."""
        mock_redis.get = AsyncMock(return_value=None)

        result = await queue_depth_manager.get_depth()

        assert result == 0

    @pytest.mark.asyncio()
    async def test_get_depth_with_value(self, queue_depth_manager, mock_redis):
        """Test getting depth with value."""
        mock_redis.get = AsyncMock(return_value=b"42")

        result = await queue_depth_manager.get_depth()

        assert result == 42

    @pytest.mark.asyncio()
    async def test_is_accepting_when_below_max(self, queue_depth_manager, mock_redis):
        """Test is_accepting returns True when below max."""
        mock_redis.get = AsyncMock(return_value=b"5000")

        result = await queue_depth_manager.is_accepting()

        assert result is True

    @pytest.mark.asyncio()
    async def test_is_accepting_stops_at_max(self, queue_depth_manager, mock_redis):
        """Test is_accepting returns False at max."""
        mock_redis.get = AsyncMock(return_value=b"10000")

        result = await queue_depth_manager.is_accepting()

        assert result is False

    @pytest.mark.asyncio()
    async def test_hysteresis_resume_threshold(self, queue_depth_manager, mock_redis):
        """Test hysteresis: must drop below resume threshold."""
        # First, hit max to set _accepting to False
        mock_redis.get = AsyncMock(return_value=b"10000")
        await queue_depth_manager.is_accepting()
        assert queue_depth_manager._accepting is False

        # Drop to 9000 - still above resume threshold (8000)
        mock_redis.get = AsyncMock(return_value=b"9000")
        result = await queue_depth_manager.is_accepting()
        assert result is False

        # Drop below resume threshold
        mock_redis.get = AsyncMock(return_value=b"7999")
        result = await queue_depth_manager.is_accepting()
        assert result is True


class TestIsTransientError:
    """Test is_transient_error helper."""

    def test_success_is_not_transient(self):
        """Test success result is not transient."""
        result = DeliveryResult(success=True)
        assert is_transient_error(result) is False

    def test_retryable_failure_is_transient(self):
        """Test retryable failure is transient."""
        result = DeliveryResult(success=False, error="timeout", retryable=True)
        assert is_transient_error(result) is True

    def test_non_retryable_failure_is_not_transient(self):
        """Test non-retryable failure is not transient."""
        result = DeliveryResult(success=False, error="invalid", retryable=False)
        assert is_transient_error(result) is False


class TestDeliveryExecutor:
    """Test DeliveryExecutor."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

        # Setup context managers
        pool.connection = MagicMock(return_value=conn)
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor)
        conn.commit = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)

        return pool

    @pytest.fixture()
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=1)
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(return_value=True)
        redis.eval = AsyncMock(return_value=0)
        return redis

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.fixture()
    def mock_channel(self):
        """Create mock channel handler."""
        channel = AsyncMock()
        channel.send = AsyncMock(return_value=DeliveryResult(success=True, message_id="msg-123"))
        return channel

    def test_retry_delays(self):
        """Test RETRY_DELAYS constant."""
        assert DeliveryExecutor.RETRY_DELAYS == [5, 30]

    def test_max_attempts(self):
        """Test MAX_ATTEMPTS constant."""
        assert DeliveryExecutor.MAX_ATTEMPTS == 3

    def test_stuck_task_threshold(self):
        """Test STUCK_TASK_THRESHOLD_MINUTES constant."""
        assert DeliveryExecutor.STUCK_TASK_THRESHOLD_MINUTES == 15

    @pytest.mark.asyncio()
    async def test_execute_decrements_when_unclaimed(
        self, mock_db_pool, mock_redis, mock_poison_queue, mock_channel
    ):
        """Release reserved queue slot even when delivery was not claimed."""
        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: mock_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=None)  # type: ignore[method-assign]
        executor._record_attempt = AsyncMock()  # type: ignore[method-assign]
        executor._record_attempt_failure = AsyncMock()  # type: ignore[method-assign]

        result = await executor.execute(
            delivery_id="unclaimed-id",
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            subject="Test",
            body="Body",
        )

        assert result.success is True
        executor.queue_depth_manager.decrement.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_execute_decrements_on_claimed_delivery(
        self, mock_db_pool, mock_redis, mock_poison_queue, mock_channel
    ):
        """Decrement queue depth only when claim succeeds."""
        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: mock_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._record_attempt = AsyncMock()  # type: ignore[method-assign]
        executor._record_attempt_failure = AsyncMock()  # type: ignore[method-assign]

        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="dedup:slack:hash",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)  # type: ignore[method-assign]

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            subject="Test",
            body="Body",
        )

        assert result.success is True
        executor.queue_depth_manager.decrement.assert_awaited_once()


class TestDeliveryExecutorExceptionHandling:
    """Test DeliveryExecutor exception handling improvements."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

        # Setup context managers
        pool.connection = MagicMock(return_value=conn)
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor)
        conn.commit = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)

        return pool

    @pytest.fixture()
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=1)
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(return_value=True)
        redis.eval = AsyncMock(return_value=0)
        return redis

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.fixture()
    def mock_channel(self):
        """Create mock channel handler."""
        channel = AsyncMock()
        channel.send = AsyncMock(return_value=DeliveryResult(success=True, message_id="msg-123"))
        return channel

    @pytest.mark.asyncio()
    async def test_claim_delivery_db_error_logs_and_decrements(
        self, mock_db_pool, mock_redis, mock_poison_queue, mock_channel
    ):
        """Test DB error during claim logs warning and decrements queue depth."""
        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: mock_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()

        # Mock claim to raise psycopg.Error
        db_error = psycopg.OperationalError("DB connection failed")
        executor._claim_delivery = AsyncMock(side_effect=db_error)  # type: ignore[method-assign]

        with pytest.raises(psycopg.OperationalError):
            await executor.execute(
                delivery_id="test-id",
                channel=ChannelType.SLACK,
                recipient="https://hooks.slack.com/services/T000/B000/XXXX",
                subject="Test",
                body="Body",
            )

        # Verify queue depth was decremented
        executor.queue_depth_manager.decrement.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_claim_delivery_redis_error_logs_and_decrements(
        self, mock_db_pool, mock_redis, mock_poison_queue, mock_channel
    ):
        """Test Redis error during claim logs warning and decrements queue depth."""
        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: mock_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()

        # Mock claim to raise redis error
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        executor._claim_delivery = AsyncMock(side_effect=redis_error)  # type: ignore[method-assign]

        with pytest.raises(redis.exceptions.ConnectionError):
            await executor.execute(
                delivery_id="test-id",
                channel=ChannelType.SLACK,
                recipient="https://hooks.slack.com/services/T000/B000/XXXX",
                subject="Test",
                body="Body",
            )

        # Verify queue depth was decremented
        executor.queue_depth_manager.decrement.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_rate_limit_check_redis_error_continues_with_fallback(
        self, mock_db_pool, mock_redis, mock_poison_queue, mock_channel
    ):
        """Test Redis error during rate limit check logs warning and continues with fallback."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="dedup:slack:hash",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: mock_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)  # type: ignore[method-assign]
        executor._record_attempt = AsyncMock()  # type: ignore[method-assign]

        # Mock rate limit check to raise redis error
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        executor._check_rate_limits = AsyncMock(side_effect=redis_error)  # type: ignore[method-assign]

        # Should not raise - fallback to retryable error
        # After MAX_RATE_LIMIT_WAITS (3), it will exhaust and return error
        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            subject="Test",
            body="Body",
        )

        # Verify it exhausted rate limit waits (no retry scheduler available)
        assert result.success is False
        assert result.error == "rate_limit_wait_exhausted"
        assert result.retryable is False

    @pytest.mark.asyncio()
    async def test_retry_scheduler_redis_error_logs_and_propagates(
        self, mock_db_pool, mock_redis, mock_poison_queue, mock_channel
    ):
        """Test Redis error in retry scheduler logs warning and propagates."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="dedup:slack:hash",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        # Create failing retry scheduler
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        retry_scheduler = AsyncMock(side_effect=redis_error)

        # Channel fails with retryable error and long retry delay
        channel = AsyncMock()
        channel.send = AsyncMock(
            return_value=DeliveryResult(
                success=False, error="timeout", retryable=True, metadata={"retry_after": "10"}
            )
        )

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            retry_scheduler=retry_scheduler,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)  # type: ignore[method-assign]
        executor._record_attempt = AsyncMock()  # type: ignore[method-assign]
        executor._check_rate_limits = AsyncMock(return_value=None)  # type: ignore[method-assign]

        # Should propagate the redis error
        with pytest.raises(redis.exceptions.ConnectionError):
            await executor.execute(
                delivery_id=str(delivery_id),
                channel=ChannelType.SLACK,
                recipient="https://hooks.slack.com/services/T000/B000/XXXX",
                subject="Test",
                body="Body",
            )
