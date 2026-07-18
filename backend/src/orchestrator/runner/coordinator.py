"""Deterministic coordinator writes for the fixed runner graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Final, Literal

from sqlalchemy import text

from orchestrator.domain import (
    AcceptedTransition,
    AuthenticatedActor,
    ControlStage,
    EventDraft,
    EventStatus,
    RecordMetadata,
    RunGateState,
    RunGateTransitionDecision,
    RunRecord,
    decide_run_gate_transition,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.runner.leases import PostgresRunLeaseQueue, RunLease
from orchestrator.services.events import DurableEventService, EventWakeupNotifier

StageStatus = Literal[
    "accepted",
    "revision",
    "rejected",
    "pass",
    "fail",
    "local_defect",
    "design_gap",
    "authority_needed",
    "cannot_continue",
    "approved",
]

STAGE_STATUS_TARGETS: Final[dict[ControlStage, dict[StageStatus, ControlStage]]] = {
    "INTAKE": {"accepted": "INVESTIGATE"},
    "INVESTIGATE": {"accepted": "DESIGN"},
    "DESIGN": {"accepted": "DESIGN_CRITIQUE"},
    "DESIGN_CRITIQUE": {"accepted": "PLAN", "revision": "DESIGN"},
    "PLAN": {"accepted": "VALIDATE_PLAN"},
    "VALIDATE_PLAN": {"accepted": "DISPATCH", "rejected": "TRIAGE"},
    "DISPATCH": {"accepted": "EXECUTE"},
    "EXECUTE": {"accepted": "LOCAL_VERIFY"},
    "LOCAL_VERIFY": {"pass": "INTEGRATE", "fail": "TRIAGE"},
    "INTEGRATE": {"pass": "OUTCOME_VERIFY", "fail": "TRIAGE"},
    "OUTCOME_VERIFY": {"pass": "COMPLETE", "fail": "TRIAGE"},
    "TRIAGE": {
        "local_defect": "DISPATCH",
        "design_gap": "DESIGN",
        "authority_needed": "USER_APPROVAL",
        "cannot_continue": "BLOCKED",
    },
    "USER_APPROVAL": {"approved": "RESUME_GATE", "rejected": "BLOCKED"},
    "RESUME_GATE": {"accepted": "DISPATCH"},
    "COMPLETE": {},
    "BLOCKED": {},
}


class InvalidStageStatusError(ValueError):
    """A port returned a status that the current fixed gate cannot accept."""


class StaleCheckpointError(RuntimeError):
    """Checkpoint state cannot safely be reconciled to authoritative state."""


@dataclass(frozen=True, slots=True)
class StageResult:
    """Validated, non-model-specific status supplied by a stage application port."""

    status: StageStatus


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    target: ControlStage
    committed: bool


class _NoopNotifier:
    def notify_run_events(self, run_id: str) -> None:
        del run_id


class RunnerCoordinator:
    """Maps typed stage outcomes through domain policy and durable events.

    It intentionally has no model or tool dependency.  A stage port produces a
    bounded status; this shell selects the already-declared target, calls the
    pure transition service, writes the authoritative record, and appends the
    matching event in one transaction.
    """

    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        lease_queue: PostgresRunLeaseQueue,
        *,
        notifier: EventWakeupNotifier | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._lease_queue = lease_queue
        self._events = DurableEventService(unit_of_work, notifier or _NoopNotifier())

    def target_for(self, stage: ControlStage, result: StageResult) -> ControlStage:
        try:
            return STAGE_STATUS_TARGETS[stage][result.status]
        except KeyError as error:
            raise InvalidStageStatusError(
                f"status {result.status!r} is not permitted at {stage}"
            ) from error

    def advance(
        self,
        *,
        stage: ControlStage,
        result: StageResult,
        lease: RunLease,
    ) -> AdvanceResult:
        target = self.target_for(stage, result)
        with self._unit_of_work.transaction() as unit_of_work:
            run = self._require_run(unit_of_work, lease.run_id)
            if run.status in {"blocked", "completed", "failed"}:
                raise StaleCheckpointError(
                    f"run {lease.run_id} is terminal while checkpoint requests {stage}"
                )
            if run.current_gate == target:
                # A process may have committed the domain transition before it
                # died while LangGraph was saving its next checkpoint.  This
                # is the normal, safe recovery path: do not write it again.
                return AdvanceResult(target=target, committed=False)
            if run.current_gate != stage:
                raise StaleCheckpointError(
                    f"checkpoint gate {stage} conflicts with authoritative "
                    f"gate {run.current_gate}"
                )

        command_key = self._command_key(
            lease.run_id, stage, target, run.metadata.record_version
        )
        transition_id = self._identifier("transition_runner", command_key)
        event_id = self._identifier("evt_runner", command_key)
        captured: AcceptedTransition | None = None
        draft = self._event_draft(
            event_id=event_id,
            command_key=command_key,
            transition_id=transition_id,
            run_id=lease.run_id,
            stage=stage,
            target=target,
            lease=lease,
            conversation_id=self._conversation_id_for_run(lease.run_id),
        )

        def state_change(unit_of_work: PostgresUnitOfWork) -> None:
            nonlocal captured
            self._lease_queue.require_current(unit_of_work._require_connection(), lease)
            current_run = self._require_run(unit_of_work, lease.run_id)
            if current_run.current_gate == target:
                return
            if current_run.current_gate != stage:
                raise StaleCheckpointError(
                    f"authoritative gate changed to {current_run.current_gate}"
                )
            decision = RunGateTransitionDecision(
                transition_id=transition_id,
                run_id=lease.run_id,
                requested_gate=target,
                reason=f"Validated {stage} status selects {target}",
                actor=self._transition_actor(stage, target),
                expected_record_version=current_run.metadata.record_version,
                idempotency_key=command_key,
                decided_at=datetime.now(UTC),
                trace_id=self._trace_id(lease.run_id),
            )
            prior = unit_of_work.transition_log.get_by_idempotency_key(command_key)
            decision_result = decide_run_gate_transition(
                RunGateState(
                    run_id=current_run.run_id,
                    gate=current_run.current_gate,
                    record_version=current_run.metadata.record_version,
                ),
                decision,
                prior,
            )
            if not isinstance(decision_result, AcceptedTransition):
                raise StaleCheckpointError(
                    f"transition policy rejected {stage} to {target}: "
                    f"{decision_result.code}"
                )
            captured = decision_result
            if decision_result.replayed:
                return
            unit_of_work.transition_log.add(decision_result.transition)
            unit_of_work.runs.compare_and_swap(
                self._advanced_run(current_run, target, command_key),
                expected_record_version=current_run.metadata.record_version,
            )

        self._events.apply(draft, state_change)
        if captured is None:
            # DurableEventService skipped the state-change closure because an
            # identical command event already exists.  Reconstruct only a
            # policy replay; it must never run a second mutation.
            with self._unit_of_work.transaction() as unit_of_work:
                prior = unit_of_work.transition_log.get_by_idempotency_key(command_key)
                if prior is None:
                    raise RuntimeError("event replay has no matching transition audit")
        return AdvanceResult(target=target, committed=captured is not None)

    def stop_safely(
        self,
        run_id: str,
        *,
        reason: Literal["cancelled", "attempts_exhausted", "stale_checkpoint"],
    ) -> bool:
        """Make cancellation, budget, and irreconcilable recovery explicit.

        The current control gate is retained because the fixed transition table
        has no globally legal edge to BLOCKED.  ``status=blocked`` is the
        authoritative safe stop, with a durable event explaining why.
        """
        with self._unit_of_work.transaction() as unit_of_work:
            run = self._require_run(unit_of_work, run_id)
            if run.status in {"blocked", "completed", "failed"}:
                return False
        command_key = f"runner:safe-stop:{run_id}:{reason}"
        event_id = self._identifier("evt_runner_stop", command_key)
        draft = EventDraft(
            event_id=event_id,
            run_id=run_id,
            conversation_id=self._conversation_id_for_run(run_id),
            occurred_at=datetime.now(UTC),
            type="run.blocked",
            stage=run.current_gate,
            node_id="runner-coordinator",
            attempt_id=self._attempt_id(run_id, 0),
            design_version=1,
            packet_version=1,
            actor_role="runner",
            status="blocked",
            outcome="blocked",
            summary=f"Runner safely stopped: {reason.replace('_', ' ')}",
            detail_ref=f"/api/v1/runs/{run_id}/events/{event_id}/detail",
            correlation_id=command_key,
            trace_id=self._trace_id(run_id),
            span_id="0123456789abcdef",
            command_idempotency_key=command_key,
            inline_detail={
                "next_state": "blocked",
                "policy_rule_ids": [reason.replace("_", "-")],
            },
        )

        changed = False

        def state_change(unit_of_work: PostgresUnitOfWork) -> None:
            nonlocal changed
            current = self._require_run(unit_of_work, run_id)
            if current.status in {"blocked", "completed", "failed"}:
                return
            changed = True
            unit_of_work.runs.compare_and_swap(
                current.model_copy(
                    update={
                        "status": "blocked",
                        "metadata": self._metadata(
                            current.metadata,
                            current.metadata.record_version + 1,
                            command_key,
                        ),
                    }
                ),
                expected_record_version=current.metadata.record_version,
            )

        self._events.apply(draft, state_change)
        return changed

    @staticmethod
    def _require_run(unit_of_work: PostgresUnitOfWork, run_id: str) -> RunRecord:
        run = unit_of_work.runs.get(run_id)
        if run is None:
            raise LookupError(f"run {run_id!r} does not exist")
        return run

    def _advanced_run(
        self, run: RunRecord, target: ControlStage, key: str
    ) -> RunRecord:
        status: EventStatus = (
            "completed"
            if target == "COMPLETE"
            else "blocked"
            if target == "BLOCKED"
            else "running"
        )
        return run.model_copy(
            update={
                "current_gate": target,
                "status": status,
                "metadata": self._metadata(
                    run.metadata, run.metadata.record_version + 1, key
                ),
            }
        )

    @staticmethod
    def _metadata(metadata: RecordMetadata, version: int, key: str) -> RecordMetadata:
        return metadata.model_copy(
            update={
                "record_version": version,
                "updated_at": datetime.now(UTC),
                "idempotency_key": key,
            }
        )

    def _event_draft(
        self,
        *,
        event_id: str,
        command_key: str,
        transition_id: str,
        run_id: str,
        stage: ControlStage,
        target: ControlStage,
        lease: RunLease,
        conversation_id: str,
    ) -> EventDraft:
        status: EventStatus = (
            "completed"
            if target == "COMPLETE"
            else "blocked"
            if target == "BLOCKED"
            else "accepted"
        )
        return EventDraft(
            event_id=event_id,
            run_id=run_id,
            conversation_id=conversation_id,
            occurred_at=datetime.now(UTC),
            type="run.completed"
            if target == "COMPLETE"
            else "run.blocked"
            if target == "BLOCKED"
            else "transition.applied",
            stage=stage,
            node_id="runner-coordinator",
            attempt_id=self._attempt_id(run_id, lease.attempt),
            design_version=1,
            packet_version=1,
            actor_role="runner",
            status=status,
            outcome=status,
            summary=f"Validated {stage} status advanced to {target}",
            detail_ref=f"/api/v1/runs/{run_id}/events/{event_id}/detail",
            correlation_id=command_key,
            trace_id=self._trace_id(run_id),
            span_id="0123456789abcdef",
            command_idempotency_key=command_key,
            transition_id=transition_id,
            inline_detail={
                "previous_state": stage,
                "next_state": target,
            },
        )

    @staticmethod
    def _transition_actor(
        stage: ControlStage, target: ControlStage
    ) -> AuthenticatedActor:
        now = datetime.now(UTC)
        if stage == "OUTCOME_VERIFY" and target == "COMPLETE":
            return AuthenticatedActor(
                actor_id="service_assurance",
                kind="service",
                role="assurance",
                authenticated_at=now,
                authentication_context="runner-service",
            )
        return AuthenticatedActor(
            actor_id="service_transition",
            kind="service",
            role="transition-service",
            authenticated_at=now,
            authentication_context="runner-service",
        )

    @staticmethod
    def _command_key(
        run_id: str, stage: ControlStage, target: ControlStage, version: int
    ) -> str:
        return f"runner:transition:{run_id}:{stage}:{target}:{version}"

    @staticmethod
    def _identifier(prefix: str, value: str) -> str:
        return f"{prefix}_{sha256(value.encode()).hexdigest()[:32]}"

    @staticmethod
    def _trace_id(run_id: str) -> str:
        return sha256(run_id.encode()).hexdigest()[:32]

    def _conversation_id_for_run(self, run_id: str) -> str:
        """Use the command-owned conversation projection for every runner event.

        Legacy/test records predate that projection, so they retain a stable
        deterministic fallback instead of making recovery impossible.
        """
        with self._unit_of_work.transaction() as unit_of_work:
            value = unit_of_work.connection.execute(
                text("SELECT conversation_id FROM runs WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).scalar_one_or_none()
        return str(value) if value else f"conv_{sha256(run_id.encode()).hexdigest()[:32]}"

    @staticmethod
    def _attempt_id(run_id: str, attempt: int) -> str:
        return f"attempt_{sha256(f'{run_id}:{attempt}'.encode()).hexdigest()[:24]}"
