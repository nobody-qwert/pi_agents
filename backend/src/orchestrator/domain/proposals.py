"""Untrusted agent proposals that require deterministic acceptance."""

from __future__ import annotations

from typing import Literal

from orchestrator.domain.primitives import (
    AcceptanceCriterion,
    AgentActor,
    ApprovalDecision,
    ArtifactId,
    AttemptId,
    AuthorityScope,
    CriterionId,
    DesignReference,
    DesignVersion,
    EvidenceResult,
    LongText,
    NonEmptyStr,
    ProposalId,
    RelativePath,
    RiskClass,
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


class LeafReadinessClaim(StrictDomainModel):
    """Explicit planner claims for leaf criteria that cannot be inferred safely."""

    observable_outcome: bool
    single_responsibility: bool
    inputs_explicit: bool
    outputs_explicit: bool
    design_rules_and_interfaces_cited: bool
    single_verification_boundary: bool
    failure_isolated: bool
    context_fits: bool
    no_unapproved_decisions: bool


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
    output_consumer_ids: tuple[WorkNodeId, ...] = ()
    interfaces: tuple[NonEmptyStr, ...] = ()
    produces_interfaces: tuple[ShortStr, ...] = ()
    consumes_interfaces: tuple[ShortStr, ...] = ()
    acceptance_criterion_ids: tuple[CriterionId, ...]
    expected_touch_points: tuple[RelativePath, ...] = ()
    non_blocking_dependencies: tuple[WorkNodeId, ...] = ()
    leaf_readiness: LeafReadinessClaim | None = None


class ProposedWorkPlan(StrictDomainModel):
    kind: Literal["proposed_work_plan"] = "proposed_work_plan"
    context: SubmissionContext
    root_work_node_id: WorkNodeId
    nodes: tuple[WorkNodeProposal, ...]


class CharterProposal(StrictDomainModel):
    """Untrusted intake draft accepted only by the charter application service."""

    kind: Literal["charter_proposal"] = "charter_proposal"
    context: SubmissionContext
    requested_outcome: NonEmptyStr
    intended_users: tuple[ShortStr, ...]
    included_scope: tuple[NonEmptyStr, ...]
    excluded_scope: tuple[NonEmptyStr, ...]
    assumptions: tuple[NonEmptyStr, ...] = ()
    constraints: tuple[NonEmptyStr, ...] = ()
    protected_artifacts: tuple[RelativePath, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    authority_questions: tuple[NonEmptyStr, ...] = ()
    required_approvals: tuple[AuthorityScope, ...] = ()
    risk_class: RiskClass
    evidence_expectations: tuple[NonEmptyStr, ...]


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
    design_content: LongText
    summary: NonEmptyStr
