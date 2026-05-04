from pathlib import Path


def test_web_console_image_includes_data_acquisition_adapters() -> None:
    repo_root = Path(__file__).parents[3]
    dockerfile = (repo_root / "apps/web_console_ng/Dockerfile").read_text()
    dockerignore = (repo_root / ".dockerignore").read_text()
    compose = (repo_root / "docker-compose.yml").read_text()

    assert "scripts/data/alpaca_sip_sync.py" in dockerfile
    assert "scripts/data/alpaca_corp_actions_sync.py" in dockerfile
    assert "/app/scripts/data/" in dockerfile
    assert "ARG APP_UID=1000" in dockerfile
    assert "ARG APP_GID=1000" in dockerfile
    assert "groupadd --gid \"${APP_GID}\" --non-unique appuser" in dockerfile
    assert "useradd --uid \"${APP_UID}\" --gid appuser --no-create-home --non-unique appuser" in dockerfile

    assert "!scripts/data/" in dockerignore
    assert "scripts/data/*" in dockerignore
    assert "!scripts/data/alpaca_sip_sync.py" in dockerignore
    assert "!scripts/data/alpaca_corp_actions_sync.py" in dockerignore

    assert "./data:/app/data" in compose
    assert "APP_UID: ${WEB_CONSOLE_UID:-1000}" in compose
    assert "APP_GID: ${WEB_CONSOLE_GID:-1000}" in compose
