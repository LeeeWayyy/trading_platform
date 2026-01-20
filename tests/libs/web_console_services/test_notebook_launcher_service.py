"""Unit tests for libs.web_console_services.notebook_launcher_service."""

from __future__ import annotations

import signal
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from libs.web_console_services.notebook_launcher_service import (
    NotebookLauncherService,
    NotebookSession,
    NotebookTemplate,
    SessionStatus,
)


@pytest.fixture()
def user() -> dict[str, object]:
    return {"user_id": "user-1", "role": "researcher"}


@pytest.fixture()
def template() -> NotebookTemplate:
    return NotebookTemplate(
        template_id="alpha_research",
        name="Alpha Research",
        description="Test template",
        notebook_path="notebooks/templates/alpha_research.ipynb",
        parameters=(),
    )


def test_list_templates_requires_permission(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=False
    ):
        with pytest.raises(PermissionError):
            service.list_templates()


def test_list_templates_returns_templates(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
    ):
        templates = service.list_templates()

    assert templates == [template]


def test_create_notebook_unknown_template_raises(user: dict[str, object]) -> None:
    service = NotebookLauncherService(user=user, templates=[])

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
    ):
        with pytest.raises(ValueError, match="Unknown template_id"):
            service.create_notebook("missing", parameters={})


def test_create_notebook_success(
    user: dict[str, object], template: NotebookTemplate, tmp_path: Path
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with (
        patch(
            "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
        ),
        patch("libs.web_console_services.notebook_launcher_service.subprocess.Popen") as popen_mock,
        patch(
            "libs.web_console_services.notebook_launcher_service.secrets.token_urlsafe",
            return_value="token-123",
        ),
        patch("libs.web_console_services.notebook_launcher_service.uuid4") as uuid_mock,
        patch.object(service, "_allocate_port", return_value=9001),
        patch.object(service, "_resolve_log_dir", return_value=tmp_path),
        patch.dict(
            "os.environ",
            {
                "NOTEBOOK_LAUNCH_COMMAND": "jupyter --port {port} --token {token}",
                "NOTEBOOK_BASE_URL": "http://localhost",
            },
            clear=False,
        ),
    ):
        uuid_mock.return_value.hex = "session-1"
        popen_instance = Mock()
        popen_instance.pid = 1234
        popen_mock.return_value = popen_instance

        session = service.create_notebook("alpha_research", parameters={"alpha": "mom"})

    assert session.status == SessionStatus.RUNNING
    assert session.process_id == 1234
    assert session.command == ["jupyter", "--port", "9001", "--token", "token-123"]
    assert session.access_url == "http://localhost:9001/?token=token-123"
    assert session.session_id == "session-1"
    assert session.parameters == {"alpha": "mom"}

    popen_mock.assert_called_once()
    _, kwargs = popen_mock.call_args
    assert "NOTEBOOK_SESSION_ID" in kwargs["env"]
    assert kwargs["env"]["NOTEBOOK_PORT"] == "9001"


def test_create_notebook_launch_failure_sets_error(
    user: dict[str, object], template: NotebookTemplate, tmp_path: Path
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with (
        patch(
            "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
        ),
        patch(
            "libs.web_console_services.notebook_launcher_service.subprocess.Popen",
            side_effect=RuntimeError("boom"),
        ),
        patch.object(service, "_allocate_port", return_value=9001),
        patch.object(service, "_resolve_log_dir", return_value=tmp_path),
        patch.dict(
            "os.environ",
            {"NOTEBOOK_LAUNCH_COMMAND": "jupyter --port {port}"},
            clear=False,
        ),
    ):
        session = service.create_notebook("alpha_research", parameters={})

    assert session.status == SessionStatus.ERROR
    assert "boom" in (session.error_message or "")


def test_get_session_status_updates_stopped(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        process_id=111,
    )
    service = NotebookLauncherService(user=user, templates=[template], session_store={"s": session})

    with (
        patch(
            "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
        ),
        patch.object(service, "_is_process_alive", return_value=False),
    ):
        status = service.get_session_status("s")

    assert status == SessionStatus.STOPPED
    assert session.status == SessionStatus.STOPPED


def test_terminate_session_handles_missing_session(user: dict[str, object]) -> None:
    service = NotebookLauncherService(user=user, templates=[])

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
    ):
        assert service.terminate_session("missing") is False


def test_terminate_session_without_process_id_sets_stopped(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service = NotebookLauncherService(user=user, templates=[template], session_store={"s": session})

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
    ):
        assert service.terminate_session("s") is True

    assert session.status == SessionStatus.STOPPED


def test_terminate_session_sends_sigterm(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        process_id=222,
    )
    service = NotebookLauncherService(user=user, templates=[template], session_store={"s": session})

    with (
        patch(
            "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
        ),
        patch("libs.web_console_services.notebook_launcher_service.os.kill") as kill_mock,
    ):
        assert service.terminate_session("s") is True

    kill_mock.assert_called_once_with(222, signal.SIGTERM)
    assert session.status == SessionStatus.STOPPING


def test_terminate_session_process_lookup_sets_stopped(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        process_id=333,
    )
    service = NotebookLauncherService(user=user, templates=[template], session_store={"s": session})

    with (
        patch(
            "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
        ),
        patch(
            "libs.web_console_services.notebook_launcher_service.os.kill",
            side_effect=ProcessLookupError,
        ),
    ):
        assert service.terminate_session("s") is True

    assert session.status == SessionStatus.STOPPED


def test_force_terminate_requires_stopping(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        process_id=444,
    )
    service = NotebookLauncherService(user=user, templates=[template], session_store={"s": session})

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
    ):
        assert service.force_terminate_session("s") is False


