"""Owned API projections and commands for durable guest workspace recovery."""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol, cast

from sqlalchemy import text

from orchestrator.checkpoints import PostgresCheckpointService
from orchestrator.commands import CommandError
from orchestrator.persistence import PostgresUnitOfWork
from orchestrator.promotion_preview import ChangedPath
from orchestrator.vm import GuestHandle, PostgresVmLifecycleService
from orchestrator.workspace import (
    PostgresWorkspaceImportStore,
    WorkspaceImport,
    WorkspaceImportService,
)


@dataclass(frozen=True, slots=True)
class WorkspacePreviewResponse:
    status_code: int
    content_type: str
    content: bytes


class WorkspacePreviewTransport(Protocol):
    def fetch(
        self,
        guest: GuestHandle,
        port: int,
        method: Literal["GET", "HEAD"],
        target: str,
    ) -> tuple[int, str, bytes]: ...


class WorkspaceDiffTransport(Protocol):
    def diff_paths(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> tuple[ChangedPath, ...]: ...


class PostgresWorkspaceApiService:
    """Keeps HTTP handlers thin while enforcing run ownership and durable state."""

    def __init__(
        self,
        unit_of_work: PostgresUnitOfWork,
        lifecycle: PostgresVmLifecycleService,
        imports: WorkspaceImportService,
        import_store: PostgresWorkspaceImportStore,
        checkpoints: PostgresCheckpointService,
        preview_transport: WorkspacePreviewTransport | None = None,
        preview_ports: tuple[int, ...] = (),
        preview_secret: str | None = None,
        diff_transport: WorkspaceDiffTransport | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._lifecycle = lifecycle
        self._imports = imports
        self._import_store = import_store
        self._checkpoints = checkpoints
        self._preview_transport = preview_transport
        self._preview_ports = preview_ports
        self._preview_secret = preview_secret.encode() if preview_secret else None
        self._diff_transport = diff_transport
        if preview_transport is not None and (
            self._preview_secret is None or len(self._preview_secret) < 32
        ):
            raise ValueError("preview secret must contain at least 32 characters")

    def prepare(self, *, run_id: str, user_id: str) -> dict[str, object]:
        project_id, fingerprint = self._owned_run(run_id, user_id)
        handle = self._lifecycle.create(run_id)
        if handle.status != "ready":
            handle = self._lifecycle.probe(run_id)
        if handle.status != "ready":
            raise CommandError("workspace_not_ready")
        workspace_id = self._lifecycle.workspace_id(run_id)
        self._imports.import_snapshot(
            workspace_id=workspace_id,
            run_id=run_id,
            project_id=project_id,
            expected_source_fingerprint=fingerprint,
        )
        return self.get(run_id=run_id, user_id=user_id)

    def get(self, *, run_id: str, user_id: str) -> dict[str, object]:
        project_id, fingerprint = self._owned_run(run_id, user_id)
        workspace_id = self._lifecycle.workspace_id(run_id)
        imported = self._import_store.get(workspace_id)
        if imported is None:
            handle = self._lifecycle.get(run_id)
            return {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "project_id": project_id,
                "source_fingerprint": fingerprint,
                "excluded_paths": [],
                "protected_paths": [],
                "guest_path": None,
                "status": handle.status,
                "health": {
                    "vm": handle.status,
                    "ssh": "ready" if handle.status == "ready" else "unavailable",
                    "browser": "pending",
                    "egress": "policy-controlled",
                },
            }
        handle = self._lifecycle.get(run_id)
        return {
            "workspace_id": imported.workspace_id,
            "run_id": imported.run_id,
            "project_id": imported.project_id,
            "source_fingerprint": imported.source_fingerprint,
            "excluded_paths": list(imported.excluded_paths),
            "protected_paths": list(imported.protected_paths),
            "guest_path": imported.guest_path,
            "status": "ready",
            "health": {
                "vm": handle.status,
                "ssh": "ready" if handle.status == "ready" else "unavailable",
                "browser": "available-through-desktop",
                "egress": "policy-controlled",
            },
        }

    def list_checkpoints(
        self, *, run_id: str, user_id: str
    ) -> dict[str, object]:
        self._owned_run(run_id, user_id)
        workspace_id = self._lifecycle.workspace_id(run_id)
        checkpoints = self._checkpoints.list_checkpoints(workspace_id)
        return {
            "current_checkpoint_id": (
                checkpoints[-1].checkpoint_id if checkpoints else None
            ),
            "checkpoints": [
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "kind": checkpoint.kind,
                    "commit_hash": checkpoint.commit_hash,
                    "tree_hash": checkpoint.tree_hash,
                    "parent_checkpoint_id": checkpoint.parent_checkpoint_id,
                    "design_version": checkpoint.design_version,
                    "work_node_id": checkpoint.work_node_id,
                    "evidence_ids": list(checkpoint.evidence_ids),
                    "rollback_from_checkpoint_id": (
                        checkpoint.rollback_from_checkpoint_id
                    ),
                }
                for checkpoint in checkpoints
            ],
        }

    def list_previews(self, *, run_id: str, user_id: str) -> dict[str, object]:
        self._owned_run(run_id, user_id)
        if self._preview_transport is None or self._preview_secret is None:
            return {"previews": []}
        guest = self._lifecycle.get(run_id)
        if guest.status != "ready":
            raise CommandError("workspace_not_ready")
        expires_at = int(time.time()) + 600
        previews: list[dict[str, object]] = []
        for port in self._preview_ports:
            try:
                status_code, _, _ = self._preview_transport.fetch(
                    guest, port, "HEAD", "/"
                )
            except Exception:
                continue
            if not 100 <= status_code < 500:
                continue
            token = self._preview_token(run_id, user_id, port, expires_at)
            previews.append(
                {
                    "label": f"Guest application on port {port}",
                    "port": port,
                    "url": f"/api/v1/workspace-previews/{token}/",
                    "expires_at": expires_at,
                }
            )
        return {"previews": previews}

    def rollback_preview(
        self, *, run_id: str, user_id: str, target_checkpoint_id: str
    ) -> dict[str, object]:
        self._owned_run(run_id, user_id)
        if self._diff_transport is None:
            raise CommandError("workspace_preview_unavailable")
        workspace_id = self._lifecycle.workspace_id(run_id)
        workspace = self._import_store.get(workspace_id)
        if workspace is None:
            raise CommandError("workspace_not_ready")
        checkpoints = self._checkpoints.list_checkpoints(workspace_id)
        target = next(
            (
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.checkpoint_id == target_checkpoint_id
            ),
            None,
        )
        if target is None:
            raise CommandError("unknown_checkpoint")
        current = checkpoints[-1]
        changed = self._diff_transport.diff_paths(
            workspace, current.commit_hash, target.commit_hash
        )
        return {
            "current_checkpoint_id": current.checkpoint_id,
            "target_checkpoint_id": target.checkpoint_id,
            "changed_paths": [
                {"status": item.status, "path": item.path} for item in changed
            ],
        }

    def fetch_preview(self, *, token: str, target: str) -> WorkspacePreviewResponse:
        if self._preview_transport is None or self._preview_secret is None:
            raise CommandError("workspace_preview_unavailable")
        run_id, user_id, port, expires_at = self._decode_preview_token(token)
        if expires_at < int(time.time()):
            raise CommandError("workspace_preview_expired")
        self._owned_run(run_id, user_id)
        guest = self._lifecycle.get(run_id)
        if guest.status != "ready" or port not in self._preview_ports:
            raise CommandError("workspace_preview_unavailable")
        if not target.startswith("/") or "\r" in target or "\n" in target:
            raise CommandError("invalid_preview_request")
        status_code, content_type, content = self._preview_transport.fetch(
            guest, port, "GET", target
        )
        return WorkspacePreviewResponse(status_code, content_type, content)

    def _preview_token(
        self, run_id: str, user_id: str, port: int, expires_at: int
    ) -> str:
        assert self._preview_secret is not None
        payload = base64.urlsafe_b64encode(
            json.dumps([run_id, user_id, port, expires_at], separators=(",", ":")).encode()
        ).decode().rstrip("=")
        signature = hmac.new(self._preview_secret, payload.encode(), sha256).hexdigest()
        return f"{payload}.{signature}"

    def _decode_preview_token(self, token: str) -> tuple[str, str, int, int]:
        assert self._preview_secret is not None
        payload, separator, signature = token.partition(".")
        expected = hmac.new(self._preview_secret, payload.encode(), sha256).hexdigest()
        if not separator or not hmac.compare_digest(signature, expected):
            raise CommandError("workspace_preview_not_authorized")
        try:
            padding = "=" * (-len(payload) % 4)
            values = json.loads(base64.urlsafe_b64decode(payload + padding))
            run_id, user_id, port, expires_at = cast(list[object], values)
            if not isinstance(run_id, str) or not isinstance(user_id, str):
                raise ValueError
            return run_id, user_id, int(cast(int, port)), int(cast(int, expires_at))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise CommandError("workspace_preview_not_authorized") from error

    def rollback(
        self,
        *,
        run_id: str,
        user_id: str,
        target_checkpoint_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not idempotency_key or len(idempotency_key) > 256:
            raise CommandError("missing_idempotency_key")
        self._owned_run(run_id, user_id)
        self._require_user_input(run_id)
        workspace_id = self._lifecycle.workspace_id(run_id)
        digest = sha256(
            f"{run_id}\0{user_id}\0{target_checkpoint_id}\0{idempotency_key}".encode()
        ).hexdigest()[:24]
        self._checkpoints.rollback(
            workspace_id=workspace_id,
            target_checkpoint_id=target_checkpoint_id,
            rollback_checkpoint_id=f"checkpoint_rollback_{digest}",
            design_version=self._design_version(run_id),
        )
        return self.list_checkpoints(run_id=run_id, user_id=user_id)

    def create_checkpoint(
        self, *, run_id: str, user_id: str, idempotency_key: str
    ) -> dict[str, object]:
        if not idempotency_key or len(idempotency_key) > 256:
            raise CommandError("missing_idempotency_key")
        self._owned_run(run_id, user_id)
        self._require_user_input(run_id)
        workspace_id = self._lifecycle.workspace_id(run_id)
        digest = sha256(
            f"{run_id}\0{user_id}\0{idempotency_key}".encode()
        ).hexdigest()[:24]
        self._checkpoints.create(
            workspace_id=workspace_id,
            checkpoint_id=f"checkpoint_user_{digest}",
            kind="user_accepted",
            design_version=self._design_version(run_id),
        )
        return self.list_checkpoints(run_id=run_id, user_id=user_id)

    def _owned_run(self, run_id: str, user_id: str) -> tuple[str, str]:
        with self._unit_of_work.transaction() as unit_of_work:
            row = unit_of_work.connection.execute(
                text(
                    "SELECT project_id, source_fingerprint FROM runs "
                    "WHERE run_id = :run_id AND user_id = :user_id"
                ),
                {"run_id": run_id, "user_id": user_id},
            ).one_or_none()
        if row is None or row.project_id is None or row.source_fingerprint is None:
            raise CommandError("run_not_found")
        return str(row.project_id), str(row.source_fingerprint)

    def _design_version(self, run_id: str) -> int:
        with self._unit_of_work.transaction() as unit_of_work:
            version = unit_of_work.connection.execute(
                text(
                    "SELECT COALESCE(MAX(design_version), 1) FROM design_revisions "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
        return int(version)

    def _require_user_input(self, run_id: str) -> None:
        with self._unit_of_work.transaction() as unit_of_work:
            owner = unit_of_work.connection.execute(
                text(
                    "SELECT owner FROM workspace_input_ownership WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).scalar()
        if owner != "USER":
            raise CommandError("workspace_input_not_owned")
