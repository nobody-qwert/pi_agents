"""In-memory and PostgreSQL adapters for immutable artifact metadata."""

from __future__ import annotations

import json
from threading import Lock

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from orchestrator.artifacts.models import ArtifactVersionRecord
from orchestrator.artifacts.ports import (
    ArtifactVersionConflictError,
)
from orchestrator.domain.primitives import ArtifactId, ArtifactVersion


class InMemoryArtifactMetadataRepository:
    """Contract-test metadata adapter with the same expected-version semantics."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], ArtifactVersionRecord] = {}
        self._latest: dict[str, int] = {}
        self._lock = Lock()

    def latest_version(self, artifact_id: ArtifactId) -> ArtifactVersion:
        with self._lock:
            return self._latest.get(artifact_id, 0)

    def publish(self, record: ArtifactVersionRecord, *, expected_version: int) -> None:
        with self._lock:
            latest = self._latest.get(record.artifact_id, 0)
            if expected_version != latest or record.version != latest + 1:
                raise ArtifactVersionConflictError(
                    f"artifact {record.artifact_id!r} expected v{expected_version}, "
                    f"current version is v{latest}"
                )
            self._records[(record.artifact_id, record.version)] = record
            self._latest[record.artifact_id] = record.version

    def get(
        self, artifact_id: ArtifactId, version: ArtifactVersion
    ) -> ArtifactVersionRecord | None:
        with self._lock:
            return self._records.get((artifact_id, version))


class PostgresArtifactMetadataRepository:
    """PostgreSQL metadata adapter with per-logical-artifact optimistic locking."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url, pool_pre_ping=True)

    def close(self) -> None:
        """Release the connection pool owned by this adapter."""
        self._engine.dispose()

    def latest_version(self, artifact_id: ArtifactId) -> ArtifactVersion:
        with self._engine.connect() as connection:
            version = connection.execute(
                text(
                    "SELECT current_version FROM artifact_version_heads "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": artifact_id},
            ).scalar()
        return int(version) if version is not None else 0

    def publish(self, record: ArtifactVersionRecord, *, expected_version: int) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO artifact_version_heads (artifact_id, current_version) "
                        "VALUES (:artifact_id, 0) ON CONFLICT (artifact_id) DO NOTHING"
                    ),
                    {"artifact_id": record.artifact_id},
                )
                latest = connection.execute(
                    text(
                        "SELECT current_version FROM artifact_version_heads "
                        "WHERE artifact_id = :artifact_id FOR UPDATE"
                    ),
                    {"artifact_id": record.artifact_id},
                ).scalar_one()
                if expected_version != latest or record.version != latest + 1:
                    raise ArtifactVersionConflictError(
                        f"artifact {record.artifact_id!r} expected v{expected_version}, "
                        f"current version is v{latest}"
                    )
                connection.execute(
                    text(
                        "INSERT INTO artifact_versions ("
                        "artifact_id, version, tenant_id, run_id, media_type, sha256, "
                        "size_bytes, storage_key, scope, created_at, payload"
                        ") VALUES ("
                        ":artifact_id, :version, :tenant_id, :run_id, :media_type, :sha256, "
                        ":size_bytes, :storage_key, CAST(:scope AS jsonb), :created_at, "
                        "CAST(:payload AS jsonb)"
                        ")"
                    ),
                    _record_values(record),
                )
                connection.execute(
                    text(
                        "UPDATE artifact_version_heads SET current_version = :version "
                        "WHERE artifact_id = :artifact_id"
                    ),
                    {"artifact_id": record.artifact_id, "version": record.version},
                )
        except IntegrityError as error:
            raise ArtifactVersionConflictError(
                f"artifact {record.artifact_id!r} version {record.version} already exists"
            ) from error

    def get(
        self, artifact_id: ArtifactId, version: ArtifactVersion
    ) -> ArtifactVersionRecord | None:
        with self._engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload FROM artifact_versions "
                    "WHERE artifact_id = :artifact_id AND version = :version"
                ),
                {"artifact_id": artifact_id, "version": version},
            ).scalar()
        if payload is None:
            return None
        return ArtifactVersionRecord.model_validate_json(
            json.dumps(payload, separators=(",", ":"))
        )


def _record_values(record: ArtifactVersionRecord) -> dict[str, object]:
    return {
        "artifact_id": record.artifact_id,
        "version": record.version,
        "tenant_id": record.scope.tenant_id,
        "run_id": record.scope.run_id,
        "media_type": record.media_type,
        "sha256": record.content_sha256,
        "size_bytes": record.size_bytes,
        "storage_key": record.storage_key,
        "scope": json.dumps(
            record.scope.model_dump(mode="json"), separators=(",", ":")
        ),
        "created_at": record.created_at,
        "payload": record.model_dump_json(),
    }
