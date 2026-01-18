"""Unit tests for orchestrator HTTP clients and auth helpers.

This module provides comprehensive test coverage for:
- Service-to-service authentication (S2S) helpers
- SignalServiceClient for fetching trading signals
- ExecutionGatewayClient for order submission and queries
- HTTP client initialization and lifecycle
- Request/response handling and error scenarios
- Connection pooling and retry behavior
- Edge cases and security scenarios

Target: 85%+ branch coverage with focus on:
- Auth header generation with HMAC signatures
- API client methods with various parameters
- Error handling (timeouts, HTTP errors, network errors)
- Client lifecycle (initialization, close)
- Retry behavior for transient failures
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest

from apps.orchestrator import clients as clients_mod
from apps.orchestrator.schemas import OrderRequest, SignalServiceResponse


class TestServiceSecret:
    def test_get_service_secret_prefers_per_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET_ORCHESTRATOR", "per-service")
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "global")

        assert clients_mod._get_service_secret() == "per-service"

    def test_get_service_secret_falls_back_to_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INTERNAL_TOKEN_SECRET_ORCHESTRATOR", raising=False)
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "global")

        assert clients_mod._get_service_secret() == "global"


class TestInternalAuthHeaders:
    def test_raises_when_secret_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INTERNAL_TOKEN_SECRET_ORCHESTRATOR", raising=False)
        monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
        monkeypatch.delenv("S2S_AUTH_OPTIONAL", raising=False)

        with pytest.raises(RuntimeError, match="INTERNAL_TOKEN_SECRET is required"):
            clients_mod._get_internal_auth_headers("GET", "/health")

    def test_optional_returns_empty_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INTERNAL_TOKEN_SECRET_ORCHESTRATOR", raising=False)
        monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
        monkeypatch.setenv("S2S_AUTH_OPTIONAL", "true")

        assert clients_mod._get_internal_auth_headers("GET", "/health") == {}

    def test_deterministic_signature_and_body_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")
        monkeypatch.delenv("S2S_AUTH_OPTIONAL", raising=False)

        fixed_uuid = UUID("12345678-1234-5678-1234-567812345678")
        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch("apps.orchestrator.clients.uuid.uuid4", return_value=fixed_uuid),
        ):
            headers = clients_mod._get_internal_auth_headers(
                "POST",
                "/api/v1/orders",
                query="a=1",
                body=b'{"x":1}',
                user_id="user-1",
                strategy_id="strat-9",
            )

        expected_body_hash = hashlib.sha256(b'{"x":1}').hexdigest()
        payload_dict = {
            "service_id": "orchestrator",
            "method": "POST",
            "path": "/api/v1/orders",
            "query": "a=1",
            "timestamp": str(1700000000),
            "nonce": str(fixed_uuid),
            "user_id": "user-1",
            "strategy_id": "strat-9",
            "body_hash": expected_body_hash,
        }
        payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
        expected_signature = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()

        assert headers["X-Internal-Token"] == expected_signature
        assert headers["X-Internal-Timestamp"] == "1700000000"
        assert headers["X-Internal-Nonce"] == str(fixed_uuid)
        assert headers["X-Service-ID"] == "orchestrator"
        assert headers["X-Body-Hash"] == expected_body_hash
        assert headers["X-User-ID"] == "user-1"
        assert headers["X-Strategy-ID"] == "strat-9"


class TestSignalServiceClient:
    @pytest.mark.asyncio()
    async def test_fetch_signals_success(self) -> None:
        response_payload = {
            "signals": [
                {"symbol": "AAPL", "predicted_return": 0.12, "rank": 1, "target_weight": 0.5}
            ],
            "metadata": {
                "as_of_date": "2024-12-31",
                "model_version": "v1",
                "strategy": "alpha",
                "num_signals": 1,
                "generated_at": "2024-12-31T00:00:00Z",
                "top_n": 1,
                "bottom_n": 0,
            },
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_payload

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ) as headers_mock,
        ):
            client = clients_mod.SignalServiceClient("http://signals")
            result = await client.fetch_signals(
                symbols=["AAPL"],
                as_of_date=date(2024, 12, 31),
                top_n=1,
                bottom_n=0,
            )

        assert isinstance(result, SignalServiceResponse)
        assert result.signals[0].symbol == "AAPL"
        headers_mock.assert_called_once()
        client_instance.post.assert_called_once()
        _, kwargs = client_instance.post.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["content"].startswith(b"{")

    @pytest.mark.asyncio()
    async def test_fetch_signals_raises_for_non_200(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "boom"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(status_code=500)
        )

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.SignalServiceClient("http://signals")
            with pytest.raises(httpx.HTTPStatusError):
                await client.fetch_signals(symbols=["AAPL"])

    @pytest.mark.asyncio()
    async def test_health_check_connect_timeout_returns_false(self) -> None:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.SignalServiceClient("http://signals")
            assert await client.health_check() is False


class TestExecutionGatewayClient:
    @pytest.mark.asyncio()
    async def test_submit_order_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "client_order_id": "abc",
            "status": "accepted",
            "broker_order_id": "brk-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": None,
            "created_at": "2024-12-31T00:00:00Z",
            "message": "ok",
        }

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ) as headers_mock,
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
            result = await client.submit_order(order)

        assert result.client_order_id == "abc"
        headers_mock.assert_called_once()
        client_instance.post.assert_called_once()

    @pytest.mark.asyncio()
    async def test_get_order_returns_json(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"client_order_id": "abc"}

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.get_order("abc")

        assert result["client_order_id"] == "abc"
        client_instance.get.assert_called_once()

    @pytest.mark.asyncio()
    async def test_get_positions_returns_json(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"positions": []}

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.get_positions()

        assert result["positions"] == []
        client_instance.get.assert_called_once()


# ==============================================================================
# Extended Coverage Tests
# ==============================================================================


class TestInternalAuthHeadersExtended:
    """Extended tests for _get_internal_auth_headers covering edge cases."""

    def test_empty_body_generates_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that empty body (None) generates valid hash."""
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch(
                "apps.orchestrator.clients.uuid.uuid4",
                return_value=UUID("12345678-1234-5678-1234-567812345678"),
            ),
        ):
            headers = clients_mod._get_internal_auth_headers("GET", "/health", body=None)

        # Empty body should hash to empty bytes
        expected_body_hash = hashlib.sha256(b"").hexdigest()
        assert headers["X-Body-Hash"] == expected_body_hash

    def test_string_body_is_encoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that string body is properly encoded before hashing."""
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

        body_str = '{"test": "value"}'
        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch(
                "apps.orchestrator.clients.uuid.uuid4",
                return_value=UUID("12345678-1234-5678-1234-567812345678"),
            ),
        ):
            headers = clients_mod._get_internal_auth_headers("POST", "/api/test", body=body_str)

        expected_body_hash = hashlib.sha256(body_str.encode()).hexdigest()
        assert headers["X-Body-Hash"] == expected_body_hash

    def test_bytes_body_is_hashed_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that bytes body is hashed without encoding."""
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

        body_bytes = b'{"test": "value"}'
        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch(
                "apps.orchestrator.clients.uuid.uuid4",
                return_value=UUID("12345678-1234-5678-1234-567812345678"),
            ),
        ):
            headers = clients_mod._get_internal_auth_headers("POST", "/api/test", body=body_bytes)

        expected_body_hash = hashlib.sha256(body_bytes).hexdigest()
        assert headers["X-Body-Hash"] == expected_body_hash

    def test_query_string_included_in_signature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that query string is included in signature payload."""
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

        fixed_uuid = UUID("12345678-1234-5678-1234-567812345678")
        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch("apps.orchestrator.clients.uuid.uuid4", return_value=fixed_uuid),
        ):
            headers = clients_mod._get_internal_auth_headers(
                "GET", "/api/orders", query="limit=10&offset=0"
            )

        # Verify signature includes query string
        payload_dict = {
            "service_id": "orchestrator",
            "method": "GET",
            "path": "/api/orders",
            "query": "limit=10&offset=0",
            "timestamp": "1700000000",
            "nonce": str(fixed_uuid),
            "user_id": "",
            "strategy_id": "",
            "body_hash": hashlib.sha256(b"").hexdigest(),
        }
        payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
        expected_signature = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()

        assert headers["X-Internal-Token"] == expected_signature

    def test_none_query_normalizes_to_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that None query parameter is normalized to empty string."""
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

        fixed_uuid = UUID("12345678-1234-5678-1234-567812345678")
        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch("apps.orchestrator.clients.uuid.uuid4", return_value=fixed_uuid),
        ):
            headers = clients_mod._get_internal_auth_headers("GET", "/api/orders", query=None)

        # Verify query is empty string in signature
        payload_dict = {
            "service_id": "orchestrator",
            "method": "GET",
            "path": "/api/orders",
            "query": "",
            "timestamp": "1700000000",
            "nonce": str(fixed_uuid),
            "user_id": "",
            "strategy_id": "",
            "body_hash": hashlib.sha256(b"").hexdigest(),
        }
        payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
        expected_signature = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()

        assert headers["X-Internal-Token"] == expected_signature

    def test_optional_user_id_and_strategy_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that optional user_id and strategy_id are included in headers when provided."""
        monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

        with (
            patch("apps.orchestrator.clients.time.time", return_value=1700000000),
            patch(
                "apps.orchestrator.clients.uuid.uuid4",
                return_value=UUID("12345678-1234-5678-1234-567812345678"),
            ),
        ):
            # Without user_id and strategy_id
            headers_without = clients_mod._get_internal_auth_headers("GET", "/api/test")
            assert "X-User-ID" not in headers_without
            assert "X-Strategy-ID" not in headers_without

            # With user_id and strategy_id
            headers_with = clients_mod._get_internal_auth_headers(
                "GET", "/api/test", user_id="user-123", strategy_id="strat-456"
            )
            assert headers_with["X-User-ID"] == "user-123"
            assert headers_with["X-Strategy-ID"] == "strat-456"


class TestSignalServiceClientExtended:
    """Extended tests for SignalServiceClient covering additional scenarios."""

    @pytest.mark.asyncio()
    async def test_initialization_strips_trailing_slash(self) -> None:
        """Test that base_url trailing slash is stripped during initialization."""
        with patch("apps.orchestrator.clients.httpx.AsyncClient"):
            client = clients_mod.SignalServiceClient("http://signals/")
            assert client.base_url == "http://signals"

    @pytest.mark.asyncio()
    async def test_initialization_sets_timeout(self) -> None:
        """Test that custom timeout is passed to httpx client."""
        with patch("apps.orchestrator.clients.httpx.AsyncClient") as mock_client:
            clients_mod.SignalServiceClient("http://signals", timeout=60.0)
            mock_client.assert_called_once_with(timeout=60.0)

    @pytest.mark.asyncio()
    async def test_close_calls_aclose(self) -> None:
        """Test that close method calls AsyncClient.aclose()."""
        client_instance = AsyncMock()
        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.SignalServiceClient("http://signals")
            await client.close()

        client_instance.aclose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_health_check_success_returns_true(self) -> None:
        """Test that health check returns True when endpoint returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_response)

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.SignalServiceClient("http://signals")
            result = await client.health_check()

        assert result is True
        client_instance.get.assert_called_once_with("http://signals/ready")

    @pytest.mark.asyncio()
    async def test_health_check_http_status_error_returns_false(self) -> None:
        """Test that health check returns False on HTTP status error."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=503)
            )
        )

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.SignalServiceClient("http://signals")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_health_check_network_error_returns_false(self) -> None:
        """Test that health check returns False on network error."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=httpx.NetworkError("network error"))

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.SignalServiceClient("http://signals")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_health_check_unexpected_error_returns_false(self) -> None:
        """Test that health check returns False on unexpected exceptions."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.SignalServiceClient("http://signals")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_fetch_signals_with_minimal_params(self) -> None:
        """Test fetch_signals with only required parameters."""
        response_payload = {
            "signals": [
                {"symbol": "AAPL", "predicted_return": 0.1, "rank": 1, "target_weight": 1.0}
            ],
            "metadata": {
                "as_of_date": "2024-12-31",
                "model_version": "v1",
                "strategy": "alpha",
                "num_signals": 1,
                "generated_at": "2024-12-31T00:00:00Z",
                "top_n": 5,
                "bottom_n": 5,
            },
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_payload

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.SignalServiceClient("http://signals")
            # Only symbols parameter
            result = await client.fetch_signals(symbols=["AAPL"])

        assert isinstance(result, SignalServiceResponse)
        assert len(result.signals) == 1

    @pytest.mark.asyncio()
    async def test_fetch_signals_with_all_params(self) -> None:
        """Test fetch_signals with all optional parameters."""
        response_payload = {
            "signals": [],
            "metadata": {
                "as_of_date": "2024-01-15",
                "model_version": "v2",
                "strategy": "test",
                "num_signals": 0,
                "generated_at": "2024-01-15T00:00:00Z",
                "top_n": 3,
                "bottom_n": 2,
            },
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_payload

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ) as headers_mock,
        ):
            client = clients_mod.SignalServiceClient("http://signals")
            result = await client.fetch_signals(
                symbols=["AAPL", "MSFT"],
                as_of_date=date(2024, 1, 15),
                top_n=3,
                bottom_n=2,
                user_id="user-999",
                strategy_id="strat-111",
            )

        assert isinstance(result, SignalServiceResponse)
        # Verify auth headers were called with user_id and strategy_id
        call_kwargs = headers_mock.call_args[1]
        assert call_kwargs["user_id"] == "user-999"
        assert call_kwargs["strategy_id"] == "strat-111"

    @pytest.mark.asyncio()
    async def test_fetch_signals_body_serialization(self) -> None:
        """Test that fetch_signals uses deterministic JSON serialization for body hash."""
        response_payload = {
            "signals": [],
            "metadata": {
                "as_of_date": "2024-12-31",
                "model_version": "v1",
                "strategy": "test",
                "num_signals": 0,
                "generated_at": "2024-12-31T00:00:00Z",
                "top_n": 1,
                "bottom_n": 1,
            },
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_payload

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig", "Content-Type": "application/json"},
            ),
        ):
            client = clients_mod.SignalServiceClient("http://signals")
            await client.fetch_signals(symbols=["TEST"], top_n=1, bottom_n=1)

        # Verify post was called with bytes content
        call_kwargs = client_instance.post.call_args[1]
        assert isinstance(call_kwargs["content"], bytes)
        # Verify headers include Content-Type
        assert call_kwargs["headers"]["Content-Type"] == "application/json"


class TestExecutionGatewayClientExtended:
    """Extended tests for ExecutionGatewayClient covering additional scenarios."""

    @pytest.mark.asyncio()
    async def test_initialization_strips_trailing_slash(self) -> None:
        """Test that base_url trailing slash is stripped during initialization."""
        with patch("apps.orchestrator.clients.httpx.AsyncClient"):
            client = clients_mod.ExecutionGatewayClient("http://exec/")
            assert client.base_url == "http://exec"

    @pytest.mark.asyncio()
    async def test_initialization_sets_timeout(self) -> None:
        """Test that custom timeout is passed to httpx client."""
        with patch("apps.orchestrator.clients.httpx.AsyncClient") as mock_client:
            clients_mod.ExecutionGatewayClient("http://exec", timeout=45.0)
            mock_client.assert_called_once_with(timeout=45.0)

    @pytest.mark.asyncio()
    async def test_close_calls_aclose(self) -> None:
        """Test that close method calls AsyncClient.aclose()."""
        client_instance = AsyncMock()
        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            await client.close()

        client_instance.aclose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_health_check_success_returns_true(self) -> None:
        """Test that health check returns True when endpoint returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_response)

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.health_check()

        assert result is True
        client_instance.get.assert_called_once_with("http://exec/health")

    @pytest.mark.asyncio()
    async def test_health_check_connect_timeout_returns_false(self) -> None:
        """Test that health check returns False on connection timeout."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_health_check_http_status_error_returns_false(self) -> None:
        """Test that health check returns False on HTTP status error."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=500)
            )
        )

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_health_check_network_error_returns_false(self) -> None:
        """Test that health check returns False on network error."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=httpx.NetworkError("network error"))

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_health_check_unexpected_error_returns_false(self) -> None:
        """Test that health check returns False on unexpected exceptions."""
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio()
    async def test_submit_order_accepts_200_status(self) -> None:
        """Test that submit_order accepts 200 status code."""
        mock_response = MagicMock()
        mock_response.status_code = 200  # Also acceptable besides 201
        mock_response.json.return_value = {
            "client_order_id": "xyz",
            "status": "pending",
            "broker_order_id": None,
            "symbol": "MSFT",
            "side": "sell",
            "qty": 5,
            "order_type": "limit",
            "limit_price": "350.00",
            "created_at": "2024-12-31T00:00:00Z",
            "message": "queued",
        }

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            order = OrderRequest(
                symbol="MSFT", side="sell", qty=5, order_type="limit", limit_price="350.00"
            )
            result = await client.submit_order(order)

        assert result.client_order_id == "xyz"
        assert result.status == "pending"

    @pytest.mark.asyncio()
    async def test_submit_order_raises_for_error_status(self) -> None:
        """Test that submit_order raises exception for non-200/201 status."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "bad request"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(status_code=400)
        )

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            order = OrderRequest(symbol="TEST", side="buy", qty=1, order_type="market")

            with pytest.raises(httpx.HTTPStatusError):
                await client.submit_order(order)

    @pytest.mark.asyncio()
    async def test_submit_order_with_user_and_strategy_context(self) -> None:
        """Test that submit_order passes user_id and strategy_id to auth headers."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "client_order_id": "abc123",
            "status": "accepted",
            "broker_order_id": "brk-999",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": None,
            "created_at": "2024-12-31T00:00:00Z",
            "message": "ok",
        }

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ) as headers_mock,
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            order = OrderRequest(symbol="AAPL", side="buy", qty=10, order_type="market")
            await client.submit_order(order, user_id="user-789", strategy_id="strat-222")

        # Verify auth headers were called with user_id and strategy_id
        call_kwargs = headers_mock.call_args[1]
        assert call_kwargs["user_id"] == "user-789"
        assert call_kwargs["strategy_id"] == "strat-222"

    @pytest.mark.asyncio()
    async def test_submit_order_body_serialization(self) -> None:
        """Test that submit_order uses deterministic JSON serialization for body hash."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "client_order_id": "test",
            "status": "accepted",
            "broker_order_id": None,
            "symbol": "TEST",
            "side": "buy",
            "qty": 1,
            "order_type": "market",
            "limit_price": None,
            "created_at": "2024-12-31T00:00:00Z",
            "message": "ok",
        }

        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig", "Content-Type": "application/json"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")
            order = OrderRequest(symbol="TEST", side="buy", qty=1, order_type="market")
            await client.submit_order(order)

        # Verify post was called with bytes content
        call_kwargs = client_instance.post.call_args[1]
        assert isinstance(call_kwargs["content"], bytes)
        # Verify headers include Content-Type
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio()
    async def test_get_order_raises_for_non_200(self) -> None:
        """Test that get_order raises exception for non-200 status."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found", request=MagicMock(), response=MagicMock(status_code=404)
        )

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")

            with pytest.raises(httpx.HTTPStatusError):
                await client.get_order("nonexistent")

    @pytest.mark.asyncio()
    async def test_get_positions_raises_for_non_200(self) -> None:
        """Test that get_positions raises exception for non-200 status."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "internal error", request=MagicMock(), response=MagicMock(status_code=500)
        )

        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=mock_response)

        with (
            patch("apps.orchestrator.clients.httpx.AsyncClient", return_value=client_instance),
            patch(
                "apps.orchestrator.clients._get_internal_auth_headers",
                return_value={"X-Internal-Token": "sig"},
            ),
        ):
            client = clients_mod.ExecutionGatewayClient("http://exec")

            with pytest.raises(httpx.HTTPStatusError):
                await client.get_positions()
