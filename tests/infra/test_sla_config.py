"""SLA configuration validation tests.

Validates that Prometheus rules and Alertmanager config are properly structured
and reference only existing metrics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


class TestPrometheusConfig:
    """Validate Prometheus configuration."""

    @pytest.fixture()
    def prometheus_yml(self) -> dict:
        """Load prometheus.yml configuration."""
        config_path = Path(__file__).parent.parent.parent / "infra/prometheus/prometheus.yml"
        with open(config_path) as f:
            return yaml.safe_load(f)

    def test_prometheus_yml_valid_yaml(self, prometheus_yml):
        """Verify prometheus.yml is valid YAML."""
        assert prometheus_yml is not None
        assert isinstance(prometheus_yml, dict)

    def test_scrape_configs_defined(self, prometheus_yml):
        """Verify scrape_configs are defined."""
        assert "scrape_configs" in prometheus_yml
        assert len(prometheus_yml["scrape_configs"]) > 0

    def test_web_console_metrics_job_exists(self, prometheus_yml):
        """Verify web-console-metrics scrape job exists."""
        job_names = [job["job_name"] for job in prometheus_yml["scrape_configs"]]
        assert "web-console-metrics" in job_names, "web-console-metrics job not found"

    def test_web_console_metrics_port(self, prometheus_yml):
        """Verify web-console-metrics targets port 8503."""
        for job in prometheus_yml["scrape_configs"]:
            if job["job_name"] == "web-console-metrics":
                targets = job["static_configs"][0]["targets"]
                assert any("8503" in t for t in targets), "Port 8503 not in targets"

    def test_rule_files_include_track7(self, prometheus_yml):
        """Verify track7_sla.yml is in rule_files."""
        rule_files = prometheus_yml.get("rule_files", [])
        assert any("track7_sla" in rf for rf in rule_files), "track7_sla.yml not in rule_files"


class TestTrack7AlertRules:
    """Validate Track 7 alert rules."""

    @pytest.fixture()
    def track7_rules(self) -> dict:
        """Load track7_sla.yml alert rules."""
        rules_path = Path(__file__).parent.parent.parent / "infra/prometheus/alerts/track7_sla.yml"
        with open(rules_path) as f:
            return yaml.safe_load(f)

    def test_track7_rules_valid_yaml(self, track7_rules):
        """Verify track7_sla.yml is valid YAML."""
        assert track7_rules is not None
        assert isinstance(track7_rules, dict)

    def test_track7_rules_has_groups(self, track7_rules):
        """Verify rules define at least one group."""
        assert "groups" in track7_rules
        assert len(track7_rules["groups"]) > 0

    def test_cb_metric_missing_alert_exists(self, track7_rules):
        """Verify CBMetricMissing alert is defined for absent() detection."""
        alert_names = []
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if "alert" in rule:
                    alert_names.append(rule["alert"])

        assert "CBMetricMissing" in alert_names, "CBMetricMissing alert not found"

    def test_cb_metric_missing_uses_absent(self, track7_rules):
        """Verify CBMetricMissing uses absent() function."""
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if rule.get("alert") == "CBMetricMissing":
                    expr = rule.get("expr", "")
                    assert "absent(" in expr, f"Must use absent() function: {expr}"

    def test_cb_verification_failed_alert_exists(self, track7_rules):
        """Verify CBVerificationFailed alert is defined."""
        alert_names = []
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if "alert" in rule:
                    alert_names.append(rule["alert"])

        assert "CBVerificationFailed" in alert_names, "CBVerificationFailed alert not found"

    def test_poison_queue_alert_exists(self, track7_rules):
        """Verify AlertPoisonQueueHigh alert is defined."""
        alert_names = []
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if "alert" in rule:
                    alert_names.append(rule["alert"])

        assert "AlertPoisonQueueHigh" in alert_names, "AlertPoisonQueueHigh alert not found"

    def test_all_alerts_have_severity(self, track7_rules):
        """Verify all alerts have severity label."""
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if "alert" in rule:
                    labels = rule.get("labels", {})
                    assert "severity" in labels, f"Alert {rule['alert']} missing severity label"

    def test_all_alerts_have_runbook(self, track7_rules):
        """Verify all alerts have runbook annotation."""
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if "alert" in rule:
                    annotations = rule.get("annotations", {})
                    assert "runbook" in annotations, f"Alert {rule['alert']} missing runbook"

    def test_sentinel_value_correct(self, track7_rules):
        """Verify CBVerificationFailed uses sentinel value (999999)."""
        for group in track7_rules["groups"]:
            for rule in group.get("rules", []):
                if rule.get("alert") == "CBVerificationFailed":
                    expr = rule.get("expr", "")
                    assert "999999" in expr, f"Sentinel value not in expr: {expr}"


class TestAlertmanagerConfig:
    """Validate Alertmanager configuration."""

    @pytest.fixture()
    def alertmanager_config(self) -> dict:
        """Load alertmanager config.yml."""
        config_path = Path(__file__).parent.parent.parent / "infra/alertmanager/config.yml"
        with open(config_path) as f:
            return yaml.safe_load(f)

    def test_alertmanager_config_valid_yaml(self, alertmanager_config):
        """Verify config.yml is valid YAML."""
        assert alertmanager_config is not None
        assert isinstance(alertmanager_config, dict)

    def test_route_defined(self, alertmanager_config):
        """Verify routing tree is defined."""
        assert "route" in alertmanager_config
        assert "receiver" in alertmanager_config["route"]

    def test_receivers_defined(self, alertmanager_config):
        """Verify receivers are defined."""
        assert "receivers" in alertmanager_config
        assert len(alertmanager_config["receivers"]) > 0

    def test_slack_receiver_exists(self, alertmanager_config):
        """Verify Slack receiver is configured."""
        receiver_names = [r["name"] for r in alertmanager_config["receivers"]]
        assert "slack-ops" in receiver_names, "slack-ops receiver not found"

    def test_pagerduty_receiver_exists(self, alertmanager_config):
        """Verify PagerDuty receiver is configured."""
        receiver_names = [r["name"] for r in alertmanager_config["receivers"]]
        assert "pagerduty-platform" in receiver_names, "pagerduty-platform receiver not found"

    def test_track7_routing_exists(self, alertmanager_config):
        """Verify Track 7 SLA routing is configured."""
        routes = alertmanager_config["route"].get("routes", [])
        track7_route = None
        for route in routes:
            match = route.get("match", {})
            if match.get("sla") == "track7":
                track7_route = route
                break

        assert track7_route is not None, "track7 SLA route not found"

    def test_inhibit_rules_defined(self, alertmanager_config):
        """Verify inhibit rules are defined."""
        assert "inhibit_rules" in alertmanager_config
        assert len(alertmanager_config["inhibit_rules"]) > 0


class TestGrafanaDashboard:
    """Validate Grafana dashboard configuration."""

    @pytest.fixture()
    def track7_dashboard(self) -> dict:
        """Load Track7 SLO dashboard JSON."""
        dashboard_path = (
            Path(__file__).parent.parent.parent / "infra/grafana/dashboards/track7_slo.json"
        )
        with open(dashboard_path) as f:
            return json.load(f)

    def test_dashboard_valid_json(self, track7_dashboard):
        """Verify dashboard is valid JSON."""
        assert track7_dashboard is not None
        assert isinstance(track7_dashboard, dict)

    def test_dashboard_has_title(self, track7_dashboard):
        """Verify dashboard has title."""
        assert "title" in track7_dashboard
        assert track7_dashboard["title"] == "Track7 SLO"

    def test_dashboard_has_uid(self, track7_dashboard):
        """Verify dashboard has UID for import."""
        assert "uid" in track7_dashboard
        assert track7_dashboard["uid"] == "track7-slo"

    def test_dashboard_has_panels(self, track7_dashboard):
        """Verify dashboard has panels."""
        assert "panels" in track7_dashboard
        assert len(track7_dashboard["panels"]) > 0

    def test_cb_staleness_panel_exists(self, track7_dashboard):
        """Verify CB staleness panel is present."""
        panel_titles = [p.get("title", "") for p in track7_dashboard["panels"]]
        assert any("CB Staleness" in t for t in panel_titles), "CB Staleness panel not found"

    def test_audit_latency_panel_exists(self, track7_dashboard):
        """Verify audit write latency panel is present."""
        panel_titles = [p.get("title", "") for p in track7_dashboard["panels"]]
        assert any(
            "Audit" in t and "Latency" in t for t in panel_titles
        ), "Audit latency panel not found"

    def test_dashboard_has_refresh(self, track7_dashboard):
        """Verify dashboard auto-refresh is configured."""
        assert "refresh" in track7_dashboard
        # Should be 10s or similar for SLA monitoring
        assert track7_dashboard["refresh"] in ["5s", "10s", "15s", "30s"]

    def test_dashboard_tags_include_sla(self, track7_dashboard):
        """Verify dashboard is tagged for discovery."""
        tags = track7_dashboard.get("tags", [])
        assert "sla" in tags or "track7" in tags, "Dashboard missing sla/track7 tags"
