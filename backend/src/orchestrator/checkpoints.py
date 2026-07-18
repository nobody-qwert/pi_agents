"""Immutable guest Git checkpoint lineage and rollback coordination."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

from sqlalchemy import text

from orchestrator.domain import EventDraft
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.services.events import DurableEventService, EventWakeupNotifier
from orchestrator.workspace import (
    GuestBaseline,
    WorkspaceImport,
    WorkspaceImportService,
)

CheckpointKind = Literal[
    "baseline", "execution", "service_accepted", "user_accepted", "rollback"
]


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
        kind: Literal["execution", "service_accepted", "user_accepted"],
        design_version: int,
        work_node_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> WorkspaceCheckpoint:
        if not checkpoint_id.startswith("checkpoint_") or design_version < 1:
            raise CheckpointError("invalid_checkpoint_request")
        if kind in {"execution", "service_accepted"} and work_node_id is None:
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


class PostgresCheckpointService:
    """Restart-safe checkpoint lineage serialized by the workspace database row."""

    def __init__(
        self,
        imports: WorkspaceImportService,
        adapter: GuestCheckpointAdapter,
        unit_of_work: PostgresUnitOfWork,
        notifier: EventWakeupNotifier | None = None,
    ) -> None:
        self._imports = imports
        self._adapter = adapter
        self._unit_of_work = unit_of_work
        self._events = (
            DurableEventService(unit_of_work, notifier) if notifier is not None else None
        )

    def list_checkpoints(self, workspace_id: str) -> tuple[WorkspaceCheckpoint, ...]:
        workspace = self._imports.get(workspace_id)
        with self._unit_of_work.transaction() as unit_of_work:
            self._lock_workspace(unit_of_work, workspace_id)
            self._ensure_baseline(unit_of_work, workspace)
            rows = unit_of_work.connection.execute(
                text(
                    "SELECT payload FROM workspace_checkpoints "
                    "WHERE workspace_id = :workspace_id ORDER BY created_at, checkpoint_id"
                ),
                {"workspace_id": workspace_id},
            ).scalars()
            return tuple(self._decode(payload) for payload in rows)

    def create(
        self,
        *,
        workspace_id: str,
        checkpoint_id: str,
        kind: Literal["execution", "service_accepted", "user_accepted"],
        design_version: int,
        work_node_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> WorkspaceCheckpoint:
        if not checkpoint_id.startswith("checkpoint_") or design_version < 1:
            raise CheckpointError("invalid_checkpoint_request")
        if kind in {"execution", "service_accepted"} and work_node_id is None:
            raise CheckpointError("service_checkpoint_requires_work_node")
        workspace = self._imports.get(workspace_id)
        result: list[WorkspaceCheckpoint] = []

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            result.append(
                self._create_locked(
                    unit_of_work,
                    workspace=workspace,
                    checkpoint_id=checkpoint_id,
                    kind=kind,
                    design_version=design_version,
                    work_node_id=work_node_id,
                    evidence_ids=evidence_ids,
                )
            )

        if self._events is None:
            with self._unit_of_work.transaction() as unit_of_work:
                persist(unit_of_work)
        else:
            self._events.apply(
                self._checkpoint_draft(
                    workspace,
                    checkpoint_id=checkpoint_id,
                    design_version=design_version,
                    event_type="workspace.checkpointed",
                    summary="Guest workspace checkpoint recorded",
                    key=f"checkpoint:{checkpoint_id}",
                    stage="EXECUTE" if kind == "execution" else "LOCAL_VERIFY",
                ),
                persist,
            )
        if result:
            return result[0]
        with self._unit_of_work.transaction() as unit_of_work:
            replay = self._get(unit_of_work, checkpoint_id)
        if replay is None:
            raise CheckpointError("checkpoint_event_without_record")
        return replay

    def _create_locked(
        self,
        unit_of_work: PostgresUnitOfWork,
        *,
        workspace: WorkspaceImport,
        checkpoint_id: str,
        kind: Literal["execution", "service_accepted", "user_accepted"],
        design_version: int,
        work_node_id: str | None,
        evidence_ids: tuple[str, ...],
    ) -> WorkspaceCheckpoint:
        workspace_id = workspace.workspace_id
        self._lock_workspace(unit_of_work, workspace_id)
        self._ensure_baseline(unit_of_work, workspace)
        existing = self._get(unit_of_work, checkpoint_id)
        if existing is not None:
            if (
                existing.workspace_id == workspace_id
                and existing.kind == kind
                and existing.design_version == design_version
                and existing.work_node_id == work_node_id
                and existing.evidence_ids == evidence_ids
            ):
                return existing
            raise CheckpointError("checkpoint_id_already_bound")
        parent_id = self._current_id(unit_of_work, workspace_id)
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
            parent_checkpoint_id=parent_id,
        )
        self._insert(unit_of_work, record)
        return record

    def rollback(
        self,
        *,
        workspace_id: str,
        target_checkpoint_id: str,
        rollback_checkpoint_id: str,
        design_version: int,
    ) -> WorkspaceCheckpoint:
        if not rollback_checkpoint_id.startswith("checkpoint_") or design_version < 1:
            raise CheckpointError("invalid_checkpoint_request")
        workspace = self._imports.get(workspace_id)
        result: list[WorkspaceCheckpoint] = []

        def persist(unit_of_work: PostgresUnitOfWork) -> None:
            result.append(
                self._rollback_locked(
                    unit_of_work,
                    workspace=workspace,
                    target_checkpoint_id=target_checkpoint_id,
                    rollback_checkpoint_id=rollback_checkpoint_id,
                    design_version=design_version,
                )
            )

        if self._events is None:
            with self._unit_of_work.transaction() as unit_of_work:
                persist(unit_of_work)
        else:
            self._events.apply(
                self._checkpoint_draft(
                    workspace,
                    checkpoint_id=rollback_checkpoint_id,
                    design_version=design_version,
                    event_type="workspace.rolled_back",
                    summary="Guest workspace rollback recorded",
                    key=f"rollback:{rollback_checkpoint_id}",
                ),
                persist,
            )
        if result:
            return result[0]
        with self._unit_of_work.transaction() as unit_of_work:
            replay = self._get(unit_of_work, rollback_checkpoint_id)
        if replay is None:
            raise CheckpointError("rollback_event_without_record")
        return replay

    def _rollback_locked(
        self,
        unit_of_work: PostgresUnitOfWork,
        *,
        workspace: WorkspaceImport,
        target_checkpoint_id: str,
        rollback_checkpoint_id: str,
        design_version: int,
    ) -> WorkspaceCheckpoint:
        workspace_id = workspace.workspace_id
        self._lock_workspace(unit_of_work, workspace_id)
        self._ensure_baseline(unit_of_work, workspace)
        existing = self._get(unit_of_work, rollback_checkpoint_id)
        if existing is not None:
            if (
                existing.workspace_id == workspace_id
                and existing.kind == "rollback"
                and existing.rollback_from_checkpoint_id == target_checkpoint_id
            ):
                return existing
            raise CheckpointError("checkpoint_id_already_bound")
        target = self._get(unit_of_work, target_checkpoint_id)
        if target is None or target.workspace_id != workspace_id:
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
            parent_checkpoint_id=self._current_id(unit_of_work, workspace_id),
            rollback_from_checkpoint_id=target_checkpoint_id,
        )
        self._insert(unit_of_work, record)
        return record

    def _checkpoint_draft(
        self,
        workspace: WorkspaceImport,
        *,
        checkpoint_id: str,
        design_version: int,
        event_type: Literal["workspace.checkpointed", "workspace.rolled_back"],
        summary: str,
        key: str,
        stage: Literal["EXECUTE", "LOCAL_VERIFY", "RESUME_GATE"] = "RESUME_GATE",
    ) -> EventDraft:
        with self._unit_of_work.transaction() as unit_of_work:
            row = unit_of_work.connection.execute(
                text(
                    "SELECT conversation_id, payload FROM runs WHERE run_id = :run_id"
                ),
                {"run_id": workspace.run_id},
            ).mappings().one_or_none()
        if row is None or row["conversation_id"] is None:
            raise CheckpointError("checkpoint_run_context_missing")
        digest = sha256(key.encode()).hexdigest()
        event_id = f"evt_{digest[:32]}"
        trace_id = row["payload"]["metadata"].get("trace_id") or digest[:32]
        return EventDraft(
            event_id=event_id,
            run_id=workspace.run_id,
            conversation_id=str(row["conversation_id"]),
            occurred_at=datetime.now(UTC),
            type=event_type,
            stage=stage,
            node_id="checkpoint-service",
            attempt_id=f"attempt_{digest[:32]}",
            design_version=design_version,
            packet_version=1,
            actor_role="checkpoint-service",
            status="accepted",
            outcome="accepted",
            summary=summary,
            detail_ref=(
                f"/api/v1/runs/{workspace.run_id}/events/{event_id}/detail"
            ),
            correlation_id=checkpoint_id,
            trace_id=str(trace_id),
            span_id=digest[:16],
            command_idempotency_key=key,
        )

    @staticmethod
    def _lock_workspace(unit_of_work: PostgresUnitOfWork, workspace_id: str) -> None:
        found = unit_of_work.connection.execute(
            text(
                "SELECT workspace_id FROM workspace_sessions "
                "WHERE workspace_id = :workspace_id FOR UPDATE"
            ),
            {"workspace_id": workspace_id},
        ).scalar()
        if found is None:
            raise CheckpointError("unknown_workspace")

    def _ensure_baseline(
        self, unit_of_work: PostgresUnitOfWork, workspace: WorkspaceImport
    ) -> None:
        if self._current_id(unit_of_work, workspace.workspace_id) is not None:
            return
        self._insert(
            unit_of_work,
            WorkspaceCheckpoint(
                checkpoint_id=(
                    "checkpoint_baseline_"
                    + workspace.workspace_id.removeprefix("workspace_")
                ),
                workspace_id=workspace.workspace_id,
                run_id=workspace.run_id,
                kind="baseline",
                commit_hash=workspace.baseline.commit_hash,
                tree_hash=workspace.baseline.tree_hash,
                design_version=1,
                work_node_id=None,
                evidence_ids=(),
                parent_checkpoint_id=None,
            ),
        )

    @staticmethod
    def _current_id(
        unit_of_work: PostgresUnitOfWork, workspace_id: str
    ) -> str | None:
        value = unit_of_work.connection.execute(
            text(
                "SELECT checkpoint_id FROM workspace_checkpoints "
                "WHERE workspace_id = :workspace_id "
                "ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1"
            ),
            {"workspace_id": workspace_id},
        ).scalar()
        return str(value) if value is not None else None

    @staticmethod
    def _get(
        unit_of_work: PostgresUnitOfWork, checkpoint_id: str
    ) -> WorkspaceCheckpoint | None:
        payload = unit_of_work.connection.execute(
            text(
                "SELECT payload FROM workspace_checkpoints "
                "WHERE checkpoint_id = :checkpoint_id"
            ),
            {"checkpoint_id": checkpoint_id},
        ).scalar()
        return PostgresCheckpointService._decode(payload) if payload is not None else None

    @staticmethod
    def _insert(
        unit_of_work: PostgresUnitOfWork, checkpoint: WorkspaceCheckpoint
    ) -> None:
        now = datetime.now(UTC)
        unit_of_work.connection.execute(
            text(
                "INSERT INTO workspace_checkpoints ("
                "checkpoint_id, run_id, workspace_id, work_node_id, "
                "parent_checkpoint_id, checkpoint_kind, commit_hash, tree_hash, "
                "design_version, evidence_ids, rollback_from_checkpoint_id, "
                "record_version, created_at, updated_at, idempotency_key, payload"
                ") VALUES ("
                ":checkpoint_id, :run_id, :workspace_id, :work_node_id, "
                ":parent_checkpoint_id, :kind, :commit_hash, :tree_hash, "
                ":design_version, CAST(:evidence_ids AS jsonb), "
                ":rollback_from_checkpoint_id, 1, :now, :now, :idempotency_key, "
                "CAST(:payload AS jsonb))"
            ),
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "run_id": checkpoint.run_id,
                "workspace_id": checkpoint.workspace_id,
                "work_node_id": checkpoint.work_node_id,
                "parent_checkpoint_id": checkpoint.parent_checkpoint_id,
                "kind": checkpoint.kind,
                "commit_hash": checkpoint.commit_hash,
                "tree_hash": checkpoint.tree_hash,
                "design_version": checkpoint.design_version,
                "evidence_ids": json.dumps(checkpoint.evidence_ids),
                "rollback_from_checkpoint_id": checkpoint.rollback_from_checkpoint_id,
                "now": now,
                "idempotency_key": f"checkpoint:{checkpoint.checkpoint_id}",
                "payload": json.dumps(asdict(checkpoint), separators=(",", ":")),
            },
        )

    @staticmethod
    def _decode(payload: object) -> WorkspaceCheckpoint:
        if not isinstance(payload, dict):
            raise CheckpointError("invalid_checkpoint_record")
        values = cast(dict[str, object], payload)
        try:
            return WorkspaceCheckpoint(
                checkpoint_id=str(values["checkpoint_id"]),
                workspace_id=str(values["workspace_id"]),
                run_id=str(values["run_id"]),
                kind=cast(CheckpointKind, values["kind"]),
                commit_hash=str(values["commit_hash"]),
                tree_hash=str(values["tree_hash"]),
                design_version=int(cast(int | str, values["design_version"])),
                work_node_id=(
                    str(values["work_node_id"])
                    if values.get("work_node_id") is not None
                    else None
                ),
                evidence_ids=tuple(
                    str(value)
                    for value in cast(list[object], values.get("evidence_ids", []))
                ),
                parent_checkpoint_id=(
                    str(values["parent_checkpoint_id"])
                    if values.get("parent_checkpoint_id") is not None
                    else None
                ),
                rollback_from_checkpoint_id=(
                    str(values["rollback_from_checkpoint_id"])
                    if values.get("rollback_from_checkpoint_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CheckpointError("invalid_checkpoint_record") from error


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
