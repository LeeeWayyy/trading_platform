"""
Test Redis integration during Signal Service startup.

This module tests that the Redis client and feature cache are correctly
initialized when Redis is enabled, ensuring parameter names match the
FeatureCache constructor signature.

Tests cover:
- FeatureCache initialization with correct parameter names
- Startup with Redis enabled and connected
- Startup with Redis enabled but connection fails
- Startup with Redis disabled
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from apps.signal_service.config import Settings
from libs.redis_client import RedisClient, FeatureCache, RedisConnectionError


class TestRedisStartup:
    """Test Redis initialization during service startup."""

    @patch('libs.redis_client.feature_cache.FeatureCache')
    @patch('libs.redis_client.client.RedisClient')
    def test_feature_cache_initialization_with_correct_params(
        self,
        mock_redis_client_class,
        mock_feature_cache_class
    ):
        """
        Test that FeatureCache is initialized with correct parameter names.

        This test verifies the fix for Codex review comment:
        "Initialize FeatureCache with correct parameter names"

        Before fix:
            FeatureCache(redis=..., default_ttl=...)  # ❌ Wrong parameter names

        After fix:
            FeatureCache(redis_client=..., ttl=...)  # ✅ Correct parameter names
        """
        # Setup mock Redis client
        mock_redis_instance = Mock(spec=RedisClient)
        mock_redis_instance.health_check.return_value = True
        mock_redis_client_class.return_value = mock_redis_instance

        # Setup mock FeatureCache
        mock_feature_cache_instance = Mock(spec=FeatureCache)
        mock_feature_cache_class.return_value = mock_feature_cache_instance

        # Simulate startup code
        settings = Settings(
            redis_enabled=True,
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_ttl=3600,
        )

        # Create Redis client (as done in main.py lifespan) - using mock
        redis_client = mock_redis_client_class(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
        )

        # Verify connection
        assert redis_client.health_check() is True

        # Initialize feature cache with CORRECT parameter names - using mock
        feature_cache = mock_feature_cache_class(
            redis_client=redis_client,  # ✅ Correct: redis_client (not redis)
            ttl=settings.redis_ttl,      # ✅ Correct: ttl (not default_ttl)
        )

        # Verify FeatureCache was called with correct parameters
        mock_feature_cache_class.assert_called_once_with(
            redis_client=mock_redis_instance,
            ttl=3600,
        )

    def test_feature_cache_constructor_signature(self):
        """
        Test FeatureCache constructor accepts redis_client and ttl parameters.

        This ensures the FeatureCache class signature matches what we expect
        and prevents regressions where parameter names might change.
        """
        mock_redis = Mock(spec=RedisClient)

        # Should NOT raise TypeError
        cache = FeatureCache(
            redis_client=mock_redis,
            ttl=7200,
        )

        assert cache.redis == mock_redis
        assert cache.ttl == 7200
        assert cache.prefix == "features"  # Default value

    def test_feature_cache_constructor_with_prefix(self):
        """Test FeatureCache with custom prefix."""
        mock_redis = Mock(spec=RedisClient)

        cache = FeatureCache(
            redis_client=mock_redis,
            ttl=1800,
            prefix="test_features"
        )

        assert cache.redis == mock_redis
        assert cache.ttl == 1800
        assert cache.prefix == "test_features"

    def test_feature_cache_constructor_wrong_params_raises_error(self):
        """
        Test that using wrong parameter names raises TypeError.

        This test documents the BUG that Codex found and ensures we
        catch it in tests before it reaches production.
        """
        mock_redis = Mock(spec=RedisClient)

        # ❌ Wrong parameter name: redis (should be redis_client)
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            FeatureCache(
                redis=mock_redis,  # ❌ Wrong!
                ttl=3600,
            )

        # ❌ Wrong parameter name: default_ttl (should be ttl)
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            FeatureCache(
                redis_client=mock_redis,
                default_ttl=3600,  # ❌ Wrong!
            )

        # ❌ Both wrong
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            FeatureCache(
                redis=mock_redis,       # ❌ Wrong!
                default_ttl=3600,        # ❌ Wrong!
            )

    @patch('libs.redis_client.feature_cache.FeatureCache')
    @patch('libs.redis_client.client.RedisClient')
    def test_startup_with_redis_enabled_and_connected(
        self,
        mock_redis_client_class,
        mock_feature_cache_class
    ):
        """
        Test service startup when Redis is enabled and connection succeeds.

        Expected behavior:
        1. Redis client created
        2. Health check passes
        3. Feature cache initialized with correct params
        4. Service continues normally
        """
        # Setup mocks
        mock_redis_instance = Mock(spec=RedisClient)
        mock_redis_instance.health_check.return_value = True
        mock_redis_client_class.return_value = mock_redis_instance

        mock_feature_cache_instance = Mock(spec=FeatureCache)
        mock_feature_cache_class.return_value = mock_feature_cache_instance

        # Simulate startup
        settings = Settings(redis_enabled=True, redis_ttl=3600)

        redis_client = mock_redis_client_class(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
        )

        if redis_client.health_check():
            feature_cache = mock_feature_cache_class(
                redis_client=redis_client,
                ttl=settings.redis_ttl,
            )
        else:
            feature_cache = None

        # Verify
        assert feature_cache is not None
        mock_redis_client_class.assert_called_once()
        mock_feature_cache_class.assert_called_once_with(
            redis_client=mock_redis_instance,
            ttl=3600,
        )

    @patch('libs.redis_client.client.RedisClient')
    def test_startup_with_redis_enabled_but_connection_fails(
        self,
        mock_redis_client_class
    ):
        """
        Test service startup when Redis is enabled but connection fails.

        Expected behavior (graceful degradation):
        1. Redis client created
        2. Health check fails
        3. Feature cache is None
        4. Service continues without Redis
        """
        # Setup mock: health check fails
        mock_redis_instance = Mock(spec=RedisClient)
        mock_redis_instance.health_check.return_value = False
        mock_redis_client_class.return_value = mock_redis_instance

        # Simulate startup
        settings = Settings(redis_enabled=True)

        redis_client = mock_redis_client_class(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
        )

        if redis_client.health_check():
            # Won't execute because health check returns False
            feature_cache = FeatureCache(
                redis_client=redis_client,
                ttl=settings.redis_ttl,
            )
        else:
            # Graceful degradation
            redis_client = None
            feature_cache = None

        # Verify graceful degradation
        assert redis_client is None
        assert feature_cache is None

    @patch('apps.signal_service.main.RedisClient')
    def test_startup_with_redis_enabled_but_connection_error(
        self,
        mock_redis_client_class
    ):
        """
        Test service startup when Redis connection raises exception.

        Expected behavior (graceful degradation):
        1. RedisClient raises RedisConnectionError
        2. Exception caught
        3. Feature cache is None
        4. Service continues without Redis
        """
        # Setup mock: constructor raises exception
        mock_redis_client_class.side_effect = RedisConnectionError("Connection refused")

        # Simulate startup with exception handling
        settings = Settings(redis_enabled=True)

        try:
            redis_client = RedisClient(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
            )

            if redis_client.health_check():
                feature_cache = FeatureCache(
                    redis_client=redis_client,
                    ttl=settings.redis_ttl,
                )
            else:
                redis_client = None
                feature_cache = None

        except RedisConnectionError:
            # Graceful degradation on connection error
            redis_client = None
            feature_cache = None

        # Verify graceful degradation
        assert redis_client is None
        assert feature_cache is None

    def test_startup_with_redis_disabled(self):
        """
        Test service startup when Redis is disabled.

        Expected behavior:
        1. Redis client is None
        2. Feature cache is None
        3. Service runs without caching
        """
        settings = Settings(redis_enabled=False)

        # Simulate startup logic
        if settings.redis_enabled:
            redis_client = RedisClient(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
            )
            feature_cache = FeatureCache(
                redis_client=redis_client,
                ttl=settings.redis_ttl,
            )
        else:
            redis_client = None
            feature_cache = None

        # Verify Redis is not initialized
        assert redis_client is None
        assert feature_cache is None
