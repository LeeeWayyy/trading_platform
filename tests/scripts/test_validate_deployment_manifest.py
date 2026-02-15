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


def test_k8s_valid_network_policy_and_readonly(tmp_path: Path) -> None:
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text(
        """
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
---
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
""".strip()
        + "\n",
        encoding="utf-8",
    )

    code, errors, _warnings = validate_manifest(manifest, "web_console", "production")
    assert code == 0
    assert errors == []


def test_main_missing_manifest_returns_2(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yml"
    code = main(["--manifest", str(missing), "--service", "web_console", "--env", "production"])
    assert code == 2
