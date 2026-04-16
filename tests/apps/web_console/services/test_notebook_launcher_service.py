"""Tests for NotebookLauncherService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from libs.web_console_services.notebook_launcher_service import (
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
        "libs.web_console_services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ) as mock_popen:
        session = service.create_notebook(template_id, parameters={"alpha": "momentum"})

    assert session.status == SessionStatus.RUNNING
    assert session.process_id == 4242
    mock_popen.assert_called_once()


def test_viewer_can_list_templates_single_admin() -> None:
    """P6T19: Viewer can list templates — single-admin model."""
    service = NotebookLauncherService(user={"role": "viewer"})

    templates = service.list_templates()
    assert isinstance(templates, list)


def test_terminate_session_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify terminate_session sends SIGTERM and sets STOPPING status."""
    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")

    service = NotebookLauncherService(user={"role": "researcher"})
    template_id = service.list_templates()[0].template_id

    mock_process = MagicMock()
    mock_process.pid = 4242

    with patch(
        "libs.web_console_services.notebook_launcher_service.subprocess.Popen",
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
        "libs.web_console_services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ):
        session = service.create_notebook(template_id, parameters={})

    # Mock os.kill to raise ProcessLookupError (process died)
    with patch("os.kill", side_effect=ProcessLookupError):
        # get_session_status returns SessionStatus enum directly
        status = service.get_session_status(session.session_id)

    # Status should be updated to STOPPED when process is detected as dead
    assert status == SessionStatus.STOPPED


def test_viewer_can_create_notebook_single_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """P6T19: Viewer can create notebooks — single-admin model."""
    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")
    service = NotebookLauncherService(user={"role": "viewer"})
    template_id = service.list_templates()[0].template_id

    mock_process = MagicMock()
    mock_process.pid = 1234

    with patch(
        "libs.web_console_services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ):
        session = service.create_notebook(template_id, parameters={})
    assert session is not None


def test_viewer_can_terminate_session_single_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """P6T19: Viewer can terminate sessions — single-admin model."""
    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")
    service = NotebookLauncherService(user={"role": "viewer"})
    template_id = service.list_templates()[0].template_id

    mock_process = MagicMock()
    mock_process.pid = 1234

    with patch(
        "libs.web_console_services.notebook_launcher_service.subprocess.Popen",
        return_value=mock_process,
    ):
        session = service.create_notebook(template_id, parameters={})

    with patch("os.kill") as mock_kill:
        mock_kill.return_value = None
        result = service.terminate_session(session.session_id)
    assert result is True


def test_port_allocation_skips_bound_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify port allocator skips ports that are already bound."""
    import socket

    monkeypatch.setenv("NOTEBOOK_LAUNCH_COMMAND", "echo launch {template_id}")

    # Pick a currently-free base port, then occupy it to force skip behavior.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        bound_port = probe.getsockname()[1]

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", bound_port))

    try:
        service = NotebookLauncherService(
            user={"role": "researcher"},
            port_base=bound_port,
            port_span=2,
        )
        # _allocate_port should skip the occupied base port.
        allocated = service._allocate_port()
        assert allocated != bound_port
        assert allocated == bound_port + 1
    finally:
        sock.close()
