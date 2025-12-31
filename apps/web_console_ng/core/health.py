"""Health endpoint registration for NiceGUI app."""

from __future__ import annotations

from nicegui import app


def setup_health_endpoint() -> None:
    """Register the /health endpoint on the FastAPI app."""

    @app.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}
