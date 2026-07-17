"""Authoritative domain records written only by deterministic services."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from orchestrator.domain.primitives import (
    AcceptanceCriterion,
    ActorReference,
    ApprovalDecision,
    ApprovalId,
    ArtifactId,
    ArtifactPointer,
    ArtifactVersion,
    AuthenticatedActor,
    AuthorityGrant,
    AuthorityScope,
    CharterId,
    CheckpointId,
    CompletionId,
    ControlStage,
    CriterionId,
    DesignReference,
    DesignRevisionId,
    DesignVersion,
    EvidenceId,
    EvidenceResult,
    GitObjectHash,
    IssueClassification,
    IssueId,
    NonEmptyStr,
    PacketId,
    PromotionId,
    PromotionStatus,
    RecordMetadata,
    RelativePath,
    RiskClass,
    RunId,
    RunStatus,
    Severity,
    Sha256Digest,
    ShortStr,
    StrictDomainModel,
    TaskId,
    TenantId,
    TransitionId,
    TransitionState,
    UtcTimestamp,
    WorkNodeId,
    WorkNodeStatus,
    WorkNodeType,
    WorkspaceId,
    WorkspaceStatus,
)


class AuthoritativeRecord(StrictDomainModel):
    """Marker base for accepted state; agent submissions never inherit it."""

    metadata: RecordMetadata


class RunRecord(AuthoritativeRecord):
    kind: Literal["run_record"] = "run_record"
    run_id: RunId
    tenant_id: TenantId
    outcome: NonEmptyStr
    current_gate: ControlStage
    risk_class: RiskClass
    status: RunStatus


class CharterRecord(AuthoritativeRecord):
    kind: Literal["charter_record"] = "charter_record"
    charter_id: CharterId
    run_id: RunId
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
    accepted_by: AuthenticatedActor


class DesignDecision(StrictDomainModel):
    decision_id: ShortStr
    question: NonEmptyStr
    decision_owner: AuthenticatedActor
    chosen_option: NonEmptyStr
    rejected_alternatives: tuple[NonEmptyStr, ...] = ()
    rationale: NonEmptyStr
    affected_sections: tuple[ShortStr, ...]
    affected_work_node_ids: tuple[WorkNodeId, ...] = ()


class DesignRevision(AuthoritativeRecord):
    kind: Literal["design_revision"] = "design_revision"
    design_revision_id: DesignRevisionId
    run_id: RunId
    design_version: DesignVersion
    design_artifact_id: ArtifactId
    decisions: tuple[DesignDecision, ...] = ()
    accepted_by: AuthenticatedActor


class WorkNodeRecord(AuthoritativeRecord):
    kind: Literal["work_node_record"] = "work_node_record"
    work_node_id: WorkNodeId
    run_id: RunId
    parent_id: WorkNodeId | None = None
    node_type: WorkNodeType
    goal: NonEmptyStr
    owner_role: ShortStr
    status: WorkNodeStatus
    design_refs: tuple[DesignReference, ...]
    depends_on: tuple[WorkNodeId, ...] = ()
    inputs: tuple[ArtifactPointer, ...] = ()
    outputs: tuple[ShortStr, ...]
    interfaces: tuple[NonEmptyStr, ...] = ()
    acceptance_criterion_ids: tuple[CriterionId, ...]
    child_ids: tuple[WorkNodeId, ...] = ()


class AcceptanceCheck(StrictDomainModel):
    method: Literal["command", "inspection", "cross-check", "human approval"]
    procedure: NonEmptyStr
    evidence: NonEmptyStr


class PacketAcceptanceCriterion(StrictDomainModel):
    """Worker-facing acceptance statement pinned into an immutable packet."""

    criterion_id: CriterionId
    observable_result: NonEmptyStr


class OutputArtifactSpecification(StrictDomainModel):
    """Identity and required form of an artifact a worker must produce."""

    artifact_id: ArtifactId | None = None
    path: RelativePath | None = None
    required_form: NonEmptyStr

    @model_validator(mode="after")
    def has_artifact_identity(self) -> OutputArtifactSpecification:
        if self.artifact_id is None and self.path is None:
            raise ValueError("an output artifact requires an artifact_id or path")
        return self


class IssueContract(StrictDomainModel):
    """Bounded instructions for reporting defects without redesigning in place."""

    report_evidence: NonEmptyStr
    proposed_classifications: tuple[IssueClassification, ...] = Field(min_length=1)
    redesign_in_place_allowed: Literal[False] = False


class OutputContract(StrictDomainModel):
    """Required contents of the worker's result handoff."""

    status: NonEmptyStr
    outputs: NonEmptyStr
    checks: NonEmptyStr
    risks: NonEmptyStr
    issues: NonEmptyStr
    design_version_used: DesignVersion


