"""
Tests for signal_service/main.py background tasks.

Tests coverage for:
- model_reload_task() (lines 184-300)
- feature_hydration_task() (lines 321-380)
- _attempt_redis_reconnect() (lines 390-424)
- redis_fallback_replay_task() (lines 436-472)
- Helper functions for shadow validation and Redis fallback
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from apps.signal_service.main import (
    _attempt_redis_reconnect,
    _buffer_signal_payload,
    _on_model_activated,
    _publish_signal_event_with_fallback,
    _record_fallback_buffer_metrics,
    _record_shadow_skip_if_bypassed,
    _record_shadow_validation,
    _schedule_shadow_validation,
    _shadow_validate,
    _should_hydrate_features,
    feature_hydration_task,
    model_reload_task,
    redis_fallback_replay_task,
)
from apps.signal_service.model_registry import ModelMetadata
from apps.signal_service.shadow_validator import ShadowValidationResult
from libs.core.redis_client import RedisConnectionError, SignalEvent

pytestmark = pytest.mark.asyncio


class TestModelReloadTask:
    """Test model_reload_task() background task."""

    async def test_model_reload_task_successful_reload(
        self,
        mock_settings: Mock,
        mock_model_registry: Mock,
    ) -> None:
        """Test model reload task successfully reloads model on version change."""
        mock_settings.model_reload_interval_seconds = 0.01  # Fast for testing

        # Simulate reload on second iteration
        mock_model_registry.reload_if_changed.side_effect = [False, True, asyncio.CancelledError()]
        mock_metadata = Mock()
        mock_metadata.strategy_name = "alpha_baseline"
        mock_metadata.version = "v1.0.1"
        mock_model_registry.current_metadata = mock_metadata
        mock_model_registry.pending_validation = False

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_model_registry):
                with patch("apps.signal_service.main._shadow_validate"):
                    with patch("apps.signal_service.main._schedule_shadow_validation"):
                        with patch("apps.signal_service.main._on_model_activated"):
                            with pytest.raises(asyncio.CancelledError):
                                await model_reload_task()

    async def test_model_reload_task_cold_load_recovery_success(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test model reload task performs cold-load recovery when model not loaded."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_registry = Mock()
        mock_registry.is_loaded = False  # Not loaded initially

        # Simulate successful cold load on first attempt
        def reload_side_effect(*args, **kwargs):
            mock_registry.is_loaded = True  # Model loaded after call
            return True

        mock_registry.reload_if_changed.side_effect = [reload_side_effect, asyncio.CancelledError()]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with patch("apps.signal_service.main._shadow_validate"):
                    with patch("apps.signal_service.main._schedule_shadow_validation"):
                        with patch("apps.signal_service.main._on_model_activated"):
                            with pytest.raises(asyncio.CancelledError):
                                await model_reload_task()

    async def test_model_reload_task_cold_load_recovery_value_error(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test model reload task handles ValueError during cold-load recovery."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_registry = Mock()
        mock_registry.is_loaded = False
        mock_registry.reload_if_changed.side_effect = [
            ValueError("Invalid model"),
            asyncio.CancelledError(),
        ]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()

    async def test_model_reload_task_cold_load_recovery_file_not_found(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test model reload task handles FileNotFoundError during cold-load recovery."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_registry = Mock()
        mock_registry.is_loaded = False
        mock_registry.reload_if_changed.side_effect = [
            FileNotFoundError("Model file missing"),
            asyncio.CancelledError(),
        ]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()

    async def test_model_reload_task_cold_load_recovery_redis_error(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test model reload task handles RedisConnectionError during cold-load recovery."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_registry = Mock()
        mock_registry.is_loaded = False
        mock_registry.reload_if_changed.side_effect = [
            RedisConnectionError("Redis down"),
            asyncio.CancelledError(),
        ]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()

    async def test_model_reload_task_pending_validation(
        self,
        mock_settings: Mock,
        mock_model_registry: Mock,
    ) -> None:
        """Test model reload task logs when shadow validation is pending."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_model_registry.reload_if_changed.side_effect = [False, asyncio.CancelledError()]
        mock_model_registry.pending_validation = True
        mock_metadata = Mock()
        mock_metadata.version = "v1.0.1"
        mock_model_registry.pending_metadata = mock_metadata

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_model_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()

    async def test_model_reload_task_handles_value_error(
        self,
        mock_settings: Mock,
        mock_model_registry: Mock,
    ) -> None:
        """Test model reload task handles ValueError and continues polling."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_model_registry.reload_if_changed.side_effect = [
            ValueError("Invalid data"),
            asyncio.CancelledError(),
        ]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_model_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()

    async def test_model_reload_task_handles_file_error(
        self,
        mock_settings: Mock,
        mock_model_registry: Mock,
    ) -> None:
        """Test model reload task handles FileNotFoundError and continues polling."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_model_registry.reload_if_changed.side_effect = [
            FileNotFoundError("Missing file"),
            asyncio.CancelledError(),
        ]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_model_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()

    async def test_model_reload_task_handles_redis_error(
        self,
        mock_settings: Mock,
        mock_model_registry: Mock,
    ) -> None:
        """Test model reload task handles RedisConnectionError and continues polling."""
        mock_settings.model_reload_interval_seconds = 0.01

        mock_model_registry.reload_if_changed.side_effect = [
            RedisConnectionError("Redis down"),
            asyncio.CancelledError(),
        ]

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.model_registry", mock_model_registry):
                with pytest.raises(asyncio.CancelledError):
                    await model_reload_task()


