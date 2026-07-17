"""Unit coverage for PostgreSQL-specific payload restoration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from orchestrator.domain import (
    ArtifactRecord,
    AuthenticatedActor,
    RecordMetadata,
    RunRecord,
)
from orchestrator.persistence import ConcurrentWriteError
from orchestrator.persistence.postgres import (
    PostgresAuthoritativeRepository,
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
