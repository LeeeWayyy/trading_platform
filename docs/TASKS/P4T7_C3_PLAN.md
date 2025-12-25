# P4T7 C3: Research Notebook Launcher - Component Plan (STRETCH)

**Component:** C3 - T9.3 Research Notebook Launcher
**Parent Task:** P4T7 Web Console Research & Reporting
**Status:** PLANNING (STRETCH)
**Estimated Effort:** 2-3 days
**Dependencies:** C0 (Prep & Validation)

---

## Overview

Implement T9.3 Research Notebook Launcher that enables researchers to launch pre-configured Jupyter notebooks from the web console.

**Note:** This is a STRETCH item. Implement only if schedule permits after core items (C1, C2, C4, C5) are complete.

## Acceptance Criteria (from P4T7_TASK.md)

- [ ] One-click Jupyter notebook launch from web console
- [ ] Pre-configured environment with PYTHONPATH and data paths set
- [ ] Template notebooks for common analyses (alpha research, factor analysis, backtest review)
- [ ] Session management: start/stop/status of notebook server
- [ ] Security: notebooks run in isolated container with read-only data access
- [ ] Auto-shutdown after 4 hours of inactivity
- [ ] RBAC: LAUNCH_NOTEBOOKS permission required (admin/researcher roles only)

---

## Architecture

### Security Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    Web Console (Host)                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                  Notebook Launcher UI                       ││
│  │  - Start/Stop controls                                      ││
│  │  - Session status display                                   ││
│  │  - Template selection                                       ││
│  └───────────────────────────────────────────────────────────────┘│
│                           │                                     │
│                           ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                  NotebookService                            ││
│  │  - start_session(user_id) → session_url                     ││
│  │  - stop_session(user_id)                                    ││
│  │  - get_session_status(user_id) → SessionStatus              ││
│  └───────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Docker Container (Isolated)                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                  Jupyter Lab Server                         ││
│  │  - Port: 8888 (mapped per user)                             ││
│  │  - Token-based authentication                               ││
│  └───────────────────────────────────────────────────────────────┘│
│                                                                  │
│  MOUNTS:                                                        │
│  ├── data/wrds/        → /data/wrds/         (read-only)       │
│  ├── data/analytics/   → /data/analytics/    (read-only)       │
│  ├── notebooks/user/   → /home/jovyan/work/  (read-write)      │
│  └── notebooks/templates/ → /templates/      (read-only)       │
│                                                                  │
│  ENVIRONMENT:                                                   │
│  ├── PYTHONPATH=/app                                            │
│  ├── DATA_PATH=/data                                            │
│  └── USER_ID=<user_id>                                          │
│                                                                  │
│  LIMITS:                                                        │
│  ├── CPU: 2 cores                                               │
│  ├── Memory: 8GB                                                │
│  └── No network egress (only localhost)                         │
└─────────────────────────────────────────────────────────────────┘
```

### File Structure

```
apps/web_console/
├── pages/
│   └── notebooks.py             # Notebook launcher page
├── services/
│   └── notebook_service.py      # Session management service

notebooks/
├── templates/
│   ├── alpha_research.ipynb     # Alpha research template
│   ├── factor_analysis.ipynb    # Factor analysis template
│   └── backtest_review.ipynb    # Backtest review template
└── docker/
    └── Dockerfile.notebook      # Notebook container image

scripts/
└── launch_notebook.py           # CLI for notebook server

tests/apps/web_console/
└── test_notebook_launcher.py    # Integration tests

docs/CONCEPTS/
└── notebook-launcher.md         # User documentation
```

---

## Implementation Details

### 1. NotebookService

```python
# apps/web_console/services/notebook_service.py
"""Service for managing Jupyter notebook sessions."""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

import docker

logger = logging.getLogger(__name__)

NOTEBOOK_IMAGE = os.getenv("NOTEBOOK_IMAGE", "trading-platform-notebook:latest")
NOTEBOOK_TIMEOUT_HOURS = 4
MAX_CONCURRENT_SESSIONS = 5


class SessionStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class NotebookSession:
    """Active notebook session."""
    user_id: str
    container_id: str
    port: int
    token: str
    status: SessionStatus
    started_at: datetime
    last_activity_at: datetime
    url: str


class NotebookService:
    """Manages Jupyter notebook container sessions."""

    def __init__(self, docker_client: docker.DockerClient | None = None):
        self._client = docker_client or docker.from_env()
        self._sessions: dict[str, NotebookSession] = {}
        self._port_base = 8900  # User ports start here

    def start_session(
        self,
        user_id: str,
        template: str | None = None,
    ) -> NotebookSession:
        """Start a new notebook session for user.

        Args:
            user_id: User requesting notebook
            template: Optional template to pre-load

        Returns:
            NotebookSession with access URL and token

        Raises:
            RuntimeError: If session already exists or max sessions reached
        """
        # Check existing session
        if user_id in self._sessions:
            session = self._sessions[user_id]
            if session.status == SessionStatus.RUNNING:
                return session

        # Check max sessions
        active = sum(1 for s in self._sessions.values() if s.status == SessionStatus.RUNNING)
        if active >= MAX_CONCURRENT_SESSIONS:
            raise RuntimeError(f"Max concurrent sessions ({MAX_CONCURRENT_SESSIONS}) reached")

        # Allocate port
        port = self._allocate_port()

        # Generate token
        token = secrets.token_urlsafe(32)

        # Prepare user workspace
        workspace_path = self._prepare_workspace(user_id, template)

        # Start container
        container = self._client.containers.run(
            NOTEBOOK_IMAGE,
            detach=True,
            name=f"notebook-{user_id}",
            ports={"8888/tcp": port},
            environment={
                "JUPYTER_TOKEN": token,
                "PYTHONPATH": "/app",
                "DATA_PATH": "/data",
                "USER_ID": user_id,
            },
            volumes={
                str(Path("data/wrds").absolute()): {"bind": "/data/wrds", "mode": "ro"},
                str(Path("data/analytics").absolute()): {"bind": "/data/analytics", "mode": "ro"},
                str(workspace_path): {"bind": "/home/jovyan/work", "mode": "rw"},
                str(Path("notebooks/templates").absolute()): {"bind": "/templates", "mode": "ro"},
            },
            mem_limit="8g",
            cpu_quota=200000,  # 2 CPUs
            # NOTE: network_mode="none" prevents port access!
            # Use bridge network with egress controls instead
            network_mode="bridge",
            # Add iptables rules to block external egress while allowing localhost
            # Alternative: Use Docker network with no external connectivity
            remove=True,
        )

        now = datetime.now(UTC)
        session = NotebookSession(
            user_id=user_id,
            container_id=container.id,
            port=port,
            token=token,
            status=SessionStatus.RUNNING,
            started_at=now,
            last_activity_at=now,
            url=f"http://localhost:{port}/lab?token={token}",
        )

        self._sessions[user_id] = session
        logger.info(f"Started notebook session for {user_id} on port {port}")

        return session

    def stop_session(self, user_id: str) -> bool:
        """Stop a user's notebook session.

        Returns:
            True if stopped, False if not found
        """
        if user_id not in self._sessions:
            return False

        session = self._sessions[user_id]
        session.status = SessionStatus.STOPPING

        try:
            container = self._client.containers.get(session.container_id)
            container.stop(timeout=10)
            logger.info(f"Stopped notebook session for {user_id}")
        except docker.errors.NotFound:
            pass

        session.status = SessionStatus.STOPPED
        return True

    def get_session_status(self, user_id: str) -> NotebookSession | None:
        """Get status of user's session."""
        return self._sessions.get(user_id)

    def cleanup_inactive_sessions(self) -> int:
        """Stop sessions inactive for more than NOTEBOOK_TIMEOUT_HOURS.

        Returns:
            Number of sessions stopped
        """
        cutoff = datetime.now(UTC) - timedelta(hours=NOTEBOOK_TIMEOUT_HOURS)
        stopped = 0

        for user_id, session in list(self._sessions.items()):
            if session.last_activity_at < cutoff:
                self.stop_session(user_id)
                stopped += 1

        return stopped

    def _allocate_port(self) -> int:
        """Allocate an available port for notebook."""
        used_ports = {s.port for s in self._sessions.values() if s.status == SessionStatus.RUNNING}
        for port in range(self._port_base, self._port_base + 100):
            if port not in used_ports:
                return port
        raise RuntimeError("No available ports")

    def _prepare_workspace(self, user_id: str, template: str | None) -> Path:
        """Prepare user workspace directory."""
        workspace = Path(f"notebooks/users/{user_id}")
        workspace.mkdir(parents=True, exist_ok=True)

        if template:
            template_path = Path(f"notebooks/templates/{template}.ipynb")
            if template_path.exists():
                import shutil
                shutil.copy(template_path, workspace / f"{template}.ipynb")

        return workspace
