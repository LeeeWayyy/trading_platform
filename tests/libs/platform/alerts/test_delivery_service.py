"""Tests for alert delivery service and queue depth manager."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import psycopg
import pytest
import redis.exceptions

from libs.platform.alerts.delivery_service import (
    DeliveryExecutor,
    QueueDepthManager,
    QueueFullError,
    is_transient_error,
)
from libs.platform.alerts.models import AlertDelivery, ChannelType, DeliveryResult, DeliveryStatus


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


class TestQueueDepthManagerSyncFromDb:
    """Test QueueDepthManager.sync_depth_from_db method."""

    @pytest.fixture()
    def mock_redis(self):
        """Create mock Redis client."""
        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=1)
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

        pool.connection = MagicMock(return_value=conn)
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor)
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(42,))

        return pool

    @pytest.mark.asyncio()
    async def test_sync_depth_from_db(self, mock_redis, mock_db_pool):
        """Test syncing queue depth from database."""
        manager = QueueDepthManager(mock_redis)

        result = await manager.sync_depth_from_db(mock_db_pool)

        assert result == 42
        mock_redis.set.assert_called_once_with(QueueDepthManager.REDIS_KEY, 42)

    @pytest.mark.asyncio()
    async def test_sync_depth_from_db_empty(self, mock_redis, mock_db_pool):
        """Test syncing queue depth when no records found."""
        conn = AsyncMock()
        cursor = AsyncMock()
        mock_db_pool.connection = MagicMock(return_value=conn)
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor)
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)

        manager = QueueDepthManager(mock_redis)

        result = await manager.sync_depth_from_db(mock_db_pool)

        assert result == 0
        mock_redis.set.assert_called_once_with(QueueDepthManager.REDIS_KEY, 0)


class TestDeliveryExecutorDelayMethods:
    """Test DeliveryExecutor delay-related methods."""

    @pytest.fixture()
    def executor(self):
        """Create minimal executor for testing helper methods."""
        mock_redis = AsyncMock()
        mock_db_pool = AsyncMock()
        mock_poison_queue = AsyncMock()
        return DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

    def test_delay_for_attempt_zero(self, executor):
        """Test delay for attempt 0 returns 0."""
        assert executor._delay_for_attempt(0) == 0

    def test_delay_for_attempt_negative(self, executor):
        """Test delay for negative attempt returns 0."""
        assert executor._delay_for_attempt(-1) == 0

    def test_delay_for_attempt_one(self, executor):
        """Test delay for attempt 1."""
        assert executor._delay_for_attempt(1) == 5

    def test_delay_for_attempt_two(self, executor):
        """Test delay for attempt 2."""
        assert executor._delay_for_attempt(2) == 30

    def test_delay_for_attempt_beyond_list(self, executor):
        """Test delay for attempt beyond list length uses last value."""
        assert executor._delay_for_attempt(10) == 30

    def test_should_reenqueue_no_retry_after(self, executor):
        """Test _should_reenqueue with no retry_after."""
        result = DeliveryResult(success=False, error="error")
        should, delay = executor._should_reenqueue(result)
        assert should is False
        assert delay == 0

    def test_should_reenqueue_short_retry_after(self, executor):
        """Test _should_reenqueue with short retry_after (below threshold)."""
        result = DeliveryResult(success=False, error="error", metadata={"retry_after": "3"})
        should, delay = executor._should_reenqueue(result)
        assert should is False
        assert delay == 0

    def test_should_reenqueue_long_retry_after(self, executor):
        """Test _should_reenqueue with long retry_after (above threshold)."""
        result = DeliveryResult(success=False, error="error", metadata={"retry_after": "10"})
        should, delay = executor._should_reenqueue(result)
        assert should is True
        assert delay == 10

    def test_should_reenqueue_invalid_retry_after(self, executor):
        """Test _should_reenqueue with invalid retry_after value."""
        result = DeliveryResult(success=False, error="error", metadata={"retry_after": "invalid"})
        should, delay = executor._should_reenqueue(result)
        assert should is False
        assert delay == 0

    def test_rate_limit_delay_no_metadata(self, executor):
        """Test _rate_limit_delay with no metadata returns default."""
        result = DeliveryResult(success=False, error="rate_limited")
        assert executor._rate_limit_delay(result) == executor.RATE_LIMIT_RETRY_DELAY

    def test_rate_limit_delay_no_retry_after(self, executor):
        """Test _rate_limit_delay with metadata but no retry_after."""
        result = DeliveryResult(success=False, error="rate_limited", metadata={"other": "value"})
        assert executor._rate_limit_delay(result) == executor.RATE_LIMIT_RETRY_DELAY

    def test_rate_limit_delay_valid_retry_after(self, executor):
        """Test _rate_limit_delay with valid retry_after."""
        result = DeliveryResult(success=False, error="rate_limited", metadata={"retry_after": "120"})
        assert executor._rate_limit_delay(result) == 120

    def test_rate_limit_delay_invalid_retry_after(self, executor):
        """Test _rate_limit_delay with invalid retry_after."""
        result = DeliveryResult(success=False, error="rate_limited", metadata={"retry_after": "bad"})
        assert executor._rate_limit_delay(result) == executor.RATE_LIMIT_RETRY_DELAY

    def test_rate_limit_delay_zero_retry_after(self, executor):
        """Test _rate_limit_delay with zero retry_after returns default."""
        result = DeliveryResult(success=False, error="rate_limited", metadata={"retry_after": "0"})
        assert executor._rate_limit_delay(result) == executor.RATE_LIMIT_RETRY_DELAY

    def test_rate_limit_delay_negative_retry_after(self, executor):
        """Test _rate_limit_delay with negative retry_after returns default."""
        result = DeliveryResult(success=False, error="rate_limited", metadata={"retry_after": "-5"})
        assert executor._rate_limit_delay(result) == executor.RATE_LIMIT_RETRY_DELAY


class TestDeliveryExecutorGetChannel:
    """Test DeliveryExecutor._get_channel method."""

    @pytest.fixture()
    def mock_channel(self):
        """Create mock channel handler."""
        channel = AsyncMock()
        channel.send = AsyncMock(return_value=DeliveryResult(success=True))
        return channel

    def test_get_channel_exists(self, mock_channel):
        """Test getting an existing channel."""
        mock_redis = AsyncMock()
        mock_db_pool = AsyncMock()
        mock_poison_queue = AsyncMock()

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: mock_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        result = executor._get_channel(ChannelType.SLACK)
        assert result == mock_channel

    def test_get_channel_not_exists(self):
        """Test getting a non-existent channel raises ValueError."""
        mock_redis = AsyncMock()
        mock_db_pool = AsyncMock()
        mock_poison_queue = AsyncMock()

        executor = DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        with pytest.raises(ValueError, match="No channel handler configured"):
            executor._get_channel(ChannelType.SLACK)


class TestDeliveryExecutorExtractRecipientHash:
    """Test DeliveryExecutor._extract_recipient_hash method."""

    @pytest.fixture()
    def executor(self):
        """Create executor for testing."""
        mock_redis = AsyncMock()
        mock_db_pool = AsyncMock()
        mock_poison_queue = AsyncMock()
        return DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

    def test_extract_recipient_hash_valid_dedup_key(self, executor):
        """Test extracting recipient hash from valid dedup_key."""
        delivery = AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="masked",
            dedup_key="rule123:slack:abc123hash:2024010112",
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        result = executor._extract_recipient_hash(delivery, "slack")
        assert result == "abc123hash"

    def test_extract_recipient_hash_short_dedup_key(self, executor):
        """Test extracting recipient hash from short dedup_key returns None."""
        delivery = AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="masked",
            dedup_key="rule123:slack",
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        result = executor._extract_recipient_hash(delivery, "slack")
        assert result is None

    def test_extract_recipient_hash_empty_dedup_key(self, executor):
        """Test extracting recipient hash when dedup_key is empty."""
        delivery = AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="masked",
            dedup_key="",
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        result = executor._extract_recipient_hash(delivery, "slack")
        assert result is None


class TestDeliveryExecutorRateLimits:
    """Test DeliveryExecutor._check_rate_limits method."""

    @pytest.fixture()
    def mock_rate_limiter(self):
        """Create mock rate limiter."""
        limiter = AsyncMock()
        limiter.check_channel_rate_limit = AsyncMock(return_value=True)
        limiter.check_recipient_rate_limit = AsyncMock(return_value=True)
        limiter.check_global_rate_limit = AsyncMock(return_value=True)
        return limiter

    @pytest.fixture()
    def executor_with_rate_limiter(self, mock_rate_limiter):
        """Create executor with rate limiter."""
        mock_redis = AsyncMock()
        mock_db_pool = AsyncMock()
        mock_poison_queue = AsyncMock()
        return DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

    @pytest.fixture()
    def sample_delivery(self):
        """Create sample delivery for testing."""
        return AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="masked",
            dedup_key="rule123:slack:abc123hash:2024010112",
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=datetime.now(UTC),
        )

    @pytest.mark.asyncio()
    async def test_check_rate_limits_no_limiter(self, sample_delivery):
        """Test rate limit check returns None when no rate limiter configured."""
        mock_redis = AsyncMock()
        mock_db_pool = AsyncMock()
        mock_poison_queue = AsyncMock()
        executor = DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=None,
        )

        result = await executor._check_rate_limits(sample_delivery, ChannelType.SLACK)
        assert result is None

    @pytest.mark.asyncio()
    async def test_check_rate_limits_all_pass(
        self, executor_with_rate_limiter, sample_delivery, mock_rate_limiter
    ):
        """Test rate limit check when all limits pass."""
        result = await executor_with_rate_limiter._check_rate_limits(
            sample_delivery, ChannelType.SLACK
        )
        assert result is None

    @pytest.mark.asyncio()
    async def test_check_rate_limits_channel_limited(
        self, executor_with_rate_limiter, sample_delivery, mock_rate_limiter
    ):
        """Test rate limit check when channel is limited."""
        mock_rate_limiter.check_channel_rate_limit = AsyncMock(return_value=False)

        result = await executor_with_rate_limiter._check_rate_limits(
            sample_delivery, ChannelType.SLACK
        )

        assert result is not None
        assert result.success is False
        assert result.error == "channel_rate_limited"
        assert result.metadata["limit"] == "channel"

    @pytest.mark.asyncio()
    async def test_check_rate_limits_recipient_limited(
        self, executor_with_rate_limiter, sample_delivery, mock_rate_limiter
    ):
        """Test rate limit check when recipient is limited."""
        mock_rate_limiter.check_recipient_rate_limit = AsyncMock(return_value=False)

        result = await executor_with_rate_limiter._check_rate_limits(
            sample_delivery, ChannelType.SLACK
        )

        assert result is not None
        assert result.success is False
        assert result.error == "recipient_rate_limited"
        assert result.metadata["limit"] == "recipient"

    @pytest.mark.asyncio()
    async def test_check_rate_limits_global_limited(
        self, executor_with_rate_limiter, sample_delivery, mock_rate_limiter
    ):
        """Test rate limit check when global limit is hit."""
        mock_rate_limiter.check_global_rate_limit = AsyncMock(return_value=False)

        result = await executor_with_rate_limiter._check_rate_limits(
            sample_delivery, ChannelType.SLACK
        )

        assert result is not None
        assert result.success is False
        assert result.error == "global_rate_limited"
        assert result.metadata["limit"] == "global"

    @pytest.mark.asyncio()
    async def test_check_rate_limits_no_recipient_hash(
        self, executor_with_rate_limiter, mock_rate_limiter
    ):
        """Test rate limit check skips recipient check when no hash extractable."""
        # Use a short dedup_key that won't have 3 parts
        delivery = AlertDelivery(
            id=uuid4(),
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="masked",
            dedup_key="rule:slack",  # Only 2 parts, no recipient hash
            status=DeliveryStatus.PENDING,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        result = await executor_with_rate_limiter._check_rate_limits(delivery, ChannelType.SLACK)
        assert result is None
        mock_rate_limiter.check_recipient_rate_limit.assert_not_called()


class TestDeliveryExecutorClaimDelivery:
    """Test DeliveryExecutor._claim_delivery method."""

    @pytest.fixture()
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_claim_delivery_success(self, mock_redis, mock_poison_queue):
        """Test successful delivery claim."""
        delivery_id = uuid4()
        alert_id = uuid4()
        now = datetime.now(UTC)

        # Setup mock cursor to return a delivery row
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(
            return_value={
                "id": delivery_id,
                "alert_id": alert_id,
                "channel": "slack",
                "recipient": "https://hooks.slack.com/test",
                "dedup_key": "test:slack:hash:123",
                "status": "in_progress",
                "attempts": 0,
                "last_attempt_at": now,
                "delivered_at": None,
                "poison_at": None,
                "error_message": None,
                "created_at": now,
            }
        )
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)

        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=cursor)
        conn.commit = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.connection = MagicMock(return_value=conn)

        executor = DeliveryExecutor(
            channels={},
            db_pool=pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        result = await executor._claim_delivery(str(delivery_id))

        assert result is not None
        assert result.id == delivery_id
        assert result.channel == ChannelType.SLACK

    @pytest.mark.asyncio()
    async def test_claim_delivery_already_delivered(self, mock_redis, mock_poison_queue):
        """Test claim when delivery already completed."""
        delivery_id = uuid4()

        # First cursor for UPDATE RETURNING returns None
        cursor1 = AsyncMock()
        cursor1.execute = AsyncMock()
        cursor1.fetchone = AsyncMock(return_value=None)
        cursor1.__aenter__ = AsyncMock(return_value=cursor1)
        cursor1.__aexit__ = AsyncMock(return_value=None)

        # Second cursor for status check returns DELIVERED
        cursor2 = AsyncMock()
        cursor2.execute = AsyncMock()
        cursor2.fetchone = AsyncMock(return_value={"id": delivery_id, "status": "delivered"})
        cursor2.__aenter__ = AsyncMock(return_value=cursor2)
        cursor2.__aexit__ = AsyncMock(return_value=None)

        cursor_calls = [cursor1, cursor2]
        cursor_index = [0]

        def get_cursor(*args, **kwargs):
            idx = cursor_index[0]
            cursor_index[0] += 1
            return cursor_calls[idx]

        conn = AsyncMock()
        conn.cursor = MagicMock(side_effect=get_cursor)
        conn.commit = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.connection = MagicMock(return_value=conn)

        executor = DeliveryExecutor(
            channels={},
            db_pool=pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        result = await executor._claim_delivery(str(delivery_id))

        assert result is None

    @pytest.mark.asyncio()
    async def test_claim_delivery_already_in_progress(self, mock_redis, mock_poison_queue):
        """Test claim when delivery already in progress by another worker."""
        delivery_id = uuid4()

        cursor1 = AsyncMock()
        cursor1.execute = AsyncMock()
        cursor1.fetchone = AsyncMock(return_value=None)
        cursor1.__aenter__ = AsyncMock(return_value=cursor1)
        cursor1.__aexit__ = AsyncMock(return_value=None)

        cursor2 = AsyncMock()
        cursor2.execute = AsyncMock()
        cursor2.fetchone = AsyncMock(return_value={"id": delivery_id, "status": "in_progress"})
        cursor2.__aenter__ = AsyncMock(return_value=cursor2)
        cursor2.__aexit__ = AsyncMock(return_value=None)

        cursor_calls = [cursor1, cursor2]
        cursor_index = [0]

        def get_cursor(*args, **kwargs):
            idx = cursor_index[0]
            cursor_index[0] += 1
            return cursor_calls[idx]

        conn = AsyncMock()
        conn.cursor = MagicMock(side_effect=get_cursor)
        conn.commit = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.connection = MagicMock(return_value=conn)

        executor = DeliveryExecutor(
            channels={},
            db_pool=pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        result = await executor._claim_delivery(str(delivery_id))
        assert result is None

    @pytest.mark.asyncio()
    async def test_claim_delivery_terminal_state(self, mock_redis, mock_poison_queue):
        """Test claim when delivery is in terminal state (failed)."""
        delivery_id = uuid4()

        cursor1 = AsyncMock()
        cursor1.execute = AsyncMock()
        cursor1.fetchone = AsyncMock(return_value=None)
        cursor1.__aenter__ = AsyncMock(return_value=cursor1)
        cursor1.__aexit__ = AsyncMock(return_value=None)

        cursor2 = AsyncMock()
        cursor2.execute = AsyncMock()
        cursor2.fetchone = AsyncMock(return_value={"id": delivery_id, "status": "failed"})
        cursor2.__aenter__ = AsyncMock(return_value=cursor2)
        cursor2.__aexit__ = AsyncMock(return_value=None)

        cursor_calls = [cursor1, cursor2]
        cursor_index = [0]

        def get_cursor(*args, **kwargs):
            idx = cursor_index[0]
            cursor_index[0] += 1
            return cursor_calls[idx]

        conn = AsyncMock()
        conn.cursor = MagicMock(side_effect=get_cursor)
        conn.commit = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.connection = MagicMock(return_value=conn)

        executor = DeliveryExecutor(
            channels={},
            db_pool=pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        result = await executor._claim_delivery(str(delivery_id))
        assert result is None

    @pytest.mark.asyncio()
    async def test_claim_delivery_not_found(self, mock_redis, mock_poison_queue):
        """Test claim when delivery does not exist."""
        delivery_id = uuid4()

        cursor1 = AsyncMock()
        cursor1.execute = AsyncMock()
        cursor1.fetchone = AsyncMock(return_value=None)
        cursor1.__aenter__ = AsyncMock(return_value=cursor1)
        cursor1.__aexit__ = AsyncMock(return_value=None)

        cursor2 = AsyncMock()
        cursor2.execute = AsyncMock()
        cursor2.fetchone = AsyncMock(return_value=None)  # Not found
        cursor2.__aenter__ = AsyncMock(return_value=cursor2)
        cursor2.__aexit__ = AsyncMock(return_value=None)

        cursor_calls = [cursor1, cursor2]
        cursor_index = [0]

        def get_cursor(*args, **kwargs):
            idx = cursor_index[0]
            cursor_index[0] += 1
            return cursor_calls[idx]

        conn = AsyncMock()
        conn.cursor = MagicMock(side_effect=get_cursor)
        conn.commit = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.connection = MagicMock(return_value=conn)

        executor = DeliveryExecutor(
            channels={},
            db_pool=pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

        result = await executor._claim_delivery(str(delivery_id))
        assert result is None


class TestDeliveryExecutorRecordAttempt:
    """Test DeliveryExecutor._record_attempt method."""

    @pytest.fixture()
    def executor(self):
        """Create executor for testing."""
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()

        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)

        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=cursor)
        conn.commit = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)

        pool = AsyncMock()
        pool.connection = MagicMock(return_value=conn)

        return DeliveryExecutor(
            channels={},
            db_pool=pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )

    @pytest.mark.asyncio()
    async def test_record_attempt_success(self, executor):
        """Test recording a successful delivery attempt."""
        delivery_id = str(uuid4())

        await executor._record_attempt(
            delivery_id=delivery_id,
            attempts=1,
            status=DeliveryStatus.DELIVERED,
            error=None,
            delivered=True,
        )

        # Verify execute was called (basic sanity check)
        # The actual SQL verification would require inspecting the mock calls

    @pytest.mark.asyncio()
    async def test_record_attempt_failure(self, executor):
        """Test recording a failed delivery attempt."""
        delivery_id = str(uuid4())

        await executor._record_attempt(
            delivery_id=delivery_id,
            attempts=2,
            status=DeliveryStatus.FAILED,
            error="Connection timeout",
            delivered=False,
        )


class TestDeliveryExecutorUnknownChannel:
    """Test DeliveryExecutor handling of unknown channel types."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_execute_unknown_channel(self, mock_db_pool, mock_redis, mock_poison_queue):
        """Test execute with unknown channel type goes to poison queue."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        # Create executor with NO channels configured
        executor = DeliveryExecutor(
            channels={},  # No channels!
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
        )

        assert result.success is False
        assert result.error == "Unknown channel type"
        mock_poison_queue.add.assert_called_once()


class TestDeliveryExecutorChannelSendException:
    """Test DeliveryExecutor handling of channel.send exceptions."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_execute_channel_raises_exception(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test execute when channel.send raises an exception."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        # Create channel that raises exception
        failing_channel = AsyncMock()
        failing_channel.send = AsyncMock(side_effect=RuntimeError("Connection refused"))

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: failing_channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
        )

        # After MAX_ATTEMPTS (3), it should fail
        assert result.success is False
        assert "Connection refused" in result.error
        mock_poison_queue.add.assert_called_once()


