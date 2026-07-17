"""Integration coverage for atomic ordered PostgreSQL run events."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock
from time import sleep

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from orchestrator.domain import (
    AuthenticatedActor,
    EventDraft,
    EventEnvelope,
    RecordMetadata,
    RunId,
    RunRecord,
    TransitionRecord,
)
from orchestrator.persistence import EventConflictError, PostgresUnitOfWork
from orchestrator.services.events import (
    DurableEventService,
    PostgresEventWakeupNotifier,
    ReplayAccessDeniedError,
)

BACKEND_DIR = Path(__file__).parents[2]


class _RecordingNotifier:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    def notify_run_events(self, run_id: RunId) -> None:
        self.run_ids.append(run_id)


class _AllowedReplay:
    def can_replay_events(self, run_id: RunId) -> bool:
        return True


class _DeniedReplay:
    def can_replay_events(self, run_id: RunId) -> bool:
        return False


def _run(run_id: str) -> RunRecord:
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
        tenant_id="tenant_event_log",
        outcome="Exercise the durable event log",
        current_gate="INTAKE",
        risk_class="low",
        status="created",
    )


def _draft(
    suffix: str,
    *,
    run_id: str = "run_event_log",
    transition_id: str | None = None,
) -> EventDraft:
    return EventDraft(
        event_id=f"evt_{suffix}",
        run_id=run_id,
        conversation_id="conv_event_log",
        occurred_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
        type="transition.applied",
        stage="INTAKE",
        node_id="event-service",
        attempt_id=f"attempt_{suffix}",
        design_version=1,
        packet_version=1,
        actor_role="event-service",
        status="accepted",
        outcome="accepted",
        summary=f"Event {suffix} was accepted",
        detail_ref=f"/api/v1/runs/{run_id}/events/evt_{suffix}/detail",
        correlation_id=f"command-{suffix}",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="0123456789abcdef",
        command_idempotency_key=f"command:{suffix}",
        transition_id=transition_id,
        inline_detail={
            "outcome": "accepted",
            "policy_rule_ids": ["transition.allowed"],
        },
    )


def _seed_run(postgres_uow: PostgresUnitOfWork, run_id: str) -> None:
    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.runs.add(_run(run_id))


def _transition(transition_id: str, run_id: str) -> TransitionRecord:
    timestamp = datetime(2026, 7, 17, 8, tzinfo=UTC)
    return TransitionRecord(
        metadata=RecordMetadata(
            record_version=1,
            created_at=timestamp,
            updated_at=timestamp,
            idempotency_key=f"transition:create:{transition_id}",
            trace_id="0123456789abcdef0123456789abcdef",
        ),
        transition_id=transition_id,
        run_id=run_id,
        previous_state="INTAKE",
        next_state="INVESTIGATE",
        reason="Exercise event transition ownership",
        actor=AuthenticatedActor(
            actor_id="service_event_log",
            kind="service",
            role="event-service",
            authenticated_at=timestamp,
            authentication_context="test-mtls",
        ),
        previous_record_version=1,
        next_record_version=2,
    )


def test_event_and_state_commit_or_roll_back_together(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    notifier = _RecordingNotifier()
    service = DurableEventService(postgres_uow, notifier)
    successful_draft = _draft("commit")

    event = service.apply(
        successful_draft,
        lambda unit_of_work: unit_of_work.runs.add(_run(successful_draft.run_id)),
    )

    assert event.sequence == 1
    assert notifier.run_ids == [successful_draft.run_id]
    with postgres_uow.transaction() as unit_of_work:
        assert unit_of_work.runs.get(successful_draft.run_id) is not None
        assert unit_of_work.events.replay(
            run_id=successful_draft.run_id, after_sequence=0
        ) == (event,)

    failed_draft = _draft(
        "rollback",
        run_id="run_event_log_rollback",
        transition_id="transition_missing",
    )
    with pytest.raises(EventConflictError):
        service.apply(
            failed_draft,
            lambda unit_of_work: unit_of_work.runs.add(_run(failed_draft.run_id)),
        )

    assert notifier.run_ids == [successful_draft.run_id]
    with postgres_uow.transaction() as unit_of_work:
        assert unit_of_work.runs.get(failed_draft.run_id) is None
        assert (
            unit_of_work.events.replay(run_id=failed_draft.run_id, after_sequence=0)
            == ()
        )

    # The failed transaction must release its reservation, allowing the same
    # command to create the run and event in a subsequent transaction.
    recovered_draft = _draft(
        "rollback_retried",
        run_id=failed_draft.run_id,
    ).model_copy(
        update={"command_idempotency_key": failed_draft.command_idempotency_key}
    )
    recovered_event = service.apply(
        recovered_draft,
        lambda unit_of_work: unit_of_work.runs.add(_run(recovered_draft.run_id)),
    )

    assert recovered_event.sequence == 1
    assert notifier.run_ids == [successful_draft.run_id, recovered_draft.run_id]
    with postgres_uow.transaction() as unit_of_work:
        assert unit_of_work.events.replay(
            run_id=recovered_draft.run_id, after_sequence=0
        ) == (recovered_event,)


def test_command_reservation_keeps_run_foreign_key(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    """A deferred reservation still cannot commit without its owning run."""
    with pytest.raises(IntegrityError), postgres_uow.transaction() as unit_of_work:
        assert unit_of_work.events.reserve_command(
            run_id="run_event_log_missing",
            command_idempotency_key="command:missing-run",
        )


def test_sequence_allocation_keeps_authoritative_run_audit_in_sync(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    run_id = "run_event_log_audit"
    _seed_run(postgres_uow, run_id)
    event = DurableEventService(postgres_uow, _RecordingNotifier()).apply(
        _draft("audit", run_id=run_id), lambda _: None
    )

    with postgres_uow.transaction() as unit_of_work:
        stored = unit_of_work.runs.get(run_id)
        row = (
            unit_of_work._require_connection()
            .execute(
                text(
                    "SELECT record_version, updated_at, next_event_sequence "
                    "FROM runs WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            .one()
        )

    assert event.sequence == 1
    assert stored is not None
    assert stored.metadata.record_version == 2
    assert row.record_version == stored.metadata.record_version
    assert row.updated_at == stored.metadata.updated_at
    assert row.next_event_sequence == 2


def test_transition_link_must_belong_to_the_event_run(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    event_run_id = "run_event_log_owner"
    transition_run_id = "run_event_log_other_owner"
    transition_id = "transition_other_owner"
    _seed_run(postgres_uow, event_run_id)
    _seed_run(postgres_uow, transition_run_id)
    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.transition_log.add(_transition(transition_id, transition_run_id))

    service = DurableEventService(postgres_uow, _RecordingNotifier())
    with pytest.raises(EventConflictError):
        service.apply(
            _draft(
                "cross_run_transition",
                run_id=event_run_id,
                transition_id=transition_id,
            ),
            lambda _: None,
        )

    with postgres_uow.transaction() as unit_of_work:
        assert unit_of_work.events.replay(run_id=event_run_id, after_sequence=0) == ()
        unchanged_run = unit_of_work.runs.get(event_run_id)

    assert unchanged_run is not None
    assert unchanged_run.metadata.record_version == 1


def test_database_rejects_unrestricted_detail_reference(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    """The storage boundary remains safe if a caller bypasses EventDraft."""
    run_id = "run_event_log_detail_constraint"
    _seed_run(postgres_uow, run_id)
    envelope = _draft("detail_constraint", run_id=run_id).envelope(1)
    unsafe_payload = envelope.model_dump(mode="json")
    unsafe_payload["detail_ref"] = "reasoning: raw private analysis"

    with pytest.raises(IntegrityError), postgres_uow.transaction() as unit_of_work:
        unit_of_work._require_connection().execute(
            text(
                "INSERT INTO run_events ("
                "event_id, run_id, sequence, event_type, occurred_at, payload, detail_ref"
                ") VALUES ("
                ":event_id, :run_id, :sequence, :event_type, :occurred_at, "
                "CAST(:payload AS jsonb), :detail_ref"
                ")"
            ),
            {
                "event_id": envelope.event_id,
                "run_id": envelope.run_id,
                "sequence": envelope.sequence,
                "event_type": envelope.type,
                "occurred_at": envelope.occurred_at,
                "payload": json.dumps(unsafe_payload, separators=(",", ":")),
                "detail_ref": None,
            },
        )

    with postgres_uow.transaction() as unit_of_work:
        unit_of_work._require_connection().execute(
            text(
                "INSERT INTO run_events ("
                "event_id, run_id, sequence, event_type, occurred_at, payload, detail_ref"
                ") VALUES ("
                ":event_id, :run_id, :sequence, :event_type, :occurred_at, "
                "CAST(:payload AS jsonb), :detail_ref"
                ")"
            ),
            {
                "event_id": envelope.event_id,
                "run_id": envelope.run_id,
                "sequence": envelope.sequence,
                "event_type": envelope.type,
                "occurred_at": envelope.occurred_at,
                "payload": json.dumps(
                    envelope.model_dump(mode="json"), separators=(",", ":")
                ),
                "detail_ref": envelope.detail_ref,
            },
        )
        nullable_envelope = (
            _draft("detail_constraint_nullable", run_id=run_id)
            .model_copy(update={"detail_ref": None})
            .envelope(2)
        )
        unit_of_work._require_connection().execute(
            text(
                "INSERT INTO run_events ("
                "event_id, run_id, sequence, event_type, occurred_at, payload, detail_ref"
                ") VALUES ("
                ":event_id, :run_id, :sequence, :event_type, :occurred_at, "
                "CAST(:payload AS jsonb), :detail_ref"
                ")"
            ),
            {
                "event_id": nullable_envelope.event_id,
                "run_id": nullable_envelope.run_id,
                "sequence": nullable_envelope.sequence,
                "event_type": nullable_envelope.type,
                "occurred_at": nullable_envelope.occurred_at,
                "payload": json.dumps(
                    nullable_envelope.model_dump(mode="json"), separators=(",", ":")
                ),
                "detail_ref": None,
            },
        )

    with pytest.raises(IntegrityError), postgres_uow.transaction() as unit_of_work:
        unit_of_work._require_connection().execute(
            text(
                "UPDATE run_events SET detail_ref = :detail_ref, "
                "payload = jsonb_set(payload, '{detail_ref}', "
                "to_jsonb(CAST(:detail_ref AS varchar))) "
                "WHERE event_id = :event_id"
            ),
            {
                "event_id": envelope.event_id,
                "detail_ref": "reasoning: raw private analysis",
            },
        )


def test_upgrade_backfills_legacy_event_envelopes_and_run_sequence(
    migrated_postgres_database: str,
) -> None:
    """Upgrade a populated 01 schema without losing replay or sequence safety."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migrated_postgres_database)
    engine = create_engine(migrated_postgres_database)
    run_id = "run_event_log_legacy"
    timestamp = datetime(2026, 7, 17, 8, tzinfo=UTC)
    legacy_run = _run(run_id)
    legacy_events = (
        {
            "schema_version": 1,
            "event_id": "evt_legacy_one",
            "run_id": run_id,
            "conversation_id": "conv_event_log",
            "sequence": 1,
            "occurred_at": timestamp.isoformat(),
            "type": "transition.applied",
            "stage": "INTAKE",
            "node_id": "legacy-event-service",
            "status": "accepted",
            "summary": "Legacy event one was accepted",
            "detail_ref": "reasoning: legacy raw event detail",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "span_id": "0123456789abcdef",
        },
        {
            "schema_version": 1,
            "event_id": "evt_legacy_four",
            "run_id": run_id,
            "conversation_id": "conv_event_log",
            "sequence": 4,
            "occurred_at": timestamp.isoformat(),
            "type": "transition.applied",
            "stage": "INTAKE",
            "node_id": "legacy-event-service",
            "status": "accepted",
            "summary": "Legacy event four was accepted",
            "detail_ref": (f"/api/v1/runs/{run_id}/events/evt_legacy_four/detail"),
            "trace_id": "0123456789abcdef0123456789abcdef",
            "span_id": "0123456789abcdef",
        },
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE runs CASCADE"))
        command.downgrade(config, "20260717_01")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO runs ("
                    "run_id, tenant_id, record_version, created_at, updated_at, "
                    "idempotency_key, trace_id, payload"
                    ") VALUES ("
                    ":run_id, :tenant_id, :record_version, :created_at, :updated_at, "
                    ":idempotency_key, :trace_id, CAST(:payload AS jsonb)"
                    ")"
                ),
                {
                    "run_id": legacy_run.run_id,
                    "tenant_id": legacy_run.tenant_id,
                    "record_version": legacy_run.metadata.record_version,
                    "created_at": legacy_run.metadata.created_at,
                    "updated_at": legacy_run.metadata.updated_at,
                    "idempotency_key": legacy_run.metadata.idempotency_key,
                    "trace_id": legacy_run.metadata.trace_id,
                    "payload": json.dumps(
                        legacy_run.model_dump(mode="json"), separators=(",", ":")
                    ),
                },
            )
            for legacy_event in legacy_events:
                connection.execute(
                    text(
                        "INSERT INTO run_events ("
                        "event_id, run_id, sequence, event_type, occurred_at, payload"
                        ") VALUES ("
                        ":event_id, :run_id, :sequence, :event_type, :occurred_at, "
                        "CAST(:payload AS jsonb)"
                        ")"
                    ),
                    {
                        "event_id": legacy_event["event_id"],
                        "run_id": run_id,
                        "sequence": legacy_event["sequence"],
                        "event_type": legacy_event["type"],
                        "occurred_at": timestamp,
                        "payload": json.dumps(legacy_event, separators=(",", ":")),
                    },
                )
        command.upgrade(config, "head")

        unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
        try:
            with unit_of_work.transaction() as transaction:
                replay = transaction.events.replay(run_id=run_id, after_sequence=0)
                upgraded_run = transaction.runs.get(run_id)
                run_row = (
                    transaction._require_connection()
                    .execute(
                        text(
                            "SELECT record_version, updated_at, next_event_sequence "
                            "FROM runs WHERE run_id = :run_id"
                        ),
                        {"run_id": run_id},
                    )
                    .one()
                )
                legacy_detail_ref = (
                    transaction._require_connection()
                    .execute(
                        text(
                            "SELECT detail_ref FROM run_events "
                            "WHERE event_id = 'evt_legacy_one'"
                        )
                    )
                    .scalar_one()
                )

            assert [event.sequence for event in replay] == [1, 4]
            assert all(
                event.attempt_id.startswith("attempt_legacy_") for event in replay
            )
            assert all(
                event.design_version == event.packet_version == 1 for event in replay
            )
            assert all(event.actor_role == "legacy-event-log" for event in replay)
            assert all(event.outcome == "accepted" for event in replay)
            assert replay[0].detail_ref is None
            assert replay[1].detail_ref == (
                f"/api/v1/runs/{run_id}/events/evt_legacy_four/detail"
            )
            assert legacy_detail_ref is None
            assert upgraded_run is not None
            assert upgraded_run.metadata.record_version == run_row.record_version == 2
            assert upgraded_run.metadata.updated_at == run_row.updated_at
            assert run_row.next_event_sequence == 5

            next_event = DurableEventService(unit_of_work, _RecordingNotifier()).apply(
                _draft("legacy_after_upgrade", run_id=run_id), lambda _: None
            )
            assert next_event.sequence == 5
        finally:
            unit_of_work.close()
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE runs CASCADE"))
        engine.dispose()


