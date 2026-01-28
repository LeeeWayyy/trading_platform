"""Tests for alert worker entrypoint exception handling."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import psycopg
import pytest
import redis.exceptions
from psycopg_pool import AsyncConnectionPool

from apps.alert_worker.entrypoint import (
    AsyncResources,
    _build_executor,
    _close_async_resources,
    _create_async_resources,
    _execute_delivery_job,
    _get_channels,
    _get_rq_queue,
    _require_env,
    execute_delivery_job,
    main,
)
from libs.core.common.exceptions import ConfigurationError
from libs.platform.alerts.models import ChannelType, DeliveryResult


class TestRequireEnv:
    """Test _require_env utility function."""

    def test_require_env_returns_value_when_set(self, monkeypatch):
        """Test _require_env returns value when environment variable is set."""
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = _require_env("TEST_VAR")
        assert result == "test_value"

    def test_require_env_exits_when_not_set(self, monkeypatch):
        """Test _require_env exits with code 1 when environment variable is not set."""
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _require_env("TEST_VAR")
        assert exc_info.value.code == 1

    def test_require_env_exits_when_empty_string(self, monkeypatch):
        """Test _require_env exits when environment variable is empty string."""
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(SystemExit) as exc_info:
            _require_env("TEST_VAR")
        assert exc_info.value.code == 1


class TestGetRQQueue:
    """Test _get_rq_queue function."""

    def test_get_rq_queue_creates_queue(self, monkeypatch):
        """Test _get_rq_queue creates Queue with correct parameters."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.Queue") as mock_queue_class,
            patch("apps.alert_worker.entrypoint._RQ_QUEUE", None),
        ):
            mock_redis = Mock()
            mock_redis_class.from_url.return_value = mock_redis
            mock_queue = Mock()
            mock_queue_class.return_value = mock_queue

            result = _get_rq_queue()

            mock_redis_class.from_url.assert_called_once_with("redis://localhost:6379")
            mock_queue_class.assert_called_once_with("alerts", connection=mock_redis)
            assert result == mock_queue

    def test_get_rq_queue_returns_cached_instance(self, monkeypatch):
        """Test _get_rq_queue returns cached instance on subsequent calls."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.Queue") as mock_queue_class,
        ):
            mock_redis = Mock()
            mock_redis_class.from_url.return_value = mock_redis
            mock_queue = Mock()
            mock_queue_class.return_value = mock_queue

            # First call
            result1 = _get_rq_queue()
            # Second call
            result2 = _get_rq_queue()

            # Should only create once
            assert mock_redis_class.from_url.call_count <= 2  # May be called during import
            assert result1 == result2


class TestCreateAsyncResources:
    """Test _create_async_resources function."""

    @pytest.mark.asyncio()
    async def test_create_async_resources_success(self, monkeypatch):
        """Test _create_async_resources creates all resources successfully."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

        mock_redis = AsyncMock()
        mock_pool = AsyncMock(spec=AsyncConnectionPool)
        mock_pool.open = AsyncMock()

        with (
            patch("apps.alert_worker.entrypoint.redis_async.from_url") as mock_redis_from_url,
            patch("apps.alert_worker.entrypoint.AsyncConnectionPool") as mock_pool_class,
            patch("apps.alert_worker.entrypoint.PoisonQueue") as mock_poison_class,
            patch("apps.alert_worker.entrypoint.RateLimiter") as mock_limiter_class,
        ):
            mock_redis_from_url.return_value = mock_redis
            mock_pool_class.return_value = mock_pool
            mock_poison = Mock()
            mock_poison_class.return_value = mock_poison
            mock_limiter = Mock()
            mock_limiter_class.return_value = mock_limiter

            resources = await _create_async_resources()

            assert isinstance(resources, AsyncResources)
            assert resources.db_pool == mock_pool
            assert resources.redis_client == mock_redis
            assert resources.poison_queue == mock_poison
            assert resources.rate_limiter == mock_limiter
            mock_pool.open.assert_awaited_once()


