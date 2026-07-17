"""Normalized, strict event schemas for durable audit and future streaming."""

from __future__ import annotations

import json
import re
from typing import Literal, cast

from pydantic import JsonValue, field_validator, model_validator

from orchestrator.domain.primitives import (
    AttemptId,
    ControlStage,
    ConversationId,
    CorrelationId,
    DesignVersion,
    EventId,
    IdempotencyKey,
    NonEmptyStr,
    PacketVersion,
    RunId,
    SequenceNumber,
    ShortStr,
    SpanId,
    StrictDomainModel,
    TraceId,
    TransitionId,
    UtcTimestamp,
    WorkNodeId,
)

EventType = Literal[
    "run.created",
    "run.started",
    "run.paused",
    "run.completed",
    "run.blocked",
    "run.failed",
    "stage.entered",
    "stage.exited",
    "agent.started",
    "agent.token",
    "agent.completed",
    "agent.failed",
    "tool.requested",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "validation.started",
    "validation.rejected",
    "validation.accepted",
    "transition.applied",
    "work_node.proposed",
    "work_node.ready",
    "work_node.started",
    "work_node.verified",
    "design.revised",
    "work_node.invalidated",
    "approval.requested",
    "approval.recorded",
    "artifact.created",
    "evidence.recorded",
    "workspace.selected",
    "workspace.imported",
    "workspace.checkpointed",
    "workspace.rolled_back",
    "vm.started",
    "vm.ready",
    "vm.input_owner_changed",
    "vm.destroyed",
    "promotion.previewed",
    "promotion.confirmed",
    "promotion.committed",
    "promotion.rejected",
]
EventStatus = Literal[
    "created",
    "started",
    "running",
    "completed",
    "failed",
    "paused",
    "blocked",
    "accepted",
    "rejected",
    "ready",
    "verified",
]


class EventEnvelope(StrictDomainModel):
    """The durable, replayable audit contract for one domain event."""

    event_id: EventId
    run_id: RunId
    conversation_id: ConversationId
    sequence: SequenceNumber
    occurred_at: UtcTimestamp
    type: EventType
    stage: ControlStage
    node_id: ShortStr
    work_node_id: WorkNodeId | None = None
    attempt_id: AttemptId
    design_version: DesignVersion
    packet_version: PacketVersion
    actor_role: ShortStr
    status: EventStatus
    outcome: EventStatus
    summary: NonEmptyStr
    detail_ref: NonEmptyStr | None = None
    correlation_id: CorrelationId
    trace_id: TraceId
    span_id: SpanId

    @field_validator("detail_ref")
    @classmethod
    def validate_detail_ref(cls, value: str | None) -> str | None:
        return _safe_detail_ref(value)

    @model_validator(mode="after")
    def detail_ref_belongs_to_event(self) -> EventEnvelope:
        _require_detail_ref_owner(self.detail_ref, self.run_id, self.event_id)
        return self


class EventDraft(StrictDomainModel):
    """An event before the durable store assigns its per-run sequence.

    Inline data is deliberately small and redacted.  Rich or sensitive detail
    belongs behind a separately authorized ``detail_ref`` instead of this audit
    projection.
    """

    event_id: EventId
    run_id: RunId
    conversation_id: ConversationId
    occurred_at: UtcTimestamp
    type: EventType
    stage: ControlStage
    node_id: ShortStr
    work_node_id: WorkNodeId | None = None
    attempt_id: AttemptId
    design_version: DesignVersion
    packet_version: PacketVersion
    actor_role: ShortStr
    status: EventStatus
    outcome: EventStatus
    summary: NonEmptyStr
    detail_ref: NonEmptyStr | None = None
    correlation_id: CorrelationId
    trace_id: TraceId
    span_id: SpanId
    command_idempotency_key: IdempotencyKey
    transition_id: TransitionId | None = None
    inline_detail: dict[str, JsonValue] | None = None

    @field_validator("inline_detail")
    @classmethod
    def validate_inline_detail(
        cls, value: dict[str, JsonValue] | None
    ) -> dict[str, JsonValue] | None:
        return _safe_inline_detail(value)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _safe_event_summary(value)

    @field_validator("detail_ref")
    @classmethod
    def validate_detail_ref(cls, value: str | None) -> str | None:
        return _safe_detail_ref(value)

    @model_validator(mode="after")
    def detail_ref_belongs_to_event(self) -> EventDraft:
        _require_detail_ref_owner(self.detail_ref, self.run_id, self.event_id)
        return self

    def envelope(self, sequence: SequenceNumber) -> EventEnvelope:
        """Build the public envelope once sequence allocation has succeeded."""
        return EventEnvelope(
            event_id=self.event_id,
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            sequence=sequence,
            occurred_at=self.occurred_at,
            type=self.type,
            stage=self.stage,
            node_id=self.node_id,
            work_node_id=self.work_node_id,
            attempt_id=self.attempt_id,
            design_version=self.design_version,
            packet_version=self.packet_version,
            actor_role=self.actor_role,
            status=self.status,
            outcome=self.outcome,
            summary=self.summary,
            detail_ref=self.detail_ref,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
        )


