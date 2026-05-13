"""Tests for data quality service."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.alert_acknowledgment_store import (
    InMemoryAlertAcknowledgmentStore,
)
from libs.web_console_services.data_quality_service import DataQualityService


class _FakePersistentStore(InMemoryAlertAcknowledgmentStore):
    """In-memory store that advertises durable persistence for tests."""

    is_persistent = True


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
async def service() -> DataQualityService:
    """Create fresh service with a fake persistent ack store.

    ``DataQualityService.acknowledge_alert`` raises when the active store is
    not durable; tests that exercise that path must inject a persistent
    store.
    """
    return DataQualityService(acknowledgment_store=_FakePersistentStore())


@pytest.fixture()
async def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
async def viewer_user() -> DummyUser:
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.mark.asyncio()
async def test_get_anomaly_alerts_filters_datasets(
    service: DataQualityService, operator_user: DummyUser
) -> None:
    """Operator should only see datasets they can access."""
    alerts = await service.get_anomaly_alerts(operator_user, severity=None, acknowledged=None)

    datasets = {alert.dataset for alert in alerts}
    assert datasets == {"crsp", "compustat", "fama_french", "taq"}


@pytest.mark.asyncio()
async def test_acknowledge_alert_idempotent(
    service: DataQualityService, operator_user: DummyUser
) -> None:
    """Acknowledging the same alert twice should return the first ack."""
    first = await service.acknowledge_alert(operator_user, alert_id="alert-1", reason="triage")
    second = await service.acknowledge_alert(operator_user, alert_id="alert-1", reason="ignored")

    assert first.id == second.id
    assert first.reason == "triage"


@pytest.mark.asyncio()
async def test_acknowledge_alert_viewer_allowed_single_admin(
    service: DataQualityService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer can acknowledge alerts — single-admin model."""
    result = await service.acknowledge_alert(viewer_user, alert_id="alert-1", reason="triage")
    assert result is not None


@pytest.mark.asyncio()
async def test_acknowledge_alert_records_user(
    service: DataQualityService, operator_user: DummyUser
) -> None:
    """Acknowledgment should record the user id."""
    ack = await service.acknowledge_alert(operator_user, alert_id="alert-2", reason="triage")

    assert ack.acknowledged_by == "user-operator"