def test_force_terminate_kills_process(user: dict[str, object], template: NotebookTemplate) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.STOPPING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        process_id=555,
    )
    service = NotebookLauncherService(user=user, templates=[template], session_store={"s": session})

    with (
        patch(
            "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
        ),
        patch.object(service, "_is_process_alive", return_value=True),
        patch("libs.web_console_services.notebook_launcher_service.os.kill") as kill_mock,
    ):
        assert service.force_terminate_session("s") is True

    kill_mock.assert_called_once()
    assert session.status == SessionStatus.STOPPED


def test_list_sessions_excludes_stopped(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    session_running = NotebookSession(
        session_id="running",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session_stopped = NotebookSession(
        session_id="stopped",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.STOPPED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    service = NotebookLauncherService(
        user=user,
        templates=[template],
        session_store={"running": session_running, "stopped": session_stopped},
    )

    with patch(
        "libs.web_console_services.notebook_launcher_service.has_permission", return_value=True
    ):
        sessions = service.list_sessions()
        sessions_all = service.list_sessions(include_stopped=True)

    assert sessions == [session_running]
    assert session_stopped in sessions_all


def test_build_access_url_formats(user: dict[str, object], template: NotebookTemplate) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with patch.dict(
        "os.environ",
        {"NOTEBOOK_BASE_URL": "http://localhost/"},
        clear=False,
    ):
        url = service._build_access_url(port=9001, token="token")

    assert url == "http://localhost:9001/?token=token"


def test_build_launch_command_requires_env(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="NOTEBOOK_LAUNCH_COMMAND"):
            service._build_launch_command(
                template=template, session_id="session", port=9001, token="token"
            )


def test_build_launch_command_missing_placeholder(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    with patch.dict(
        "os.environ",
        {"NOTEBOOK_LAUNCH_COMMAND": "jupyter --missing {unknown}"},
        clear=False,
    ):
        with pytest.raises(ValueError, match="missing placeholders"):
            service._build_launch_command(
                template=template, session_id="session", port=9001, token="token"
            )


def test_build_launch_env_serializes_parameters(
    user: dict[str, object], template: NotebookTemplate
) -> None:
    service = NotebookLauncherService(user=user, templates=[template])

    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={"as_of": date(2024, 1, 1)},
        status=SessionStatus.STARTING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        port=9001,
        token="token",
    )

    env = service._build_launch_env(template, session, ["jupyter"])

    assert env["NOTEBOOK_TEMPLATE_ID"] == template.template_id
    assert "NOTEBOOK_PARAMETERS" in env
    assert "2024-01-01" in env["NOTEBOOK_PARAMETERS"]


def test_allocate_port_skips_used(user: dict[str, object], template: NotebookTemplate) -> None:
    session = NotebookSession(
        session_id="session-1",
        template_id=template.template_id,
        parameters={},
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        port=8900,
    )
    service = NotebookLauncherService(
        user=user,
        templates=[template],
        session_store={"s": session},
        port_base=8900,
        port_span=2,
    )

    with patch.object(service, "_is_port_available", return_value=True):
        port = service._allocate_port()

    assert port == 8901
