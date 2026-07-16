"""Untrusted agent proposals that require deterministic acceptance."""

from __future__ import annotations

from typing import Literal

from orchestrator.domain.primitives import (
    AgentActor,
    ApprovalDecision,
    ArtifactId,
    AttemptId,
    AuthorityScope,
    CriterionId,
    DesignReference,
    DesignVersion,
    EvidenceResult,
    NonEmptyStr,
    ProposalId,
    RunId,
    ShortStr,
    StrictDomainModel,
    TransitionState,
    UtcTimestamp,
    WorkNodeId,
    WorkNodeType,
)


class SubmissionContext(StrictDomainModel):
    """Attribution for untrusted content; deliberately contains no authority."""

    proposal_id: ProposalId
    run_id: RunId
    work_node_id: WorkNodeId | None = None
    attempt_id: AttemptId
    submitted_at: UtcTimestamp
    producer: AgentActor
    design_version: DesignVersion


class WorkNodeProposal(StrictDomainModel):
    work_node_id: WorkNodeId
    parent_id: WorkNodeId | None = None
    node_type: WorkNodeType
    goal: NonEmptyStr
    owner_role: ShortStr
    design_refs: tuple[DesignReference, ...]
    depends_on: tuple[WorkNodeId, ...] = ()
    inputs: tuple[ArtifactId, ...] = ()
    expected_outputs: tuple[ShortStr, ...]
    interfaces: tuple[NonEmptyStr, ...] = ()
    acceptance_criterion_ids: tuple[CriterionId, ...]


class ProposedWorkPlan(StrictDomainModel):
    kind: Literal["proposed_work_plan"] = "proposed_work_plan"
    context: SubmissionContext
    root_work_node_id: WorkNodeId
    nodes: tuple[WorkNodeProposal, ...]


class ApprovalProposal(StrictDomainModel):
    """An agent recommendation, never an authenticated approval decision."""

    kind: Literal["approval_proposal"] = "approval_proposal"
    context: SubmissionContext
    requested_scope: AuthorityScope
    recommendation: ApprovalDecision
    rationale: NonEmptyStr


class EvidenceProposal(StrictDomainModel):
    kind: Literal["evidence_proposal"] = "evidence_proposal"
    context: SubmissionContext
    criterion_id: CriterionId
    claimed_result: EvidenceResult
    artifact_ids: tuple[ArtifactId, ...] = ()
    summary: NonEmptyStr


class TransitionProposal(StrictDomainModel):
    kind: Literal["transition_proposal"] = "transition_proposal"
    context: SubmissionContext
    requested_next_state: TransitionState
    rationale: NonEmptyStr


class CompletionProposal(StrictDomainModel):
    kind: Literal["completion_proposal"] = "completion_proposal"
    context: SubmissionContext
    claimed_criterion_ids: tuple[CriterionId, ...]
    summary: NonEmptyStr


class DesignProposal(StrictDomainModel):
    kind: Literal["design_proposal"] = "design_proposal"
    context: SubmissionContext
    proposed_design_version: DesignVersion
    design_artifact_id: ArtifactId
    summary: NonEmptyStr
