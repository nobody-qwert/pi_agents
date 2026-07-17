"""Authenticated, idempotent promotion into an isolated host Git worktree."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

from orchestrator.projects import ProjectCatalog
from orchestrator.promotion_preview import PromotionPreview, PromotionPreviewService
from orchestrator.workspace import WorkspaceImport, WorkspaceImportService

_VERSION = re.compile(r"^(?:v\d+\.\d+\.\d+|run-\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+)$")


class PromotionError(Exception):
    """A requested host promotion was refused without changing the active checkout."""


@dataclass(frozen=True, slots=True)
class ConfirmationGrant:
    nonce: str
    preview_hash: str
    user_id: str


@dataclass(frozen=True, slots=True)
class PromotionResult:
    idempotency_key: str
    preview_hash: str
    status: str
    branch: str | None
    commit_hash: str | None
    tag: str | None
    reason: str | None = None


class GitPromotionService:
    """Consumes one human confirmation and delegates fixed Git operations only."""

    def __init__(
        self,
        previews: PromotionPreviewService,
        imports: WorkspaceImportService,
        catalog: ProjectCatalog,
        guest_root: Path,
        review_root: Path,
    ) -> None:
        self._previews = previews
        self._imports = imports
        self._catalog = catalog
        self._guest_root = guest_root.resolve()
        self._review_root = review_root.resolve()
        self._grants: dict[str, ConfirmationGrant] = {}
        self._results: dict[str, PromotionResult] = {}

    def issue_confirmation(
        self, *, preview_hash: str, user_id: str
    ) -> ConfirmationGrant:
        self._previews.get(preview_hash)
        if not user_id.startswith("user_"):
            raise PromotionError("invalid_user")
        grant = ConfirmationGrant(secrets.token_urlsafe(24), preview_hash, user_id)
        self._grants[grant.nonce] = grant
        return grant

    def confirm(
        self,
        *,
        nonce: str,
        user_id: str,
        idempotency_key: str,
        version: str,
        message: str,
        tag: str | None = None,
    ) -> PromotionResult:
        existing = self._results.get(idempotency_key)
        if existing is not None:
            return existing
        grant = self._grants.pop(nonce, None)
        if grant is None or grant.user_id != user_id:
            raise PromotionError("invalid_confirmation")
        if not _VERSION.fullmatch(version) or not message.strip() or len(message) > 512:
            raise PromotionError("invalid_promotion_input")
        if tag is not None and tag != version:
            raise PromotionError("tag_must_match_version")
        preview = self._previews.get(grant.preview_hash)
        if not preview.direct_eligible:
            result = PromotionResult(
                idempotency_key,
                preview.preview_hash,
                "fallback",
                None,
                None,
                None,
                "preview_ineligible",
            )
            self._results[idempotency_key] = result
            return result
        workspace = self._imports.get(preview.workspace_id)
        try:
            result = self._promote(
                workspace, preview, idempotency_key, version, message, tag
            )
        except PromotionError as error:
            result = PromotionResult(
                idempotency_key,
                preview.preview_hash,
                "fallback",
                None,
                None,
                None,
                str(error),
            )
        self._results[idempotency_key] = result
        return result

    def _promote(
        self,
        workspace: WorkspaceImport,
        preview: PromotionPreview,
        key: str,
        version: str,
        message: str,
        tag: str | None,
    ) -> PromotionResult:
        source = self._catalog.resolve(workspace.project_id)
        if self._git(
            source, "rev-parse", "HEAD"
        ) != workspace.source_git_head or self._git(source, "status", "--porcelain"):
            raise PromotionError("source_baseline_changed")
        branch = f"orchestrator/{version}-{workspace.run_id.removeprefix('run_')}"
        if (
            self._git(
                source, "show-ref", "--verify", f"refs/heads/{branch}", fail=False
            )
            is not None
        ):
            raise PromotionError("branch_already_exists")
        if (
            tag
            and self._git(
                source, "show-ref", "--verify", f"refs/tags/{tag}", fail=False
            )
            is not None
        ):
            raise PromotionError("tag_already_exists")
        review = self._review_root / f"{key[:24]}"
        review.parent.mkdir(parents=True, exist_ok=True)
        self._git(source, "worktree", "add", "-b", branch, os.fspath(review), "HEAD")
        try:
            patch = self._guest_git(
                workspace,
                "diff",
                "--binary",
                workspace.baseline.commit_hash,
                preview.checkpoint_commit,
            )
            applied = subprocess.run(
                ("git", "-C", os.fspath(review), "apply", "--index"),
                input=patch.encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=20,
            )
            if applied.returncode:
                raise PromotionError("manifest_apply_failed")
            self._git(
                review,
                "-c",
                "user.name=orchestrator-service",
                "-c",
                "user.email=service@orchestrator.invalid",
                "commit",
                "-m",
                message,
            )
            commit_hash = self._git(review, "rev-parse", "HEAD")
            if tag:
                self._git(review, "tag", "-a", tag, "-m", f"Promotion {version}")
            return PromotionResult(
                key, preview.preview_hash, "committed", branch, commit_hash, tag
            )
        finally:
            self._git(
                source, "worktree", "remove", "--force", os.fspath(review), fail=False
            )

    def _guest_git(self, workspace: WorkspaceImport, *args: str) -> str:
        path = (self._guest_root / workspace.guest_id / workspace.guest_path).resolve()
        if not path.is_relative_to(self._guest_root / workspace.guest_id):
            raise PromotionError("guest_path_escape")
        result = self._git(path, *args)
        if result is None:
            raise PromotionError("git_operation_failed")
        return result

    @staticmethod
    def _git(path: Path, *args: str, fail: bool = True) -> str | None:
        result = subprocess.run(
            ("git", "-C", os.fspath(path), *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
        )
        if result.returncode:
            if fail:
                raise PromotionError("git_operation_failed")
            return None
        return result.stdout.decode().strip()
