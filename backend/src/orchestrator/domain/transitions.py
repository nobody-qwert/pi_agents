"""Pure transition policy for authoritative run gates and work-node states."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from pydantic import model_validator

from orchestrator.domain.authoritative import TransitionRecord
from orchestrator.domain.primitives import (
    ActorRef,
    AuthenticatedActor,
    ControlStage,
    IdempotencyKey,
    NonEmptyStr,
    RecordMetadata,
    RecordVersion,
    RunId,
    StrictDomainModel,
    TraceId,
    TransitionId,
    TransitionState,
    UtcTimestamp,
    WorkNodeId,
    WorkNodeStatus,
)

RUN_GATE_TRANSITIONS: Final[Mapping[ControlStage, frozenset[ControlStage]]] = (
    MappingProxyType(
        {
            "INTAKE": frozenset({"INVESTIGATE"}),
            "INVESTIGATE": frozenset({"DESIGN"}),
            "DESIGN": frozenset({"DESIGN_CRITIQUE"}),
            "DESIGN_CRITIQUE": frozenset({"DESIGN", "PLAN"}),
            "PLAN": frozenset({"VALIDATE_PLAN"}),
            "VALIDATE_PLAN": frozenset({"DISPATCH", "TRIAGE"}),
            "DISPATCH": frozenset({"EXECUTE"}),
            "EXECUTE": frozenset({"LOCAL_VERIFY"}),
            "LOCAL_VERIFY": frozenset({"INTEGRATE", "TRIAGE"}),
            "INTEGRATE": frozenset({"OUTCOME_VERIFY", "TRIAGE"}),
            "OUTCOME_VERIFY": frozenset({"COMPLETE", "TRIAGE"}),
            "TRIAGE": frozenset({"DISPATCH", "DESIGN", "USER_APPROVAL", "BLOCKED"}),
            "USER_APPROVAL": frozenset({"RESUME_GATE", "BLOCKED"}),
            "RESUME_GATE": frozenset({"DISPATCH"}),
            "COMPLETE": frozenset(),
            "BLOCKED": frozenset(),
        }
    )
)
"""Allowed fixed-control-graph edges. Missing edges are denied."""


_WORK_INTERRUPTION_STATES: Final[frozenset[WorkNodeStatus]] = frozenset(
    {"BLOCKED", "CHANGE_REQUESTED", "INVALIDATED"}
)


def _work_states(*states: WorkNodeStatus) -> frozenset[WorkNodeStatus]:
    return frozenset(states)


WORK_NODE_TRANSITIONS: Final[Mapping[WorkNodeStatus, frozenset[WorkNodeStatus]]] = (
    MappingProxyType(
        {
            "PROPOSED": _work_states("DESIGNED") | _WORK_INTERRUPTION_STATES,
            "DESIGNED": _work_states("DECOMPOSED", "READY") | _WORK_INTERRUPTION_STATES,
            "DECOMPOSED": _WORK_INTERRUPTION_STATES,
            "READY": _work_states("IN_PROGRESS") | _WORK_INTERRUPTION_STATES,
            "IN_PROGRESS": _work_states("IMPLEMENTED") | _WORK_INTERRUPTION_STATES,
            "IMPLEMENTED": _work_states("LOCALLY_VERIFIED") | _WORK_INTERRUPTION_STATES,
            "LOCALLY_VERIFIED": _work_states("INTEGRATED") | _WORK_INTERRUPTION_STATES,
            "INTEGRATED": _work_states("VERIFIED") | _WORK_INTERRUPTION_STATES,
            "VERIFIED": frozenset(),
            "BLOCKED": frozenset(),
            "CHANGE_REQUESTED": frozenset(),
            "INVALIDATED": frozenset(),
        }
    )
)
"""Allowed work lifecycle edges. Resumption and repair policy are not implicit."""


TransitionAggregate = Literal["run_gate", "work_node"]
TransitionRejectionCode = Literal[
    "target_mismatch",
    "idempotency_conflict",
    "stale_version",
    "invalid_actor",
    "forbidden_edge",
]
TransitionAuditOutcome = Literal["accepted", "replayed", "rejected"]
TransitionAuthority = Literal["transition", "verification", "completion"]

_ASSURANCE_ONLY_WORK_EDGES: Final[frozenset[tuple[WorkNodeStatus, WorkNodeStatus]]] = (
    frozenset(
        {
            ("IMPLEMENTED", "LOCALLY_VERIFIED"),
            ("INTEGRATED", "VERIFIED"),
        }
    )
)
_COMPLETION_RUN_EDGE: Final[tuple[ControlStage, ControlStage]] = (
    "OUTCOME_VERIFY",
    "COMPLETE",
)
_SERVICE_AUTHORITIES: Final[
    Mapping[tuple[str, str], frozenset[TransitionAuthority]]
] = MappingProxyType(
    {
        ("service_transition", "transition-service"): frozenset({"transition"}),
        ("service_assurance", "assurance"): frozenset({"verification", "completion"}),
    }
)
"""Closed authority policy for deterministic service identities and roles."""


class RunGateState(StrictDomainModel):
    """Current authoritative run-gate state supplied by a repository boundary."""

    run_id: RunId
    gate: ControlStage
    record_version: RecordVersion


class WorkNodeState(StrictDomainModel):
    """Current authoritative work-node state supplied by a repository boundary."""

    run_id: RunId
    work_node_id: WorkNodeId
    status: WorkNodeStatus
    record_version: RecordVersion


class RunGateTransitionDecision(StrictDomainModel):
    """Validated deterministic request to move a run through the fixed graph."""

    transition_id: TransitionId
    run_id: RunId
    requested_gate: ControlStage
    reason: NonEmptyStr
    actor: ActorRef
    expected_record_version: RecordVersion
    idempotency_key: IdempotencyKey
    decided_at: UtcTimestamp
    trace_id: TraceId | None = None


class WorkNodeTransitionDecision(StrictDomainModel):
    """Validated deterministic request to move one approved work node."""

    transition_id: TransitionId
    run_id: RunId
    work_node_id: WorkNodeId
    requested_status: WorkNodeStatus
    reason: NonEmptyStr
    actor: ActorRef
    expected_record_version: RecordVersion
    idempotency_key: IdempotencyKey
    decided_at: UtcTimestamp
    trace_id: TraceId | None = None


class TransitionAuditPayload(StrictDomainModel):
    """Persistence-neutral audit facts returned for every policy decision."""

    aggregate: TransitionAggregate
    run_id: RunId
    work_node_id: WorkNodeId | None = None
    idempotency_key: IdempotencyKey
    transition_id: TransitionId
    previous_state: TransitionState
    requested_state: TransitionState
    current_record_version: RecordVersion
    expected_record_version: RecordVersion
    actor: ActorRef
    reason: NonEmptyStr
    decided_at: UtcTimestamp
    trace_id: TraceId | None = None
    outcome: TransitionAuditOutcome
    rejection_code: TransitionRejectionCode | None = None

    @model_validator(mode="after")
    def aggregate_and_outcome_are_consistent(self) -> TransitionAuditPayload:
        if (self.aggregate == "work_node") != (self.work_node_id is not None):
            raise ValueError("work-node audit payloads require a work_node_id")
        if (self.outcome == "rejected") != (self.rejection_code is not None):
            raise ValueError("only rejected audits carry a rejection code")
        return self


class AcceptedTransition(StrictDomainModel):
    """A newly accepted transition or the safe replay of an accepted command."""

    accepted: Literal[True] = True
    replayed: bool
    transition: TransitionRecord
    audit: TransitionAuditPayload

    @model_validator(mode="after")
    def replay_flag_matches_audit(self) -> AcceptedTransition:
        expected = "replayed" if self.replayed else "accepted"
        if self.audit.outcome != expected:
            raise ValueError("replay flag must match the audit outcome")
        return self


class RejectedTransition(StrictDomainModel):
    """A denied transition with a stable machine-readable reason."""

    accepted: Literal[False] = False
    code: TransitionRejectionCode
    message: NonEmptyStr
    audit: TransitionAuditPayload

    @model_validator(mode="after")
    def code_matches_audit(self) -> RejectedTransition:
        if self.audit.outcome != "rejected" or self.audit.rejection_code != self.code:
            raise ValueError("rejection result and audit code must match")
        return self


TransitionResult = AcceptedTransition | RejectedTransition


def decide_run_gate_transition(
    current: RunGateState,
    decision: RunGateTransitionDecision,
    prior_transition: TransitionRecord | None = None,
) -> TransitionResult:
    """Apply the run-gate table, optimistic version, actor, and replay guards."""

    return _decide_transition(
        aggregate="run_gate",
        current_run_id=current.run_id,
        current_work_node_id=None,
        current_state=current.gate,
        current_record_version=current.record_version,
        decision_run_id=decision.run_id,
        decision_work_node_id=None,
        requested_state=decision.requested_gate,
        transition_id=decision.transition_id,
        reason=decision.reason,
        actor=decision.actor,
        expected_record_version=decision.expected_record_version,
        idempotency_key=decision.idempotency_key,
        decided_at=decision.decided_at,
        trace_id=decision.trace_id,
        allowed_next_states=RUN_GATE_TRANSITIONS[current.gate],
        prior_transition=prior_transition,
    )


def decide_work_node_transition(
    current: WorkNodeState,
    decision: WorkNodeTransitionDecision,
    prior_transition: TransitionRecord | None = None,
) -> TransitionResult:
    """Apply the work-node table, optimistic version, actor, and replay guards."""

    return _decide_transition(
        aggregate="work_node",
        current_run_id=current.run_id,
        current_work_node_id=current.work_node_id,
        current_state=current.status,
        current_record_version=current.record_version,
        decision_run_id=decision.run_id,
        decision_work_node_id=decision.work_node_id,
        requested_state=decision.requested_status,
        transition_id=decision.transition_id,
        reason=decision.reason,
        actor=decision.actor,
        expected_record_version=decision.expected_record_version,
        idempotency_key=decision.idempotency_key,
        decided_at=decision.decided_at,
        trace_id=decision.trace_id,
        allowed_next_states=WORK_NODE_TRANSITIONS[current.status],
        prior_transition=prior_transition,
    )


def _decide_transition(
    *,
    aggregate: TransitionAggregate,
    current_run_id: RunId,
    current_work_node_id: WorkNodeId | None,
    current_state: TransitionState,
    current_record_version: RecordVersion,
    decision_run_id: RunId,
    decision_work_node_id: WorkNodeId | None,
    requested_state: TransitionState,
    transition_id: TransitionId,
    reason: NonEmptyStr,
    actor: ActorRef,
    expected_record_version: RecordVersion,
    idempotency_key: IdempotencyKey,
    decided_at: UtcTimestamp,
    trace_id: TraceId | None,
    allowed_next_states: frozenset[ControlStage] | frozenset[WorkNodeStatus],
    prior_transition: TransitionRecord | None,
) -> TransitionResult:
    if (
        current_run_id != decision_run_id
        or current_work_node_id != decision_work_node_id
    ):
        return _reject(
            code="target_mismatch",
            message="decision target does not match current state",
            aggregate=aggregate,
            current_run_id=current_run_id,
            current_work_node_id=current_work_node_id,
            current_state=current_state,
            current_record_version=current_record_version,
            requested_state=requested_state,
            transition_id=transition_id,
            reason=reason,
            actor=actor,
            expected_record_version=expected_record_version,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            trace_id=trace_id,
        )

    if prior_transition is not None:
        if _matches_prior_transition(
            prior=prior_transition,
            run_id=decision_run_id,
            work_node_id=decision_work_node_id,
            requested_state=requested_state,
            transition_id=transition_id,
            reason=reason,
            actor=actor,
            expected_record_version=expected_record_version,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            trace_id=trace_id,
        ):
            return AcceptedTransition(
                replayed=True,
                transition=prior_transition,
                audit=_audit(
                    aggregate=aggregate,
                    run_id=decision_run_id,
                    work_node_id=decision_work_node_id,
                    idempotency_key=idempotency_key,
                    transition_id=transition_id,
                    previous_state=prior_transition.previous_state,
                    requested_state=requested_state,
                    current_record_version=current_record_version,
                    expected_record_version=expected_record_version,
                    actor=actor,
                    reason=reason,
                    decided_at=decided_at,
                    trace_id=trace_id,
                    outcome="replayed",
                ),
            )
        return _reject(
            code="idempotency_conflict",
            message="idempotency key was already used for a different command",
            aggregate=aggregate,
            current_run_id=current_run_id,
            current_work_node_id=current_work_node_id,
            current_state=current_state,
            current_record_version=current_record_version,
            requested_state=requested_state,
            transition_id=transition_id,
            reason=reason,
            actor=actor,
            expected_record_version=expected_record_version,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            trace_id=trace_id,
        )

    if current_record_version != expected_record_version:
        return _reject(
            code="stale_version",
            message="expected record version does not match current version",
            aggregate=aggregate,
            current_run_id=current_run_id,
            current_work_node_id=current_work_node_id,
            current_state=current_state,
            current_record_version=current_record_version,
            requested_state=requested_state,
            transition_id=transition_id,
            reason=reason,
            actor=actor,
            expected_record_version=expected_record_version,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            trace_id=trace_id,
        )

    required_authority = _required_authority(
        aggregate=aggregate,
        current_state=current_state,
        requested_state=requested_state,
    )
    if not _actor_has_authority(actor, required_authority):
        return _reject(
            code="invalid_actor",
            message=f"actor is not authorized for {required_authority} transitions",
            aggregate=aggregate,
            current_run_id=current_run_id,
            current_work_node_id=current_work_node_id,
            current_state=current_state,
            current_record_version=current_record_version,
            requested_state=requested_state,
            transition_id=transition_id,
            reason=reason,
            actor=actor,
            expected_record_version=expected_record_version,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            trace_id=trace_id,
        )

    if requested_state not in allowed_next_states:
        return _reject(
            code="forbidden_edge",
            message="requested edge is not present in the transition table",
            aggregate=aggregate,
            current_run_id=current_run_id,
            current_work_node_id=current_work_node_id,
            current_state=current_state,
            current_record_version=current_record_version,
            requested_state=requested_state,
            transition_id=transition_id,
            reason=reason,
            actor=actor,
            expected_record_version=expected_record_version,
            idempotency_key=idempotency_key,
            decided_at=decided_at,
            trace_id=trace_id,
        )

    next_record_version = expected_record_version + 1
    transition = TransitionRecord(
        metadata=RecordMetadata(
            record_version=next_record_version,
            created_at=decided_at,
            updated_at=decided_at,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        ),
        transition_id=transition_id,
        run_id=decision_run_id,
        work_node_id=decision_work_node_id,
        previous_state=current_state,
        next_state=requested_state,
        reason=reason,
        actor=actor,
        previous_record_version=expected_record_version,
        next_record_version=next_record_version,
    )
    return AcceptedTransition(
        replayed=False,
        transition=transition,
        audit=_audit(
            aggregate=aggregate,
            run_id=decision_run_id,
            work_node_id=decision_work_node_id,
            idempotency_key=idempotency_key,
            transition_id=transition_id,
            previous_state=current_state,
            requested_state=requested_state,
            current_record_version=current_record_version,
            expected_record_version=expected_record_version,
            actor=actor,
            reason=reason,
            decided_at=decided_at,
            trace_id=trace_id,
            outcome="accepted",
        ),
    )


def _required_authority(
    *,
    aggregate: TransitionAggregate,
    current_state: TransitionState,
    requested_state: TransitionState,
) -> TransitionAuthority:
    if (
        aggregate == "work_node"
        and (
            current_state,
            requested_state,
        )
        in _ASSURANCE_ONLY_WORK_EDGES
    ):
        return "verification"
    if (
        aggregate == "run_gate"
        and (
            current_state,
            requested_state,
        )
        == _COMPLETION_RUN_EDGE
    ):
        return "completion"
    return "transition"


def _actor_has_authority(
    actor: ActorRef, required_authority: TransitionAuthority
) -> bool:
    if not isinstance(actor, AuthenticatedActor) or actor.kind != "service":
        return False
    granted = _SERVICE_AUTHORITIES.get((actor.actor_id, actor.role), frozenset())
    return required_authority in granted


def _matches_prior_transition(
    *,
    prior: TransitionRecord,
    run_id: RunId,
    work_node_id: WorkNodeId | None,
    requested_state: TransitionState,
    transition_id: TransitionId,
    reason: NonEmptyStr,
    actor: ActorRef,
    expected_record_version: RecordVersion,
    idempotency_key: IdempotencyKey,
    decided_at: UtcTimestamp,
    trace_id: TraceId | None,
) -> bool:
    return (
        prior.metadata.idempotency_key == idempotency_key
        and prior.transition_id == transition_id
        and prior.run_id == run_id
        and prior.work_node_id == work_node_id
        and prior.next_state == requested_state
        and prior.reason == reason
        and prior.actor == actor
        and prior.previous_record_version == expected_record_version
        and prior.next_record_version == expected_record_version + 1
        and prior.metadata.record_version == expected_record_version + 1
        and prior.metadata.created_at == decided_at
        and prior.metadata.updated_at == decided_at
        and prior.metadata.trace_id == trace_id
    )


def _reject(
    *,
    code: TransitionRejectionCode,
    message: NonEmptyStr,
    aggregate: TransitionAggregate,
    current_run_id: RunId,
    current_work_node_id: WorkNodeId | None,
    current_state: TransitionState,
    current_record_version: RecordVersion,
    requested_state: TransitionState,
    transition_id: TransitionId,
    reason: NonEmptyStr,
    actor: ActorRef,
    expected_record_version: RecordVersion,
    idempotency_key: IdempotencyKey,
    decided_at: UtcTimestamp,
    trace_id: TraceId | None,
) -> RejectedTransition:
    return RejectedTransition(
        code=code,
        message=message,
        audit=_audit(
            aggregate=aggregate,
            run_id=current_run_id,
            work_node_id=current_work_node_id,
            idempotency_key=idempotency_key,
            transition_id=transition_id,
            previous_state=current_state,
            requested_state=requested_state,
            current_record_version=current_record_version,
            expected_record_version=expected_record_version,
            actor=actor,
            reason=reason,
            decided_at=decided_at,
            trace_id=trace_id,
            outcome="rejected",
            rejection_code=code,
        ),
    )


def _audit(
    *,
    aggregate: TransitionAggregate,
    run_id: RunId,
    work_node_id: WorkNodeId | None,
    idempotency_key: IdempotencyKey,
    transition_id: TransitionId,
    previous_state: TransitionState,
    requested_state: TransitionState,
    current_record_version: RecordVersion,
    expected_record_version: RecordVersion,
    actor: ActorRef,
    reason: NonEmptyStr,
    decided_at: UtcTimestamp,
    trace_id: TraceId | None,
    outcome: TransitionAuditOutcome,
    rejection_code: TransitionRejectionCode | None = None,
) -> TransitionAuditPayload:
    return TransitionAuditPayload(
        aggregate=aggregate,
        run_id=run_id,
        work_node_id=work_node_id,
        idempotency_key=idempotency_key,
        transition_id=transition_id,
        previous_state=previous_state,
        requested_state=requested_state,
        current_record_version=current_record_version,
        expected_record_version=expected_record_version,
        actor=actor,
        reason=reason,
        decided_at=decided_at,
        trace_id=trace_id,
        outcome=outcome,
        rejection_code=rejection_code,
    )
