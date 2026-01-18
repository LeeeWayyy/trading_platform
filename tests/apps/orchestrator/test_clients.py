"""Unit tests for orchestrator HTTP clients and auth helpers."""

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
                body=b"{\"x\":1}",
                user_id="user-1",
                strategy_id="strat-9",
            )

        expected_body_hash = hashlib.sha256(b"{\"x\":1}").hexdigest()
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