class TestDeliveryExecutorTerminalFailure:
    """Test DeliveryExecutor terminal failure handling."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_execute_non_retryable_failure(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test execute with non-retryable failure goes to poison queue immediately."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        # Create channel that returns non-retryable failure
        channel = AsyncMock()
        channel.send = AsyncMock(
            return_value=DeliveryResult(
                success=False,
                error="Invalid recipient format",
                retryable=False,  # Non-retryable
            )
        )

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
        )

        assert result.success is False
        assert result.error == "Invalid recipient format"
        # Should go to poison queue on first non-retryable failure
        mock_poison_queue.add.assert_called_once()
        # Should only attempt once for non-retryable
        assert channel.send.call_count == 1


class TestDeliveryExecutorReenqueueWithScheduler:
    """Test DeliveryExecutor reenqueue logic with retry scheduler."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_execute_reenqueue_with_long_retry_after(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test execute reenqueues with scheduler when retry_after is long."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        # Create channel that returns retryable failure with long retry_after
        channel = AsyncMock()
        channel.send = AsyncMock(
            return_value=DeliveryResult(
                success=False,
                error="Rate limited",
                retryable=True,
                metadata={"retry_after": "60"},  # Long retry
            )
        )

        retry_scheduler = AsyncMock()

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            retry_scheduler=retry_scheduler,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
        )

        assert result.success is False
        assert result.error == "Rate limited"
        # Scheduler should be called with the delay and attempt number
        retry_scheduler.assert_called_once_with(60, 1)
        # Should NOT decrement queue depth when handing off to scheduler
        executor.queue_depth_manager.decrement.assert_not_called()


