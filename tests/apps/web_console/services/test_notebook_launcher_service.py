"""Tests for NotebookLauncherService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.web_console.services.notebook_launcher_service import (
    NotebookLauncherService,
    SessionStatus,
)


def test_list_templates_returns_entries() -> None:
    service = NotebookLauncherService(user={"role": "researcher"})

    templates = service.list_templates()

    assert templates
    assert templates[0].template_id


def test_create_notebook_with_mocked_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")

    service = NotebookLauncherService(user={"role": "researcher"})
    template_id = service.list_templates()[0].template_id

    mock_process = MagicMock()
    mock_process.pid = 4242

    with patch(
        "apps.web_console.services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ) as mock_popen:
        session = service.create_notebook(template_id, parameters={"alpha": "momentum"})

    assert session.status == SessionStatus.RUNNING
    assert session.process_id == 4242
    mock_popen.assert_called_once()


def test_permission_denied_without_launch_notebooks() -> None:
    service = NotebookLauncherService(user={"role": "viewer"})

    with pytest.raises(PermissionError):
        service.list_templates()


def test_terminate_session_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify terminate_session sends SIGTERM and sets STOPPING status."""
    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")

    service = NotebookLauncherService(user={"role": "researcher"})
    template_id = service.list_templates()[0].template_id

    mock_process = MagicMock()
    mock_process.pid = 4242

    with patch(
        "apps.web_console.services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ):
        session = service.create_notebook(template_id, parameters={})

    # Mock os.kill to succeed (SIGTERM sent but process still running)
    with patch("os.kill") as mock_kill:
        mock_kill.return_value = None
        success = service.terminate_session(session.session_id)

    assert success is True
    # After terminate, status should be STOPPING (not STOPPED yet)
    # Process is still alive, so get_session_status keeps it as STOPPING
    with patch("os.kill") as mock_alive_check:
        mock_alive_check.return_value = None  # Process still alive
        status = service.get_session_status(session.session_id)
    assert status == SessionStatus.STOPPING

    # Once process dies, status becomes STOPPED
    with patch("os.kill", side_effect=ProcessLookupError):
        status = service.get_session_status(session.session_id)
    assert status == SessionStatus.STOPPED


def test_get_session_status_detects_dead_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_session_status detects zombie/dead processes."""
    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")

    service = NotebookLauncherService(user={"role": "researcher"})
    template_id = service.list_templates()[0].template_id

    mock_process = MagicMock()
    mock_process.pid = 9999

    with patch(
        "apps.web_console.services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ):
        session = service.create_notebook(template_id, parameters={})

    # Mock os.kill to raise ProcessLookupError (process died)
    with patch("os.kill", side_effect=ProcessLookupError):
        # get_session_status returns SessionStatus enum directly
        status = service.get_session_status(session.session_id)

    # Status should be updated to STOPPED when process is detected as dead
    assert status == SessionStatus.STOPPED


def test_permission_denied_for_create_notebook() -> None:
    """Verify create_notebook requires LAUNCH_NOTEBOOKS permission."""
    service = NotebookLauncherService(user={"role": "viewer"})

    with pytest.raises(PermissionError):
        service.create_notebook("alpha_research", parameters={})


def test_permission_denied_for_terminate_session() -> None:
    """Verify terminate_session requires LAUNCH_NOTEBOOKS permission."""
    service = NotebookLauncherService(user={"role": "viewer"})

    with pytest.raises(PermissionError):
        service.terminate_session("some-session-id")


def test_port_allocation_skips_bound_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify port allocator skips ports that are already bound."""
    import socket

    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")

    # Bind a socket to the first port in the range
    bound_port = 8900
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", bound_port))
    sock.listen(1)  # Put socket in LISTEN state to make it truly unavailable

    try:
        service = NotebookLauncherService(user={"role": "researcher"})
        # _allocate_port should skip 8900 and return 8901
        allocated = service._allocate_port()
        assert allocated != bound_port
        assert allocated == bound_port + 1
    finally:
        sock.close()
