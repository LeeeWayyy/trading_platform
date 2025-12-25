"""Tests for data quality service."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from apps.web_console.services.data_quality_service import DataQualityService
from libs.web_console_auth.permissions import Role


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture(autouse=True)
async def clear_ack_store() -> None:
    """Ensure acknowledgment store is cleared between tests."""
    DataQualityService._ack_store.clear()


@pytest.fixture()
async def service() -> DataQualityService:
    return DataQualityService()


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
    assert datasets == {"crsp", "compustat", "fama_french"}


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
async def test_acknowledge_alert_denied_without_permission(
    service: DataQualityService, viewer_user: DummyUser
) -> None:
    """Viewer lacks ACKNOWLEDGE_ALERTS permission."""
    with pytest.raises(PermissionError):
        await service.acknowledge_alert(viewer_user, alert_id="alert-1", reason="triage")


@pytest.mark.asyncio()
async def test_acknowledge_alert_records_user(
    service: DataQualityService, operator_user: DummyUser
) -> None:
    """Acknowledgment should record the user id."""
    ack = await service.acknowledge_alert(operator_user, alert_id="alert-2", reason="triage")

    assert ack.acknowledged_by == "user-operator"
