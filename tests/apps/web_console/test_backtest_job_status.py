"""Tests for backtest job status and queue integration.

Tests job submission, status tracking, and Redis progress integration.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


class TestJobStatusValidation:
    """Tests for job status validation."""

    def test_valid_statuses_match_database(self) -> None:
        """Verify VALID_STATUSES matches database schema."""
        from apps.web_console.pages.backtest import VALID_STATUSES

        # These are the only valid statuses in backtest_jobs table
        expected = {"pending", "running", "completed", "failed", "cancelled"}
        assert VALID_STATUSES == expected

    def test_get_user_jobs_rejects_invalid_status(self) -> None:
        """Verify invalid status raises ValueError."""
        from apps.web_console.pages.backtest import get_user_jobs

        with pytest.raises(ValueError, match="Invalid statuses"):
            # Mock the pool to prevent actual DB call
            with patch("apps.web_console.pages.backtest.get_sync_db_pool"):
                get_user_jobs("user1", ["invalid_status"])

    def test_get_user_jobs_rejects_rq_vocabulary(self) -> None:
        """Verify RQ status vocabulary is rejected."""
        from apps.web_console.pages.backtest import get_user_jobs

        rq_statuses = ["queued", "started", "finished", "deferred"]

        for status in rq_statuses:
            with pytest.raises(ValueError, match="Invalid statuses"):
                with patch("apps.web_console.pages.backtest.get_sync_db_pool"):
                    get_user_jobs("user1", [status])


class TestJobSubmission:
    """Tests for job submission flow."""

    def test_submit_backtest_job_uses_job_queue(self) -> None:
        """Verify job submission goes through BacktestJobQueue."""
        from apps.web_console.pages.backtest import submit_backtest_job
        from libs.backtest.job_queue import BacktestJobConfig, JobPriority

        config = BacktestJobConfig(
            alpha_name="momentum",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            weight_method="zscore",
        )

        mock_job = MagicMock()
        mock_job.id = "job123"

        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = mock_job

        with patch(
            "apps.web_console.pages.backtest.get_job_queue"
        ) as mock_get_queue:
            mock_get_queue.return_value.__enter__.return_value = mock_queue
            mock_get_queue.return_value.__exit__.return_value = None

            job = submit_backtest_job(config, JobPriority.NORMAL, "test_user")

            assert job.id == "job123"
            mock_queue.enqueue.assert_called_once_with(
                config,
                priority=JobPriority.NORMAL,
                created_by="test_user",
            )

    def test_job_priority_enum_validation(self) -> None:
        """Verify JobPriority enum values are correct."""
        from libs.backtest.job_queue import JobPriority

        assert JobPriority("normal") == JobPriority.NORMAL
        assert JobPriority("high") == JobPriority.HIGH
        assert JobPriority("low") == JobPriority.LOW

        with pytest.raises(ValueError, match="invalid"):
            JobPriority("invalid")


class TestProgressTracking:
    """Tests for Redis progress tracking integration."""

    def test_progress_fetched_from_redis(self) -> None:
        """Verify progress is fetched from Redis using MGET batch call."""
        import json

        from apps.web_console.pages.backtest import get_user_jobs

        # Mock database results
        mock_jobs = [
            {
                "job_id": "job1",
                "alpha_name": "momentum",
                "start_date": date(2023, 1, 1),
                "end_date": date(2023, 12, 31),
                "status": "running",
                "created_at": None,
                "error_message": None,
                "mean_ic": None,
                "icir": None,
                "hit_rate": None,
                "coverage": None,
                "average_turnover": None,
            }
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_jobs
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)

        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        mock_redis = MagicMock()
        # Redis MGET returns list of progress values as JSON bytes
        mock_redis.mget.return_value = [json.dumps({"pct": 75}).encode()]

        with patch("apps.web_console.pages.backtest.get_sync_db_pool", return_value=mock_pool):
            with patch(
                "apps.web_console.pages.backtest.get_sync_redis_client",
                return_value=mock_redis,
            ):
                jobs = get_user_jobs("user1", ["running"])

        assert len(jobs) == 1
        assert jobs[0]["progress_pct"] == 75
        mock_redis.mget.assert_called_with(["backtest:progress:job1"])

    def test_missing_progress_defaults_to_zero(self) -> None:
        """Verify missing Redis progress defaults to 0%."""
        from apps.web_console.pages.backtest import get_user_jobs

        mock_jobs = [
            {
                "job_id": "job1",
                "alpha_name": "momentum",
                "start_date": date(2023, 1, 1),
                "end_date": date(2023, 12, 31),
                "status": "pending",
                "created_at": None,
                "error_message": None,
                "mean_ic": None,
                "icir": None,
                "hit_rate": None,
                "coverage": None,
                "average_turnover": None,
            }
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = mock_jobs
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)

        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        mock_redis = MagicMock()
        mock_redis.mget.return_value = [None]  # No progress in Redis (MGET returns list)

        with patch("apps.web_console.pages.backtest.get_sync_db_pool", return_value=mock_pool):
            with patch(
                "apps.web_console.pages.backtest.get_sync_redis_client",
                return_value=mock_redis,
            ):
                jobs = get_user_jobs("user1", ["pending"])

        assert jobs[0]["progress_pct"] == 0


class TestSyncPoolIntegration:
    """Tests for sync DB pool integration."""

    def test_sync_pool_is_cached(self) -> None:
        """Verify sync pool uses st.cache_resource for caching."""
        from apps.web_console.utils.sync_db_pool import get_sync_db_pool

        # Verify the function has cache_resource decorator attributes
        # (Streamlit decorators add __wrapped__ and _cached properties)
        assert hasattr(get_sync_db_pool, "clear")  # cache_resource has clear method

    def test_redis_url_builder_uses_env(self) -> None:
        """Verify Redis URL is built from environment variables."""
        from apps.web_console.utils.sync_db_pool import _get_redis_url

        # With REDIS_URL set
        with patch.dict("os.environ", {"REDIS_URL": "redis://custom:1234/5"}):
            url = _get_redis_url()
            assert url == "redis://custom:1234/5"

        # With individual vars (fallback)
        with patch.dict(
            "os.environ",
            {"REDIS_HOST": "myhost", "REDIS_PORT": "6380", "REDIS_DB": "2"},
            clear=True,
        ):
            url = _get_redis_url()
            assert url == "redis://myhost:6380/2"

    def test_redis_url_defaults_to_container_name(self) -> None:
        """Verify Redis URL defaults to 'redis' hostname for containers."""
        from apps.web_console.utils.sync_db_pool import _get_redis_url

        # With no env vars
        with patch.dict("os.environ", {}, clear=True):
            url = _get_redis_url()
            # Should use 'redis' (docker service name) not 'localhost'
            assert url == "redis://redis:6379/0"


class TestFeatureFlag:
    """Tests for backtest manager feature flag."""

    def test_feature_flag_env_parsing(self) -> None:
        """Verify feature flag parses various true values."""
        import importlib

        true_values = ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"]
        false_values = ["0", "false", "False", "no", "off", ""]

        for val in true_values:
            with patch.dict("os.environ", {"FEATURE_BACKTEST_MANAGER": val}):
                import apps.web_console.config as config_module

                importlib.reload(config_module)
                assert config_module.FEATURE_BACKTEST_MANAGER is True, f"Failed for {val}"

        for val in false_values:
            with patch.dict("os.environ", {"FEATURE_BACKTEST_MANAGER": val}):
                import apps.web_console.config as config_module

                importlib.reload(config_module)
                assert config_module.FEATURE_BACKTEST_MANAGER is False, f"Failed for {val}"
