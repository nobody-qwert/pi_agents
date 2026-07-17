"""Immutable guest Git checkpoint lineage and rollback coordination."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from orchestrator.workspace import (
    GuestBaseline,
    WorkspaceImport,
    WorkspaceImportService,
)

CheckpointKind = Literal["baseline", "service_accepted", "user_accepted", "rollback"]


class CheckpointError(Exception):
    """A checkpoint request could not safely affect the requested guest workspace."""


@dataclass(frozen=True, slots=True)
class WorkspaceCheckpoint:
    checkpoint_id: str
    workspace_id: str
    run_id: str
    kind: CheckpointKind
    commit_hash: str
    tree_hash: str
    design_version: int
    work_node_id: str | None
    evidence_ids: tuple[str, ...]
    parent_checkpoint_id: str | None
    rollback_from_checkpoint_id: str | None = None


class GuestCheckpointAdapter(Protocol):
    def checkpoint(
        self, workspace: WorkspaceImport, checkpoint_id: str
    ) -> GuestBaseline: ...

    def verify(self, workspace: WorkspaceImport, baseline: GuestBaseline) -> bool: ...

    def restore(self, workspace: WorkspaceImport, baseline: GuestBaseline) -> None: ...


class CheckpointService:
    """Serializes mutations while checkpointing and retains every lineage record."""

    def __init__(
        self, imports: WorkspaceImportService, adapter: GuestCheckpointAdapter
    ) -> None:
        self._imports = imports
        self._adapter = adapter
        self._records: dict[str, list[WorkspaceCheckpoint]] = {}
        self._locks: dict[str, threading.Lock] = {}

    def list_checkpoints(self, workspace_id: str) -> tuple[WorkspaceCheckpoint, ...]:
        workspace = self._imports.get(workspace_id)
        self._ensure_baseline(workspace)
        return tuple(self._records[workspace_id])

    def create(
        self,
        *,
        workspace_id: str,
        checkpoint_id: str,
        kind: Literal["service_accepted", "user_accepted"],
        design_version: int,
        work_node_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> WorkspaceCheckpoint:
        if not checkpoint_id.startswith("checkpoint_") or design_version < 1:
            raise CheckpointError("invalid_checkpoint_request")
        if kind == "service_accepted" and work_node_id is None:
            raise CheckpointError("service_checkpoint_requires_work_node")
        workspace = self._imports.get(workspace_id)
        with self._lock(workspace_id):
            records = self._ensure_baseline(workspace)
            existing = self._find(records, checkpoint_id)
            if existing is not None:
                if existing.kind == kind and existing.design_version == design_version:
                    return existing
                raise CheckpointError("checkpoint_id_already_bound")
            baseline = self._adapter.checkpoint(workspace, checkpoint_id)
            if not self._adapter.verify(workspace, baseline):
                raise CheckpointError("guest_checkpoint_verification_failed")
            record = WorkspaceCheckpoint(
                checkpoint_id=checkpoint_id,
                workspace_id=workspace_id,
                run_id=workspace.run_id,
                kind=kind,
                commit_hash=baseline.commit_hash,
                tree_hash=baseline.tree_hash,
                design_version=design_version,
                work_node_id=work_node_id,
                evidence_ids=evidence_ids,
                parent_checkpoint_id=records[-1].checkpoint_id,
            )
            records.append(record)
            return record

    def rollback(
        self,
        *,
        workspace_id: str,
        target_checkpoint_id: str,
        rollback_checkpoint_id: str,
        design_version: int,
    ) -> WorkspaceCheckpoint:
        workspace = self._imports.get(workspace_id)
        with self._lock(workspace_id):
            records = self._ensure_baseline(workspace)
            existing = self._find(records, rollback_checkpoint_id)
            if existing is not None:
                if (
                    existing.kind == "rollback"
                    and existing.rollback_from_checkpoint_id == target_checkpoint_id
                ):
                    return existing
                raise CheckpointError("checkpoint_id_already_bound")
            target = self._find(records, target_checkpoint_id)
            if target is None:
                raise CheckpointError("unknown_checkpoint")
            baseline = GuestBaseline(target.commit_hash, target.tree_hash)
            if not self._adapter.verify(workspace, baseline):
                raise CheckpointError("damaged_checkpoint")
            self._adapter.restore(workspace, baseline)
            if not self._adapter.verify(workspace, baseline):
                raise CheckpointError("restore_verification_failed")
            record = WorkspaceCheckpoint(
                checkpoint_id=rollback_checkpoint_id,
                workspace_id=workspace_id,
                run_id=workspace.run_id,
                kind="rollback",
                commit_hash=target.commit_hash,
                tree_hash=target.tree_hash,
                design_version=design_version,
                work_node_id=None,
                evidence_ids=(),
                parent_checkpoint_id=records[-1].checkpoint_id,
                rollback_from_checkpoint_id=target_checkpoint_id,
            )
            records.append(record)
            return record

    def _ensure_baseline(self, workspace: WorkspaceImport) -> list[WorkspaceCheckpoint]:
        records = self._records.setdefault(workspace.workspace_id, [])
        if not records:
            records.append(
                WorkspaceCheckpoint(
                    checkpoint_id=f"checkpoint_baseline_{workspace.workspace_id.removeprefix('workspace_')}",
                    workspace_id=workspace.workspace_id,
                    run_id=workspace.run_id,
                    kind="baseline",
                    commit_hash=workspace.baseline.commit_hash,
                    tree_hash=workspace.baseline.tree_hash,
                    design_version=1,
                    work_node_id=None,
                    evidence_ids=(),
                    parent_checkpoint_id=None,
                )
            )
        return records

    def _lock(self, workspace_id: str) -> threading.Lock:
        return self._locks.setdefault(workspace_id, threading.Lock())

    @staticmethod
    def _find(
        records: list[WorkspaceCheckpoint], checkpoint_id: str
    ) -> WorkspaceCheckpoint | None:
        return next(
            (record for record in records if record.checkpoint_id == checkpoint_id),
            None,
        )


class LocalGuestCheckpointAdapter:
    """Fixture Git adapter rooted in the imported guest workspace only."""

    def __init__(self, guest_root: Path) -> None:
        self._guest_root = guest_root.resolve()

    def checkpoint(
        self, workspace: WorkspaceImport, checkpoint_id: str
    ) -> GuestBaseline:
        path = self._path(workspace)
        self._git(path, "add", "--all")
        self._git(path, "commit", "--quiet", "--allow-empty", "-m", checkpoint_id)
        return self._baseline(path)

    def verify(self, workspace: WorkspaceImport, baseline: GuestBaseline) -> bool:
        path = self._path(workspace)
        return (
            self._git(
                path, "cat-file", "-e", f"{baseline.commit_hash}^{{commit}}", fail=False
            )
            is not None
            and self._git(
                path, "rev-parse", f"{baseline.commit_hash}^{{tree}}", fail=False
            )
            == baseline.tree_hash
        )

    def restore(self, workspace: WorkspaceImport, baseline: GuestBaseline) -> None:
        path = self._path(workspace)
        self._git(path, "reset", "--hard", baseline.commit_hash)
        self._git(path, "clean", "-fd")

    def _path(self, workspace: WorkspaceImport) -> Path:
        path = (self._guest_root / workspace.guest_id / workspace.guest_path).resolve()
        if (
            not path.is_relative_to(self._guest_root / workspace.guest_id)
            or not path.is_dir()
        ):
            raise CheckpointError("unknown_guest_workspace")
        return path

    def _baseline(self, path: Path) -> GuestBaseline:
        commit_hash = self._git(path, "rev-parse", "HEAD")
        tree_hash = self._git(path, "rev-parse", "HEAD^{tree}")
        if commit_hash is None or tree_hash is None:
            raise CheckpointError("guest_git_operation_failed")
        return GuestBaseline(commit_hash, tree_hash)

    @staticmethod
    def _git(path: Path, *arguments: str, fail: bool = True) -> str | None:
        result = subprocess.run(
            ("git", "-C", os.fspath(path), *arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if result.returncode != 0:
            if fail:
                raise CheckpointError("guest_git_operation_failed")
            return None
        return result.stdout.decode("ascii").strip()