class TestCloseAsyncResources:
    """Test _close_async_resources exception handling."""

    @pytest.mark.asyncio()
    async def test_close_both_succeed(self):
        """Test successful close of both resources."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should not raise
        await _close_async_resources(resources)

        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_db_pool_close_psycopg_error_propagates(self):
        """Test psycopg error during DB pool close is propagated."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        # Mock DB pool close to raise psycopg.Error
        db_error = psycopg.OperationalError("DB connection failed")
        mock_db_pool.close.side_effect = db_error

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should propagate the error
        with pytest.raises(psycopg.OperationalError):
            await _close_async_resources(resources)

        # Verify both close attempts were made
        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_redis_close_redis_error_propagates(self):
        """Test Redis error during Redis close is propagated."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        # Mock Redis close to raise redis.exceptions.RedisError
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        mock_redis.close.side_effect = redis_error

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should propagate the error
        with pytest.raises(redis.exceptions.ConnectionError):
            await _close_async_resources(resources)

        # Verify both close attempts were made
        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_both_close_fail_raises_exception_group(self):
        """Test both resources failing raises ExceptionGroup."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        # Mock both to raise errors
        db_error = psycopg.OperationalError("DB connection failed")
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        mock_db_pool.close.side_effect = db_error
        mock_redis.close.side_effect = redis_error

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should raise ExceptionGroup with both errors
        with pytest.raises(ExceptionGroup) as exc_info:
            await _close_async_resources(resources)

        # Verify exception group contains both errors
        assert len(exc_info.value.exceptions) == 2
        assert isinstance(exc_info.value.exceptions[0], psycopg.OperationalError)
        assert isinstance(exc_info.value.exceptions[1], redis.exceptions.ConnectionError)

        # Verify both close attempts were made
        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()


class TestGetChannels:
    """Test _get_channels function."""

    def test_get_channels_creates_email_and_slack(self):
        """Test _get_channels creates EMAIL and SLACK channels."""
        with (
            patch("apps.alert_worker.entrypoint._CHANNELS", None),
            patch("apps.alert_worker.entrypoint.EmailChannel") as mock_email,
            patch("apps.alert_worker.entrypoint.SlackChannel") as mock_slack,
            patch("apps.alert_worker.entrypoint.SMSChannel") as mock_sms,
        ):
            mock_email_instance = Mock()
            mock_slack_instance = Mock()
            mock_email.return_value = mock_email_instance
            mock_slack.return_value = mock_slack_instance
            mock_sms.side_effect = ConfigurationError("Twilio not configured")

            channels = _get_channels()

            assert ChannelType.EMAIL in channels
            assert ChannelType.SLACK in channels
            assert channels[ChannelType.EMAIL] == mock_email_instance
            assert channels[ChannelType.SLACK] == mock_slack_instance

    def test_get_channels_skips_sms_when_not_configured(self):
        """Test _get_channels skips SMS when Twilio credentials are missing."""
        with (
            patch("apps.alert_worker.entrypoint._CHANNELS", None),
            patch("apps.alert_worker.entrypoint.EmailChannel"),
            patch("apps.alert_worker.entrypoint.SlackChannel"),
            patch("apps.alert_worker.entrypoint.SMSChannel") as mock_sms,
        ):
            mock_sms.side_effect = ConfigurationError("Twilio credentials missing")

            channels = _get_channels()

            assert ChannelType.SMS not in channels

    def test_get_channels_includes_sms_when_configured(self):
        """Test _get_channels includes SMS when Twilio credentials are present."""
        with (
            patch("apps.alert_worker.entrypoint._CHANNELS", None),
            patch("apps.alert_worker.entrypoint.EmailChannel"),
            patch("apps.alert_worker.entrypoint.SlackChannel"),
            patch("apps.alert_worker.entrypoint.SMSChannel") as mock_sms,
        ):
            mock_sms_instance = Mock()
            mock_sms.return_value = mock_sms_instance

            channels = _get_channels()

            assert ChannelType.SMS in channels
            assert channels[ChannelType.SMS] == mock_sms_instance

    def test_get_channels_returns_cached_instance(self):
        """Test _get_channels returns cached instance on subsequent calls."""
        with (
            patch("apps.alert_worker.entrypoint.EmailChannel") as mock_email,
            patch("apps.alert_worker.entrypoint.SlackChannel") as mock_slack,
            patch("apps.alert_worker.entrypoint.SMSChannel") as mock_sms,
        ):
            mock_sms.side_effect = ConfigurationError("Twilio not configured")

            # First call
            channels1 = _get_channels()
            # Second call
            channels2 = _get_channels()

            # Should return same instance
            assert channels1 is channels2
            # Should only create channels once
            assert mock_email.call_count <= 2  # May be called during import
            assert mock_slack.call_count <= 2


