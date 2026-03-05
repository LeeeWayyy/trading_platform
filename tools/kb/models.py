"""Pydantic models for knowledge base entities and query outputs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# === Enums ===


class Relation(str, Enum):
    """Edge relation types between files."""

    CO_CHANGE = "CO_CHANGE"
    REFERENCES = "REFERENCES"
    IMPORTS = "IMPORTS"
    TESTS = "TESTS"
    ERROR_FIX = "ERROR_FIX"


class EvidenceSource(str, Enum):
    """Signal sources that produce edge evidence."""

    COMMIT = "COMMIT"
    REVIEW = "REVIEW"
    ANALYZE = "ANALYZE"
    SESSION = "SESSION"
    TEST = "TEST"
    ERROR_FIX = "ERROR_FIX"


class TestStatus(str, Enum):
    """Test result status."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    XFAIL = "XFAIL"


class SessionOutcome(str, Enum):
    """Implementation session outcome."""

    COMMITTED = "COMMITTED"
    ABANDONED = "ABANDONED"
    WIP = "WIP"


class Severity(str, Enum):
    """Finding severity levels."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


# === Table models ===


class FileEdge(BaseModel):
    """Aggregated file relationship edge."""

    src_file: str
    dst_file: str
    relation: Relation
    weight: float = 0.0
    support_count: int = 0
    last_seen_sha: str | None = None


class EdgeEvidence(BaseModel):
    """Raw evidence item for a file relationship."""

    evidence_id: str
    src_file: str
    dst_file: str
    relation: Relation
    source: EvidenceSource
    source_id: str
    weight: float
    observed_at: datetime


class Finding(BaseModel):
    """Review finding extracted from reviewer output."""

    finding_id: str
    run_id: str
    severity: Severity | None = None
    file_path: str | None = None
    line: int | None = None
    rule_id: str | None = None
    summary: str | None = None
    fixed_in_sha: str | None = None
    confidence: float | None = None


class ReviewRun(BaseModel):
    """Review run metadata."""

    run_id: str
    reviewer: str | None = None
    commit_sha: str | None = None
    reviewed_at: datetime | None = None
    artifact_path: str | None = None


class IssuePattern(BaseModel):
    """Recurring issue pattern identified across reviews."""

    rule_id: str
    scope_path: str
    count: int = 0
    last_seen_sha: str | None = None
    examples_json: str | None = None


class ImplementationSession(BaseModel):
    """Implementation session tracking."""

    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    branch: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    outcome: SessionOutcome


class TestRun(BaseModel):
    """Test execution run."""

    run_id: str
    session_id: str | None = None
    command: str
    status: str  # "PASS" | "FAIL"
    started_at: datetime
    finished_at: datetime
    git_sha: str | None = None
    changed_files_json: str | None = None


class TestResult(BaseModel):
    """Individual test result within a run."""

    run_id: str
    test_nodeid: str
    status: TestStatus
    error_signature: str | None = None
    duration_ms: int | None = None


class ErrorFix(BaseModel):
    """Error-fix mapping linking failing tests to fix files."""

    fix_id: str
    session_id: str
    error_signature: str
    failing_run_id: str | None = None
    passing_run_id: str | None = None
    fixed_files_json: str
    confidence: float


# === Query output models ===


class ImpactedFile(BaseModel):
    """File likely impacted by changes."""

    path: str
    score: float
    reason: str


class RecommendedTest(BaseModel):
    """Test recommended to run for given changes."""

    path: str
    confidence: float


class KnownPitfall(BaseModel):
    """Known pitfall for a directory scope."""

    rule_id: str
    scope: str
    count: int
    example: str | None = None


class ImplementationBrief(BaseModel):
    """Pre-implementation context brief from KB query."""

    likely_impacted_files: list[ImpactedFile] = Field(default_factory=list)
    recommended_tests: list[RecommendedTest] = Field(default_factory=list)
    known_pitfalls: list[KnownPitfall] = Field(default_factory=list)


class TroubleshootResult(BaseModel):
    """Troubleshoot query result for error resolution."""

    likely_fix_files: list[ImpactedFile] = Field(default_factory=list)
    past_fixes: list[dict[str, Any]] = Field(default_factory=list)


class PreCommitCheckResult(BaseModel):
    """Pre-commit co-change check result."""

    missing_co_changes: list[ImpactedFile] = Field(default_factory=list)
    advisory: str = "These files are historically coupled with your staged changes."


__all__ = [
    "Relation",
    "EvidenceSource",
    "TestStatus",
    "SessionOutcome",
    "Severity",
    "FileEdge",
    "EdgeEvidence",
    "Finding",
    "ReviewRun",
    "IssuePattern",
    "ImplementationSession",
    "TestRun",
    "TestResult",
    "ErrorFix",
    "ImpactedFile",
    "RecommendedTest",
    "KnownPitfall",
    "ImplementationBrief",
    "TroubleshootResult",
    "PreCommitCheckResult",
]
