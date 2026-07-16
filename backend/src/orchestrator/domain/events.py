"""Normalized, strict event envelope shared by durable audit and streaming boundaries."""

from __future__ import annotations

from typing import Literal

from orchestrator.domain.primitives import (
    AttemptId,
    ControlStage,
    ConversationId,
    EventId,
    NonEmptyStr,
    RunId,
    SequenceNumber,
    ShortStr,
    SpanId,
    StrictDomainModel,
    TraceId,
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
    event_id: EventId
    run_id: RunId
    conversation_id: ConversationId
    sequence: SequenceNumber
    occurred_at: UtcTimestamp
    type: EventType
    stage: ControlStage
    node_id: ShortStr
    work_node_id: WorkNodeId | None = None
    attempt_id: AttemptId | None = None
    status: EventStatus
    summary: NonEmptyStr
    detail_ref: NonEmptyStr | None = None
    trace_id: TraceId
    span_id: SpanId
