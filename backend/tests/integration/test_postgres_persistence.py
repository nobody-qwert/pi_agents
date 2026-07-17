"""Acceptance coverage for PostgreSQL migrations and authoritative repositories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

from orchestrator.domain import (
    AcceptanceCheck,
    ApprovalRecord,
    ArtifactPointer,
    ArtifactRecord,
    AuthenticatedActor,
    AuthoritativeRecord,
    AuthorityGrant,
    CharterRecord,
    CheckpointRecord,
    DesignRevision,
    EvidenceRecord,
    IssueContract,
    IssueRecord,
    OutputArtifactSpecification,
    OutputContract,
    PacketAcceptanceCriterion,
    PacketRecord,
    PromotionRecord,
    RecordMetadata,
    RunCompletionRecord,
    RunRecord,
    TransitionRecord,
    WorkNodeRecord,
    WorkspaceRecord,
)
from orchestrator.persistence import (
    ConcurrentWriteError,
    DuplicateIdempotencyKeyError,
    DuplicateRecordError,
    PostgresUnitOfWork,
    RepositoryConstraintError,
)


def _metadata(
    version: int = 1, *, idempotency_key: str | None = None
) -> RecordMetadata:
    created_at = datetime(2026, 7, 17, 8, tzinfo=UTC)
    return RecordMetadata(
        record_version=version,
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=version - 1),
        idempotency_key=idempotency_key or f"test:{version}",
        trace_id="0123456789abcdef0123456789abcdef",
    )


def _run(
    version: int = 1,
    *,
    run_id: str = "run_persistence",
    idempotency_key: str | None = None,
) -> RunRecord:
    return RunRecord(
        metadata=_metadata(version, idempotency_key=idempotency_key),
        run_id=run_id,
        tenant_id="tenant_persistence",
        outcome="Persist authoritative records",
        current_gate="INTAKE",
        risk_class="low",
        status="created",
    )


def _work_node() -> WorkNodeRecord:
    return WorkNodeRecord(
        metadata=_metadata(),
        work_node_id="wn_persistence",
        run_id="run_persistence",
        node_type="LEAF_TASK",
        goal="Exercise PostgreSQL mapping",
        owner_role="repository-test",
        status="READY",
        design_refs=(),
        outputs=("repository test evidence",),
        acceptance_criterion_ids=("criterion_persistence",),
    )


def _packet() -> PacketRecord:
    return PacketRecord(
        metadata=_metadata(),
        packet_id="pkt_persistence",
        run_id="run_persistence",
        task_id="task_persistence",
        work_node_id="wn_persistence",
        task_type="LEAF_TASK",
        goal="Persist type and audit data",
        design_baseline=(),
        acceptance_criteria=(
            PacketAcceptanceCriterion(
                criterion_id="criterion_persistence",
                observable_result="Stored packet round-trips exactly",
            ),
        ),
        output_artifacts=(
            OutputArtifactSpecification(
                path="backend/tests/integration/test_postgres_persistence.py",
                required_form="PostgreSQL integration evidence",
            ),
        ),
        acceptance_checks=(
            AcceptanceCheck(
                method="command",
                procedure="pytest integration",
                evidence="passing result",
            ),
        ),
        authority_limits=("Do not embed business policy in repositories",),
        issue_contract=IssueContract(
            report_evidence="Report database failures",
            proposed_classifications=("ENVIRONMENT_BLOCKER",),
        ),
        output_contract=OutputContract(
            status="completed",
            outputs="repository records",
            checks="integration checks",
            risks="none",
            issues="none",
            design_version_used=1,
        ),
    )


def _all_authoritative_records() -> tuple[tuple[str, str, AuthoritativeRecord], ...]:
    metadata = _metadata()
    service_actor = AuthenticatedActor(
        actor_id="service_persistence",
        kind="service",
        role="persistence-service",
        authenticated_at=metadata.created_at,
        authentication_context="test-mtls",
    )
    user_actor = AuthenticatedActor(
        actor_id="user_persistence",
        kind="human",
        role="operator",
        authenticated_at=metadata.created_at,
        authentication_context="test-session",
    )
    artifact = ArtifactRecord(
        metadata=metadata,
        artifact_id="art_persistence",
        run_id="run_persistence",
        work_node_id="wn_persistence",
        logical_name="persistence-evidence",
        version=1,
        media_type="application/json",
        storage_locator="artifacts/persistence-evidence.json",
        sha256="a" * 64,
        producer=service_actor,
        access_policy=("operator",),
    )
    artifact_pointer = ArtifactPointer(
        artifact_id=artifact.artifact_id,
        version=artifact.version,
        purpose="round-trip dependency",
    )
    authority = AuthorityGrant(
        scope="design",
        source="persistence integration test",
        granted_at=metadata.created_at,
    )
    return (
        ("runs", "run_id", _run()),
        (
            "charters",
            "charter_id",
            CharterRecord(
                metadata=metadata,
                charter_id="charter_persistence",
                run_id="run_persistence",
                requested_outcome="Exercise every authoritative mapping",
                intended_users=("operators",),
                included_scope=("repository coverage",),
                excluded_scope=("business policy",),
                acceptance_criteria=(),
                risk_class="low",
                evidence_expectations=("PostgreSQL round trip",),
                accepted_by=user_actor,
            ),
        ),
        (
            "work_nodes",
            "work_node_id",
            _work_node(),
        ),
        ("artifacts", "artifact_id", artifact),
        (
            "design_revisions",
            "design_revision_id",
            DesignRevision(
                metadata=metadata,
                design_revision_id="design_persistence",
                run_id="run_persistence",
                design_version=1,
                design_artifact_id=artifact.artifact_id,
                accepted_by=user_actor,
            ),
        ),
        ("packets", "packet_id", _packet()),
        (
            "evidence",
            "evidence_id",
            EvidenceRecord(
                metadata=metadata,
                evidence_id="evidence_persistence",
                run_id="run_persistence",
                work_node_id="wn_persistence",
                criterion_id="criterion_persistence",
                result="passed",
                summary="Every authoritative aggregate survives persistence",
                supporting_artifacts=(artifact_pointer,),
                verifier=service_actor,
                design_version=1,
            ),
        ),
        (
            "issues",
            "issue_id",
            IssueRecord(
                metadata=metadata,
                issue_id="issue_persistence",
                run_id="run_persistence",
                reporter=service_actor,
                affected_work_node_ids=("wn_persistence",),
                observed_evidence="No issue observed",
                expected_result="All mappings round trip",
                actual_result="All mappings round trip",
                classification="LOCAL_DEFECT",
                severity="info",
                blocking=False,
                design_version=1,
            ),
        ),
        (
            "approvals",
            "approval_id",
            ApprovalRecord(
                metadata=metadata,
                approval_id="approval_persistence",
                run_id="run_persistence",
                approver=user_actor,
                authority=authority,
                decision="approved",
                decided_at=metadata.created_at,
                affected_record_version=1,
            ),
        ),
        (
            "workspace_sessions",
            "workspace_id",
            WorkspaceRecord(
                metadata=metadata,
                workspace_id="workspace_persistence",
                run_id="run_persistence",
                selected_source="project_persistence",
                source_fingerprint="b" * 64,
                guest_identity="guest-persistence",
                guest_path="workspace/persistence",
                status="ready",
            ),
        ),
        (
            "workspace_checkpoints",
            "checkpoint_id",
            CheckpointRecord(
                metadata=metadata,
                checkpoint_id="checkpoint_persistence",
                workspace_id="workspace_persistence",
                run_id="run_persistence",
                work_node_id="wn_persistence",
                commit_hash="c" * 40,
                tree_hash="d" * 40,
                accepted_evidence_ids=("evidence_persistence",),
                recorded_by=service_actor,
            ),
        ),
        (
            "promotions",
            "promotion_id",
            PromotionRecord(
                metadata=metadata,
                promotion_id="promotion_persistence",
                run_id="run_persistence",
                workspace_id="workspace_persistence",
                preview_artifact_id=artifact.artifact_id,
                confirmed_artifact_version=1,
                target_branch="persistence-test",
                status="previewed",
                decided_by=user_actor,
                authority=AuthorityGrant(
                    scope="promotion",
                    source="persistence integration test",
                    granted_at=metadata.created_at,
                ),
                result_summary="Promotion mapping coverage",
            ),
        ),
        (
            "transition_log",
            "transition_id",
            TransitionRecord(
                metadata=metadata,
                transition_id="transition_persistence",
                run_id="run_persistence",
                work_node_id="wn_persistence",
                previous_state="READY",
                next_state="IN_PROGRESS",
                reason="Exercise transition persistence mapping",
                actor=service_actor,
                previous_record_version=1,
                next_record_version=2,
            ),
        ),
        (
            "run_completions",
            "completion_id",
            RunCompletionRecord(
                metadata=metadata,
                completion_id="completion_persistence",
                run_id="run_persistence",
                outcome_evidence_ids=("evidence_persistence",),
                completed_at=metadata.created_at,
                completed_by=service_actor,
                authority=AuthorityGrant(
                    scope="completion",
                    source="persistence integration test",
                    granted_at=metadata.created_at,
                ),
                summary="Completion mapping coverage",
            ),
        ),
    )


def test_migrations_create_initial_durable_tables(
    migrated_postgres_database: str,
) -> None:
    engine = create_engine(migrated_postgres_database)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            agent_attempt_columns = {
                column["name"] for column in inspector.get_columns("agent_attempts")
            }
            agent_attempt_artifact_foreign_keys = {
                foreign_key["constrained_columns"][0]: foreign_key["referred_columns"][
                    0
                ]
                for foreign_key in inspector.get_foreign_keys("agent_attempts")
                if foreign_key["referred_table"] == "artifacts"
            }
    finally:
        engine.dispose()

    assert {
        "users",
        "conversations",
        "messages",
        "runs",
        "run_events",
        "agent_registry_versions",
        "agent_attempts",
        "workspace_sessions",
        "workspace_checkpoints",
        "workspace_transfers",
        "promotion_previews",
        "promotions",
        "design_revisions",
        "work_nodes",
        "work_edges",
        "packets",
        "artifacts",
        "evidence",
        "issues",
        "approvals",
        "transition_log",
    } <= tables
    assert {"input_artifact_id", "result_artifact_id"} <= agent_attempt_columns
    assert agent_attempt_artifact_foreign_keys == {
        "input_artifact_id": "artifact_id",
        "result_artifact_id": "artifact_id",
    }


def test_authoritative_records_round_trip_with_type_version_and_audit_data(
    postgres_uow: PostgresUnitOfWork,
    migrated_postgres_database: str,
) -> None:
    run = _run()
    work_node = _work_node()
    packet = _packet()

    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.runs.add(run)
        unit_of_work.work_nodes.add(work_node)
        unit_of_work.packets.add(packet)
        assert unit_of_work.runs.get(run.run_id) == run
        assert unit_of_work.packets.get(packet.packet_id) == packet

    engine = create_engine(migrated_postgres_database)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT tenant_id FROM runs WHERE run_id = :run_id"),
                    {"run_id": run.run_id},
                ).scalar_one()
                == run.tenant_id
            )
            assert (
                connection.execute(
                    text(
                        "SELECT work_node_id FROM packets WHERE packet_id = :packet_id"
                    ),
                    {"packet_id": packet.packet_id},
                ).scalar_one()
                == packet.work_node_id
            )
    finally:
        engine.dispose()


def test_every_authoritative_aggregate_mapping_round_trips(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    with postgres_uow.transaction() as unit_of_work:
        for repository_name, id_column, record in _all_authoritative_records():
            repository = getattr(unit_of_work, repository_name)
            repository.add(record)
            assert repository.get(getattr(record, id_column)) == record


def test_duplicate_identifiers_and_stale_writes_are_rejected(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    run = _run()
    current = _run(version=2, idempotency_key="run:advance:persistence")

    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.runs.add(run)
        with pytest.raises(DuplicateRecordError):
            unit_of_work.runs.add(run)
        with pytest.raises(ConcurrentWriteError):
            unit_of_work.runs.compare_and_swap(current, expected_record_version=2)
        with pytest.raises(ConcurrentWriteError):
            unit_of_work.runs.compare_and_swap(
                _run(version=3), expected_record_version=1
            )
        unit_of_work.runs.compare_and_swap(current, expected_record_version=1)

    with postgres_uow.transaction() as unit_of_work:
        assert unit_of_work.runs.get(run.run_id) == current
        assert (
            unit_of_work.runs.get_by_idempotency_key("run:advance:persistence")
            == current
        )
        assert unit_of_work.runs.get_by_idempotency_key("test:1") is None


def test_idempotency_keys_are_unique_and_resolve_the_original_record(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    original = _run(idempotency_key="run:create:persistence")
    retry = _run(
        run_id="run_persistence_retry", idempotency_key="run:create:persistence"
    )

    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.runs.add(original)
        assert (
            unit_of_work.runs.get_by_idempotency_key("run:create:persistence")
            == original
        )
        with pytest.raises(DuplicateIdempotencyKeyError):
            unit_of_work.runs.add(retry)


def test_non_duplicate_constraints_remain_distinct_from_duplicate_identifiers(
    postgres_uow: PostgresUnitOfWork,
) -> None:
    run = _run()
    work_node = _work_node()
    packet = _packet()

    with postgres_uow.transaction() as unit_of_work:
        unit_of_work.runs.add(run)
        with pytest.raises(RepositoryConstraintError):
            unit_of_work.packets.add(packet)
        unit_of_work.work_nodes.add(work_node)
        unit_of_work.packets.add(packet)
