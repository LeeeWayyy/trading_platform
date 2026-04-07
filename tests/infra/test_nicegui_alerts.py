"""Validate NiceGUI Prometheus alert rules against Docker Compose scrape labels."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# Matches PromQL label references to "pod" in aggregation clauses like
# ``by (pod)`` / ``by(pod, ...)`` or label matchers like ``{pod="..."}``
_POD_LABEL_RE = re.compile(r"\b(?:by|without)\s*\([^)]*\bpod\b|{\s*[^}]*\bpod\s*[=!~]")

# Matches PromQL aggregation clauses that reference the "instance" label
# e.g. ``by (instance)`` or ``by (le, instance)``
_INSTANCE_AGG_RE = re.compile(r"\b(?:by|without)\s*\([^)]*\binstance\b")

# Matches PromQL expressions that perform per-target aggregation
# e.g. ``sum by (...)``, ``avg by (...)``
_HAS_AGGREGATION_RE = re.compile(r"\b(?:sum|avg|min|max|count|group)\s+by\s*\(")


@pytest.fixture()
def nicegui_rules() -> dict[str, Any]:
    """Load NiceGUI alert rules."""
    rules_path = Path(__file__).parent.parent.parent / "infra/prometheus/alerts/nicegui.yml"
    with open(rules_path, encoding="utf-8") as f:
        return yaml.safe_load(f)  # type: ignore[no-any-return]


class TestNiceGUIAlertRules:
    """Validate NiceGUI alert rules use labels available in Docker Compose scraping."""

    def test_nicegui_rules_valid_yaml(self, nicegui_rules: dict[str, Any]) -> None:
        assert nicegui_rules is not None
        assert isinstance(nicegui_rules, dict)

    def test_nicegui_rules_do_not_reference_pod_label(self, nicegui_rules: dict[str, Any]) -> None:
        """Docker Compose static scrape targets expose instance/job labels, not pod."""
        for group in nicegui_rules.get("groups", []):
            for rule in group.get("rules", []):
                expr = rule.get("expr", "")
                assert not _POD_LABEL_RE.search(expr), (
                    f"Alert {rule.get('alert', '<recording-rule>')} still references pod label: {expr}"
                )

    def test_nicegui_rules_use_instance_label_for_per_target_alerts(
        self, nicegui_rules: dict[str, Any],
    ) -> None:
        """Per-target aggregations should key on instance under Docker Compose."""
        for group in nicegui_rules.get("groups", []):
            for rule in group.get("rules", []):
                expr = rule.get("expr", "")
                if _HAS_AGGREGATION_RE.search(expr):
                    assert _INSTANCE_AGG_RE.search(expr), (
                        f"Alert {rule.get('alert', '<recording-rule>')} aggregates "
                        f"without instance label under Docker Compose: {expr}"
                    )
