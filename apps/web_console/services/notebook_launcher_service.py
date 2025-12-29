"""Service for launching research notebook sessions."""

from __future__ import annotations

import json
import logging
import os
import secrets
import shlex
import signal
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)

_DEFAULT_PORT_BASE = 8900
_DEFAULT_PORT_SPAN = 50


class SessionStatus(str, Enum):
    """Lifecycle state of a notebook session."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class NotebookParameter:
    """Notebook parameter definition for dynamic UI rendering."""

    key: str
    label: str
    kind: str
    default: Any | None = None
    required: bool = False
    options: list[str] | None = None
    help: str | None = None


@dataclass(frozen=True)
class NotebookTemplate:
    """Notebook template metadata."""

    template_id: str
    name: str
    description: str
    notebook_path: str | None = None
    parameters: tuple[NotebookParameter, ...] = ()


@dataclass
class NotebookSession:
    """Notebook session metadata for UI rendering and control."""

    session_id: str
    template_id: str
    parameters: dict[str, Any]
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    process_id: int | None = None
    port: int | None = None
    token: str | None = None
    access_url: str | None = None
    error_message: str | None = None
    command: list[str] | None = None


class NotebookLauncherService:
    """Manage notebook templates and runtime sessions for the web console."""

    def __init__(
        self,
        *,
        user: dict[str, Any],
        templates: Iterable[NotebookTemplate] | None = None,
        session_store: dict[str, NotebookSession] | None = None,
        port_base: int | None = None,
        port_span: int | None = None,
    ) -> None:
        self._user = user
        self._templates = list(templates or self._default_templates())
        self._sessions = session_store if session_store is not None else {}
        self._port_base = port_base or int(os.getenv("NOTEBOOK_PORT_BASE", _DEFAULT_PORT_BASE))
        self._port_span = port_span or int(os.getenv("NOTEBOOK_PORT_SPAN", _DEFAULT_PORT_SPAN))

    def list_templates(self) -> list[NotebookTemplate]:
        """Return available notebook templates."""

        self._require_permission()
        return list(self._templates)

    def create_notebook(self, template_id: str, parameters: dict[str, Any]) -> NotebookSession:
        """Spawn a notebook session for the selected template."""

        self._require_permission()

        template = self._find_template(template_id)
        if template is None:
            raise ValueError(f"Unknown template_id: {template_id}")

        now = datetime.now(UTC)
        session_id = uuid4().hex
        token = secrets.token_urlsafe(16)
        port = self._allocate_port()
        access_url = self._build_access_url(port=port, token=token)

        session = NotebookSession(
            session_id=session_id,
            template_id=template_id,
            parameters=dict(parameters),
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            port=port,
            token=token,
            access_url=access_url,
        )
        self._sessions[session_id] = session

        try:
            command = self._build_launch_command(
                template=template,
                session_id=session_id,
                port=port,
                token=token,
            )
            env = self._build_launch_env(template, session, command)
            process = subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            session.process_id = process.pid
            session.command = command
            session.status = SessionStatus.RUNNING
            session.updated_at = datetime.now(UTC)
        except Exception as exc:  # pragma: no cover - defensive logging
            session.status = SessionStatus.ERROR
            session.error_message = str(exc)
            session.updated_at = datetime.now(UTC)
            logger.exception(
                "notebook_launch_failed",
                extra={
                    "template_id": template_id,
                    "session_id": session_id,
                    "user_id": self._user.get("user_id"),
                },
            )

        return session

    def get_session_status(self, session_id: str) -> SessionStatus:
        """Return the status for the requested session."""

        self._require_permission()

        session = self._sessions.get(session_id)
        if session is None:
            return SessionStatus.STOPPED

        # Check liveness for STARTING, RUNNING, and STOPPING states
        if session.process_id and session.status in {
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
            SessionStatus.STOPPING,
        }:
            if not self._is_process_alive(session.process_id):
                session.status = SessionStatus.STOPPED
                session.updated_at = datetime.now(UTC)

        return session.status

    def terminate_session(self, session_id: str) -> bool:
        """Terminate a running notebook session.

        Sets status to STOPPING and sends SIGTERM. The status will be updated
        to STOPPED by get_session_status once the process is confirmed dead.
        """

        self._require_permission()

        session = self._sessions.get(session_id)
        if session is None:
            return False

        session.status = SessionStatus.STOPPING
        session.updated_at = datetime.now(UTC)

        if session.process_id is None:
            session.status = SessionStatus.STOPPED
            session.updated_at = datetime.now(UTC)
            return True

        try:
            os.kill(session.process_id, signal.SIGTERM)
            # Keep status as STOPPING - get_session_status will mark STOPPED
            # once process is confirmed dead (handles stubborn processes)
            return True
        except ProcessLookupError:
            # Process already dead
            session.status = SessionStatus.STOPPED
            session.updated_at = datetime.now(UTC)
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            session.status = SessionStatus.ERROR
            session.error_message = str(exc)
            session.updated_at = datetime.now(UTC)
            logger.exception(
                "notebook_terminate_failed",
                extra={
                    "session_id": session_id,
                    "process_id": session.process_id,
                    "user_id": self._user.get("user_id"),
                },
            )
            return False

    def list_sessions(self, *, include_stopped: bool = False) -> list[NotebookSession]:
        """Return tracked sessions for UI rendering."""

        self._require_permission()
        sessions = list(self._sessions.values())
        if include_stopped:
            return sessions
        return [session for session in sessions if session.status != SessionStatus.STOPPED]

    def _require_permission(self) -> None:
        if not has_permission(self._user, Permission.LAUNCH_NOTEBOOKS):
            logger.warning(
                "notebook_permission_denied",
                extra={
                    "user_id": self._user.get("user_id"),
                    "permission": Permission.LAUNCH_NOTEBOOKS.value,
                },
            )
            raise PermissionError(f"Permission {Permission.LAUNCH_NOTEBOOKS.value} required")

    def _find_template(self, template_id: str) -> NotebookTemplate | None:
        for template in self._templates:
            if template.template_id == template_id:
                return template
        return None

    def _build_access_url(self, *, port: int | None, token: str | None) -> str | None:
        base_url = os.getenv("NOTEBOOK_BASE_URL")
        if not base_url or port is None:
            return None
        base_url = base_url.rstrip("/")
        if token:
            return f"{base_url}:{port}/?token={token}"
        return f"{base_url}:{port}/"

    def _build_launch_command(
        self,
        *,
        template: NotebookTemplate,
        session_id: str,
        port: int | None,
        token: str | None,
    ) -> list[str]:
        command_template = os.getenv("NOTEBOOK_LAUNCH_COMMAND")
        if not command_template:
            raise RuntimeError("NOTEBOOK_LAUNCH_COMMAND is not configured")

        template_path = template.notebook_path or ""
        try:
            formatted = command_template.format(
                template_id=template.template_id,
                template_path=template_path,
                session_id=session_id,
                port=port or "",
                token=token or "",
            )
        except KeyError as exc:
            raise ValueError("NOTEBOOK_LAUNCH_COMMAND template is missing placeholders") from exc

        return shlex.split(formatted)

    def _build_launch_env(
        self,
        template: NotebookTemplate,
        session: NotebookSession,
        command: list[str],
    ) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "NOTEBOOK_TEMPLATE_ID": template.template_id,
                "NOTEBOOK_TEMPLATE_PATH": template.notebook_path or "",
                "NOTEBOOK_SESSION_ID": session.session_id,
                "NOTEBOOK_TOKEN": session.token or "",
                "NOTEBOOK_PORT": str(session.port or ""),
                "NOTEBOOK_PARAMETERS": json.dumps(
                    session.parameters, default=self._json_default
                ),
                "NOTEBOOK_LAUNCH_COMMAND": " ".join(command),
            }
        )
        return env

    def _allocate_port(self) -> int:
        used_ports = {
            session.port
            for session in self._sessions.values()
            if session.port is not None and session.status != SessionStatus.STOPPED
        }
        for offset in range(self._port_span):
            port = self._port_base + offset
            if port in used_ports:
                continue
            # Verify port is actually free on the host (handles multi-user/multi-tab)
            if self._is_port_available(port):
                return port
        raise RuntimeError("No available notebook ports")

    @staticmethod
    def _is_port_available(port: int) -> bool:
        """Check if a port is available by attempting to bind to it."""
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    @staticmethod
    def _is_process_alive(process_id: int) -> bool:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _default_templates() -> list[NotebookTemplate]:
        return [
            NotebookTemplate(
                template_id="alpha_research",
                name="Alpha Research",
                description="Explore alpha signal behavior and IC decay.",
                notebook_path="notebooks/templates/alpha_research.ipynb",
                parameters=(
                    NotebookParameter(
                        key="alpha_name",
                        label="Alpha Name",
                        kind="text",
                        default="momentum_alpha",
                        required=True,
                        help="Name of the alpha signal to analyze.",
                    ),
                    NotebookParameter(
                        key="start_date",
                        label="Start Date",
                        kind="date",
                        required=True,
                        help="Start of the analysis window.",
                    ),
                    NotebookParameter(
                        key="end_date",
                        label="End Date",
                        kind="date",
                        required=True,
                        help="End of the analysis window.",
                    ),
                ),
            ),
            NotebookTemplate(
                template_id="factor_analysis",
                name="Factor Analysis",
                description="Inspect factor exposures and contributions.",
                notebook_path="notebooks/templates/factor_analysis.ipynb",
                parameters=(
                    NotebookParameter(
                        key="portfolio_id",
                        label="Portfolio",
                        kind="text",
                        default="global",
                        required=True,
                        help="Portfolio identifier to analyze.",
                    ),
                    NotebookParameter(
                        key="as_of_date",
                        label="As of Date",
                        kind="date",
                        required=True,
                    ),
                ),
            ),
            NotebookTemplate(
                template_id="backtest_review",
                name="Backtest Review",
                description="Review backtest results and diagnostics.",
                notebook_path="notebooks/templates/backtest_review.ipynb",
                parameters=(
                    NotebookParameter(
                        key="backtest_id",
                        label="Backtest ID",
                        kind="text",
                        required=True,
                        help="Backtest job identifier to load.",
                    ),
                ),
            ),
        ]

    @staticmethod
    def _json_default(value: Any) -> str:
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return str(value)


__all__ = [
    "NotebookLauncherService",
    "NotebookTemplate",
    "NotebookSession",
    "NotebookParameter",
    "SessionStatus",
]
