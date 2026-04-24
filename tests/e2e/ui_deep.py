"""Deep page inspector for selected web console routes.

Useful after a broad crawl when report snippets are truncated. This script logs in
and prints full (or capped) rendered body text for a focused route subset.

Usage:
    PYTHONPATH=. poetry run python tests/e2e/ui_deep.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_TARGETS: list[tuple[str, str]] = [
    ("/", "main dashboard"),
    ("/execution-quality", "execution quality full message"),
    ("/risk", "risk full state"),
    ("/risk/exposure", "exposure details"),
    ("/tax-lots", "tax lots state"),
    ("/alerts", "alerts config"),
    ("/reports/scheduled", "scheduled reports"),
    ("/admin", "admin tabs"),
    ("/compare", "compare page"),
    ("/notebooks", "notebooks"),
    ("/health", "health page"),
    ("/journal", "journal page"),
    ("/data/features", "feature store"),
    ("/data/sources", "data sources"),
    ("/data/coverage", "coverage inspector"),
    ("/research/universes", "universes"),
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect rendered text on selected routes.")
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
        "--route",
        action="append",
        dest="routes",
        default=None,
        help="Optional route override (repeatable).",
    )
    parser.add_argument(
        "--chars",
        type=int,
        default=2_500,
        help="Max chars of body text to print per route.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional output path for structured route text payload.",
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


def _login(page: object, *, base_url: str, username: str, password: str) -> bool:
    page_ref = page
    page_ref.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=25_000)
    page_ref.get_by_label("Username").fill(username)
    page_ref.get_by_label("Password").fill(password)
    page_ref.get_by_role("button", name="Sign In").click(timeout=5_000)
    page_ref.wait_for_timeout(1_000)
    return "/login" not in page_ref.url


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
    targets = (
        [(route, "custom") for route in args.routes]
        if args.routes
        else DEFAULT_TARGETS
    )
    collected: list[dict[str, str]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()

        if not _login(page, base_url=base_url, username=username, password=password):
            print("Login failed; still on /login")
            browser.close()
            return 2

        page.goto(f"{base_url}/", wait_until="networkidle", timeout=25_000)

        for route, label in targets:
            print(f"\n=== {route} — {label} ===")
            page.goto(f"{base_url}{route}", wait_until="networkidle", timeout=25_000)
            if "/login" in page.url:
                print("  bounced to login; re-authenticating")
                if not _login(page, base_url=base_url, username=username, password=password):
                    print("  re-login failed")
                    continue
                page.goto(f"{base_url}{route}", wait_until="networkidle", timeout=25_000)

            time.sleep(2.0)
            body_text = page.inner_text("body")
            snippet = body_text[: args.chars]
            print(snippet)
            collected.append(
                {
                    "route": route,
                    "label": label,
                    "url": page.url,
                    "text": snippet,
                }
            )

        browser.close()

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"pages": collected}, indent=2), encoding="utf-8")
        print(f"\nSaved deep inspection report to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
