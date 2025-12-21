from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

sys.modules.setdefault("plotly", MagicMock())
sys.modules.setdefault("plotly.graph_objects", MagicMock())

from apps.web_console.pages import alerts


def test_feature_flag_disabled(monkeypatch):
    monkeypatch.setattr(alerts, "FEATURE_ALERTS", False)
    user = {"role": "admin"}
    # Should return early without error when feature flag disabled
    alerts.render_alerts_page(user=user, db_pool=None)


def test_permission_denied(monkeypatch):
    monkeypatch.setattr(alerts, "FEATURE_ALERTS", True)
    user = {"role": "viewer"}  # viewer only, but will be blocked by has_permission mock
    with patch("apps.web_console.pages.alerts.has_permission", return_value=False):
        # Should stop without raising
        alerts.render_alerts_page(user=user, db_pool=None)
