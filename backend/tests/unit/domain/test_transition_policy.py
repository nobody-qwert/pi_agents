from datetime import UTC, datetime
from typing import cast, get_args

import pytest

from orchestrator.domain import (
    RUN_GATE_TRANSITIONS,
    WORK_NODE_TRANSITIONS,
    AcceptedTransition,
    ActorRef,
    AuthenticatedActor,
    ControlStage,
    RejectedTransition,
    RunGateState,
    RunGateTransitionDecision,
    WorkNodeState,
    WorkNodeStatus,
    WorkNodeTransitionDecision,
    decide_run_gate_transition,
    decide_work_node_transition,
)

DECIDED_AT = datetime(2026, 7, 16, 12, tzinfo=UTC)
TRACE_ID = "0123456789abcdef0123456789abcdef"


def authenticated_service_actor(actor_id: str, role: str) -> AuthenticatedActor:
    return AuthenticatedActor(
        actor_id=actor_id,
        kind="service",
        role=role,
        authenticated_at=DECIDED_AT,
        authentication_context="service-mtls",
    )


def service_actor() -> AuthenticatedActor:
    return authenticated_service_actor(
        actor_id="service_transition",
        role="transition-service",
    )


def assurance_service_actor() -> AuthenticatedActor:
    return authenticated_service_actor(
        actor_id="service_assurance",
        role="assurance",
    )


def actor_for_work_edge(
    current_status: WorkNodeStatus, requested_status: WorkNodeStatus
) -> ActorRef:
    if (current_status, requested_status) in {
        ("IMPLEMENTED", "LOCALLY_VERIFIED"),
        ("INTEGRATED", "VERIFIED"),
    }:
        return assurance_service_actor()
    return service_actor()


def actor_for_run_edge(
    current_gate: ControlStage, requested_gate: ControlStage
) -> ActorRef:
    if (current_gate, requested_gate) == ("OUTCOME_VERIFY", "COMPLETE"):
        return assurance_service_actor()
    return service_actor()


def run_decision(
    requested_gate: ControlStage,
    *,
    actor: ActorRef | None = None,
    expected_version: int = 3,
    idempotency_key: str = "transition:run_policy:gate:3",
    transition_id: str = "transition_run_policy_3",
    reason: str = "Validated fixed-graph decision",
    run_id: str = "run_policy",
) -> RunGateTransitionDecision:
    return RunGateTransitionDecision(
        transition_id=transition_id,
        run_id=run_id,
        requested_gate=requested_gate,
        reason=reason,
        actor=actor or service_actor(),
        expected_record_version=expected_version,
        idempotency_key=idempotency_key,
        decided_at=DECIDED_AT,
        trace_id=TRACE_ID,
    )


def work_decision(
    requested_status: WorkNodeStatus,
    *,
    actor: ActorRef | None = None,
    expected_version: int = 3,
    idempotency_key: str = "transition:run_policy:wn_policy:3",
    transition_id: str = "transition_work_policy_3",
    reason: str = "Validated work-node decision",
    run_id: str = "run_policy",
    work_node_id: str = "wn_policy",
) -> WorkNodeTransitionDecision:
    return WorkNodeTransitionDecision(
        transition_id=transition_id,
        run_id=run_id,
        work_node_id=work_node_id,
        requested_status=requested_status,
        reason=reason,
        actor=actor or service_actor(),
        expected_record_version=expected_version,
        idempotency_key=idempotency_key,
        decided_at=DECIDED_AT,
        trace_id=TRACE_ID,
    )


RUN_EDGES = tuple(
    (current, requested)
    for current, requested_states in RUN_GATE_TRANSITIONS.items()
    for requested in requested_states
)
WORK_EDGES = tuple(
    (current, requested)
    for current, requested_states in WORK_NODE_TRANSITIONS.items()
    for requested in requested_states
)


@pytest.mark.parametrize(("current_gate", "requested_gate"), RUN_EDGES)
def test_every_permitted_run_gate_edge_is_accepted(
    current_gate: ControlStage, requested_gate: ControlStage
) -> None:
    result = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate=current_gate, record_version=3),
        run_decision(
            requested_gate,
            actor=actor_for_run_edge(current_gate, requested_gate),
        ),
    )

    assert isinstance(result, AcceptedTransition)
    assert result.replayed is False
    assert result.transition.previous_state == current_gate
    assert result.transition.next_state == requested_gate
    assert result.transition.previous_record_version == 3
    assert result.transition.next_record_version == 4
    assert result.transition.metadata.record_version == 4
    assert result.audit.outcome == "accepted"