class TestDeliveryExecutorAttemptLimitReached:
    """Test DeliveryExecutor when attempt limit is reached."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_execute_max_attempts_reached(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test execute when max attempts are exhausted."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=2,  # Already at 2 attempts
            created_at=datetime.now(UTC),
        )

        # Create channel that always fails
        channel = AsyncMock()
        channel.send = AsyncMock(
            return_value=DeliveryResult(
                success=False,
                error="Service unavailable",
                retryable=True,
            )
        )

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
            attempt=2,  # Starting at attempt 2
        )

        assert result.success is False
        # Should go to poison queue after exhausting attempts
        mock_poison_queue.add.assert_called()


class TestDeliveryExecutorRateLimitRetriesExhausted:
    """Test DeliveryExecutor rate limit retries exhausted scenario."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_rate_limit_retries_exhausted_with_scheduler(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test rate limit retries exhausted goes to poison queue."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=2,  # At attempt 2, next will be 3 which equals MAX_ATTEMPTS
            created_at=datetime.now(UTC),
        )

        mock_rate_limiter = AsyncMock()
        mock_rate_limiter.check_channel_rate_limit = AsyncMock(return_value=False)

        retry_scheduler = AsyncMock()

        executor = DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
            retry_scheduler=retry_scheduler,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._record_attempt_failure = AsyncMock()

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
            attempt=2,
        )

        assert result.success is False
        assert result.error == "rate_limit_retries_exhausted"
        mock_poison_queue.add.assert_called_once()


