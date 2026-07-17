"""Strict, versioned primitives shared by domain boundary schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _safe_relative_path(value: str) -> str:
    if value.startswith("/") or ".." in value.split("/"):
        raise ValueError("path must be relative and cannot contain parent traversal")
    return value


class StrictDomainModel(BaseModel):
    """Base policy for every authoritative and agent-facing domain schema."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    schema_version: Literal[1] = 1


NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4096),
]
ShortStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
RelativePath = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1024,
    ),
    AfterValidator(_safe_relative_path),
]
UtcTimestamp = Annotated[datetime, AwareDatetime(), AfterValidator(_as_utc)]
SchemaVersion = Literal[1]
RecordVersion = Annotated[int, Field(ge=1)]
DesignVersion = Annotated[int, Field(ge=1)]
PacketVersion = Annotated[int, Field(ge=1)]
ArtifactVersion = Annotated[int, Field(ge=1)]
SequenceNumber = Annotated[int, Field(ge=1)]

TenantId = Annotated[
    str, StringConstraints(pattern=r"^tenant_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
ConversationId = Annotated[
    str, StringConstraints(pattern=r"^conv_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
RunId = Annotated[
    str, StringConstraints(pattern=r"^run_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
CharterId = Annotated[
    str, StringConstraints(pattern=r"^charter_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
DesignRevisionId = Annotated[
    str, StringConstraints(pattern=r"^design_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
WorkNodeId = Annotated[
    str, StringConstraints(pattern=r"^wn_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
TaskId = Annotated[
    str, StringConstraints(pattern=r"^task_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
PacketId = Annotated[
    str, StringConstraints(pattern=r"^pkt_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
ArtifactId = Annotated[
    str, StringConstraints(pattern=r"^art_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
CriterionId = Annotated[
    str, StringConstraints(pattern=r"^criterion_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
EvidenceId = Annotated[
    str, StringConstraints(pattern=r"^evidence_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
IssueId = Annotated[
    str, StringConstraints(pattern=r"^issue_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
ApprovalId = Annotated[
    str, StringConstraints(pattern=r"^approval_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
WorkspaceId = Annotated[
    str, StringConstraints(pattern=r"^workspace_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
CheckpointId = Annotated[
    str, StringConstraints(pattern=r"^checkpoint_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
PromotionId = Annotated[
    str, StringConstraints(pattern=r"^promotion_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
TransitionId = Annotated[
    str, StringConstraints(pattern=r"^transition_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
CompletionId = Annotated[
    str, StringConstraints(pattern=r"^completion_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
ProposalId = Annotated[
    str, StringConstraints(pattern=r"^proposal_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
ReportId = Annotated[
    str, StringConstraints(pattern=r"^report_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
AttemptId = Annotated[
    str, StringConstraints(pattern=r"^attempt_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
EventId = Annotated[
    str, StringConstraints(pattern=r"^evt_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
]
# A correlation ID is deliberately opaque to the domain.  Trace and span IDs
# retain their OpenTelemetry-specific validation below; this ID also links an
# event to a command or cross-service workflow when no trace backend is present.
CorrelationId = ShortStr
ActorId = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:user|agent|service|system)_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    ),
]
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitObjectHash = Annotated[
    str, StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]

ActorKind = Literal["human", "agent", "service", "system"]
AuthorityScope = Literal[
    "charter",
    "design",
    "work_plan",
    "verification",
    "transition",
    "workspace",
    "promotion",
    "completion",
]
RiskClass = Literal["low", "medium", "high", "regulated"]
RunStatus = Literal["created", "running", "paused", "blocked", "completed", "failed"]
ControlStage = Literal[
    "INTAKE",
    "INVESTIGATE",
    "DESIGN",
    "DESIGN_CRITIQUE",
    "PLAN",
    "VALIDATE_PLAN",
    "DISPATCH",
    "EXECUTE",
    "LOCAL_VERIFY",
    "INTEGRATE",
    "OUTCOME_VERIFY",
    "TRIAGE",
    "USER_APPROVAL",
    "RESUME_GATE",
    "COMPLETE",
    "BLOCKED",
]
WorkNodeType = Literal[
    "OUTCOME",
    "SYSTEM",
    "WORK_PACKAGE",
    "LEAF_TASK",
    "INTEGRATION",
    "VERIFICATION",
    "DECISION",
]
WorkNodeStatus = Literal[
    "PROPOSED",
    "DESIGNED",
    "DECOMPOSED",
    "READY",
    "IN_PROGRESS",
    "IMPLEMENTED",
    "LOCALLY_VERIFIED",
    "INTEGRATED",
    "VERIFIED",
    "BLOCKED",
    "CHANGE_REQUESTED",
    "INVALIDATED",
]
EvidenceResult = Literal["passed", "failed", "inconclusive"]
IssueClassification = Literal[
    "LOCAL_DEFECT",
    "INTERFACE_MISMATCH",
    "DESIGN_GAP",
    "REQUIREMENT_GAP",
    "EVIDENCE_GAP",
    "ENVIRONMENT_BLOCKER",
]
Severity = Literal["info", "low", "medium", "high", "critical"]
ApprovalDecision = Literal["approved", "rejected"]
WorkspaceStatus = Literal[
    "selected",
    "imported",
    "ready",
    "active",
    "rolled_back",
    "destroyed",
    "blocked",
]
PromotionStatus = Literal[
    "previewed",
    "confirmed",
    "committed",
    "rejected",
    "failed",
]
TransitionState = (
    RunStatus | ControlStage | WorkNodeStatus | WorkspaceStatus | PromotionStatus
)


class ActorRef(StrictDomainModel):
    """An attributed actor; this reference alone conveys no authority."""

    actor_id: ActorId
    kind: ActorKind
    role: ShortStr

    @model_validator(mode="after")
    def actor_namespace_matches_kind(self) -> Self:
        namespace_to_kind: dict[str, ActorKind] = {
            "user": "human",
            "agent": "agent",
            "service": "service",
            "system": "system",
        }
        namespace = self.actor_id.partition("_")[0]
        if self.kind != namespace_to_kind[namespace]:
            raise ValueError("actor_id namespace must match actor kind")
        return self


class AuthenticatedActor(ActorRef):
    """Identity asserted by an authentication boundary, never by an agent."""

    kind: Literal["human", "service"]
    authenticated_at: UtcTimestamp
    authentication_context: ShortStr


class AgentActor(ActorRef):
    """Agent producer identity permitted on untrusted submissions."""

    kind: Literal["agent"]


# Keep the concrete actor form at persistence and API boundaries.  A plain
# ActorRef remains valid where authentication details are intentionally absent.
ActorReference = AuthenticatedActor | AgentActor | ActorRef


class RecordMetadata(StrictDomainModel):
    """Common optimistic-version and audit metadata for authoritative records."""

    record_version: RecordVersion
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    idempotency_key: IdempotencyKey | None = None
    trace_id: TraceId | None = None

    @model_validator(mode="after")
    def updated_at_is_not_earlier(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be at or after created_at")
        return self


class DesignReference(StrictDomainModel):
    design_version: DesignVersion
    section: ShortStr
    decision_ids: tuple[ShortStr, ...] = ()


class AcceptanceCriterion(StrictDomainModel):
    criterion_id: CriterionId
    description: NonEmptyStr
    evidence_expectation: NonEmptyStr


class CriterionResult(StrictDomainModel):
    criterion_id: CriterionId
    result: EvidenceResult
    summary: NonEmptyStr
    artifact_ids: tuple[ArtifactId, ...] = ()


class ArtifactPointer(StrictDomainModel):
    artifact_id: ArtifactId
    version: ArtifactVersion
    purpose: NonEmptyStr


class AuthorityGrant(StrictDomainModel):
    """Authority derived from an authenticated system source."""

    scope: AuthorityScope
    source: ShortStr
    granted_at: UtcTimestamp