class EventDetail(StrictDomainModel):
    """Authorized safe detail associated with one event envelope."""

    event_id: EventId
    detail_ref: NonEmptyStr | None = None
    inline_detail: dict[str, JsonValue] | None = None

    @field_validator("inline_detail")
    @classmethod
    def validate_inline_detail(
        cls, value: dict[str, JsonValue] | None
    ) -> dict[str, JsonValue] | None:
        return _safe_inline_detail(value)

    @field_validator("detail_ref")
    @classmethod
    def validate_detail_ref(cls, value: str | None) -> str | None:
        return _safe_detail_ref(value)


_MAX_INLINE_DETAIL_BYTES = 2048
_MAX_INLINE_DETAIL_FIELDS = 12
_MAX_INLINE_DETAIL_LIST_ITEMS = 16
_MAX_SUMMARY_CHARS = 280
_MAX_DETAIL_REF_CHARS = 384
_SUMMARY_ALLOWED_CHARACTERS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;()/_-]*$")
_DETAIL_REF = re.compile(
    r"^/api/v1/runs/"
    r"(?P<run_id>run_[A-Za-z0-9][A-Za-z0-9_-]{0,127})/events/"
    r"(?P<event_id>evt_[A-Za-z0-9][A-Za-z0-9_-]{0,127})/detail$"
)
_SUMMARY_SENSITIVE_CONTENT = re.compile(
    r"(?:\b(?:api[-_ ]?key|authorization|bearer|credential|cookie|"
    r"password|secret|private[-_ ]?key|client[-_ ]?secret|session[-_ ]?id)\b|"
    r"\b(?:access|refresh|id|api)?[-_ ]?token\s*(?:[:=]|is\s)|"
    r"\bsk-[A-Za-z0-9_-]{8,}\b|\bAKIA[0-9A-Z]{16}\b|"
    r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----)",
    re.IGNORECASE,
)
_SUMMARY_REASONING_CONTENT = re.compile(
    r"(?:\bchain[- ]of[- ]thought\b|\bthought process\b|"
    r"\b(?:internal|private|hidden) (?:analysis|reasoning)\b|"
    r"\b(?:my|the|model|agent) (?:analysis|reasoning)\b|"
    r"\breasoning:\s|\banalysis:\s|"
    r"\bstep[- ]by[- ]step (?:reasoning|analysis)\b|\bscratchpad\b)",
    re.IGNORECASE,
)
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SENSITIVE_IDENTIFIER = re.compile(
    r"(?:^sk[-_]|api[-_]?key|authorization|credential|cookie|password|"
    r"secret|token|reasoning|analysis|session)",
    re.IGNORECASE,
)
_SAFE_STATES = {
    "created",
    "started",
    "running",
    "completed",
    "failed",
    "paused",
    "blocked",
    "accepted",
    "rejected",
    "ready",
    "verified",
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
}
_SAFE_OUTCOMES = {
    "created",
    "started",
    "running",
    "completed",
    "failed",
    "paused",
    "blocked",
    "accepted",
    "rejected",
    "ready",
    "verified",
}