class PacketRecord(AuthoritativeRecord):
    kind: Literal["packet_record"] = "packet_record"
    packet_id: PacketId
    run_id: RunId
    task_id: TaskId
    work_node_id: WorkNodeId
    task_type: WorkNodeType
    goal: NonEmptyStr
    design_baseline: tuple[DesignReference, ...]
    acceptance_criteria: tuple[PacketAcceptanceCriterion, ...] = Field(min_length=1)
    input_artifacts: tuple[ArtifactPointer, ...] = ()
    output_artifacts: tuple[OutputArtifactSpecification, ...] = Field(min_length=1)
    interfaces: tuple[NonEmptyStr, ...] = ()
    starting_points: tuple[NonEmptyStr, ...] = ()
    depends_on: tuple[WorkNodeId, ...] = ()
    expected_touch_points: tuple[RelativePath, ...] = ()
    protected_touch_points: tuple[RelativePath, ...] = ()
    acceptance_checks: tuple[AcceptanceCheck, ...]
    authority_limits: tuple[NonEmptyStr, ...]
    known_facts: tuple[NonEmptyStr, ...] = ()
    known_failed_approaches: tuple[NonEmptyStr, ...] = ()
    issue_contract: IssueContract
    output_contract: OutputContract


class ArtifactRecord(AuthoritativeRecord):
    kind: Literal["artifact_record"] = "artifact_record"
    artifact_id: ArtifactId
    run_id: RunId
    work_node_id: WorkNodeId | None = None
    logical_name: ShortStr
    version: ArtifactVersion
    media_type: ShortStr
    storage_locator: NonEmptyStr
    sha256: Sha256Digest
    producer: ActorReference
    access_policy: tuple[ShortStr, ...]


class EvidenceRecord(AuthoritativeRecord):
    kind: Literal["evidence_record"] = "evidence_record"
    evidence_id: EvidenceId
    run_id: RunId
    work_node_id: WorkNodeId
    criterion_id: CriterionId
    result: EvidenceResult
    summary: NonEmptyStr
    supporting_artifacts: tuple[ArtifactPointer, ...] = ()
    verifier: ActorReference
    design_version: DesignVersion


class IssueRecord(AuthoritativeRecord):
    kind: Literal["issue_record"] = "issue_record"
    issue_id: IssueId
    run_id: RunId
    reporter: ActorReference
    affected_work_node_ids: tuple[WorkNodeId, ...]
    affected_artifact_ids: tuple[ArtifactId, ...] = ()
    observed_evidence: NonEmptyStr
    expected_result: NonEmptyStr
    actual_result: NonEmptyStr
    classification: IssueClassification
    severity: Severity
    blocking: bool
    design_version: DesignVersion
    routing_outcome: NonEmptyStr | None = None


class ApprovalRecord(AuthoritativeRecord):
    kind: Literal["approval_record"] = "approval_record"
    approval_id: ApprovalId
    run_id: RunId
    approver: AuthenticatedActor
    authority: AuthorityGrant
    decision: ApprovalDecision
    decided_at: UtcTimestamp
    affected_record_version: int = Field(ge=1)
    comment: NonEmptyStr | None = None


class WorkspaceRecord(AuthoritativeRecord):
    kind: Literal["workspace_record"] = "workspace_record"
    workspace_id: WorkspaceId
    run_id: RunId
    selected_source: NonEmptyStr
    source_fingerprint: Sha256Digest
    guest_identity: ShortStr
    guest_path: RelativePath
    status: WorkspaceStatus
    input_owner: ActorReference | None = None


class CheckpointRecord(AuthoritativeRecord):
    kind: Literal["checkpoint_record"] = "checkpoint_record"
    checkpoint_id: CheckpointId
    workspace_id: WorkspaceId
    run_id: RunId
    work_node_id: WorkNodeId
    commit_hash: GitObjectHash
    tree_hash: GitObjectHash
    accepted_evidence_ids: tuple[EvidenceId, ...]
    parent_checkpoint_id: CheckpointId | None = None
    rollback_from_checkpoint_id: CheckpointId | None = None
    recorded_by: ActorReference


class PromotionRecord(AuthoritativeRecord):
    kind: Literal["promotion_record"] = "promotion_record"
    promotion_id: PromotionId
    run_id: RunId
    workspace_id: WorkspaceId
    preview_artifact_id: ArtifactId
    confirmed_artifact_version: ArtifactVersion
    target_branch: ShortStr
    target_commit: GitObjectHash | None = None
    target_tag: ShortStr | None = None
    status: PromotionStatus
    decided_by: AuthenticatedActor
    authority: AuthorityGrant
    check_evidence_ids: tuple[EvidenceId, ...] = ()
    result_summary: NonEmptyStr


class TransitionRecord(AuthoritativeRecord):
    """Audited accepted transition; this module does not decide transitions."""

    kind: Literal["transition_record"] = "transition_record"
    transition_id: TransitionId
    run_id: RunId
    work_node_id: WorkNodeId | None = None
    previous_state: TransitionState
    next_state: TransitionState
    reason: NonEmptyStr
    actor: ActorReference
    previous_record_version: int = Field(ge=1)
    next_record_version: int = Field(ge=2)


class RunCompletionRecord(AuthoritativeRecord):
    kind: Literal["run_completion_record"] = "run_completion_record"
    completion_id: CompletionId
    run_id: RunId
    outcome_evidence_ids: tuple[EvidenceId, ...]
    completed_at: UtcTimestamp
    completed_by: ActorReference
    authority: AuthorityGrant
    summary: NonEmptyStr
