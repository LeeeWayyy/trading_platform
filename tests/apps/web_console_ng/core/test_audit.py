"""Tests for audit logging wrapper."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from apps.web_console_ng.core import audit as audit_module


def test_audit_log_emits_expected_fields(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fixed = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            assert tz == UTC
            return fixed

    monkeypatch.setattr(audit_module, "datetime", FakeDateTime)

    caplog.set_level(logging.INFO, logger=audit_module.logger.name)

    audit_module.audit_log("order_submitted", "user-1", {"symbol": "AAPL"})

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.msg == "trading_audit"
    assert record.action == "order_submitted"
    assert record.user_id == "user-1"
    assert record.details == {"symbol": "AAPL"}
    assert record.timestamp == fixed.isoformat()
