"""Owned immutable artifact reads without exposing storage locators."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from orchestrator.artifacts import ArtifactService
from orchestrator.artifacts.models import ArtifactAccessRequest, ArtifactReference
from orchestrator.commands import CommandError
from orchestrator.persistence import PostgresUnitOfWork


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    artifact_id: str
    version: int
    media_type: str
    sha256: str
    size_bytes: int
    content: bytes

    def projection(self) -> dict[str, object]:
        preview: str | None = None
        preview_truncated = False
        if self.media_type.startswith("text/") or self.media_type == "application/json":
            bounded = self.content[:65_536]
            preview = bounded.decode("utf-8", errors="replace")
            preview_truncated = len(self.content) > len(bounded)
        return {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "preview": preview,
            "preview_truncated": preview_truncated,
        }


class PostgresArtifactApiService:
    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        artifacts: ArtifactService,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._artifacts = artifacts

    def read(self, *, artifact_id: str, user_id: str) -> ArtifactContent:
        with self._unit_of_work.transaction() as unit_of_work:
            record = unit_of_work.artifacts.get(artifact_id)
            if record is None:
                raise CommandError("artifact_not_found")
            owned = unit_of_work.connection.execute(
                text(
                    "SELECT tenant_id FROM runs WHERE run_id = :run_id "
                    "AND user_id = :user_id"
                ),
                {"run_id": record.run_id, "user_id": user_id},
            ).scalar()
        if owned is None:
            raise CommandError("artifact_not_found")
        result = self._artifacts.read(
            ArtifactReference(
                artifact_id=record.artifact_id,
                version=record.version,
            ),
            ArtifactAccessRequest(
                tenant_id=str(owned), run_id=record.run_id, role="operator"
            ),
        )
        return ArtifactContent(
            artifact_id=record.artifact_id,
            version=record.version,
            media_type=result.metadata.media_type,
            sha256=result.metadata.content_sha256,
            size_bytes=result.metadata.size_bytes,
            content=result.content,
        )
