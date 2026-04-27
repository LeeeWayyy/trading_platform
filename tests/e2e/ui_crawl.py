"""Playwright crawler for broad web console route coverage.

This script logs into the web console, walks a fixed route set, and captures:
1) HTTP status and final URL per route
2) Console warnings/errors and page errors
3) Failed HTTP requests (>=400)
4) Visible error markers and error-style notifications
5) Per-route screenshots and a consolidated JSON report

Usage:
    PYTHONPATH=. poetry run python tests/e2e/ui_crawl.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Response, sync_playwright

DEFAULT_ROUTES = [
    "/",
    "/trade",
    "/risk",
    "/risk/exposure",
    "/exposure",
    "/alerts",
    "/performance",
    "/attribution",
    "/tax-lots",
    "/strategies",
    "/research",
    "/research/universes",
    "/compare",
    "/shadow-results",
    "/data/shadow",
    "/data",
    "/data/management",
    "/data/coverage",
    "/data/inspector",
    "/data/features",
    "/data/sources",
    "/data/source-status",
    "/data/sql-explorer",
    "/sql-explorer",
    "/reports",
    "/reports/scheduled",
    "/execution-quality",
    "/journal",
    "/notebooks",
    "/admin",
    "/health",
]

VISIBLE_ERROR_MARKERS = [
    "internal server error",
    "500 internal",
    "traceback",
    "exception:",
    "error loading",
    "failed to load",
    "not found",
    "unauthorized",
    "forbidden",
    "something went wrong",
    "service unavailable",
    "no data available",
    "data unavailable",
    "not implemented",
    "coming soon",
    "to be implemented",
    "nan",
]


def _login_via_form(page: Page, *, base_url: str, username: str, password: str) -> bool:
    """Fallback login path using browser form controls."""
    page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=25_000)
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign In").click(timeout=5_000)
    page.wait_for_timeout(1_200)
    return "/login" not in page.url


def _login(page: Page, *, base_url: str, username: str, password: str) -> bool:
    """Login using API context first, then fallback to form login."""
    try:
        api = page.context.request
        response = api.post(
            f"{base_url}/auth/login",
            form={
                "username": username,
                "password": password,
                "auth_type": "dev",
                "next": "/",
            },
            max_redirects=0,
            timeout=30_000,
        )
        print(f"[login POST] status={response.status}")
    except PlaywrightError as exc:
        print(f"[login POST] failed: {exc}")

    page.goto(f"{base_url}/", wait_until="networkidle", timeout=30_000)
    print(f"[after login GET /] url={page.url}")
    if "/login" not in page.url:
        return True
    print("[login fallback] API login did not stick; trying form login")
    return _login_via_form(page, base_url=base_url, username=username, password=password)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run broad Playwright UI crawl against web_console.")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument(
        "--username",
        default=None,
        help="Override login username (default: WEB_CONSOLE_USER from .env)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Override login password (default: WEB_CONSOLE_PASSWORD from .env)",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional .env file path (defaults to repository .env)",
    )
    parser.add_argument(
        "--screens-dir",
        default="/tmp/ui_crawl_screens",
        help="Directory for page screenshots",
    )
    parser.add_argument(
        "--report-path",
        default="/tmp/ui_crawl_report.json",
        help="Path for JSON crawl report",
    )
    parser.add_argument(
        "--route",
        action="append",
        dest="routes",
        default=None,
        help="Optional route override (repeatable). Defaults to built-in route list.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Extra delay after route load before assertions/screenshot.",
    )
    parser.add_argument(
        "--screenshot-timeout-ms",
        type=int,
        default=5_000,
        help="Screenshot timeout per route in milliseconds.",
    )
    return parser.parse_args()


def _resolve_credentials(
    *,
    username_override: str | None,
    password_override: str | None,
    env_file: str,
) -> tuple[str, str]:
    dotenv_path = Path(env_file).expanduser() if env_file else Path(__file__).resolve().parents[2] / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=dotenv_path, override=False)
    except ImportError:
        # python-dotenv is optional; continue with already-exported environment variables.
        pass

    username = (username_override or os.getenv("WEB_CONSOLE_USER") or "").strip()
    password = password_override or os.getenv("WEB_CONSOLE_PASSWORD") or ""
    if not username or not password:
        env_hint = str(dotenv_path)
        msg = (
            "Missing login credentials. Set WEB_CONSOLE_USER and WEB_CONSOLE_PASSWORD "
            f"in environment or .env ({env_hint}), or pass --username/--password."
        )
        raise ValueError(msg)
    return username, password


def main() -> int:
    args = _parse_args()
    try:
        username, password = _resolve_credentials(
            username_override=args.username,
            password_override=args.password,
            env_file=args.env_file,
        )
    except ValueError as exc:
        print(str(exc))
        return 2

    base_url = args.base_url.rstrip("/")
    routes = args.routes if args.routes else DEFAULT_ROUTES
    screens_dir = Path(args.screens_dir)
    report_path = Path(args.report_path)
    screens_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    issues: dict[str, list[str]] = defaultdict(list)
    console_by_page: dict[str, list[str]] = defaultdict(list)
    failed_requests_by_page: dict[str, list[str]] = defaultdict(list)
    page_errors_by_page: dict[str, list[str]] = defaultdict(list)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        if not _login(page, base_url=base_url, username=username, password=password):
            print("!! Login failed. Aborting crawl.")
            browser.close()
            return 2

        for route in routes:
            url = f"{base_url}{route}"
            console_messages: list[str] = []
            failed_requests: list[str] = []
            page_errors: list[str] = []
            status: int | None = None

            def _on_console(msg: object, sink: list[str] = console_messages) -> None:
                if getattr(msg, "type", None) in {"error", "warning"}:
                    text = getattr(msg, "text", "")
                    sink.append(f"{msg.type}: {text[:400]}")

            def _on_response(
                response: Response, sink: list[str] = failed_requests
            ) -> None:
                if response.status >= 400:
                    sink.append(
                        f"{response.status} {response.request.method} {response.url}"
                    )

            def _on_page_error(exc: object, sink: list[str] = page_errors) -> None:
                sink.append(str(exc)[:500])

            page.on("console", _on_console)
            page.on("response", _on_response)
            page.on("pageerror", _on_page_error)

            print(f"\n=== {route} ===")
            try:
                response = page.goto(url, wait_until="networkidle", timeout=25_000)
                status = response.status if response else None
            except PlaywrightError as exc:
                issues[route].append(f"navigation-error: {exc}")

            if "/login" in page.url and route != "/login":
                print("  bounced to login, re-auth + retry")
                if _login(page, base_url=base_url, username=username, password=password):
                    try:
                        response = page.goto(url, wait_until="networkidle", timeout=25_000)
                        status = response.status if response else None
                    except PlaywrightError as exc:
                        issues[route].append(f"navigation-error-retry: {exc}")
                else:
                    issues[route].append("relogin-failed")

            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except PlaywrightError:
                pass
            time.sleep(args.settle_seconds)

            screenshot_name = (route.strip("/").replace("/", "_") or "root") + ".png"
            try:
                page.screenshot(
                    path=str(screens_dir / screenshot_name),
                    full_page=True,
                    timeout=args.screenshot_timeout_ms,
                )
            except PlaywrightError as exc:
                issues[route].append(f"screenshot-failed: {exc}")

            final_url = page.url
            if "/login" in final_url and route != "/login":
                issues[route].append(f"redirected-to-login (final={final_url})")

            try:
                body_text = page.inner_text("body", timeout=5_000)
            except PlaywrightError:
                body_text = ""

            lowered = body_text.lower()
            for marker in VISIBLE_ERROR_MARKERS:
                if marker in lowered:
                    index = lowered.find(marker)
                    snippet = body_text[max(0, index - 60) : index + 160].replace("\n", " ")
                    issues[route].append(f"visible:{marker!r} ctx='{snippet.strip()}'")

            try:
                notifications = page.evaluate(
                    """() => {
                        const selectors = [
                            '.q-notification--error',
                            '.q-notification.bg-negative',
                            '.q-notification.text-negative',
                            '[role=alert]',
                        ];
                        const texts = [];
                        for (const selector of selectors) {
                            document.querySelectorAll(selector).forEach((el) => {
                                const text = (el.innerText || '').slice(0, 200);
                                if (text.trim()) texts.push(text.trim());
                            });
                        }
                        return texts;
                    }"""
                )
                for entry in notifications or []:
                    issues[route].append(f"error-notification: {entry[:200]}")
            except PlaywrightError:
                pass

            if not body_text.strip():
                issues[route].append("empty-body (no rendered content)")

            if status is not None and status >= 400:
                issues[route].append(f"http-status={status}")

            console_by_page[route] = console_messages
            failed_requests_by_page[route] = failed_requests
            page_errors_by_page[route] = page_errors

            page.remove_listener("console", _on_console)
            page.remove_listener("response", _on_response)
            page.remove_listener("pageerror", _on_page_error)

            print(
                f"  status={status}, final_url={final_url}, "
                f"issues={len(issues[route])}, console={len(console_messages)}, "
                f"failed_reqs={len(failed_requests)}, page_errors={len(page_errors)}"
            )

        browser.close()

    report = {
        "config": {
            "base_url": base_url,
            "routes": routes,
            "screens_dir": str(screens_dir),
        },
        "summary": {
            route: {
                "issues": issues[route],
                "console_errors_warnings": console_by_page[route][:20],
                "failed_requests": failed_requests_by_page[route][:20],
                "page_errors": page_errors_by_page[route][:20],
            }
            for route in routes
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n========= SUMMARY =========")
    total_issue_count = 0
    for route in routes:
        total = (
            len(issues[route])
            + len(page_errors_by_page[route])
            + len(failed_requests_by_page[route])
        )
        if total:
            total_issue_count += total
            print(
                f"- {route}: issues={len(issues[route])} "
                f"page_errs={len(page_errors_by_page[route])} "
                f"failed_reqs={len(failed_requests_by_page[route])}"
            )
    print(f"\nFull report: {report_path}")
    return 0 if total_issue_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
