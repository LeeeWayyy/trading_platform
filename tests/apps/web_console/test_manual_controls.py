from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_streamlit(monkeypatch):
    """Lightweight Streamlit stub."""

    class Stub:
        def __init__(self) -> None:
            self.last_error: Any = None
            self.last_warning: Any = None
            self.last_info: Any = None
            self.last_success: Any = None
            self.rerun_called = False
            self.session_state = {}

        # UI methods
        def error(self, msg, *_, **__):
            self.last_error = msg

        def warning(self, msg, *_, **__):
            self.last_warning = msg

        def info(self, msg, *_, **__):
            self.last_info = msg

        def success(self, msg, *_, **__):
            self.last_success = msg

        def title(self, *_, **__):
            return None

        def subheader(self, *_, **__):
            return None

        def caption(self, *_, **__):
            return None

        def text_input(self, *_, **kwargs):
            return kwargs.get("value", "")

        def text_area(self, *_, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, *_, **kwargs):
            return kwargs.get("value", 0.0)

        def selectbox(self, *_, **kwargs):
            return (kwargs.get("options") or ["market"])[0]

        def button(self, *_, **__):
            return False

        def expander(self, *_, **__):
            class Ctx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def write(self_inner, *args, **kwargs):
                    return None

            return Ctx()

        def divider(self, *_, **__):
            return None

        def rerun(self):
            self.rerun_called = True

        def stop(self):
            raise RuntimeError("st.stop")

        def cache_resource(self, func=None, **_kwargs):
            if func:
                return func

            def decorator(fn):
                return fn

            return decorator

    stub = Stub()
    monkeypatch.setitem(sys.modules, "streamlit", stub)
    return stub


@pytest.fixture()
def user_with_session():
    return {
        "user_id": "test-user",
        "session_id": "test-session",
        "session_version": 1,
        "role": "operator",
        "strategies": ["alpha"],
    }


def _import_modules(monkeypatch, stub_streamlit):
    """Import modules under test with stubs in place."""

    # Stub jwt to avoid crypto dependency
    jwt_stub = SimpleNamespace(
        api_jwk=SimpleNamespace(),
        algorithms=SimpleNamespace(),
        utils=SimpleNamespace(),
        encode=lambda *args, **kwargs: "tok",
    )
    monkeypatch.setitem(sys.modules, "jwt", jwt_stub)
    monkeypatch.setitem(sys.modules, "jwt.api_jwk", jwt_stub.api_jwk)
    monkeypatch.setitem(sys.modules, "jwt.algorithms", jwt_stub.algorithms)
    monkeypatch.setitem(sys.modules, "jwt.utils", jwt_stub.utils)

    # Stub auth permissions module to avoid loading full auth stack
    perm_stub = SimpleNamespace(
        Permission=SimpleNamespace(
            VIEW_TRADES="view_trades",
            CANCEL_ORDER="cancel_order",
            CLOSE_POSITION="close_position",
            FLATTEN_ALL="flatten_all",
        ),
        has_permission=lambda *_args, **_kwargs: True,
        get_authorized_strategies=lambda user: user.get("strategies", []) if isinstance(user, dict) else [],
    )
    monkeypatch.setitem(sys.modules, "apps.web_console.auth.permissions", perm_stub)

    session_stub = SimpleNamespace(get_current_user=lambda: {})
    monkeypatch.setitem(sys.modules, "apps.web_console.auth.session_manager", session_stub)

    # Stub web_console_auth modules to avoid cryptography/redis dependencies
    class FakeJWTManager:
        def generate_service_token(self, user_id, session_id, client_ip, user_agent):
            return "fake-service-token"

    jwt_manager_stub = SimpleNamespace(JWTManager=FakeJWTManager)
    monkeypatch.setitem(sys.modules, "libs.web_console_auth.jwt_manager", jwt_manager_stub)
    monkeypatch.setitem(sys.modules, "libs.web_console_auth.__init__", SimpleNamespace(JWTManager=FakeJWTManager))

    auth_config_stub = SimpleNamespace(AuthConfig=SimpleNamespace(from_env=lambda: SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "libs.web_console_auth.config", auth_config_stub)
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=lambda **kwargs: None))

    import apps.web_console.pages.manual_controls as manual_controls  # type: ignore
    import apps.web_console.utils.api_client as api_client  # type: ignore

    monkeypatch.setattr(api_client, "_get_jwt_manager", lambda: FakeJWTManager(), raising=False)

    return manual_controls, api_client


def test_get_manual_controls_headers_requires_session_version(monkeypatch, stub_streamlit):
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    with pytest.raises(ValueError, match="session_version"):
        api_client.get_manual_controls_headers({"user_id": "u1"})


def test_cancel_order_success(monkeypatch, stub_streamlit, user_with_session):
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "cancelled"}

    post_mock = MagicMock(return_value=Resp())
    monkeypatch.setattr(api_client.requests, "post", post_mock)

    resp = manual_controls.cancel_order("ord-1", "valid reason", user_with_session)
    assert resp["status"] == "cancelled"
    post_mock.assert_called_once()