class TestFeatureHydrationTask:
    """Test feature_hydration_task() background task."""

    async def test_feature_hydration_success(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test feature hydration task completes successfully."""
        mock_settings.feature_hydration_timeout_seconds = 5

        mock_generator = Mock()
        mock_generator.hydrate_feature_cache = Mock()

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.signal_generator", mock_generator):
                with patch("apps.signal_service.main.hydration_complete", False):
                    await feature_hydration_task(symbols=["AAPL", "MSFT"], history_days=30)

                    mock_generator.hydrate_feature_cache.assert_called_once()

    async def test_feature_hydration_timeout(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test feature hydration task handles timeout."""
        mock_settings.feature_hydration_timeout_seconds = 0.01

        mock_generator = Mock()

        async def slow_hydration(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than timeout

        mock_generator.hydrate_feature_cache = slow_hydration

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.signal_generator", mock_generator):
                await feature_hydration_task(symbols=["AAPL"], history_days=30)

                # Task should complete without raising, but hydration_complete stays False

    async def test_feature_hydration_value_error(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test feature hydration task handles ValueError."""
        mock_settings.feature_hydration_timeout_seconds = 5

        mock_generator = Mock()
        mock_generator.hydrate_feature_cache.side_effect = ValueError("Invalid data")

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.signal_generator", mock_generator):
                await feature_hydration_task(symbols=["AAPL"], history_days=30)

                # Task completes without raising

    async def test_feature_hydration_file_not_found(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test feature hydration task handles FileNotFoundError."""
        mock_settings.feature_hydration_timeout_seconds = 5

        mock_generator = Mock()
        mock_generator.hydrate_feature_cache.side_effect = FileNotFoundError("Data missing")

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.signal_generator", mock_generator):
                await feature_hydration_task(symbols=["AAPL"], history_days=30)

    async def test_feature_hydration_redis_error(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test feature hydration task handles RedisConnectionError."""
        mock_settings.feature_hydration_timeout_seconds = 5

        mock_generator = Mock()
        mock_generator.hydrate_feature_cache.side_effect = RedisConnectionError("Redis down")

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.signal_generator", mock_generator):
                await feature_hydration_task(symbols=["AAPL"], history_days=30)


class TestAttemptRedisReconnect:
    """Test _attempt_redis_reconnect() function."""

    def test_attempt_redis_reconnect_already_connected(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test reconnect returns True when already connected."""
        mock_redis = Mock()

        with patch("apps.signal_service.main.redis_client", mock_redis):
            result = _attempt_redis_reconnect()

        assert result is True

    def test_attempt_redis_reconnect_success(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test successful Redis reconnection."""
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_db = 0
        mock_settings.redis_ttl = 3600

        mock_redis_instance = Mock()
        mock_feature_cache = Mock()

        with patch("apps.signal_service.main.redis_client", None):
            with patch("apps.signal_service.main.settings", mock_settings):
                with patch(
                    "apps.signal_service.main.get_optional_secret_or_none", return_value=None
                ):
                    with patch(
                        "apps.signal_service.main.RedisClient", return_value=mock_redis_instance
                    ):
                        with patch("apps.signal_service.main.EventPublisher"):
                            with patch(
                                "apps.signal_service.main.FeatureCache",
                                return_value=mock_feature_cache,
                            ):
                                with patch("apps.signal_service.main.signal_generator", Mock()):
                                    with patch("apps.signal_service.main._generator_cache", {}):
                                        result = _attempt_redis_reconnect()

        assert result is True

    def test_attempt_redis_reconnect_failure(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test failed Redis reconnection."""
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379

        with patch("apps.signal_service.main.redis_client", None):
            with patch("apps.signal_service.main.settings", mock_settings):
                with patch(
                    "apps.signal_service.main.get_optional_secret_or_none", return_value=None
                ):
                    with patch(
                        "apps.signal_service.main.RedisClient",
                        side_effect=RedisConnectionError("Connection failed"),
                    ):
                        result = _attempt_redis_reconnect()

        assert result is False


class TestRedFallbackReplayTask:
    """Test redis_fallback_replay_task() background task."""

    async def test_redis_fallback_replay_disabled(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test replay task skips when Redis disabled."""
        mock_settings.redis_enabled = False
        mock_settings.redis_fallback_replay_interval_seconds = 0.01

        with patch("apps.signal_service.main.settings", mock_settings):
            # Task should loop once and be cancelled
            task = asyncio.create_task(redis_fallback_replay_task())
            await asyncio.sleep(0.05)
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_redis_fallback_replay_success(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test replay task successfully replays buffered signals."""
        mock_settings.redis_enabled = True
        mock_settings.redis_fallback_replay_interval_seconds = 0.01

        mock_fallback = Mock()
        mock_fallback.size = 5
        mock_fallback.replay.return_value = 5

        mock_redis = Mock()
        mock_redis.health_check.return_value = True

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.fallback_buffer", mock_fallback):
                with patch("apps.signal_service.main.redis_client", mock_redis):
                    with patch("apps.signal_service.main.signals_replayed_total"):
                        with patch("apps.signal_service.main.redis_fallback_buffer_size"):
                            task = asyncio.create_task(redis_fallback_replay_task())
                            await asyncio.sleep(0.05)
                            task.cancel()

                            with pytest.raises(asyncio.CancelledError):
                                await task

    async def test_redis_fallback_replay_reconnect_attempt(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test replay task attempts reconnection when Redis is None."""
        mock_settings.redis_enabled = True
        mock_settings.redis_fallback_replay_interval_seconds = 0.01

        mock_fallback = Mock()

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.fallback_buffer", mock_fallback):
                with patch("apps.signal_service.main.redis_client", None):
                    with patch(
                        "apps.signal_service.main._attempt_redis_reconnect", return_value=False
                    ):
                        task = asyncio.create_task(redis_fallback_replay_task())
                        await asyncio.sleep(0.05)
                        task.cancel()

                        with pytest.raises(asyncio.CancelledError):
                            await task


class TestShouldHydrateFeatures:
    """Test _should_hydrate_features() helper."""

    def test_should_hydrate_features_all_conditions_met(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test returns True when all conditions met."""
        mock_settings.feature_hydration_enabled = True

        mock_feature_cache = Mock()
        mock_generator = Mock()
        mock_registry = Mock()
        mock_registry.is_loaded = True

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.feature_cache", mock_feature_cache):
                with patch("apps.signal_service.main.signal_generator", mock_generator):
                    with patch("apps.signal_service.main.model_registry", mock_registry):
                        result = _should_hydrate_features()

        assert result is True

    def test_should_hydrate_features_disabled(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test returns False when hydration disabled."""
        mock_settings.feature_hydration_enabled = False

        with patch("apps.signal_service.main.settings", mock_settings):
            result = _should_hydrate_features()

        assert result is False


class TestShadowValidationHelpers:
    """Test shadow validation helper functions."""

    def test_record_shadow_validation_with_result(self) -> None:
        """Test _record_shadow_validation records metrics."""
        result = ShadowValidationResult(
            passed=True,
            correlation=0.95,
            mean_abs_diff_ratio=0.02,
            sign_change_rate=0.01,
            sample_count=100,
            old_range=1.5,
            new_range=1.6,
            message="Validation passed",
        )

        with patch("apps.signal_service.main.shadow_validation_total") as mock_counter:
            with patch("apps.signal_service.main.shadow_validation_correlation"):
                _record_shadow_validation(result, "passed")

                mock_counter.labels.assert_called_with(status="passed")

    def test_record_shadow_validation_none_result(self) -> None:
        """Test _record_shadow_validation with None result."""
        with patch("apps.signal_service.main.shadow_validation_total") as mock_counter:
            _record_shadow_validation(None, "skipped")

            mock_counter.labels.assert_called_with(status="skipped")

    def test_record_shadow_skip_if_bypassed_not_reloaded(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _record_shadow_skip_if_bypassed does nothing when not reloaded."""
        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main._record_shadow_validation") as mock_record:
                _record_shadow_skip_if_bypassed(reloaded=False)

                mock_record.assert_not_called()

    def test_record_shadow_skip_if_bypassed_validation_disabled(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _record_shadow_skip_if_bypassed records skip when validation disabled."""
        mock_settings.shadow_validation_enabled = False

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main._record_shadow_validation") as mock_record:
                _record_shadow_skip_if_bypassed(reloaded=True)

                mock_record.assert_called_with(None, "skipped")

    def test_on_model_activated(self) -> None:
        """Test _on_model_activated updates metrics."""
        metadata = ModelMetadata(
            id=1,
            strategy_name="alpha_baseline",
            version="v1.0.0",
            mlflow_run_id=None,
            mlflow_experiment_id=None,
            status="active",
            model_path="/path/to/model",
            activated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            performance_metrics={},
            config={},
        )

        with patch("apps.signal_service.main.model_version_info"):
            with patch("apps.signal_service.main.model_loaded_status") as mock_status:
                with patch("apps.signal_service.main.model_reload_total"):
                    _on_model_activated(metadata)

                    mock_status.set.assert_called_with(1)

    def test_schedule_shadow_validation(self) -> None:
        """Test _schedule_shadow_validation schedules task."""
        mock_task = Mock()

        with patch("asyncio.create_task") as mock_create:
            _schedule_shadow_validation(mock_task)

            mock_create.assert_called_once()

    def test_shadow_validate_success(self) -> None:
        """Test _shadow_validate with successful validation."""
        mock_validator = Mock()
        mock_result = ShadowValidationResult(
            passed=True,
            correlation=0.95,
            mean_abs_diff_ratio=0.02,
            sign_change_rate=0.01,
            sample_count=100,
            old_range=1.5,
            new_range=1.6,
            message="Validation passed",
        )
        mock_validator.validate.return_value = mock_result

        with patch("apps.signal_service.main.shadow_validator", mock_validator):
            with patch("apps.signal_service.main._record_shadow_validation"):
                result = _shadow_validate(old_model=Mock(), new_model=Mock())

        assert result == mock_result

    def test_shadow_validate_value_error(self) -> None:
        """Test _shadow_validate handles ValueError."""
        mock_validator = Mock()
        mock_validator.validate.side_effect = ValueError("Invalid data")

        with patch("apps.signal_service.main.shadow_validator", mock_validator):
            with patch("apps.signal_service.main._record_shadow_validation"):
                with pytest.raises(ValueError, match="Invalid data"):
                    _shadow_validate(old_model=Mock(), new_model=Mock())

    def test_shadow_validate_file_not_found(self) -> None:
        """Test _shadow_validate handles FileNotFoundError."""
        mock_validator = Mock()
        mock_validator.validate.side_effect = FileNotFoundError("Data missing")

        with patch("apps.signal_service.main.shadow_validator", mock_validator):
            with patch("apps.signal_service.main._record_shadow_validation"):
                with pytest.raises(FileNotFoundError):
                    _shadow_validate(old_model=Mock(), new_model=Mock())

    def test_shadow_validate_runtime_error(self) -> None:
        """Test _shadow_validate handles RuntimeError."""
        mock_validator = Mock()
        mock_validator.validate.side_effect = RuntimeError("Validation failed")

        with patch("apps.signal_service.main.shadow_validator", mock_validator):
            with patch("apps.signal_service.main._record_shadow_validation"):
                with pytest.raises(RuntimeError):
                    _shadow_validate(old_model=Mock(), new_model=Mock())


class TestRedisFallbackHelpers:
    """Test Redis fallback helper functions."""

    def test_record_fallback_buffer_metrics(self) -> None:
        """Test _record_fallback_buffer_metrics records metrics."""
        with patch("apps.signal_service.main.signals_buffered_total") as mock_buffered:
            with patch("apps.signal_service.main.signals_dropped_total") as mock_dropped:
                with patch("apps.signal_service.main.redis_fallback_buffer_size") as mock_size:
                    _record_fallback_buffer_metrics(buffered=5, dropped=2, size=10)

                    mock_buffered.inc.assert_called_with(5)
                    mock_dropped.inc.assert_called_with(2)
                    mock_size.set.assert_called_with(10)

    def test_buffer_signal_payload_no_fallback_buffer(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _buffer_signal_payload when fallback_buffer is None."""
        with patch("apps.signal_service.main.fallback_buffer", None):
            _buffer_signal_payload(payload='{"test": "data"}', reason="Redis down")

            # Should log warning but not crash

    def test_buffer_signal_payload_success(self) -> None:
        """Test _buffer_signal_payload buffers message."""
        mock_buffer = Mock()
        mock_outcome = Mock()
        mock_outcome.buffered = 1
        mock_outcome.dropped = 0
        mock_outcome.size = 5
        mock_buffer.buffer_message.return_value = mock_outcome

        with patch("apps.signal_service.main.fallback_buffer", mock_buffer):
            with patch("apps.signal_service.main._record_fallback_buffer_metrics"):
                _buffer_signal_payload(payload='{"test": "data"}', reason="Redis down")

                mock_buffer.buffer_message.assert_called_once()

    def test_publish_signal_event_with_fallback_redis_disabled(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _publish_signal_event_with_fallback when Redis disabled."""
        mock_settings.redis_enabled = False

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2024-12-31",
        )

        with patch("apps.signal_service.main.settings", mock_settings):
            _publish_signal_event_with_fallback(event)

            # Should return immediately without publishing

    def test_publish_signal_event_with_fallback_serialization_error(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _publish_signal_event_with_fallback handles serialization error."""
        mock_settings.redis_enabled = True

        # Create event that will fail serialization (mock model_dump_json)
        event = Mock()
        event.model_dump_json.side_effect = TypeError("Cannot serialize")

        with patch("apps.signal_service.main.settings", mock_settings):
            _publish_signal_event_with_fallback(event)

            # Should log error but not crash

    def test_publish_signal_event_with_fallback_publisher_none(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _publish_signal_event_with_fallback when event_publisher is None."""
        mock_settings.redis_enabled = True

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2024-12-31",
        )

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.event_publisher", None):
                with patch("apps.signal_service.main._buffer_signal_payload") as mock_buffer:
                    _publish_signal_event_with_fallback(event)

                    mock_buffer.assert_called_once()

    def test_publish_signal_event_with_fallback_publish_error(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _publish_signal_event_with_fallback handles publish ValueError."""
        mock_settings.redis_enabled = True

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2024-12-31",
        )

        mock_publisher = Mock()
        mock_publisher.publish_signal_event.side_effect = ValueError("Serialization error")

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.event_publisher", mock_publisher):
                _publish_signal_event_with_fallback(event)

                # Should log error but not crash

    def test_publish_signal_event_with_fallback_publish_returns_none(
        self,
        mock_settings: Mock,
    ) -> None:
        """Test _publish_signal_event_with_fallback buffers when publish returns None."""
        mock_settings.redis_enabled = True

        event = SignalEvent(
            timestamp=datetime.now(UTC),
            strategy_id="alpha_baseline",
            symbols=["AAPL"],
            num_signals=1,
            as_of_date="2024-12-31",
        )

        mock_publisher = Mock()
        mock_publisher.publish_signal_event.return_value = None  # Redis error

        with patch("apps.signal_service.main.settings", mock_settings):
            with patch("apps.signal_service.main.event_publisher", mock_publisher):
                with patch("apps.signal_service.main._buffer_signal_payload") as mock_buffer:
                    _publish_signal_event_with_fallback(event)

                    mock_buffer.assert_called_once()
