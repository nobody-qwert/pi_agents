"""Typed VM-manager client adapters for guest Git recovery and preview reads."""

from __future__ import annotations

import base64
from hashlib import sha256
from typing import cast

from orchestrator.checkpoints import CheckpointError, GuestCheckpointAdapter
from orchestrator.promotion_preview import ChangedPath, GuestPreviewAdapter
from orchestrator.vm_manager import VmManagerHttpAdapter
from orchestrator.workspace import GuestBaseline, WorkspaceImport


class VmManagerGuestGitAdapter(GuestCheckpointAdapter, GuestPreviewAdapter):
    """Uses only the VM manager's fixed guest-Git operations."""

    def __init__(self, client: VmManagerHttpAdapter) -> None:
        self._client = client

    def checkpoint(
        self, workspace: WorkspaceImport, checkpoint_id: str
    ) -> GuestBaseline:
        response = self._client._request(
            "POST",
            f"/v1/guests/{workspace.guest_id}/workspace/checkpoints",
            {
                "guest_path": workspace.guest_path,
                "checkpoint_id": checkpoint_id,
            },
        )
        return self._baseline(response)

    def verify(self, workspace: WorkspaceImport, baseline: GuestBaseline) -> bool:
        response = self._client._request(
            "POST",
            f"/v1/guests/{workspace.guest_id}/workspace/checkpoints/verify",
            {
                "guest_path": workspace.guest_path,
                "commit_hash": baseline.commit_hash,
                "tree_hash": baseline.tree_hash,
            },
        )
        valid = response.get("valid")
        if not isinstance(valid, bool):
            raise CheckpointError("invalid_vm_manager_response")
        return valid

    def restore(self, workspace: WorkspaceImport, baseline: GuestBaseline) -> None:
        self._client._request(
            "POST",
            f"/v1/guests/{workspace.guest_id}/workspace/checkpoints/restore",
            {
                "guest_path": workspace.guest_path,
                "commit_hash": baseline.commit_hash,
                "tree_hash": baseline.tree_hash,
            },
        )

    def diff_paths(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> tuple[ChangedPath, ...]:
        response = self._client._request(
            "POST",
            f"/v1/guests/{workspace.guest_id}/workspace/diff",
            {
                "guest_path": workspace.guest_path,
                "baseline_commit": baseline_commit,
                "target_commit": target_commit,
            },
        )
        raw = response.get("changed_paths")
        if not isinstance(raw, list):
            raise CheckpointError("invalid_vm_manager_response")
        changed: list[ChangedPath] = []
        for value in cast(list[object], raw):
            if not isinstance(value, dict):
                raise CheckpointError("invalid_vm_manager_response")
            status = value.get("status")
            path = value.get("path")
            if not isinstance(status, str) or not isinstance(path, str):
                raise CheckpointError("invalid_vm_manager_response")
            changed.append(ChangedPath(path=path, status=status))
        return tuple(changed)

    def export_patch(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> bytes:
        response = self._client._request(
            "POST",
            f"/v1/guests/{workspace.guest_id}/workspace/patch",
            {
                "guest_path": workspace.guest_path,
                "baseline_commit": baseline_commit,
                "target_commit": target_commit,
            },
            max_response_bytes=14_000_000,
        )
        encoded = response.get("patch_base64")
        expected = response.get("patch_sha256")
        if not isinstance(encoded, str) or not isinstance(expected, str):
            raise CheckpointError("invalid_vm_manager_response")
        try:
            patch = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise CheckpointError("invalid_vm_manager_response") from error
        if len(patch) > 10_485_760 or sha256(patch).hexdigest() != expected:
            raise CheckpointError("invalid_vm_manager_response")
        return patch

    @staticmethod
    def _baseline(response: dict[str, object]) -> GuestBaseline:
        commit_hash = response.get("commit_hash")
        tree_hash = response.get("tree_hash")
        if not isinstance(commit_hash, str) or not isinstance(tree_hash, str):
            raise CheckpointError("invalid_vm_manager_response")
        return GuestBaseline(commit_hash=commit_hash, tree_hash=tree_hash)