@pytest.mark.parametrize(("current_status", "requested_status"), WORK_EDGES)
def test_every_permitted_work_node_edge_is_accepted(
    current_status: WorkNodeStatus, requested_status: WorkNodeStatus
) -> None:
    result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status=current_status,
            record_version=3,
        ),
        work_decision(
            requested_status,
            actor=actor_for_work_edge(current_status, requested_status),
        ),
    )

    assert isinstance(result, AcceptedTransition)
    assert result.transition.work_node_id == "wn_policy"
    assert result.transition.previous_state == current_status
    assert result.transition.next_state == requested_status


def test_transition_tables_consider_every_enum_state() -> None:
    run_states = cast(tuple[ControlStage, ...], get_args(ControlStage))
    work_states = cast(tuple[WorkNodeStatus, ...], get_args(WorkNodeStatus))

    assert set(RUN_GATE_TRANSITIONS) == set(run_states)
    assert set(WORK_NODE_TRANSITIONS) == set(work_states)


@pytest.mark.parametrize(
    ("current_gate", "requested_gate"),
    (
        ("INTAKE", "INTAKE"),
        ("INTAKE", "COMPLETE"),
        ("EXECUTE", "COMPLETE"),
        ("COMPLETE", "INTAKE"),
        ("BLOCKED", "DISPATCH"),
    ),
)
def test_unlisted_run_gate_edges_are_rejected(
    current_gate: ControlStage, requested_gate: ControlStage
) -> None:
    result = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate=current_gate, record_version=3),
        run_decision(requested_gate),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "forbidden_edge"
    assert result.audit.rejection_code == "forbidden_edge"


@pytest.mark.parametrize(
    ("current_status", "requested_status"),
    (
        ("PROPOSED", "PROPOSED"),
        ("PROPOSED", "VERIFIED"),
        ("IN_PROGRESS", "LOCALLY_VERIFIED"),
        ("IN_PROGRESS", "VERIFIED"),
        ("IMPLEMENTED", "VERIFIED"),
        ("VERIFIED", "READY"),
        ("INVALIDATED", "IN_PROGRESS"),
    ),
)
def test_unlisted_work_node_edges_are_rejected(
    current_status: WorkNodeStatus, requested_status: WorkNodeStatus
) -> None:
    result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status=current_status,
            record_version=3,
        ),
        work_decision(requested_status),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "forbidden_edge"


def test_stale_optimistic_version_is_rejected_without_increment() -> None:
    result = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate="INTAKE", record_version=4),
        run_decision("INVESTIGATE", expected_version=3),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "stale_version"
    assert result.audit.current_record_version == 4
    assert result.audit.expected_record_version == 3


@pytest.mark.parametrize(
    "actor",
    (
        ActorRef(
            actor_id="service_transition",
            kind="service",
            role="transition-service",
        ),
        ActorRef(actor_id="service_worker", kind="service", role="worker"),
        ActorRef(actor_id="service_impostor", kind="service", role="assurance"),
        authenticated_service_actor("service_worker", "worker"),
        authenticated_service_actor("service_impostor", "assurance"),
        ActorRef(
            actor_id="service_assurance",
            kind="service",
            role="transition-service",
        ),
        ActorRef(actor_id="agent_worker", kind="agent", role="worker"),
        ActorRef(actor_id="user_operator", kind="human", role="operator"),
        ActorRef(actor_id="system_runtime", kind="system", role="runtime"),
    ),
)
def test_untrusted_or_unknown_actors_are_rejected(actor: ActorRef) -> None:
    result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status="IN_PROGRESS",
            record_version=3,
        ),
        work_decision("IMPLEMENTED", actor=actor),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "invalid_actor"


@pytest.mark.parametrize(
    ("current_status", "requested_status"),
    (
        ("IMPLEMENTED", "LOCALLY_VERIFIED"),
        ("INTEGRATED", "VERIFIED"),
    ),
)
@pytest.mark.parametrize(
    "actor",
    (
        ActorRef(actor_id="service_assurance", kind="service", role="assurance"),
        ActorRef(actor_id="service_worker", kind="service", role="worker"),
        ActorRef(actor_id="service_impostor", kind="service", role="assurance"),
        authenticated_service_actor("service_worker", "worker"),
        authenticated_service_actor("service_impostor", "assurance"),
        ActorRef(
            actor_id="service_transition",
            kind="service",
            role="transition-service",
        ),
        ActorRef(
            actor_id="service_assurance",
            kind="service",
            role="transition-service",
        ),
    ),
)
def test_non_assurance_service_cannot_accept_verification_states(
    current_status: WorkNodeStatus,
    requested_status: WorkNodeStatus,
    actor: ActorRef,
) -> None:
    result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status=current_status,
            record_version=3,
        ),
        work_decision(requested_status, actor=actor),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "invalid_actor"
    assert result.audit.rejection_code == "invalid_actor"


