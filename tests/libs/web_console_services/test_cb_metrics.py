"""Tests for CB metrics helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import redis

import libs.web_console_services.cb_metrics as cb_metrics


@pytest.fixture()
def redis_client() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def staleness_gauge(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    gauge = MagicMock()
    monkeypatch.setattr(cb_metrics, "cb_staleness_seconds", gauge)
    return gauge


def test_update_cb_staleness_metric_success(redis_client: MagicMock, staleness_gauge: MagicMock) -> None:
    redis_client.get.return_value = json.dumps({"state": "OPEN"})

    cb_metrics.update_cb_staleness_metric(redis_client)

    staleness_gauge.set.assert_called_once_with(0)


def test_update_cb_staleness_metric_missing_state(
    redis_client: MagicMock, staleness_gauge: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    redis_client.get.return_value = None

    with caplog.at_level("ERROR"):
        cb_metrics.update_cb_staleness_metric(redis_client)

    assert "cb_state_missing" in caplog.text
    staleness_gauge.set.assert_called_once_with(cb_metrics.CB_VERIFICATION_FAILED_SENTINEL)


def test_update_cb_staleness_metric_malformed_json(
    redis_client: MagicMock, staleness_gauge: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    redis_client.get.return_value = "{not-json}"

    with caplog.at_level("ERROR"):
        cb_metrics.update_cb_staleness_metric(redis_client)

    assert "cb_state_malformed_json" in caplog.text
    staleness_gauge.set.assert_called_once_with(cb_metrics.CB_VERIFICATION_FAILED_SENTINEL)


def test_update_cb_staleness_metric_redis_error(
    redis_client: MagicMock, staleness_gauge: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    redis_client.get.side_effect = redis.RedisError("redis down")

    with caplog.at_level("ERROR"):
        cb_metrics.update_cb_staleness_metric(redis_client)

    assert "cb_verification_failed" in caplog.text
    staleness_gauge.set.assert_called_once_with(cb_metrics.CB_VERIFICATION_FAILED_SENTINEL)


def test_update_cb_staleness_metric_json_decode_error(
    redis_client: MagicMock, staleness_gauge: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    redis_client.get.side_effect = json.JSONDecodeError("invalid", "doc", 0)

    with caplog.at_level("ERROR"):
        cb_metrics.update_cb_staleness_metric(redis_client)

    assert "cb_verification_failed" in caplog.text
    staleness_gauge.set.assert_called_once_with(cb_metrics.CB_VERIFICATION_FAILED_SENTINEL)
