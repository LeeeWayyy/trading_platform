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
    """Manages Jupyter notebook container sessions.

    Session state is persisted to Redis for:
    1. Survival across web console restarts
    2. Consistency across multiple web console instances
    3. Activity tracking via heartbeat updates

    Uses AsyncRedisAdapter from apps.web_console.utils.db_pool which has
    an async context manager `.client()` method.

    NOTE: Docker SDK is synchronous. All Docker operations are wrapped in
    asyncio.run_in_executor() to prevent blocking the event loop.
    """

    def __init__(
        self,
        docker_client: docker.DockerClient | None = None,
        redis_adapter=None,  # AsyncRedisAdapter from apps.web_console.utils.db_pool
    ):
        self._client = docker_client or docker.from_env()
        self._redis_adapter = redis_adapter  # For session persistence
        self._sessions: dict[str, NotebookSession] = {}  # Local cache
        self._port_base = 8900  # User ports start here
        self._session_ttl_hours = NOTEBOOK_TIMEOUT_HOURS

    # === Docker Async Wrappers ===
    # Docker SDK is synchronous. These wrappers run Docker operations in
    # a thread executor to prevent blocking the async event loop.

    async def _get_container_async(self, container_id: str):
        """Get container by ID (non-blocking)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._client.containers.get, container_id
        )

    async def _run_container_async(self, *args, **kwargs):
        """Run a new container (non-blocking)."""
        import asyncio
        from functools import partial
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(self._client.containers.run, *args, **kwargs)
        )

    async def _stop_container_async(self, container, timeout: int = 10):
        """Stop a container (non-blocking)."""
        import asyncio
        from functools import partial
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(container.stop, timeout=timeout)
        )

    async def _get_container_status_async(self, container) -> str:
        """Get container status (non-blocking)."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: container.status)

    async def _persist_session(self, session: NotebookSession) -> None:
        """Persist session state to Redis.

        Uses AsyncRedisAdapter.client() async context manager.
        """
        if self._redis_adapter is None:
            return  # Fall back to in-memory only
        import json
        key = f"notebook:session:{session.user_id}"
        data = {
            "user_id": session.user_id,
            "container_id": session.container_id,
            "port": session.port,
            "token": session.token,
            "status": session.status.value,
            "started_at": session.started_at.isoformat(),
            "last_activity_at": session.last_activity_at.isoformat(),
            "url": session.url,
        }
        # Use adapter's async context manager
        async with self._redis_adapter.client() as redis:
            await redis.setex(key, self._session_ttl_hours * 3600, json.dumps(data))

    async def _load_session(self, user_id: str) -> NotebookSession | None:
        """Load session from Redis.

        Uses AsyncRedisAdapter.client() async context manager.
        """
        if self._redis_adapter is None:
            return self._sessions.get(user_id)
        import json
        key = f"notebook:session:{user_id}"
        # Use adapter's async context manager
        async with self._redis_adapter.client() as redis:
            data = await redis.get(key)
        if not data:
            return None
        parsed = json.loads(data)
        return NotebookSession(
            user_id=parsed["user_id"],
            container_id=parsed["container_id"],
            port=parsed["port"],
            token=parsed["token"],
            status=SessionStatus(parsed["status"]),
            started_at=datetime.fromisoformat(parsed["started_at"]),
            last_activity_at=datetime.fromisoformat(parsed["last_activity_at"]),
            url=parsed["url"],
        )

    async def _delete_session(self, user_id: str) -> None:
        """Delete session from Redis.

        Uses AsyncRedisAdapter.client() async context manager.
        """
        if self._redis_adapter is not None:
            async with self._redis_adapter.client() as redis:
                await redis.delete(f"notebook:session:{user_id}")
        self._sessions.pop(user_id, None)

    async def _load_all_sessions(self) -> list[NotebookSession]:
        """Load all active sessions from Redis.

        Used for accurate session counting and port allocation across
        multiple web console instances and restarts.

        Returns:
            List of all active NotebookSession objects from Redis
        """
        if self._redis_adapter is None:
            return list(self._sessions.values())

        import json
        sessions = []
        async with self._redis_adapter.client() as redis:
            # Scan for all session keys
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match="notebook:session:*", count=100
                )
                for key in keys:
                    data = await redis.get(key)
                    if data:
                        try:
                            parsed = json.loads(data)
                            session = NotebookSession(
                                user_id=parsed["user_id"],
                                container_id=parsed["container_id"],
                                port=parsed["port"],
                                token=parsed["token"],
                                status=SessionStatus(parsed["status"]),
                                started_at=datetime.fromisoformat(parsed["started_at"]),
                                last_activity_at=datetime.fromisoformat(parsed["last_activity_at"]),
                                url=parsed["url"],
                            )
                            sessions.append(session)
                        except (json.JSONDecodeError, KeyError):
                            continue
                if cursor == 0:
                    break
        return sessions

    async def start_session(
        self,
        user_id: str,
        template: str | None = None,
    ) -> NotebookSession:
        """Start a new notebook session for user.

        Persists session to Redis for survival across restarts.

        Args:
            user_id: User requesting notebook
            template: Optional template to pre-load

        Returns:
            NotebookSession with access URL and token

        Raises:
            RuntimeError: If session already exists or max sessions reached
        """
        # Check existing session (Redis first, then local cache)
        existing = await self._load_session(user_id)
        if existing and existing.status == SessionStatus.RUNNING:
            # Verify container is still running (async to not block event loop)
            try:
                container = await self._get_container_async(existing.container_id)
                status = await self._get_container_status_async(container)
                if status == "running":
                    return existing
            except docker.errors.NotFound:
                # Container gone, delete stale session
                await self._delete_session(user_id)

        # Check max sessions (from Redis for accuracy across instances/restarts)
        all_sessions = await self._load_all_sessions()
        active = sum(1 for s in all_sessions if s.status == SessionStatus.RUNNING)
        if active >= MAX_CONCURRENT_SESSIONS:
            raise RuntimeError(f"Max concurrent sessions ({MAX_CONCURRENT_SESSIONS}) reached")

        # Allocate port (from Redis for accuracy across instances/restarts)
        port = await self._allocate_port_async(all_sessions)

        # Generate token
        token = secrets.token_urlsafe(32)

        # SECURITY: Sanitize user_id for container name and filesystem path
        # Only allow alphanumeric, underscore, and hyphen to prevent:
        # - Path traversal (../, /)
        # - Invalid container names (special chars)
        import re
        safe_user_id = re.sub(r"[^a-zA-Z0-9_-]", "_", user_id)[:32]  # Limit length too
        if safe_user_id != user_id:
            logger.info(f"Sanitized user_id: {user_id!r} -> {safe_user_id!r}")

        # Prepare user workspace (uses sanitized user_id)
        workspace_path = self._prepare_workspace(safe_user_id, template)

        # Start container (uses sanitized user_id for container name)
        # Use async wrapper to not block event loop during container creation
        container = await self._run_container_async(
            NOTEBOOK_IMAGE,
            detach=True,
            name=f"notebook-{safe_user_id}",
            ports={"8888/tcp": port},
            environment={
                "JUPYTER_TOKEN": token,
                "PYTHONPATH": "/app",
                "DATA_PATH": "/data",
                "USER_ID": user_id,  # Original user_id for app logic (safe in env var)
            },
            volumes={
                str(Path("data/wrds").absolute()): {"bind": "/data/wrds", "mode": "ro"},
                str(Path("data/analytics").absolute()): {"bind": "/data/analytics", "mode": "ro"},
                str(workspace_path): {"bind": "/home/jovyan/work", "mode": "rw"},
                str(Path("notebooks/templates").absolute()): {"bind": "/templates", "mode": "ro"},
            },
            mem_limit="8g",
            cpu_quota=200000,  # 2 CPUs
            # SECURITY: Use internal Docker network with no external egress
            # The "notebook-internal" network must be created with --internal flag:
            #   docker network create --internal notebook-internal
            # This prevents containers from reaching external networks
            # Port publishing still works for localhost access via host mapping
            network="notebook-internal",
            # Alternative for quick testing (less secure): network_mode="bridge"
            remove=True,
        )

        # SECURITY VERIFICATION: Verify container has no external connectivity
        # This is a defense-in-depth check - the internal network should prevent this
        # Test: exec into container and run `curl -s --max-time 5 https://example.com`
        # Expected: connection timeout or failure

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

        # Persist to Redis for survival across restarts
        await self._persist_session(session)

        logger.info(f"Started notebook session for {user_id} on port {port}")

        return session

    async def stop_session(self, user_id: str) -> bool:
        """Stop a user's notebook session.

        Returns:
            True if stopped, False if not found
        """
        session = await self._load_session(user_id)
        if not session:
            return False

        session.status = SessionStatus.STOPPING

        try:
            # Use async wrappers to not block event loop during container stop
            container = await self._get_container_async(session.container_id)
            await self._stop_container_async(container, timeout=10)
            logger.info(f"Stopped notebook session for {user_id}")
        except docker.errors.NotFound:
            pass

        # Remove from Redis and local cache
        await self._delete_session(user_id)
        return True

    async def get_session_status(self, user_id: str) -> NotebookSession | None:
        """Get status of user's session.

        Loads from Redis first, then local cache.
        """
        return await self._load_session(user_id)

    async def heartbeat(self, user_id: str) -> bool:
        """Update session activity timestamp to prevent auto-shutdown.

        The frontend should call this periodically (e.g., every 5 minutes)
        while the notebook iframe is active. This prevents cleanup_inactive_sessions
        from terminating sessions that are actually in use.

        Args:
            user_id: User whose session to update

        Returns:
            True if session updated, False if not found
        """
        session = await self._load_session(user_id)
        if not session:
            return False

        # Update activity timestamp
        session.last_activity_at = datetime.now(UTC)

        # Persist updated session to Redis
        await self._persist_session(session)

        # Also update local cache if present
        if user_id in self._sessions:
            self._sessions[user_id] = session

        logger.debug(f"Heartbeat received for {user_id}")
        return True

    async def cleanup_inactive_sessions(self) -> int:
        """Stop sessions inactive for more than NOTEBOOK_TIMEOUT_HOURS.

        Loads sessions from Redis (not just in-memory cache) to handle
        sessions persisted after restart or created by other instances.

        Returns:
            Number of sessions stopped
        """
        cutoff = datetime.now(UTC) - timedelta(hours=NOTEBOOK_TIMEOUT_HOURS)
        stopped = 0

        # Load ALL sessions from Redis for accurate cleanup across instances/restarts
        all_sessions = await self._load_all_sessions()

        for session in all_sessions:
            if session.last_activity_at < cutoff:
                await self.stop_session(session.user_id)  # stop_session is async
                stopped += 1

        return stopped

    async def _allocate_port_async(
        self,
        all_sessions: list[NotebookSession] | None = None,
    ) -> int:
        """Allocate an available port for notebook with atomic reservation.

        Uses Redis SETNX for atomic port reservation to prevent race conditions
        when multiple start_session calls happen concurrently.

        Args:
            all_sessions: Pre-loaded sessions list. If None, loads from Redis.

        Returns:
            Available port number.

        Raises:
            RuntimeError: If no ports available.
        """
        if all_sessions is None:
            all_sessions = await self._load_all_sessions()
        used_ports = {s.port for s in all_sessions if s.status == SessionStatus.RUNNING}

        if self._redis_adapter is not None:
            # ATOMIC RESERVATION: Use Redis SETNX to claim port
            # This prevents race conditions when concurrent requests try same port
            async with self._redis_adapter.client() as redis:
                for port in range(self._port_base, self._port_base + 100):
                    if port in used_ports:
                        continue
                    # Try to atomically reserve this port with SETNX
                    # TTL of 60s covers container startup; session persist extends it
                    key = f"notebook:port_lock:{port}"
                    acquired = await redis.setnx(key, "reserved")
                    if acquired:
                        await redis.expire(key, 60)  # Short TTL during startup
                        return port
                    # Port claimed by another request, try next
        else:
            # Fallback for no Redis (tests, dev)
            for port in range(self._port_base, self._port_base + 100):
                if port not in used_ports:
                    return port

        raise RuntimeError("No available ports")

    def _prepare_workspace(self, user_id: str, template: str | None) -> Path:
        """Prepare user workspace directory.

        SECURITY: template is validated against ALLOWED_TEMPLATES allowlist
        to prevent path traversal attacks.
        """
        # user_id should already be sanitized by caller (start_session)
        workspace = Path(f"notebooks/users/{user_id}")
        workspace.mkdir(parents=True, exist_ok=True)

        if template:
            # SECURITY: Validate template against allowlist to prevent path traversal
            # Template names must match exactly - no path components allowed
            # NOTE: Must match TEMPLATES list in notebook_launcher.py page
            ALLOWED_TEMPLATES = {"alpha_research", "factor_analysis", "backtest_review"}
            if template not in ALLOWED_TEMPLATES:
                logger.warning(f"Invalid template requested: {template!r}")
                raise ValueError(f"Invalid template: {template}")

            templates_dir = Path("notebooks/templates").resolve()
            template_path = templates_dir / f"{template}.ipynb"

            # Defense in depth: Verify resolved path is under templates_dir
            if not template_path.resolve().is_relative_to(templates_dir):
                logger.error(f"Path traversal attempt detected: {template}")
                raise ValueError(f"Invalid template path: {template}")

            if template_path.exists():
                import shutil
                shutil.copy(template_path, workspace / f"{template}.ipynb")
            else:
                logger.warning(f"Template file not found: {template_path}")

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

    # Initialize service with Redis adapter for session persistence
    # get_redis_client returns AsyncRedisAdapter which has an async client() context manager
    # Pass the adapter directly - NotebookService will use adapter.client() context manager
    from apps.web_console.utils.db_pool import get_redis_client
    redis_adapter = get_redis_client()
    # NotebookService accepts the adapter and uses its .client() async context manager
    service = NotebookService(redis_adapter=redis_adapter)

    # Check existing session (async)
    # run_async is in async_helpers module
    from apps.web_console.utils.async_helpers import run_async
    session = run_async(service.get_session_status(user_id))

    if session and session.status == SessionStatus.RUNNING:
        st.success("Your notebook session is running!")

        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.markdown(f"**URL:** {session.url}")
        with col2:
            st.caption(f"Started: {session.started_at.strftime('%H:%M')}")
        with col3:
            if st.button("Stop Session", type="secondary"):
                run_async(service.stop_session(user_id))
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
                    session = run_async(service.start_session(user_id, template_choice))
                    st.success(f"Notebook ready! Open Notebook: {session.url}")
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
8. **Session Persistence:** Session state stored in Redis with TTL to survive restarts
9. **Activity Tracking:** Implement heartbeat to update `last_activity_at` via Jupyter API
10. **Admin Permission:** MANAGE_NOTEBOOKS permission required to view/stop other users' sessions

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


