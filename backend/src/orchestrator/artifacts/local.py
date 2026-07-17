"""Atomic local-volume implementation of the artifact content-store port."""

from __future__ import annotations

import hashlib
import os
import secrets
from contextlib import suppress
from pathlib import Path

from orchestrator.artifacts.models import ArtifactVersionRecord
from orchestrator.artifacts.ports import ArtifactIntegrityError, ArtifactNotFoundError


class LocalVolumeArtifactStore:
    """Store content under a private volume using only metadata-derived paths."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put_if_absent(self, record: ArtifactVersionRecord, content: bytes) -> bool:
        self._assert_content_matches(record, content)
        target = self._path_for(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(16)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                self._assert_file_matches(record, target)
                return False
            return True
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    def read(self, record: ArtifactVersionRecord) -> bytes:
        target = self._path_for(record)
        try:
            content = target.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(
                f"stored content is missing for {record.artifact_id!r} v{record.version}"
            ) from error
        self._assert_content_matches(record, content)
        return content

    def delete_if_matches(self, record: ArtifactVersionRecord) -> None:
        target = self._path_for(record)
        try:
            self._assert_file_matches(record, target)
            target.unlink()
        except FileNotFoundError:
            return

    def _path_for(self, record: ArtifactVersionRecord) -> Path:
        path = (self._root / record.storage_key).resolve()
        if not path.is_relative_to(self._root):
            raise ArtifactIntegrityError(
                "derived artifact key escaped the configured volume"
            )
        return path

    @staticmethod
    def _assert_content_matches(record: ArtifactVersionRecord, content: bytes) -> None:
        if len(content) != record.size_bytes:
            raise ArtifactIntegrityError(
                "artifact content length does not match metadata"
            )
        if hashlib.sha256(content).hexdigest() != record.content_sha256:
            raise ArtifactIntegrityError(
                "artifact content digest does not match metadata"
            )

    def _assert_file_matches(self, record: ArtifactVersionRecord, path: Path) -> None:
        self._assert_content_matches(record, path.read_bytes())