def test_concurrent_writers_allocate_unique_monotonic_run_sequences(
    migrated_postgres_database: str,
) -> None:
    run_id = "run_event_log_concurrent"
    setup_uow = PostgresUnitOfWork(migrated_postgres_database)
    _seed_run(setup_uow, run_id)
    setup_uow.close()
    barrier = Barrier(8)

    def write_event(index: int) -> int:
        unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
        try:
            service = DurableEventService(unit_of_work, _RecordingNotifier())
            barrier.wait()
            return service.apply(
                _draft(f"concurrent_{index}", run_id=run_id), lambda _: None
            ).sequence
        finally:
            unit_of_work.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(write_event, range(8)))

    assert len(set(sequences)) == 8
    assert sorted(sequences) == sorted(set(sequences))
    assert min(sequences) >= 1


def test_concurrent_duplicate_command_runs_state_change_once(
    migrated_postgres_database: str,
) -> None:
    """Only the transaction that reserves a command may execute its mutation."""
    run_id = "run_event_log_duplicate_command"
    setup_uow = PostgresUnitOfWork(migrated_postgres_database)
    _seed_run(setup_uow, run_id)
    setup_uow.close()
    start = Barrier(2)
    state_change_count = 0
    count_lock = Lock()

    def apply_duplicate() -> EventEnvelope:
        nonlocal state_change_count
        unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
        try:
            service = DurableEventService(unit_of_work, _RecordingNotifier())

            def state_change(_: PostgresUnitOfWork) -> None:
                nonlocal state_change_count
                with count_lock:
                    state_change_count += 1
                # Keep the owning transaction open so the duplicate must race
                # against PostgreSQL's command reservation, not test timing.
                sleep(0.2)

            start.wait()
            return service.apply(
                _draft("duplicate_command", run_id=run_id), state_change
            )
        finally:
            unit_of_work.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(apply_duplicate)
        second_future = executor.submit(apply_duplicate)
        first = first_future.result()
        second = second_future.result()

    assert state_change_count == 1
    assert first == second
    verification_uow = PostgresUnitOfWork(migrated_postgres_database)
    try:
        with verification_uow.transaction() as unit_of_work:
            assert unit_of_work.events.replay(run_id=run_id, after_sequence=0) == (
                first,
            )
    finally:
        verification_uow.close()


