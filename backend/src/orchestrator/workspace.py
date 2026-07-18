"""Sanitized project import and separate guest Git baseline boundary."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy import text

from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.projects import ProjectCatalog, ProjectFile, ProjectPolicyError
from orchestrator.vm import GuestHandle


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
    project_id: str
    guest_id: str
    guest_path: str
    source_fingerprint: str
    transfer_id: str
    manifest: tuple[TransferManifestEntry, ...]
    excluded_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    source_git_head: str | None
    source_git_dirty: bool | None
    baseline: GuestBaseline


class GuestWorkspaceAdapter(Protocol):
    """Narrow guest-only filesystem/Git port; it never accepts a host path."""

    def prepare(self, guest: GuestHandle, guest_path: str) -> None: ...

    def write_file(
        self, guest: GuestHandle, guest_path: str, file: ProjectFile
    ) -> None: ...

    def create_baseline(self, guest: GuestHandle, guest_path: str) -> GuestBaseline: ...

    def cleanup(self, guest: GuestHandle, guest_path: str) -> None: ...


class WorkspaceLifecycle(Protocol):
    def probe(self, run_id: str) -> GuestHandle: ...


class WorkspaceImportStore(Protocol):
    def get(self, workspace_id: str) -> WorkspaceImport | None: ...

    def begin(
        self,
        *,
        transfer_id: str,
        workspace_id: str,
        run_id: str,
        project_id: str,
        guest_id: str,
        guest_path: str,
        source_fingerprint: str,
        manifest: tuple[TransferManifestEntry, ...],
        excluded_paths: tuple[str, ...],
        protected_paths: tuple[str, ...],
        source_git_head: str | None,
        source_git_dirty: bool | None,
    ) -> None: ...

    def accept(self, imported: WorkspaceImport) -> WorkspaceImport: ...

    def fail(self, transfer_id: str, error_code: str) -> None: ...


class MemoryWorkspaceImportStore:
    """Process-local fixture store; production composes the PostgreSQL store."""

    def __init__(self) -> None:
        self._imports: dict[str, WorkspaceImport] = {}

    def get(self, workspace_id: str) -> WorkspaceImport | None:
        return self._imports.get(workspace_id)

    def begin(self, **_: object) -> None:
        return None

    def accept(self, imported: WorkspaceImport) -> WorkspaceImport:
        existing = self._imports.get(imported.workspace_id)
        if existing is not None and existing != imported:
            raise WorkspaceImportError("workspace_id_already_bound")
        self._imports[imported.workspace_id] = imported
        return imported

    def fail(self, transfer_id: str, error_code: str) -> None:
        return None


class WorkspaceImportService:
    """Copies a verified catalog snapshot into one ready run-scoped guest."""

    def __init__(
        self,
        catalog: ProjectCatalog,
        lifecycle: WorkspaceLifecycle,
        guest_adapter: GuestWorkspaceAdapter,
        store: WorkspaceImportStore | None = None,
    ) -> None:
        self._catalog = catalog
        self._lifecycle = lifecycle
        self._guest_adapter = guest_adapter
        self._store = store or MemoryWorkspaceImportStore()

    def import_snapshot(
        self,
        *,
        workspace_id: str,
        run_id: str,
        project_id: str,
        expected_source_fingerprint: str,
    ) -> WorkspaceImport:
        self._validate_ids(workspace_id, run_id)
        existing = self._store.get(workspace_id)
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
            preview = self._catalog.preview(project_id)
            if preview.source_fingerprint != expected_source_fingerprint:
                raise ProjectPolicyError("source_fingerprint_changed")
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
        self._store.begin(
            transfer_id=transfer_id,
            workspace_id=workspace_id,
            run_id=run_id,
            project_id=project_id,
            guest_id=guest.guest_id,
            guest_path=guest_path,
            source_fingerprint=snapshot.source_fingerprint,
            manifest=manifest,
            excluded_paths=snapshot.excluded_paths,
            protected_paths=preview.protected_paths,
            source_git_head=preview.git_head,
            source_git_dirty=preview.git_dirty,
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
            with suppress(Exception):
                self._store.fail(transfer_id, "guest_import_failed")
            if isinstance(error, WorkspaceImportError):
                raise
            raise WorkspaceImportError("guest_import_failed") from error
        imported = WorkspaceImport(
            workspace_id=workspace_id,
            run_id=run_id,
            project_id=project_id,
            guest_id=guest.guest_id,
            guest_path=guest_path,
            source_fingerprint=snapshot.source_fingerprint,
            transfer_id=transfer_id,
            manifest=manifest,
            excluded_paths=snapshot.excluded_paths,
            protected_paths=preview.protected_paths,
            source_git_head=preview.git_head,
            source_git_dirty=preview.git_dirty,
            baseline=baseline,
        )
        return self._store.accept(imported)

    def get(self, workspace_id: str) -> WorkspaceImport:
        try:
            imported = self._store.get(workspace_id)
            if imported is None:
                raise KeyError(workspace_id)
            return imported
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
        destination.mkdir(parents=True, exist_ok=True)

    def write_file(
        self, guest: GuestHandle, guest_path: str, file: ProjectFile
    ) -> None:
        destination = self._destination(guest, guest_path) / file.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if (
                destination.is_file()
                and not destination.is_symlink()
                and hashlib.sha256(destination.read_bytes()).hexdigest() == file.sha256
            ):
                return
            raise WorkspaceImportError("guest_import_conflict")
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
        if (destination / ".git").is_dir():
            if self._git(destination, "status", "--porcelain"):
                raise WorkspaceImportError("guest_baseline_changed")
            return GuestBaseline(
                commit_hash=self._git(destination, "rev-parse", "HEAD"),
                tree_hash=self._git(destination, "rev-parse", "HEAD^{tree}"),
            )
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


class PostgresWorkspaceImportStore:
    """Durable two-phase record of sanitized copy-in and baseline acceptance."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get(self, workspace_id: str) -> WorkspaceImport | None:
        with self._unit_of_work.transaction() as unit_of_work:
            payload = unit_of_work.connection.execute(
                text(
                    "SELECT manifest FROM workspace_transfers "
                    "WHERE workspace_id = :workspace_id AND direction = 'copy_in' "
                    "AND status = 'completed' ORDER BY created_at DESC LIMIT 1"
                ),
                {"workspace_id": workspace_id},
            ).scalar()
        return self._decode(payload) if payload is not None else None

    def begin(
        self,
        *,
        transfer_id: str,
        workspace_id: str,
        run_id: str,
        project_id: str,
        guest_id: str,
        guest_path: str,
        source_fingerprint: str,
        manifest: tuple[TransferManifestEntry, ...],
        excluded_paths: tuple[str, ...],
        protected_paths: tuple[str, ...],
        source_git_head: str | None,
        source_git_dirty: bool | None,
    ) -> None:
        preliminary = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "project_id": project_id,
            "guest_id": guest_id,
            "guest_path": guest_path,
            "source_fingerprint": source_fingerprint,
            "transfer_id": transfer_id,
            "manifest": [self._manifest_entry(entry) for entry in manifest],
            "excluded_paths": list(excluded_paths),
            "protected_paths": list(protected_paths),
            "source_git_head": source_git_head,
            "source_git_dirty": source_git_dirty,
            "baseline": None,
        }
        now = datetime.now(UTC)
        with self._unit_of_work.transaction() as unit_of_work:
            workspace = unit_of_work.connection.execute(
                text(
                    "SELECT run_id, selected_source, source_fingerprint, "
                    "guest_identity, guest_path, lifecycle_status "
                    "FROM workspace_sessions WHERE workspace_id = :workspace_id FOR UPDATE"
                ),
                {"workspace_id": workspace_id},
            ).mappings().one_or_none()
            if workspace is None:
                raise WorkspaceImportError("unknown_workspace")
            expected = (
                run_id,
                project_id,
                source_fingerprint,
                guest_id,
                guest_path,
                "ready",
            )
            actual = (
                workspace["run_id"],
                workspace["selected_source"],
                workspace["source_fingerprint"],
                workspace["guest_identity"],
                workspace["guest_path"],
                workspace["lifecycle_status"],
            )
            if actual != expected:
                raise WorkspaceImportError("workspace_binding_mismatch")
            existing = unit_of_work.connection.execute(
                text(
                    "SELECT run_id, workspace_id, direction, source_fingerprint, status "
                    "FROM workspace_transfers WHERE transfer_id = :transfer_id FOR UPDATE"
                ),
                {"transfer_id": transfer_id},
            ).mappings().one_or_none()
            if existing is None:
                unit_of_work.connection.execute(
                    text(
                        "INSERT INTO workspace_transfers (transfer_id, run_id, workspace_id, "
                        "direction, manifest, status, created_at, source_fingerprint, "
                        "excluded_paths, completed_at, error_code) VALUES ("
                        ":transfer_id, :run_id, :workspace_id, 'copy_in', "
                        "CAST(:manifest AS jsonb), 'started', :now, :source_fingerprint, "
                        "CAST(:excluded_paths AS jsonb), NULL, NULL)"
                    ),
                    {
                        "transfer_id": transfer_id,
                        "run_id": run_id,
                        "workspace_id": workspace_id,
                        "manifest": json.dumps(preliminary, separators=(",", ":")),
                        "now": now,
                        "source_fingerprint": source_fingerprint,
                        "excluded_paths": json.dumps(excluded_paths),
                    },
                )
                return
            binding = (
                existing["run_id"],
                existing["workspace_id"],
                existing["direction"],
                existing["source_fingerprint"],
            )
            if binding != (run_id, workspace_id, "copy_in", source_fingerprint):
                raise WorkspaceImportError("transfer_id_already_bound")
            if existing["status"] != "completed":
                unit_of_work.connection.execute(
                    text(
                        "UPDATE workspace_transfers SET manifest = CAST(:manifest AS jsonb), "
                        "status = 'started', completed_at = NULL, error_code = NULL "
                        "WHERE transfer_id = :transfer_id"
                    ),
                    {
                        "transfer_id": transfer_id,
                        "manifest": json.dumps(preliminary, separators=(",", ":")),
                    },
                )

    def accept(self, imported: WorkspaceImport) -> WorkspaceImport:
        payload = self._encode(imported)
        now = datetime.now(UTC)
        with self._unit_of_work.transaction() as unit_of_work:
            transfer = unit_of_work.connection.execute(
                text(
                    "SELECT status, manifest FROM workspace_transfers "
                    "WHERE transfer_id = :transfer_id AND workspace_id = :workspace_id "
                    "AND run_id = :run_id AND direction = 'copy_in' FOR UPDATE"
                ),
                {
                    "transfer_id": imported.transfer_id,
                    "workspace_id": imported.workspace_id,
                    "run_id": imported.run_id,
                },
            ).mappings().one_or_none()
            if transfer is None:
                raise WorkspaceImportError("unknown_transfer")
            if transfer["status"] == "completed":
                existing = self._decode(transfer["manifest"])
                if existing != imported:
                    raise WorkspaceImportError("completed_transfer_conflict")
                return existing
            workspace = unit_of_work.workspace_sessions.get(imported.workspace_id)
            if workspace is None:
                raise WorkspaceImportError("unknown_workspace")
            if (
                workspace.run_id != imported.run_id
                or workspace.selected_source != imported.project_id
                or workspace.source_fingerprint != imported.source_fingerprint
                or workspace.guest_identity != imported.guest_id
                or workspace.guest_path != imported.guest_path
            ):
                raise WorkspaceImportError("workspace_binding_mismatch")
            unit_of_work.workspace_sessions.compare_and_swap(
                workspace.model_copy(
                    update={
                        "status": "ready",
                        "metadata": workspace.metadata.model_copy(
                            update={
                                "record_version": workspace.metadata.record_version + 1,
                                "updated_at": now,
                                "idempotency_key": f"workspace:import:{imported.transfer_id}",
                            }
                        ),
                    }
                ),
                expected_record_version=workspace.metadata.record_version,
            )
            unit_of_work.connection.execute(
                text(
                    "UPDATE workspace_transfers SET manifest = CAST(:manifest AS jsonb), "
                    "status = 'completed', completed_at = :now, error_code = NULL "
                    "WHERE transfer_id = :transfer_id"
                ),
                {
                    "transfer_id": imported.transfer_id,
                    "manifest": json.dumps(payload, separators=(",", ":")),
                    "now": now,
                },
            )
        return imported

    def fail(self, transfer_id: str, error_code: str) -> None:
        now = datetime.now(UTC)
        with self._unit_of_work.transaction() as unit_of_work:
            unit_of_work.connection.execute(
                text(
                    "UPDATE workspace_transfers SET status = 'failed', "
                    "completed_at = :now, error_code = :error_code "
                    "WHERE transfer_id = :transfer_id AND status <> 'completed'"
                ),
                {
                    "transfer_id": transfer_id,
                    "now": now,
                    "error_code": error_code[:128],
                },
            )

    @staticmethod
    def _encode(imported: WorkspaceImport) -> dict[str, object]:
        return {
            "workspace_id": imported.workspace_id,
            "run_id": imported.run_id,
            "project_id": imported.project_id,
            "guest_id": imported.guest_id,
            "guest_path": imported.guest_path,
            "source_fingerprint": imported.source_fingerprint,
            "transfer_id": imported.transfer_id,
            "manifest": [
                PostgresWorkspaceImportStore._manifest_entry(entry)
                for entry in imported.manifest
            ],
            "excluded_paths": list(imported.excluded_paths),
            "protected_paths": list(imported.protected_paths),
            "source_git_head": imported.source_git_head,
            "source_git_dirty": imported.source_git_dirty,
            "baseline": {
                "commit_hash": imported.baseline.commit_hash,
                "tree_hash": imported.baseline.tree_hash,
            },
        }

    @staticmethod
    def _manifest_entry(entry: TransferManifestEntry) -> dict[str, object]:
        return {
            "relative_path": entry.relative_path,
            "sha256": entry.sha256,
            "size_bytes": entry.size_bytes,
            "executable": entry.executable,
        }

    @staticmethod
    def _decode(payload: object) -> WorkspaceImport:
        if not isinstance(payload, dict):
            raise WorkspaceImportError("invalid_transfer_record")
        values = cast(dict[str, object], payload)
        manifest_value = values.get("manifest")
        baseline_value = values.get("baseline")
        if not isinstance(manifest_value, list) or not isinstance(baseline_value, dict):
            raise WorkspaceImportError("invalid_transfer_record")
        try:
            manifest = tuple(
                TransferManifestEntry(
                    relative_path=str(entry["relative_path"]),
                    sha256=str(entry["sha256"]),
                    size_bytes=int(cast(int | str, entry["size_bytes"])),
                    executable=bool(entry["executable"]),
                )
                for entry in cast(list[dict[str, object]], manifest_value)
            )
            baseline = GuestBaseline(
                commit_hash=str(baseline_value["commit_hash"]),
                tree_hash=str(baseline_value["tree_hash"]),
            )
            return WorkspaceImport(
                workspace_id=str(values["workspace_id"]),
                run_id=str(values["run_id"]),
                project_id=str(values["project_id"]),
                guest_id=str(values["guest_id"]),
                guest_path=str(values["guest_path"]),
                source_fingerprint=str(values["source_fingerprint"]),
                transfer_id=str(values["transfer_id"]),
                manifest=manifest,
                excluded_paths=tuple(
                    str(value) for value in cast(list[object], values["excluded_paths"])
                ),
                protected_paths=tuple(
                    str(value) for value in cast(list[object], values["protected_paths"])
                ),
                source_git_head=(
                    str(values["source_git_head"])
                    if values.get("source_git_head") is not None
                    else None
                ),
                source_git_dirty=(
                    bool(values["source_git_dirty"])
                    if values.get("source_git_dirty") is not None
                    else None
                ),
                baseline=baseline,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkspaceImportError("invalid_transfer_record") from error
