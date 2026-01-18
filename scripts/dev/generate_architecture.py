#!/usr/bin/env python3
"""Generate architecture visualization artifacts.

This script scans internal Python imports within apps/, libs/, and strategies/
using the stdlib ast module. It builds a component-level dependency graph and
emits layered architecture diagrams based on configuration.

Outputs:
- docs/ARCHITECTURE/system_map_flow.md (high-level data flow, Mermaid TB)
- docs/ARCHITECTURE/system_map_deps.md (filtered dependencies, Mermaid TB)
- docs/ARCHITECTURE/system_map.canvas (Obsidian Canvas JSON)

It supports:
- --generate: write outputs
- --check: verify outputs are up-to-date (CI drift detection)

Configuration is loaded from docs/ARCHITECTURE/system_map.config.json.
Parse errors are logged as warnings and skipped to keep the pipeline resilient.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = (
    Path(__file__).resolve().parents[2]
)  # scripts/dev/generate_architecture.py → scripts/ → repo_root
ARCH_DIR = REPO_ROOT / "docs" / "ARCHITECTURE"
CONFIG_PATH = ARCH_DIR / "system_map.config.json"

CATEGORY_ORDER = ["Services", "Libraries", "Strategies"]
CATEGORY_ROOTS = {
    "Services": REPO_ROOT / "apps",
    "Libraries": REPO_ROOT / "libs",
    "Strategies": REPO_ROOT / "strategies",
}

NODE_WIDTH = 320
NODE_HEIGHT = 60
NODE_GAP = 20
GROUP_PADDING = 40
GROUP_GAP = 100
LAYER_GAP = 150


@dataclass(frozen=True)
class Component:
    category: str
    name: str
    path: Path
    layer: str = ""
    spec: str = ""

    @property
    def module_prefix(self) -> str:
        root_name = self.path.parent.name
        return f"{root_name}.{self.name}"

    @property
    def config_key(self) -> str:
        root_name = self.path.parent.name
        return f"{root_name}/{self.name}"

    @property
    def node_id(self) -> str:
        prefix = {
            "Services": "svc",
            "Libraries": "lib",
            "Strategies": "strat",
        }[self.category]
        safe = self.name.replace("-", "_").replace(".", "_")
        return f"{prefix}_{safe}"

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


@dataclass
class ExternalNode:
    id: str
    label: str
    layer: str
    node_type: str
    spec: str = ""


@dataclass
class VirtualEdge:
    from_id: str
    to_id: str
    edge_type: str
    label: str = ""


@dataclass
class Subgroup:
    id: str
    label: str
    members: list[str]


@dataclass
class Config:
    version: str
    layers: list[dict[str, Any]]
    components: dict[str, dict[str, str]]
    external_nodes: list[ExternalNode] = field(default_factory=list)
    virtual_edges: list[VirtualEdge] = field(default_factory=list)
    subgroups: dict[str, list[Subgroup]] = field(default_factory=dict)
    filtering: dict[str, Any] = field(default_factory=dict)
    rendering: dict[str, Any] = field(default_factory=dict)

    @property
    def layer_order(self) -> dict[str, int]:
        return {layer["id"]: layer["order"] for layer in self.layers}

    @property
    def layer_labels(self) -> dict[str, str]:
        return {layer["id"]: layer["label"] for layer in self.layers}


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        warn(f"Config file not found: {CONFIG_PATH}")
        return Config(version="0.0.0", layers=[], components={})

    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)

    external_nodes = [
        ExternalNode(
            id=n["id"],
            label=n["label"],
            layer=n["layer"],
            node_type=n["type"],
            spec=n.get("spec", ""),
        )
        for n in data.get("external_nodes", [])
    ]

    virtual_edges = [
        VirtualEdge(
            from_id=e["from"],
            to_id=e["to"],
            edge_type=e["type"],
            label=e.get("label", ""),
        )
        for e in data.get("virtual_edges", [])
    ]

    # Parse subgroups
    subgroups: dict[str, list[Subgroup]] = {}
    for layer_id, sg_list in data.get("subgroups", {}).items():
        subgroups[layer_id] = [
            Subgroup(
                id=sg["id"],
                label=sg["label"],
                members=sg["members"],
            )
            for sg in sg_list
        ]

    return Config(
        version=data.get("version", "1.0.0"),
        layers=data.get("layers", []),
        components=data.get("components", {}),
        external_nodes=external_nodes,
        virtual_edges=virtual_edges,
        subgroups=subgroups,
        filtering=data.get("filtering", {}),
        rendering=data.get("rendering", {}),
    )


def iter_python_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if path.is_file():
            yield path


def has_python_files(path: Path) -> bool:
    try:
        next(iter_python_files(path))
        return True
    except StopIteration:
        return False


def discover_components(config: Config) -> tuple[list[Component], list[str]]:
    components: list[Component] = []
    unmapped: list[str] = []

    for category in CATEGORY_ORDER:
        root = CATEGORY_ROOTS[category]
        if not root.exists():
            continue
        # TODO: Consider also discovering top-level .py modules (not just packages)
        # for components like libs/duckdb_catalog.py. Currently only subdirectories
        # are discovered. See PR #108 review comments (P2 enhancement).
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if not has_python_files(child):
                continue

            config_key = f"{root.name}/{child.name}"
            comp_config = config.components.get(config_key, {})

            if not comp_config:
                unmapped.append(config_key)
                layer = "domain"  # Default layer for unmapped
                spec = ""
            else:
                layer = comp_config.get("layer", "domain")
                spec = comp_config.get("spec", "")

            components.append(
                Component(
                    category=category,
                    name=child.name,
                    path=child,
                    layer=layer,
                    spec=spec,
                )
            )

    return components, unmapped


def check_spec_files(components: list[Component], external_nodes: list[ExternalNode]) -> list[str]:
    missing: list[str] = []

    for comp in components:
        if comp.spec:
            spec_path = ARCH_DIR / comp.spec
            if not spec_path.exists():
                missing.append(f"{comp.config_key}: {comp.spec}")

    for node in external_nodes:
        if node.spec:
            spec_path = ARCH_DIR / node.spec
            if not spec_path.exists():
                missing.append(f"{node.id}: {node.spec}")

    return missing


def module_name_for_path(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def warn(message: str) -> None:
    print(f"[generate_architecture] {message}", file=sys.stderr)


def resolve_relative_import(module_name: str, level: int, module: str | None) -> str | None:
    if level <= 0:
        return module
    parts = module_name.split(".")
    if level > len(parts):
        return None
    prefix = parts[:-level]
    if module:
        prefix.extend(module.split("."))
    if not prefix:
        return None
    return ".".join(prefix)


def extract_imports(path: Path, module_name: str) -> Iterable[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        warn(f"Unable to read {path}: {exc}")
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        warn(f"Skipping {path} due to parse error: {exc}")
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            target = resolve_relative_import(module_name, node.level, node.module)
            if target:
                imports.append(target)
    return imports


def build_dependency_graph(
    components: list[Component],
    config: Config,
) -> set[tuple[str, str]]:
    prefix_to_component: dict[str, Component] = {comp.module_prefix: comp for comp in components}
    edges: set[tuple[str, str]] = set()

    filtering = config.filtering
    hide_to_common = filtering.get("hide_to_common_libs", False)
    common_libs = set(filtering.get("common_libs", []))
    allowlist = set(filtering.get("allowlist", []))

    for comp in components:
        for path in iter_python_files(comp.path):
            module_name = module_name_for_path(path)
            for imported in extract_imports(path, module_name):
                if not imported:
                    continue
                if not (
                    imported.startswith("apps.")
                    or imported.startswith("libs.")
                    or imported.startswith("strategies.")
                ):
                    continue
                parts = imported.split(".")
                if len(parts) < 2:
                    continue
                prefix = ".".join(parts[:2])
                target = prefix_to_component.get(prefix)
                if not target:
                    continue
                if target.node_id == comp.node_id:
                    continue

                # Apply filtering
                target_key = target.config_key
                if hide_to_common and target_key in common_libs:
                    # Skip edges to common libs unless source is in allowlist
                    if comp.config_key not in allowlist:
                        continue

                edges.add((comp.node_id, target.node_id))

    return edges


def resolve_virtual_edge_id(
    edge_ref: str, components: list[Component], external_nodes: list[ExternalNode]
) -> str | None:
    """Resolve a config reference (apps/signal_service or ext_alpaca) to a node ID."""
    # Check if it's an external node - validate it exists
    if edge_ref.startswith("ext_"):
        valid_external_ids = {node.id for node in external_nodes}
        if edge_ref in valid_external_ids:
            return edge_ref
        warn(f"Unknown external node reference: {edge_ref}")
        return None

    # Check components
    for comp in components:
        if comp.config_key == edge_ref:
            return comp.node_id

    return None


# Obsidian Canvas color mapping for edge types
EDGE_TYPE_COLORS: dict[str, str] = {
    "data_flow": "4",  # Green
    "control": "5",  # Blue
    "event": "6",  # Pink
}


def render_mermaid_flow(
    components: list[Component],
    config: Config,
) -> str:
    """Render high-level data flow diagram with virtual edges."""
    lines = ["# System Architecture - Data Flow", "", "```mermaid", "flowchart TB"]

    # Group components by layer
    components_by_layer: dict[str, list[Component]] = {layer["id"]: [] for layer in config.layers}

    for comp in components:
        if comp.layer in components_by_layer:
            components_by_layer[comp.layer].append(comp)

    # Add external nodes to their layers
    external_by_layer: dict[str, list[ExternalNode]] = {layer["id"]: [] for layer in config.layers}
    for node in config.external_nodes:
        if node.layer in external_by_layer:
            external_by_layer[node.layer].append(node)

    # Render layers as subgraphs (sorted by order)
    sorted_layers = sorted(config.layers, key=lambda x: x["order"])
    for layer in sorted_layers:
        layer_id = layer["id"]
        layer_label = layer["label"]
        layer_comps = sorted(components_by_layer.get(layer_id, []), key=lambda c: c.name)
        layer_externals = sorted(external_by_layer.get(layer_id, []), key=lambda n: n.label)

        if not layer_comps and not layer_externals:
            continue

        lines.append(f'  subgraph {layer_id}["{layer_label}"]')

        for comp in layer_comps:
            lines.append(f'    {comp.node_id}["{comp.label}"]')

        for ext in layer_externals:
            lines.append(f'    {ext.id}[("{ext.label}")]')

        lines.append("  end")

    # Add virtual edges with styling
    lines.append("")
    for edge in config.virtual_edges:
        from_id = resolve_virtual_edge_id(edge.from_id, components, config.external_nodes)
        to_id = resolve_virtual_edge_id(edge.to_id, components, config.external_nodes)

        if not from_id or not to_id:
            warn(f"Cannot resolve virtual edge: {edge.from_id} -> {edge.to_id}")
            continue

        if edge.label:
            lines.append(f"  {from_id} -->|{edge.label}| {to_id}")
        else:
            lines.append(f"  {from_id} --> {to_id}")

    # Add click links to specs
    lines.append("")
    lines.append("  %% Click links to documentation")
    for comp in components:
        if comp.spec:
            lines.append(f'  click {comp.node_id} "{comp.spec}"')

    for ext in config.external_nodes:
        if ext.spec:
            lines.append(f'  click {ext.id} "{ext.spec}"')

    # Add legend
    lines.append("")
    lines.append("  %% Styling")
    lines.append("  classDef external fill:#f9f,stroke:#333,stroke-width:2px")
    for ext in config.external_nodes:
        lines.append(f"  class {ext.id} external")

    lines.append("```")
    lines.append("")
    lines.append("## Legend")
    lines.append("")
    lines.append("- **Boxes**: Internal services and libraries")
    lines.append("- **Cylinders**: External systems (databases, APIs)")
    lines.append("- **Arrows**: Data flow direction")
    lines.append("- Click any node to view its specification")
    lines.append("")

    return "\n".join(lines) + "\n"


def render_mermaid_deps(
    components: list[Component],
    edges: set[tuple[str, str]],
    config: Config,
) -> str:
    """Render filtered dependency diagram."""
    lines = ["# System Architecture - Dependencies", "", "```mermaid", "flowchart TB"]

    # Group components by layer
    components_by_layer: dict[str, list[Component]] = {layer["id"]: [] for layer in config.layers}

    for comp in components:
        if comp.layer in components_by_layer:
            components_by_layer[comp.layer].append(comp)

    # Render layers as subgraphs
    sorted_layers = sorted(config.layers, key=lambda x: x["order"])
    for layer in sorted_layers:
        layer_id = layer["id"]
        layer_label = layer["label"]
        layer_comps = sorted(components_by_layer.get(layer_id, []), key=lambda c: c.name)

        if not layer_comps:
            continue

        lines.append(f'  subgraph {layer_id}["{layer_label}"]')
        for comp in layer_comps:
            lines.append(f'    {comp.node_id}["{comp.label}"]')
        lines.append("  end")

    # Add dependency edges (dashed style)
    lines.append("")
    for src, dst in sorted(edges):
        lines.append(f"  {src} -.-> {dst}")

    # Add click links
    lines.append("")
    lines.append("  %% Click links to documentation")
    for comp in components:
        if comp.spec:
            lines.append(f'  click {comp.node_id} "{comp.spec}"')

    lines.append("```")
    lines.append("")
    lines.append("## Legend")
    lines.append("")
    lines.append("- **Dashed arrows**: Code dependencies (imports)")
    lines.append(
        "- Edges to common infrastructure libs (common, secrets, health) are hidden for clarity"
    )
    lines.append("- Click any node to view its specification")
    lines.append("")

    return "\n".join(lines) + "\n"


def get_edge_sides(
    from_pos: tuple[int, int],
    to_pos: tuple[int, int],
) -> tuple[str, str]:
    """Determine edge connection sides based on vertical position.

    Returns (fromSide, toSide) - always connects via top/bottom for clean vertical flow.
    """
    _from_x, from_y = from_pos
    _to_x, to_y = to_pos

    # If target is below source, use bottom->top
    if to_y > from_y:
        return "bottom", "top"
    # If target is above source, use top->bottom
    return "top", "bottom"


def render_canvas(
    components: list[Component],
    edges: set[tuple[str, str]],
    config: Config,
) -> str:
    """Render Obsidian Canvas with layered layout, subgroups, and top/bottom edge routing."""
    # Build lookup maps
    comp_by_key: dict[str, Component] = {comp.config_key: comp for comp in components}
    ext_by_id: dict[str, ExternalNode] = {ext.id: ext for ext in config.external_nodes}

    nodes: list[dict[str, object]] = []
    group_nodes: list[dict[str, object]] = []
    node_positions: dict[str, tuple[int, int]] = {}

    # Layout constants for horizontal arrangement
    SUBGROUP_GAP = 40
    SUBGROUP_PADDING = 30

    y_cursor = 0
    sorted_layers = sorted(config.layers, key=lambda x: x["order"])

    for layer in sorted_layers:
        layer_id = layer["id"]
        layer_label = layer["label"]

        # Check if this layer has subgroups
        layer_subgroups = config.subgroups.get(layer_id, [])

        if layer_subgroups:
            # Render with subgroups - horizontal layout within each subgroup
            subgroup_x = GROUP_PADDING
            max_subgroup_height = 0
            subgroup_nodes_list: list[dict[str, object]] = []

            for sg in layer_subgroups:
                # Find members for this subgroup
                sg_components: list[Component] = []
                sg_externals: list[ExternalNode] = []

                for member in sg.members:
                    if member in comp_by_key:
                        sg_components.append(comp_by_key[member])
                    elif member in ext_by_id:
                        sg_externals.append(ext_by_id[member])

                sg_components.sort(key=lambda c: c.name)
                sg_externals.sort(key=lambda e: e.label)

                all_items = len(sg_components) + len(sg_externals)
                if all_items == 0:
                    continue

                # Horizontal layout: all items in one row
                sg_width = SUBGROUP_PADDING * 2 + all_items * (NODE_WIDTH + NODE_GAP) - NODE_GAP
                sg_height = SUBGROUP_PADDING * 2 + NODE_HEIGHT

                sg_group_id = f"group_{layer_id}_{sg.id}"

                # Place components horizontally
                item_idx = 0
                for comp in sg_components:
                    node_x = subgroup_x + SUBGROUP_PADDING + item_idx * (NODE_WIDTH + NODE_GAP)
                    node_y = y_cursor + GROUP_PADDING + SUBGROUP_PADDING

                    node_positions[comp.node_id] = (
                        node_x + NODE_WIDTH // 2,
                        node_y + NODE_HEIGHT // 2,
                    )

                    text = f"[[{comp.spec}|{comp.label}]]" if comp.spec else comp.label
                    nodes.append(
                        {
                            "id": comp.node_id,
                            "type": "text",
                            "text": text,
                            "x": node_x,
                            "y": node_y,
                            "width": NODE_WIDTH,
                            "height": NODE_HEIGHT,
                        }
                    )
                    item_idx += 1

                # Place external nodes horizontally
                for ext in sg_externals:
                    node_x = subgroup_x + SUBGROUP_PADDING + item_idx * (NODE_WIDTH + NODE_GAP)
                    node_y = y_cursor + GROUP_PADDING + SUBGROUP_PADDING

                    node_positions[ext.id] = (node_x + NODE_WIDTH // 2, node_y + NODE_HEIGHT // 2)

                    text = f"[[{ext.spec}|{ext.label}]]" if ext.spec else ext.label
                    nodes.append(
                        {
                            "id": ext.id,
                            "type": "text",
                            "text": text,
                            "x": node_x,
                            "y": node_y,
                            "width": NODE_WIDTH,
                            "height": NODE_HEIGHT,
                            "color": "6",  # Pink for external
                        }
                    )
                    item_idx += 1

                # Add subgroup
                subgroup_nodes_list.append(
                    {
                        "id": sg_group_id,
                        "type": "group",
                        "label": sg.label,
                        "x": subgroup_x,
                        "y": y_cursor + GROUP_PADDING,
                        "width": sg_width,
                        "height": sg_height,
                    }
                )

                subgroup_x += sg_width + SUBGROUP_GAP
                max_subgroup_height = max(max_subgroup_height, sg_height)

            # Add all subgroups
            group_nodes.extend(subgroup_nodes_list)

            # Add layer group encompassing all subgroups
            layer_width = subgroup_x - SUBGROUP_GAP + GROUP_PADDING
            layer_height = GROUP_PADDING * 2 + max_subgroup_height

            group_nodes.append(
                {
                    "id": f"group_{layer_id}",
                    "type": "group",
                    "label": layer_label,
                    "x": 0,
                    "y": y_cursor,
                    "width": layer_width,
                    "height": layer_height,
                }
            )

            y_cursor += layer_height + LAYER_GAP

        else:
            # No subgroups - simple horizontal layout
            layer_comps = sorted(
                [c for c in components if c.layer == layer_id],
                key=lambda c: c.name,
            )
            layer_externals = sorted(
                [e for e in config.external_nodes if e.layer == layer_id],
                key=lambda e: e.label,
            )

            all_items = len(layer_comps) + len(layer_externals)
            if all_items == 0:
                continue

            # Horizontal layout: all items in one row
            group_width = GROUP_PADDING * 2 + all_items * (NODE_WIDTH + NODE_GAP) - NODE_GAP
            group_height = GROUP_PADDING * 2 + NODE_HEIGHT

            item_idx = 0
            for comp in layer_comps:
                node_x = GROUP_PADDING + item_idx * (NODE_WIDTH + NODE_GAP)
                node_y = y_cursor + GROUP_PADDING

                node_positions[comp.node_id] = (node_x + NODE_WIDTH // 2, node_y + NODE_HEIGHT // 2)

                text = f"[[{comp.spec}|{comp.label}]]" if comp.spec else comp.label
                nodes.append(
                    {
                        "id": comp.node_id,
                        "type": "text",
                        "text": text,
                        "x": node_x,
                        "y": node_y,
                        "width": NODE_WIDTH,
                        "height": NODE_HEIGHT,
                    }
                )
                item_idx += 1

            for ext in layer_externals:
                node_x = GROUP_PADDING + item_idx * (NODE_WIDTH + NODE_GAP)
                node_y = y_cursor + GROUP_PADDING

                node_positions[ext.id] = (node_x + NODE_WIDTH // 2, node_y + NODE_HEIGHT // 2)

                text = f"[[{ext.spec}|{ext.label}]]" if ext.spec else ext.label
                nodes.append(
                    {
                        "id": ext.id,
                        "type": "text",
                        "text": text,
                        "x": node_x,
                        "y": node_y,
                        "width": NODE_WIDTH,
                        "height": NODE_HEIGHT,
                        "color": "6",
                    }
                )
                item_idx += 1

            group_nodes.append(
                {
                    "id": f"group_{layer_id}",
                    "type": "group",
                    "label": layer_label,
                    "x": 0,
                    "y": y_cursor,
                    "width": group_width,
                    "height": group_height,
                }
            )

            y_cursor += group_height + LAYER_GAP

    # Create edges with top/bottom routing
    edge_nodes: list[dict[str, object]] = []
    edge_idx = 0

    # Virtual edges (colored by type)
    for vedge in config.virtual_edges:
        from_id = resolve_virtual_edge_id(vedge.from_id, components, config.external_nodes)
        to_id = resolve_virtual_edge_id(vedge.to_id, components, config.external_nodes)
        if from_id and to_id and from_id in node_positions and to_id in node_positions:
            from_side, to_side = get_edge_sides(
                node_positions[from_id],
                node_positions[to_id],
            )
            edge_color = EDGE_TYPE_COLORS.get(vedge.edge_type, "4")
            edge_nodes.append(
                {
                    "id": f"edge_{edge_idx}",
                    "fromNode": from_id,
                    "toNode": to_id,
                    "fromSide": from_side,
                    "toSide": to_side,
                    "label": vedge.label,
                    "color": edge_color,
                }
            )
            edge_idx += 1

    # AST edges (top/bottom routing, no color = default)
    for src, dst in sorted(edges):
        if src in node_positions and dst in node_positions:
            from_side, to_side = get_edge_sides(
                node_positions[src],
                node_positions[dst],
            )
            edge_nodes.append(
                {
                    "id": f"edge_{edge_idx}",
                    "fromNode": src,
                    "toNode": dst,
                    "fromSide": from_side,
                    "toSide": to_side,
                }
            )
            edge_idx += 1

    canvas = {
        "nodes": nodes + group_nodes,
        "edges": edge_nodes,
    }
    return json.dumps(canvas, indent=2, sort_keys=True) + "\n"


def write_if_changed(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def check_match(path: Path, expected: str) -> bool:
    if not path.exists():
        warn(f"Missing generated file: {path}")
        return False
    current = path.read_text(encoding="utf-8")
    if current != expected:
        warn(f"Generated output out of date: {path}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate architecture visualization artifacts.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true", help="Write architecture artifacts")
    mode.add_argument(
        "--check", action="store_true", help="Verify architecture artifacts are up to date"
    )
    args = parser.parse_args()

    config = load_config()
    components, unmapped = discover_components(config)

    if not components:
        warn("No components discovered. Nothing to generate.")
        return 1

    # Check for unmapped components (hard failure in --check mode)
    if unmapped:
        for comp in unmapped:
            warn(f"Unmapped component (add to config): {comp}")
        if args.check:
            warn("ERROR: Unmapped components found. Update system_map.config.json.")
            return 1

    # Check for missing spec files
    missing_specs = check_spec_files(components, config.external_nodes)
    if missing_specs:
        for spec in missing_specs:
            warn(f"Missing spec file: {spec}")
        if args.check:
            warn("ERROR: Missing spec files found.")
            return 1

    edges = build_dependency_graph(components, config)

    # Generate outputs
    flow_path = ARCH_DIR / config.rendering.get("flow_diagram", {}).get(
        "output", "system_map_flow.md"
    )
    deps_path = ARCH_DIR / config.rendering.get("deps_diagram", {}).get(
        "output", "system_map_deps.md"
    )
    canvas_path = ARCH_DIR / config.rendering.get("canvas", {}).get("output", "system_map.canvas")

    flow_content = render_mermaid_flow(components, config)
    deps_content = render_mermaid_deps(components, edges, config)
    canvas_content = render_canvas(components, edges, config)

    if args.generate:
        write_if_changed(flow_path, flow_content)
        write_if_changed(deps_path, deps_content)
        write_if_changed(canvas_path, canvas_content)
        print(f"Generated: {flow_path}")
        print(f"Generated: {deps_path}")
        print(f"Generated: {canvas_path}")
        return 0

    ok = True
    ok &= check_match(flow_path, flow_content)
    ok &= check_match(deps_path, deps_content)
    ok &= check_match(canvas_path, canvas_content)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
