"""Immutable, manifest-checked guest promotion previews with no host mutation."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from orchestrator.checkpoints import CheckpointService, WorkspaceCheckpoint
from orchestrator.workspace import WorkspaceImport, WorkspaceImportService

_SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class PromotionPreviewError(Exception):
    """A preview cannot be safely constructed from the requested workspace state."""


@dataclass(frozen=True, slots=True)
class PreviewCheck:
    name: str
    passed: bool
    summary: str


@dataclass(frozen=True, slots=True)
class ChangedPath:
    path: str
    status: str


@dataclass(frozen=True, slots=True)
class PromotionPreview:
    preview_id: str
    preview_hash: str
    workspace_id: str
    checkpoint_id: str
    checkpoint_commit: str
    changed_paths: tuple[ChangedPath, ...]
    checks: tuple[PreviewCheck, ...]
    proposed_version: str
    direct_eligible: bool
    ineligible_reasons: tuple[str, ...]


class GuestPreviewAdapter(Protocol):
    def diff_paths(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> tuple[ChangedPath, ...]: ...


class PromotionPreviewService:
    """Stable previews derive only from immutable import/checkpoint/check inputs."""

    def __init__(
        self,
        imports: WorkspaceImportService,
        checkpoints: CheckpointService,
        adapter: GuestPreviewAdapter,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._imports = imports
        self._checkpoints = checkpoints
        self._adapter = adapter
        self._now = now or (lambda: datetime.now(UTC))
        self._previews: dict[str, PromotionPreview] = {}

    def get(self, preview_hash: str) -> PromotionPreview:
        try:
            return self._previews[preview_hash]
        except KeyError as error:
            raise PromotionPreviewError("unknown_preview") from error

    def preview(
        self,
        *,
        workspace_id: str,
        checkpoint_id: str,
        checks: tuple[PreviewCheck, ...],
        unresolved_issues: int,
        source_tags: tuple[str, ...] = (),
    ) -> PromotionPreview:
        if unresolved_issues < 0:
            raise PromotionPreviewError("invalid_issue_count")
        workspace = self._imports.get(workspace_id)
        checkpoint = self._checkpoint(workspace_id, checkpoint_id)
        changed = self._adapter.diff_paths(
            workspace, workspace.baseline.commit_hash, checkpoint.commit_hash
        )
        proposed_version = self._propose_version(source_tags, workspace.run_id)
        reasons = self._eligibility_reasons(
            workspace, changed, checks, unresolved_issues
        )
        digest_input = repr(
            (
                workspace_id,
                checkpoint_id,
                checkpoint.commit_hash,
                changed,
                checks,
                unresolved_issues,
                source_tags,
            )
        ).encode()
        preview_hash = hashlib.sha256(digest_input).hexdigest()
        existing = self._previews.get(preview_hash)
        if existing is not None:
            return existing
        preview = PromotionPreview(
            preview_id="preview_" + preview_hash[:24],
            preview_hash=preview_hash,
            workspace_id=workspace_id,
            checkpoint_id=checkpoint_id,
            checkpoint_commit=checkpoint.commit_hash,
            changed_paths=changed,
            checks=checks,
            proposed_version=proposed_version,
            direct_eligible=not reasons,
            ineligible_reasons=tuple(reasons),
        )
        self._previews[preview_hash] = preview
        return preview

    def _checkpoint(self, workspace_id: str, checkpoint_id: str) -> WorkspaceCheckpoint:
        for checkpoint in self._checkpoints.list_checkpoints(workspace_id):
            if checkpoint.checkpoint_id == checkpoint_id:
                return checkpoint
        raise PromotionPreviewError("unknown_checkpoint")

    @staticmethod
    def _eligibility_reasons(
        workspace: WorkspaceImport,
        changed: tuple[ChangedPath, ...],
        checks: tuple[PreviewCheck, ...],
        unresolved_issues: int,
    ) -> list[str]:
        reasons: list[str] = []
        if workspace.source_git_head is None:
            reasons.append("source_not_git")
        if workspace.source_git_dirty is not False:
            reasons.append("source_not_clean")
        if unresolved_issues:
            reasons.append("unresolved_issues")
        if any(not check.passed for check in checks):
            reasons.append("required_checks_failed")
        protected = set(workspace.excluded_paths).union(workspace.protected_paths)
        if any(
            path.path in protected or path.path.startswith(".git/") for path in changed
        ):
            reasons.append("protected_path_changed")
        return reasons

    def _propose_version(self, tags: tuple[str, ...], run_id: str) -> str:
        versions = [
            tuple(map(int, match.groups()))
            for tag in tags
            if (match := _SEMVER_TAG.fullmatch(tag))
        ]
        if versions:
            major, minor, _ = max(versions)
            return f"v{major}.{minor + 1}.0"
        return (
            f"run-{self._now().date().isoformat()}-{run_id.removeprefix('run_')[:12]}"
        )


class LocalGuestPreviewAdapter:
    """Read-only Git diff adapter for the disposable fixture guest."""

    def __init__(self, guest_root: Path) -> None:
        self._guest_root = guest_root.resolve()

    def diff_paths(
        self, workspace: WorkspaceImport, baseline_commit: str, target_commit: str
    ) -> tuple[ChangedPath, ...]:
        path = (self._guest_root / workspace.guest_id / workspace.guest_path).resolve()
        if not path.is_relative_to(self._guest_root / workspace.guest_id):
            raise PromotionPreviewError("guest_path_escape")
        result = subprocess.run(
            (
                "git",
                "-C",
                os.fspath(path),
                "diff",
                "--name-status",
                baseline_commit,
                target_commit,
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            text=True,
        )
        if result.returncode:
            raise PromotionPreviewError("guest_diff_failed")
        changed: list[ChangedPath] = []
        for line in result.stdout.splitlines():
            status, separator, name = line.partition("\t")
            if (
                not separator
                or not name
                or name.startswith("/")
                or ".." in Path(name).parts
            ):
                raise PromotionPreviewError("invalid_guest_diff")
            changed.append(ChangedPath(path=name, status=status))
        return tuple(changed)
