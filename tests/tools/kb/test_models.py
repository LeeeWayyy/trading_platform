"""Unit tests for tools.kb.models — Pydantic validation and roundtrip."""

from __future__ import annotations

from datetime import UTC, datetime

from tools.kb.models import (
    EdgeEvidence,
    ErrorFix,
    EvidenceSource,
    FileEdge,
    Finding,
    ImpactedFile,
    ImplementationBrief,
    ImplementationSession,
    IssuePattern,
    KnownPitfall,
    PreCommitCheckResult,
    RecommendedTest,
    Relation,
    ReviewRun,
    SessionOutcome,
    Severity,
    TestResult,
    TestRun,
    TestStatus,
    TroubleshootResult,
)


class TestEnums:
    """Tests for enum definitions."""

    def test_relation_values(self) -> None:
        """Test Relation enum has expected values."""
        assert Relation.CO_CHANGE == "CO_CHANGE"
        assert Relation.TESTS == "TESTS"
        assert Relation.ERROR_FIX == "ERROR_FIX"

    def test_evidence_source_values(self) -> None:
        """Test EvidenceSource enum values."""
        assert EvidenceSource.COMMIT == "COMMIT"
        assert EvidenceSource.REVIEW == "REVIEW"
        assert EvidenceSource.ANALYZE == "ANALYZE"

    def test_test_status_values(self) -> None:
        """Test TestStatus enum values."""
        assert TestStatus.PASS == "PASS"
        assert TestStatus.FAIL == "FAIL"
        assert TestStatus.SKIP == "SKIP"
        assert TestStatus.XFAIL == "XFAIL"

    def test_session_outcome_values(self) -> None:
        """Test SessionOutcome enum values."""
        assert SessionOutcome.COMMITTED == "COMMITTED"
        assert SessionOutcome.ABANDONED == "ABANDONED"
        assert SessionOutcome.WIP == "WIP"

    def test_severity_values(self) -> None:
        """Test Severity enum values."""
        assert Severity.CRITICAL == "CRITICAL"
        assert Severity.LOW == "LOW"


class TestTableModels:
    """Tests for table-mapping Pydantic models."""

    def test_file_edge_defaults(self) -> None:
        """Test FileEdge default values."""
        edge = FileEdge(src_file="a.py", dst_file="b.py", relation=Relation.CO_CHANGE)
        assert edge.weight == 0.0
        assert edge.support_count == 0
        assert edge.last_seen_sha is None

    def test_file_edge_roundtrip(self) -> None:
        """Test FileEdge JSON roundtrip."""
        edge = FileEdge(
            src_file="a.py",
            dst_file="b.py",
            relation=Relation.CO_CHANGE,
            weight=2.5,
            support_count=3,
            last_seen_sha="abc123",
        )
        data = edge.model_dump()
        restored = FileEdge(**data)
        assert restored == edge

    def test_edge_evidence_required_fields(self) -> None:
        """Test EdgeEvidence requires all mandatory fields."""
        now = datetime.now(UTC)
        ev = EdgeEvidence(
            evidence_id="ev1",
            src_file="a.py",
            dst_file="b.py",
            relation=Relation.CO_CHANGE,
            source=EvidenceSource.COMMIT,
            source_id="sha123",
            weight=1.0,
            observed_at=now,
        )
        assert ev.evidence_id == "ev1"
        assert ev.observed_at == now

    def test_finding_optional_fields(self) -> None:
        """Test Finding allows all optional fields to be None."""
        f = Finding(finding_id="f1", run_id="r1")
        assert f.severity is None
        assert f.file_path is None
        assert f.rule_id is None

    def test_review_run(self) -> None:
        """Test ReviewRun creation."""
        rr = ReviewRun(run_id="run1", reviewer="gemini")
        assert rr.run_id == "run1"
        assert rr.reviewer == "gemini"

    def test_issue_pattern(self) -> None:
        """Test IssuePattern creation."""
        ip = IssuePattern(rule_id="UTC_NAIVE_DATETIME", scope_path="apps/")
        assert ip.count == 0

    def test_implementation_session(self) -> None:
        """Test ImplementationSession creation."""
        now = datetime.now(UTC)
        s = ImplementationSession(
            session_id="s1",
            started_at=now,
            outcome=SessionOutcome.COMMITTED,
        )
        assert s.ended_at is None
        assert s.outcome == SessionOutcome.COMMITTED

    def test_test_run(self) -> None:
        """Test TestRun creation."""
        now = datetime.now(UTC)
        tr = TestRun(
            run_id="tr1",
            command="pytest",
            status="PASS",
            started_at=now,
            finished_at=now,
        )
        assert tr.status == "PASS"

    def test_test_result(self) -> None:
        """Test TestResult creation."""
        r = TestResult(
            run_id="tr1",
            test_nodeid="tests/test_foo.py::test_bar",
            status=TestStatus.FAIL,
            error_signature="abc123",
            duration_ms=150,
        )
        assert r.status == TestStatus.FAIL

    def test_error_fix(self) -> None:
        """Test ErrorFix creation."""
        ef = ErrorFix(
            fix_id="ef1",
            session_id="s1",
            error_signature="sig123",
            fixed_files_json='["a.py"]',
            confidence=0.9,
        )
        assert ef.confidence == 0.9


class TestQueryModels:
    """Tests for query output models."""

    def test_impacted_file(self) -> None:
        """Test ImpactedFile creation."""
        f = ImpactedFile(path="a.py", score=0.85, reason="CO_CHANGE in 4 commits")
        assert f.score == 0.85

    def test_recommended_test(self) -> None:
        """Test RecommendedTest creation."""
        t = RecommendedTest(path="tests/test_a.py", confidence=0.95)
        assert t.confidence == 0.95

    def test_known_pitfall(self) -> None:
        """Test KnownPitfall creation."""
        p = KnownPitfall(rule_id="UTC_NAIVE_DATETIME", scope="apps/", count=5)
        assert p.example is None

    def test_implementation_brief_defaults(self) -> None:
        """Test ImplementationBrief has empty defaults."""
        brief = ImplementationBrief()
        assert brief.likely_impacted_files == []
        assert brief.recommended_tests == []
        assert brief.known_pitfalls == []

    def test_troubleshoot_result_defaults(self) -> None:
        """Test TroubleshootResult has empty defaults."""
        result = TroubleshootResult()
        assert result.likely_fix_files == []
        assert result.past_fixes == []

    def test_pre_commit_check_result(self) -> None:
        """Test PreCommitCheckResult with advisory text."""
        result = PreCommitCheckResult()
        assert "historically coupled" in result.advisory