class TestDeliveryExecutorSchedulerFailureContinue:
    """Test DeliveryExecutor scheduler failure continuation logic."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_rate_limit_scheduler_failure_exhausts_attempts(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test rate limit scheduler failure exhausts attempts and goes to poison queue."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        mock_rate_limiter = AsyncMock()
        mock_rate_limiter.check_channel_rate_limit = AsyncMock(return_value=False)

        # Scheduler fails
        retry_scheduler = AsyncMock(
            side_effect=redis.exceptions.ConnectionError("Redis down")
        )

        executor = DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
            retry_scheduler=retry_scheduler,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._record_attempt_failure = AsyncMock()

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
            attempt=0,
        )

        # After 3 scheduler failures, should go to poison queue
        assert result.success is False
        mock_poison_queue.add.assert_called()


class TestDeliveryExecutorRateLimitSchedulerSuccess:
    """Test DeliveryExecutor rate limit scheduler success path."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_rate_limit_retry_with_scheduler_success(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test rate limit with retry scheduler success records metric and returns."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        mock_rate_limiter = AsyncMock()
        mock_rate_limiter.check_channel_rate_limit = AsyncMock(return_value=False)

        # Scheduler succeeds
        retry_scheduler = AsyncMock()

        executor = DeliveryExecutor(
            channels={},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
            retry_scheduler=retry_scheduler,
        )
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._record_attempt_failure = AsyncMock()

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
            attempt=0,
        )

        # Should return with rate limit error
        assert result.success is False
        assert result.error == "channel_rate_limited"
        # Scheduler should be called
        retry_scheduler.assert_called_once()
        # Should NOT decrement queue depth when handing off to scheduler
        executor.queue_depth_manager.decrement.assert_not_called()


