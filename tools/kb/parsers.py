"""Parsers for review artifacts, JUnit XML, git history, and rule classification."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as ET
import yaml

from tools.kb.models import Finding, Severity, TestResult, TestStatus

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".md",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".sh",
        ".sql",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".cfg",
        ".ini",
        ".txt",
        ".rst",
    }
)

# Common extensionless files that should be treated as trackable
ALLOWED_EXTENSIONLESS: frozenset[str] = frozenset(
    {"Makefile", "Dockerfile", "Procfile", "Gemfile", "Rakefile", "Vagrantfile"}
)

# Taxonomy location: env override > importlib.resources > __file__ fallback.
def _default_taxonomy_path() -> Path:
    env = os.environ.get("KB_TAXONOMY_PATH")
    if env:
        return Path(env)
    try:
        ref = importlib.resources.files("tools.kb").joinpath("taxonomy.yaml")
        return Path(str(ref))
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        return Path(__file__).parent / "taxonomy.yaml"


_TAXONOMY_PATH = _default_taxonomy_path()
_TAXONOMY: dict[str, list[re.Pattern[str]]] = {}
_REPO_ROOT: str | None = None


def _get_repo_root() -> str:
    """Get the git repository root directory (cached)."""
    global _REPO_ROOT  # noqa: PLW0603
    if _REPO_ROOT is not None:
        return _REPO_ROOT
    from tools.kb.db import _get_repo_root as _db_get_repo_root

    _REPO_ROOT = str(_db_get_repo_root())
    return _REPO_ROOT


def _to_repo_relative(absolute_path: str) -> str:
    """Convert an absolute file path to a repository-relative path.

    If the path is not under the repo root, returns the original path unchanged.
    """
    repo_root = _get_repo_root()
    try:
        return str(Path(absolute_path).relative_to(repo_root))
    except ValueError:
        # Path is not under repo root — return as-is
        return absolute_path


def _load_taxonomy() -> dict[str, list[re.Pattern[str]]]:
    """Load and compile taxonomy patterns from YAML (cached after first call)."""
    if _TAXONOMY:
        return _TAXONOMY
    if not _TAXONOMY_PATH.exists():
        logger.warning("Taxonomy YAML not found at %s; rule classification disabled", _TAXONOMY_PATH)
        return _TAXONOMY
    with open(_TAXONOMY_PATH) as f:
        data: Any = yaml.safe_load(f)
    for rule in data.get("rules", []):
        rule_id: str = rule["id"]
        patterns = [re.compile(p, re.IGNORECASE) for p in rule.get("patterns", [])]
        _TAXONOMY[rule_id] = patterns
    return _TAXONOMY


def classify_rule_id(summary: str, file_path: str | None = None) -> str | None:
    """Classify a finding summary into a rule_id using regex taxonomy matching."""
    taxonomy = _load_taxonomy()
    text = summary
    if file_path:
        text = f"{text} {file_path}"
    for rule_id, patterns in taxonomy.items():
        for pattern in patterns:
            if pattern.search(text):
                return rule_id
    return None


def normalize_error_signature(traceback_text: str) -> str:
    """Normalize a traceback into a stable dedup key."""
    cleaned = re.sub(r'File ".*?"', 'File "..."', traceback_text)
    cleaned = re.sub(r"line \d+", "line N", cleaned)
    cleaned = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", cleaned)
    lines = cleaned.strip().splitlines()
    last_line = lines[-1].strip() if lines else traceback_text
    return hashlib.sha256(last_line.encode()).hexdigest()[:16]


def _filter_files(files: list[str]) -> list[str]:
    """Filter file list to allowed extensions and known extensionless files."""
    return [
        f
        for f in files
        if Path(f).suffix in ALLOWED_EXTENSIONS or Path(f).name in ALLOWED_EXTENSIONLESS
    ]


def parse_review_artifact(
    path: str | Path,
    reviewer: str,
    run_id: str,
) -> list[Finding]:
    """Parse a review artifact (JSON or JSONL) into Finding objects."""
    path = Path(path)
    findings: list[Finding] = []
    if not path.exists():
        logger.warning("Review artifact not found: %s", path)
        return findings

    content = path.read_text()

    # Parse JSON or JSONL
    items: list[dict[str, Any]] = []
    try:
        data: Any = json.loads(content)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            findings_val = data.get("findings")
            items = findings_val if isinstance(findings_val, list) else []
    except json.JSONDecodeError:
        # Try JSONL (one JSON object per line)
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not items and content.strip():
        logger.warning(
            "Review artifact %s is not machine-readable JSON/JSONL — "
            "no findings extracted. Ensure the reviewer produces structured output.",
            path,
        )

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        finding_id = hashlib.sha256(
            f"{run_id}:{i}:{item.get('file_path', '')}:{item.get('summary', '')}".encode()
        ).hexdigest()[:16]

        raw_severity = item.get("severity")
        severity_str = str(raw_severity).upper() if raw_severity is not None else ""
        # Fallback: map Codex priority (P0-P3 or numeric) to Severity enum
        if severity_str not in Severity.__members__:
            priority = item.get("priority")
            title = str(item.get("title", ""))
            if priority is not None or re.search(r"\[P\d\]", title):
                prio_val = priority if priority is not None else None
                if prio_val is None:
                    match = re.search(r"\[P(\d)\]", title)
                    prio_val = int(match.group(1)) if match else None
                _prio_to_sev = {0: "CRITICAL", 1: "HIGH", 2: "MEDIUM", 3: "LOW"}
                severity_str = _prio_to_sev.get(prio_val, "") if prio_val is not None else ""
        severity = Severity(severity_str) if severity_str in Severity.__members__ else None

        summary_text: str = str(item.get("summary", item.get("message", "")) or "")
        # Fall back to title/body if summary/message are absent
        if not summary_text:
            raw_title = item.get("title", "")
            summary_text = str(raw_title) if raw_title else ""
            raw_body = item.get("body", "")
            body = str(raw_body) if raw_body else ""
            if body:
                summary_text = f"{summary_text}: {body}" if summary_text else body

        file_path_str: str = str(item.get("file_path", item.get("file", "")) or "")
        line_num = item.get("line")
        # Support nested code_location schema (e.g., Codex output)
        code_loc = item.get("code_location")
        if code_loc and isinstance(code_loc, dict):
            if not file_path_str:
                raw_path = code_loc.get("absolute_file_path", "")
                file_path_str = str(raw_path) if isinstance(raw_path, str) else ""
            line_range = code_loc.get("line_range")
            if line_num is None and isinstance(line_range, dict):
                line_num = line_range.get("start")

        # Normalize absolute paths to repo-relative
        if file_path_str and Path(file_path_str).is_absolute():
            file_path_str = _to_repo_relative(file_path_str)

        provided_rule_id = item.get("rule_id")
        taxonomy = _load_taxonomy()
        if provided_rule_id and provided_rule_id in taxonomy:
            rule_id = provided_rule_id
        else:
            rule_id = classify_rule_id(summary_text, file_path_str)

        try:
            findings.append(
                Finding(
                    finding_id=finding_id,
                    run_id=run_id,
                    severity=severity,
                    file_path=file_path_str or None,
                    line=line_num,
                    rule_id=rule_id,
                    summary=summary_text or None,
                    confidence=item.get("confidence", item.get("confidence_score")),
                )
            )
        except Exception:
            logger.warning("Skipping malformed finding item %d in %s", i, path)
            continue

    return findings


def parse_junit_xml(xml_path: str | Path, run_id: str) -> list[TestResult]:
    """Parse JUnit XML into TestResult objects."""
    xml_path = Path(xml_path)
    results: list[TestResult] = []
    if not xml_path.exists():
        logger.warning("JUnit XML not found: %s", xml_path)
        return results

    try:
        tree = ET.parse(str(xml_path))
    except (ET.ParseError, Exception) as exc:
        # defusedxml raises DefusedXmlException variants (DTD, entities, etc.)
        # in addition to standard ParseError — catch all to fail open.
        logger.warning("Malformed or forbidden JUnit XML, skipping %s: %s", xml_path, exc)
        return results
    root = tree.getroot()
    if root is None:
        logger.warning("JUnit XML has no root element: %s", xml_path)
        return results

    for tc in root.findall(".//testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        nodeid = f"{classname}::{name}" if classname else name
        time_s = tc.get("time", "0")
        try:
            duration_ms = int(float(time_s) * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        failure = tc.find("failure")
        error = tc.find("error")
        skipped = tc.find("skipped")

        status = TestStatus.PASS
        error_signature: str | None = None

        if failure is not None:
            status = TestStatus.FAIL
            error_text = failure.get("message", "") or failure.text or ""
            if error_text:
                error_signature = normalize_error_signature(error_text)
        elif error is not None:
            status = TestStatus.FAIL
            error_text = error.get("message", "") or error.text or ""
            if error_text:
                error_signature = normalize_error_signature(error_text)
        elif skipped is not None:
            msg = skipped.get("message", "")
            skip_type = skipped.get("type", "")
            is_xfail = "xfail" in msg.lower() or "xfail" in skip_type.lower()
            status = TestStatus.XFAIL if is_xfail else TestStatus.SKIP

        results.append(
            TestResult(
                run_id=run_id,
                test_nodeid=nodeid,
                status=status,
                error_signature=error_signature,
                duration_ms=duration_ms,
            )
        )

    return results


# Git subprocess timeout — configurable via env for large repos.
_GIT_TIMEOUT = int(os.environ.get("KB_GIT_TIMEOUT", "30"))


def parse_git_show(sha: str) -> list[str]:
    """Return changed (non-deleted) file paths for a commit SHA.

    Uses --diff-filter=ACMR to exclude deleted files while including
    rename targets, preventing stale edges without losing coupling
    signals from refactors.
    """
    try:
        result = subprocess.run(
            ["git", "show", "--name-only", "--diff-filter=ACMR", "--format=", sha],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to parse git show for %s", sha)
        return []
    files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    return _filter_files(files)


def parse_git_commit_date(sha: str) -> str | None:
    """Return the commit date in UTC ISO8601 format for a given SHA.

    Uses --format=%cI to get the commit date, then normalizes to UTC
    for consistent text-based timestamp ordering in SQLite.
    """
    try:
        result = subprocess.run(
            ["git", "show", "-s", "--format=%cI", sha],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    date_str = result.stdout.strip()
    if not date_str:
        return None
    # Normalize to UTC for consistent text-based ordering in SQLite
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError):
        return date_str


def parse_git_log_range(since: str) -> list[tuple[str, list[str]]]:
    """Mine git history since a date. Returns [(sha, [files]), ...]."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H", "--no-merges", f"--since={since}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT * 3,  # log range needs more time than single-commit ops
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to parse git log since %s", since)
        return []

    commits: list[tuple[str, list[str]]] = []
    for line in result.stdout.strip().splitlines():
        sha = line.strip()
        if sha:
            files = parse_git_show(sha)
            if files:
                commits.append((sha, files))
    return commits
