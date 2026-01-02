"""Spike C0.1: Validate NiceGUI API access patterns.

Run with:
    .venv/bin/python -m apps.web_console_ng.spike_c01
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from nicegui import Client, app, ui
from starlette.requests import Request

from apps.web_console_ng import config

logger = logging.getLogger("spike.c01")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _truncate(value: str | None, limit: int = 120) -> str:
    if value is None:
        return "<missing>"
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _report(name: str, ok: bool, details: str = "") -> None:
    status = "SUCCESS" if ok else "FAIL"
    line = f"[{status}] {name}"
    if details:
        line = f"{line} - {details}"
    print(line, flush=True)


def _info(name: str, details: str) -> None:
    print(f"[INFO] {name} - {details}", flush=True)


def _report_reconnect_settings() -> None:
    sig = inspect.signature(ui.run)
    params = sig.parameters
    reconnect_params = [p for p in params if "reconnect" in p]
    _info("ui.run signature", str(sig))
    if reconnect_params:
        _info("ui.run reconnect params", ", ".join(reconnect_params))
    else:
        _info("ui.run reconnect params", "<none>")

    if "reconnect_timeout" in params:
        default = params["reconnect_timeout"].default
        _report(
            "reconnect: ui.run(reconnect_timeout=...) available",
            True,
            f"default={default}",
        )
    else:
        _report("reconnect: ui.run(reconnect_timeout=...) available", False)


@ui.page("/spike/request")
async def spike_request(request: Request) -> None:
    """Validate request access and cookies in @ui.page handlers."""
    is_request = isinstance(request, Request)
    _report("request handler: request param", is_request, f"type={type(request).__name__}")

    cookies = cast(Mapping[str, str], request.cookies)
    has_cookies_attr = bool(cookies)
    _report("request handler: request.cookies available", has_cookies_attr)

    if cookies:
        cookie_keys = list(cookies.keys())
        _info("request handler: cookies", f"keys={cookie_keys}")
        _info("request handler: spike_cookie", _truncate(cookies.get("spike_cookie")))

    ui.label("Spike C0.1 request handler active. Check server logs for results.")


async def _on_connect(client: Client) -> None:
    """Validate WS origin/cookie access in app.on_connect."""
    client_id = getattr(client, "id", None)
    _report("ws: on_connect called", True, f"client_id={client_id}")

    environ = getattr(client, "environ", None)
    has_environ = isinstance(environ, Mapping)
    _report("ws: client.environ available", has_environ, f"type={type(environ).__name__}")

    has_origin = False
    origin_value = None
    has_cookie = False
    cookie_value = None
    if has_environ:
        env_map = cast(Mapping[str, Any], environ)
        has_origin = "HTTP_ORIGIN" in env_map
        origin_value = env_map.get("HTTP_ORIGIN")
        has_cookie = "HTTP_COOKIE" in env_map
        cookie_value = env_map.get("HTTP_COOKIE")

    _report("ws: HTTP_ORIGIN key present", has_origin, _truncate(origin_value))
    _report("ws: HTTP_COOKIE key present", has_cookie, _truncate(cookie_value))


async def _on_disconnect(client: Client) -> None:
    client_id = getattr(client, "id", None)
    _info("ws: on_disconnect", f"client_id={client_id}")


async def startup() -> None:
    """Startup hook validation."""
    _report("lifecycle: startup hook called", True, f"time={_utc_now()}")
    _report_reconnect_settings()


async def shutdown() -> None:
    """Shutdown hook validation."""
    _report("lifecycle: shutdown hook called", True, f"time={_utc_now()}")


app.on_startup(startup)
app.on_shutdown(shutdown)
app.on_connect(_on_connect)
app.on_disconnect(_on_disconnect)


if __name__ == "__main__":
    ui.run(
        host=config.HOST,
        port=config.PORT,
        title="NiceGUI Spike C0.1",
        reload=config.DEBUG,
        show=False,
    )