def test_replay_is_authorized_stable_ordered_and_idempotent(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    run_id = "run_event_log_replay"
    _seed_run(postgres_uow, run_id)
    service = DurableEventService(postgres_uow, _RecordingNotifier())
    first = service.apply(_draft("replay_first", run_id=run_id), lambda _: None)
    second = service.apply(_draft("replay_second", run_id=run_id), lambda _: None)

    replay = service.replay(
        run_id=run_id,
        after_sequence=first.sequence,
        authorizer=_AllowedReplay(),
    )
    repeated_replay = service.replay(
        run_id=run_id,
        after_sequence=first.sequence,
        authorizer=_AllowedReplay(),
    )

    assert replay == repeated_replay == (second,)
    assert (
        service.apply(_draft("replay_second", run_id=run_id), lambda _: None) == second
    )
    with pytest.raises(ReplayAccessDeniedError):
        service.replay(
            run_id=run_id,
            after_sequence=0,
            authorizer=_DeniedReplay(),
        )


def test_post_commit_notification_wakes_postgres_listeners(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    run_id = "run_event_log_notify"
    _seed_run(postgres_uow, run_id)
    notifier = PostgresEventWakeupNotifier(migrated_postgres_database)
    service = DurableEventService(postgres_uow, notifier)
    psycopg_url = migrated_postgres_database.replace(
        "postgresql+psycopg://", "postgresql://"
    )
    try:
        with psycopg.connect(psycopg_url, autocommit=True) as listener:
            listener.execute("LISTEN orchestrator_run_events")
            service.apply(_draft("notify", run_id=run_id), lambda _: None)
            notification = next(listener.notifies(timeout=5))
    finally:
        notifier.close()

    assert notification.channel == "orchestrator_run_events"
    assert notification.payload == run_id
