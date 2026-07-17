"""PostgreSQL integration tests for immutable artifact metadata."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orchestrator.artifacts import (
    ArtifactVersionConflictError,
    PostgresArtifactMetadataRepository,
)
from orchestrator.artifacts.models import (
    ArtifactScope,
    ArtifactVersionRecord,
    artifact_storage_key,
)
from orchestrator.domain import RecordMetadata, RunRecord
from orchestrator.persistence import PostgresUnitOfWork


def _record(*, version: int = 1) -> ArtifactVersionRecord:
    digest = "a" * 64
    return ArtifactVersionRecord(
        artifact_id="art_metadata",
        version=version,
        scope=ArtifactScope(
            tenant_id="tenant_metadata",
            run_id="run_metadata",
            allowed_roles=("operator",),
        ),
        media_type="application/json",
        content_sha256=digest,
        size_bytes=0,
        storage_key=artifact_storage_key("art_metadata", version, digest),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def test_postgres_metadata_repository_uses_atomic_expected_versions(
    migrated_postgres_database: str,
) -> None:
    run_metadata = RecordMetadata(
        record_version=1,
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        updated_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
    with unit_of_work.transaction() as transaction:
        transaction.runs.add(
            RunRecord(
                metadata=run_metadata,
                run_id="run_metadata",
                tenant_id="tenant_metadata",
                outcome="Test immutable artifact metadata",
                current_gate="INTAKE",
                risk_class="low",
                status="created",
            )
        )
    unit_of_work.close()
    repository = PostgresArtifactMetadataRepository(migrated_postgres_database)
    try:
        first = _record()
        repository.publish(first, expected_version=0)

        assert repository.latest_version("art_metadata") == 1
        assert repository.get("art_metadata", 1) == first

        with pytest.raises(ArtifactVersionConflictError):
            repository.publish(_record(version=2), expected_version=0)
        assert repository.get("art_metadata", 2) is None
    finally:
        repository.close()
