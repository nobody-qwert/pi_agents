"""Unit coverage for PostgreSQL-specific payload restoration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from orchestrator.domain import (
    ArtifactRecord,
    AuthenticatedActor,
    EventDraft,
    RecordMetadata,
    RunRecord,
)
from orchestrator.persistence import ConcurrentWriteError
from orchestrator.persistence.postgres import (
    PostgresAuthoritativeRepository,
    PostgresRunEventRepository,
    _RepositoryDefinition,
)


def test_get_restores_jsonb_timestamps_without_relaxing_domain_validation() -> None:
    run = RunRecord(
        metadata=RecordMetadata(
            record_version=1,
            created_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
            updated_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
            idempotency_key="test:jsonb",
            trace_id="0123456789abcdef0123456789abcdef",
        ),
        run_id="run_jsonb",
        tenant_id="tenant_jsonb",
        outcome="Restore persisted timestamps",
        current_gate="INTAKE",
        risk_class="low",
        status="created",
    )
    postgres_payload = run.model_dump(mode="json")

    with pytest.raises(ValidationError):
        RunRecord.model_validate(postgres_payload)

    connection = Mock()
    connection.execute.return_value.scalar.return_value = postgres_payload
    repository = PostgresAuthoritativeRepository(
        connection,
        _RepositoryDefinition("runs", "run_id", RunRecord),
    )

    assert repository.get(run.run_id) == run


def test_artifact_get_preserves_authenticated_producer_details() -> None:
    metadata = RecordMetadata(
        record_version=1,
        created_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
        updated_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
        idempotency_key="artifact:jsonb",
        trace_id="0123456789abcdef0123456789abcdef",
    )
    artifact = ArtifactRecord(
        metadata=metadata,
        artifact_id="art_jsonb",
        run_id="run_jsonb",
        logical_name="JSONB artifact",
        version=1,
        media_type="application/json",
        storage_locator="artifacts/jsonb.json",
        sha256="a" * 64,
        producer=AuthenticatedActor(
            actor_id="service_persistence",
            kind="service",
            role="persistence-service",
            authenticated_at=metadata.created_at,
            authentication_context="test-mtls",
        ),
        access_policy=("operator",),
    )
    connection = Mock()
    connection.execute.return_value.scalar.return_value = artifact.model_dump(
        mode="json"
    )
    repository = PostgresAuthoritativeRepository(
        connection,
        _RepositoryDefinition("artifacts", "artifact_id", ArtifactRecord),
    )

    restored = repository.get(artifact.artifact_id)

    assert restored == artifact
    assert isinstance(restored.producer, AuthenticatedActor)


def test_compare_and_swap_requires_the_next_record_version() -> None:
    connection = Mock()
    repository = PostgresAuthoritativeRepository(
        connection,
        _RepositoryDefinition("runs", "run_id", RunRecord),
    )
    replacement = RunRecord(
        metadata=RecordMetadata(
            record_version=3,
            created_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
            updated_at=datetime(2026, 7, 17, 10, tzinfo=UTC),
            idempotency_key="test:version-jump",
            trace_id="0123456789abcdef0123456789abcdef",
        ),
        run_id="run_jsonb",
        tenant_id="tenant_jsonb",
        outcome="Reject version jumps before PostgreSQL execution",
        current_gate="INTAKE",
        risk_class="low",
        status="created",
    )

    with pytest.raises(ConcurrentWriteError, match="exactly one greater"):
        repository.compare_and_swap(replacement, expected_record_version=1)

    connection.execute.assert_not_called()


def test_event_sequence_allocation_advances_the_run_record_version() -> None:
    connection = MagicMock()
    connection.execute.return_value.scalar.side_effect = [None, 1]
    repository = PostgresRunEventRepository(connection)
    draft = EventDraft(
        event_id="evt_record_version",
        run_id="run_record_version",
        conversation_id="conv_record_version",
        occurred_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
        type="transition.applied",
        stage="INTAKE",
        node_id="event-service",
        attempt_id="attempt_record_version",
        design_version=1,
        packet_version=1,
        actor_role="event-service",
        status="accepted",
        outcome="accepted",
        summary="Allocate an event sequence with a versioned run update",
        detail_ref="/api/v1/runs/run_record_version/events/evt_record_version/detail",
        correlation_id="command-record-version",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="0123456789abcdef",
        command_idempotency_key="command:record-version",
    )

    repository.append(draft)

    allocation = connection.execute.call_args_list[1].args[0]
    assert "next_event_sequence = next_event_sequence + 1" in allocation.text
    assert "record_version = record_version + 1" in allocation.text
    assert "updated_at = stamp.value" in allocation.text
    assert "'record_version', record_version + 1" in allocation.text
    assert "'updated_at', stamp.value" in allocation.text
