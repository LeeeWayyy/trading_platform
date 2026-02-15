#!/usr/bin/env python3
"""Validate deployment manifests for SQL Explorer sandbox requirements."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def _load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        docs = [doc for doc in yaml.safe_load_all(handle) if isinstance(doc, dict)]
    return docs


def _compose_attached_networks(service_cfg: dict[str, Any]) -> list[str]:
    raw = service_cfg.get("networks")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, dict):
        return [str(key) for key in raw]
    return []


def _compose_tmpfs_has_tmp(service_cfg: dict[str, Any]) -> bool:
    tmpfs = service_cfg.get("tmpfs")
    if isinstance(tmpfs, list):
        for item in tmpfs:
            if isinstance(item, str) and item.split(":", 1)[0] == "/tmp":
                return True
            if isinstance(item, dict) and str(item.get("target")) == "/tmp":
                return True
    volumes = service_cfg.get("volumes")
    if isinstance(volumes, list):
        for item in volumes:
            if isinstance(item, dict):
                if item.get("type") == "tmpfs" and str(item.get("target")) == "/tmp":
                    return True
    return False


def _validate_compose(
    manifest: dict[str, Any],
    service: str,
    env: str,
) -> tuple[int, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    services = manifest.get("services")
    if not isinstance(services, dict):
        return 1, ["Invalid compose manifest: missing services mapping"], warnings

    service_cfg = services.get(service)
    if not isinstance(service_cfg, dict):
        return 1, [f"Service '{service}' not found in compose manifest"], warnings

    network_mode = service_cfg.get("network_mode")
    if isinstance(network_mode, str) and network_mode == "none":
        errors.append(
            "network_mode: none blocks required Redis/Postgres access; use internal networks instead"
        )

    attached = _compose_attached_networks(service_cfg)
    networks_cfg = manifest.get("networks", {})
    if not isinstance(networks_cfg, dict):
        networks_cfg = {}

    if not attached:
        errors.append("Service must declare explicit networks (default bridge is not allowed)")
    else:
        internal_flags = []
        for network_name in attached:
            cfg = networks_cfg.get(network_name)
            is_internal = isinstance(cfg, dict) and bool(cfg.get("internal", False))
            internal_flags.append((network_name, is_internal))

        if env == "production":
            non_internal = [name for name, is_internal in internal_flags if not is_internal]
            if non_internal:
                errors.append(
                    "Production requires all attached networks to be internal:true; "
                    f"non-internal: {', '.join(non_internal)}"
                )
        else:
            has_internal = any(is_internal for _, is_internal in internal_flags)
            if not has_internal:
                errors.append("At least one internal:true network is required")
            non_internal = [name for name, is_internal in internal_flags if not is_internal]
            if non_internal:
                warnings.append(
                    f"Non-internal networks attached in {env}: {', '.join(non_internal)}"
                )

    if service_cfg.get("read_only") is not True:
        errors.append("Service must set read_only: true")

    if not _compose_tmpfs_has_tmp(service_cfg):
        errors.append("Service must provide writable tmpfs/emptyDir mount at /tmp")

    return (0 if not errors else 1), errors, warnings


def _labels_match(selector: dict[str, Any], labels: dict[str, Any]) -> bool:
    if not selector:
        return False
    for key, value in selector.items():
        if labels.get(key) != value:
            return False
    return True


def _match_expression_matches(expression: dict[str, Any], labels: dict[str, Any]) -> bool:
    """Evaluate a single matchExpressions entry against pod labels."""
    key = str(expression.get("key", ""))
    operator = str(expression.get("operator", ""))
    values = expression.get("values", [])
    if not isinstance(values, list):
        values = []

    label_value = labels.get(key)
    if operator == "In":
        return label_value is not None and label_value in values
    elif operator == "NotIn":
        return label_value is None or label_value not in values
    elif operator == "Exists":
        return key in labels
    elif operator == "DoesNotExist":
        return key not in labels
    return False


def _pod_selector_matches(pod_selector: dict[str, Any] | None, labels: dict[str, Any]) -> bool:
    """Evaluate full K8s podSelector (matchLabels + matchExpressions) against labels.

    Per K8s semantics: empty podSelector ({}) matches all pods.
    """
    if not isinstance(pod_selector, dict):
        return False

    # Empty podSelector = match-all (K8s semantics)
    match_labels = pod_selector.get("matchLabels")
    match_expressions = pod_selector.get("matchExpressions")

    if not match_labels and not match_expressions:
        return True  # Empty selector matches all

    # Check matchLabels (all must match)
    if isinstance(match_labels, dict) and match_labels:
        if not _labels_match(match_labels, labels):
            return False

    # Check matchExpressions (all must match)
    if isinstance(match_expressions, list):
        for expr in match_expressions:
            if isinstance(expr, dict) and not _match_expression_matches(expr, labels):
                return False

    return True


def _is_wildcard_peer(peer: Any) -> bool:
    """Check if an egress peer is a wildcard (matches all destinations).

    Wildcard peers include:
    - Empty dict {}
    - Empty podSelector/namespaceSelector ({})
    - ipBlock with 0.0.0.0/0 or ::/0 CIDR
    """
    if not isinstance(peer, dict):
        return False
    if not peer:
        return True  # Empty dict = match all

    # Check for catch-all ipBlock CIDRs
    ip_block = peer.get("ipBlock")
    if isinstance(ip_block, dict):
        cidr = str(ip_block.get("cidr", ""))
        if cidr in ("0.0.0.0/0", "::/0"):
            return True

    # Check for wildcard selectors (match all pods in namespace or cluster)
    pod_sel = peer.get("podSelector")
    ns_sel = peer.get("namespaceSelector")

    # Empty namespaceSelector ({}) = all namespaces; omitted podSelector = all pods
    is_ns_wildcard = isinstance(ns_sel, dict) and not ns_sel
    is_pod_wildcard = (isinstance(pod_sel, dict) and not pod_sel) or pod_sel is None

    if is_ns_wildcard and is_pod_wildcard:
        return True  # All pods in all namespaces

    # Empty podSelector alone = all pods in local namespace (overly permissive for sandbox)
    if isinstance(pod_sel, dict) and not pod_sel and ns_sel is None:
        return True  # All pods in local namespace

    # Empty namespaceSelector alone = all namespaces (cluster-wide)
    if is_ns_wildcard and pod_sel is None:
        return True  # All pods (implicit) in all namespaces

    return False


def _is_allow_all_egress_rule(rule: Any) -> bool:
    """Check if an egress rule is permissive (allow-all / wildcard).

    A rule is considered allow-all if:
    - It's an empty dict (no restrictions)
    - Its 'to' field is empty list or missing (no destination restrictions)
    - Its 'to' field contains any wildcard peer
    """
    if not isinstance(rule, dict):
        return False
    if not rule:
        return True  # Empty dict = allow-all
    to_field = rule.get("to")
    if to_field is None:
        return True  # Missing 'to' = no destination restriction
    if isinstance(to_field, list) and not to_field:
        return True  # Empty 'to' list = no destination restriction
    # Check individual peers for wildcards
    if isinstance(to_field, list):
        if any(_is_wildcard_peer(peer) for peer in to_field):
            return True
    return False


def _validate_k8s(
    docs: list[dict[str, Any]],
    service: str,
) -> tuple[int, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    workload: dict[str, Any] | None = None
    workload_labels: dict[str, Any] = {}
    workload_namespace: str = "default"
    workload_container: dict[str, Any] | None = None

    for doc in docs:
        kind = str(doc.get("kind", ""))
        metadata = doc.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("name") != service:
            continue

        if kind in {"Deployment", "StatefulSet", "DaemonSet", "Pod"}:
            workload = doc
            workload_namespace = str(metadata.get("namespace", "default"))
            if kind == "Pod":
                spec = doc.get("spec", {})
                pod_meta_labels = metadata.get("labels", {})
            else:
                spec = doc.get("spec", {})
                template = spec.get("template", {}) if isinstance(spec, dict) else {}
                pod_meta = template.get("metadata", {}) if isinstance(template, dict) else {}
                pod_meta_labels = pod_meta.get("labels", {}) if isinstance(pod_meta, dict) else {}
                spec = template.get("spec", {}) if isinstance(template, dict) else {}

            if isinstance(pod_meta_labels, dict):
                workload_labels = pod_meta_labels

            containers = spec.get("containers") if isinstance(spec, dict) else None
            if isinstance(containers, list) and containers:
                named = next(
                    (
                        c
                        for c in containers
                        if isinstance(c, dict) and c.get("name") == service
                    ),
                    None,
                )
                if isinstance(named, dict):
                    workload_container = named
                elif isinstance(containers[0], dict):
                    workload_container = containers[0]
            break

    if workload is None or workload_container is None:
        return 1, [f"Kubernetes workload '{service}' not found"], warnings

    sec_ctx = workload_container.get("securityContext")
    if not isinstance(sec_ctx, dict) or sec_ctx.get("readOnlyRootFilesystem") is not True:
        errors.append("Container securityContext.readOnlyRootFilesystem must be true")

    spec_obj = workload.get("spec", {})
    if workload.get("kind") != "Pod":
        spec_obj = (
            spec_obj.get("template", {}).get("spec", {})
            if isinstance(spec_obj, dict)
            else {}
        )

    mounts = workload_container.get("volumeMounts")
    has_tmp_mount = False
    tmp_mount_name = None
    if isinstance(mounts, list):
        for mount in mounts:
            if isinstance(mount, dict) and mount.get("mountPath") == "/tmp":
                has_tmp_mount = True
                tmp_mount_name = mount.get("name")
                break
    if not has_tmp_mount:
        errors.append("Container must mount /tmp via volumeMounts")

    volumes = spec_obj.get("volumes") if isinstance(spec_obj, dict) else None
    has_tmp_volume = False
    if isinstance(volumes, list) and tmp_mount_name is not None:
        for volume in volumes:
            if (
                isinstance(volume, dict)
                and volume.get("name") == tmp_mount_name
                and isinstance(volume.get("emptyDir"), dict)
            ):
                has_tmp_volume = True
                break
    if not has_tmp_volume:
        errors.append("/tmp mount must reference a volume with emptyDir")

    matched_policies = 0
    for doc in docs:
        if str(doc.get("kind", "")) != "NetworkPolicy":
            continue
        # NetworkPolicies are namespace-scoped: only match within workload namespace
        policy_meta = doc.get("metadata")
        if isinstance(policy_meta, dict):
            policy_ns = str(policy_meta.get("namespace", "default"))
        else:
            policy_ns = "default"
        if policy_ns != workload_namespace:
            continue
        spec = doc.get("spec")
        if not isinstance(spec, dict):
            continue
        policy_types = spec.get("policyTypes")
        if not (isinstance(policy_types, list) and "Egress" in policy_types):
            continue
        pod_selector = spec.get("podSelector")
        if _pod_selector_matches(pod_selector, workload_labels):
            matched_policies += 1
            # Validate egress rules are restrictive (deny-by-default semantics).
            # K8s unions allow-rules across ALL matching policies, so every
            # policy must be restrictive — a permissive policy anywhere opens egress.
            egress_rules = spec.get("egress")
            if egress_rules is None:
                # No egress key = deny-all (K8s default when policyTypes includes Egress)
                pass
            elif isinstance(egress_rules, list):
                if not egress_rules:
                    # Empty list = deny-all
                    pass
                elif any(_is_allow_all_egress_rule(rule) for rule in egress_rules):
                    errors.append(
                        "NetworkPolicy has permissive egress rules (empty 'to' or wildcard); "
                        "must restrict egress to specific internal destinations only"
                    )

    if matched_policies == 0:
        errors.append(
            "No matching NetworkPolicy with policyTypes including Egress for target workload"
        )

    return (0 if not errors else 1), errors, warnings


def validate_manifest(manifest_path: Path, service: str, env: str) -> tuple[int, list[str], list[str]]:
    docs = _load_yaml_documents(manifest_path)
    if not docs:
        return 1, ["Manifest is empty or invalid YAML"], []

    first = docs[0]
    if "services" in first:
        return _validate_compose(first, service, env)
    return _validate_k8s(docs, service)


def _resolve_manifest(path_arg: str | None) -> Path | None:
    if path_arg:
        path = Path(path_arg)
        if path.exists():
            return path
        return None

    for candidate in ("docker-compose.yml", "docker-compose.staging.yml"):
        path = Path(candidate)
        if path.exists():
            return path
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate deployment manifest sandbox controls")
    parser.add_argument("--manifest", dest="manifest", default=None)
    parser.add_argument("--service", dest="service", default="web_console")
    parser.add_argument(
        "--env",
        dest="env",
        default="production",
        choices=["production", "staging", "dev"],
    )

    args = parser.parse_args(argv)
    manifest = _resolve_manifest(args.manifest)
    if manifest is None:
        print("Manifest not found")
        return 2

    code, errors, warnings = validate_manifest(manifest, args.service, args.env)

    for warning in warnings:
        print(f"WARNING: {warning}")

    if code == 0:
        print(f"PASS: {manifest}")
        return 0

    for error in errors:
        print(f"FAIL: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
