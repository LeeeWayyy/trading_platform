import time

import pytest

from apps.web_console.auth.mfa_verification import (
    get_amr_method,
    require_2fa_for_action,
    verify_step_up_auth,
)


def test_verify_step_up_auth_success():
    now = int(time.time())
    ok, err = verify_step_up_auth({"auth_time": now, "amr": ["otp"]})
    assert ok is True
    assert err is None


def test_verify_step_up_auth_failures():
    old = int(time.time()) - 120
    ok, err = verify_step_up_auth({"auth_time": old, "amr": ["otp"]})
    assert ok is False
    assert err == "auth_too_old"

    ok, err = verify_step_up_auth({"auth_time": int(time.time())})
    assert ok is False
    assert err == "mfa_not_performed"

    ok, err = verify_step_up_auth({"auth_time": int(time.time()), "amr": ["pwd"]})
    assert ok is False
    assert err == "mfa_method_not_allowed"


@pytest.mark.asyncio
async def test_require_2fa_for_action_requires_claims():
    class DummyAudit:
        def __init__(self):
            self.logged = False

        async def log_auth_event(self, **kwargs):
            self.logged = True

    class Session:
        def __init__(self, claims=None):
            self.step_up_claims = claims
            self.user_id = "user"

    audit = DummyAudit()
    allowed, error = await require_2fa_for_action(Session(), "flatten_all", audit)
    assert allowed is False
    assert error == "step_up_required"
    assert audit.logged

    now_claims = {"auth_time": int(time.time()), "amr": ["otp"]}
    allowed, error = await require_2fa_for_action(Session(now_claims), "flatten_all", audit)
    assert allowed is True
    assert error is None


def test_get_amr_method():
    assert get_amr_method({"amr": ["sms"]}) == "sms"
    assert get_amr_method({}) is None