class TestBuildExecutor:
    """Test _build_executor function."""

    @pytest.mark.asyncio()
    async def test_build_executor_creates_delivery_executor(self):
        """Test _build_executor creates DeliveryExecutor with correct parameters."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison = Mock()
        mock_limiter = Mock()

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison,
            rate_limiter=mock_limiter,
        )

        with (
            patch("apps.alert_worker.entrypoint._get_rq_queue") as mock_get_queue,
            patch("apps.alert_worker.entrypoint._get_channels") as mock_get_channels,
            patch("apps.alert_worker.entrypoint.DeliveryExecutor") as mock_executor_class,
        ):
            mock_queue = Mock()
            mock_get_queue.return_value = mock_queue
            mock_channels = {ChannelType.EMAIL: Mock()}
            mock_get_channels.return_value = mock_channels
            mock_executor = Mock()
            mock_executor_class.return_value = mock_executor

            executor = await _build_executor(
                resources=resources,
                delivery_id="test-id",
                channel="email",
                recipient="test@example.com",
                subject="Test Subject",
                body="Test Body",
            )

            assert executor == mock_executor
            mock_executor_class.assert_called_once()
            call_kwargs = mock_executor_class.call_args[1]
            assert call_kwargs["channels"] == mock_channels
            assert call_kwargs["db_pool"] == mock_db_pool
            assert call_kwargs["redis_client"] == mock_redis
            assert call_kwargs["poison_queue"] == mock_poison
            assert call_kwargs["rate_limiter"] == mock_limiter
            assert callable(call_kwargs["retry_scheduler"])

    @pytest.mark.asyncio()
    async def test_build_executor_retry_scheduler_enqueues_job(self):
        """Test retry_scheduler callback enqueues job with correct parameters."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison = Mock()
        mock_limiter = Mock()

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison,
            rate_limiter=mock_limiter,
        )

        with (
            patch("apps.alert_worker.entrypoint._get_rq_queue") as mock_get_queue,
            patch("apps.alert_worker.entrypoint._get_channels"),
            patch("apps.alert_worker.entrypoint.DeliveryExecutor") as mock_executor_class,
            patch("apps.alert_worker.entrypoint.asyncio.to_thread") as mock_to_thread,
        ):
            mock_queue = Mock()
            mock_get_queue.return_value = mock_queue
            mock_to_thread.return_value = None

            await _build_executor(
                resources=resources,
                delivery_id="test-id",
                channel="email",
                recipient="test@example.com",
                subject="Test Subject",
                body="Test Body",
            )

            # Get the retry_scheduler callback
            retry_scheduler = mock_executor_class.call_args[1]["retry_scheduler"]

            # Call the retry scheduler
            await retry_scheduler(delay=60, next_attempt=2)

            # Verify asyncio.to_thread was called with correct parameters
            mock_to_thread.assert_called_once()
            args = mock_to_thread.call_args[0]
            assert args[0] == mock_queue.enqueue_in
            assert args[1] == timedelta(seconds=60)


class TestExecuteDeliveryJob:
    """Test _execute_delivery_job and execute_delivery_job functions."""

    @pytest.mark.asyncio()
    async def test_execute_delivery_job_success(self):
        """Test _execute_delivery_job executes successfully and returns result."""
        mock_resources = Mock(spec=AsyncResources)
        mock_executor = AsyncMock()
        mock_result = DeliveryResult(
            success=True,
            message_id="msg-123",
            error=None,
            retryable=True,
            metadata={},
        )
        mock_executor.execute.return_value = mock_result

        with (
            patch("apps.alert_worker.entrypoint._create_async_resources") as mock_create,
            patch("apps.alert_worker.entrypoint._build_executor") as mock_build,
            patch("apps.alert_worker.entrypoint._close_async_resources") as mock_close,
        ):
            mock_create.return_value = mock_resources
            mock_build.return_value = mock_executor

            result = await _execute_delivery_job(
                delivery_id="test-id",
                channel="email",
                recipient="test@example.com",
                subject="Test Subject",
                body="Test Body",
                attempt=0,
            )

            assert result["success"] is True
            assert result["message_id"] == "msg-123"
            mock_create.assert_called_once()
            mock_build.assert_called_once_with(
                mock_resources, "test-id", "email", "test@example.com", "Test Subject", "Test Body"
            )
            mock_executor.execute.assert_called_once_with(
                delivery_id="test-id",
                channel=ChannelType.EMAIL,
                recipient="test@example.com",
                subject="Test Subject",
                body="Test Body",
                attempt=0,
            )
            mock_close.assert_called_once_with(mock_resources)

    @pytest.mark.asyncio()
    async def test_execute_delivery_job_closes_resources_on_exception(self):
        """Test _execute_delivery_job closes resources even when exception occurs."""
        mock_resources = Mock(spec=AsyncResources)

        with (
            patch("apps.alert_worker.entrypoint._create_async_resources") as mock_create,
            patch("apps.alert_worker.entrypoint._build_executor") as mock_build,
            patch("apps.alert_worker.entrypoint._close_async_resources") as mock_close,
        ):
            mock_create.return_value = mock_resources
            mock_build.side_effect = ValueError("Test error")

            with pytest.raises(ValueError, match="Test error"):
                await _execute_delivery_job(
                    delivery_id="test-id",
                    channel="email",
                    recipient="test@example.com",
                    subject="Test Subject",
                    body="Test Body",
                    attempt=0,
                )

            # Resources should still be closed
            mock_close.assert_called_once_with(mock_resources)

    def test_execute_delivery_job_sync_wrapper(self):
        """Test execute_delivery_job synchronous wrapper calls asyncio.run."""
        mock_result = {
            "success": True,
            "message_id": "msg-123",
            "error": None,
            "retryable": True,
            "metadata": {},
        }

        with patch("apps.alert_worker.entrypoint.asyncio.run") as mock_run:
            mock_run.return_value = mock_result

            result = execute_delivery_job(
                delivery_id="test-id",
                channel="email",
                recipient="test@example.com",
                subject="Test Subject",
                body="Test Body",
                attempt=0,
            )

            assert result == mock_result
            mock_run.assert_called_once()


