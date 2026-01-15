#!/usr/bin/env python3
"""
Tests for scripts/dev/generate_architecture.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.dev.generate_architecture as architecture


@pytest.fixture()
def tmp_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary repo root and patch module constants."""
    monkeypatch.setattr(architecture, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(architecture, "ARCH_DIR", tmp_path / "docs" / "ARCHITECTURE")
    return tmp_path


@pytest.fixture()
def minimal_config() -> architecture.Config:
    """Provide a minimal config for testing."""
    return architecture.Config(
        version="1.0.0",
        layers=[
            {"id": "core", "label": "Core Services", "order": 1},
            {"id": "domain", "label": "Domain Logic", "order": 2},
        ],
        components={
            "apps/service_a": {"layer": "core", "spec": "../SPECS/services/service_a.md"},
            "libs/lib_b": {"layer": "domain", "spec": "../SPECS/libs/lib_b.md"},
        },
        external_nodes=[],
        virtual_edges=[],
        filtering={},
        rendering={},
    )


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_imports_parses_various_patterns(tmp_path: Path) -> None:
    source = (
        "import apps.service_a\n"
        "import libs.core.common.utils as utils\n"
        "from strategies.alpha_baseline import model\n"
        "from .helpers import foo\n"
        "from ..shared import bar\n"
    )
    module_path = tmp_path / "module.py"
    _write_file(module_path, source)

    imports = architecture.extract_imports(module_path, "apps.service_a.module")

    assert set(imports) == {
        "apps.service_a",
        "libs.core.common.utils",
        "strategies.alpha_baseline",
        "apps.service_a.helpers",
        "apps.shared",
    }


def test_scan_imports_handles_syntax_error(tmp_path: Path) -> None:
    module_path = tmp_path / "broken.py"
    _write_file(module_path, "def broken(:\n")

    imports = architecture.extract_imports(module_path, "apps.broken")

    assert imports == []


def test_build_dependency_graph_empty_components(minimal_config: architecture.Config) -> None:
    edges = architecture.build_dependency_graph([], minimal_config)

    assert edges == set()


def test_build_dependency_graph_internal_and_circular_edges(
    tmp_repo_root: Path, minimal_config: architecture.Config
) -> None:
    service_dir = tmp_repo_root / "apps" / "service_a"
    library_dir = tmp_repo_root / "libs" / "lib_b"
    _write_file(
        service_dir / "main.py",
        "import libs.lib_b.utils\nimport pandas\n",
    )
    _write_file(
        library_dir / "__init__.py",
        "from apps.service_a import main\n",
    )

    components = [
        architecture.Component(
            category="Services", name="service_a", path=service_dir, layer="core", spec=""
        ),
        architecture.Component(
            category="Libraries", name="lib_b", path=library_dir, layer="domain", spec=""
        ),
    ]

    edges = architecture.build_dependency_graph(components, minimal_config)

    assert ("svc_service_a", "lib_lib_b") in edges
    assert ("lib_lib_b", "svc_service_a") in edges


def test_build_dependency_graph_with_filtering(
    tmp_repo_root: Path,
) -> None:
    """Test that filtering hides edges to common libs."""
    service_dir = tmp_repo_root / "apps" / "service_a"
    common_dir = tmp_repo_root / "libs" / "common"
    _write_file(service_dir / "main.py", "import libs.core.common.utils\n")
    _write_file(common_dir / "__init__.py", "pass\n")

    config = architecture.Config(
        version="1.0.0",
        layers=[
            {"id": "core", "label": "Core", "order": 1},
            {"id": "infra", "label": "Infra", "order": 2},
        ],
        components={
            "apps/service_a": {"layer": "core", "spec": ""},
            "libs/common": {"layer": "infra", "spec": ""},
        },
        filtering={
            "hide_to_common_libs": True,
            "common_libs": ["libs/common"],
            "allowlist": [],
        },
    )

    components = [
        architecture.Component(
            category="Services", name="service_a", path=service_dir, layer="core", spec=""
        ),
        architecture.Component(
            category="Libraries", name="common", path=common_dir, layer="infra", spec=""
        ),
    ]

    edges = architecture.build_dependency_graph(components, config)

    # Edge should be filtered out
    assert ("svc_service_a", "lib_common") not in edges


def test_build_dependency_graph_allowlist_preserves_edges(
    tmp_repo_root: Path,
) -> None:
    """Test that allowlisted components keep edges to common libs."""
    service_dir = tmp_repo_root / "apps" / "execution_gateway"
    core_dir = tmp_repo_root / "libs" / "core"
    _write_file(service_dir / "main.py", "import libs.core.common.utils\n")
    _write_file(core_dir / "common" / "__init__.py", "pass\n")

    config = architecture.Config(
        version="1.0.0",
        layers=[
            {"id": "core", "label": "Core", "order": 1},
            {"id": "infra", "label": "Infra", "order": 2},
        ],
        components={
            "apps/execution_gateway": {"layer": "core", "spec": ""},
            "libs/core": {"layer": "infra", "spec": ""},
        },
        filtering={
            "hide_to_common_libs": True,
            "common_libs": ["libs/core"],
            "allowlist": ["apps/execution_gateway"],
        },
    )

    components = [
        architecture.Component(
            category="Services",
            name="execution_gateway",
            path=service_dir,
            layer="core",
            spec="",
        ),
        architecture.Component(
            category="Libraries",
            name="core",
            path=core_dir,
            layer="infra",
            spec="",
        ),
    ]

    edges = architecture.build_dependency_graph(components, config)

    # Edge should be preserved for allowlisted component
    assert ("svc_execution_gateway", "lib_core") in edges


def test_generate_obsidian_canvas_structure(minimal_config: architecture.Config) -> None:
    components = [
        architecture.Component(
            category="Services",
            name="service_a",
            path=Path("apps/service_a"),
            layer="core",
            spec="../SPECS/services/service_a.md",
        ),
        architecture.Component(
            category="Libraries",
            name="lib_b",
            path=Path("libs/lib_b"),
            layer="domain",
            spec="../SPECS/libs/lib_b.md",
        ),
    ]
    edges: set[tuple[str, str]] = {("svc_service_a", "lib_lib_b")}

    canvas_json = architecture.render_canvas(components, edges, minimal_config)
    payload = json.loads(canvas_json)

    assert set(payload.keys()) == {"nodes", "edges"}
    # Check for layer-based groups
    group_labels = {
        node["label"] for node in payload["nodes"] if node.get("type") == "group"
    }
    assert "Core Services" in group_labels
    assert "Domain Logic" in group_labels


def test_generate_mermaid_flow_format(minimal_config: architecture.Config) -> None:
    components = [
        architecture.Component(
            category="Services",
            name="service_a",
            path=Path("apps/service_a"),
            layer="core",
            spec="../SPECS/services/service_a.md",
        ),
    ]
    diagram = architecture.render_mermaid_flow(components, minimal_config)

    assert diagram.startswith("# System Architecture - Data Flow")
    assert "flowchart TB" in diagram
    assert 'subgraph core["Core Services"]' in diagram
    assert 'svc_service_a["Service A"]' in diagram
    assert 'click svc_service_a "../SPECS/services/service_a.md"' in diagram


def test_generate_mermaid_deps_format(minimal_config: architecture.Config) -> None:
    components = [
        architecture.Component(
            category="Services",
            name="service_a",
            path=Path("apps/service_a"),
            layer="core",
            spec="../SPECS/services/service_a.md",
        ),
        architecture.Component(
            category="Libraries",
            name="lib_b",
            path=Path("libs/lib_b"),
            layer="domain",
            spec="../SPECS/libs/lib_b.md",
        ),
    ]
    edges: set[tuple[str, str]] = {("svc_service_a", "lib_lib_b")}
    diagram = architecture.render_mermaid_deps(components, edges, minimal_config)

    assert diagram.startswith("# System Architecture - Dependencies")
    assert "flowchart TB" in diagram
    assert "svc_service_a -.-> lib_lib_b" in diagram  # Dashed arrow for deps


def test_virtual_edges_in_flow_diagram() -> None:
    """Test that virtual edges appear in flow diagram."""
    config = architecture.Config(
        version="1.0.0",
        layers=[
            {"id": "orchestration", "label": "Orchestration", "order": 1},
            {"id": "core", "label": "Core", "order": 2},
        ],
        components={
            "apps/orchestrator": {"layer": "orchestration", "spec": ""},
            "apps/signal_service": {"layer": "core", "spec": ""},
        },
        virtual_edges=[
            architecture.VirtualEdge(
                from_id="apps/orchestrator",
                to_id="apps/signal_service",
                edge_type="data_flow",
                label="request signals",
            ),
        ],
    )

    components = [
        architecture.Component(
            category="Services",
            name="orchestrator",
            path=Path("apps/orchestrator"),
            layer="orchestration",
            spec="",
        ),
        architecture.Component(
            category="Services",
            name="signal_service",
            path=Path("apps/signal_service"),
            layer="core",
            spec="",
        ),
    ]

    diagram = architecture.render_mermaid_flow(components, config)

    assert "svc_orchestrator -->|request signals| svc_signal_service" in diagram


def test_external_nodes_in_flow_diagram() -> None:
    """Test that external nodes appear in flow diagram."""
    config = architecture.Config(
        version="1.0.0",
        layers=[
            {"id": "infra", "label": "Infrastructure", "order": 1},
        ],
        components={},
        external_nodes=[
            architecture.ExternalNode(
                id="ext_redis",
                label="Redis",
                layer="infra",
                node_type="database",
                spec="../SPECS/infrastructure/redis.md",
            ),
        ],
    )

    diagram = architecture.render_mermaid_flow([], config)

    assert 'ext_redis[("Redis")]' in diagram
    assert 'click ext_redis "../SPECS/infrastructure/redis.md"' in diagram
    assert "class ext_redis external" in diagram


def test_check_spec_files_detects_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that missing spec files are detected."""
    arch_dir = tmp_path / "docs" / "ARCHITECTURE"
    arch_dir.mkdir(parents=True)
    monkeypatch.setattr(architecture, "ARCH_DIR", arch_dir)

    components = [
        architecture.Component(
            category="Services",
            name="service_a",
            path=Path("apps/service_a"),
            layer="core",
            spec="../SPECS/services/service_a.md",
        ),
    ]
    external_nodes: list[architecture.ExternalNode] = []

    missing = architecture.check_spec_files(components, external_nodes)

    assert len(missing) == 1
    assert "service_a" in missing[0]


def test_check_spec_files_passes_when_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that existing spec files pass validation."""
    arch_dir = tmp_path / "docs" / "ARCHITECTURE"
    arch_dir.mkdir(parents=True)
    monkeypatch.setattr(architecture, "ARCH_DIR", arch_dir)

    # Create the spec file
    spec_path = arch_dir / ".." / "SPECS" / "services" / "service_a.md"
    _write_file(spec_path, "# Service A Spec\n")

    components = [
        architecture.Component(
            category="Services",
            name="service_a",
            path=Path("apps/service_a"),
            layer="core",
            spec="../SPECS/services/service_a.md",
        ),
    ]

    missing = architecture.check_spec_files(components, [])

    assert missing == []


def test_check_drift_reports_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "output.txt"
    _write_file(target, "alpha")

    assert architecture.check_match(target, "alpha") is True
    assert architecture.check_match(target, "bravo") is False
    assert architecture.check_match(tmp_path / "missing.txt", "charlie") is False


def test_resolve_virtual_edge_id() -> None:
    """Test resolution of virtual edge references."""
    components = [
        architecture.Component(
            category="Services",
            name="signal_service",
            path=Path("apps/signal_service"),
            layer="core",
            spec="",
        ),
    ]
    external_nodes = [
        architecture.ExternalNode(
            id="ext_redis", label="Redis", layer="infra", node_type="database"
        ),
    ]

    # Component reference
    assert (
        architecture.resolve_virtual_edge_id(
            "apps/signal_service", components, external_nodes
        )
        == "svc_signal_service"
    )

    # External node reference
    assert (
        architecture.resolve_virtual_edge_id("ext_redis", components, external_nodes)
        == "ext_redis"
    )

    # Unknown component reference
    assert (
        architecture.resolve_virtual_edge_id("apps/unknown", components, external_nodes)
        is None
    )

    # Unknown external reference (should warn and return None)
    assert (
        architecture.resolve_virtual_edge_id("ext_unknown", components, external_nodes)
        is None
    )


def test_canvas_edge_colors_by_type() -> None:
    """Test that Canvas edges use correct colors based on edge type."""
    config = architecture.Config(
        version="1.0.0",
        layers=[
            {"id": "core", "label": "Core", "order": 1},
        ],
        components={
            "apps/orchestrator": {"layer": "core", "spec": ""},
            "apps/signal_service": {"layer": "core", "spec": ""},
        },
        virtual_edges=[
            architecture.VirtualEdge(
                from_id="apps/orchestrator",
                to_id="apps/signal_service",
                edge_type="data_flow",
                label="data",
            ),
            architecture.VirtualEdge(
                from_id="apps/signal_service",
                to_id="apps/orchestrator",
                edge_type="control",
                label="control",
            ),
        ],
    )

    components = [
        architecture.Component(
            category="Services",
            name="orchestrator",
            path=Path("apps/orchestrator"),
            layer="core",
            spec="",
        ),
        architecture.Component(
            category="Services",
            name="signal_service",
            path=Path("apps/signal_service"),
            layer="core",
            spec="",
        ),
    ]

    import json
    canvas_json = architecture.render_canvas(components, set(), config)
    payload = json.loads(canvas_json)

    # Find edges and check colors
    edges = payload["edges"]
    assert len(edges) == 2

    # Find data_flow edge (green = "4")
    data_edge = next(e for e in edges if e.get("label") == "data")
    assert data_edge["color"] == "4"

    # Find control edge (blue = "5")
    control_edge = next(e for e in edges if e.get("label") == "control")
    assert control_edge["color"] == "5"
