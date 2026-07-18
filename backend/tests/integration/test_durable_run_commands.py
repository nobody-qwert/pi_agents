"""PostgreSQL-backed command, queue, ownership, and SSE integration proof."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from orchestrator.approvals import PostgresApprovalService
from orchestrator.commands import CommandError, PostgresRunCommandService
from orchestrator.egress_proxy_main import EgressAuditStore
from orchestrator.model_gateway import (
    CancellationToken,
    ModelReadiness,
    ModelRequest,
    ModelResponse,
)
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog
from orchestrator.runner import PostgresRunLeaseQueue, RunnerCoordinator, StageResult
from orchestrator.sse import PostgresEventStreamStore, SseEventService


class ReadyGateway:
    def readiness(
        self, *, cancellation: CancellationToken | None = None
    ) -> ModelReadiness:
        return ModelReadiness(status="ready", configured_model_id="qwen3.6-27b")

    def complete(
        self,
        request: ModelRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ModelResponse:
        raise AssertionError("commands do not invoke the model")


def _service(
    database_url: str, tmp_path: Path
) -> tuple[PostgresRunCommandService, PostgresUnitOfWork, str]:
    root = tmp_path / "projects"
    project = root / "example"
    project.mkdir(parents=True)
    (project / "README.md").write_text("durable command")
    catalog = ProjectCatalog((root,))
    unit_of_work = PostgresUnitOfWork(database_url)
    return (
        PostgresRunCommandService(catalog, ReadyGateway(), unit_of_work),
        unit_of_work,
        catalog.discover()[0].project_id,
    )


def test_create_is_atomic_owned_queued_audited_and_restart_durable(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    commands, unit_of_work, project_id = _service(migrated_postgres_database, tmp_path)
    created = commands.create(
        user_id="user_command",
        project_id=project_id,
        message="Build the durable path",
        idempotency_key="create-command",
    )
    replay = SseEventService(PostgresEventStreamStore(unit_of_work)).replay(
        run_id=created.run_id, user_id="user_command", after_sequence=0
    )

    assert created.status == "queued"
    assert created.conversation_id is not None
    assert [event.event_type for event in replay] == ["run.created"]
    with unit_of_work.transaction() as transaction:
        row = transaction.connection.execute(
            text(
                "SELECT queue.max_attempts, message.content FROM runs "
                "JOIN run_queue AS queue USING (run_id) "
                "JOIN messages AS message USING (conversation_id) "
                "WHERE run_id = :run_id"
            ),
            {"run_id": created.run_id},
        ).one()
    assert row.max_attempts == 3
    assert row.content == "Build the durable path"

    restarted, restarted_uow, _ = _service(
        migrated_postgres_database, tmp_path / "restart"
    )
    try:
        assert restarted.get(run_id=created.run_id, user_id="user_command") == created
        assert (
            restarted.create(
                user_id="user_command",
                project_id=project_id,
                message="Build the durable path",
                idempotency_key="create-command",
            )
            == created
        )
        with pytest.raises(CommandError, match="run_not_found"):
            restarted.get(run_id=created.run_id, user_id="user_other")
    finally:
        restarted_uow.close()
        unit_of_work.close()


def test_cancel_is_durable_idempotent_and_visible_in_replay(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    commands, unit_of_work, project_id = _service(migrated_postgres_database, tmp_path)
    try:
        created = commands.create(
            user_id="user_cancel",
            project_id=project_id,
            message="Cancel safely",
            idempotency_key="create-cancel",
        )
        cancelled = commands.cancel(
            run_id=created.run_id,
            user_id="user_cancel",
            idempotency_key="cancel-command",
        )
        replay = SseEventService(PostgresEventStreamStore(unit_of_work)).replay(
            run_id=created.run_id, user_id="user_cancel", after_sequence=0
        )

        assert cancelled.status == "cancelled"
        assert (
            commands.cancel(
                run_id=created.run_id,
                user_id="user_cancel",
                idempotency_key="cancel-command",
            )
            == cancelled
        )
        assert [event.event_type for event in replay] == [
            "run.created",
            "run.cancel_requested",
        ]
        with pytest.raises(CommandError, match="run_not_found"):
            commands.cancel(
                run_id=created.run_id,
                user_id="user_other",
                idempotency_key="cross-owner",
            )
    finally:
        unit_of_work.close()


def test_egress_budget_reservation_is_atomic_and_durably_audited(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    commands, unit_of_work, project_id = _service(migrated_postgres_database, tmp_path)
    audit = EgressAuditStore(migrated_postgres_database)
    try:
        created = commands.create(
            user_id="user_egress",
            project_id=project_id,
            message="Audit bounded guest egress",
            idempotency_key="create-egress",
        )
        assert audit.reserve(
            request_id="egress_allowed",
            run_id=created.run_id,
            hostname="example.com",
            port=443,
            scheme="https",
            budget_bytes=10,
        ) == "allowed"
        audit.classify(
            "egress_allowed",
            decision="allowed",
            reason_code="policy_allowed",
            hostname="example.com",
            port=443,
            scheme="https",
            resolved_ips=("93.184.216.34",),
        )
        audit.finish(
            "egress_allowed", bytes_up=4, bytes_down=6, failed=False
        )
        assert audit.reserve(
            request_id="egress_denied",
            run_id=created.run_id,
            hostname="example.com",
            port=443,
            scheme="https",
            budget_bytes=10,
        ) == "budget_exhausted"
        audit.finish("egress_denied", bytes_up=0, bytes_down=0, failed=False)
        with unit_of_work.transaction() as transaction:
            rows = transaction.connection.execute(
                text(
                    "SELECT decision, reason_code, bytes_up, bytes_down "
                    "FROM egress_requests WHERE run_id = :run_id ORDER BY request_id"
                ),
                {"run_id": created.run_id},
            ).all()
        assert [tuple(row) for row in rows] == [
            ("allowed", "policy_allowed", 4, 6),
            ("denied", "run_budget_exhausted", 0, 0),
        ]
    finally:
        audit.close()
        unit_of_work.close()


def test_approval_request_and_authenticated_decision_are_atomic_and_audited(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    commands, unit_of_work, project_id = _service(migrated_postgres_database, tmp_path)
    try:
        created = commands.create(
            user_id="user_approval",
            project_id=project_id,
            message="Require an authority decision",
            idempotency_key="create-approval",
        )
        approvals = PostgresApprovalService(unit_of_work)
        approval_id = approvals.request(
            run_id=created.run_id,
            authority="transition",
            affected_versions=("1",),
            idempotency_key="request-transition",
        )
        with unit_of_work.transaction() as transaction:
            version_binding = transaction.connection.execute(
                text(
                    "SELECT request.requested_record_version, "
                    "(runs.payload -> 'metadata' ->> 'record_version')::integer "
                    "FROM approval_requests AS request JOIN runs USING (run_id) "
                    "WHERE request.approval_id = :approval_id"
                ),
                {"approval_id": approval_id},
            ).one()
        assert tuple(version_binding) == (4, 4)
        result = approvals.decide(
            run_id=created.run_id,
            approval_id=approval_id,
            user_id="user_approval",
            decision="approved",
            comment="Proceed",
            idempotency_key="approve-transition",
        )
        replay = SseEventService(PostgresEventStreamStore(unit_of_work)).replay(
            run_id=created.run_id, user_id="user_approval", after_sequence=0
        )

        assert result["status"] == "approved"
        assert [event.event_type for event in replay] == [
            "run.created",
            "approval.requested",
            "approval.recorded",
        ]
        assert (
            approvals.decide(
                run_id=created.run_id,
                approval_id=approval_id,
                user_id="user_approval",
                decision="approved",
                comment="Proceed",
                idempotency_key="approve-transition-replay",
            )
            == result
        )
        with pytest.raises(CommandError, match="approval_already_decided"):
            approvals.decide(
                run_id=created.run_id,
                approval_id=approval_id,
                user_id="user_approval",
                decision="rejected",
                comment=None,
                idempotency_key="reject-conflict",
            )
        with pytest.raises(CommandError, match="approval_not_found"):
            approvals.decide(
                run_id=created.run_id,
                approval_id=approval_id,
                user_id="user_other",
                decision="approved",
                comment=None,
                idempotency_key="cross-user",
            )

        expired_id = approvals.request(
            run_id=created.run_id,
            authority="workspace",
            affected_versions=("1",),
            idempotency_key="request-expired",
        )
        with unit_of_work.transaction() as transaction:
            transaction.connection.execute(
                text(
                    "UPDATE approval_requests SET expires_at = :expired "
                    "WHERE approval_id = :approval_id"
                ),
                {
                    "expired": datetime.now(UTC) - timedelta(seconds=1),
                    "approval_id": expired_id,
                },
            )
        with pytest.raises(CommandError, match="approval_expired"):
            approvals.decide(
                run_id=created.run_id,
                approval_id=expired_id,
                user_id="user_approval",
                decision="approved",
                comment=None,
                idempotency_key="expired-decision",
            )

        stale_id = approvals.request(
            run_id=created.run_id,
            authority="completion",
            affected_versions=("1",),
            idempotency_key="request-stale",
        )
        with unit_of_work.transaction() as transaction:
            transaction.connection.execute(
                text(
                    "UPDATE runs SET record_version = record_version + 1, payload = "
                    "jsonb_set(payload, '{metadata,record_version}', "
                    "to_jsonb(record_version + 1)) "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": created.run_id},
            )
        with pytest.raises(CommandError, match="approval_stale"):
            approvals.decide(
                run_id=created.run_id,
                approval_id=stale_id,
                user_id="user_approval",
                decision="approved",
                comment=None,
                idempotency_key="stale-decision",
            )
    finally:
        unit_of_work.close()


def test_runner_events_keep_the_command_conversation_identity(
    migrated_postgres_database: str, tmp_path: Path
) -> None:
    commands, unit_of_work, project_id = _service(migrated_postgres_database, tmp_path)
    queue = PostgresRunLeaseQueue(
        migrated_postgres_database, lease_duration=timedelta(seconds=30)
    )
    try:
        created = commands.create(
            user_id="user_conversation",
            project_id=project_id,
            message="Keep one conversation",
            idempotency_key="create-conversation",
        )
        claim = queue.claim(created.run_id, owner="runner-conversation")
        assert claim.lease is not None

        RunnerCoordinator(unit_of_work, queue).advance(
            stage="INTAKE", result=StageResult("accepted"), lease=claim.lease
        )
        replay = SseEventService(PostgresEventStreamStore(unit_of_work)).replay(
            run_id=created.run_id, user_id="user_conversation", after_sequence=0
        )

        assert len(replay) == 2
        assert {event.payload["conversation_id"] for event in replay} == {
            created.conversation_id
        }
    finally:
        queue.close()
        unit_of_work.close()
