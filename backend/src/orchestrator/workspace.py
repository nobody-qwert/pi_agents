"""Sanitized project import and separate guest Git baseline boundary."""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.projects import ProjectCatalog, ProjectFile, ProjectPolicyError
from orchestrator.vm import GuestHandle, VmLifecycleService


class WorkspaceImportError(Exception):
    """A source snapshot could not become a ready guest workspace safely."""


@dataclass(frozen=True, slots=True)
class GuestBaseline:
    commit_hash: str
    tree_hash: str


@dataclass(frozen=True, slots=True)
class TransferManifestEntry:
    relative_path: str
    sha256: str
    size_bytes: int
    executable: bool


@dataclass(frozen=True, slots=True)
class WorkspaceImport:
    workspace_id: str
    run_id: str
    guest_id: str
    guest_path: str
    source_fingerprint: str
    transfer_id: str
    manifest: tuple[TransferManifestEntry, ...]
    excluded_paths: tuple[str, ...]
    baseline: GuestBaseline


class GuestWorkspaceAdapter(Protocol):
    """Narrow guest-only filesystem/Git port; it never accepts a host path."""

    def prepare(self, guest: GuestHandle, guest_path: str) -> None: ...

    def write_file(
        self, guest: GuestHandle, guest_path: str, file: ProjectFile
    ) -> None: ...

    def create_baseline(self, guest: GuestHandle, guest_path: str) -> GuestBaseline: ...

    def cleanup(self, guest: GuestHandle, guest_path: str) -> None: ...


class WorkspaceImportService:
    """Copies a verified catalog snapshot into one ready run-scoped guest."""

    def __init__(
        self,
        catalog: ProjectCatalog,
        lifecycle: VmLifecycleService,
        guest_adapter: GuestWorkspaceAdapter,
    ) -> None:
        self._catalog = catalog
        self._lifecycle = lifecycle
        self._guest_adapter = guest_adapter
        self._imports: dict[str, WorkspaceImport] = {}

    def import_snapshot(
        self,
        *,
        workspace_id: str,
        run_id: str,
        project_id: str,
        expected_source_fingerprint: str,
    ) -> WorkspaceImport:
        self._validate_ids(workspace_id, run_id)
        existing = self._imports.get(workspace_id)
        if existing is not None:
            if (
                existing.run_id == run_id
                and existing.source_fingerprint == expected_source_fingerprint
            ):
                return existing
            raise WorkspaceImportError("workspace_id_already_bound")
        guest = self._lifecycle.probe(run_id)
        if guest.status != "ready":
            raise WorkspaceImportError("guest_not_ready")
        try:
            snapshot = self._catalog.snapshot(
                project_id, expected_source_fingerprint=expected_source_fingerprint
            )
        except ProjectPolicyError as error:
            raise WorkspaceImportError(str(error)) from error
        guest_path = f"home/piagent/workspaces/{run_id}/{project_id}"
        manifest = tuple(
            TransferManifestEntry(
                relative_path=file.relative_path,
                sha256=file.sha256,
                size_bytes=len(file.content),
                executable=file.executable,
            )
            for file in snapshot.files
        )
        transfer_id = (
            "transfer_"
            + hashlib.sha256(
                f"{workspace_id}:{snapshot.source_fingerprint}".encode()
            ).hexdigest()[:24]
        )
        try:
            self._guest_adapter.prepare(guest, guest_path)
            for file in snapshot.files:
                if hashlib.sha256(file.content).hexdigest() != file.sha256:
                    raise WorkspaceImportError("invalid_snapshot_manifest")
                self._guest_adapter.write_file(guest, guest_path, file)
            baseline = self._guest_adapter.create_baseline(guest, guest_path)
        except Exception as error:
            with suppress(Exception):
                self._guest_adapter.cleanup(guest, guest_path)
            if isinstance(error, WorkspaceImportError):
                raise
            raise WorkspaceImportError("guest_import_failed") from error
        imported = WorkspaceImport(
            workspace_id=workspace_id,
            run_id=run_id,
            guest_id=guest.guest_id,
            guest_path=guest_path,
            source_fingerprint=snapshot.source_fingerprint,
            transfer_id=transfer_id,
            manifest=manifest,
            excluded_paths=snapshot.excluded_paths,
            baseline=baseline,
        )
        self._imports[workspace_id] = imported
        return imported

    def get(self, workspace_id: str) -> WorkspaceImport:
        try:
            return self._imports[workspace_id]
        except KeyError as error:
            raise WorkspaceImportError("unknown_workspace") from error

    @staticmethod
    def _validate_ids(workspace_id: str, run_id: str) -> None:
        if not workspace_id.startswith("workspace_"):
            raise WorkspaceImportError("invalid_workspace_id")
        if not run_id.startswith("run_"):
            raise WorkspaceImportError("invalid_run_id")


class LocalGuestWorkspaceAdapter:
    """Fixture adapter rooted in a disposable local directory, never a source tree."""

    def __init__(self, guest_root: Path) -> None:
        self._guest_root = guest_root.resolve()

    def prepare(self, guest: GuestHandle, guest_path: str) -> None:
        destination = self._destination(guest, guest_path)
        destination.mkdir(parents=True, exist_ok=False)

    def write_file(
        self, guest: GuestHandle, guest_path: str, file: ProjectFile
    ) -> None:
        destination = self._destination(guest, guest_path) / file.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(file.content)
        finally:
            os.close(descriptor)
        if file.executable:
            destination.chmod(0o700)

    def create_baseline(self, guest: GuestHandle, guest_path: str) -> GuestBaseline:
        destination = self._destination(guest, guest_path)
        self._git(destination, "init", "--quiet")
        self._git(destination, "config", "user.name", "orchestrator-service")
        self._git(destination, "config", "user.email", "service@orchestrator.invalid")
        self._git(destination, "add", "--all")
        self._git(destination, "commit", "--quiet", "-m", "orchestrator baseline")
        return GuestBaseline(
            commit_hash=self._git(destination, "rev-parse", "HEAD"),
            tree_hash=self._git(destination, "rev-parse", "HEAD^{tree}"),
        )

    def cleanup(self, guest: GuestHandle, guest_path: str) -> None:
        destination = self._destination(guest, guest_path)
        if not destination.exists():
            return
        for path in sorted(destination.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        destination.rmdir()

    def _destination(self, guest: GuestHandle, guest_path: str) -> Path:
        expected = f"home/piagent/workspaces/{guest.run_id}/"
        if not guest_path.startswith(expected) or ".." in Path(guest_path).parts:
            raise WorkspaceImportError("invalid_guest_path")
        destination = (self._guest_root / guest.guest_id / guest_path).resolve()
        if not destination.is_relative_to(self._guest_root / guest.guest_id):
            raise WorkspaceImportError("guest_path_escape")
        return destination

    @staticmethod
    def _git(destination: Path, *arguments: str) -> str:
        result = subprocess.run(
            ("git", "-C", os.fspath(destination), *arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if result.returncode != 0:
            raise WorkspaceImportError("guest_git_baseline_failed")
        return result.stdout.decode("ascii").strip()
