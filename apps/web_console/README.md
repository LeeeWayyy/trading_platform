# apps/web_console (Legacy Backend Services)

**DEPRECATION NOTICE:** The Streamlit UI has been removed as of P5T9. This directory
now contains only backend services and utilities used by `apps/web_console_ng/` (NiceGUI).

## Remaining Modules

| Directory | Purpose | Status |
|-----------|---------|--------|
| `services/` | Backend services (RiskService, AlphaExplorerService, etc.) | Shared with NiceGUI |
| `utils/` | Database utilities, validators | Shared with NiceGUI |
| `data/` | Data access layer (StrategyScopedDataAccess) | Shared with NiceGUI |
| `auth/` | Auth utilities (AuditLogger, permissions) | Shared with NiceGUI |
| `config.py` | Configuration constants | Shared with NiceGUI |

## Migration Path

These modules should eventually be migrated to `libs/` subdirectories:

- `services/` -> `libs/web_console_services/` or specific domain libs
- `utils/` -> `libs/common/` or domain-specific libs
- `data/` -> `libs/data/`
- `auth/` -> `libs/web_console_auth/` (already partially exists)

This migration is deferred to reduce scope of P5T9 and avoid breaking changes.

## What Was Removed

- `pages/` - Streamlit UI pages (replaced by NiceGUI in `apps/web_console_ng/pages/`)
- `components/` - Streamlit UI components (replaced by NiceGUI components)
- `app.py` - Streamlit application entry point
- `Dockerfile` - Streamlit container config
- `requirements.txt` - Streamlit dependencies
- `nginx/` - Streamlit proxy config
- Various Streamlit-specific auth helpers

---
**Last Updated:** 2026-01-04
**Migration:** P5T9 (Streamlit -> NiceGUI)