```

### 2. Template Notebooks

```python
# notebooks/templates/alpha_research.ipynb (JSON structure)
{
    "cells": [
        {
            "cell_type": "markdown",
            "source": [
                "# Alpha Research Template\n",
                "\n",
                "This notebook provides a starting point for alpha signal research.\n",
                "Data is automatically configured and available at `/data/`."
            ]
        },
        {
            "cell_type": "code",
            "source": [
                "import polars as pl\n",
                "from libs.alpha.research_platform import PITBacktester\n",
                "from libs.alpha.alpha_definition import AlphaDefinition\n",
                "from libs.alpha.metrics import AlphaMetricsAdapter"
            ]
        },
        {
            "cell_type": "code",
            "source": [
                "# Load CRSP data\n",
                "crsp = pl.read_parquet('/data/wrds/crsp/daily/2024.parquet')\n",
                "print(f'Loaded {crsp.height:,} rows')"
            ]
        },
        {
            "cell_type": "markdown",
            "source": ["## Define Your Alpha Signal"]
        },
        {
            "cell_type": "code",
            "source": [
                "class MyAlpha(AlphaDefinition):\n",
                "    name = 'my_alpha'\n",
                "    \n",
                "    def compute(self, prices, fundamentals, as_of_date):\n",
                "        # Your alpha logic here\n",
                "        pass"
            ]
        }
    ]
}
```

### 3. Notebook Launcher Page

```python
# apps/web_console/pages/notebooks.py
"""Research Notebook Launcher page."""

from __future__ import annotations

import os

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.services.notebook_service import NotebookService, SessionStatus

FEATURE_NOTEBOOK_LAUNCHER = os.getenv("FEATURE_NOTEBOOK_LAUNCHER", "false").lower() in {
    "1", "true", "yes", "on",
}

