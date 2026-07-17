"""Deterministic policy service for authoritative artifact publication and reads."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from orchestrator.artifacts.models import (
    ArtifactAccessRequest,
    ArtifactPolicy,
    ArtifactPublishRequest,
    ArtifactReadResult,
    ArtifactReference,
    ArtifactVersionRecord,
    artifact_storage_key,
)
from orchestrator.artifacts.ports import (
    ArtifactAccessDeniedError,
    ArtifactContentStore,
    ArtifactIntegrityError,
    ArtifactMetadataRepository,
    ArtifactNotFoundError,
    ArtifactPolicyError,
    ArtifactVersionConflictError,
)


class ArtifactService:
    """The sole authoritative interface for validated artifact content."""

    def __init__(
        self,
        *,
        content_store: ArtifactContentStore,
        metadata_repository: ArtifactMetadataRepository,
        policy: ArtifactPolicy,
    ) -> None:
        self._content_store = content_store
        self._metadata_repository = metadata_repository
        self._policy = policy

    def publish(
        self, request: ArtifactPublishRequest, content: bytes
    ) -> ArtifactVersionRecord:
        """Store verified bytes then atomically make their metadata authoritative.

        A stale expected version cannot leave a metadata record behind.  A content
        object created by a losing write has a content-addressed derived key and
        is removed when it is not shared with a winning write.
        """
        self._validate_publish_policy(request, content)
        version = request.expected_version + 1
        digest = hashlib.sha256(content).hexdigest()
        record = ArtifactVersionRecord(
            artifact_id=request.artifact_id,
            version=version,
            scope=request.scope,
            media_type=request.media_type,
            content_sha256=digest,
            size_bytes=len(content),
            storage_key=artifact_storage_key(request.artifact_id, version, digest),
            created_at=datetime.now(UTC),
        )
        created = self._content_store.put_if_absent(record, content)
        try:
            self._metadata_repository.publish(
                record, expected_version=request.expected_version
            )
        except ArtifactVersionConflictError:
            if created:
                self._content_store.delete_if_matches(record)
            raise
        return record

    def read(
        self, reference: ArtifactReference, access: ArtifactAccessRequest
    ) -> ArtifactReadResult:
        """Authorize an immutable reference before returning verified bytes."""
        record = self._metadata_repository.get(reference.artifact_id, reference.version)
        if record is None:
            raise ArtifactNotFoundError(
                f"artifact {reference.artifact_id!r} v{reference.version} was not found"
            )
        if (
            record.scope.tenant_id != access.tenant_id
            or record.scope.run_id != access.run_id
            or access.role not in record.scope.allowed_roles
        ):
            raise ArtifactAccessDeniedError(
                "artifact access scope does not permit this read"
            )
        content = self._content_store.read(record)
        if len(content) != record.size_bytes:
            raise ArtifactIntegrityError("artifact read length differs from metadata")
        return ArtifactReadResult(metadata=record.preview(), content=content)

    def _validate_publish_policy(
        self, request: ArtifactPublishRequest, content: bytes
    ) -> None:
        if len(content) > self._policy.max_content_bytes:
            raise ArtifactPolicyError(
                f"artifact is {len(content)} bytes; configured limit is "
                f"{self._policy.max_content_bytes} bytes"
            )
        if request.media_type not in self._policy.allowed_media_types:
            raise ArtifactPolicyError(
                f"artifact media type {request.media_type!r} is not allowed"
            )
        digest = hashlib.sha256(content).hexdigest()
        if request.expected_sha256 is not None and request.expected_sha256 != digest:
            raise ArtifactPolicyError("declared artifact digest does not match content")
