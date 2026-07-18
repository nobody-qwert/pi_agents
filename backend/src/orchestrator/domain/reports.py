"""Untrusted structured reports produced by workers and verifiers."""

from __future__ import annotations

from typing import Literal

from orchestrator.domain.primitives import (
    ArtifactId,
    CriterionId,
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


class InvestigationReport(StrictDomainModel):
    kind: Literal["investigation_report"] = "investigation_report"
    context: ReportContext
    findings: tuple[NonEmptyStr, ...]
    evidence_artifact_ids: tuple[ArtifactId, ...] = ()
    evidence_gaps: tuple[NonEmptyStr, ...] = ()
    blockers: tuple[NonEmptyStr, ...] = ()


class DesignCritiqueReport(StrictDomainModel):
    kind: Literal["design_critique_report"] = "design_critique_report"
    context: ReportContext
    verdict: Literal["accepted", "revision", "blocked"]
    uncovered_criterion_ids: tuple[CriterionId, ...] = ()
    contradictions: tuple[NonEmptyStr, ...] = ()
    authority_questions: tuple[NonEmptyStr, ...] = ()
    summary: NonEmptyStr


class IntegrationReport(StrictDomainModel):
    kind: Literal["integration_report"] = "integration_report"
    context: ReportContext
    status: Literal["integrated", "issue", "blocked"]
    integrated_artifact_ids: tuple[ArtifactId, ...] = ()
    interfaces_checked: tuple[NonEmptyStr, ...] = ()
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
