"""Untrusted structured reports produced by workers and verifiers."""

from __future__ import annotations

from typing import Literal

from orchestrator.domain.primitives import (
    ArtifactId,
    CriterionResult,
    EvidenceResult,
    IssueClassification,
    NonEmptyStr,
    ReportId,
    Severity,
    StrictDomainModel,
    UtcTimestamp,
    WorkNodeId,
)
from orchestrator.domain.proposals import SubmissionContext


class ReportContext(StrictDomainModel):
    report_id: ReportId
    submission: SubmissionContext
    reported_at: UtcTimestamp


class WorkReport(StrictDomainModel):
    kind: Literal["work_report"] = "work_report"
    context: ReportContext
    status: Literal["implemented", "blocked", "failed"]
    output_artifact_ids: tuple[ArtifactId, ...] = ()
    checks: tuple[NonEmptyStr, ...] = ()
    risks: tuple[NonEmptyStr, ...] = ()
    issues: tuple[NonEmptyStr, ...] = ()


class VerificationReport(StrictDomainModel):
    kind: Literal["verification_report"] = "verification_report"
    context: ReportContext
    work_node_id: WorkNodeId
    verdict: EvidenceResult
    criterion_results: tuple[CriterionResult, ...]
    summary: NonEmptyStr


class OutcomeEvidence(StrictDomainModel):
    """Outcome verifier claim; acceptance produces authoritative evidence records."""

    kind: Literal["outcome_evidence"] = "outcome_evidence"
    context: ReportContext
    verdict: EvidenceResult
    criterion_results: tuple[CriterionResult, ...]
    summary: NonEmptyStr


class IssueReport(StrictDomainModel):
    kind: Literal["issue_report"] = "issue_report"
    context: ReportContext
    affected_work_node_ids: tuple[WorkNodeId, ...]
    observed_evidence: NonEmptyStr
    expected_result: NonEmptyStr
    actual_result: NonEmptyStr
    proposed_classification: IssueClassification
    severity: Severity
    blocking: bool