TEMPLATES = [
    ("alpha_research", "Alpha Research", "Template for developing alpha signals"),
    ("factor_analysis", "Factor Analysis", "Template for analyzing factor exposures"),
    ("backtest_review", "Backtest Review", "Template for reviewing backtest results"),
]


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Research Notebooks", page_icon="📓", layout="wide")
    st.title("Research Notebook Launcher")

    if not FEATURE_NOTEBOOK_LAUNCHER:
        st.info("Feature not available.")
        return

    user = get_current_user()
    if not has_permission(user, Permission.LAUNCH_NOTEBOOKS):
        st.error("Permission denied: LAUNCH_NOTEBOOKS required.")
        st.stop()

    user_id = user.get("user_id")
    service = NotebookService()

    # Check existing session
    session = service.get_session_status(user_id)

    if session and session.status == SessionStatus.RUNNING:
        st.success("Your notebook session is running!")

        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.markdown(f"**URL:** [{session.url}]({session.url})")
        with col2:
            st.caption(f"Started: {session.started_at.strftime('%H:%M')}")
        with col3:
            if st.button("Stop Session", type="secondary"):
                service.stop_session(user_id)
                st.rerun()

        st.info("Session will auto-shutdown after 4 hours of inactivity.")

    else:
        st.info("No active notebook session. Start one below.")

        st.subheader("Select Template")

        template_choice = st.radio(
            "Template",
            [t[0] for t in TEMPLATES],
            format_func=lambda x: next(t[1] for t in TEMPLATES if t[0] == x),
            horizontal=True,
        )

        # Show template description
        template_desc = next(t[2] for t in TEMPLATES if t[0] == template_choice)
        st.caption(template_desc)

        if st.button("Launch Notebook", type="primary"):
            with st.spinner("Starting notebook server..."):
                try:
                    session = service.start_session(user_id, template_choice)
                    st.success(f"Notebook ready! [Open Notebook]({session.url})")
                    st.rerun()
                except RuntimeError as e:
                    st.error(f"Failed to start notebook: {e}")

    st.divider()

    # Admin section
    if has_permission(user, Permission.MANAGE_NOTEBOOKS):
        st.subheader("Admin: Active Sessions")
        # Show all active sessions
        st.info("Admin view of active sessions would go here.")


if __name__ == "__main__":
    main()
```

---

## Security Considerations

1. **Container Isolation:** Notebooks run in Docker containers
2. **Network Isolation:** Use bridge network with egress controls (NOT network_mode="none" which blocks port access)
   - Option A: Custom Docker network with no default gateway
   - Option B: iptables rules to block external egress
   - Option C: Docker network policies (requires Docker Enterprise)
3. **Read-Only Data:** Data directories mounted read-only
4. **Resource Limits:** CPU (2 cores) and memory (8GB) caps
5. **Token Auth:** Each session has unique token
6. **Auto-Shutdown:** Sessions terminate after 4 hours idle
7. **RBAC:** Only researchers/admins can launch (LAUNCH_NOTEBOOKS permission)
8. **Session Persistence:** Store session state in Redis/DB (not just in-memory) to survive restarts
9. **Activity Tracking:** Implement heartbeat to update `last_activity_at` via Jupyter API

---

## Testing Strategy

### Integration Tests

```python
# tests/apps/web_console/test_notebook_launcher.py

import pytest
from unittest.mock import MagicMock, patch

from apps.web_console.services.notebook_service import NotebookService, SessionStatus


@pytest.fixture
def mock_docker():
    with patch("docker.from_env") as mock:
        yield mock


def test_start_session_creates_container(mock_docker):
    mock_container = MagicMock()
    mock_container.id = "container123"
    mock_docker.return_value.containers.run.return_value = mock_container

    service = NotebookService(mock_docker.return_value)
    session = service.start_session("test_user")

    assert session.user_id == "test_user"
    assert session.status == SessionStatus.RUNNING
    assert session.token is not None


def test_stop_session_stops_container(mock_docker):
    mock_container = MagicMock()
    mock_docker.return_value.containers.get.return_value = mock_container

    service = NotebookService(mock_docker.return_value)
    service._sessions["test_user"] = MagicMock(
        container_id="container123",
        status=SessionStatus.RUNNING,
    )

    result = service.stop_session("test_user")

    assert result is True
    mock_container.stop.assert_called_once()
```

---

## Deliverables

1. **NotebookService:** Container session management
2. **Template Notebooks:** Alpha research, factor analysis, backtest review
3. **Dockerfile.notebook:** Container image definition
4. **Notebook Launcher Page:** Streamlit UI
5. **Tests:** Integration tests with Docker mocks
6. **Documentation:** `docs/CONCEPTS/notebook-launcher.md`

---

## Verification Checklist

- [ ] Notebook container starts successfully
- [ ] Data mounted read-only
- [ ] Token authentication works
- [ ] Auto-shutdown after inactivity
- [ ] Template notebooks pre-loaded
- [ ] RBAC enforcement tested
- [ ] Resource limits enforced
- [ ] All tests pass
