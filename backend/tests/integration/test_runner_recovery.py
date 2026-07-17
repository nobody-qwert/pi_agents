"""PostgreSQL proof for runner leases, fixed-graph checkpoints, and recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from orchestrator.domain import (
    RUN_GATE_TRANSITIONS,
    ControlStage,
    RecordMetadata,
    RunRecord,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.runner import (
    LeaseLostError,
    PostgresRunLeaseQueue,
    RunnerCoordinator,
    RunnerService,
    StageResult,
    StaleCheckpointError,
    deterministic_happy_path,
)


def _run(run_id: str, *, gate: ControlStage = "INTAKE") -> RunRecord:
    timestamp = datetime(2026, 7, 17, 8, tzinfo=UTC)
    return RunRecord(
        metadata=RecordMetadata(
            record_version=1,
            created_at=timestamp,
            updated_at=timestamp,
            idempotency_key=f"run:create:{run_id}",
            trace_id="0123456789abcdef0123456789abcdef",
        ),
        run_id=run_id,
        tenant_id="tenant_runner",
        outcome="Exercise lease and checkpoint recovery",
        current_gate=gate,
        risk_class="low",
        status="created",
    )


def _components(
    postgres_uow: PostgresUnitOfWork, database_url: str
) -> tuple[PostgresRunLeaseQueue, RunnerCoordinator, RunnerService]:
    queue = PostgresRunLeaseQueue(database_url, lease_duration=timedelta(seconds=30))
    coordinator = RunnerCoordinator(postgres_uow, queue)
    runner = RunnerService(
        database_url=database_url,
        owner="runner-integration",
        lease_queue=queue,
        coordinator=coordinator,
        stage_port=deterministic_happy_path(),
    )
    return queue, coordinator, runner


def _seed(
    postgres_uow: PostgresUnitOfWork,
    queue: PostgresRunLeaseQueue,
    run_id: str,
    *,
    max_attempts: int = 3,
    gate: ControlStage = "INTAKE",
    available_at: datetime | None = None,
) -> None:
    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.runs.add(_run(run_id, gate=gate))
    assert queue.enqueue(
        run_id=run_id, max_attempts=max_attempts, available_at=available_at
    )


def test_runner_uses_postgres_checkpoints_and_only_fixed_permitted_gates(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    queue, _, runner = _components(postgres_uow, migrated_postgres_database)
    try:
        _seed(postgres_uow, queue, "run_runner_happy")

        result = runner.run("run_runner_happy")

        assert result.outcome == "completed"
        with postgres_uow.transaction() as unit_of_work:
            run = unit_of_work.runs.get("run_runner_happy")
            transitions = (
                unit_of_work._require_connection()
                .execute(
                    text(
                        "SELECT previous_state, next_state FROM transition_log "
                        "WHERE run_id = :run_id ORDER BY created_at, transition_id"
                    ),
                    {"run_id": "run_runner_happy"},
                )
                .all()
            )
            checkpoint_count = (
                unit_of_work._require_connection()
                .execute(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = :run_id"),
                    {"run_id": "run_runner_happy"},
                )
                .scalar_one()
            )

        assert run is not None
        assert run.current_gate == "COMPLETE"
        assert run.status == "completed"
        assert checkpoint_count > 0
        assert all(
            (previous, target)
            in {
                (source, destination)
                for source, destinations in RUN_GATE_TRANSITIONS.items()
                for destination in destinations
            }
            for previous, target in transitions
        )
    finally:
        queue.close()


def test_crash_after_domain_commit_resumes_without_duplicate_event_or_transition(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    queue, coordinator, runner = _components(postgres_uow, migrated_postgres_database)
    try:
        run_id = "run_runner_resume"
        _seed(postgres_uow, queue, run_id)
        claimed = queue.claim(run_id, owner="runner-crashed")
        assert claimed.lease is not None

        # Simulate death after the authoritative transaction commits but before
        # LangGraph can save the next checkpoint.
        coordinator.advance(
            stage="INTAKE", result=StageResult("accepted"), lease=claimed.lease
        )
        assert queue.release(claimed.lease)

        assert runner.run(run_id).outcome == "completed"
        with postgres_uow.transaction() as unit_of_work:
            transitions = (
                unit_of_work._require_connection()
                .execute(
                    text(
                        "SELECT count(*) FROM transition_log WHERE run_id = :run_id "
                        "AND previous_state = 'INTAKE' AND next_state = 'INVESTIGATE'"
                    ),
                    {"run_id": run_id},
                )
                .scalar_one()
            )
            command_counts = (
                unit_of_work._require_connection()
                .execute(
                    text(
                        "SELECT count(*), count(DISTINCT command_idempotency_key) "
                        "FROM run_events WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .one()
            )

        assert transitions == 1
        assert command_counts[0] == command_counts[1]
    finally:
        queue.close()


def test_duplicate_delivery_cannot_obtain_a_second_current_lease(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    queue, _, _ = _components(postgres_uow, migrated_postgres_database)
    try:
        _seed(postgres_uow, queue, "run_runner_duplicate")
        first = queue.claim("run_runner_duplicate", owner="runner-one")
        second = queue.claim("run_runner_duplicate", owner="runner-two")

        assert first.outcome == "claimed"
        assert second.outcome == "unavailable"
        assert first.lease is not None
        assert queue.release(first.lease)
    finally:
        queue.close()


def test_lease_renewal_is_compare_and_swap_and_expiry_allows_takeover(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    queue, _, _ = _components(postgres_uow, migrated_postgres_database)
    try:
        run_id = "run_runner_lease_cas"
        start = datetime(2026, 7, 17, 8, tzinfo=UTC)
        _seed(postgres_uow, queue, run_id, available_at=start)
        claim = queue.claim(run_id, owner="runner-one", now=start)
        assert claim.lease is not None
        renewed = queue.renew(claim.lease, now=start + timedelta(seconds=1))

        # The original expiry is now stale, so a second renewal cannot overwrite
        # the holder's newer expiry even with the same token and epoch.
        with pytest.raises(LeaseLostError):
            queue.renew(claim.lease, now=start + timedelta(seconds=2))

        takeover = queue.claim(run_id, owner="runner-two", now=renewed.expires_at)
        assert takeover.outcome == "claimed"
        assert takeover.lease is not None
        assert takeover.lease.epoch == renewed.epoch + 1
    finally:
        queue.close()


def test_cancellation_and_exhausted_attempt_budget_become_blocked_states(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    queue, _, runner = _components(postgres_uow, migrated_postgres_database)
    try:
        cancelled_run = "run_runner_cancelled"
        _seed(postgres_uow, queue, cancelled_run)
        assert queue.request_cancellation(cancelled_run)
        assert runner.run(cancelled_run).outcome == "blocked"

        exhausted_run = "run_runner_budget"
        _seed(postgres_uow, queue, exhausted_run, max_attempts=1)
        lease = queue.claim(exhausted_run, owner="runner-failing")
        assert lease.lease is not None
        assert queue.release(lease.lease)
        assert runner.run(exhausted_run).outcome == "blocked"

        with postgres_uow.transaction() as unit_of_work:
            cancelled = unit_of_work.runs.get(cancelled_run)
            exhausted = unit_of_work.runs.get(exhausted_run)
        cancelled_entry = queue.entry(cancelled_run)
        budget = queue.entry(exhausted_run)

        assert cancelled is not None and cancelled.status == "blocked"
        assert exhausted is not None and exhausted.status == "blocked"
        assert cancelled_entry is not None and cancelled_entry.completed_at is not None
        assert budget is not None and budget.budget_exhausted_at is not None
        assert budget.completed_at is not None
    finally:
        queue.close()


def test_irreconcilable_stale_checkpoint_is_explicitly_blocked(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    queue, coordinator, _ = _components(postgres_uow, migrated_postgres_database)
    try:
        run_id = "run_runner_stale"
        _seed(postgres_uow, queue, run_id, gate="DESIGN")
        claim = queue.claim(run_id, owner="runner-stale")
        assert claim.lease is not None

        with pytest.raises(StaleCheckpointError):
            coordinator.advance(
                stage="INTAKE", result=StageResult("accepted"), lease=claim.lease
            )
        assert coordinator.stop_safely(run_id, reason="stale_checkpoint")

        with postgres_uow.transaction() as unit_of_work:
            run = unit_of_work.runs.get(run_id)
        assert run is not None and run.status == "blocked"
    finally:
        queue.close()