def _safe_inline_detail(
    value: dict[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    """Validate the only detail shape permitted in the durable audit stream.

    The event table is not a general-purpose payload store.  Its inline detail
    is limited to a handful of scalar counters, identifiers, and state/policy
    labels that are useful for replay.  In particular it accepts neither free
    text nor nested objects, so model reasoning, prompts, sessions, credentials,
    and arbitrary tool input/output cannot be retained accidentally.  Rich data
    must use an independently authorized ``detail_ref``.
    """
    if value is None:
        return None
    if len(value) > _MAX_INLINE_DETAIL_FIELDS:
        raise ValueError("inline event detail has too many safe projection fields")

    projected: dict[str, JsonValue] = {}
    for key, item in value.items():
        projected[key] = _safe_inline_detail_value(key, item)

    serialized = json.dumps(projected, separators=(",", ":"), allow_nan=False)
    if len(serialized.encode()) > _MAX_INLINE_DETAIL_BYTES:
        raise ValueError(
            "inline event detail exceeds the safe audit projection limit; "
            "store it behind detail_ref"
        )
    return projected


def _safe_event_summary(value: str) -> str:
    """Allow a short operational audit label, never a transcript or secret.

    Summaries are persisted inside the replayable event envelope and are shown
    without a separate detail authorization check.  Keep them bounded and
    plain-text, and force sensitive or reasoning-bearing content into an
    authorized reference rather than storing it in the audit projection.
    """
    if len(value) > _MAX_SUMMARY_CHARS:
        raise ValueError("event summary exceeds the 280-character audit limit")
    if _SUMMARY_SENSITIVE_CONTENT.search(value):
        raise ValueError("event summary contains secret-bearing content")
    if _SUMMARY_REASONING_CONTENT.search(value):
        raise ValueError("event summary cannot contain raw reasoning")
    if not _SUMMARY_ALLOWED_CHARACTERS.fullmatch(value):
        raise ValueError("event summary must be a plain operational audit label")
    return value


def _safe_detail_ref(value: str | None) -> str | None:
    """Allow only the bounded, run-scoped detail endpoint reference.

    Event rows are a replayable audit projection, so a reference is not a
    generic URI or a free-form storage locator.  The only permitted form names
    the separately authorized event-detail endpoint using already-safe IDs.
    """
    if value is None:
        return None
    if len(value) > _MAX_DETAIL_REF_CHARS:
        raise ValueError("detail_ref exceeds the bounded safe reference limit")
    if _DETAIL_REF.fullmatch(value) is None:
        raise ValueError(
            "detail_ref must be the canonical safe run event-detail reference"
        )
    return value


def _require_detail_ref_owner(
    detail_ref: str | None, run_id: str, event_id: str
) -> None:
    if detail_ref is None:
        return
    match = _DETAIL_REF.fullmatch(detail_ref)
    if match is None:  # The field validator supplies the user-facing error.
        return
    if match["run_id"] != run_id or match["event_id"] != event_id:
        raise ValueError("detail_ref must belong to the event's run and event ID")


def _safe_inline_detail_value(key: str, value: JsonValue) -> JsonValue:
    if key in {"token_count", "duration_ms", "retry_count"}:
        if type(value) is not int or value < 0 or value > 1_000_000_000:
            raise ValueError(
                f"inline detail {key!r} must be a bounded non-negative integer"
            )
        return value
    if key == "exit_code":
        if type(value) is not int or value < 0 or value > 255:
            raise ValueError(
                "inline detail 'exit_code' must be an integer from 0 through 255"
            )
        return value
    if key in {"record_version", "artifact_version"}:
        if type(value) is not int or value < 1 or value > 1_000_000_000:
            raise ValueError(
                f"inline detail {key!r} must be a positive bounded integer"
            )
        return value
    if key in {"outcome", "validation_status"}:
        if not isinstance(value, str) or value not in _SAFE_OUTCOMES:
            raise ValueError(f"inline detail {key!r} must be a known event outcome")
        return value
    if key in {"previous_state", "next_state"}:
        if not isinstance(value, str) or value not in _SAFE_STATES:
            raise ValueError(f"inline detail {key!r} must be a known lifecycle state")
        return value
    if key == "permission_decision":
        if not isinstance(value, str) or value not in {"allowed", "denied"}:
            raise ValueError(
                "inline detail 'permission_decision' must be allowed or denied"
            )
        return value
    if key in {"policy_rule_ids", "rejected_field_names"}:
        if not isinstance(value, list) or len(value) > _MAX_INLINE_DETAIL_LIST_ITEMS:
            raise ValueError(
                f"inline detail {key!r} must be a short list of safe identifiers"
            )
        if not all(
            isinstance(item, str)
            and _SAFE_IDENTIFIER.fullmatch(item)
            and not _SENSITIVE_IDENTIFIER.search(item)
            for item in value
        ):
            raise ValueError(
                f"inline detail {key!r} must contain only safe identifiers"
            )
        return cast(JsonValue, value)
    raise ValueError(
        f"inline detail field {key!r} is not in the durable safe projection; "
        "use detail_ref for authorized rich detail"
    )
