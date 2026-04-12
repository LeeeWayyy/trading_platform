"""Live UI click-through test for web_console_ng on Docker.

This test performs a broad, authenticated click-through across reachable pages
and asserts that:
1) visible button-like controls can be clicked without browser/runtime errors
2) Docker logs do not report errors during the click-through window

Run:
    pytest tests/e2e/test_web_console_ui_clickthrough.py -v -m e2e
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


BASE_URL = os.getenv("WEB_CONSOLE_UI_BASE_URL", "http://localhost:8080").rstrip("/")
EXEC_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_BASE_URL", "http://localhost:8002")
DOCKER_LOG_WINDOW_MINUTES = int(os.getenv("WEB_CONSOLE_UI_DOCKER_LOG_WINDOW_MINUTES", "15"))
MAX_PAGES = int(os.getenv("WEB_CONSOLE_UI_MAX_PAGES", "18"))
MAX_CLICKS_PER_PAGE = int(os.getenv("WEB_CONSOLE_UI_MAX_CLICKS_PER_PAGE", "60"))

LOGIN_USER = os.getenv("WEB_CONSOLE_USER", "admin")
LOGIN_PASSWORD = os.getenv("WEB_CONSOLE_PASSWORD", "changeme")

SKIP_PATH_PREFIXES = (
    "/auth/",
    "/login",
    "/forgot-password",
    "/mfa-verify",
    "/health",
)

SEED_PATHS = [
    "/",
    "/position-management",
    "/manual-order",
    "/circuit-breaker",
    "/risk",
    "/research/universes",
    "/data",
    "/data/sql-explorer",
    "/models",
    "/backtest",
    "/reports",
    "/strategies",
]

DOCKER_ERROR_PATTERN = re.compile(
    r"(\blevel=error\b|\bERROR\b|Traceback|Exception|status=500|ModuleNotFound|NameError|UndefinedTable|No such container|does not exist)"
)


@dataclass
class PageResult:
    path: str
    status_code: int
    clicked: int
    click_failures: list[str]
    discovered_paths: list[str]


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
    )


def _docker_available() -> bool:
    result = _run(["docker", "compose", "ps"])
    return result.returncode == 0


def _http_ready(url: str, timeout: float = 2.5) -> bool:
    try:
        import httpx

        response = httpx.get(url, timeout=timeout)
        return response.status_code < 500
    except Exception:
        return False


def _normalize_path(href: str) -> str | None:
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.scheme and parsed.netloc:
        base = urlparse(BASE_URL)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            return None
        path = parsed.path or "/"
    else:
        path = href if href.startswith("/") else f"/{href}"
    if not path.startswith("/"):
        return None
    if any(path.startswith(prefix) for prefix in SKIP_PATH_PREFIXES):
        return None
    return path


def _discover_paths(page: Any) -> list[str]:
    hrefs = page.eval_on_selector_all(
        "a[href]",
        """
        (nodes) => nodes
          .map((n) => n.getAttribute('href') || '')
          .filter(Boolean)
        """,
    )
    discovered: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        normalized = _normalize_path(href)
        if normalized and normalized not in seen:
            seen.add(normalized)
            discovered.append(normalized)
    return discovered


def _collect_click_targets(page: Any) -> list[dict[str, str]]:
    return page.evaluate(
        """
        () => {
          const attr = 'data-live-click-target';
          const targets = [];
          const seen = new Set();
          const isVisible = (el) => {
            const style = getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 8 && rect.height > 8 && rect.bottom >= 0 && rect.right >= 0;
          };
          const all = Array.from(document.querySelectorAll('button, [role="button"], .q-btn'));
          let idx = 0;
          for (const el of all) {
            if (!isVisible(el)) continue;
            if (el.closest('[aria-hidden="true"]')) continue;
            if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
            const nameRaw = el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '';
            const name = nameRaw.trim().replace(/\\s+/g, ' ');
            if (!name) continue;
            const key = `${name}|${el.tagName}|${el.className}`;
            if (seen.has(key)) continue;
            seen.add(key);
            const value = `target-${Date.now()}-${idx++}`;
            el.setAttribute(attr, value);
            targets.push({ selector: `[${attr}="${value}"]`, name });
          }
          return targets;
        }
        """
    )


def _click_targets(page: Any, path: str) -> tuple[int, list[str]]:
    failures: list[str] = []
    clicked = 0
    targets = _collect_click_targets(page)[:MAX_CLICKS_PER_PAGE]
    for target in targets:
        selector = target["selector"]
        name = target["name"]
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            locator.scroll_into_view_if_needed(timeout=1500)
            locator.click(timeout=3000)
            clicked += 1
            page.wait_for_timeout(150)
            # Close transient dialogs/popovers opened by click actions.
            page.keyboard.press("Escape")
            page.wait_for_timeout(50)
            # Keep traversal stable on the target page.
            current_path = urlparse(page.url).path or "/"
            if current_path != path:
                page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(100)
        except PlaywrightError as exc:
            failures.append(f"{name}: {exc!s}")
    return clicked, failures


def _collect_docker_errors(since_iso: str) -> list[str]:
    result = _run(["docker", "compose", "logs", "--since", since_iso, "--no-color"])
    if result.returncode != 0:
        return [f"docker compose logs failed: {result.stderr.strip()}"]
    lines = result.stdout.splitlines()
    return [line for line in lines if DOCKER_ERROR_PATTERN.search(line)]


@pytest.mark.e2e()
def test_web_console_live_clickthrough_has_no_browser_or_docker_errors() -> None:
    """Run authenticated live click-through and verify no runtime errors."""
    if not _docker_available():
        pytest.skip("docker compose is not available")

    if not _http_ready(f"{EXEC_GATEWAY_URL}/health"):
        pytest.skip("execution gateway is not healthy; start docker services first")
    if not _http_ready(f"{BASE_URL}/health"):
        pytest.skip("web_console_dev is not healthy; start docker services first")

    started_at = datetime.now(UTC).isoformat()

    page_errors: list[str] = []
    console_errors: list[str] = []
    response_5xx: list[str] = []
    request_failures: list[str] = []
    results: list[PageResult] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(base_url=BASE_URL)
        page = context.new_page()

        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on(
            "response",
            lambda resp: response_5xx.append(f"{resp.status} {resp.url}") if resp.status >= 500 else None,
        )
        page.on(
            "requestfailed",
            lambda req: request_failures.append(f"{req.method} {req.url} {req.failure}"),
        )

        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=15000)
        page.get_by_label("Username").fill(LOGIN_USER)
        page.get_by_label("Password").fill(LOGIN_PASSWORD)
        page.get_by_role("button", name="Sign In").click(timeout=5000)
        page.wait_for_timeout(1000)

        if "/login" in page.url:
            screenshot = Path("artifacts/ui_clickthrough_login_failed.png")
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            pytest.fail(f"Login failed for user '{LOGIN_USER}'. See screenshot: {screenshot}")

        queue: deque[str] = deque(SEED_PATHS)
        visited: set[str] = set()

        while queue and len(visited) < MAX_PAGES:
            path = queue.popleft()
            if path in visited:
                continue
            visited.add(path)

            response = page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded", timeout=15000)
            status_code = response.status if response else 0
            page.wait_for_timeout(350)

            discovered_paths = _discover_paths(page)
            for discovered in discovered_paths:
                if discovered not in visited and discovered not in queue:
                    queue.append(discovered)

            if status_code >= 400:
                results.append(
                    PageResult(
                        path=path,
                        status_code=status_code,
                        clicked=0,
                        click_failures=[f"HTTP {status_code}"],
                        discovered_paths=discovered_paths,
                    )
                )
                continue

            clicked, click_failures = _click_targets(page, path)
            results.append(
                PageResult(
                    path=path,
                    status_code=status_code,
                    clicked=clicked,
                    click_failures=click_failures,
                    discovered_paths=discovered_paths,
                )
            )

        context.close()
        browser.close()

    docker_errors = _collect_docker_errors(started_at)

    report: dict[str, Any] = {
        "base_url": BASE_URL,
        "started_at": started_at,
        "checked_pages": [r.path for r in results],
        "page_results": [
            {
                "path": r.path,
                "status_code": r.status_code,
                "clicked": r.clicked,
                "click_failures": r.click_failures,
                "discovered_paths": r.discovered_paths,
            }
            for r in results
        ],
        "page_errors": page_errors,
        "console_errors": console_errors,
        "response_5xx": response_5xx,
        "request_failures": request_failures,
        "docker_errors": docker_errors,
    }
    report_path = Path("artifacts/ui_clickthrough_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    page_click_failures = [f"{r.path}: {msg}" for r in results for msg in r.click_failures]
    assert not page_errors, f"Browser page errors found: {page_errors[:5]}"
    assert not console_errors, f"Browser console errors found: {console_errors[:5]}"
    assert not response_5xx, f"HTTP 5xx responses found: {response_5xx[:5]}"
    assert not request_failures, f"Failed requests found: {request_failures[:5]}"
    assert not page_click_failures, f"UI click failures found: {page_click_failures[:10]}"
    assert not docker_errors, f"Docker errors found after click-through: {docker_errors[:10]}"
