"""Tests for OAuth2 state storage with single-use enforcement.

Tests verify Redis-backed state storage with CSRF and replay attack protection.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.web_console.auth.oauth2_state import OAuth2State, OAuth2StateStore


@pytest.fixture()
def mock_redis():
    """Mock Redis async client."""
    redis = AsyncMock()
    # Mock pipeline - create a proper async context manager
    pipeline = AsyncMock()
    pipeline.get = AsyncMock()
    pipeline.delete = AsyncMock()
    pipeline.execute = AsyncMock()

    # Make pipeline callable and return itself as context manager
    async def async_pipeline_context():
        return pipeline

    pipeline_cm = AsyncMock()
    pipeline_cm.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline_cm.__aexit__ = AsyncMock(return_value=None)

    redis.pipeline = MagicMock(return_value=pipeline_cm)
    return redis


@pytest.fixture()
def oauth2_state():
    """Sample OAuth2 state for testing."""
    return OAuth2State(
        state="test-state-123",
        code_verifier="test-verifier-456",
        nonce="test-nonce-789",
        code_challenge="test-challenge-abc",
        redirect_uri="https://example.com/callback",
        created_at=datetime.now(UTC),
    )


class TestOAuth2State:
    """Test OAuth2State Pydantic model."""

    def test_oauth2_state_model_creation(self, oauth2_state):
        """Test OAuth2State model can be created with all fields."""
        assert oauth2_state.state == "test-state-123"
        assert oauth2_state.code_verifier == "test-verifier-456"
        assert oauth2_state.nonce == "test-nonce-789"
        assert oauth2_state.code_challenge == "test-challenge-abc"
        assert oauth2_state.redirect_uri == "https://example.com/callback"
        assert isinstance(oauth2_state.created_at, datetime)

    def test_oauth2_state_serialization(self, oauth2_state):
        """Test OAuth2State can be serialized to JSON."""
        json_str = oauth2_state.model_dump_json()
        assert isinstance(json_str, str)

        # Verify JSON contains expected fields
        data = json.loads(json_str)
        assert data["state"] == "test-state-123"
        assert data["code_verifier"] == "test-verifier-456"
        assert data["nonce"] == "test-nonce-789"

    def test_oauth2_state_deserialization(self, oauth2_state):
        """Test OAuth2State can be deserialized from JSON."""
        json_str = oauth2_state.model_dump_json()
        restored = OAuth2State.model_validate_json(json_str)

        assert restored.state == oauth2_state.state
        assert restored.code_verifier == oauth2_state.code_verifier
        assert restored.nonce == oauth2_state.nonce
        assert restored.code_challenge == oauth2_state.code_challenge
        assert restored.redirect_uri == oauth2_state.redirect_uri


class TestOAuth2StateStore:
    """Test OAuth2StateStore Redis operations."""

    def test_state_store_initialization(self, mock_redis):
        """Test OAuth2StateStore initializes with correct defaults."""
        store = OAuth2StateStore(redis_client=mock_redis)

        assert store.redis == mock_redis
        assert store.ttl_seconds == 600  # 10 minutes default

    def test_state_store_custom_ttl(self, mock_redis):
        """Test OAuth2StateStore accepts custom TTL."""
        store = OAuth2StateStore(redis_client=mock_redis, ttl_seconds=300)

        assert store.ttl_seconds == 300

    @pytest.mark.asyncio()
    async def test_store_state_sets_redis_key_with_ttl(self, mock_redis, oauth2_state):
        """Test store_state sets Redis key with correct TTL."""
        store = OAuth2StateStore(redis_client=mock_redis, ttl_seconds=600)

        await store.store_state(oauth2_state)

        # Verify Redis setex called with correct arguments
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]

        assert call_args[0] == "oauth_state:test-state-123"  # Key
        assert call_args[1] == 600  # TTL
        # call_args[2] is JSON value, verify it's valid JSON
        json.loads(call_args[2])

    @pytest.mark.asyncio()
    async def test_get_and_delete_state_returns_state_on_success(self, mock_redis, oauth2_state):
        """Test get_and_delete_state retrieves and deletes state atomically."""
        store = OAuth2StateStore(redis_client=mock_redis)

        # Set up pipeline mock to return state data
        pipeline_cm = mock_redis.pipeline.return_value
        pipeline = await pipeline_cm.__aenter__()
        pipeline.execute.return_value = [
            oauth2_state.model_dump_json().encode("utf-8"),  # GET result
            1,  # DELETE result (key existed)
        ]

        result = await store.get_and_delete_state("test-state-123")

        # Verify pipeline operations
        pipeline.get.assert_called_once_with("oauth_state:test-state-123")
        pipeline.delete.assert_called_once_with("oauth_state:test-state-123")

        # Verify returned state matches
        assert result is not None
        assert result.state == oauth2_state.state
        assert result.code_verifier == oauth2_state.code_verifier
        assert result.nonce == oauth2_state.nonce

    @pytest.mark.asyncio()
    async def test_get_and_delete_state_returns_none_if_not_found(self, mock_redis):
        """Test get_and_delete_state returns None if state doesn't exist."""
        store = OAuth2StateStore(redis_client=mock_redis)

        # Set up pipeline mock to return None (state not found)
        pipeline_cm = mock_redis.pipeline.return_value
        pipeline = await pipeline_cm.__aenter__()
        pipeline.execute.return_value = [None, 0]  # GET=None, DELETE=0

        result = await store.get_and_delete_state("nonexistent-state")

        assert result is None

    @pytest.mark.asyncio()
    async def test_get_and_delete_state_enforces_single_use(self, mock_redis, oauth2_state):
        """Test that second get_and_delete_state call returns None (single-use)."""
        store = OAuth2StateStore(redis_client=mock_redis)

        # First call: state exists
        pipeline_cm = mock_redis.pipeline.return_value
        pipeline = await pipeline_cm.__aenter__()
        pipeline.execute.return_value = [
            oauth2_state.model_dump_json().encode("utf-8"),
            1,
        ]

        first_result = await store.get_and_delete_state("test-state-123")
        assert first_result is not None

        # Second call: state already deleted (simulates replay attack)
        pipeline.execute.return_value = [None, 0]

        second_result = await store.get_and_delete_state("test-state-123")
        assert second_result is None

    @pytest.mark.asyncio()
    async def test_get_and_delete_uses_atomic_pipeline(self, mock_redis, oauth2_state):
        """Test get_and_delete_state uses Redis pipeline for atomicity."""
        store = OAuth2StateStore(redis_client=mock_redis)

        pipeline_cm = mock_redis.pipeline.return_value
        pipeline = await pipeline_cm.__aenter__()
        pipeline.execute.return_value = [
            oauth2_state.model_dump_json().encode("utf-8"),
            1,
        ]

        await store.get_and_delete_state("test-state-123")

        # Verify pipeline was created and used
        mock_redis.pipeline.assert_called_once()
        pipeline.get.assert_called_once()
        pipeline.delete.assert_called_once()
        pipeline.execute.assert_called_once()
