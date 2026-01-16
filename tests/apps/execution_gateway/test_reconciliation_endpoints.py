from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from apps.execution_gateway.main import app


class StubReconciliationService:
    def __init__(self):
        self._complete = False
        self._override = False
        self._override_context = {}
        self.runs = []
        self.timeout_seconds = 300

    def is_startup_complete(self):
        return self._complete

    def startup_elapsed_seconds(self):
        return 10.0

    def startup_timed_out(self):
        return False

    def override_active(self):
        return self._override

    def override_context(self):
        return dict(self._override_context)

    async def run_reconciliation_once(self, mode):
        self.runs.append(mode)

    def mark_startup_complete(self, forced=False, user_id=None, reason=None):
        self._complete = True
        if forced:
            self._override = True
            self._override_context = {
                "user_id": user_id,
                "reason": reason,
            }


@pytest.fixture()
def client():
    return TestClient(app)


def test_reconciliation_status_dry_run(client):
    response = client.get("/api/v1/reconciliation/status")
    assert response.status_code == 200
    data = response.json()
    assert data["startup_complete"] is True
    assert data["dry_run"] is True


def test_reconciliation_force_complete_admin(client):
    stub = StubReconciliationService()

    with (
        patch.object(app.state.config, "dry_run", False),
        patch.object(app.state.context, "reconciliation_service", stub),
    ):
        response = client.post(
            "/api/v1/reconciliation/force-complete",
            json={"reason": "maintenance"},
            headers={"X-User-Role": "admin", "X-User-Id": "ops"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "override_enabled"
    assert stub._override is True


def test_reconciliation_run_admin(client):
    stub = StubReconciliationService()

    with (
        patch.object(app.state.config, "dry_run", False),
        patch.object(app.state.context, "reconciliation_service", stub),
    ):
        response = client.post(
            "/api/v1/reconciliation/run",
            headers={"X-User-Role": "admin", "X-User-Id": "ops"},
        )

    assert response.status_code == 200
    assert stub.runs == ["manual"]
