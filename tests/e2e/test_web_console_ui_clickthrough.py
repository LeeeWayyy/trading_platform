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
import time
from collections import Counter, deque
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
MAX_PAGES = int(os.getenv("WEB_CONSOLE_UI_MAX_PAGES", "32"))
MAX_INTERACTIONS_PER_PAGE = int(
    os.getenv("WEB_CONSOLE_UI_MAX_INTERACTIONS_PER_PAGE", os.getenv("WEB_CONSOLE_UI_MAX_CLICKS_PER_PAGE", "20"))
)
MAX_INTERACTION_PASSES = int(os.getenv("WEB_CONSOLE_UI_MAX_INTERACTION_PASSES", "3"))
MAX_TOTAL_INTERACTIONS = int(os.getenv("WEB_CONSOLE_UI_MAX_TOTAL_INTERACTIONS", "220"))
MIN_TOTAL_INTERACTIONS = int(os.getenv("WEB_CONSOLE_UI_MIN_TOTAL_INTERACTIONS", "35"))
MAX_TEST_DURATION_SECONDS = int(os.getenv("WEB_CONSOLE_UI_MAX_DURATION_SECONDS", "360"))
PAGE_GOTO_TIMEOUT_MS = int(os.getenv("WEB_CONSOLE_UI_PAGE_GOTO_TIMEOUT_MS", "12000"))
ACTION_TIMEOUT_MS = int(os.getenv("WEB_CONSOLE_UI_ACTION_TIMEOUT_MS", "900"))
PAGE_SETTLE_MS = int(os.getenv("WEB_CONSOLE_UI_PAGE_SETTLE_MS", "160"))
ACTION_SETTLE_MS = int(os.getenv("WEB_CONSOLE_UI_ACTION_SETTLE_MS", "90"))
DOCKER_ERROR_SERVICES = tuple(
    token
    for token in os.getenv("WEB_CONSOLE_UI_DOCKER_ERROR_SERVICES", "web_console_dev").split()
    if token
)

SKIP_PATH_PREFIXES = (
    "/auth/",
    "/login",
    "/forgot-password",
    "/mfa-verify",
    "/health",
    "/_nicegui/",
    "/static/",
)

SEED_PATHS = [
    "/",
    "/alerts",
    "/journal",
    "/performance",
    "/risk/exposure",
    "/attribution",
    "/tax-lots",
    "/circuit-breaker",
    "/risk",
    "/research?tab=discover",
    "/research?tab=validate&backtest_tab=running",
    "/research?tab=promote",
    "/research/universes",
    "/compare",
    "/execution-quality",
    "/notebooks",
    "/data",
    "/data/coverage",
    "/data/sources",
    "/data/inspector",
    "/data/features",
    "/data/sql-explorer",
    "/reports",
    "/strategies",
    "/admin",
]

DOCKER_ERROR_PATTERN = re.compile(
    r"(\blevel=error\b|\bERROR\b|Traceback|Exception|status=500|ModuleNotFound|NameError|UndefinedTable|No such container|does not exist)"
)
RISKY_ACTION_PATTERN = re.compile(
    r"(logout|sign\s*out|kill\s*switch|flatten|cancel\s*all|delete|remove|drop|submit|execute|buy|sell|trip|arm|engage|disengage)",
    re.IGNORECASE,
)
IGNORABLE_CONSOLE_PATTERNS = (
    re.compile(r"Clipboard copy failed: .*Write permission denied", re.IGNORECASE),
    re.compile(r"AG Grid: .*treeData.*ag-grid-enterprise", re.IGNORECASE),
)


@dataclass
class PageResult:
    path: str
    status_code: int
    interactions: int
    total_targets_seen: int
    skipped_risky: int
    skipped_flaky: int
    interaction_counts: dict[str, int]
    interaction_failures: list[str]
    discovered_paths: list[str]