class TestDeliveryExecutorInMemoryRetry:
    """Test DeliveryExecutor in-memory retry without scheduler."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_in_memory_retry_without_scheduler(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test in-memory retry sleep when no scheduler available and long retry_after."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=0,
            created_at=datetime.now(UTC),
        )

        # Create channel that fails first, then succeeds
        call_count = [0]

        async def send_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return DeliveryResult(
                    success=False,
                    error="Rate limited",
                    retryable=True,
                    metadata={"retry_after": "10"},  # Long retry but NO scheduler
                )
            return DeliveryResult(success=True, message_id="msg-123")

        channel = AsyncMock()
        channel.send = AsyncMock(side_effect=send_side_effect)

        # NO retry scheduler - will do in-memory retry
        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            retry_scheduler=None,  # No scheduler!
        )
        # Patch sleep to avoid waiting
        executor.MAX_IN_MEMORY_SLEEP_SECONDS = 0.001
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
        )

        # Should succeed on second attempt
        assert result.success is True
        assert call_count[0] == 2


class TestDeliveryExecutorRetryWithDelay:
    """Test DeliveryExecutor retry logic with delay."""

    @pytest.fixture()
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()

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
        redis_mock = AsyncMock()
        redis_mock.eval = AsyncMock(return_value=0)
        return redis_mock

    @pytest.fixture()
    def mock_poison_queue(self):
        """Create mock poison queue."""
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_retry_with_delay_on_subsequent_attempts(
        self, mock_db_pool, mock_redis, mock_poison_queue
    ):
        """Test that retry delay is applied on subsequent attempts."""
        delivery_id = uuid4()
        claimed_delivery = AlertDelivery(
            id=delivery_id,
            alert_id=uuid4(),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/services/T000/B000/XXXX",
            dedup_key="test:slack:hash:123",
            status=DeliveryStatus.IN_PROGRESS,
            attempts=1,  # Start at attempt 1 (retry)
            created_at=datetime.now(UTC),
        )

        # Channel succeeds on this attempt
        channel = AsyncMock()
        channel.send = AsyncMock(return_value=DeliveryResult(success=True, message_id="msg-123"))

        executor = DeliveryExecutor(
            channels={ChannelType.SLACK: channel},
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
        )
        # Patch delays to be very short for testing
        executor.MAX_IN_MEMORY_SLEEP_SECONDS = 0.001
        executor.RETRY_DELAYS = [0.001, 0.001]
        executor.queue_depth_manager = MagicMock()
        executor.queue_depth_manager.decrement = AsyncMock()
        executor._claim_delivery = AsyncMock(return_value=claimed_delivery)
        executor._record_attempt = AsyncMock()
        executor._check_rate_limits = AsyncMock(return_value=None)

        result = await executor.execute(
            delivery_id=str(delivery_id),
            channel=ChannelType.SLACK,
            recipient="https://hooks.slack.com/test",
            subject="Test",
            body="Body",
            attempt=1,  # This triggers delay since current_attempt > attempt
        )

        assert result.success is True
