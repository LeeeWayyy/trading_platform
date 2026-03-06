"""Unit tests for tools.kb.parsers — review, JUnit, git, and classifier."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tools.kb.models import Severity, TestStatus
from tools.kb.parsers import (
    ALLOWED_EXTENSIONLESS,
    ALLOWED_EXTENSIONS,
    _default_taxonomy_path,
    _filter_files,
    classify_rule_id,
    normalize_error_signature,
    parse_git_commit_date,
    parse_git_show,
    parse_junit_xml,
    parse_review_artifact,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestClassifyRuleId:
    """Tests for rule_id classification."""

    def test_utc_naive_datetime(self) -> None:
        """Test classification of naive datetime issues."""
        assert classify_rule_id("datetime.now() used without timezone") == "UTC_NAIVE_DATETIME"

    def test_missing_cb_check(self) -> None:
        """Test classification of missing circuit breaker check."""
        assert classify_rule_id("Missing circuit breaker check") == "MISSING_CB_CHECK"

    def test_swallowed_exception(self) -> None:
        """Test classification of swallowed exception."""
        assert classify_rule_id("bare except clause swallows exception") == "SWALLOWED_EXCEPTION"

    def test_swallowed_exception_canonical_phrasing(self) -> None:
        """Test that canonical description phrasing matches SWALLOWED_EXCEPTION."""
        assert classify_rule_id("Exception caught but not logged or re-raised") == "SWALLOWED_EXCEPTION"

    def test_missing_structured_log_context(self) -> None:
        """Test that canonical description phrasing matches MISSING_STRUCTURED_LOG_CONTEXT."""
        assert classify_rule_id("Log statement missing structured context fields") == "MISSING_STRUCTURED_LOG_CONTEXT"

    def test_sql_injection(self) -> None:
        """Test classification of SQL injection risk."""
        assert classify_rule_id("f-string used in SQL query") == "SQL_INJECTION_RISK"

    def test_hardcoded_credential(self) -> None:
        """Test classification of hardcoded credentials."""
        assert classify_rule_id("hardcoded password found") == "HARDCODED_CREDENTIAL"

    def test_no_match_returns_none(self) -> None:
        """Test that unclassifiable text returns None."""
        assert classify_rule_id("some random unrelated text") is None

    def test_file_path_included_in_match(self) -> None:
        """Test that file_path is included in classification text."""
        result = classify_rule_id("missing check", "circuit_breaker.py")
        assert result == "MISSING_CB_CHECK"

    def test_case_insensitive(self) -> None:
        """Test that classification is case-insensitive."""
        assert classify_rule_id("DATETIME.NOW() without timezone") == "UTC_NAIVE_DATETIME"


class TestNormalizeErrorSignature:
    """Tests for error signature normalization."""

    def test_strips_line_numbers(self) -> None:
        """Test that line numbers are stripped."""
        sig1 = normalize_error_signature("File 'test.py', line 42\nAssertionError: bad")
        sig2 = normalize_error_signature("File 'test.py', line 99\nAssertionError: bad")
        assert sig1 == sig2

    def test_strips_file_paths(self) -> None:
        """Test that file paths are stripped."""
        sig1 = normalize_error_signature('File "/path/a.py", line 1\nTypeError: x')
        sig2 = normalize_error_signature('File "/other/b.py", line 1\nTypeError: x')
        assert sig1 == sig2

    def test_deterministic(self) -> None:
        """Test that same input gives same output."""
        text = "AssertionError: expected True"
        assert normalize_error_signature(text) == normalize_error_signature(text)

    def test_different_errors_different_sigs(self) -> None:
        """Test that different errors produce different signatures."""
        sig1 = normalize_error_signature("TypeError: int not callable")
        sig2 = normalize_error_signature("ValueError: invalid literal")
        assert sig1 != sig2


class TestFilterFiles:
    """Tests for file extension filtering."""

    def test_allows_python_files(self) -> None:
        """Test that .py files pass filter."""
        assert _filter_files(["a.py", "b.py"]) == ["a.py", "b.py"]

    def test_filters_binary_files(self) -> None:
        """Test that binary files are filtered out."""
        assert _filter_files(["a.py", "image.png", "lib.so"]) == ["a.py"]

    def test_allows_markdown(self) -> None:
        """Test that .md files pass filter."""
        assert _filter_files(["doc.md"]) == ["doc.md"]

    def test_allows_extensionless_files(self) -> None:
        """Test that known extensionless files (Makefile, Dockerfile) pass filter."""
        result = _filter_files(["Makefile", "a.py", "Dockerfile", "image.png"])
        assert "Makefile" in result
        assert "Dockerfile" in result
        assert "a.py" in result
        assert "image.png" not in result

    def test_allowed_extensions_frozen(self) -> None:
        """Test ALLOWED_EXTENSIONS is immutable."""
        assert isinstance(ALLOWED_EXTENSIONS, frozenset)
        assert ".py" in ALLOWED_EXTENSIONS

    def test_allowed_extensionless_frozen(self) -> None:
        """Test ALLOWED_EXTENSIONLESS is immutable."""
        assert isinstance(ALLOWED_EXTENSIONLESS, frozenset)
        assert "Makefile" in ALLOWED_EXTENSIONLESS


class TestParseReviewArtifact:
    """Tests for review artifact parsing."""

    def test_parses_json_array(self, tmp_path: Path) -> None:
        """Test parsing a JSON array of findings."""
        data = [
            {"file_path": "a.py", "severity": "HIGH", "summary": "naive datetime"},
            {"file_path": "b.py", "severity": "LOW", "summary": "missing test marker"},
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert len(findings) == 2
        assert findings[0].severity == Severity.HIGH
        assert findings[0].run_id == "run1"

    def test_parses_json_with_findings_key(self, tmp_path: Path) -> None:
        """Test parsing JSON with 'findings' wrapper key."""
        findings = parse_review_artifact(FIXTURES_DIR / "sample_review.json", "gemini", "run1")
        assert len(findings) == 3
        assert findings[0].file_path == "apps/signal_service/main.py"
        assert findings[0].rule_id == "UTC_NAIVE_DATETIME"

    def test_parses_jsonl(self, tmp_path: Path) -> None:
        """Test parsing JSONL format (one JSON per line)."""
        lines = [
            json.dumps({"file_path": "a.py", "summary": "issue 1"}),
            json.dumps({"file_path": "b.py", "summary": "issue 2"}),
        ]
        artifact = tmp_path / "review.jsonl"
        artifact.write_text("\n".join(lines))
        findings = parse_review_artifact(artifact, "codex", "run2")
        assert len(findings) == 2

    def test_handles_null_severity(self, tmp_path: Path) -> None:
        """Test that null severity in JSON doesn't crash parser."""
        data = [{"file_path": "a.py", "severity": None, "summary": "issue with null severity"}]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert len(findings) == 1
        assert findings[0].severity is None

    def test_handles_numeric_severity(self, tmp_path: Path) -> None:
        """Test that numeric severity values don't crash parser."""
        data = [{"file_path": "a.py", "severity": 1, "summary": "numeric severity"}]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert len(findings) == 1
        # Numeric "1" doesn't match any Severity enum member, so severity is None
        assert findings[0].severity is None

    def test_maps_codex_priority_to_severity(self, tmp_path: Path) -> None:
        """Test that Codex priority field maps to Severity enum."""
        data = [
            {"file_path": "a.py", "priority": 0, "summary": "P0 critical"},
            {"file_path": "b.py", "priority": 1, "summary": "P1 high"},
            {"file_path": "c.py", "priority": 2, "summary": "P2 medium"},
            {"file_path": "d.py", "priority": 3, "summary": "P3 low"},
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 4
        assert findings[0].severity == Severity.CRITICAL
        assert findings[1].severity == Severity.HIGH
        assert findings[2].severity == Severity.MEDIUM
        assert findings[3].severity == Severity.LOW

    def test_maps_string_priority_to_severity(self, tmp_path: Path) -> None:
        """Test that string priority values (e.g., '1') are coerced to int for mapping."""
        data = [
            {"file_path": "a.py", "priority": "0", "summary": "P0 string"},
            {"file_path": "b.py", "priority": "1", "summary": "P1 string"},
            {"file_path": "c.py", "priority": "2", "summary": "P2 string"},
            {"file_path": "d.py", "priority": "3", "summary": "P3 string"},
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 4
        assert findings[0].severity == Severity.CRITICAL
        assert findings[1].severity == Severity.HIGH
        assert findings[2].severity == Severity.MEDIUM
        assert findings[3].severity == Severity.LOW

    def test_maps_priority_from_title_bracket(self, tmp_path: Path) -> None:
        """Test that [P1] in title maps to severity when no priority field exists."""
        data = [{"title": "[P1] Missing check", "body": "desc", "file_path": "a.py"}]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_parses_code_location_fields(self, tmp_path: Path) -> None:
        """Test that nested code_location fields are parsed correctly."""
        data = [
            {
                "title": "[P1] Example issue",
                "body": "Detailed description",
                "code_location": {
                    "absolute_file_path": "apps/main.py",
                    "line_range": {"start": 42, "end": 50},
                },
                "priority": 1,
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        assert findings[0].file_path == "apps/main.py"
        assert findings[0].line == 42
        assert "Example issue" in (findings[0].summary or "")

    def test_normalizes_absolute_paths_to_repo_relative(self, tmp_path: Path) -> None:
        """Test that absolute file paths are converted to repo-relative."""
        data = [
            {
                "summary": "issue found",
                "code_location": {
                    "absolute_file_path": "/fake/repo/root/apps/main.py",
                    "line_range": {"start": 10, "end": 20},
                },
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        with patch("tools.kb.parsers._get_repo_root", return_value="/fake/repo/root"):
            findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        assert findings[0].file_path == "apps/main.py"

    def test_absolute_path_outside_repo_kept_as_is(self, tmp_path: Path) -> None:
        """Test that absolute paths outside repo root are kept unchanged."""
        data = [
            {
                "file_path": "/other/location/file.py",
                "summary": "issue found",
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        with patch("tools.kb.parsers._get_repo_root", return_value="/fake/repo/root"):
            findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        assert findings[0].file_path == "/other/location/file.py"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Test that missing artifact returns empty list."""
        findings = parse_review_artifact(tmp_path / "nonexistent.json", "gemini", "run1")
        assert findings == []

    def test_classifies_rule_ids(self) -> None:
        """Test that findings are auto-classified by rule_id."""
        findings = parse_review_artifact(FIXTURES_DIR / "sample_review.json", "gemini", "run1")
        rule_ids = [f.rule_id for f in findings]
        assert "UTC_NAIVE_DATETIME" in rule_ids
        assert "MISSING_CB_CHECK" in rule_ids

    def test_rejects_unregistered_rule_id(self, tmp_path: Path) -> None:
        """Test that unregistered rule_ids from artifacts are rejected and re-classified."""
        data = [{"file_path": "a.py", "rule_id": "FAKE_RULE_999", "summary": "some issue"}]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert len(findings) == 1
        # FAKE_RULE_999 is not in taxonomy, so it should be re-classified (likely None)
        assert findings[0].rule_id != "FAKE_RULE_999"

    def test_accepts_registered_rule_id(self, tmp_path: Path) -> None:
        """Test that registered rule_ids from artifacts are preserved."""
        data = [
            {
                "file_path": "a.py",
                "rule_id": "UTC_NAIVE_DATETIME",
                "summary": "some unrelated text",
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert len(findings) == 1
        assert findings[0].rule_id == "UTC_NAIVE_DATETIME"

    def test_metadata_only_dict_yields_no_findings(self, tmp_path: Path) -> None:
        """Test that a dict without a 'findings' list yields no findings."""
        data = {"model": "gemini-2.5", "status": "ok"}
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert findings == []

    def test_findings_null_yields_no_findings(self, tmp_path: Path) -> None:
        """Test that {'findings': null} yields no findings instead of crashing."""
        data = {"findings": None}
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert findings == []

    def test_non_dict_jsonl_items_skipped(self, tmp_path: Path) -> None:
        """Test that non-dict JSONL items (scalars, arrays) are skipped."""
        lines = [
            '"just a string"',
            json.dumps({"file_path": "a.py", "summary": "valid finding"}),
            "42",
            json.dumps([1, 2, 3]),
        ]
        artifact = tmp_path / "review.jsonl"
        artifact.write_text("\n".join(lines))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        assert len(findings) == 1
        assert findings[0].file_path == "a.py"

    def test_malformed_finding_skipped(self, tmp_path: Path) -> None:
        """Test that a malformed finding item is skipped, not aborting the parse."""
        data = [
            {"file_path": "a.py", "summary": "good finding"},
            {"file_path": "b.py", "summary": "bad", "confidence": "not-a-number"},
            {"file_path": "c.py", "summary": "also good"},
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "gemini", "run1")
        # At least the valid findings should be parsed
        paths = [f.file_path for f in findings]
        assert "a.py" in paths
        assert "c.py" in paths

    def test_non_string_title_body_coerced(self, tmp_path: Path) -> None:
        """Test that non-string title/body fields are coerced without crashing."""
        data = [
            {
                "title": {"nested": "dict"},
                "body": 12345,
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        assert findings[0].summary is not None

    def test_non_string_code_location_path_ignored(self, tmp_path: Path) -> None:
        """Test that non-string code_location.absolute_file_path is safely ignored."""
        data = [
            {
                "summary": "issue found",
                "code_location": {
                    "absolute_file_path": 12345,
                    "line_range": {"start": 10, "end": 20},
                },
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        # Non-string path should be ignored, not crash
        assert findings[0].file_path is None
        assert findings[0].line == 10

    def test_confidence_score_alias(self, tmp_path: Path) -> None:
        """Test that confidence_score (Codex-style) is used when confidence is absent."""
        data = [
            {
                "file_path": "a.py",
                "summary": "issue",
                "confidence_score": 0.92,
            }
        ]
        artifact = tmp_path / "review.json"
        artifact.write_text(json.dumps(data))
        findings = parse_review_artifact(artifact, "codex", "run1")
        assert len(findings) == 1
        assert findings[0].confidence == 0.92

    def test_finding_ids_are_unique(self) -> None:
        """Test that finding IDs are unique within a run."""
        findings = parse_review_artifact(FIXTURES_DIR / "sample_review.json", "gemini", "run1")
        ids = [f.finding_id for f in findings]
        assert len(ids) == len(set(ids))

    def test_idempotent_ids(self) -> None:
        """Test that parsing the same artifact twice gives same finding IDs."""
        f1 = parse_review_artifact(FIXTURES_DIR / "sample_review.json", "gemini", "run1")
        f2 = parse_review_artifact(FIXTURES_DIR / "sample_review.json", "gemini", "run1")
        assert [f.finding_id for f in f1] == [f.finding_id for f in f2]


class TestParseJunitXml:
    """Tests for JUnit XML parsing."""

    def test_parses_sample(self) -> None:
        """Test parsing sample JUnit XML fixture."""
        results = parse_junit_xml(FIXTURES_DIR / "sample_junit.xml", "run1")
        assert len(results) == 4

    def test_identifies_pass(self) -> None:
        """Test that passing tests are identified."""
        results = parse_junit_xml(FIXTURES_DIR / "sample_junit.xml", "run1")
        passing = [r for r in results if r.status == TestStatus.PASS]
        assert len(passing) == 2

    def test_identifies_failure(self) -> None:
        """Test that failing tests have error signature."""
        results = parse_junit_xml(FIXTURES_DIR / "sample_junit.xml", "run1")
        failures = [r for r in results if r.status == TestStatus.FAIL]
        assert len(failures) == 1
        assert failures[0].error_signature is not None

    def test_identifies_skipped(self) -> None:
        """Test that skipped tests are identified."""
        results = parse_junit_xml(FIXTURES_DIR / "sample_junit.xml", "run1")
        skipped = [r for r in results if r.status == TestStatus.SKIP]
        assert len(skipped) == 1

    def test_duration_parsed(self) -> None:
        """Test that duration is parsed from time attribute."""
        results = parse_junit_xml(FIXTURES_DIR / "sample_junit.xml", "run1")
        assert results[0].duration_ms == 123  # 0.123s * 1000

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Test that missing XML returns empty list."""
        results = parse_junit_xml(tmp_path / "nonexistent.xml", "run1")
        assert results == []

    def test_non_numeric_time_yields_zero_duration(self, tmp_path: Path) -> None:
        """Test that non-numeric time attribute yields 0 duration instead of crashing."""
        xml_content = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="test">'
            '<testcase classname="test_mod" name="test_func" time="abc"/>'
            "</testsuite>"
        )
        xml_path = tmp_path / "bad_time.xml"
        xml_path.write_text(xml_content)
        results = parse_junit_xml(xml_path, "run1")
        assert len(results) == 1
        assert results[0].duration_ms == 0

    def test_defusedxml_exception_handled(self, tmp_path: Path) -> None:
        """Test that DefusedXmlException variants are caught and return empty."""
        xml_content = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<testsuite><testcase classname='a' name='b'/></testsuite>"
        )
        xml_path = tmp_path / "xxe.xml"
        xml_path.write_text(xml_content)
        results = parse_junit_xml(xml_path, "run1")
        # defusedxml should raise an exception for the DTD, and we catch it
        assert results == []

    def test_xfail_detected_by_type_attribute(self, tmp_path: Path) -> None:
        """Test that <skipped type="pytest.xfail"> is classified as XFAIL."""
        xml_content = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="test">'
            '<testcase classname="tests.test_mod" name="test_expected_fail" time="0.05">'
            '<skipped type="pytest.xfail" message="strict reason for skip"/>'
            '</testcase>'
            '</testsuite>'
        )
        xml_path = tmp_path / "xfail.xml"
        xml_path.write_text(xml_content)
        results = parse_junit_xml(xml_path, "run1")
        assert len(results) == 1
        assert results[0].status == TestStatus.XFAIL

    def test_xfail_detected_by_message(self, tmp_path: Path) -> None:
        """Test that 'xfail' in message is still classified as XFAIL."""
        xml_content = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="test">'
            '<testcase classname="tests.test_mod" name="test_xfail_msg" time="0.05">'
            '<skipped message="reason: xfail - expected failure"/>'
            '</testcase>'
            '</testsuite>'
        )
        xml_path = tmp_path / "xfail_msg.xml"
        xml_path.write_text(xml_content)
        results = parse_junit_xml(xml_path, "run1")
        assert len(results) == 1
        assert results[0].status == TestStatus.XFAIL

    def test_nodeid_format(self) -> None:
        """Test that nodeid combines classname and name."""
        results = parse_junit_xml(FIXTURES_DIR / "sample_junit.xml", "run1")
        assert results[0].test_nodeid == "tests.test_signal_service::test_signal_generation"


class TestParseGitShow:
    """Tests for git show parsing."""

    def test_parses_files(self) -> None:
        """Test that git show output is parsed into file list."""
        mock_output = "apps/main.py\nlibs/utils.py\nREADME.md\n"
        with patch("tools.kb.parsers.subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            mock_run.return_value.returncode = 0
            files = parse_git_show("abc123")
        assert "apps/main.py" in files
        assert "libs/utils.py" in files
        assert "README.md" in files

    def test_uses_diff_filter_acmr(self) -> None:
        """Test that --diff-filter=ACMR is passed to exclude deletes but include renames."""
        with patch("tools.kb.parsers.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "a.py\n"
            mock_run.return_value.returncode = 0
            parse_git_show("abc123")
        cmd = mock_run.call_args[0][0]
        assert "--diff-filter=ACMR" in cmd

    def test_filters_binary_files(self) -> None:
        """Test that non-allowed extensions are filtered."""
        mock_output = "a.py\nimage.png\nlib.so\n"
        with patch("tools.kb.parsers.subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            mock_run.return_value.returncode = 0
            files = parse_git_show("abc123")
        assert files == ["a.py"]

    def test_handles_failure(self) -> None:
        """Test that subprocess failure returns empty list."""
        import subprocess

        with patch(
            "tools.kb.parsers.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            files = parse_git_show("bad_sha")
        assert files == []


class TestParseGitCommitDate:
    """Tests for git commit date parsing."""

    def test_returns_date(self) -> None:
        """Test that commit date is returned normalized to UTC."""
        with patch("tools.kb.parsers.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "2024-06-15T10:30:00+00:00\n"
            mock_run.return_value.returncode = 0
            date = parse_git_commit_date("abc123")
        assert date is not None
        assert date.startswith("2024-06-15T10:30:00.")
        assert date.endswith("Z")

    def test_normalizes_timezone_offset(self) -> None:
        """Test that non-UTC timezone offsets are normalized to UTC."""
        with patch("tools.kb.parsers.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "2024-06-15T12:30:00+02:00\n"
            mock_run.return_value.returncode = 0
            date = parse_git_commit_date("abc123")
        assert date is not None
        assert date.startswith("2024-06-15T10:30:00.")
        assert date.endswith("Z")

    def test_includes_microseconds(self) -> None:
        """Test that commit dates include microsecond precision for consistent sorting."""
        with patch("tools.kb.parsers.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "2024-06-15T10:30:00+00:00\n"
            mock_run.return_value.returncode = 0
            date = parse_git_commit_date("abc123")
        assert date is not None
        # Should match _now_iso format: %Y-%m-%dT%H:%M:%S.%fZ
        assert ".000000Z" in date

    def test_handles_failure(self) -> None:
        """Test that failure returns None."""
        import subprocess

        with patch(
            "tools.kb.parsers.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            date = parse_git_commit_date("bad_sha")
        assert date is None


class TestDefaultTaxonomyPath:
    """Tests for _default_taxonomy_path with importlib.resources."""

    def test_env_override_takes_precedence(self) -> None:
        """Test that KB_TAXONOMY_PATH env var overrides importlib.resources."""
        with patch.dict("os.environ", {"KB_TAXONOMY_PATH": "/custom/taxonomy.yaml"}):
            result = _default_taxonomy_path()
        assert result == Path("/custom/taxonomy.yaml")

    def test_uses_importlib_resources_by_default(self) -> None:
        """Test that importlib.resources is used when env var is unset."""
        with patch.dict("os.environ", {}, clear=False):
            # Remove env override if present
            import os

            env = os.environ.pop("KB_TAXONOMY_PATH", None)
            try:
                result = _default_taxonomy_path()
                # Should resolve to a path ending in taxonomy.yaml
                assert result.name == "taxonomy.yaml"
                assert "tools" in str(result) or "kb" in str(result)
            finally:
                if env is not None:
                    os.environ["KB_TAXONOMY_PATH"] = env
