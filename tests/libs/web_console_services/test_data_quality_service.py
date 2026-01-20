"""Unit tests for libs.web_console_services.data_quality_service."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from libs.web_console_services.data_quality_service import DataQualityService


@dataclass(frozen=True)
class DummyUser:
    user_id: str


@pytest.fixture()
def service() -> DataQualityService:
    return DataQualityService()


@pytest.mark.asyncio()
async def test_get_validation_results_filters_by_dataset_permission(
    service: DataQualityService,
) -> None:
    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "compustat"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        results = await service.get_validation_results(DummyUser(user_id="user-1"), dataset=None)

    datasets = {item.dataset for item in results}
    assert datasets == {"crsp", "compustat"}


@pytest.mark.asyncio()
async def test_get_validation_results_requires_dataset_access(service: DataQualityService) -> None:
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=False,
        ),
    ):
        with pytest.raises(PermissionError, match="Dataset access required"):
            await service.get_validation_results(DummyUser(user_id="user-1"), dataset="taq")


@pytest.mark.asyncio()
async def test_get_anomaly_alerts_filters_severity_and_acknowledged(
    service: DataQualityService,
) -> None:
    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "fama_french"}

    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        alerts = await service.get_anomaly_alerts(
            DummyUser(user_id="user-1"), severity="warning", acknowledged=False
        )

    assert {alert.dataset for alert in alerts} == {"crsp", "fama_french"}
    assert all(alert.severity == "warning" for alert in alerts)
    assert all(alert.acknowledged is False for alert in alerts)


@pytest.mark.asyncio()
async def test_acknowledge_alert_idempotent(service: DataQualityService) -> None:
    with (
        patch("libs.web_console_services.data_quality_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_quality_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_quality_service.get_user_id", return_value="user-1"),
    ):
        first = await service.acknowledge_alert(
            DummyUser(user_id="user-1"), alert_id="alert-1", reason="triage"
        )
        second = await service.acknowledge_alert(
            DummyUser(user_id="user-1"), alert_id="alert-1", reason="ignore"
        )

    assert first.id == second.id
    assert second.reason == "triage"
    assert second.acknowledged_by == "user-1"


def test_resolve_alert_dataset_invalid_id() -> None:
    with pytest.raises(ValueError, match="Could not resolve dataset"):
        DataQualityService._resolve_alert_dataset("bad-id")
