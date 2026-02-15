"""Tests for scripts/validate_deployment_manifest.py."""

from __future__ import annotations

from pathlib import Path

from scripts.validate_deployment_manifest import main, validate_manifest


def test_compose_production_valid(tmp_path: Path) -> None:
    manifest = tmp_path / "docker-compose.yml"
    manifest.write_text(
        """
services:
  web_console:
    image: test
    networks:
      - internal_net
    read_only: true
    tmpfs:
      - /tmp
networks:
  internal_net:
    internal: true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    code, errors, warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 0
    assert errors == []
    assert warnings == []


def test_compose_production_non_internal_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "docker-compose.yml"
    manifest.write_text(
        """
services:
  web_console:
    image: test
    networks:
      - internal_net
      - edge_net
    read_only: true
    tmpfs:
      - /tmp
networks:
  internal_net:
    internal: true
  edge_net:
    internal: false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("non-internal" in err for err in errors)


_K8S_BASE = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web_console
spec:
  template:
    metadata:
      labels:
        app: web-console
    spec:
      containers:
        - name: web_console
          image: test
          securityContext:
            readOnlyRootFilesystem: true
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
"""


def test_k8s_valid_network_policy_and_readonly(tmp_path: Path) -> None:
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - port: 6379
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 0
    assert errors == []


def test_k8s_deny_all_egress_passes(tmp_path: Path) -> None:
    """NetworkPolicy with no egress rules = deny-all, should pass."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 0
    assert errors == []


def test_k8s_allow_all_egress_rejected(tmp_path: Path) -> None:
    """NetworkPolicy with empty 'to' (allow-all) must be rejected."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to: []
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_empty_egress_rule_rejected(tmp_path: Path) -> None:
    """NetworkPolicy with empty egress rule dict (allow-all) must be rejected."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_multi_policy_permissive_second_rejected(tmp_path: Path) -> None:
    """When multiple NetworkPolicies match, a permissive one anywhere must fail."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress-restrictive
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - port: 6379
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress-permissive
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_match_expressions_permissive_rejected(tmp_path: Path) -> None:
    """Permissive policy using matchExpressions (not matchLabels) must still be caught."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress-restrictive
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - port: 6379
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress-via-expressions
spec:
  podSelector:
    matchExpressions:
      - key: app
        operator: In
        values: ["web-console"]
  policyTypes: ["Egress"]
  egress:
    - {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_empty_pod_selector_matches_all(tmp_path: Path) -> None:
    """Empty podSelector ({}) matches all pods — permissive policy must be caught."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress-restrictive
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - port: 6379
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: catch-all-permissive
spec:
  podSelector: {}
  policyTypes: ["Egress"]
  egress:
    - {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_wildcard_peer_in_to_rejected(tmp_path: Path) -> None:
    """Egress rule with wildcard peer ({}) inside 'to' array must be rejected."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_wildcard_ipblock_rejected(tmp_path: Path) -> None:
    """Egress rule with 0.0.0.0/0 ipBlock must be rejected."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_namespace_selector_wildcard_rejected(tmp_path: Path) -> None:
    """Peer with empty namespaceSelector (no podSelector) = cluster-wide wildcard."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - namespaceSelector: {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_local_namespace_wildcard_rejected(tmp_path: Path) -> None:
    """Peer with empty podSelector (no namespaceSelector) = all pods in local namespace."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchLabels:
      app: web-console
  policyTypes: ["Egress"]
  egress:
    - to:
        - podSelector: {}
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 1
    assert any("permissive egress" in err for err in errors)


def test_k8s_notin_absent_key_matches(tmp_path: Path) -> None:
    """NotIn with absent label key MUST match (K8s semantics)."""
    manifest = tmp_path / "k8s.yaml"
    # Policy uses NotIn with key "env" that doesn't exist on workload labels.
    # Per K8s semantics, NotIn SHOULD match when key is absent.
    manifest.write_text(
        _K8S_BASE
        + """---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-console-egress
spec:
  podSelector:
    matchExpressions:
      - key: env
        operator: NotIn
        values: ["staging"]
  policyTypes: ["Egress"]
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - port: 6379
""",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 0
    assert errors == []


def test_main_missing_manifest_returns_2(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yml"
    code = main(["--manifest", str(missing), "--service", "web_console", "--env", "production"])
    assert code == 2