def _load_login_credentials() -> tuple[str, str]:
    """Resolve UI login credentials from .env/environment without hardcoded fallbacks."""
    dotenv_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=dotenv_path, override=False)
    except ImportError:
        pass

    username = (os.getenv("WEB_CONSOLE_USER") or "").strip()
    password = os.getenv("WEB_CONSOLE_PASSWORD") or ""
    if not username or not password:
        raise RuntimeError(
            "Missing WEB_CONSOLE_USER/WEB_CONSOLE_PASSWORD for E2E login. "
            f"Set them in environment or .env ({dotenv_path})."
        )
    return username, password


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
        "a[href], [href], [to], [data-href]",
        """
        (nodes) => nodes
          .map((n) => n.getAttribute('href') || n.getAttribute('to') || n.getAttribute('data-href') || '')
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


def _collect_interaction_targets(page: Any) -> list[dict[str, str]]:
    return page.evaluate(
        """
        () => {
          const attr = 'data-live-interaction-target';
          const targets = [];
          const seen = new Set();
          const isVisible = (el) => {
            const style = getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 8 && rect.height > 8 && rect.bottom >= 0 && rect.right >= 0;
          };
          const groups = [
            ['button', 'button, [role="button"], .q-btn'],
            ['tab', '[role="tab"], .q-tab'],
            ['link', 'a[href], [role="link"], .q-item[clickable], [href], [to], [data-href]'],
            ['toggle', 'input[type="checkbox"], input[type="radio"], [role="switch"], .q-toggle, .q-checkbox, .q-radio'],
            ['input', 'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]), textarea'],
            ['select', 'select, [role="combobox"], .q-select'],
          ];
          let idx = 0;
          for (const [kind, selector] of groups) {
            const all = Array.from(document.querySelectorAll(selector));
            for (const el of all) {
              if (!isVisible(el)) continue;
              if (el.closest('[aria-hidden="true"]')) continue;
              if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
              if (el.getAttribute('readonly') === 'true') continue;
              if (kind === 'input' && (el.getAttribute('type') || '').toLowerCase() === 'password') continue;
              const rect = el.getBoundingClientRect();
              const nameRaw =
                el.innerText ||
                el.getAttribute('aria-label') ||
                el.getAttribute('title') ||
                el.getAttribute('name') ||
                el.getAttribute('placeholder') ||
                el.getAttribute('id') ||
                '';
              const name = nameRaw.trim().replace(/\\s+/g, ' ');
              const href = el.getAttribute('href') || el.getAttribute('to') || el.getAttribute('data-href') || '';
              const inputType = (el.getAttribute('type') || '').toLowerCase();
              // Keep unnamed links/tabs when they expose a route; avoid blind icon-only controls.
              if (!name && kind === 'button') continue;
              if (!name && !href) continue;
              const displayName = name || `${kind}-${idx}`;
              const key = `${kind}|${displayName}|${href}|${inputType}|${Math.round(rect.top)}|${Math.round(rect.left)}`;
              if (seen.has(key)) continue;
              seen.add(key);
              const value = `target-${Date.now()}-${idx++}`;
              el.setAttribute(attr, value);
              targets.push({
                selector: `[${attr}="${value}"]`,
                name: displayName,
                kind,
                href,
                key,
                input_type: inputType,
              });
            }
          }
          return targets;
        }
        """
    )


def _is_risky_target(target: dict[str, str]) -> bool:
    name = target.get("name", "")
    href = target.get("href", "")
    kind = target.get("kind", "")
    if RISKY_ACTION_PATTERN.search(name):
        return True
    if href in {"/logout", "/auth/logout"}:
        return True
    if kind == "input" and re.search(r"(password|token|secret|api\s*key)", name, re.IGNORECASE):
        return True
    return False


def _is_flaky_calendar_target(target: dict[str, str]) -> bool:
    """Skip unstable date-picker navigation controls during broad click-through."""
    if target.get("kind") not in {"button", "tab", "link"}:
        return False
    normalized = target.get("name", "").strip().lower()
    if normalized.isdigit() and len(normalized) == 4:
        year = int(normalized)
        if 1900 <= year <= 2200:
            return True
    return normalized in {
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
    }


def _should_ignore_click_error(exc: PlaywrightError) -> bool:
    text = str(exc).lower()
    ignorable = (
        "not attached to the dom",
        "element is not attached",
        "target closed",
        "strict mode violation",
        "element is outside of the viewport",
        "intercepts pointer events",
        "element is not visible",
        "element is not enabled",
        "element is not stable",
        "another element would receive the click",
    )
    return any(token in text for token in ignorable)


def _is_stale_marker_timeout(exc: PlaywrightError, selector: str) -> bool:
    """Identify timeout caused by transient marker detachment after re-render."""
    text = str(exc).lower()
    if "timeout" not in text or "waiting for locator" not in text:
        return False
    return "data-live-interaction-target" in selector


def _retry_marker_click_by_semantics(page: Any, target: dict[str, str]) -> bool:
    """Retry a failed marker click using semantic selectors (role/name)."""
    kind = target.get("kind", "")
    name = target.get("name", "").strip()
    if kind not in {"button", "tab", "link", "toggle"} or not name:
        return False

    try:
        if kind == "button":
            locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)).first
            if locator.count() == 0:
                locator = page.locator(f'button:has-text("{name}")').first
        elif kind == "tab":
            locator = page.get_by_role("tab", name=re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)).first
        elif kind == "link":
            locator = page.get_by_role("link", name=re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)).first
            if locator.count() == 0:
                locator = page.locator(f'a:has-text("{name}")').first
        else:
            locator = page.get_by_role("switch", name=re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)).first
            if locator.count() == 0:
                locator = page.get_by_role("checkbox", name=re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)).first

        if locator.count() == 0:
            return False

        locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
        locator.click(timeout=ACTION_TIMEOUT_MS)
    except PlaywrightError:
        return False

    return True


def _is_ignorable_console_error(message: str) -> bool:
    return any(pattern.search(message) for pattern in IGNORABLE_CONSOLE_PATTERNS)


def _is_ignorable_request_failure(method: str, url: str, failure_message: str) -> bool:
    """Ignore expected browser-aborted navigations during rapid click-through."""
    if method.upper() != "GET":
        return False

    normalized_failure = failure_message.lower()
    if "net::err_aborted" not in normalized_failure:
        return False

    parsed_url = urlparse(url)
    parsed_base_url = urlparse(BASE_URL)
    same_origin = (
        parsed_url.scheme == parsed_base_url.scheme
        and parsed_url.netloc == parsed_base_url.netloc
    )
    if same_origin:
        # Same-origin requests can be cancelled while the crawler rapidly
        # transitions routes. Route-level failures are still caught by page
        # status/interactions, so ignore browser-level abort noise here.
        normalized_path = (parsed_url.path or "").lower()
        return normalized_path.startswith("/_nicegui/") or normalized_path.startswith(
            "/static/"
        )

    # Third-party static assets can be aborted during rapid route transitions.
    return bool(parsed_url.scheme and parsed_url.netloc)


def test_is_ignorable_request_failure_allows_only_static_same_origin_aborts() -> None:
    failure = "net::ERR_ABORTED"

    assert _is_ignorable_request_failure(
        "GET",
        f"{BASE_URL}/_nicegui/client.js",
        failure,
    )
    assert _is_ignorable_request_failure(
        "GET",
        f"{BASE_URL}/static/app.css",
        failure,
    )
    assert not _is_ignorable_request_failure(
        "GET",
        f"{BASE_URL}/api/v1/orders",
        failure,
    )
    assert not _is_ignorable_request_failure(
        "GET",
        f"{BASE_URL}/trade",
        failure,
    )


def _default_input_value(input_type: str) -> str:
    normalized = (input_type or "").lower()
    if normalized in {"number", "range"}:
        return "1"
    if normalized in {"date"}:
        return "2026-01-05"
    if normalized in {"datetime-local"}:
        return "2026-01-05T09:30"
    if normalized in {"time"}:
        return "09:30"
    if normalized in {"email"}:
        return "smoke-test@example.com"
    return "smoke-test"


def _select_first_safe_option(page: Any) -> None:
    options = page.locator('[role="option"], .q-menu .q-item, .q-virtual-scroll__content .q-item')
    count = min(options.count(), 8)
    for idx in range(count):
        option = options.nth(idx)
        label = ""
        try:
            label = option.inner_text(timeout=400).strip()
        except PlaywrightError:
            continue
        if RISKY_ACTION_PATTERN.search(label):
            continue
        option.click(timeout=ACTION_TIMEOUT_MS)
        page.wait_for_timeout(80)
        page.keyboard.press("Escape")
        return
    page.keyboard.press("Escape")


def _interact_with_targets(
    page: Any,
    path: str,
    *,
    remaining_total: int,
    deadline_monotonic: float,
) -> tuple[int, int, int, int, list[str], list[str], dict[str, int]]:
    failures: list[str] = []
    interactions = 0
    discovered_via_click: set[str] = set()
    attempted_keys: set[str] = set()
    total_targets_seen = 0
    skipped_risky = 0
    skipped_flaky = 0
    interaction_counts: Counter[str] = Counter()

    for _ in range(MAX_INTERACTION_PASSES):
        if interactions >= MAX_INTERACTIONS_PER_PAGE or interactions >= remaining_total:
            break
        if time.monotonic() >= deadline_monotonic:
            break

        all_targets = _collect_interaction_targets(page)
        total_targets_seen = max(total_targets_seen, len(all_targets))
        pending = [t for t in all_targets if t.get("key", "") not in attempted_keys]
        if not pending:
            break

        safe_targets: list[dict[str, str]] = []
        for target in pending:
            attempted_keys.add(target.get("key", ""))
            if _is_risky_target(target):
                skipped_risky += 1
                continue
            if _is_flaky_calendar_target(target):
                skipped_flaky += 1
                continue
            safe_targets.append(target)

        if not safe_targets:
            continue

        for target in safe_targets:
            if interactions >= MAX_INTERACTIONS_PER_PAGE or interactions >= remaining_total:
                break
            if time.monotonic() >= deadline_monotonic:
                break

            selector = target["selector"]
            name = target["name"]
            kind = target.get("kind", "button")
            href = target.get("href", "")
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                try:
                    locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
                except PlaywrightError as scroll_exc:
                    # Best-effort scroll only. In dense grids/nav drawers Playwright can
                    # intermittently time out while waiting for layout stabilization.
                    # Continue and let click/fill determine true interactability.
                    if (
                        "timeout" not in str(scroll_exc).lower()
                        and not _should_ignore_click_error(scroll_exc)
                    ):
                        raise

                if kind in {"button", "tab", "link", "toggle"}:
                    locator.click(timeout=ACTION_TIMEOUT_MS)
                elif kind == "input":
                    locator.click(timeout=ACTION_TIMEOUT_MS)
                    locator.fill(_default_input_value(target.get("input_type", "")), timeout=ACTION_TIMEOUT_MS)
                    locator.press("Tab", timeout=ACTION_TIMEOUT_MS)
                elif kind == "select":
                    locator.click(timeout=ACTION_TIMEOUT_MS)
                    page.wait_for_timeout(120)
                    try:
                        tag_name = locator.evaluate("el => el.tagName.toLowerCase()")
                    except PlaywrightError:
                        tag_name = ""
                    if tag_name == "select":
                        options = locator.evaluate(
                            """
                            (el) => Array.from(el.options)
                              .filter((o) => !o.disabled && o.value)
                              .map((o) => ({ value: o.value, label: (o.textContent || '').trim() }))
                            """
                        )
                        selected = False
                        for option in options:
                            if RISKY_ACTION_PATTERN.search(option.get("label", "")):
                                continue
                            locator.select_option(option["value"], timeout=ACTION_TIMEOUT_MS)
                            selected = True
                            break
                        if not selected:
                            page.keyboard.press("Escape")
                    else:
                        _select_first_safe_option(page)
                else:
                    locator.click(timeout=ACTION_TIMEOUT_MS)

                interactions += 1
                interaction_counts[kind] += 1
                page.wait_for_timeout(ACTION_SETTLE_MS)
                page.keyboard.press("Escape")
                page.wait_for_timeout(70)

                normalized_href = _normalize_path(href)
                if normalized_href and normalized_href != path:
                    discovered_via_click.add(normalized_href)

                current_path = urlparse(page.url).path or "/"
                normalized = _normalize_path(current_path)
                if normalized and normalized != path:
                    discovered_via_click.add(normalized)
                    page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT_MS)
                    page.wait_for_timeout(PAGE_SETTLE_MS)
            except PlaywrightError as exc:
                if _should_ignore_click_error(exc):
                    continue
                if _is_stale_marker_timeout(exc, selector) and _retry_marker_click_by_semantics(page, target):
                    interactions += 1
                    interaction_counts[kind] += 1
                    page.wait_for_timeout(ACTION_SETTLE_MS)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(70)

                    normalized_href = _normalize_path(target.get("href", ""))
                    if normalized_href and normalized_href != path:
                        discovered_via_click.add(normalized_href)

                    current_path = urlparse(page.url).path or "/"
                    normalized = _normalize_path(current_path)
                    if normalized and normalized != path:
                        discovered_via_click.add(normalized)
                        page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT_MS)
                        page.wait_for_timeout(PAGE_SETTLE_MS)
                    continue
                failures.append(f"{kind}:{name}: {exc!s}")

    return (
        interactions,
        total_targets_seen,
        skipped_risky,
        skipped_flaky,
        failures,
        sorted(discovered_via_click),
        dict(interaction_counts),
    )


def _collect_docker_errors(since_token: str) -> list[str]:
    cmd = ["docker", "compose", "logs", "--since", since_token, "--no-color"]
    if DOCKER_ERROR_SERVICES:
        cmd.extend(DOCKER_ERROR_SERVICES)
    result = _run(cmd)
    if result.returncode != 0:
        return [f"docker compose logs failed: {result.stderr.strip()}"]
    lines = result.stdout.splitlines()
    return [line for line in lines if DOCKER_ERROR_PATTERN.search(line)]


def _login_with_retry(page: Any, *, username: str, password: str, attempts: int = 3) -> bool:
    """Login with bounded retries to handle transient auth/bootstrap delays."""
    for _ in range(attempts):
        try:
            page.goto(
                f"{BASE_URL}/login",
                wait_until="domcontentloaded",
                timeout=max(PAGE_GOTO_TIMEOUT_MS, 20_000),
            )
        except PlaywrightError:
            page.wait_for_timeout(1500)
            continue
        page.get_by_label("Username").fill(username)
        page.get_by_label("Password").fill(password)
        page.get_by_role("button", name="Sign In").click(timeout=ACTION_TIMEOUT_MS * 2)
        page.wait_for_timeout(1000)
        if "/login" not in page.url:
            return True
        page.wait_for_timeout(1500)
    return False


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
    docker_since_token = str(int(time.time()))
    deadline_monotonic = time.monotonic() + MAX_TEST_DURATION_SECONDS
    login_user, login_password = _load_login_credentials()

    page_errors: list[str] = []
    console_errors: list[str] = []
    ignored_console_errors: list[str] = []
    response_5xx: list[str] = []
    request_failures: list[str] = []
    ignored_request_failures: list[str] = []
    results: list[PageResult] = []
    total_interactions = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(base_url=BASE_URL)
        page = context.new_page()

        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: (
                ignored_console_errors.append(msg.text)
                if msg.type == "error" and _is_ignorable_console_error(msg.text)
                else console_errors.append(msg.text)
                if msg.type == "error"
                else None
            ),
        )
        page.on(
            "response",
            lambda resp: response_5xx.append(f"{resp.status} {resp.url}") if resp.status >= 500 else None,
        )
        def _on_request_failed(req: Any) -> None:
            failure_message = str(req.failure or "")
            entry = f"{req.method} {req.url} {failure_message}"
            if _is_ignorable_request_failure(str(req.method), str(req.url), failure_message):
                ignored_request_failures.append(entry)
                return
            request_failures.append(entry)

        page.on("requestfailed", _on_request_failed)

        if not _login_with_retry(page, username=login_user, password=login_password):
            screenshot = Path("artifacts/ui_clickthrough_login_failed.png")
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            pytest.fail(f"Login failed for user '{login_user}'. See screenshot: {screenshot}")

        queue: deque[str] = deque(SEED_PATHS)
        visited: set[str] = set()

        while (
            queue
            and len(visited) < MAX_PAGES
            and total_interactions < MAX_TOTAL_INTERACTIONS
            and time.monotonic() < deadline_monotonic
        ):
            path = queue.popleft()
            if path in visited:
                continue
            visited.add(path)

            try:
                response = page.goto(
                    f"{BASE_URL}{path}",
                    wait_until="domcontentloaded",
                    timeout=PAGE_GOTO_TIMEOUT_MS,
                )
            except PlaywrightError as exc:
                results.append(
                    PageResult(
                        path=path,
                        status_code=0,
                        interactions=0,
                        total_targets_seen=0,
                        skipped_risky=0,
                        skipped_flaky=0,
                        interaction_counts={},
                        interaction_failures=[f"GOTO failed: {exc!s}"],
                        discovered_paths=[],
                    )
                )
                continue
            status_code = response.status if response else 0
            page.wait_for_timeout(PAGE_SETTLE_MS)

            discovered_paths = _discover_paths(page)
            for discovered in discovered_paths:
                if discovered not in visited and discovered not in queue:
                    queue.append(discovered)

            if status_code >= 400:
                results.append(
                    PageResult(
                        path=path,
                        status_code=status_code,
                        interactions=0,
                        total_targets_seen=0,
                        skipped_risky=0,
                        skipped_flaky=0,
                        interaction_counts={},
                        interaction_failures=[f"HTTP {status_code}"],
                        discovered_paths=discovered_paths,
                    )
                )
                continue

            remaining_total = max(MAX_TOTAL_INTERACTIONS - total_interactions, 0)
            (
                interactions,
                total_targets_seen,
                skipped_risky,
                skipped_flaky,
                interaction_failures,
                discovered_via_click,
                interaction_counts,
            ) = _interact_with_targets(
                page,
                path,
                remaining_total=remaining_total,
                deadline_monotonic=deadline_monotonic,
            )
            total_interactions += interactions
            for discovered in discovered_via_click:
                if discovered not in visited and discovered not in queue:
                    queue.append(discovered)
            results.append(
                PageResult(
                    path=path,
                    status_code=status_code,
                    interactions=interactions,
                    total_targets_seen=total_targets_seen,
                    skipped_risky=skipped_risky,
                    skipped_flaky=skipped_flaky,
                    interaction_counts=interaction_counts,
                    interaction_failures=interaction_failures,
                    discovered_paths=sorted(set(discovered_paths + discovered_via_click)),
                )
            )

        context.close()
        browser.close()

    docker_errors = _collect_docker_errors(docker_since_token)

    report: dict[str, Any] = {
        "base_url": BASE_URL,
        "started_at": started_at,
        "docker_error_services": list(DOCKER_ERROR_SERVICES),
        "checked_pages": [r.path for r in results],
        "page_results": [
            {
                "path": r.path,
                "status_code": r.status_code,
                "interactions": r.interactions,
                "total_targets_seen": r.total_targets_seen,
                "skipped_risky": r.skipped_risky,
                "skipped_flaky": r.skipped_flaky,
                "interaction_counts": r.interaction_counts,
                "interaction_failures": r.interaction_failures,
                "discovered_paths": r.discovered_paths,
                "passed": r.status_code < 400 and not r.interaction_failures,
            }
            for r in results
        ],
        "summary": {
            "total_pages_checked": len(results),
            "total_interactions": sum(r.interactions for r in results),
            "total_click_like_interactions": sum(
                r.interaction_counts.get("button", 0)
                + r.interaction_counts.get("tab", 0)
                + r.interaction_counts.get("link", 0)
                + r.interaction_counts.get("toggle", 0)
                for r in results
            ),
            "total_form_interactions": sum(
                r.interaction_counts.get("input", 0) + r.interaction_counts.get("select", 0) for r in results
            ),
            "total_targets_seen": sum(r.total_targets_seen for r in results),
            "total_skipped_risky": sum(r.skipped_risky for r in results),
            "total_skipped_flaky": sum(r.skipped_flaky for r in results),
            "pages_passed": sum(1 for r in results if r.status_code < 400 and not r.interaction_failures),
            "pages_failed": sum(1 for r in results if r.status_code >= 400 or r.interaction_failures),
            "max_pages_budget": MAX_PAGES,
            "max_total_interactions_budget": MAX_TOTAL_INTERACTIONS,
            "max_duration_seconds_budget": MAX_TEST_DURATION_SECONDS,
            "ignored_console_errors": len(ignored_console_errors),
        },
        "page_errors": page_errors,
        "console_errors": console_errors,
        "ignored_console_errors": ignored_console_errors,
        "response_5xx": response_5xx,
        "request_failures": request_failures,
        "ignored_request_failures": ignored_request_failures,
        "docker_errors": docker_errors,
    }
    report_path = Path("artifacts/ui_clickthrough_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    page_interaction_failures = [f"{r.path}: {msg}" for r in results for msg in r.interaction_failures]
    total_interactions_observed = sum(r.interactions for r in results)
    assert (
        total_interactions_observed >= MIN_TOTAL_INTERACTIONS
    ), f"Interaction coverage too low: observed {total_interactions_observed}, expected >= {MIN_TOTAL_INTERACTIONS}"
    assert not page_errors, f"Browser page errors found: {page_errors[:5]}"
    assert not console_errors, f"Browser console errors found: {console_errors[:5]}"
    assert not response_5xx, f"HTTP 5xx responses found: {response_5xx[:5]}"
    assert not request_failures, f"Failed requests found: {request_failures[:5]}"
    assert not page_interaction_failures, f"UI interaction failures found: {page_interaction_failures[:10]}"
    assert not docker_errors, f"Docker errors found after click-through: {docker_errors[:10]}"