def test_flatten_all_includes_id_token(monkeypatch, stub_streamlit, user_with_session):
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "flattening"}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return Resp()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    manual_controls.flatten_all_positions(user_with_session, "x" * 20, "idtoken123")
    assert captured["json"]["id_token"] == "idtoken123"
    assert captured["json"]["reason"] == "x" * 20


def test_strategy_unauthorized_warning(monkeypatch, stub_streamlit):
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    err = manual_controls.ManualControlsAPIError(403, "strategy_unauthorized", "no strategies")
    # Should not raise
    manual_controls.handle_api_error(err, "load")


def test_validation_422_error(monkeypatch, stub_streamlit):
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    detail = [{"msg": "qty must be positive"}]
    err = manual_controls.ManualControlsAPIError(422, "validation_error", "bad", detail=detail)
    manual_controls.handle_api_error(err, "close position")


def test_permission_gating_blocks(monkeypatch, stub_streamlit, user_with_session):
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)

    # Force feature flag on and deny VIEW_TRADES
    monkeypatch.setattr(manual_controls, "FEATURE_MANUAL_CONTROLS", True)
    monkeypatch.setattr(manual_controls, "has_permission", lambda *_args: False)

    with pytest.raises(RuntimeError, match="st.stop"):
        manual_controls.render_manual_controls(user_with_session, None, None)


def test_close_position_partial_qty(monkeypatch, stub_streamlit, user_with_session):
    """Test close_position sends partial qty in payload."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "closing"}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        return Resp()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    from decimal import Decimal

    manual_controls.close_position("AAPL", "closing partial position", Decimal("50"), user_with_session)

    assert "/positions/AAPL/close" in captured["url"]
    assert captured["json"]["qty"] == Decimal("50")
    assert captured["json"]["reason"] == "closing partial position"
    assert "requested_by" in captured["json"]
    assert "requested_at" in captured["json"]


def test_close_position_full_no_qty(monkeypatch, stub_streamlit, user_with_session):
    """Test close_position without qty (full close)."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "closing"}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return Resp()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    manual_controls.close_position("AAPL", "full position close", None, user_with_session)

    assert "qty" not in captured["json"]
    assert captured["json"]["reason"] == "full position close"


def test_adjust_position_limit_order(monkeypatch, stub_streamlit, user_with_session):
    """Test adjust_position sends limit price and order_type in payload."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "adjusting"}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        return Resp()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    from decimal import Decimal

    manual_controls.adjust_position(
        "TSLA",
        Decimal("100"),
        "adjusting to target",
        "limit",
        Decimal("250.50"),
        user_with_session,
    )

    assert "/positions/TSLA/adjust" in captured["url"]
    assert captured["json"]["target_qty"] == Decimal("100")
    assert captured["json"]["order_type"] == "limit"
    assert captured["json"]["limit_price"] == Decimal("250.50")
    assert captured["json"]["reason"] == "adjusting to target"


def test_adjust_position_market_order(monkeypatch, stub_streamlit, user_with_session):
    """Test adjust_position with market order (no limit price)."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    captured = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "adjusting"}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return Resp()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    from decimal import Decimal

    manual_controls.adjust_position(
        "TSLA",
        Decimal("-50"),
        "reducing position",
        "market",
        None,
        user_with_session,
    )

    assert captured["json"]["order_type"] == "market"
    assert "limit_price" not in captured["json"]
    assert captured["json"]["target_qty"] == Decimal("-50")


def test_generate_service_token_requires_user_id(monkeypatch, stub_streamlit):
    """Test that generate_service_token_for_user fails closed when user_id is missing."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    with pytest.raises(ValueError, match="User identity missing"):
        api_client.generate_service_token_for_user({})

    with pytest.raises(ValueError, match="User identity missing"):
        api_client.generate_service_token_for_user({"session_version": 1})


def test_error_handling_400_bad_request(monkeypatch, stub_streamlit):
    """Test handle_api_error for 400 Bad Request."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    # Patch st in the module after import
    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    err = manual_controls.ManualControlsAPIError(400, "invalid_request", "qty must be positive")
    manual_controls.handle_api_error(err, "close position")
    assert stub_streamlit.last_error is not None


def test_error_handling_401_unauthorized(monkeypatch, stub_streamlit):
    """Test handle_api_error for 401 Unauthorized triggers re-login message."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    err = manual_controls.ManualControlsAPIError(401, "token_expired", "Token has expired")
    manual_controls.handle_api_error(err, "cancel order")
    assert stub_streamlit.last_error is not None or stub_streamlit.last_info is not None


def test_error_handling_403_permission_denied(monkeypatch, stub_streamlit):
    """Test handle_api_error for 403 permission denied."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    err = manual_controls.ManualControlsAPIError(403, "permission_denied", "No permission")
    manual_controls.handle_api_error(err, "flatten all")
    # permission_denied uses st.warning, not st.error
    assert stub_streamlit.last_warning is not None