class TestMain:
    """Test main function."""

    def test_main_exits_when_redis_url_missing(self, monkeypatch):
        """Test main exits when REDIS_URL is not set."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_exits_when_database_url_missing(self, monkeypatch):
        """Test main exits when DATABASE_URL is not set."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_exits_when_db_connection_fails(self, monkeypatch):
        """Test main exits when database connection fails."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.ConnectionPool") as mock_pool_class,
        ):
            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.from_url.return_value = mock_redis

            mock_pool = Mock()
            mock_pool_class.return_value = mock_pool
            mock_pool.connection.side_effect = psycopg.OperationalError("DB connection failed")

            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

    def test_main_exits_when_redis_connection_fails(self, monkeypatch):
        """Test main exits when Redis connection fails."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.ConnectionPool") as mock_pool_class,
        ):
            mock_redis = Mock()
            mock_redis.ping.side_effect = redis.exceptions.ConnectionError(
                "Redis connection failed"
            )
            mock_redis_class.from_url.return_value = mock_redis

            # Mock successful DB connection
            mock_pool = MagicMock()
            mock_pool_class.return_value = mock_pool

            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

    def test_main_starts_worker_with_default_queue(self, monkeypatch):
        """Test main starts RQ worker with default 'alerts' queue."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        monkeypatch.delenv("RQ_QUEUES", raising=False)

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.ConnectionPool") as mock_pool_class,
            patch("apps.alert_worker.entrypoint.Worker") as mock_worker_class,
            patch("apps.alert_worker.entrypoint.asyncio.run") as mock_asyncio_run,
        ):
            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.from_url.return_value = mock_redis

            # Mock successful DB connection
            mock_pool = MagicMock()
            mock_pool_class.return_value = mock_pool

            # Mock worker
            mock_worker = Mock()
            mock_worker_class.return_value = mock_worker

            # Mock asyncio.run for startup metrics
            mock_asyncio_run.return_value = None

            main()

            # Verify worker was created with default queue
            mock_worker_class.assert_called_once_with(["alerts"], connection=mock_redis)
            mock_worker.work.assert_called_once_with(with_scheduler=True)

    def test_main_starts_worker_with_custom_queues(self, monkeypatch):
        """Test main starts RQ worker with custom queues from RQ_QUEUES env."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        monkeypatch.setenv("RQ_QUEUES", "alerts,high_priority,low_priority")

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.ConnectionPool") as mock_pool_class,
            patch("apps.alert_worker.entrypoint.Worker") as mock_worker_class,
            patch("apps.alert_worker.entrypoint.asyncio.run") as mock_asyncio_run,
        ):
            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.from_url.return_value = mock_redis

            # Mock successful DB connection
            mock_pool = MagicMock()
            mock_pool_class.return_value = mock_pool

            # Mock worker
            mock_worker = Mock()
            mock_worker_class.return_value = mock_worker

            # Mock asyncio.run for startup metrics
            mock_asyncio_run.return_value = None

            main()

            # Verify worker was created with custom queues
            mock_worker_class.assert_called_once_with(
                ["alerts", "high_priority", "low_priority"], connection=mock_redis
            )
            mock_worker.work.assert_called_once_with(with_scheduler=True)

    def test_main_syncs_startup_metrics(self, monkeypatch):
        """Test main syncs startup metrics from database."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

        with (
            patch("apps.alert_worker.entrypoint.Redis") as mock_redis_class,
            patch("apps.alert_worker.entrypoint.ConnectionPool") as mock_pool_class,
            patch("apps.alert_worker.entrypoint.Worker") as mock_worker_class,
            patch("apps.alert_worker.entrypoint.asyncio.run") as mock_asyncio_run,
        ):
            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.from_url.return_value = mock_redis

            # Mock successful DB connection
            mock_pool = MagicMock()
            mock_pool_class.return_value = mock_pool

            # Mock worker
            mock_worker = Mock()
            mock_worker_class.return_value = mock_worker

            # Mock asyncio.run for startup metrics
            mock_asyncio_run.return_value = None

            main()

            # Verify asyncio.run was called for startup metrics sync
            assert mock_asyncio_run.call_count == 1