@pytest.mark.parametrize(
    ("current_status", "requested_status"),
    (
        ("IMPLEMENTED", "LOCALLY_VERIFIED"),
        ("INTEGRATED", "VERIFIED"),
    ),
)
def test_assurance_service_can_accept_verification_states(
    current_status: WorkNodeStatus, requested_status: WorkNodeStatus
) -> None:
    result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status=current_status,
            record_version=3,
        ),
        work_decision(requested_status, actor=assurance_service_actor()),
    )

    assert isinstance(result, AcceptedTransition)
    assert result.transition.next_state == requested_status


@pytest.mark.parametrize(
    "actor",
    (
        ActorRef(actor_id="service_assurance", kind="service", role="assurance"),
        ActorRef(actor_id="service_worker", kind="service", role="worker"),
        ActorRef(actor_id="service_transition", kind="service", role="worker"),
        ActorRef(actor_id="service_impostor", kind="service", role="assurance"),
        authenticated_service_actor("service_worker", "worker"),
        authenticated_service_actor("service_impostor", "assurance"),
        service_actor(),
    ),
)
def test_only_authorized_assurance_service_can_complete_run(actor: ActorRef) -> None:
    result = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate="OUTCOME_VERIFY", record_version=3),
        run_decision("COMPLETE", actor=actor),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "invalid_actor"
    assert result.audit.rejection_code == "invalid_actor"


def test_assurance_service_can_complete_run() -> None:
    result = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate="OUTCOME_VERIFY", record_version=3),
        run_decision("COMPLETE", actor=assurance_service_actor()),
    )

    assert isinstance(result, AcceptedTransition)
    assert result.transition.next_state == "COMPLETE"


def test_worker_completion_claim_cannot_directly_verify_work_or_complete_run() -> None:
    worker = ActorRef(actor_id="agent_worker", kind="agent", role="worker")
    work_result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status="IN_PROGRESS",
            record_version=3,
        ),
        work_decision("VERIFIED", actor=worker),
    )
    run_result = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate="OUTCOME_VERIFY", record_version=3),
        run_decision("COMPLETE", actor=worker),
    )

    assert isinstance(work_result, RejectedTransition)
    assert isinstance(run_result, RejectedTransition)
    assert work_result.accepted is False
    assert run_result.accepted is False


def test_same_accepted_command_replays_without_a_second_transition() -> None:
    decision = run_decision("INVESTIGATE")
    first = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate="INTAKE", record_version=3),
        decision,
    )
    assert isinstance(first, AcceptedTransition)

    replay = decide_run_gate_transition(
        RunGateState(run_id="run_policy", gate="INVESTIGATE", record_version=4),
        decision,
        prior_transition=first.transition,
    )

    assert isinstance(replay, AcceptedTransition)
    assert replay.replayed is True
    assert replay.transition == first.transition
    assert replay.audit.outcome == "replayed"


def test_conflicting_reuse_of_an_idempotency_key_is_rejected() -> None:
    first = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status="READY",
            record_version=3,
        ),
        work_decision("IN_PROGRESS"),
    )
    assert isinstance(first, AcceptedTransition)

    conflict = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status="IN_PROGRESS",
            record_version=4,
        ),
        work_decision(
            "IMPLEMENTED",
            expected_version=4,
            transition_id="transition_work_policy_4",
            reason="Different command reusing the same key",
        ),
        prior_transition=first.transition,
    )

    assert isinstance(conflict, RejectedTransition)
    assert conflict.code == "idempotency_conflict"


@pytest.mark.parametrize(
    ("run_id", "work_node_id"),
    (("run_other", "wn_policy"), ("run_policy", "wn_other")),
)
def test_work_decision_target_must_match_current_state(
    run_id: str, work_node_id: str
) -> None:
    result = decide_work_node_transition(
        WorkNodeState(
            run_id="run_policy",
            work_node_id="wn_policy",
            status="READY",
            record_version=3,
        ),
        work_decision("IN_PROGRESS", run_id=run_id, work_node_id=work_node_id),
    )

    assert isinstance(result, RejectedTransition)
    assert result.code == "target_mismatch"