@pytest.mark.asyncio
async def test_start_session_creates_container(mock_docker):
    """Test that start_session creates a Docker container.

    start_session is async, so must use await.
    """
    mock_container = MagicMock()
    mock_container.id = "container123"
    mock_docker.return_value.containers.run.return_value = mock_container

    service = NotebookService(mock_docker.return_value)
    session = await service.start_session("test_user")

    assert session.user_id == "test_user"
    assert session.status == SessionStatus.RUNNING
    assert session.token is not None


@pytest.mark.asyncio
async def test_stop_session_stops_container(mock_docker):
    """Test that stop_session stops and removes the container.

    stop_session is async, so must use await.
    """
    mock_container = MagicMock()
    mock_docker.return_value.containers.get.return_value = mock_container

    service = NotebookService(mock_docker.return_value)
    service._sessions["test_user"] = MagicMock(
        container_id="container123",
        status=SessionStatus.RUNNING,
    )

    result = await service.stop_session("test_user")

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
7. **Docker Network Setup:** Script/docs for creating internal network

### Network Isolation Setup (Required)

Before running notebook containers, create the internal Docker network:

```bash
# Create internal network (no external egress)
docker network create --internal notebook-internal

# Verify network exists
docker network inspect notebook-internal
```

Add to `infra/docker-compose.yml`:
```yaml
networks:
  notebook-internal:
    driver: bridge
    internal: true  # Prevents external egress
```

---

## Verification Checklist

- [ ] Notebook container starts successfully
- [ ] Data mounted read-only
- [ ] Token authentication works
- [ ] Auto-shutdown after inactivity
- [ ] Template notebooks pre-loaded
- [ ] RBAC enforcement tested
- [ ] Resource limits enforced
- [ ] **Network isolation verified** (container cannot reach external hosts)
- [ ] All tests pass
