# Research Notebook Launcher

## Plain English Explanation
The research notebook launcher lets researchers start a pre-configured Jupyter
notebook directly from the web console. Instead of setting up local environments
and wiring data paths by hand, users choose a template, fill in parameters, and
get a running notebook session that already knows where platform data lives.

## Why It Matters
Research velocity depends on fast iteration. A launcher removes friction by
standardizing environments, ensuring consistent paths, and providing repeatable
analysis entry points. It also reduces accidental data exposure by centralizing
access controls and session controls.

## Templates
The MVP ships with three templates (defined in
`apps/web_console/services/notebook_launcher_service.py`):

- **Alpha Research**: analyze a single alpha signal and its IC decay.
- **Factor Analysis**: inspect factor exposures and contributions.
- **Backtest Review**: review stored backtest results and diagnostics.

Template metadata drives the UI and can be extended with new parameters or
additional notebook paths.

## Parameters
Each template declares a list of parameters with types such as `text`, `date`,
`int`, `float`, `bool`, or `select`. The Streamlit UI renders inputs dynamically
based on this metadata. Required parameters should be validated in the UI or by
notebook startup scripts.

## Session Management
Sessions are tracked in-memory by the web console service. Each session records:

- Template ID and parameter values
- Status (`starting`, `running`, `stopping`, `stopped`, `error`)
- Process ID (for MVP subprocess launches)
- Port, token, and access URL (if configured)

Users can terminate active sessions from the web console, and the service
updates status accordingly.

## Security & Access Control
Launching notebooks requires the `LAUNCH_NOTEBOOKS` permission. The service
checks permissions before listing templates, launching notebooks, or terminating
sessions. Use this permission for researcher/admin roles only.

## Configuration
The notebook launcher reads environment variables to control runtime behavior:

- `NOTEBOOK_LAUNCH_COMMAND`: Command template used to start notebooks.
  - Supports placeholders: `{template_id}`, `{template_path}`, `{session_id}`,
    `{port}`, `{token}`.
- `NOTEBOOK_BASE_URL`: Base URL for constructing the access link.
- `NOTEBOOK_PORT_BASE`: Starting port for sessions (default 8900).
- `NOTEBOOK_PORT_SPAN`: Number of ports to consider (default 50).

## Next Steps
- Add Docker-based isolation and persistent session storage (Redis).
- Wire auto-shutdown after inactivity (heartbeat + TTL).
- Create real notebook templates under `notebooks/templates/`.
