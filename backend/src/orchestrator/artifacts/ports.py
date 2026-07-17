"""Storage and metadata ports for versioned artifact content."""

from __future__ import annotations

from typing import Protocol

from orchestrator.artifacts.models import ArtifactVersionRecord
from orchestrator.domain.primitives import ArtifactId, ArtifactVersion


class ArtifactError(Exception):
    """Base exception for a rejected artifact operation."""


class ArtifactVersionConflictError(ArtifactError):
    """The supplied expected version is not the current artifact version."""


class ArtifactNotFoundError(ArtifactError):
    """The requested immutable artifact version does not exist."""


class ArtifactAccessDeniedError(ArtifactError):
    """The supplied tenant, run, or role cannot read the artifact."""


class ArtifactPolicyError(ArtifactError):
    """Content or metadata violates the deterministic artifact policy."""


class ArtifactIntegrityError(ArtifactError):
    """Stored bytes do not match the authoritative metadata digest or size."""


class ArtifactContentStore(Protocol):
    """Content adapter contract reusable by local-volume and object stores."""

    def put_if_absent(self, record: ArtifactVersionRecord, content: bytes) -> bool:
        """Atomically make exact content readable; return whether this call created it."""

    def read(self, record: ArtifactVersionRecord) -> bytes:
        """Read the bytes addressed by internally-derived artifact metadata."""

    def delete_if_matches(self, record: ArtifactVersionRecord) -> None:
        """Best-effort cleanup of an unreferenced content object created by this request."""


class ArtifactMetadataRepository(Protocol):
    """Metadata port with atomic expected-version publication."""

    def latest_version(self, artifact_id: ArtifactId) -> ArtifactVersion:
        """Return the current version, or zero before any version is published."""

    def publish(self, record: ArtifactVersionRecord, *, expected_version: int) -> None:
        """Atomically publish metadata only when the expected version is current."""

    def get(
        self, artifact_id: ArtifactId, version: ArtifactVersion
    ) -> ArtifactVersionRecord | None:
        """Find immutable metadata for one exact version."""