def test_error_handling_429_rate_limited(monkeypatch, stub_streamlit):
    """Test handle_api_error for 429 Rate Limited."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    err = manual_controls.ManualControlsAPIError(429, "rate_limited", "Too many requests")
    manual_controls.handle_api_error(err, "cancel order")
    # Should show error or warning about rate limiting
    assert stub_streamlit.last_error is not None or stub_streamlit.last_warning is not None


def test_error_handling_500_internal_error(monkeypatch, stub_streamlit):
    """Test handle_api_error for 500 Internal Server Error."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    err = manual_controls.ManualControlsAPIError(500, "internal_error", "Server error")
    manual_controls.handle_api_error(err, "close position")
    assert stub_streamlit.last_error is not None


def test_error_handling_503_broker_unavailable(monkeypatch, stub_streamlit):
    """Test handle_api_error for 503 Service Unavailable."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    err = manual_controls.ManualControlsAPIError(503, "broker_unavailable", "Broker down")
    manual_controls.handle_api_error(err, "adjust position")
    assert stub_streamlit.last_error is not None


def test_status_code_fallback_401_plain_string(monkeypatch, stub_streamlit):
    """Test _handle_manual_controls_error uses status-code fallback for plain string detail."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    class MockResponse:
        status_code = 401
        reason = "Unauthorized"

        def json(self):
            return "Token has expired"  # Plain string, not structured

    with pytest.raises(api_client.ManualControlsAPIError) as exc_info:
        api_client._handle_manual_controls_error(MockResponse())

    # Should use status-code fallback to "token_expired"
    assert exc_info.value.error_code == "token_expired"
    assert exc_info.value.status_code == 401


def test_status_code_fallback_429_no_error_code(monkeypatch, stub_streamlit):
    """Test _handle_manual_controls_error uses fallback when detail has no error code."""
    manual_controls, api_client = _import_modules(monkeypatch, stub_streamlit)

    class MockResponse:
        status_code = 429
        reason = "Too Many Requests"
        headers = {"Retry-After": "30"}

        def json(self):
            # FastAPI returns detail with message nested
            return {"detail": {"message": "Rate limit exceeded"}}  # No "error" or "code" key

    with pytest.raises(api_client.ManualControlsAPIError) as exc_info:
        api_client._handle_manual_controls_error(MockResponse())

    # Should use status-code fallback to "rate_limited"
    assert exc_info.value.error_code == "rate_limited"
    assert exc_info.value.message == "Rate limit exceeded"


def test_mfa_token_valid_with_timestamp(monkeypatch, stub_streamlit):
    """Test _is_mfa_token_valid returns True for valid, non-expired token."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    from datetime import UTC, datetime, timedelta

    # Set up valid token with recent timestamp (within 55s max age)
    stub_streamlit.session_state["step_up_id_token"] = "valid-token"
    stub_streamlit.session_state["step_up_id_token_issued_at"] = (
        datetime.now(UTC) - timedelta(seconds=30)  # 30s < 55s max age
    ).isoformat()

    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    assert manual_controls._is_mfa_token_valid() is True


def test_mfa_token_expired(monkeypatch, stub_streamlit):
    """Test _is_mfa_token_valid returns False for expired token."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    from datetime import UTC, datetime, timedelta

    # Set up expired token (older than 55s max age, aligned with backend's 60s)
    stub_streamlit.session_state["step_up_id_token"] = "expired-token"
    stub_streamlit.session_state["step_up_id_token_issued_at"] = (
        datetime.now(UTC) - timedelta(seconds=60)  # 60s > 55s max age
    ).isoformat()

    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    assert manual_controls._is_mfa_token_valid() is False


def test_mfa_token_future_timestamp_rejected(monkeypatch, stub_streamlit):
    """Test _is_mfa_token_valid rejects future timestamps (clock skew protection)."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)
    from datetime import UTC, datetime, timedelta

    # Set up token with future timestamp (clock skew or manipulation)
    stub_streamlit.session_state["step_up_id_token"] = "future-token"
    stub_streamlit.session_state["step_up_id_token_issued_at"] = (
        datetime.now(UTC) + timedelta(seconds=10)  # 10s in the future
    ).isoformat()

    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    assert manual_controls._is_mfa_token_valid() is False


def test_mfa_token_no_timestamp(monkeypatch, stub_streamlit):
    """Test _is_mfa_token_valid returns False when token has no timestamp."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)

    # Token without timestamp - should be treated as invalid
    stub_streamlit.session_state["step_up_id_token"] = "token-without-timestamp"
    # No step_up_id_token_issued_at

    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    assert manual_controls._is_mfa_token_valid() is False


def test_clear_mfa_token(monkeypatch, stub_streamlit):
    """Test _clear_mfa_token removes both token and timestamp."""
    manual_controls, _ = _import_modules(monkeypatch, stub_streamlit)

    stub_streamlit.session_state["step_up_id_token"] = "token-to-clear"
    stub_streamlit.session_state["step_up_id_token_issued_at"] = "2025-01-01T00:00:00+00:00"

    monkeypatch.setattr(manual_controls, "st", stub_streamlit)

    manual_controls._clear_mfa_token()

    assert "step_up_id_token" not in stub_streamlit.session_state
    assert "step_up_id_token_issued_at" not in stub_streamlit.session_state
