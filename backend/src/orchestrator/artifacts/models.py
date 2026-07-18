"""Strict data contracts for the artifact service boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from orchestrator.domain.primitives import (
    ArtifactId,
    ArtifactVersion,
    RunId,
    Sha256Digest,
    StrictDomainModel,
    TenantId,
)

MediaType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$",
    ),
]
ArtifactRole = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    ),
]


class ArtifactScope(StrictDomainModel):
    """The tenant/run boundary and roles allowed to read an artifact."""

    tenant_id: TenantId
    run_id: RunId
    allowed_roles: tuple[ArtifactRole, ...] = Field(min_length=1)

    @field_validator("allowed_roles")
    @classmethod
    def roles_are_unique(cls, roles: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(roles)) != len(roles):
            raise ValueError("artifact scope roles must be unique")
        return roles


class ArtifactPublishRequest(StrictDomainModel):
    """A deterministic service's request to create exactly one new version."""

    artifact_id: ArtifactId
    scope: ArtifactScope
    media_type: MediaType
    expected_version: Annotated[int, Field(ge=0)]
    expected_sha256: Sha256Digest | None = None


class ArtifactReference(StrictDomainModel):
    """An immutable reference to a single published artifact version."""

    artifact_id: ArtifactId
    version: ArtifactVersion


class ArtifactAccessRequest(StrictDomainModel):
    """The context used for a read decision, separate from storage mechanics."""

    tenant_id: TenantId
    run_id: RunId
    role: ArtifactRole


class ArtifactPolicy(StrictDomainModel):
    """Local policy supplied to the service, never embedded in a store adapter."""

    max_content_bytes: Annotated[int, Field(ge=1, le=1_073_741_824)] = 10_485_760
    allowed_media_types: tuple[MediaType, ...] = (
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "text/markdown",
        "text/plain",
        "text/x-diff",
    )

    @field_validator("allowed_media_types")
    @classmethod
    def media_types_are_unique_and_not_empty(
        cls, media_types: tuple[str, ...]
    ) -> tuple[str, ...]:
        if not media_types:
            raise ValueError("at least one artifact media type must be allowed")
        if len(set(media_types)) != len(media_types):
            raise ValueError("artifact media types must be unique")
        return media_types


class ArtifactVersionRecord(StrictDomainModel):
    """Immutable metadata that points to verified stored content."""

    artifact_id: ArtifactId
    version: ArtifactVersion
    scope: ArtifactScope
    media_type: MediaType
    content_sha256: Sha256Digest
    size_bytes: Annotated[int, Field(ge=0)]
    storage_key: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=1024,
            pattern=r"^artifacts/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/[1-9][0-9]*/[0-9a-f]{64}\.blob$",
        ),
    ]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def storage_key_is_derived_from_metadata(self) -> ArtifactVersionRecord:
        expected = artifact_storage_key(
            self.artifact_id, self.version, self.content_sha256
        )
        if self.storage_key != expected:
            raise ValueError(
                "storage_key must be derived from artifact identity and hash"
            )
        return self

    def preview(self) -> ArtifactMetadataPreview:
        """Return metadata safe for operators; never expose storage internals."""
        return ArtifactMetadataPreview(
            artifact_id=self.artifact_id,
            version=self.version,
            scope=self.scope,
            media_type=self.media_type,
            content_sha256=self.content_sha256,
            size_bytes=self.size_bytes,
            created_at=self.created_at,
        )


class ArtifactMetadataPreview(StrictDomainModel):
    """Read-model projection intentionally omitting the internal storage key."""

    artifact_id: ArtifactId
    version: ArtifactVersion
    scope: ArtifactScope
    media_type: MediaType
    content_sha256: Sha256Digest
    size_bytes: Annotated[int, Field(ge=0)]
    created_at: datetime


class ArtifactReadResult(StrictDomainModel):
    """Authorized artifact bytes plus their safe immutable metadata projection."""

    metadata: ArtifactMetadataPreview
    content: bytes


def artifact_storage_key(
    artifact_id: ArtifactId, version: ArtifactVersion, content_sha256: Sha256Digest
) -> str:
    """Derive the sole local/object-store key; callers never provide paths."""
    return f"artifacts/{artifact_id}/{version}/{content_sha256}.blob"
