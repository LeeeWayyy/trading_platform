# Architecture Visualization

This folder contains the auto-generated architecture maps for the trading platform.

## Files

### Diagrams
- `system_map_flow.md` — **High-level data flow** (Mermaid TB). Shows virtual edges representing system data flows between services and external systems.
- `system_map_deps.md` — **Code dependencies** (Mermaid TB). Shows filtered AST-discovered import relationships with noise reduction.
- `system_map.canvas` — **Obsidian Canvas JSON**. Combined view with layered layout, virtual edges, and dependencies.

### Configuration
- `system_map.config.json` — Configuration for layer taxonomy, component mapping, virtual edges, external nodes, and filtering rules.
- `system_map.schema.json` — JSON schema for config validation.

## Architecture Layers

The system is organized into 5 layers (top to bottom):

1. **Presentation Layer** — Web consoles and auth services (user-facing)
2. **Orchestration** — Orchestrator service (coordinates trading workflow)
3. **Core Services** — Signal, Execution, Market Data, Model Registry, etc.
4. **Domain Logic** — Libraries for strategies, data, risk, analytics
5. **Infrastructure** — Common libs, Redis client, secrets, external systems

## Viewing & Editing

### Mermaid (GitHub/VSCode)
Open `system_map_flow.md` or `system_map_deps.md` in any Markdown viewer that supports Mermaid.
- **Click any node** to navigate to its specification in `../SPECS/`

### Obsidian Canvas
1. Open the repository in Obsidian.
2. Navigate to `docs/ARCHITECTURE/system_map.canvas`.
3. Use the Canvas UI to pan, zoom, and explore nodes.
4. Nodes link to their spec files (e.g., `[[../SPECS/services/orchestrator.md|Orchestrator]]`)

## Regenerating the Map

The map is generated from:
1. **Configuration** (`system_map.config.json`) — Layer assignments, virtual edges, external nodes
2. **AST scanning** — Python imports within `apps/`, `libs/`, `strategies/`

```bash
# Generate / overwrite the outputs
python3 scripts/dev/generate_architecture.py --generate

# CI drift check (fails if outputs are stale or components unmapped)
python3 scripts/dev/generate_architecture.py --check
```

## Adding New Components

When adding a new service, library, or strategy:

1. **Add to config** — Edit `system_map.config.json`:
   ```json
   "components": {
     "apps/new_service": {
       "layer": "core",
       "spec": "../SPECS/services/new_service.md"
     }
   }
   ```

2. **Create spec file** — Add `docs/SPECS/services/new_service.md` using the template.

3. **Regenerate** — Run `python3 scripts/dev/generate_architecture.py --generate`

4. **CI enforces** — The `--check` mode will fail if:
   - A component exists but is not in the config
   - A spec file referenced in the config is missing

## Virtual Edges (Data Flows)

Virtual edges represent high-level data flows that aren't captured by import analysis:

```json
"virtual_edges": [
  {"from": "apps/orchestrator", "to": "apps/signal_service", "type": "data_flow", "label": "request signals"},
  {"from": "apps/execution_gateway", "to": "ext_alpaca", "type": "data_flow", "label": "submit orders"}
]
```

Edge types and colors (in Canvas):
- `data_flow` — Data moving between components (green)
- `control` — Control flow (blue)
- `event` — Event-driven communication (pink)

## Filtering Rules

To reduce noise, the dependency diagram filters edges to common infrastructure libs:

```json
"filtering": {
  "hide_to_common_libs": true,
  "common_libs": ["libs/common", "libs/secrets", "libs/health"],
  "allowlist": ["apps/execution_gateway", "apps/signal_service", "apps/orchestrator", ...]
}
```

- Edges to `common_libs` are hidden unless the source is in the `allowlist`
- Critical services always show their infrastructure dependencies

## Notes

- The graph only includes **internal** imports (`apps.*`, `libs.*`, `strategies.*`).
- Parse errors are logged as warnings and skipped so a single broken file does not block generation.
- External nodes (Redis, Postgres, Alpaca API) are shown in the flow diagram with cylinder shapes.
- All nodes link to their specification documents for documentation navigation.
